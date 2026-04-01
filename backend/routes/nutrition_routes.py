"""Nutrition endpoints — meal logging and AI analysis."""

import asyncio
import json
import re
import uuid
from datetime import datetime, timedelta, timezone
from pathlib import Path

from fastapi import APIRouter, HTTPException, Request

import database as db
from config import logger, UPLOAD_DIR
from data_processing import _load_summary, _meal_relevant_to_workout
from data_processing.helpers import _strip_control, _parse_json_array_response
from routes.deps import _require_ai, _uid, _user_data_dir
from services.task_tracker import _register_task, _unregister_task

router = APIRouter()


@router.get("/api/nutrition")
async def nutrition_day(date: str, request: Request):
    conn = await db.get_db()
    try:
        return await db.nutrition_get_day(conn, date, user_id=_uid(request))
    finally:
        await conn.close()


@router.get("/api/nutrition/range")
async def nutrition_range(request: Request, from_date: str = "", to_date: str = ""):
    if not from_date or not to_date:
        raise HTTPException(400, "from_date and to_date required")
    conn = await db.get_db()
    try:
        return await db.nutrition_get_range(conn, from_date, to_date, user_id=_uid(request))
    finally:
        await conn.close()


@router.get("/api/nutrition/recent")
async def nutrition_recent(request: Request):
    """Return distinct recently-used food items for autocomplete."""
    conn = await db.get_db()
    try:
        return await db.nutrition_recent_items(conn, user_id=_uid(request))
    finally:
        await conn.close()


async def _check_meal_regeneration(meal_date: str, meal_time: str, meal_type: str, uid: int) -> list:
    """Check which workouts need insight regeneration for this meal."""
    if not meal_date:
        return []
    workouts = _load_summary(_user_data_dir(uid))
    day_workouts = [w for w in workouts if w.get("startDate", "")[:10] == meal_date]
    regenerating = []
    conn = await db.get_db()
    try:
        for w in day_workouts:
            wnum = int(w.get("workout_num", 0))
            existing = await db.insight_get(conn, wnum, user_id=uid)
            if not existing or not existing.get("generated_at", ""):
                continue
            w_start = w.get("startDate", "")
            w_dur = float(w.get("duration_min", 0) or 0)
            if _meal_relevant_to_workout(meal_time, meal_type, w_start, w_dur):
                regenerating.append({"workout_num": wnum, "type": w.get("type", "")})
    finally:
        await conn.close()
    return regenerating


@router.post("/api/nutrition")
async def nutrition_add(request: Request):
    from services.insights_engine import _maybe_regenerate_insight_for_date

    data = await request.json()
    for field in ("description", "notes"):
        if field in data:
            data[field] = _strip_control(data[field])
    data["created_at"] = datetime.now(tz=timezone.utc).isoformat()
    uid = _uid(request)
    conn = await db.get_db()
    try:
        new_id = await db.nutrition_create(conn, data, user_id=uid)
    finally:
        await conn.close()

    meal_date = data.get("date", "")
    meal_time = data.get("meal_time", "")
    meal_type = data.get("meal_type", "")
    regenerating_workouts = await _check_meal_regeneration(meal_date, meal_time, meal_type, uid)

    if regenerating_workouts:
        asyncio.create_task(_maybe_regenerate_insight_for_date(meal_date, data, user_id=uid))
    return {"id": new_id, "regenerating": regenerating_workouts}


# ── Per-user settings (used for nutrition auto-suggest toggle) ────────

_ALLOWED_USER_SETTINGS = {
    "nutrition_auto_suggest", "nutrition_auto_suggest_last_run", "nutrition_targets",
    "dismissed_insights", "hidden_workouts", "pending_import",
}


@router.get("/api/settings/{key}")
async def get_user_setting(key: str, request: Request):
    if key not in _ALLOWED_USER_SETTINGS:
        raise HTTPException(400, f"Unknown setting key: {key}")
    uid = _uid(request)
    conn = await db.get_db()
    try:
        value = await db.setting_get(conn, f"{key}_{uid}", "0")
        return {"key": key, "value": value}
    finally:
        await conn.close()


@router.put("/api/settings/{key}")
async def set_user_setting(key: str, request: Request):
    if key not in _ALLOWED_USER_SETTINGS:
        raise HTTPException(400, f"Unknown setting key: {key}")
    uid = _uid(request)
    data = await request.json()
    conn = await db.get_db()
    try:
        await db.setting_set(conn, f"{key}_{uid}", data.get("value", "0"))
        return {"ok": True}
    finally:
        await conn.close()


# ── Nutrition Targets (defined before {entry_id} routes) ────────

DEFAULT_TARGETS = {"calories": 2500, "protein_g": 150, "carbs_g": 300, "fat_g": 80, "water_ml": 2500}

@router.get("/api/nutrition/targets")
async def get_nutrition_targets(request: Request):
    uid = _uid(request)
    conn = await db.get_db()
    try:
        raw = await db.setting_get(conn, f"nutrition_targets_{uid}", "")
        if raw:
            return json.loads(raw)
        return DEFAULT_TARGETS
    finally:
        await conn.close()


@router.put("/api/nutrition/targets")
async def set_nutrition_targets(request: Request):
    uid = _uid(request)
    body = await request.json()
    targets = {
        "calories": int(body.get("calories", DEFAULT_TARGETS["calories"])),
        "protein_g": int(body.get("protein_g", DEFAULT_TARGETS["protein_g"])),
        "carbs_g": int(body.get("carbs_g", DEFAULT_TARGETS["carbs_g"])),
        "fat_g": int(body.get("fat_g", DEFAULT_TARGETS["fat_g"])),
        "water_ml": int(body.get("water_ml", DEFAULT_TARGETS["water_ml"])),
    }
    conn = await db.get_db()
    try:
        await db.setting_set(conn, f"nutrition_targets_{uid}", json.dumps(targets))
        return targets
    finally:
        await conn.close()


@router.post("/api/nutrition/targets/suggest")
async def suggest_nutrition_targets(request: Request):
    """Use AI (nutrition coach) to suggest daily targets based on athlete profile, training load, and race goals."""
    await _require_ai()
    uid = _uid(request)

    from services.coach_preamble import _build_coach_preamble
    from services.insights_engine import _call_claude_for_insight
    from services.claude_cli import _find_claude_cli

    if not _find_claude_cli():
        raise HTTPException(503, "Claude CLI not available")

    # Build context: preamble + actual body metrics + recent training summary
    preamble = await _build_coach_preamble(uid)
    dd = _user_data_dir(uid)
    workouts = _load_summary(dd)

    # Latest body metrics from Apple Health (scale data)
    from data_processing import _load_body_metrics
    body_rows = _load_body_metrics(dd)
    body_info = ""
    if body_rows:
        latest_weight = next((r for r in reversed(body_rows) if r.get("type") == "BodyMass"), None)
        latest_fat = next((r for r in reversed(body_rows) if r.get("type") == "BodyFatPercentage"), None)
        latest_lean = next((r for r in reversed(body_rows) if r.get("type") == "LeanBodyMass"), None)
        parts = []
        if latest_weight:
            parts.append(f"Weight: {latest_weight['value']}kg (measured {latest_weight['date']})")
        if latest_fat:
            parts.append(f"Body fat: {latest_fat['value']}%")
        if latest_lean:
            parts.append(f"Lean mass: {latest_lean['value']}kg")
        if parts:
            body_info = "Actual body metrics from scale: " + ", ".join(parts)

    # Last 7 days workout summary
    cutoff = (datetime.now() - timedelta(days=7)).strftime("%Y-%m-%d")
    recent = [w for w in workouts if (w.get("startDate") or "") >= cutoff]
    week_summary = ""
    if recent:
        total_min = sum(float(w.get("duration_min", 0) or 0) for w in recent)
        disciplines = {}
        for w in recent:
            d = w.get("discipline", "other")
            disciplines[d] = disciplines.get(d, 0) + 1
        week_summary = f"Last 7 days: {len(recent)} workouts, {total_min:.0f} min total. "
        week_summary += ", ".join(f"{d}: {c}x" for d, c in disciplines.items())

    prompt = f"""{preamble}

{body_info}
{week_summary}

Based on the athlete's body metrics, training load, race goals, and current training phase,
suggest optimal DAILY nutrition targets. Consider:
- Body weight and composition for protein needs (1.4-2.0g/kg for endurance athletes)
- Training volume and intensity for calorie needs
- Race proximity for carb loading adjustments
- Hydration based on training load

Respond with ONLY a JSON object (no markdown, no explanation):
{{"calories": <number>, "protein_g": <number>, "carbs_g": <number>, "fat_g": <number>, "water_ml": <number>, "reasoning": "<1-2 sentence explanation>"}}"""

    try:
        result = await _call_claude_for_insight(prompt, user_id=uid)
    except Exception as e:
        logger.error(f"AI suggest call failed: {e}")
        raise HTTPException(500, f"AI call failed: {e}")

    if not result:
        raise HTTPException(500, "AI did not return a response")

    # Parse JSON from response — try nested objects too
    try:
        match = re.search(r'\{[^{}]*"calories"[^{}]*\}', result)
        if match:
            suggested = json.loads(match.group())
            return {
                "calories": int(suggested.get("calories", 2500)),
                "protein_g": int(suggested.get("protein_g", 150)),
                "carbs_g": int(suggested.get("carbs_g", 300)),
                "fat_g": int(suggested.get("fat_g", 80)),
                "water_ml": int(suggested.get("water_ml", 2500)),
                "reasoning": suggested.get("reasoning", ""),
            }
    except (json.JSONDecodeError, ValueError) as e:
        logger.error(f"AI suggest parse error: {e}, raw: {result[:500]}")
    logger.error(f"AI suggest: no JSON found in response: {result[:500]}")
    raise HTTPException(500, "Could not parse AI suggestion")


@router.put("/api/nutrition/{entry_id}")
async def nutrition_edit(entry_id: int, request: Request):
    from services.insights_engine import _maybe_regenerate_insight_for_date

    data = await request.json()
    for field in ("description", "notes"):
        if field in data:
            data[field] = _strip_control(data[field])
    data["created_at"] = datetime.now(tz=timezone.utc).isoformat()
    uid = _uid(request)
    conn = await db.get_db()
    try:
        await db.nutrition_update(conn, entry_id, data, user_id=uid)
        cursor = await conn.execute(
            "SELECT date, meal_time, meal_type FROM nutrition_log WHERE id = ? AND user_id = ?", (entry_id, uid)
        )
        row = await cursor.fetchone()
        meal_date = row["date"] if row else ""
        meal_time = row["meal_time"] if row else ""
        meal_type = row["meal_type"] if row else ""
    finally:
        await conn.close()

    regenerating_workouts = await _check_meal_regeneration(meal_date, meal_time, meal_type, uid)

    if regenerating_workouts:
        asyncio.create_task(_maybe_regenerate_insight_for_date(meal_date, data, user_id=uid))
    return {"ok": True, "regenerating": regenerating_workouts}


@router.delete("/api/nutrition/{entry_id}")
async def nutrition_remove(entry_id: int, request: Request):
    conn = await db.get_db()
    try:
        await db.nutrition_delete(conn, entry_id, user_id=_uid(request))
        return {"ok": True}
    finally:
        await conn.close()


@router.post("/api/nutrition/analyze")
async def nutrition_analyze(request: Request):
    """Use Claude CLI to analyze a free-text meal description and extract macros.
    Supports Hebrew and English. Accepts JSON body or multipart form with images.
    Returns a JSON array of meals."""
    await _require_ai()
    from services.insights_engine import _call_claude_for_insight

    content_type = request.headers.get("content-type", "")
    file_paths: list[str] = []

    if "multipart/form-data" in content_type:
        form = await request.form()
        text = (form.get("text") or "").strip()
        # Save uploaded files
        for key in form:
            if key.startswith("file"):
                upload = form[key]
                if hasattr(upload, "read"):
                    ext = Path(upload.filename).suffix if upload.filename else ".jpg"
                    fname = f"{uuid.uuid4().hex[:12]}{ext}"
                    dest = UPLOAD_DIR / fname
                    content = await upload.read()
                    with open(dest, "wb") as f:
                        f.write(content)
                    file_paths.append(str(dest))
        # Auto-cleanup old uploads if directory exceeds threshold
        from routes.chat_routes import _cleanup_uploads
        await _cleanup_uploads()
    else:
        data = await request.json()
        text = (data.get("text") or "").strip()
        file_paths = data.get("file_paths") or []

    if not text and not file_paths:
        raise HTTPException(400, "text or images required")
    logger.debug(f"Meal analysis requested: {text[:80]} (+{len(file_paths)} files)")

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
        '"base_name":"normalized singular name with cooking method (e.g. scrambled egg, boiled egg, white rice, pita bread) — same language as name",'
        '"quantity":number_of_units,'
        '"unit_calories":calories_per_one_unit,"unit_protein_g":protein_per_unit,"unit_carbs_g":carbs_per_unit,"unit_fat_g":fat_per_unit,'
        '"calories":total_calories,"protein_g":total_protein,"carbs_g":total_carbs,"fat_g":total_fat}]}]\n\n'
        "Rules:\n"
        "- Group foods eaten in one sitting as one meal with multiple items\n"
        "- If the text describes meals at different times (e.g. lunch AND snack), return separate objects\n"
        "- Extract meal_time from the text if the user mentions a specific time (e.g. '7:30 morning', 'at 13:00', 'lunch at 2pm'). Leave empty if no time mentioned.\n"
        "- Each meal MUST have an 'items' array listing every individual food with its own macros\n"
        "- base_name: normalized singular form including cooking method/type (e.g. 'ביצה מקושקשת' not 'חביתה מ-2 ביצים', 'egg scrambled' not '2 scrambled eggs'). Same base_name = same food.\n"
        "- quantity: how many units (e.g. 2 eggs = quantity:2). Default 1.\n"
        "- unit_* fields: macros for ONE unit. Total = unit * quantity (verify before responding).\n"
        "- Item names should be descriptive and in the SAME language as input (no abbreviations)\n"
        "- For photos: use the input language if text is provided, otherwise use Hebrew\n"
        "- The meal-level totals MUST equal the sum of all items (verify before responding)\n"
        "- Estimate reasonable values if exact amounts aren't given\n"
        "- Use typical portion sizes for an athletic male (180cm)\n"
        "- Include cooking oils/fats in the calorie count (1 tbsp oil = ~120 kcal)\n"
        "- Common Israeli portions: hummus plate ~300g, pita ~80g, schnitzel ~200g, salad ~200g\n"
        "- hydration_ml: only liquid beverages (water, juice, coffee, etc.), 0 if no drink\n"
        "- Always return an array, even for a single meal\n\n"
    )
    if text:
        prompt_parts.append(f"MEAL DESCRIPTION: {text}")
    else:
        prompt_parts.append("Analyze the food in the attached photo(s). Default language: Hebrew.")

    prompt = "".join(prompt_parts)
    has_images = bool(file_paths)
    uid = _uid(request)
    task_id = f"meal-analyze-user{uid}-{int(datetime.now().timestamp())}"
    await _register_task(task_id, "Meal Analysis", "/nutrition")
    try:
        result = await _call_claude_for_insight(prompt, allowed_tools=["Read"] if has_images else None,
                                                user_id=uid)
        if not result:
            raise HTTPException(500, "Failed to analyze meal")
    except Exception:
        await _unregister_task(task_id)
        raise

    parsed = _parse_json_array_response(result)
    if parsed is None:
        raise HTTPException(500, f"Could not parse AI response as JSON: {result[:200]}")

    await _unregister_task(task_id)
    return parsed
