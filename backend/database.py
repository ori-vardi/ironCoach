"""SQLite database setup and helpers using aiosqlite."""

import asyncio
import json
import aiosqlite
from datetime import datetime, timedelta, timezone
from pathlib import Path

DB_PATH = Path(__file__).resolve().parent / "data" / "dashboard.db"

CREATE_TABLES = """
CREATE TABLE IF NOT EXISTS users (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    username TEXT NOT NULL UNIQUE,
    password_hash TEXT NOT NULL,
    display_name TEXT NOT NULL DEFAULT '',
    role TEXT NOT NULL DEFAULT 'user',
    data_dir TEXT NOT NULL DEFAULT '',
    created_at TEXT NOT NULL DEFAULT '',
    height_cm REAL DEFAULT 0,
    weight_kg REAL DEFAULT 0,
    birth_date TEXT NOT NULL DEFAULT '',
    sex TEXT NOT NULL DEFAULT 'male',
    token_version INTEGER DEFAULT 0
);

CREATE TABLE IF NOT EXISTS server_logs (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    timestamp TEXT NOT NULL,
    username TEXT NOT NULL DEFAULT '',
    method TEXT NOT NULL,
    path TEXT NOT NULL,
    status INTEGER NOT NULL DEFAULT 0,
    duration_ms REAL NOT NULL DEFAULT 0
);

CREATE TABLE IF NOT EXISTS training_plan (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    date TEXT NOT NULL,
    discipline TEXT NOT NULL DEFAULT 'rest',
    title TEXT NOT NULL DEFAULT '',
    description TEXT NOT NULL DEFAULT '',
    duration_planned_min REAL DEFAULT 0,
    distance_planned_km REAL DEFAULT 0,
    intensity TEXT NOT NULL DEFAULT 'easy',
    phase TEXT NOT NULL DEFAULT 'build',
    completed INTEGER NOT NULL DEFAULT 0,
    notes TEXT NOT NULL DEFAULT '',
    user_id INTEGER NOT NULL DEFAULT 1
);

CREATE TABLE IF NOT EXISTS nutrition_log (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    date TEXT NOT NULL,
    meal_time TEXT NOT NULL DEFAULT '',
    meal_type TEXT NOT NULL DEFAULT 'snack',
    description TEXT NOT NULL DEFAULT '',
    calories REAL DEFAULT 0,
    protein_g REAL DEFAULT 0,
    carbs_g REAL DEFAULT 0,
    fat_g REAL DEFAULT 0,
    hydration_ml REAL DEFAULT 0,
    notes TEXT NOT NULL DEFAULT '',
    created_at TEXT NOT NULL DEFAULT '',
    user_id INTEGER NOT NULL DEFAULT 1
);

CREATE TABLE IF NOT EXISTS chat_history (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    session_id TEXT NOT NULL,
    role TEXT NOT NULL,
    content TEXT NOT NULL DEFAULT '',
    timestamp TEXT NOT NULL,
    file_path TEXT DEFAULT NULL,
    user_id INTEGER NOT NULL DEFAULT 1
);

CREATE TABLE IF NOT EXISTS workout_insights (
    workout_num INTEGER NOT NULL,
    workout_date TEXT NOT NULL,
    workout_type TEXT NOT NULL DEFAULT '',
    insight TEXT NOT NULL DEFAULT '',
    plan_comparison TEXT NOT NULL DEFAULT '',
    generated_at TEXT NOT NULL,
    user_id INTEGER NOT NULL DEFAULT 1,
    PRIMARY KEY (workout_num, user_id)
);

CREATE TABLE IF NOT EXISTS general_insights (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    content TEXT NOT NULL DEFAULT '',
    generated_at TEXT NOT NULL,
    user_id INTEGER NOT NULL DEFAULT 1
);

CREATE TABLE IF NOT EXISTS period_insights (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    category TEXT NOT NULL,
    from_date TEXT NOT NULL,
    to_date TEXT NOT NULL,
    content TEXT NOT NULL DEFAULT '',
    generated_at TEXT NOT NULL,
    user_id INTEGER NOT NULL DEFAULT 1,
    UNIQUE(category, from_date, to_date, user_id)
);

CREATE TABLE IF NOT EXISTS race_info (
    id INTEGER PRIMARY KEY CHECK (id = 1),
    race_name TEXT NOT NULL DEFAULT '',
    race_date TEXT NOT NULL DEFAULT '',
    swim_km REAL NOT NULL DEFAULT 1.9,
    bike_km REAL NOT NULL DEFAULT 90.0,
    run_km REAL NOT NULL DEFAULT 21.1,
    cutoff_swim TEXT NOT NULL DEFAULT '1:10',
    cutoff_bike TEXT NOT NULL DEFAULT '11:30',
    cutoff_finish TEXT NOT NULL DEFAULT '13:55',
    target_swim TEXT NOT NULL DEFAULT '',
    target_bike TEXT NOT NULL DEFAULT '',
    target_run TEXT NOT NULL DEFAULT '',
    target_total TEXT NOT NULL DEFAULT '',
    notes TEXT NOT NULL DEFAULT ''
);

CREATE TABLE IF NOT EXISTS events (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    event_name TEXT NOT NULL DEFAULT '',
    event_type TEXT NOT NULL DEFAULT 'half_ironman',
    event_date TEXT NOT NULL DEFAULT '',
    swim_km REAL DEFAULT 0,
    bike_km REAL DEFAULT 0,
    run_km REAL DEFAULT 0,
    cutoff_swim TEXT NOT NULL DEFAULT '',
    cutoff_bike TEXT NOT NULL DEFAULT '',
    cutoff_finish TEXT NOT NULL DEFAULT '',
    target_swim TEXT NOT NULL DEFAULT '',
    target_bike TEXT NOT NULL DEFAULT '',
    target_run TEXT NOT NULL DEFAULT '',
    target_total TEXT NOT NULL DEFAULT '',
    goal TEXT NOT NULL DEFAULT '',
    notes TEXT NOT NULL DEFAULT '',
    is_primary INTEGER NOT NULL DEFAULT 0,
    user_id INTEGER NOT NULL DEFAULT 1,
    created_at TEXT NOT NULL DEFAULT ''
);

CREATE TABLE IF NOT EXISTS agent_sessions (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    session_uuid TEXT NOT NULL UNIQUE,
    agent_name TEXT NOT NULL,
    context_key TEXT NOT NULL DEFAULT '',
    created_at TEXT NOT NULL,
    last_used_at TEXT NOT NULL,
    message_count INTEGER DEFAULT 1,
    notes TEXT DEFAULT ''
);

CREATE TABLE IF NOT EXISTS notification_history (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    label TEXT NOT NULL,
    detail TEXT NOT NULL DEFAULT '',
    status TEXT NOT NULL DEFAULT 'done',
    link TEXT DEFAULT '',
    finished_at TEXT NOT NULL,
    user_id INTEGER NOT NULL DEFAULT 1
);

CREATE TABLE IF NOT EXISTS chat_session_titles (
    session_id TEXT PRIMARY KEY,
    title TEXT NOT NULL DEFAULT '',
    user_id INTEGER NOT NULL DEFAULT 1,
    agent_name TEXT NOT NULL DEFAULT 'main-coach',
    mode TEXT NOT NULL DEFAULT 'coach'
);

CREATE TABLE IF NOT EXISTS token_usage (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    timestamp TEXT NOT NULL,
    source TEXT NOT NULL DEFAULT '',
    agent_name TEXT NOT NULL DEFAULT '',
    session_id TEXT NOT NULL DEFAULT '',
    input_tokens INTEGER NOT NULL DEFAULT 0,
    output_tokens INTEGER NOT NULL DEFAULT 0,
    cache_read_tokens INTEGER NOT NULL DEFAULT 0,
    cache_creation_tokens INTEGER NOT NULL DEFAULT 0,
    cost_usd REAL NOT NULL DEFAULT 0,
    model TEXT NOT NULL DEFAULT '',
    duration_ms REAL NOT NULL DEFAULT 0,
    user_id INTEGER NOT NULL DEFAULT 1
);

CREATE TABLE IF NOT EXISTS app_settings (
    key TEXT PRIMARY KEY,
    value TEXT NOT NULL DEFAULT ''
);

CREATE TABLE IF NOT EXISTS coach_memory (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    user_id INTEGER NOT NULL DEFAULT 1,
    content TEXT NOT NULL,
    created_at TEXT DEFAULT (datetime('now')),
    updated_at TEXT DEFAULT (datetime('now'))
);

CREATE INDEX IF NOT EXISTS idx_coach_memory_user ON coach_memory(user_id);

CREATE TABLE IF NOT EXISTS agent_memory (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    user_id INTEGER NOT NULL DEFAULT 1,
    agent_type TEXT NOT NULL,
    content TEXT NOT NULL,
    created_at TEXT DEFAULT (datetime('now')),
    updated_at TEXT DEFAULT (datetime('now'))
);
CREATE INDEX IF NOT EXISTS idx_agent_memory_user_type ON agent_memory(user_id, agent_type);
CREATE INDEX IF NOT EXISTS idx_chat_history_session ON chat_history(session_id, user_id);
CREATE INDEX IF NOT EXISTS idx_nutrition_date ON nutrition_log(date, user_id);
CREATE INDEX IF NOT EXISTS idx_nutrition_user_date ON nutrition_log(user_id, date);
CREATE INDEX IF NOT EXISTS idx_training_plan_date ON training_plan(date, user_id);
CREATE INDEX IF NOT EXISTS idx_notification_user ON notification_history(user_id);
CREATE INDEX IF NOT EXISTS idx_workout_insights_user ON workout_insights(user_id, workout_date);
CREATE INDEX IF NOT EXISTS idx_events_user ON events(user_id);
CREATE INDEX IF NOT EXISTS idx_token_usage_user ON token_usage(user_id, timestamp);
CREATE INDEX IF NOT EXISTS idx_period_insights_user ON period_insights(user_id, from_date, to_date);
"""

SEED_RACE = """
INSERT OR IGNORE INTO race_info (id, race_name, race_date, swim_km, bike_km, run_km,
    cutoff_swim, cutoff_bike, cutoff_finish, notes)
VALUES (1, '', '', 0, 0, 0, '', '', '', '');
"""


_wal_set = False


async def get_db() -> aiosqlite.Connection:
    global _wal_set
    db = await aiosqlite.connect(str(DB_PATH))
    db.row_factory = aiosqlite.Row
    if not _wal_set:
        await db.execute("PRAGMA journal_mode=WAL")
        _wal_set = True
    await db.execute("PRAGMA foreign_keys=ON")
    return db


async def init_db():
    DB_PATH.parent.mkdir(parents=True, exist_ok=True)
    db = await get_db()
    try:
        await db.executescript(CREATE_TABLES)
        await db.execute(SEED_RACE)
        await db.commit()
        # Migration: add user_id to tables that need it
        for table in ("training_plan", "nutrition_log", "chat_history",
                       "workout_insights", "notification_history"):
            cursor = await db.execute(f"PRAGMA table_info({table})")
            cols = {row[1] for row in await cursor.fetchall()}
            if "user_id" not in cols:
                await db.execute(
                    f"ALTER TABLE {table} ADD COLUMN user_id INTEGER NOT NULL DEFAULT 1"
                )
                await db.commit()
        # Migrate race_info → events table if events is empty
        cursor = await db.execute("SELECT COUNT(*) FROM events")
        event_count = (await cursor.fetchone())[0]
        if event_count == 0:
            cursor = await db.execute("SELECT * FROM race_info WHERE id = 1")
            race_row = await cursor.fetchone()
            if race_row:
                r = dict(race_row)
                # Only migrate if race_info has actual data (not empty seed)
                if r.get("race_name", "").strip():
                    await db.execute(
                        "INSERT INTO events (event_name, event_type, event_date, swim_km, bike_km, run_km, "
                        "cutoff_swim, cutoff_bike, cutoff_finish, target_swim, target_bike, target_run, "
                        "target_total, notes, is_primary, user_id, created_at) "
                        "VALUES (?, 'half_ironman', ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, 1, 1, ?)",
                        (r.get("race_name", ""), r.get("race_date", ""), r.get("swim_km", 0),
                         r.get("bike_km", 0), r.get("run_km", 0), r.get("cutoff_swim", ""),
                         r.get("cutoff_bike", ""), r.get("cutoff_finish", ""),
                         r.get("target_swim", ""), r.get("target_bike", ""), r.get("target_run", ""),
                         r.get("target_total", ""), r.get("notes", ""),
                         datetime.now(tz=timezone.utc).isoformat())
                    )
                    await db.commit()
        # Migration: add agent_name to chat_session_titles
        cursor = await db.execute("PRAGMA table_info(chat_session_titles)")
        cols = {row[1] for row in await cursor.fetchall()}
        if "agent_name" not in cols:
            await db.execute(
                "ALTER TABLE chat_session_titles ADD COLUMN agent_name TEXT NOT NULL DEFAULT 'main-coach'"
            )
            await db.commit()
        # Migration: add meal_time to nutrition_log
        cursor = await db.execute("PRAGMA table_info(nutrition_log)")
        cols = {row[1] for row in await cursor.fetchall()}
        if "meal_time" not in cols:
            await db.execute(
                "ALTER TABLE nutrition_log ADD COLUMN meal_time TEXT NOT NULL DEFAULT ''"
            )
            await db.commit()
        # Migration: add mode to chat_session_titles
        cursor = await db.execute("PRAGMA table_info(chat_session_titles)")
        cols = {row[1] for row in await cursor.fetchall()}
        if "mode" not in cols:
            await db.execute(
                "ALTER TABLE chat_session_titles ADD COLUMN mode TEXT NOT NULL DEFAULT 'coach'"
            )
            await db.commit()
        # No seed user — first user created via /api/auth/setup is automatically admin
    finally:
        await db.close()


# ── Training Plan helpers ────────────────────────────────────────────────

async def _execute_query(db, query: str, params: tuple):
    """Helper to execute query and return rows as dicts."""
    cursor = await db.execute(query, params)
    return [dict(row) for row in await cursor.fetchall()]


async def plan_get_all(db, user_id=None):
    if user_id:
        return await _execute_query(db, "SELECT * FROM training_plan WHERE user_id = ? ORDER BY date, id", (user_id,))
    return await _execute_query(db, "SELECT * FROM training_plan ORDER BY date, id", ())


async def plan_get_by_date(db, date_str, user_id=None):
    if user_id:
        return await _execute_query(db, "SELECT * FROM training_plan WHERE date = ? AND user_id = ? ORDER BY id", (date_str, user_id))
    return await _execute_query(db, "SELECT * FROM training_plan WHERE date = ? ORDER BY id", (date_str,))


async def plan_get_week(db, date_str, user_id=None):
    dt = datetime.strptime(date_str, "%Y-%m-%d")
    monday = dt - timedelta(days=dt.weekday())
    sunday = monday + timedelta(days=6)
    monday_str = monday.strftime("%Y-%m-%d")
    sunday_str = sunday.strftime("%Y-%m-%d")

    if user_id:
        return await _execute_query(db,
            "SELECT * FROM training_plan WHERE date >= ? AND date <= ? AND user_id = ? ORDER BY date, id",
            (monday_str, sunday_str, user_id))
    return await _execute_query(db,
        "SELECT * FROM training_plan WHERE date >= ? AND date <= ? ORDER BY date, id",
        (monday_str, sunday_str))


async def plan_create(db, data: dict, user_id=1):
    allowed = {"date", "discipline", "title", "description", "duration_planned_min",
               "distance_planned_km", "intensity", "phase", "completed", "notes"}
    filtered = {k: v for k, v in data.items() if k in allowed}
    filtered["user_id"] = user_id
    cols = ", ".join(filtered.keys())
    placeholders = ", ".join(["?"] * len(filtered))
    cursor = await db.execute(
        f"INSERT INTO training_plan ({cols}) VALUES ({placeholders})",
        list(filtered.values())
    )
    await db.commit()
    return cursor.lastrowid


async def plan_update(db, plan_id: int, data: dict, user_id=None):
    allowed = {"date", "discipline", "title", "description", "duration_planned_min",
               "distance_planned_km", "intensity", "phase", "completed", "notes"}
    filtered = {k: v for k, v in data.items() if k in allowed}
    if not filtered:
        return
    sets = ", ".join(f"{k} = ?" for k in filtered)
    if user_id:
        await db.execute(
            f"UPDATE training_plan SET {sets} WHERE id = ? AND user_id = ?",
            list(filtered.values()) + [plan_id, user_id])
    else:
        await db.execute(
            f"UPDATE training_plan SET {sets} WHERE id = ?",
            list(filtered.values()) + [plan_id])
    await db.commit()


async def plan_delete(db, plan_id: int, user_id=None):
    if user_id:
        await db.execute("DELETE FROM training_plan WHERE id = ? AND user_id = ?", (plan_id, user_id))
    else:
        await db.execute("DELETE FROM training_plan WHERE id = ?", (plan_id,))
    await db.commit()


# ── Nutrition helpers ────────────────────────────────────────────────────

async def nutrition_get_day(db, date_str, user_id=None):
    if user_id:
        return await _execute_query(db,
            "SELECT * FROM nutrition_log WHERE date = ? AND user_id = ? ORDER BY meal_time, id",
            (date_str, user_id))
    return await _execute_query(db, "SELECT * FROM nutrition_log WHERE date = ? ORDER BY meal_time, id", (date_str,))


async def nutrition_get_range(db, from_date, to_date, user_id=None):
    if user_id:
        return await _execute_query(db,
            "SELECT * FROM nutrition_log WHERE date >= ? AND date <= ? AND user_id = ? ORDER BY date, id",
            (from_date, to_date, user_id))
    return await _execute_query(db,
        "SELECT * FROM nutrition_log WHERE date >= ? AND date <= ? ORDER BY date, id",
        (from_date, to_date))


async def nutrition_create(db, data: dict, user_id=1):
    allowed = {"date", "meal_time", "meal_type", "description", "calories", "protein_g",
               "carbs_g", "fat_g", "hydration_ml", "notes", "created_at"}
    filtered = {k: v for k, v in data.items() if k in allowed}
    filtered["user_id"] = user_id
    cols = ", ".join(filtered.keys())
    placeholders = ", ".join(["?"] * len(filtered))
    cursor = await db.execute(
        f"INSERT INTO nutrition_log ({cols}) VALUES ({placeholders})",
        list(filtered.values())
    )
    await db.commit()
    return cursor.lastrowid


async def nutrition_update(db, entry_id: int, data: dict, user_id=None):
    allowed = {"date", "meal_time", "meal_type", "description", "calories", "protein_g",
               "carbs_g", "fat_g", "hydration_ml", "notes", "created_at"}
    filtered = {k: v for k, v in data.items() if k in allowed}
    if not filtered:
        return
    sets = ", ".join(f"{k} = ?" for k in filtered)
    if user_id:
        await db.execute(
            f"UPDATE nutrition_log SET {sets} WHERE id = ? AND user_id = ?",
            list(filtered.values()) + [entry_id, user_id])
    else:
        await db.execute(
            f"UPDATE nutrition_log SET {sets} WHERE id = ?",
            list(filtered.values()) + [entry_id])
    await db.commit()


async def nutrition_delete(db, entry_id: int, user_id=None):
    if user_id:
        await db.execute("DELETE FROM nutrition_log WHERE id = ? AND user_id = ?", (entry_id, user_id))
    else:
        await db.execute("DELETE FROM nutrition_log WHERE id = ?", (entry_id,))
    await db.commit()


async def nutrition_recent_items(db, user_id, limit=100):
    """Return distinct recently-used food items for autocomplete.
    Extracts items from notes JSON, deduplicates by base_name (or name fallback).
    Different macros per unit = different cache entry (e.g. scrambled vs boiled egg).
    Returns per-unit macros for quantity adjustment."""
    cursor = await db.execute(
        "SELECT notes, date FROM nutrition_log "
        "WHERE user_id = ? AND notes != '' "
        "ORDER BY date DESC, id DESC LIMIT 500",
        (user_id,))
    rows = await cursor.fetchall()
    seen = {}
    seen_count = 0
    for row in rows:
        if seen_count >= limit * 2:
            break
        try:
            items = json.loads(row["notes"])
            if not isinstance(items, list):
                continue
            for it in items:
                base = (it.get("base_name") or it.get("name") or "").strip()
                if not base:
                    continue
                qty = max(1, float(it.get("quantity", 1) or 1))
                u_cal = float(it.get("unit_calories") or 0) or (float(it.get("calories", 0) or 0) / qty)
                u_prot = float(it.get("unit_protein_g") or 0) or (float(it.get("protein_g", 0) or 0) / qty)
                u_carb = float(it.get("unit_carbs_g") or 0) or (float(it.get("carbs_g", 0) or 0) / qty)
                u_fat = float(it.get("unit_fat_g") or 0) or (float(it.get("fat_g", 0) or 0) / qty)
                key = f"{base.lower()}|{round(u_cal)}"
                if key not in seen:
                    seen[key] = {
                        "base_name": base,
                        "name": (it.get("name") or base).strip(),
                        "quantity": qty,
                        "unit_calories": round(u_cal, 1),
                        "unit_protein_g": round(u_prot, 1),
                        "unit_carbs_g": round(u_carb, 1),
                        "unit_fat_g": round(u_fat, 1),
                        "last_date": row["date"],
                    }
                    seen_count += 1
                    if seen_count >= limit * 2:
                        break
        except (json.JSONDecodeError, TypeError, ValueError):
            continue
    result = sorted(seen.values(), key=lambda x: x.get("last_date", ""), reverse=True)
    return result[:limit]


# ── Chat helpers ─────────────────────────────────────────────────────────

CHAT_MAX_TOTAL_BYTES = 100 * 1024 * 1024  # 100 MB per user
_prune_lock = asyncio.Lock()

async def chat_save(db, session_id, role, content, file_path=None, user_id=1):
    await db.execute(
        "INSERT INTO chat_history (session_id, role, content, timestamp, file_path, user_id) "
        "VALUES (?, ?, ?, ?, ?, ?)",
        (session_id, role, content, datetime.now(tz=timezone.utc).isoformat(), file_path, user_id)
    )
    await db.commit()
    import random
    if random.random() < 0.20:
        async with _prune_lock:
            await _chat_prune_old_sessions(db, user_id)


async def _chat_prune_old_sessions(db, user_id):
    """Delete oldest chat sessions when total content size exceeds threshold."""
    cursor = await db.execute(
        "SELECT session_id, SUM(LENGTH(content)) as sz, MAX(timestamp) as last_ts "
        "FROM chat_history WHERE user_id = ? GROUP BY session_id ORDER BY last_ts DESC",
        (user_id,)
    )
    sessions = await cursor.fetchall()
    total = sum(row[1] for row in sessions)
    if total <= CHAT_MAX_TOTAL_BYTES:
        return
    # Delete oldest sessions first until under limit
    deleted = []
    for row in reversed(sessions):
        if total <= CHAT_MAX_TOTAL_BYTES:
            break
        sid, sz = row[0], row[1]
        # Get title before deleting
        title_cur = await db.execute(
            "SELECT title FROM chat_session_titles WHERE session_id = ?", (sid,))
        title_row = await title_cur.fetchone()
        title = title_row[0] if title_row else sid[:12]
        await db.execute("DELETE FROM chat_history WHERE session_id = ? AND user_id = ?",
                         (sid, user_id))
        await db.execute("DELETE FROM chat_session_titles WHERE session_id = ?", (sid,))
        deleted.append((title, sz))
        total -= sz
    await db.commit()
    # Create notification for deleted sessions
    if deleted:
        total_freed = sum(d[1] for d in deleted)
        names = ', '.join(f'"{d[0]}"' for d in deleted[:3])
        if len(deleted) > 3:
            names += f' +{len(deleted) - 3} more'
        limit_str = f"{CHAT_MAX_TOTAL_BYTES / (1024*1024):.0f} MB" if CHAT_MAX_TOTAL_BYTES >= 1024*1024 else f"{CHAT_MAX_TOTAL_BYTES / 1024:.0f} KB"
        detail = f"Deleted {len(deleted)} old session(s): {names} ({total_freed / 1024:.0f} KB freed, storage limit {limit_str})"
        await db.execute(
            "INSERT INTO notification_history (label, detail, status, link, finished_at, user_id) "
            "VALUES (?, ?, 'done', '', ?, ?)",
            ("Chat cleanup", detail, datetime.now(tz=timezone.utc).isoformat(), user_id)
        )
        await db.commit()


async def chat_get_history(db, session_id, user_id=None):
    if user_id:
        cursor = await db.execute(
            "SELECT * FROM chat_history WHERE session_id = ? AND user_id = ? ORDER BY id",
            (session_id, user_id)
        )
    else:
        cursor = await db.execute(
            "SELECT * FROM chat_history WHERE session_id = ? ORDER BY id",
            (session_id,)
        )
    return [dict(row) for row in await cursor.fetchall()]


async def chat_get_sessions(db, user_id=None, mode=None):
    if user_id is not None:
        cursor = await db.execute(
            "SELECT session_id, user_id, MIN(timestamp) as started, MAX(timestamp) as last_msg, "
            "COUNT(*) as msg_count, SUM(LENGTH(content)) as total_bytes "
            "FROM chat_history WHERE user_id = ? GROUP BY session_id ORDER BY last_msg DESC",
            (user_id,)
        )
    else:
        cursor = await db.execute(
            "SELECT session_id, user_id, MIN(timestamp) as started, MAX(timestamp) as last_msg, "
            "COUNT(*) as msg_count, SUM(LENGTH(content)) as total_bytes "
            "FROM chat_history GROUP BY session_id ORDER BY last_msg DESC"
        )
    sessions = [dict(row) for row in await cursor.fetchall()]
    # Load all titles at once
    titles = await chat_get_all_titles(db, user_id)
    # Get total size across all sessions for this user (for percentage)
    total_all = sum(s.get("total_bytes", 0) or 0 for s in sessions)
    # Batch-load previews (first user message per session) to avoid N+1 queries
    session_ids = [s["session_id"] for s in sessions]
    previews = {}
    if session_ids:
        placeholders = ",".join("?" for _ in session_ids)
        params = list(session_ids)
        if user_id is not None:
            cur_prev = await db.execute(
                f"SELECT session_id, content FROM chat_history "
                f"WHERE session_id IN ({placeholders}) AND role = 'user' AND user_id = ? "
                f"GROUP BY session_id HAVING id = MIN(id)",
                (*params, user_id)
            )
        else:
            cur_prev = await db.execute(
                f"SELECT session_id, content FROM chat_history "
                f"WHERE session_id IN ({placeholders}) AND role = 'user' "
                f"GROUP BY session_id HAVING id = MIN(id)",
                params
            )
        for row in await cur_prev.fetchall():
            previews[row[0]] = row[1][:80] if row[1] else ""

    # Add preview, title, and size for each session
    for s in sessions:
        meta = titles.get(s["session_id"], {})
        if isinstance(meta, str):
            s["title"] = meta
            s["agent_name"] = "main-coach"
            s["mode"] = "coach"
        else:
            s["title"] = meta.get("title", "")
            s["agent_name"] = meta.get("agent_name", "main-coach")
            s["mode"] = meta.get("mode", "coach")
        s["size_bytes"] = s.pop("total_bytes", 0) or 0
        s["total_all_bytes"] = total_all
        s["max_bytes"] = CHAT_MAX_TOTAL_BYTES
        s["preview"] = previews.get(s["session_id"], "")
    # Filter by mode if specified
    if mode:
        sessions = [s for s in sessions if s.get("mode", "coach") == mode]
    return sessions


async def chat_delete_session(db, session_id, user_id=None):
    if user_id:
        await db.execute("DELETE FROM chat_history WHERE session_id = ? AND user_id = ?", (session_id, user_id))
    else:
        await db.execute("DELETE FROM chat_history WHERE session_id = ?", (session_id,))
    await db.execute("DELETE FROM chat_session_titles WHERE session_id = ?", (session_id,))
    await db.commit()


async def chat_set_title(db, session_id, title, user_id=1, agent_name=None, mode=None):
    if agent_name:
        m = mode or "coach"
        await db.execute(
            "INSERT INTO chat_session_titles (session_id, title, user_id, agent_name, mode) VALUES (?, ?, ?, ?, ?) "
            "ON CONFLICT(session_id) DO UPDATE SET title = ?, agent_name = ?, mode = ?",
            (session_id, title, user_id, agent_name, m, title, agent_name, m)
        )
    else:
        await db.execute(
            "INSERT INTO chat_session_titles (session_id, title, user_id) VALUES (?, ?, ?) "
            "ON CONFLICT(session_id) DO UPDATE SET title = ?",
            (session_id, title, user_id, title)
        )
    await db.commit()


async def chat_get_title(db, session_id):
    cursor = await db.execute(
        "SELECT title FROM chat_session_titles WHERE session_id = ?", (session_id,))
    row = await cursor.fetchone()
    return row[0] if row else ""


async def chat_get_agent(db, session_id):
    cursor = await db.execute(
        "SELECT agent_name FROM chat_session_titles WHERE session_id = ?", (session_id,))
    row = await cursor.fetchone()
    return row[0] if row else "main-coach"


async def chat_get_all_titles(db, user_id=None):
    if user_id is not None:
        cursor = await db.execute(
            "SELECT session_id, title, agent_name, mode FROM chat_session_titles WHERE user_id = ?", (user_id,))
    else:
        cursor = await db.execute("SELECT session_id, title, agent_name, mode FROM chat_session_titles")
    result = {}
    for row in await cursor.fetchall():
        result[row[0]] = {
            "title": row[1],
            "agent_name": row[2] if len(row) > 2 else "main-coach",
            "mode": row[3] if len(row) > 3 else "coach",
        }
    return result


# ── Race Info helpers ────────────────────────────────────────────────────

async def race_get(db):
    cursor = await db.execute("SELECT * FROM race_info WHERE id = 1")
    row = await cursor.fetchone()
    return dict(row) if row else None


async def race_update(db, data: dict):
    allowed = {"race_name", "race_date", "swim_km", "bike_km", "run_km",
               "cutoff_swim", "cutoff_bike", "cutoff_finish",
               "target_swim", "target_bike", "target_run", "target_total", "notes"}
    filtered = {k: v for k, v in data.items() if k in allowed}
    if not filtered:
        return
    sets = ", ".join(f"{k} = ?" for k in filtered)
    await db.execute(
        f"UPDATE race_info SET {sets} WHERE id = 1",
        list(filtered.values())
    )
    await db.commit()


# ── Events helpers ──────────────────────────────────────────────────────

async def events_get_all(db, user_id=None):
    if user_id:
        cursor = await db.execute(
            "SELECT * FROM events WHERE user_id = ? ORDER BY event_date", (user_id,))
    else:
        cursor = await db.execute("SELECT * FROM events ORDER BY event_date")
    return [dict(row) for row in await cursor.fetchall()]


async def events_get(db, event_id, user_id=None):
    if user_id:
        cursor = await db.execute("SELECT * FROM events WHERE id = ? AND user_id = ?", (event_id, user_id))
    else:
        cursor = await db.execute("SELECT * FROM events WHERE id = ?", (event_id,))
    row = await cursor.fetchone()
    return dict(row) if row else None


async def events_get_primary(db, user_id=None):
    if user_id:
        cursor = await db.execute(
            "SELECT * FROM events WHERE is_primary = 1 AND user_id = ? LIMIT 1", (user_id,))
    else:
        cursor = await db.execute("SELECT * FROM events WHERE is_primary = 1 LIMIT 1")
    row = await cursor.fetchone()
    return dict(row) if row else None


async def events_create(db, data: dict, user_id=1):
    allowed = {"event_name", "event_type", "event_date", "swim_km", "bike_km", "run_km",
               "cutoff_swim", "cutoff_bike", "cutoff_finish", "target_swim", "target_bike",
               "target_run", "target_total", "goal", "notes", "is_primary"}
    filtered = {k: v for k, v in data.items() if k in allowed}
    filtered["user_id"] = user_id
    filtered["created_at"] = datetime.now(tz=timezone.utc).isoformat()
    await db.execute("BEGIN")
    try:
        if filtered.get("is_primary"):
            await db.execute("UPDATE events SET is_primary = 0 WHERE user_id = ?", (user_id,))
        cols = ", ".join(filtered.keys())
        placeholders = ", ".join(["?"] * len(filtered))
        cursor = await db.execute(
            f"INSERT INTO events ({cols}) VALUES ({placeholders})",
            list(filtered.values())
        )
        await db.commit()
    except Exception:
        await db.rollback()
        raise
    return cursor.lastrowid


async def events_update(db, event_id: int, data: dict, user_id=None):
    allowed = {"event_name", "event_type", "event_date", "swim_km", "bike_km", "run_km",
               "cutoff_swim", "cutoff_bike", "cutoff_finish", "target_swim", "target_bike",
               "target_run", "target_total", "goal", "notes", "is_primary"}
    filtered = {k: v for k, v in data.items() if k in allowed}
    if not filtered:
        return
    await db.execute("BEGIN")
    try:
        # If setting as primary, unset others first
        if filtered.get("is_primary") and user_id:
            await db.execute("UPDATE events SET is_primary = 0 WHERE user_id = ?", (user_id,))
        elif filtered.get("is_primary"):
            await db.execute("UPDATE events SET is_primary = 0")
        sets = ", ".join(f"{k} = ?" for k in filtered)
        if user_id:
            await db.execute(
                f"UPDATE events SET {sets} WHERE id = ? AND user_id = ?",
                list(filtered.values()) + [event_id, user_id])
        else:
            await db.execute(
                f"UPDATE events SET {sets} WHERE id = ?",
                list(filtered.values()) + [event_id])
        await db.commit()
    except Exception:
        await db.rollback()
        raise


async def events_delete(db, event_id: int, user_id=None):
    if user_id:
        await db.execute("DELETE FROM events WHERE id = ? AND user_id = ?", (event_id, user_id))
    else:
        await db.execute("DELETE FROM events WHERE id = ?", (event_id,))
    await db.commit()


async def events_set_primary(db, event_id: int, user_id=None):
    await db.execute("BEGIN")
    try:
        if user_id:
            await db.execute("UPDATE events SET is_primary = 0 WHERE user_id = ?", (user_id,))
            await db.execute(
                "UPDATE events SET is_primary = 1 WHERE id = ? AND user_id = ?", (event_id, user_id))
        else:
            await db.execute("UPDATE events SET is_primary = 0")
            await db.execute("UPDATE events SET is_primary = 1 WHERE id = ?", (event_id,))
        await db.commit()
    except Exception:
        await db.rollback()
        raise


# ── Workout Insight helpers ──────────────────────────────────────────────

async def insight_get(db, workout_num: int, user_id=None):
    if user_id:
        cursor = await db.execute(
            "SELECT * FROM workout_insights WHERE workout_num = ? AND user_id = ?", (workout_num, user_id))
    else:
        cursor = await db.execute(
            "SELECT * FROM workout_insights WHERE workout_num = ?", (workout_num,))
    row = await cursor.fetchone()
    return dict(row) if row else None


async def insight_get_all(db, user_id=None):
    if user_id:
        cursor = await db.execute(
            "SELECT * FROM workout_insights WHERE user_id = ? ORDER BY workout_date DESC", (user_id,))
    else:
        cursor = await db.execute(
            "SELECT * FROM workout_insights ORDER BY workout_date DESC")
    return [dict(row) for row in await cursor.fetchall()]


async def insight_get_existing_nums(db, user_id=None):
    if user_id:
        cursor = await db.execute("SELECT workout_num FROM workout_insights WHERE user_id = ?", (user_id,))
    else:
        cursor = await db.execute("SELECT workout_num FROM workout_insights")
    return {row["workout_num"] for row in await cursor.fetchall()}


async def insight_save(db, workout_num, workout_date, workout_type, insight,
                       plan_comparison="", user_id=1):
    await db.execute(
        "INSERT OR REPLACE INTO workout_insights "
        "(workout_num, workout_date, workout_type, insight, plan_comparison, generated_at, user_id) "
        "VALUES (?, ?, ?, ?, ?, ?, ?)",
        (workout_num, workout_date, workout_type, insight, plan_comparison,
         datetime.now(tz=timezone.utc).isoformat(), user_id)
    )
    await db.commit()


async def insight_delete(db, workout_num: int, user_id=None):
    if user_id:
        await db.execute(
            "DELETE FROM workout_insights WHERE workout_num = ? AND user_id = ?",
            (workout_num, user_id))
    else:
        await db.execute(
            "DELETE FROM workout_insights WHERE workout_num = ?", (workout_num,))
    await db.commit()


async def insight_delete_many(db, workout_nums: list[int], user_id=None):
    """Delete insights for multiple workout_nums at once."""
    if not workout_nums:
        return 0
    placeholders = ",".join("?" for _ in workout_nums)
    if user_id:
        cursor = await db.execute(
            f"DELETE FROM workout_insights WHERE workout_num IN ({placeholders}) AND user_id = ?",
            (*workout_nums, user_id))
    else:
        cursor = await db.execute(
            f"DELETE FROM workout_insights WHERE workout_num IN ({placeholders})",
            workout_nums)
    await db.commit()
    return cursor.rowcount


# ── General Insight helpers ──────────────────────────────────────────────

async def general_insight_get_latest(db, user_id=None):
    if user_id:
        cursor = await db.execute(
            "SELECT * FROM general_insights WHERE user_id = ? ORDER BY id DESC LIMIT 1", (user_id,)
        )
    else:
        cursor = await db.execute(
            "SELECT * FROM general_insights ORDER BY id DESC LIMIT 1"
        )
    row = await cursor.fetchone()
    return dict(row) if row else None


async def general_insight_save(db, content, user_id=1):
    cursor = await db.execute(
        "INSERT INTO general_insights (content, generated_at, user_id) VALUES (?, ?, ?)",
        (content, datetime.now(tz=timezone.utc).isoformat(), user_id)
    )
    await db.commit()
    return cursor.lastrowid


async def general_insight_delete(db, insight_id=None, user_id=None):
    """Delete general insight(s). If insight_id given, delete that one. Otherwise delete all for user."""
    if insight_id:
        await db.execute("DELETE FROM general_insights WHERE id = ? AND user_id = ?", (insight_id, user_id))
    elif user_id:
        await db.execute("DELETE FROM general_insights WHERE user_id = ?", (user_id,))
    await db.commit()


# ── Period Insights helpers ────────────────────────────────────────────────

async def period_insight_get_all(db, user_id, from_date=None, to_date=None):
    """Get period insights, optionally filtered by date range."""
    if from_date and to_date:
        cursor = await db.execute(
            "SELECT * FROM period_insights WHERE user_id = ? AND from_date = ? AND to_date = ? "
            "ORDER BY category", (user_id, from_date, to_date))
    else:
        cursor = await db.execute(
            "SELECT * FROM period_insights WHERE user_id = ? ORDER BY from_date DESC, category",
            (user_id,))
    return [dict(r) for r in await cursor.fetchall()]


async def period_insight_exists(db, user_id, from_date, to_date, category):
    """Check if a period insight exists for this exact range + category."""
    cursor = await db.execute(
        "SELECT id FROM period_insights WHERE user_id = ? AND from_date = ? AND to_date = ? AND category = ?",
        (user_id, from_date, to_date, category))
    row = await cursor.fetchone()
    return dict(row)["id"] if row else None


async def period_insight_save(db, category, from_date, to_date, content, user_id=1):
    """Insert or replace a period insight."""
    now = datetime.now(tz=timezone.utc).isoformat()
    await db.execute(
        "INSERT INTO period_insights (category, from_date, to_date, content, generated_at, user_id) "
        "VALUES (?, ?, ?, ?, ?, ?) "
        "ON CONFLICT(category, from_date, to_date, user_id) DO UPDATE SET content=excluded.content, generated_at=excluded.generated_at",
        (category, from_date, to_date, content, now, user_id))
    await db.commit()


async def period_insight_delete(db, insight_id, user_id):
    """Delete a single period insight."""
    await db.execute("DELETE FROM period_insights WHERE id = ? AND user_id = ?", (insight_id, user_id))
    await db.commit()


async def period_insight_delete_range(db, from_date, to_date, user_id):
    """Delete all period insights for a date range."""
    await db.execute(
        "DELETE FROM period_insights WHERE from_date = ? AND to_date = ? AND user_id = ?",
        (from_date, to_date, user_id))
    await db.commit()


# ── Agent Session helpers ──────────────────────────────────────────────────

async def session_save(db, session_uuid, agent_name, context_key="", notes="", user_id=1):
    """Insert or update an agent session record."""
    now = datetime.now(tz=timezone.utc).isoformat()
    await db.execute(
        "INSERT INTO agent_sessions (session_uuid, agent_name, context_key, created_at, last_used_at, notes, user_id) "
        "VALUES (?, ?, ?, ?, ?, ?, ?) "
        "ON CONFLICT(session_uuid) DO UPDATE SET last_used_at=?, message_count=message_count+1",
        (session_uuid, agent_name, context_key, now, now, notes, user_id, now)
    )
    await db.commit()


async def session_get_all(db, user_id=None):
    if user_id is not None:
        cursor = await db.execute("SELECT * FROM agent_sessions WHERE user_id = ? ORDER BY last_used_at DESC", (user_id,))
    else:
        cursor = await db.execute("SELECT * FROM agent_sessions ORDER BY last_used_at DESC")
    return [dict(row) for row in await cursor.fetchall()]


async def session_get(db, session_uuid):
    cursor = await db.execute("SELECT * FROM agent_sessions WHERE session_uuid = ?", (session_uuid,))
    row = await cursor.fetchone()
    return dict(row) if row else None


async def session_delete(db, session_uuid, user_id=None):
    if user_id is not None:
        await db.execute("DELETE FROM agent_sessions WHERE session_uuid = ? AND user_id = ?", (session_uuid, user_id))
    else:
        await db.execute("DELETE FROM agent_sessions WHERE session_uuid = ?", (session_uuid,))
    await db.commit()


async def session_delete_all(db, agent_filter=None, user_id=None):
    if agent_filter and user_id is not None:
        await db.execute("DELETE FROM agent_sessions WHERE agent_name = ? AND user_id = ?", (agent_filter, user_id))
    elif agent_filter:
        await db.execute("DELETE FROM agent_sessions WHERE agent_name = ?", (agent_filter,))
    elif user_id is not None:
        await db.execute("DELETE FROM agent_sessions WHERE user_id = ?", (user_id,))
    else:
        await db.execute("DELETE FROM agent_sessions")
    await db.commit()


# ── Notification History helpers ─────────────────────────────────────────

async def notification_add(db, label, detail="", status="done", link="", user_id=1):
    finished_at = datetime.now(tz=timezone.utc).strftime("%Y-%m-%d %H:%M")
    await db.execute(
        "INSERT INTO notification_history (label, detail, status, link, finished_at, user_id) "
        "VALUES (?, ?, ?, ?, ?, ?)",
        (label, detail, status, link, finished_at, user_id)
    )
    # Read max_keep from app_settings (default 50)
    max_keep = 50
    try:
        row = await db.execute_fetchone(
            "SELECT value FROM app_settings WHERE key = 'notification_max_keep'")
        if row:
            max_keep = max(10, min(1000, int(row[0])))
    except Exception:
        pass
    await db.execute(
        "DELETE FROM notification_history WHERE user_id = ? AND id NOT IN "
        f"(SELECT id FROM notification_history WHERE user_id = ? ORDER BY id DESC LIMIT {int(max_keep)})",
        (user_id, user_id)
    )
    await db.commit()


async def notification_get_all(db, limit=50, user_id=None):
    if user_id:
        cursor = await db.execute(
            "SELECT * FROM notification_history WHERE user_id = ? ORDER BY id DESC LIMIT ?", (user_id, limit))
    else:
        cursor = await db.execute(
            "SELECT * FROM notification_history ORDER BY id DESC LIMIT ?", (limit,))
    return [dict(row) for row in await cursor.fetchall()]


async def notification_clear(db, user_id=None):
    if user_id:
        await db.execute("DELETE FROM notification_history WHERE user_id = ?", (user_id,))
    else:
        await db.execute("DELETE FROM notification_history")
    await db.commit()


# ── Coach Memory helpers ──────────────────────────────────────────────────

async def memory_get_all(db, user_id=1):
    cursor = await db.execute(
        "SELECT * FROM coach_memory WHERE user_id = ? ORDER BY id", (user_id,))
    return [dict(row) for row in await cursor.fetchall()]


async def memory_add(db, content, user_id=1):
    cursor = await db.execute(
        "INSERT INTO coach_memory (user_id, content) VALUES (?, ?)",
        (user_id, content))
    await db.commit()
    return cursor.lastrowid


async def memory_update(db, mem_id, content, user_id=1):
    await db.execute(
        "UPDATE coach_memory SET content = ?, updated_at = datetime('now') "
        "WHERE id = ? AND user_id = ?",
        (content, mem_id, user_id))
    await db.commit()


async def memory_delete(db, mem_id, user_id=1):
    await db.execute(
        "DELETE FROM coach_memory WHERE id = ? AND user_id = ?",
        (mem_id, user_id))
    await db.commit()


# ── Agent Memory helpers ─────────────────────────────────────────────────

async def agent_memory_get_all(db, user_id, agent_type):
    cursor = await db.execute(
        "SELECT * FROM agent_memory WHERE user_id = ? AND agent_type = ? ORDER BY id",
        (user_id, agent_type))
    return [dict(row) for row in await cursor.fetchall()]


async def agent_memory_get_all_types(db, user_id):
    """Get all agent memories grouped by agent_type."""
    cursor = await db.execute(
        "SELECT agent_type, COUNT(*) as count FROM agent_memory WHERE user_id = ? GROUP BY agent_type",
        (user_id,))
    return {row[0]: row[1] for row in await cursor.fetchall()}


async def agent_memory_add(db, user_id, agent_type, content):
    cursor = await db.execute(
        "INSERT INTO agent_memory (user_id, agent_type, content) VALUES (?, ?, ?)",
        (user_id, agent_type, content))
    await db.commit()
    return cursor.lastrowid


async def agent_memory_update(db, mem_id, content, user_id):
    await db.execute(
        "UPDATE agent_memory SET content = ?, updated_at = datetime('now') "
        "WHERE id = ? AND user_id = ?",
        (content, mem_id, user_id))
    await db.commit()


async def agent_memory_delete(db, mem_id, user_id):
    await db.execute(
        "DELETE FROM agent_memory WHERE id = ? AND user_id = ?",
        (mem_id, user_id))
    await db.commit()


# ── User helpers ─────────────────────────────────────────────────────────

async def user_get_by_username(db, username):
    cursor = await db.execute("SELECT * FROM users WHERE username = ?", (username,))
    row = await cursor.fetchone()
    return dict(row) if row else None


async def user_get_by_id(db, user_id):
    cursor = await db.execute("SELECT * FROM users WHERE id = ?", (user_id,))
    row = await cursor.fetchone()
    return dict(row) if row else None


async def user_get_all(db):
    cursor = await db.execute("SELECT id, username, display_name, role, data_dir, created_at FROM users ORDER BY id")
    return [dict(row) for row in await cursor.fetchall()]


async def user_create(db, username, password_hash, display_name="", role="user", data_dir=""):
    cursor = await db.execute(
        "INSERT INTO users (username, password_hash, display_name, role, data_dir, created_at) "
        "VALUES (?, ?, ?, ?, ?, ?)",
        (username, password_hash, display_name, role, data_dir, datetime.now(tz=timezone.utc).isoformat())
    )
    await db.commit()
    return cursor.lastrowid


async def user_delete(db, user_id):
    """Delete user and ALL related data from every table."""
    for table in (
        "chat_history", "nutrition_log", "training_plan", "workout_insights",
        "general_insights", "period_insights", "token_usage", "coach_memory",
        "agent_memory", "notification_history", "chat_session_titles",
        "agent_sessions",
    ):
        await db.execute(f"DELETE FROM {table} WHERE user_id = ?", (user_id,))
    # Per-user settings (keys like 'nutrition_auto_suggest_enabled_2')
    await db.execute(
        "DELETE FROM app_settings WHERE key LIKE ? OR key LIKE ?",
        (f"%_{user_id}", f"%_user{user_id}"),
    )
    await db.execute("DELETE FROM events WHERE user_id = ?", (user_id,))
    await db.execute("DELETE FROM users WHERE id = ?", (user_id,))
    await db.commit()


async def user_get_profile(db, user_id):
    cursor = await db.execute(
        "SELECT id, username, display_name, height_cm, weight_kg, birth_date, sex FROM users WHERE id = ?",
        (user_id,)
    )
    row = await cursor.fetchone()
    return dict(row) if row else None


async def user_update_profile(db, user_id, data: dict):
    allowed = {"display_name", "height_cm", "weight_kg", "birth_date", "sex"}
    filtered = {k: v for k, v in data.items() if k in allowed}
    if not filtered:
        return
    sets = ", ".join(f"{k} = ?" for k in filtered)
    vals = list(filtered.values()) + [user_id]
    await db.execute(f"UPDATE users SET {sets} WHERE id = ?", vals)
    await db.commit()


# ── Server Log helpers ───────────────────────────────────────────────────

async def log_request(db, username, method, path, status, duration_ms):
    import random
    await db.execute(
        "INSERT INTO server_logs (timestamp, username, method, path, status, duration_ms) "
        "VALUES (?, ?, ?, ?, ?, ?)",
        (datetime.now(tz=timezone.utc).isoformat(), username, method, path, status, duration_ms)
    )
    if random.random() < 0.05:
        await db.execute(
            "DELETE FROM server_logs WHERE id NOT IN "
            "(SELECT id FROM server_logs ORDER BY id DESC LIMIT 5000)"
        )
    await db.commit()


async def log_get_recent(db, limit=200):
    cursor = await db.execute(
        "SELECT * FROM server_logs ORDER BY id DESC LIMIT ?", (limit,)
    )
    return [dict(row) for row in await cursor.fetchall()]


# ── Token Usage helpers ──────────────────────────────────────────────────

async def usage_track(db, source, agent_name, session_id, input_tokens, output_tokens,
                      cache_read_tokens, cache_creation_tokens, cost_usd, model,
                      duration_ms, user_id=1):
    await db.execute(
        "INSERT INTO token_usage (timestamp, source, agent_name, session_id, "
        "input_tokens, output_tokens, cache_read_tokens, cache_creation_tokens, "
        "cost_usd, model, duration_ms, user_id) "
        "VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
        (datetime.now(tz=timezone.utc).isoformat(), source, agent_name, session_id,
         input_tokens, output_tokens, cache_read_tokens, cache_creation_tokens,
         cost_usd, model, duration_ms, user_id)
    )
    await db.commit()


async def usage_get_summary(db, user_id=None, from_date=None):
    """Get aggregated usage summary. Optionally filter by user and date."""
    where = []
    params = []
    if user_id:
        where.append("user_id = ?")
        params.append(user_id)
    if from_date:
        where.append("timestamp >= ?")
        params.append(from_date)
    where_sql = (" WHERE " + " AND ".join(where)) if where else ""
    cursor = await db.execute(
        f"SELECT COALESCE(SUM(input_tokens), 0) as total_input, "
        f"COALESCE(SUM(output_tokens), 0) as total_output, "
        f"COALESCE(SUM(cache_read_tokens), 0) as total_cache_read, "
        f"COALESCE(SUM(cache_creation_tokens), 0) as total_cache_creation, "
        f"COALESCE(SUM(cost_usd), 0) as total_cost, "
        f"COUNT(*) as total_calls "
        f"FROM token_usage{where_sql}", params
    )
    row = await cursor.fetchone()
    return dict(row)


async def usage_get_recent(db, limit=50, user_id=None):
    if user_id:
        cursor = await db.execute(
            "SELECT * FROM token_usage WHERE user_id = ? ORDER BY id DESC LIMIT ?",
            (user_id, limit))
    else:
        cursor = await db.execute(
            "SELECT * FROM token_usage ORDER BY id DESC LIMIT ?", (limit,))
    return [dict(row) for row in await cursor.fetchall()]


async def usage_get_per_user(db):
    """Get usage aggregated per user (for admin view)."""
    cursor = await db.execute(
        "SELECT u.id as user_id, u.username, u.display_name, "
        "COALESCE(SUM(t.input_tokens), 0) as total_input, "
        "COALESCE(SUM(t.output_tokens), 0) as total_output, "
        "COALESCE(SUM(t.cache_read_tokens), 0) as total_cache_read, "
        "COALESCE(SUM(t.cache_creation_tokens), 0) as total_cache_creation, "
        "COALESCE(SUM(t.cost_usd), 0) as total_cost, "
        "COUNT(t.id) as total_calls "
        "FROM users u LEFT JOIN token_usage t ON u.id = t.user_id "
        "GROUP BY u.id ORDER BY total_cost DESC"
    )
    return [dict(row) for row in await cursor.fetchall()]


async def usage_get_by_agent(db, user_id=None):
    """Get usage aggregated per agent_name+model (for cost analysis)."""
    where = "WHERE user_id = ?" if user_id else ""
    params = [user_id] if user_id else []
    cursor = await db.execute(
        f"SELECT COALESCE(NULLIF(agent_name, ''), source) as agent, model, "
        f"SUM(input_tokens) as input_tokens, SUM(output_tokens) as output_tokens, "
        f"SUM(cache_read_tokens) as cache_read, SUM(cache_creation_tokens) as cache_write, "
        f"SUM(cost_usd) as cost, COUNT(*) as calls "
        f"FROM token_usage {where} "
        f"GROUP BY agent, model ORDER BY cost DESC", params
    )
    return [dict(row) for row in await cursor.fetchall()]


async def usage_get_daily(db, user_id=None, from_date=None):
    """Get daily aggregated usage."""
    where = []
    params = []
    if user_id:
        where.append("user_id = ?")
        params.append(user_id)
    if from_date:
        where.append("timestamp >= ?")
        params.append(from_date)
    where_sql = (" WHERE " + " AND ".join(where)) if where else ""
    cursor = await db.execute(
        f"SELECT DATE(timestamp) as date, "
        f"SUM(input_tokens) as input_tokens, SUM(output_tokens) as output_tokens, "
        f"SUM(cache_read_tokens) as cache_read_tokens, "
        f"SUM(cache_creation_tokens) as cache_creation_tokens, "
        f"SUM(cost_usd) as cost_usd, COUNT(*) as calls "
        f"FROM token_usage{where_sql} "
        f"GROUP BY DATE(timestamp) ORDER BY date DESC", params
    )
    return [dict(row) for row in await cursor.fetchall()]


async def usage_get_by_model(db, user_id=None):
    """Get usage aggregated per model."""
    where = "WHERE user_id = ?" if user_id else ""
    params = [user_id] if user_id else []
    cursor = await db.execute(
        f"SELECT model, SUM(input_tokens) as input_tokens, SUM(output_tokens) as output_tokens, "
        f"SUM(cache_read_tokens) as cache_read, SUM(cache_creation_tokens) as cache_write, "
        f"SUM(cost_usd) as cost, COUNT(*) as calls "
        f"FROM token_usage {where} "
        f"GROUP BY model ORDER BY cost DESC", params
    )
    return [dict(row) for row in await cursor.fetchall()]


async def usage_get_daily_by_agent(db, user_id, date):
    """Get per-agent+model breakdown for a specific date."""
    cursor = await db.execute(
        "SELECT COALESCE(NULLIF(agent_name, ''), source) as agent, model, "
        "SUM(input_tokens) as input_tokens, SUM(output_tokens) as output_tokens, "
        "SUM(cache_read_tokens) as cache_read, SUM(cache_creation_tokens) as cache_write, "
        "SUM(cost_usd) as cost, COUNT(*) as calls "
        "FROM token_usage WHERE user_id = ? AND DATE(timestamp) = ? "
        "GROUP BY agent, model ORDER BY cost DESC",
        [user_id, date]
    )
    return [dict(row) for row in await cursor.fetchall()]


# ── App Settings helpers ─────────────────────────────────────────────────

async def setting_get(db, key, default=""):
    cursor = await db.execute("SELECT value FROM app_settings WHERE key = ?", (key,))
    row = await cursor.fetchone()
    return row["value"] if row else default


async def setting_set(db, key, value):
    await db.execute(
        "INSERT INTO app_settings (key, value) VALUES (?, ?) "
        "ON CONFLICT(key) DO UPDATE SET value = ?",
        (key, value, value)
    )
    await db.commit()


async def settings_get_all(db):
    cursor = await db.execute("SELECT key, value FROM app_settings")
    return {row["key"]: row["value"] for row in await cursor.fetchall()}
