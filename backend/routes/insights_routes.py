"""Insight API routes — workout insights, batch generation, period assessments."""

import asyncio
import json
import shutil
from datetime import datetime, timedelta, timezone
from pathlib import Path

from fastapi import APIRouter, HTTPException, Query, Request

import database as db
from config import TRAINING_DATA, _SESSIONS_DIR, logger, _CATEGORY_AGENTS, INSIGHT_CUTOFF_DATE
from routes.deps import _require_admin, _require_ai, _uid, _user_data_dir
from services.task_tracker import (
    _insight_status, _insight_status_lock,
    _active_tasks, _active_tasks_lock, _chat_streaming,
)
from services.insights_engine import (
    _generate_insights_batch, _generate_insight_for_workout, _generate_brick_insight,
    _maybe_regenerate_insight_for_date,
    _build_general_prompt, _call_claude_for_insight,
    _load_recovery_data_range, _split_plan_comparison,
    _extract_and_save_nutrition_from_notes,
)
from data_processing import _load_summary, _safe_float, _classify_type, _workout_distance, _detect_brick_sessions

router = APIRouter()


async def _refresh_notification_history(conn):
    """Reload notification history into in-memory status cache."""
    rows = await db.notification_get_all(conn, limit=200)
    async with _insight_status_lock:
        _insight_status["history"] = rows


def _combine_note_with_files(note: str, files: list) -> str:
    """Combine a user note with image file references for LLM context."""
    if not files:
        return note
    img_refs = "\n".join(f"[IMAGE — use Read tool to view: {fp}]" for fp in files)
    return f"{note}\n\n{img_refs}" if note else img_refs


def _is_file_old(file_path: Path, cutoff_iso: str) -> bool:
    """Check if file modification time is older than cutoff date."""
    try:
        return datetime.fromtimestamp(file_path.stat().st_mtime, tz=timezone.utc).isoformat() < cutoff_iso
    except Exception:
        return False


def _delete_old_cli_sessions(sessions_dir: Path, cutoff_iso: str) -> int:
    """Delete old CLI session JSONL files and their subagent directories."""
    if not sessions_dir.exists():
        return 0

    deleted_count = 0
    for jsonl in sessions_dir.glob("*.jsonl"):
        if not _is_file_old(jsonl, cutoff_iso):
            continue
        try:
            jsonl.unlink()
            deleted_count += 1
            # Also delete subagents dir if present
            sub_dir = sessions_dir / jsonl.stem
            if sub_dir.exists() and sub_dir.is_dir():
                shutil.rmtree(sub_dir)
        except Exception:
            pass
    return deleted_count


def _delete_old_bak_files(sessions_dir: Path, cutoff_iso: str) -> int:
    """Delete old .bak backup files."""
    if not sessions_dir.exists():
        return 0

    deleted_count = 0
    for bak in sessions_dir.glob("*.jsonl.bak"):
        if _is_file_old(bak, cutoff_iso):
            try:
                bak.unlink()
                deleted_count += 1
            except Exception:
                pass
    return deleted_count


# ── AI status (public — no auth required) ────────────────────────────────────

@router.get("/api/ai-status")
async def get_ai_status():
    """Public endpoint: check if AI features are enabled."""
    try:
        await _require_ai()
        return {"ai_enabled": True}
    except HTTPException:
        return {"ai_enabled": False}


# ── Status & notifications ────────────────────────────────────────────────────

@router.get("/api/insights/status")
async def insights_status(request: Request):
    uid = _uid(request)
    import services.task_tracker as tracker
    async with _active_tasks_lock:
        tasks = list(_active_tasks.values())
    # Filter notification history to current user only
    user_history = [h for h in _insight_status.get("history", []) if h.get("user_id", 1) == uid]
    # Only expose batch progress to the user who started it
    is_owner = _insight_status.get("user_id") == uid
    if is_owner:
        return {**_insight_status, "history": user_history, "active_tasks": tasks, "cancelling": bool(tracker._insight_batch_cancel and _insight_status.get("running"))}
    # Other users see no running batch
    return {"running": False, "total": 0, "completed": 0, "current": "", "history": user_history, "active_tasks": tasks, "cancelling": False}


@router.post("/api/insights/notifications")
async def add_notification(request: Request):
    """Add a notification from the frontend (local LLM task completion)."""
    body = await request.json()
    label = body.get("label", "")
    detail = body.get("detail", "")
    link = body.get("link", "")
    status = body.get("status", "done")
    if status not in ("done", "error", "warning", "cancelled"):
        status = "done"
    if not label:
        return {"ok": False}
    uid = _uid(request)
    conn = await db.get_db()
    try:
        await db.notification_add(conn, label, detail, status=status, link=link, user_id=uid)
        await _refresh_notification_history(conn)
    finally:
        await conn.close()
    return {"ok": True}


@router.delete("/api/insights/notifications")
async def clear_notification_history(request: Request):
    """Clear all notification history from DB."""
    uid = _uid(request)
    conn = await db.get_db()
    try:
        await db.notification_clear(conn, user_id=uid)
        await _refresh_notification_history(conn)
    finally:
        await conn.close()
    return {"ok": True}


@router.delete("/api/insights/notifications/{notif_id}")
async def delete_single_notification(notif_id: int, request: Request):
    """Delete a single notification by ID."""
    uid = _uid(request)
    conn = await db.get_db()
    try:
        await conn.execute("DELETE FROM notification_history WHERE id = ? AND user_id = ?", (notif_id, uid))
        await conn.commit()
        # Refresh in-memory history (all users — filtered at read time)
        await _refresh_notification_history(conn)
    finally:
        await conn.close()
    return {"ok": True}


# ── Admin settings & cleanup ──────────────────────────────────────────────────

@router.get("/api/admin/settings")
async def admin_get_settings(request: Request):
    """Admin: get all app settings."""
    _require_admin(request)
    conn = await db.get_db()
    try:
        settings = await db.settings_get_all(conn)
        # Defaults
        settings.setdefault("ai_enabled", "0")
        settings.setdefault("session_retention_days", "210")  # 7 months
        settings.setdefault("session_rotation_kb", "800")
        settings.setdefault("upload_max_mb", "200")
        return settings
    finally:
        await conn.close()


_ALLOWED_SETTINGS = {
    "ai_enabled", "session_retention_days", "session_rotation_kb", "upload_max_mb",
    "auto_merge_enabled", "auto_merge_gap_minutes", "agent_model",
    "nutrition_auto_suggest",
    "notification_max_keep", "chat_summary_mode", "ai_rate_limit",
}


@router.patch("/api/admin/settings")
async def admin_update_settings(request: Request):
    """Admin: update app settings."""
    _require_admin(request)
    body = await request.json()
    from config import normalize_model, VALID_MODEL_ALIASES
    if "agent_model" in body:
        raw = body["agent_model"]
        if raw:
            normalized = normalize_model(raw)
            if normalized is None:
                raise HTTPException(400, f"Invalid model alias. Must be one of: {', '.join(sorted(VALID_MODEL_ALIASES))}")
            body["agent_model"] = normalized
    conn = await db.get_db()
    try:
        for key, value in body.items():
            if key not in _ALLOWED_SETTINGS:
                raise HTTPException(400, f"Unknown setting key: {key}")
            await db.setting_set(conn, key, str(value))
        return {"ok": True}
    finally:
        await conn.close()


@router.post("/api/admin/cleanup-sessions")
async def admin_cleanup_sessions(request: Request):
    """Admin: delete chat sessions + CLI session files older than retention period."""
    _require_admin(request)
    conn = await db.get_db()
    try:
        days_str = await db.setting_get(conn, "session_retention_days", "210")
        days = int(days_str)
        cutoff = (datetime.now(tz=timezone.utc) - timedelta(days=days)).isoformat()

        # 1) Find old chat sessions from DB
        cursor = await conn.execute(
            "SELECT DISTINCT session_id FROM chat_history WHERE timestamp < ?", (cutoff,)
        )
        old_sessions = [row["session_id"] for row in await cursor.fetchall()]

        # Delete chat history for old sessions in batch
        deleted_chat = len(old_sessions)
        if old_sessions:
            placeholders = ",".join("?" for _ in old_sessions)
            await conn.execute(f"DELETE FROM chat_history WHERE session_id IN ({placeholders})", old_sessions)
            await conn.execute(f"DELETE FROM chat_session_titles WHERE session_id IN ({placeholders})", old_sessions)
        await conn.commit()

        # 2) Delete old CLI session JSONL files
        deleted_cli = _delete_old_cli_sessions(_SESSIONS_DIR, cutoff)

        # 3) Delete old .bak files
        deleted_bak = _delete_old_bak_files(_SESSIONS_DIR, cutoff)

        # 4) Clean up agent_sessions table entries
        await conn.execute("DELETE FROM agent_sessions WHERE last_used_at < ?", (cutoff,))
        await conn.commit()

        return {
            "deleted_chat_sessions": deleted_chat,
            "deleted_cli_sessions": deleted_cli,
            "deleted_bak_files": deleted_bak,
            "cutoff_date": cutoff[:10],
        }
    finally:
        await conn.close()


@router.get("/api/insights/missing")
async def insights_missing(request: Request, since_date: str = INSIGHT_CUTOFF_DATE):
    """Return workouts that don't have insights yet."""
    workouts = _load_summary(_user_data_dir(request))
    uid = _uid(request)
    conn = await db.get_db()
    try:
        existing = await db.insight_get_existing_nums(conn, user_id=uid)
    finally:
        await conn.close()
    result = []
    for w in workouts:
        if w.get("startDate", "")[:10] < since_date:
            continue
        wnum = int(w.get("workout_num", 0))
        if wnum not in existing:
            result.append({
                "workout_num": wnum,
                "date": w.get("startDate", "")[:10],
                "type": w.get("type", ""),
                "discipline": _classify_type(w.get("type", "")),
                "duration_min": _safe_float(w.get("duration_min")),
                "distance_km": _workout_distance(w),
            })
    return result


# ── Workout insights ──────────────────────────────────────────────────────────

@router.get("/api/insights/workout/{num}")
async def insights_workout(num: int, request: Request):
    uid = _uid(request)
    conn = await db.get_db()
    try:
        return await db.insight_get(conn, num, user_id=uid) or {}
    finally:
        await conn.close()


@router.get("/api/insights/all")
async def insights_all(request: Request, limit: int = Query(default=None, ge=1, le=500)):
    uid = _uid(request)
    conn = await db.get_db()
    try:
        insights = await db.insight_get_all(conn, user_id=uid)
    finally:
        await conn.close()

    if limit is not None:
        insights = insights[:limit]

    if not insights:
        return insights

    workouts = _load_summary(_user_data_dir(request))
    insight_nums = {ins["workout_num"] for ins in insights}
    by_num = {}
    for w in workouts:
        wnum = int(w.get("workout_num", 0))
        if wnum in insight_nums:
            w["discipline"] = _classify_type(w.get("type", ""))
            w["distance_km"] = _workout_distance(w)
            by_num[wnum] = w

    for ins in insights:
        w = by_num.get(ins["workout_num"])
        if w:
            ins.update({
                "duration_min": _safe_float(w.get("duration_min")),
                "distance_km": w["distance_km"],
                "discipline": w["discipline"],
                "hr_avg": _safe_float(w.get("HeartRate_average")),
                "hr_max": _safe_float(w.get("HeartRate_maximum")),
                "calories": _safe_float(w.get("ActiveEnergyBurned_sum")),
                "start_time": w.get("startDate", ""),
                "tz": w.get("meta_TimeZone", ""),
            })
    return insights


@router.post("/api/insights/dismiss")
async def insights_dismiss(request: Request):
    """Mark workout nums as dismissed so they don't appear in post-import modal again."""
    data = await request.json()
    nums = data.get("workout_nums", [])
    if not nums:
        return {"ok": True}
    uid = _uid(request)
    key = f"dismissed_insights_{uid}"
    conn = await db.get_db()
    try:
        existing = await db.setting_get(conn, key, "[]")
        current = set(json.loads(existing))
        current.update(int(n) for n in nums)
        await db.setting_set(conn, key, json.dumps(sorted(current)))
    finally:
        await conn.close()
    return {"ok": True}


@router.post("/api/insights/generate-batch")
async def insights_generate_batch(request: Request):
    await _require_ai()
    data = await request.json()
    since_date = data.get("since_date", INSIGHT_CUTOFF_DATE)
    to_date = data.get("to_date", "")
    user_context = data.get("user_context", None)  # {wnum_str: "note text", ...}
    user_files = data.get("user_files", None)  # {wnum_str: [file_path, ...], ...}
    lang = data.get("lang", "en")
    workout_nums = data.get("workout_nums", None)  # [int, ...] — specific workouts to generate
    include_raw_data = bool(data.get("include_raw_data", False))
    include_raw_data_nums = set(int(n) for n in data.get("include_raw_data_nums", []))  # per-workout override
    async with _insight_status_lock:
        if _insight_status["running"]:
            return {"status": "already_running", **_insight_status}
    uid = _uid(request)
    asyncio.create_task(_generate_insights_batch(since_date, to_date, user_id=uid, user_context=user_context, user_files=user_files, lang=lang, workout_nums=workout_nums, include_raw_data=include_raw_data, include_raw_data_nums=include_raw_data_nums))
    return {"status": "started"}


@router.post("/api/insights/generate/{num}")
async def insights_generate_one(num: int, request: Request):
    """Generate insight for a single workout. If part of a brick, generates combined brick insight."""
    await _require_ai()
    from services.task_tracker import _register_task, _unregister_task
    uid = _uid(request)
    dd = _user_data_dir(request)
    user_note = ""
    lang = "en"
    user_files = []
    include_raw_data = False
    try:
        body = await request.json()
        user_note = body.get("user_note", "")
        lang = body.get("lang", "en")
        user_files = body.get("user_files", [])  # list of file paths
        include_raw_data = bool(body.get("include_raw_data", False))
    except Exception:
        pass
    workouts = _load_summary(dd)
    w = next((x for x in workouts if int(x.get("workout_num", 0)) == num), None)
    if not w:
        raise HTTPException(404, f"Workout {num} not found")
    w["discipline"] = _classify_type(w.get("type", ""))
    w["distance_km"] = _workout_distance(w)
    wdate = w.get("startDate", "")[:10]

    # Enrich only same-day workouts for brick detection (not all workouts)
    same_day = [aw for aw in workouts if aw.get("startDate", "")[:10] == wdate]
    for aw in same_day:
        aw["discipline"] = _classify_type(aw.get("type", ""))
        aw["distance_km"] = _workout_distance(aw)
    bricks = _detect_brick_sessions(same_day)
    brick = None
    for b in bricks:
        brick_nums = [int(bw.get("workout_num", 0)) for bw in b["workouts"]]
        if num in brick_nums:
            brick = b
            break

    conn = await db.get_db()
    try:
        plans = await db.plan_get_by_date(conn, wdate, user_id=uid)
        existing = await db.insight_get(conn, num, user_id=uid)
    finally:
        await conn.close()

    reason = "user requested re-generation (update)" if existing else ""

    if brick:
        # Generate combined brick insight
        brick_workouts = brick["workouts"]
        brick_nums = [int(bw.get("workout_num", 0)) for bw in brick_workouts]
        task_id = f"insight-brick-{'-'.join(str(n) for n in brick_nums)}-user{uid}"
        await _register_task(task_id, f"Brick Insight #{'/'.join(str(n) for n in brick_nums)}", f"/insights#workout-{num}")
        try:
            # Build plans map for all brick workouts
            plans_map = {}
            conn = await db.get_db()
            try:
                for bw in brick_workouts:
                    bw_num = int(bw.get("workout_num", 0))
                    bw_date = bw.get("startDate", "")[:10]
                    plans_map[bw_num] = await db.plan_get_by_date(conn, bw_date, user_id=uid)
            finally:
                await conn.close()

            combined_note = _combine_note_with_files(user_note, user_files)
            user_notes = {str(num): combined_note} if combined_note else {}
            insight_text, plan_cmp = await _generate_brick_insight(
                brick_workouts, plans_map, dd, uid, reason=reason,
                user_notes=user_notes, lang=lang,
                include_raw_data=include_raw_data,
            )
            if not insight_text:
                raise HTTPException(500, "Failed to generate brick insight")

            # Save same insight under ALL brick workout_nums
            conn = await db.get_db()
            try:
                for bw in brick_workouts:
                    bw_num = int(bw.get("workout_num", 0))
                    bw_date = bw.get("startDate", "")[:10]
                    await db.insight_save(conn, bw_num, bw_date, bw.get("type", ""),
                                          insight_text, plan_cmp, user_id=uid)
            finally:
                await conn.close()


            return {"insight": insight_text, "plan_comparison": plan_cmp, "brick_nums": brick_nums}
        finally:
            await _unregister_task(task_id)
    else:
        # Regular single workout insight
        task_id = f"insight-{num}-user{uid}"
        await _register_task(task_id, f"Insight #{num}", f"/insights#workout-{num}")
        try:
            combined_note = _combine_note_with_files(user_note, user_files)
            insight_text, plan_cmp = await _generate_insight_for_workout(w, plans, dd, uid, reason=reason, user_note=combined_note, lang=lang, include_raw_data=include_raw_data)
            if not insight_text:
                raise HTTPException(500, "Failed to generate insight")

            conn = await db.get_db()
            try:
                await db.insight_save(conn, num, wdate, w.get("type", ""),
                                      insight_text, plan_cmp, user_id=uid)
            finally:
                await conn.close()


            return {"insight": insight_text, "plan_comparison": plan_cmp}
        finally:
            await _unregister_task(task_id)


@router.post("/api/insights/fix/{num}")
async def insights_fix_one(num: int, request: Request):
    """QA fact-check an existing insight using Haiku (cheap). Corrects factual errors."""
    from services.claude_cli import _find_claude_cli, _track_usage, _llm_preflight_check, _build_cli_env
    from config import PROJECT_ROOT

    uid = _uid(request)
    dd = _user_data_dir(request)
    workouts = _load_summary(dd)
    w = next((x for x in workouts if int(x.get("workout_num", 0)) == num), None)
    if not w:
        raise HTTPException(404, f"Workout {num} not found")

    wdate = w.get("startDate", "")[:10]
    conn = await db.get_db()
    try:
        existing = await db.insight_get(conn, num, user_id=uid)
        plans = await db.plan_get_by_date(conn, wdate, user_id=uid)
    finally:
        await conn.close()

    if not existing or not existing.get("insight"):
        raise HTTPException(404, "No insight to fix")

    same_day = [aw for aw in workouts
                if aw.get("startDate", "")[:10] == wdate and int(aw.get("workout_num", 0)) != num]

    # Build fact sheet
    fact_lines = [f"Workout #{num}: {w.get('type', '')} on {wdate}, {float(w.get('duration_min', 0)):.1f}min"]
    dist = _workout_distance(w)
    if dist > 0:
        disc = _classify_type(w.get("type", ""))
        fact_lines.append(f"Distance: {dist*1000:.0f}m" if disc == "swim" else f"Distance: {dist:.2f}km")
    if same_day:
        fact_lines.append("Other workouts today:")
        for sd in same_day:
            fact_lines.append(f"  - #{sd.get('workout_num')} {sd.get('type', '')} ({float(sd.get('duration_min', 0)):.0f}min)")
    if plans:
        fact_lines.append("Training plan for this day:")
        for p in plans:
            fact_lines.append(f"  - {p.get('discipline', '').upper()}: {p.get('title', '')} ({p.get('duration_planned_min', 0)}min, {p.get('distance_planned_km', 0)}km)")

    prompt = (
        "## QA REVIEW — Fact-check this insight\n\n"
        "### ACTUAL DATA (ground truth)\n" + "\n".join(fact_lines)
        + "\n\n### INSIGHT TO REVIEW\n" + existing["insight"]
        + "\n\n### INSTRUCTIONS\n"
        "Check for factual errors. Common errors:\n"
        "- Claiming a workout was skipped when it was done (check OTHER WORKOUTS)\n"
        "- Wrong distances, durations, or dates\n\n"
        "If accurate, output it UNCHANGED. If errors exist, output the CORRECTED full insight. "
        "No commentary — just the final text."
    )

    # Use Haiku via direct CLI call (cheap)
    cli = _find_claude_cli()
    if not cli:
        raise HTTPException(500, "Claude CLI not found")
    preflight_err = await _llm_preflight_check()
    if preflight_err:
        raise HTTPException(503, preflight_err)

    env = _build_cli_env()
    proc = await asyncio.create_subprocess_exec(
        cli, "--bare", "-p", prompt, "--output-format", "json", "--model", "haiku",
        stdout=asyncio.subprocess.PIPE, stderr=asyncio.subprocess.PIPE,
        cwd=str(PROJECT_ROOT), env=env,
    )
    stdout, _ = await asyncio.wait_for(proc.communicate(), timeout=60)
    if proc.returncode != 0:
        raise HTTPException(500, "QA check failed")

    result = json.loads(stdout.decode("utf-8", errors="replace"))
    asyncio.create_task(_track_usage(result, "fix-insight", agent_name="haiku-qa", user_id=uid))
    qa_text = result.get("result", "").strip()

    if not qa_text or len(qa_text) < 50:
        return {"changed": False, "insight": existing["insight"]}

    changed = qa_text.strip() != existing["insight"].strip()
    if changed:
        qa_text, plan_cmp = _split_plan_comparison(qa_text)
        if not plan_cmp:
            plan_cmp = existing.get("plan_comparison", "")
        conn = await db.get_db()
        try:
            await db.insight_save(conn, num, wdate, w.get("type", ""), qa_text, plan_cmp, user_id=uid)
        finally:
            await conn.close()
        logger.info(f"Fix-insight corrected #{num}")
    return {"changed": changed, "insight": qa_text}


# ── General insights ──────────────────────────────────────────────────────────

@router.get("/api/insights/general")
async def insights_general_get(request: Request):
    uid = _uid(request)
    conn = await db.get_db()
    try:
        return await db.general_insight_get_latest(conn, user_id=uid) or {}
    finally:
        await conn.close()


@router.delete("/api/insights/general")
async def insights_general_delete(request: Request):
    """Delete the general assessment. Does NOT delete per-workout insights."""
    uid = _uid(request)
    conn = await db.get_db()
    try:
        await db.general_insight_delete(conn, user_id=uid)
    finally:
        await conn.close()
    return {"ok": True}


@router.post("/api/insights/general/generate")
async def insights_general_generate(request: Request):
    await _require_ai()
    from services.task_tracker import _register_task, _unregister_task
    from services.coach_preamble import _build_coach_preamble
    from services.claude_cli import _call_agent

    data = await request.json() if request.headers.get("content-type", "").startswith("application/json") else {}
    from_date = data.get("from_date", "")
    to_date = data.get("to_date", "")
    feedback = data.get("feedback", "").strip()

    workouts = _load_summary(_user_data_dir(request))

    if from_date and to_date:
        recent = [w for w in workouts if from_date <= w.get("startDate", "")[:10] <= to_date]
    elif from_date:
        recent = [w for w in workouts if w.get("startDate", "")[:10] >= from_date]
    else:
        cutoff = (datetime.now() - timedelta(weeks=8)).strftime("%Y-%m-%d")
        recent = [w for w in workouts if w.get("startDate", "")[:10] >= cutoff]

    for w in recent:
        w["discipline"] = _classify_type(w.get("type", ""))
        w["distance_km"] = _workout_distance(w)

    if not recent:
        raise HTTPException(400, "No workouts in the selected date range")

    uid = _uid(request)
    conn = await db.get_db()
    try:
        primary_event = await db.events_get_primary(conn, uid)
    finally:
        await conn.close()

    lang = data.get("lang", "en")
    preamble = await _build_coach_preamble(uid, lang=lang)
    prompt = _build_general_prompt(recent, primary_event, preamble)

    # If feedback provided, append the previous assessment and athlete feedback
    if feedback:
        conn = await db.get_db()
        try:
            prev = await db.general_insight_get_latest(conn, user_id=uid)
        finally:
            await conn.close()
        if prev and prev.get("content"):
            prompt += (
                f"\n\nPREVIOUS ASSESSMENT:\n{prev['content']}\n\n"
                f"ATHLETE FEEDBACK: {feedback}\n\n"
                "Revise your assessment based on the athlete's feedback. "
                "Keep the same structure but adjust your analysis."
            )

    # Use main-coach agent with per-user persistent session
    session_name = f"main-coach-general-user{uid}"
    task_id = f"general-insight-user{uid}"
    await _register_task(task_id, "General Insight", "/insights")
    try:
        content, _ = await _call_agent("main-coach", prompt, session_name, user_id=uid)

        if not content:
            # Fallback to stateless call
            content = await _call_claude_for_insight(prompt, user_id=uid)
        if not content:
            raise HTTPException(500, "Failed to generate general insight")

        conn = await db.get_db()
        try:
            await db.general_insight_save(conn, content, user_id=uid)
        finally:
            await conn.close()

        return {"content": content}
    finally:
        await _unregister_task(task_id)


# ── Period insights ───────────────────────────────────────────────────────────

@router.get("/api/insights/period")
async def insights_period_get(request: Request, from_date: str = "", to_date: str = ""):
    uid = _uid(request)
    conn = await db.get_db()
    try:
        return await db.period_insight_get_all(conn, uid, from_date or None, to_date or None)
    finally:
        await conn.close()


@router.post("/api/insights/period/generate")
async def insights_period_generate(request: Request):
    """Generate one overall training assessment for a date range."""
    await _require_ai()
    from services.task_tracker import _register_task, _unregister_task
    from services.coach_preamble import _build_coach_preamble
    from services.claude_cli import _call_agent

    data = await request.json()
    from_date = data.get("from_date", "")
    to_date = data.get("to_date", "")
    lang = data.get("lang", "en")

    if not from_date or not to_date:
        raise HTTPException(400, "from_date and to_date required")

    uid = _uid(request)
    dd = _user_data_dir(request)

    # Load workouts in range (filter first, then enrich)
    workouts = [w for w in _load_summary(dd) if from_date <= w.get("startDate", "")[:10] <= to_date]
    for w in workouts:
        w["discipline"] = _classify_type(w.get("type", ""))
        w["distance_km"] = _workout_distance(w)

    # Load per-workout insights + nutrition in single connection
    conn = await db.get_db()
    try:
        all_insights = await db.insight_get_all(conn, user_id=uid)
        cursor = await conn.execute(
            "SELECT * FROM nutrition_log WHERE user_id = ? AND date >= ? AND date <= ? ORDER BY date, meal_time",
            (uid, from_date, to_date))
        nutrition = [dict(r) for r in await cursor.fetchall()]
    finally:
        await conn.close()
    range_insights = [i for i in all_insights if from_date <= i.get("workout_date", "")[:10] <= to_date]

    # Load recovery data
    recovery = _load_recovery_data_range(dd, from_date, to_date)

    preamble = await _build_coach_preamble(uid, lang=lang)

    # Build a comprehensive prompt with ALL data
    date_label = f"{from_date} to {to_date}"
    parts = [preamble, f"\n\n## PERIOD ASSESSMENT: {date_label}\n"]

    # Workout summary by discipline
    disc_groups = {}
    for w in workouts:
        disc = w["discipline"]
        disc_groups.setdefault(disc, []).append(w)

    if workouts:
        parts.append(f"### Workouts ({len(workouts)} total)\n")
        for disc, ws in sorted(disc_groups.items()):
            total_dur = sum(_safe_float(w.get("duration_min")) for w in ws)
            total_dist = sum(w["distance_km"] for w in ws)
            parts.append(f"- **{disc}**: {len(ws)} workouts, {round(total_dur)}min total"
                         + (f", {round(total_dist, 1)}km" if total_dist > 0 else ""))
    else:
        parts.append("### No workouts in this period\n")

    # Per-workout insights (summaries)
    if range_insights:
        parts.append(f"\n### Existing Workout Insights ({len(range_insights)})\n")
        for ins in range_insights:
            snippet = (ins.get("insight") or "")[:300]
            parts.append(f"- #{ins['workout_num']} {ins.get('workout_type', '')} ({ins.get('workout_date', '')[:10]}): {snippet}...")

    # Recovery data
    if recovery:
        parts.append(f"\n### Recovery Data ({len(recovery)} days)\n")
        for r in recovery[-14:]:  # last 14 days max
            line = f"- {r.get('date', '')}: "
            items = []
            if r.get("resting_hr"): items.append(f"RHR {r['resting_hr']}")
            if r.get("hrv_sdnn_ms"): items.append(f"HRV {r['hrv_sdnn_ms']}ms")
            if r.get("sleep_total_min"): items.append(f"Sleep {round(float(r['sleep_total_min'])/60, 1)}h")
            if items:
                parts.append(line + ", ".join(items))

    # Nutrition summary
    if nutrition:
        total_cal = sum(_safe_float(m.get("calories", 0)) for m in nutrition)
        parts.append(f"\n### Nutrition ({len(nutrition)} meals logged, ~{round(total_cal)} cal total)\n")

    lang_label = "Hebrew" if lang == "he" else "English"
    parts.append(
        f"\n\n## YOUR TASK\n"
        f"⚠️ LANGUAGE: Respond ENTIRELY in **{lang_label}**.\n\n"
        "Write a comprehensive training assessment for this period. Cover:\n"
        "1. **Period Overview**: volume, intensity, consistency summary\n"
        "2. **Discipline Breakdown**: progress in each discipline (swim/bike/run)\n"
        "3. **Recovery & Sleep**: patterns, quality, any concerns\n"
        "4. **Nutrition**: adequacy relative to training load\n"
        "5. **Strengths**: what went well this period\n"
        "6. **Concerns**: what needs attention\n"
        "7. **Recommendations**: top 3 priorities for the next period\n"
        "\nBe specific with numbers. Be honest — don't sugarcoat. Use the workout insights above for details.\n"
    )

    prompt = "\n".join(parts)
    session_name = f"main-coach-period-user{uid}"
    task_id = f"period-insight-user{uid}"
    await _register_task(task_id, "Period Insight", "/insights")
    try:
        text, _ = await _call_agent("main-coach", prompt, session_name, user_id=uid, max_turns=1)

        if not text:
            raise HTTPException(500, "Failed to generate assessment")

        # Save
        conn = await db.get_db()
        try:
            await db.period_insight_save(conn, "overall", from_date, to_date, text, user_id=uid)
        finally:
            await conn.close()

        return {"ok": True}
    finally:
        await _unregister_task(task_id)


@router.delete("/api/insights/period/{insight_id}")
async def insights_period_delete_one(insight_id: int, request: Request):
    uid = _uid(request)
    conn = await db.get_db()
    try:
        await db.period_insight_delete(conn, insight_id, uid)
    finally:
        await conn.close()
    return {"ok": True}


@router.delete("/api/insights/period")
async def insights_period_delete_range(request: Request, from_date: str = "", to_date: str = ""):
    uid = _uid(request)
    if not from_date or not to_date:
        raise HTTPException(400, "from_date and to_date required")
    conn = await db.get_db()
    try:
        await db.period_insight_delete_range(conn, from_date, to_date, uid)
    finally:
        await conn.close()
    return {"ok": True}


# ── Chat/streaming ────────────────────────────────────────────────────────────

@router.get("/api/chat/streaming")
async def chat_streaming_sessions(request: Request):
    """Return list of streaming session objects with mode and agent info (user-scoped)."""
    uid = _uid(request)
    return [
        {"session_id": sid, **info}
        for sid, info in _chat_streaming.items()
        if info.get("user_id") == uid
    ]


@router.post("/api/insights/batch/stop")
async def insights_batch_stop(request: Request):
    """Stop the running batch insight generation and kill active Claude CLI processes."""
    import services.task_tracker as tracker
    uid = _uid(request)
    # Only the user who started the batch can cancel it
    if tracker._insight_batch_user is not None and tracker._insight_batch_user != uid:
        return {"ok": False, "error": "not your batch"}
    tracker._insight_batch_cancel = True
    killed = 0
    # Kill all active procs, then sweep again after a short delay to catch any that spawned in between
    for proc in list(tracker._insight_active_procs):
        try:
            proc.kill()
            killed += 1
        except (ProcessLookupError, OSError):
            pass
    await asyncio.sleep(0.5)
    for proc in list(tracker._insight_active_procs):
        try:
            proc.kill()
            killed += 1
        except (ProcessLookupError, OSError):
            pass
    logger.info(f"Batch insight generation cancel requested (killed {killed} active process(es))")
    cancel_entry = {
        "label": "Insight Generation",
        "detail": "Cancelled by user",
        "status": "cancelled",
        "link": "/insights",
        "finished_at": datetime.now().strftime("%Y-%m-%d %H:%M"),
        "user_id": uid,
    }
    async with _insight_status_lock:
        _insight_status["history"].insert(0, cancel_entry)
        _insight_status["history"] = _insight_status["history"][:50]
    try:
        conn = await db.get_db()
        try:
            await db.notification_add(conn, "Insight Generation", "Cancelled by user", status="cancelled", link="/insights", user_id=uid)
        finally:
            await conn.close()
    except Exception:
        pass
    return {"ok": True, "killed": killed}


@router.get("/api/admin/chat-sessions")
async def admin_chat_sessions(request: Request):
    from config import coach_session_id
    _require_admin(request)
    conn = await db.get_db()
    try:
        sessions = await db.chat_get_sessions(conn, user_id=None)
    finally:
        await conn.close()
    # Enrich with Claude session UUID + file path + backup files
    for s in sessions:
        agent = s.get("agent_name", "main-coach")
        if agent == "main-coach":
            claude_uuid = coach_session_id(f"main-coach-{s['session_id']}")
        else:
            claude_uuid = coach_session_id(f"{agent}-user{s.get('user_id', 1)}")
        s["claude_session_uuid"] = claude_uuid
        jsonl_path = _SESSIONS_DIR / f"{claude_uuid}.jsonl"
        try:
            jsonl_stat = jsonl_path.stat()
            s["claude_file_path"] = str(jsonl_path)
            s["claude_file_size"] = jsonl_stat.st_size
        except FileNotFoundError:
            s["claude_file_path"] = ""
            s["claude_file_size"] = 0
        bak_files = sorted(_SESSIONS_DIR.glob(f"{claude_uuid}.*.jsonl.bak"), reverse=True) if _SESSIONS_DIR.exists() else []
        s["bak_files"] = [{"path": str(b), "size": b.stat().st_size, "name": b.name} for b in bak_files]
    return sessions


@router.get("/api/admin/chat-history/{session_id}")
async def admin_chat_history(session_id: str, request: Request):
    _require_admin(request)
    conn = await db.get_db()
    try:
        return await db.chat_get_history(conn, session_id, user_id=None)
    finally:
        await conn.close()
