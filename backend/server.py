#!/usr/bin/env python3
"""
Training Dashboard — FastAPI backend.
Run:  cd backend && pip install -r requirements.txt && python server.py
"""

import asyncio
import os
import shutil
import time
import logging
from collections import defaultdict

from contextlib import asynccontextmanager
from datetime import datetime

from fastapi import FastAPI, Request, WebSocket
from fastapi.responses import FileResponse, JSONResponse
from fastapi.staticfiles import StaticFiles
from starlette.formparsers import MultiPartParser
from starlette.responses import Response

MultiPartParser.max_part_size = 1024 * 1024 * 1024  # 1 GB (default 1 MB)

import database as db
from auth import decode_jwt
from config import (
    BASE_DIR, TRAINING_DATA, REACT_DIST, LOG_DIR, logger,
    _PUBLIC_PATHS, _PUBLIC_PREFIXES,
)
from routes.deps import _migrate_user1_data
from data_processing import _apply_gps_corrections_to_summary
from services.task_tracker import _insight_status


# ── Backward-compatible re-exports for tests ─────────────────────────────────
from data_processing.helpers import _safe_float  # noqa: F401 — used by tests
from data_processing.summary import _merge_nearby_workouts  # noqa: F401 — used by tests
from routes.deps import _uid  # noqa: F401 — used by tests
from services.chat_handler import _read_attached_file  # noqa: F401 — used by tests


# ── AI Rate Limiting ──────────────────────────────────────────────────────────

_ai_rate: dict[int, list[float]] = defaultdict(list)
_AI_RATE_WINDOW = 3600
_ai_rate_limit: int = 0
_ai_rate_limit_loaded: float = 0
_AI_RATE_LIMIT_CACHE_TTL = 300

_AI_PATHS = {"/api/insights/generate", "/api/insights/generate-batch", "/api/nutrition/analyze"}


async def _get_ai_rate_limit() -> int:
    global _ai_rate_limit, _ai_rate_limit_loaded
    now = time.time()
    if _ai_rate_limit_loaded > 0 and now - _ai_rate_limit_loaded < _AI_RATE_LIMIT_CACHE_TTL:
        return _ai_rate_limit
    try:
        conn = await db.get_db()
        try:
            cursor = await conn.execute(
                "SELECT value FROM app_settings WHERE key = 'ai_rate_limit'")
            row = await cursor.fetchone()
            _ai_rate_limit = int(row[0]) if row else 0
            _ai_rate_limit_loaded = now
        finally:
            await conn.close()
    except Exception as e:
        logger.warning(f"Failed to load AI rate limit: {e}")
    return _ai_rate_limit


async def _check_ai_rate(user_id: int) -> bool:
    limit = await _get_ai_rate_limit()
    if limit <= 0:
        return True
    now = time.time()
    timestamps = _ai_rate[user_id]
    _ai_rate[user_id] = [t for t in timestamps if now - t < _AI_RATE_WINDOW]
    if len(_ai_rate[user_id]) >= limit:
        return False
    _ai_rate[user_id].append(now)
    return True



# ── Lifespan ─────────────────────────────────────────────────────────────────

@asynccontextmanager
async def lifespan(application):
    logger.info("Server starting — initializing database")
    # Auto-backup DB before init (keeps last 3 backups)
    if db.DB_PATH.exists():
        backup_dir = db.DB_PATH.parent / "backups"
        backup_dir.mkdir(exist_ok=True)
        stamp = datetime.now().strftime("%Y%m%d_%H%M%S")
        backup_path = backup_dir / f"dashboard_{stamp}.db"
        shutil.copy2(str(db.DB_PATH), str(backup_path))
        import aiosqlite
        async with aiosqlite.connect(str(backup_path)) as bk:
            cursor = await bk.execute("PRAGMA integrity_check")
            result = await cursor.fetchone()
            if result[0] != "ok":
                logger.error(f"Backup integrity check FAILED: {result[0]}")
        logger.info(f"DB backup: {backup_path}")
        # Keep only last 3 backups
        MAX_BACKUPS = 3
        backups = sorted(backup_dir.glob("dashboard_*.db"), key=lambda p: p.stat().st_mtime, reverse=True)
        for old in backups[MAX_BACKUPS:]:
            old.unlink()
            logger.info(f"Removed old backup: {old.name}")
    await db.init_db()
    # Migrate: add missing columns to existing tables
    _migrate_tables = [
        ("nutrition_log", "created_at", "TEXT NOT NULL DEFAULT ''"),
        ("training_plan", "user_id", "INTEGER NOT NULL DEFAULT 1"),
        ("nutrition_log", "user_id", "INTEGER NOT NULL DEFAULT 1"),
        ("chat_history", "user_id", "INTEGER NOT NULL DEFAULT 1"),
        ("general_insights", "user_id", "INTEGER NOT NULL DEFAULT 1"),
        ("notification_history", "user_id", "INTEGER NOT NULL DEFAULT 1"),
        ("users", "height_cm", "REAL DEFAULT 0"),
        ("users", "weight_kg", "REAL DEFAULT 0"),
        ("users", "birth_date", "TEXT NOT NULL DEFAULT ''"),
        ("users", "sex", "TEXT NOT NULL DEFAULT 'male'"),
        ("agent_sessions", "user_id", "INTEGER NOT NULL DEFAULT 1"),
        ("users", "token_version", "INTEGER DEFAULT 0"),
    ]
    # Run all migrations and startup queries in a single DB connection
    conn = await db.get_db()
    try:
        for table, col, col_type in _migrate_tables:
            cursor = await conn.execute(f"PRAGMA table_info({table})")
            cols = {row[1] for row in await cursor.fetchall()}
            if col not in cols:
                await conn.execute(f"ALTER TABLE {table} ADD COLUMN {col} {col_type}")
                await conn.commit()
                logger.info(f"Migration: added {col} column to {table}")
        # Migrate workout_insights: need composite PK (workout_num, user_id)
        cursor = await conn.execute("PRAGMA table_info(workout_insights)")
        wi_cols = {row[1] for row in await cursor.fetchall()}
        if "user_id" not in wi_cols:
            await conn.execute("""CREATE TABLE IF NOT EXISTS workout_insights_new (
                workout_num INTEGER NOT NULL,
                workout_date TEXT NOT NULL,
                workout_type TEXT NOT NULL DEFAULT '',
                insight TEXT NOT NULL DEFAULT '',
                plan_comparison TEXT NOT NULL DEFAULT '',
                generated_at TEXT NOT NULL,
                user_id INTEGER NOT NULL DEFAULT 1,
                PRIMARY KEY (workout_num, user_id)
            )""")
            await conn.execute("INSERT INTO workout_insights_new SELECT *, 1 FROM workout_insights")
            await conn.execute("DROP TABLE workout_insights")
            await conn.execute("ALTER TABLE workout_insights_new RENAME TO workout_insights")
            await conn.commit()
            logger.info("Migration: recreated workout_insights with composite PK (workout_num, user_id)")
        # Migrate global settings to per-user keys (one-time, for user_id=1)
        for old_key in ("hidden_workouts", "manual_merges"):
            new_key = f"{old_key}_1"
            cur = await conn.execute("SELECT value FROM app_settings WHERE key = ?", (old_key,))
            row = await cur.fetchone()
            if row:
                cur2 = await conn.execute("SELECT 1 FROM app_settings WHERE key = ?", (new_key,))
                if not await cur2.fetchone():
                    await conn.execute("INSERT INTO app_settings (key, value) VALUES (?, ?)", (new_key, row[0]))
                    await conn.commit()
                    logger.info(f"Migrated {old_key} -> {new_key}")
        # Load persisted notification history
        NOTIFICATION_HISTORY_LIMIT = 200
        rows = await db.notification_get_all(conn, limit=NOTIFICATION_HISTORY_LIMIT)
        _insight_status["history"] = rows
    finally:
        await conn.close()
    # Migrate user 1 data from training_data/ root to training_data/users/1/
    _migrate_user1_data()
    # Apply GPS corrections to all user summaries
    users_dir = TRAINING_DATA / "users"
    if users_dir.exists():
        for ud in users_dir.iterdir():
            if ud.is_dir() and (ud / "00_workouts_summary.csv").exists():
                _apply_gps_corrections_to_summary(ud)
    logger.info(f"Training data: {TRAINING_DATA}")
    logger.info(f"Database: {db.DB_PATH}")
    logger.info(f"Log file: {LOG_DIR / 'server.log'}")
    # Start background schedulers
    from services.nutrition_scheduler import start_scheduler, stop_scheduler
    start_scheduler()
    yield
    from services.nutrition_scheduler import _scheduler_task
    if _scheduler_task and not _scheduler_task.done():
        _scheduler_task.cancel()
        try:
            await asyncio.wait_for(asyncio.shield(_scheduler_task), timeout=10)
        except (asyncio.TimeoutError, asyncio.CancelledError):
            pass
    else:
        stop_scheduler()
    logger.info("Server shutting down")


# ── App Creation ─────────────────────────────────────────────────────────────

app = FastAPI(title="Training Dashboard", lifespan=lifespan)

# Serve React build from frontend/dist/
if REACT_DIST.exists():
    app.mount("/assets", StaticFiles(directory=str(REACT_DIST / "assets")), name="assets")


# ── Security Headers Middleware ─────────────────────────────────────────────

@app.middleware("http")
async def add_security_headers(request: Request, call_next):
    """Add security headers to all responses."""
    response = await call_next(request)
    response.headers["Content-Security-Policy"] = (
        "default-src 'self'; "
        "script-src 'self' 'unsafe-inline'; "
        "style-src 'self' 'unsafe-inline' https://unpkg.com; "
        "img-src 'self' data: blob: https://*.tile.openstreetmap.org; "
        "connect-src 'self' ws: wss:; "
        "font-src 'self'"
    )
    response.headers["X-Content-Type-Options"] = "nosniff"
    response.headers["X-Frame-Options"] = "DENY"
    return response


# ── Request Size Limit Middleware ────────────────────────────────────────────

MAX_REQUEST_SIZE_MB = 1024
MAX_REQUEST_SIZE_BYTES = MAX_REQUEST_SIZE_MB * 1024 * 1024


@app.middleware("http")
async def limit_request_size(request: Request, call_next):
    content_length = request.headers.get("content-length")
    if content_length and int(content_length) > MAX_REQUEST_SIZE_BYTES:
        return JSONResponse({"error": "Request body too large"}, status_code=413)
    return await call_next(request)


# ── Auth Middleware ───────────────────────────────────────────────────────────

async def _get_current_user(request: Request) -> tuple[dict | None, dict | None]:
    """Extract user from JWT cookie. Returns (user, jwt_payload) or (None, None)."""
    token = request.cookies.get("token")
    if not token:
        return None, None
    payload = decode_jwt(token)
    if not payload or "user_id" not in payload:
        return None, None
    conn = await db.get_db()
    try:
        user = await db.user_get_by_id(conn, payload["user_id"])
        return user, payload
    finally:
        await conn.close()


@app.middleware("http")
async def auth_middleware(request: Request, call_next):
    """Check auth on API routes, log requests, save to server_logs."""
    path = request.url.path
    start = time.time()

    # Skip auth for public paths, static assets, and SPA routes
    needs_auth = (
        path.startswith("/api/") and
        path not in _PUBLIC_PATHS and
        not any(path.startswith(p) for p in _PUBLIC_PREFIXES)
    )

    username = ""
    if needs_auth:
        user, jwt_payload = await _get_current_user(request)
        if not user:
            elapsed = (time.time() - start) * 1000
            logger.debug(f"{request.method} {path} -> 401 ({elapsed:.0f}ms) [anon]")
            return JSONResponse(status_code=401, content={"detail": "Not authenticated"})
        else:
            # Token version check (skip for agent paths to avoid perf hit)
            if jwt_payload:
                tv = jwt_payload.get("token_version", 0)
                db_tv = user.get("token_version") or 0
                if db_tv != tv:
                    response = JSONResponse({"detail": "Session expired"}, status_code=401)
                    response.delete_cookie("token")
                    return response
            request.state.user = user
            username = user["username"]
    else:
        # Try to get user for logging even on public paths
        user, _ = await _get_current_user(request)
        request.state.user = user
        if user:
            username = user["username"]

    # AI rate limiting
    if path in _AI_PATHS and hasattr(request.state, "user") and request.state.user:
        uid = request.state.user["id"]
        if not await _check_ai_rate(uid):
            return JSONResponse({"detail": "AI rate limit exceeded. Try again later."}, status_code=429)

    response = await call_next(request)
    elapsed = (time.time() - start) * 1000

    if not path.startswith("/assets"):
        log_level = logging.DEBUG
        # Promote to INFO for slow requests or errors
        SLOW_REQUEST_THRESHOLD_MS = 2000
        if elapsed > SLOW_REQUEST_THRESHOLD_MS or response.status_code >= 400:
            log_level = logging.INFO
        logger.log(log_level, f"{request.method} {path} -> {response.status_code} ({elapsed:.0f}ms) [{username or 'anon'}]")
        # Save to DB (fire and forget)
        if path.startswith("/api/"):
            try:
                conn = await db.get_db()
                try:
                    await db.log_request(conn, username, request.method, path, response.status_code, elapsed)
                finally:
                    await conn.close()
            except Exception:
                pass

    return response


# ── Root route ───────────────────────────────────────────────────────────────

@app.get("/")
async def root():
    return FileResponse(str(REACT_DIST / "index.html"))


@app.get("/api/health")
async def health_check(check: str = None):
    result = {"status": "ok"}
    if check == "deep":
        cli_path = shutil.which("claude")
        result["claude_cli"] = "available" if cli_path else "not_found"
        try:
            conn = await db.get_db()
            try:
                await conn.execute("SELECT 1")
                result["database"] = "ok"
            finally:
                await conn.close()
        except Exception:
            result["database"] = "error"
    return result


# ── Register Route Modules ───────────────────────────────────────────────────

from routes.auth_routes import router as auth_router
from routes.admin_routes import router as admin_router
from routes.plan_routes import router as plan_router
from routes.events_routes import router as events_router
from routes.memory_routes import router as memory_router
from routes.usage_routes import router as usage_router
from routes.merge_routes import router as merge_router
from routes.workout_routes import router as workout_router
from routes.body_metrics_routes import router as body_metrics_router
from routes.nutrition_routes import router as nutrition_router
from routes.insights_routes import router as insights_router
from routes.chat_routes import router as chat_router
from routes.import_routes import router as import_router
from routes.agent_routes import router as agent_router
from routes.session_routes import router as session_router
from services.chat_handler import chat_router as ws_chat_router

app.include_router(auth_router)
app.include_router(admin_router)
app.include_router(plan_router)
app.include_router(events_router)
app.include_router(memory_router)
app.include_router(usage_router)
app.include_router(merge_router)
app.include_router(workout_router)
app.include_router(body_metrics_router)
app.include_router(nutrition_router)
app.include_router(insights_router)
app.include_router(chat_router)
app.include_router(import_router)
app.include_router(agent_router)
app.include_router(session_router)
app.include_router(ws_chat_router)


# ── SPA catch-all (must be LAST route) ───────────────────────────────────────

@app.get("/{full_path:path}")
async def serve_spa(full_path: str):
    """Serve React SPA index.html for all non-API client-side routes."""
    return FileResponse(str(REACT_DIST / "index.html"))


# ── Run ──────────────────────────────────────────────────────────────────────

if __name__ == "__main__":
    import uvicorn
    print(f"Training data: {TRAINING_DATA}")
    print(f"Database: {db.DB_PATH}")
    print(f"Starting server at http://localhost:8000")
    uvicorn.run(app, host="0.0.0.0", port=8000, access_log=False)
