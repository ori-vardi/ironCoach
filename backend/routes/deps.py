"""Shared route dependencies — helpers used by all route modules."""

import shutil
from pathlib import Path

from fastapi import HTTPException, Request

from config import TRAINING_DATA, logger


async def _require_ai():
    """Raise 403 if AI features are disabled in admin settings."""
    import aiosqlite
    from database import DB_PATH
    async with aiosqlite.connect(str(DB_PATH)) as conn:
        conn.row_factory = aiosqlite.Row
        cursor = await conn.execute("SELECT value FROM app_settings WHERE key = 'ai_enabled'")
        row = await cursor.fetchone()
        if not row or row["value"] != "1":
            raise HTTPException(403, "AI features are disabled. An admin can enable them in Admin > Settings.")


def _require_admin(request: Request):
    user = getattr(request.state, "user", None)
    if not user or user["role"] != "admin":
        raise HTTPException(403, "Admin access required")


def _uid(request: Request) -> int:
    """Get current user's ID from request state."""
    user = getattr(request.state, "user", None)
    if not user:
        raise HTTPException(401, "Not authenticated")
    return user["id"]


def _user_data_dir(request_or_uid) -> Path:
    """Return training data directory for the given user.
    All users get TRAINING_DATA/users/{user_id}/.
    Shared files (export.xml, workout-routes) stay at TRAINING_DATA root.
    """
    if isinstance(request_or_uid, int):
        uid = request_or_uid
    else:
        uid = _uid(request_or_uid)
    user_dir = TRAINING_DATA / "users" / str(uid)
    user_dir.mkdir(parents=True, exist_ok=True)
    return user_dir


def _migrate_user1_data():
    """Move user 1's workout data from training_data/ root to training_data/users/1/."""
    user1_dir = TRAINING_DATA / "users" / "1"
    summary_at_root = TRAINING_DATA / "00_workouts_summary.csv"
    summary_in_user = user1_dir / "00_workouts_summary.csv"

    # Skip if already migrated or no data at root
    if not summary_at_root.exists() or summary_in_user.exists():
        return

    logger.info("Migrating user 1 data to training_data/users/1/ ...")
    user1_dir.mkdir(parents=True, exist_ok=True)

    # Move global data files (per-user, NOT shared)
    for name in ("00_workouts_summary.csv", "body_metrics.csv", "daily_aggregates.csv",
                 "recovery_data.csv", ".export_state.json"):
        src = TRAINING_DATA / name
        if src.exists():
            shutil.move(str(src), str(user1_dir / name))

    # Move workouts/ subfolder
    workouts_root = TRAINING_DATA / "workouts"
    workouts_user = user1_dir / "workouts"
    if workouts_root.exists() and not workouts_user.exists():
        shutil.move(str(workouts_root), str(workouts_user))

    # Move any legacy flat workout files
    for f in TRAINING_DATA.glob("workout_*"):
        if f.is_file():
            shutil.move(str(f), str(user1_dir / f.name))

    logger.info("Migration complete: user 1 data moved to training_data/users/1/")


async def _load_user_hr(uid: int) -> dict:
    """Load per-user HR settings (DB > calculated > config fallback).

    Returns dict with hr_max, hr_rest, hr_lthr, hr_zones, locked, source.
    """
    import database as db
    from data_processing.hr_zones import resolve_hr_settings

    conn = await db.get_db()
    try:
        hr_db = await db.hr_settings_get(conn, uid)
        profile = await db.user_get_profile(conn, uid)
    finally:
        await conn.close()
    return resolve_hr_settings(hr_db, profile)
