"""
Agent Action System — structured actions that agents declare in their responses.

Instead of agents using curl to call the API (which requires an auth bypass),
agents output [ACTION:action_name {...json...}] blocks in their response text.
The chat handler detects these, strips them from user-visible output, executes
them server-side with the authenticated user's context, and sends confirmations
back via WebSocket.
"""

import asyncio
import csv
import json
import re
from datetime import datetime, timezone
import database as db
from config import logger
from data_processing.helpers import _strip_control, _parse_json_array_response
from routes.deps import _user_data_dir

# ── Action detection ────────────────────────────────────────────────────────

# Matches [ACTION:action_name {json}] — the JSON can span multiple lines
ACTION_PATTERN = re.compile(
    r'\[ACTION:(\w+)\s+(\{.*?\})\]',
    re.DOTALL,
)


def extract_actions(text: str) -> tuple[str, list[tuple[str, dict]]]:
    """Extract action blocks from text, return (clean_text, [(action_name, params)])."""
    actions = []
    for match in ACTION_PATTERN.finditer(text):
        action_name = match.group(1)
        try:
            params = json.loads(match.group(2))
            actions.append((action_name, params))
        except json.JSONDecodeError as e:
            logger.warning(f"Agent action JSON parse error for {action_name}: {e}")
    clean = ACTION_PATTERN.sub('', text)
    return clean, actions


# ── Action Handlers ─────────────────────────────────────────────────────────

async def _handle_create_event(params: dict, user_id: int) -> dict:
    conn = await db.get_db()
    try:
        existing = await db.events_get_all(conn, user_id=user_id)
        if not existing:
            params["is_primary"] = True
        eid = await db.events_create(conn, params, user_id=user_id)
        return {"ok": True, "id": eid, "message": f"Event created (ID: {eid})"}
    finally:
        await conn.close()


async def _handle_update_event(params: dict, user_id: int) -> dict:
    event_id = params.pop("id", None) or params.pop("event_id", None)
    if not event_id:
        return {"ok": False, "error": "event id is required"}
    conn = await db.get_db()
    try:
        await db.events_update(conn, int(event_id), params, user_id=user_id)
        return {"ok": True, "message": f"Event {event_id} updated"}
    finally:
        await conn.close()


async def _handle_delete_event(params: dict, user_id: int) -> dict:
    event_id = params.get("id") or params.get("event_id")
    if not event_id:
        return {"ok": False, "error": "event id is required"}
    conn = await db.get_db()
    try:
        await db.events_delete(conn, int(event_id), user_id=user_id)
        return {"ok": True, "message": f"Event {event_id} deleted"}
    finally:
        await conn.close()


async def _handle_set_primary_event(params: dict, user_id: int) -> dict:
    event_id = params.get("id") or params.get("event_id")
    if not event_id:
        return {"ok": False, "error": "event id is required"}
    conn = await db.get_db()
    try:
        await db.events_set_primary(conn, int(event_id), user_id=user_id)
        return {"ok": True, "message": f"Event {event_id} set as primary"}
    finally:
        await conn.close()


async def _handle_list_events(params: dict, user_id: int) -> dict:
    conn = await db.get_db()
    try:
        events = await db.events_get_all(conn, user_id=user_id)
        return {"ok": True, "events": events}
    finally:
        await conn.close()


async def _handle_save_nutrition(params: dict, user_id: int) -> dict:
    for field in ("description", "notes"):
        if field in params:
            params[field] = _strip_control(params[field])
    params["created_at"] = datetime.now(tz=timezone.utc).isoformat()
    conn = await db.get_db()
    try:
        new_id = await db.nutrition_create(conn, params, user_id=user_id)
    finally:
        await conn.close()

    # Check if meal triggers insight regeneration (skip if insight generation is already running)
    meal_date = params.get("date", "")
    meal_time = params.get("meal_time", "")
    meal_type = params.get("meal_type", "")
    regenerating = []
    if meal_date:
        try:
            from services.task_tracker import _insight_status, _active_tasks, _active_tasks_lock
            # Skip regen if insight generation is already running for this user
            insight_running = _insight_status.get("running") and _insight_status.get("user_id") == user_id
            if not insight_running:
                async with _active_tasks_lock:
                    insight_running = any(
                        tid.startswith("insight-") and tid.endswith(f"-user{user_id}")
                        for tid in _active_tasks
                    )
            if insight_running:
                logger.debug(f"Skipping meal regen for {meal_date} — insight generation already active for user {user_id}")
            else:
                from routes.nutrition_routes import _check_meal_regeneration
                regenerating = await _check_meal_regeneration(meal_date, meal_time, meal_type, user_id)
                if regenerating:
                    from services.insights_engine import _maybe_regenerate_insight_for_date
                    asyncio.create_task(_maybe_regenerate_insight_for_date(meal_date, params, user_id=user_id))
        except Exception as e:
            logger.warning(f"Meal regeneration check failed: {e}")

    return {"ok": True, "id": new_id, "message": f"Meal saved (ID: {new_id})",
            "regenerating": regenerating}


async def _handle_save_body_metrics(params: dict, user_id: int) -> dict:
    date_str = params.get("date", "")
    if not date_str:
        return {"ok": False, "error": "date is required (YYYY-MM-DD)"}

    source = params.get("source", "LeaOne (via IronCoach)")
    dt_str = f"{date_str} 00:00:00 +0200"

    dd = _user_data_dir(user_id)
    csv_path = dd / "body_metrics.csv"
    rows_to_add = []

    field_map = [
        ("weight_kg",       "BodyMass",          "kg",    None),
        ("body_fat_pct",    "BodyFatPercentage", "%",     "pct_decimal"),
        ("bmi",             "BodyMassIndex",     "count", None),
        ("lean_mass_kg",    "LeanBodyMass",      "kg",    None),
        ("muscle_mass_kg",  "MuscleMass",        "kg",    None),
        ("muscle_rate_pct", "MuscleRate",        "%",     None),
        ("bone_mass_kg",    "BoneMass",          "kg",    None),
        ("body_water_pct",  "BodyWater",         "%",     None),
        ("protein_pct",     "ProteinRate",       "%",     None),
        ("visceral_fat",    "VisceralFat",       "index", None),
        ("bmr_kcal",        "BMR",               "kcal",  None),
        ("body_age",        "BodyAge",           "years", None),
        ("fat_mass_kg",     "FatMass",           "kg",    None),
    ]

    for field, csv_type, unit, transform in field_map:
        val = params.get(field)
        if val is not None:
            v = float(val)
            if transform == "pct_decimal" and v > 1:
                v = v / 100
            rows_to_add.append({
                "date": date_str, "datetime": dt_str, "type": csv_type,
                "value": str(round(v, 4) if transform == "pct_decimal" else v),
                "unit": unit, "sourceName": source,
            })

    if not rows_to_add:
        return {"ok": False, "error": "No metric fields provided"}

    cols = ["date", "datetime", "type", "value", "unit", "sourceName"]
    file_exists = csv_path.exists()
    with open(csv_path, "a", newline="") as f:
        writer = csv.DictWriter(f, fieldnames=cols)
        if not file_exists:
            writer.writeheader()
        for row in rows_to_add:
            writer.writerow(row)

    return {"ok": True, "entries_added": len(rows_to_add),
            "message": f"Body metrics saved ({len(rows_to_add)} entries)"}


async def _handle_save_memory(params: dict, user_id: int) -> dict:
    content = params.get("content", "").strip()
    if not content:
        return {"ok": False, "error": "content is required"}
    conn = await db.get_db()
    try:
        mem_id = await db.memory_add(conn, content, user_id=user_id)
        return {"ok": True, "id": mem_id, "message": f"Memory saved (ID: {mem_id})"}
    finally:
        await conn.close()


async def _handle_update_memory(params: dict, user_id: int) -> dict:
    mem_id = params.get("id") or params.get("mem_id")
    content = params.get("content", "").strip()
    if not mem_id:
        return {"ok": False, "error": "memory id is required"}
    if not content:
        return {"ok": False, "error": "content is required"}
    conn = await db.get_db()
    try:
        await db.memory_update(conn, int(mem_id), content, user_id=user_id)
        return {"ok": True, "message": f"Memory {mem_id} updated"}
    finally:
        await conn.close()


async def _handle_delete_memory(params: dict, user_id: int) -> dict:
    mem_id = params.get("id") or params.get("mem_id")
    if not mem_id:
        return {"ok": False, "error": "memory id is required"}
    conn = await db.get_db()
    try:
        await db.memory_delete(conn, int(mem_id), user_id=user_id)
        return {"ok": True, "message": f"Memory {mem_id} deleted"}
    finally:
        await conn.close()


async def _handle_analyze_nutrition(params: dict, user_id: int) -> dict:
    """Run meal analysis via Claude CLI and return parsed macros.
    This is a 'followup' action — the result is sent back to the agent session."""
    from services.insights_engine import _call_claude_for_insight

    text = (params.get("text") or "").strip()
    file_paths = params.get("file_paths") or []

    if not text and not file_paths:
        return {"ok": False, "error": "text or images required"}

    prompt_parts = [
        "You are an expert sports nutrition analyst for a triathlon athlete (male, 180cm). "
        "The input may be in Hebrew, English, or mixed. Understand both languages fully.\n\n"
    ]

    if file_paths:
        prompt_parts.append(
            "## Photo Analysis Instructions\n"
            "The athlete has attached food photos. Use the Read tool to view EACH image carefully.\n"
            "When analyzing food photos:\n"
            "1. **Identify every visible food item** — scan the entire image systematically\n"
            "2. **Estimate portion sizes** by comparing to plate size, utensils, or known reference objects\n"
            "3. **Look for hidden ingredients** — sauces, dressings, oils, butter, cheese under toppings\n"
            "4. **Identify cooking method** — fried (add oil calories), grilled, baked, steamed, raw\n"
            "5. **Check for beverages** in the photo — glasses, cups, bottles\n"
            "6. **Consider cultural context** — Israeli/Middle-Eastern food: hummus, tehina, pita, schnitzel, etc.\n"
            "7. **When uncertain about portion**: estimate conservatively for vegetables, generously for calorie-dense foods\n"
            "8. **Multiple plates/dishes** = describe each separately as items within one meal\n\n"
        )
        for fp in file_paths:
            prompt_parts.append(f"[IMAGE FILE — read this file to see the image: {fp}]\n")
        prompt_parts.append("\n")

    prompt_parts.append(
        "Return ONLY a valid JSON array (no markdown fences, no extra text). "
        "Each element represents one distinct meal (group items that are eaten together in one sitting):\n"
        '[{"meal_type":"breakfast|lunch|dinner|snack|pre_workout|during_workout|post_workout",'
        '"meal_time":"HH:MM (24h format, e.g. 07:30, 13:00) or empty string if not mentioned",'
        '"description":"short description in the SAME language as the input",'
        '"calories":number,"protein_g":number,"carbs_g":number,"fat_g":number,'
        '"hydration_ml":number,'
        '"items":[{"name":"full item name in same language",'
        '"base_name":"normalized singular name with cooking method — same language as name",'
        '"quantity":number_of_units,'
        '"unit_calories":calories_per_one_unit,"unit_protein_g":protein_per_unit,"unit_carbs_g":carbs_per_unit,"unit_fat_g":fat_per_unit,'
        '"calories":total_calories,"protein_g":total_protein,"carbs_g":total_carbs,"fat_g":total_fat}]}]\n\n'
        "Rules:\n"
        "- Group foods eaten in one sitting as one meal with multiple items\n"
        "- If the text describes meals at different times, return separate objects\n"
        "- Extract meal_time from text if mentioned. Leave empty if not.\n"
        "- Each meal MUST have an 'items' array listing every food with its macros\n"
        "- base_name: normalized singular form including cooking method\n"
        "- quantity: how many units (default 1). unit_* = macros for ONE unit.\n"
        "- Item names in SAME language as input\n"
        "- Meal totals MUST equal sum of items (verify before responding)\n"
        "- Estimate reasonable values for an athletic male (180cm)\n"
        "- Include cooking oils/fats. Common Israeli portions: hummus ~300g, pita ~80g, schnitzel ~200g\n"
        "- hydration_ml: only liquid beverages, 0 if no drink\n"
        "- Always return an array, even for a single meal\n\n"
    )
    if text:
        prompt_parts.append(f"MEAL DESCRIPTION: {text}")
    else:
        prompt_parts.append("Analyze the food in the attached photo(s). Default language: Hebrew.")

    prompt = "".join(prompt_parts)
    has_images = bool(file_paths)

    try:
        result = await _call_claude_for_insight(
            prompt, allowed_tools=["Read"] if has_images else None, user_id=user_id)
        if not result:
            return {"ok": False, "error": "AI did not return a response", "_followup": True}
    except Exception as e:
        return {"ok": False, "error": f"Analysis failed: {e}", "_followup": True}

    parsed = _parse_json_array_response(result)
    if parsed is None:
        return {"ok": False, "error": f"Could not parse response: {result[:200]}",
                "_followup": True}

    return {"ok": True, "meals": parsed, "_followup": True,
            "message": "Meal analysis complete"}


# ── Training Plan Handlers ────────────────────────────────────────────────────

async def _handle_create_plan(params: dict, user_id: int) -> dict:
    conn = await db.get_db()
    try:
        new_id = await db.plan_create(conn, params, user_id=user_id)
        return {"ok": True, "id": new_id, "message": f"Plan created (ID: {new_id})"}
    finally:
        await conn.close()


async def _handle_update_plan(params: dict, user_id: int) -> dict:
    plan_id = params.pop("id", None) or params.pop("plan_id", None)
    if not plan_id:
        return {"ok": False, "error": "plan id is required"}
    conn = await db.get_db()
    try:
        await db.plan_update(conn, int(plan_id), params, user_id=user_id)
        return {"ok": True, "message": f"Plan {plan_id} updated"}
    finally:
        await conn.close()


async def _handle_delete_plan(params: dict, user_id: int) -> dict:
    plan_id = params.get("id") or params.get("plan_id")
    if not plan_id:
        return {"ok": False, "error": "plan id is required"}
    conn = await db.get_db()
    try:
        await db.plan_delete(conn, int(plan_id), user_id=user_id)
        return {"ok": True, "message": f"Plan {plan_id} deleted"}
    finally:
        await conn.close()


# ── Action Registry ─────────────────────────────────────────────────────────

ACTION_HANDLERS = {
    "create_event": _handle_create_event,
    "update_event": _handle_update_event,
    "delete_event": _handle_delete_event,
    "set_primary_event": _handle_set_primary_event,
    "list_events": _handle_list_events,
    "save_nutrition": _handle_save_nutrition,
    "save_body_metrics": _handle_save_body_metrics,
    "save_memory": _handle_save_memory,
    "update_memory": _handle_update_memory,
    "delete_memory": _handle_delete_memory,
    "analyze_nutrition": _handle_analyze_nutrition,
    "create_plan": _handle_create_plan,
    "update_plan": _handle_update_plan,
    "delete_plan": _handle_delete_plan,
}

# Actions whose results need to be sent back to the agent as a follow-up message
FOLLOWUP_ACTIONS = {"analyze_nutrition"}


async def execute_action(action_name: str, params: dict, user_id: int) -> dict:
    """Execute a named action with params on behalf of user_id."""
    handler = ACTION_HANDLERS.get(action_name)
    if not handler:
        return {"ok": False, "error": f"Unknown action: {action_name}"}
    try:
        result = await handler(params, user_id)
        logger.info(f"Agent action executed: {action_name} (user={user_id}) -> {result.get('ok')}")
        return result
    except Exception as e:
        logger.error(f"Agent action failed: {action_name} (user={user_id}): {e}")
        return {"ok": False, "error": str(e)}
