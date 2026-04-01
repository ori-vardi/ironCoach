"""Weekly nutrition targets auto-suggest scheduler.

Runs every Sunday at 06:00 for each user who has enabled it.
Respects both admin-level ai_enabled and per-user nutrition_auto_suggest settings.
"""

import asyncio
import json
import re
from datetime import datetime, timedelta

import database as db
from config import logger
from routes.deps import _user_data_dir

# Scheduler constants
RECENT_WORKOUTS_DAYS = 7
SUNDAY_WEEKDAY = 6
TARGET_HOUR = 6
CHECK_INTERVAL_SEC = 3600

_scheduler_task = None
_scheduler_lock = asyncio.Lock()


async def _auto_suggest_for_user(uid: int):
    """Run AI nutrition target suggestion for a single user."""
    from services.coach_preamble import _build_coach_preamble
    from services.claude_cli import _find_claude_cli
    from services.insights_engine import _call_claude_for_insight
    from data_processing import _load_summary

    if not _find_claude_cli():
        logger.warning("nutrition_scheduler: Claude CLI not available, skipping")
        return

    preamble = await _build_coach_preamble(uid)
    dd = _user_data_dir(uid)
    workouts = _load_summary(dd)

    cutoff = (datetime.now() - timedelta(days=RECENT_WORKOUTS_DAYS)).strftime("%Y-%m-%d")
    recent = [w for w in workouts if (w.get("startDate") or "") >= cutoff]
    week_summary = ""
    if recent:
        total_min = sum(float(w.get("duration_min", 0) or 0) for w in recent)
        disciplines = {}
        for w in recent:
            d = w.get("discipline", "other")
            disciplines[d] = disciplines.get(d, 0) + 1
        week_summary = f"Last {RECENT_WORKOUTS_DAYS} days: {len(recent)} workouts, {total_min:.0f} min total. "
        week_summary += ", ".join(f"{d}: {c}x" for d, c in disciplines.items())

    prompt = f"""{preamble}

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
        if not result:
            logger.warning(f"nutrition_scheduler: empty response for user {uid}")
            return

        match = re.search(r'\{[^{}]*"calories"[^{}]*\}', result)
        if match:
            suggested = json.loads(match.group())
            targets = {
                "calories": int(suggested.get("calories", 2500)),
                "protein_g": int(suggested.get("protein_g", 150)),
                "carbs_g": int(suggested.get("carbs_g", 300)),
                "fat_g": int(suggested.get("fat_g", 80)),
                "water_ml": int(suggested.get("water_ml", 2500)),
            }
            conn = await db.get_db()
            try:
                await db.setting_set(conn, f"nutrition_targets_{uid}", json.dumps(targets))
                reasoning = suggested.get("reasoning", "")
                detail = f"Cal: {targets['calories']} | P: {targets['protein_g']}g | C: {targets['carbs_g']}g | F: {targets['fat_g']}g"
                if reasoning:
                    detail += f" — {reasoning}"
                await db.notification_add(
                    conn,
                    label="Nutrition targets updated",
                    detail=detail,
                    link="openTargets",
                    user_id=uid,
                )
                logger.info(f"nutrition_scheduler: updated targets for user {uid}: {targets}")
            finally:
                await conn.close()
        else:
            logger.warning(f"nutrition_scheduler: no JSON in response for user {uid}")
            conn = await db.get_db()
            try:
                await db.notification_add(conn, "Nutrition targets update failed",
                    "AI returned no valid JSON", status="error", link="openTargets", user_id=uid)
            finally:
                await conn.close()
    except Exception as e:
        logger.error(f"nutrition_scheduler: failed for user {uid}: {e}")
        try:
            conn = await db.get_db()
            try:
                err_msg = str(e)[:200]
                await db.notification_add(conn, "Nutrition targets update failed",
                    err_msg, status="error", link="openTargets", user_id=uid)
            finally:
                await conn.close()
        except Exception:
            pass  # Don't fail the scheduler if notification save fails


async def _run_weekly_suggest():
    """Check if it's Sunday and run auto-suggest for all opted-in users."""
    conn = await db.get_db()
    try:
        # Check admin-level AI enabled
        ai_enabled = await db.setting_get(conn, "ai_enabled", "0")
        if ai_enabled != "1":
            logger.debug("nutrition_scheduler: AI disabled globally, skipping")
            return

        # Check admin-level auto-suggest enabled
        auto_enabled = await db.setting_get(conn, "nutrition_auto_suggest", "0")
        if auto_enabled != "1":
            logger.debug("nutrition_scheduler: auto-suggest disabled in admin, skipping")
            return

        # Get all users
        cursor = await conn.execute("SELECT id FROM users")
        users = await cursor.fetchall()
        user_ids = [u["id"] for u in users]

        # Batch-load all per-user settings at once
        settings_cursor = await conn.execute(
            "SELECT key, value FROM app_settings WHERE key LIKE 'nutrition_auto_suggest_%' AND key != 'nutrition_auto_suggest'"
        )
        settings = {row["key"]: row["value"] for row in await settings_cursor.fetchall()}

        # Filter opted-in users
        opted_in = []
        for uid in user_ids:
            setting_key = f"nutrition_auto_suggest_{uid}"
            enabled = settings.get(setting_key, "1")
            if enabled == "1":
                opted_in.append(uid)
            else:
                logger.debug(f"nutrition_scheduler: user {uid} opted out, skipping")
    finally:
        await conn.close()

    today = datetime.now().strftime("%Y-%m-%d")
    for uid in opted_in:
        logger.info(f"nutrition_scheduler: running auto-suggest for user {uid}")
        await _auto_suggest_for_user(uid)

    if opted_in:
        conn2 = await db.get_db()
        try:
            for uid in opted_in:
                await db.setting_set(conn2, f"nutrition_auto_suggest_last_run_{uid}", today)
        finally:
            await conn2.close()


async def _scheduler_loop():
    """Background loop: check every hour, run on Sundays at 06:00."""
    while True:
        try:
            now = datetime.now()
            if now.weekday() == SUNDAY_WEEKDAY and now.hour == TARGET_HOUR:
                if not _scheduler_lock.locked():
                    async with _scheduler_lock:
                        conn = await db.get_db()
                        try:
                            last_run = await db.setting_get(conn, "nutrition_auto_suggest_last_run", "")
                        finally:
                            await conn.close()

                        today = now.strftime("%Y-%m-%d")
                        if last_run != today:
                            logger.info("nutrition_scheduler: Sunday 06:00 — running weekly auto-suggest")
                            await _run_weekly_suggest()

                            conn = await db.get_db()
                            try:
                                await db.setting_set(conn, "nutrition_auto_suggest_last_run", today)
                            finally:
                                await conn.close()
        except Exception as e:
            logger.error(f"nutrition_scheduler: loop error: {e}")

        await asyncio.sleep(CHECK_INTERVAL_SEC)


def start_scheduler():
    """Start the background scheduler task."""
    global _scheduler_task
    _scheduler_task = asyncio.create_task(_scheduler_loop())
    logger.info("nutrition_scheduler: background scheduler started")


def stop_scheduler():
    """Stop the background scheduler task."""
    global _scheduler_task
    if _scheduler_task:
        _scheduler_task.cancel()
        _scheduler_task = None


async def check_missed_run(uid: int):
    """Check if a weekly auto-suggest was missed for this user and run it now.

    Called on user login. If the last per-user run was more than 7 days ago
    (meaning at least one Sunday was skipped), trigger a single catch-up run.
    Skips if multiple runs were missed — only runs once to get current targets.
    Safe to call as a fire-and-forget task — all exceptions are caught.
    """
    try:
        await _check_missed_run_inner(uid)
    except Exception as e:
        logger.error(f"nutrition catch-up failed for user {uid}: {e}")


async def _check_missed_run_inner(uid: int):
    conn = await db.get_db()
    try:
        # Check all gates: admin AI enabled, admin auto-suggest, per-user opt-in
        ai_enabled = await db.setting_get(conn, "ai_enabled", "0")
        if ai_enabled != "1":
            return
        auto_enabled = await db.setting_get(conn, "nutrition_auto_suggest", "0")
        if auto_enabled != "1":
            return
        user_enabled = await db.setting_get(conn, f"nutrition_auto_suggest_{uid}", "1")
        if user_enabled != "1":
            return

        # Check per-user last run date
        last_run = await db.setting_get(conn, f"nutrition_auto_suggest_last_run_{uid}", "")
        if not last_run:
            # Never ran for this user — don't auto-trigger on first login,
            # only catch up if they previously had it running
            return

        last_run_date = datetime.strptime(last_run, "%Y-%m-%d")
        days_since = (datetime.now() - last_run_date).days
        if days_since < 7:
            return  # Last run was recent enough, no catch-up needed

        logger.info(f"nutrition_scheduler: catch-up for user {uid} (last run {days_since}d ago)")
    finally:
        await conn.close()

    # Run the auto-suggest (outside the conn block)
    await _auto_suggest_for_user(uid)

    # Update per-user last-run date
    conn = await db.get_db()
    try:
        await db.setting_set(conn, f"nutrition_auto_suggest_last_run_{uid}", datetime.now().strftime("%Y-%m-%d"))
    finally:
        await conn.close()
