"""Admin endpoints — user management, log file access."""

import shutil
from datetime import datetime
from pathlib import Path

from fastapi import APIRouter, HTTPException, Request
from starlette.responses import Response

import database as db
from auth import hash_password
from config import LOG_DIR, TRAINING_DATA, logger
from routes.deps import _require_admin

router = APIRouter()


@router.get("/api/admin/users")
async def admin_get_users(request: Request):
    _require_admin(request)
    conn = await db.get_db()
    try:
        return await db.user_get_all(conn)
    finally:
        await conn.close()


@router.post("/api/admin/users")
async def admin_create_user(request: Request):
    _require_admin(request)
    body = await request.json()
    username = body.get("username", "").strip()
    password = body.get("password", "")
    display_name = body.get("display_name", username)
    role = body.get("role", "user")
    if not username or not password:
        raise HTTPException(400, "Username and password required")
    conn = await db.get_db()
    try:
        existing = await db.user_get_by_username(conn, username)
        if existing:
            raise HTTPException(409, "Username already exists")
        uid = await db.user_create(conn, username, hash_password(password), display_name, role)
        # Save optional profile fields if provided
        profile = {k: body[k] for k in ("height_cm", "birth_date", "sex") if body.get(k)}
        if profile:
            await db.user_update_profile(conn, uid, profile)
        return {"id": uid, "username": username}
    finally:
        await conn.close()


@router.put("/api/admin/users/{user_id}")
async def admin_update_user(user_id: int, request: Request):
    _require_admin(request)
    body = await request.json()
    conn = await db.get_db()
    try:
        user = await db.user_get_by_id(conn, user_id)
        if not user:
            raise HTTPException(404, "User not found")
        updates = {}
        if "role" in body and body["role"] in ("admin", "user"):
            updates["role"] = body["role"]
        if "display_name" in body:
            updates["display_name"] = body["display_name"].strip()
        if "password" in body and body["password"]:
            if len(body["password"]) < 8:
                raise HTTPException(400, "Password must be at least 8 characters")
            updates["password_hash"] = hash_password(body["password"])
            # Increment token_version to invalidate existing sessions
            current_tv = user.get("token_version") or 0
            updates["token_version"] = current_tv + 1
        if not updates:
            raise HTTPException(400, "No valid fields to update")
        sets = ", ".join(f"{k} = ?" for k in updates)
        vals = list(updates.values()) + [user_id]
        await conn.execute(f"UPDATE users SET {sets} WHERE id = ?", vals)
        await conn.commit()
        return {"ok": True}
    finally:
        await conn.close()


@router.delete("/api/admin/users/{user_id}")
async def admin_delete_user(user_id: int, request: Request):
    _require_admin(request)
    current = getattr(request.state, "user", None)
    is_self = current and current["id"] == user_id
    conn = await db.get_db()
    try:
        # Check if this is the last admin (factory reset)
        if is_self:
            cursor = await conn.execute(
                "SELECT COUNT(*) FROM users WHERE role = 'admin' AND id != ?", (user_id,)
            )
            other_admins = (await cursor.fetchone())[0]
            if other_admins > 0:
                raise HTTPException(400, "Other admins exist. Delete them first or use a non-self-delete.")
        await db.user_delete(conn, user_id)
    finally:
        await conn.close()
    # Clean per-user training data from disk
    user_data_dir = TRAINING_DATA / "users" / str(user_id)
    if user_data_dir.exists():
        shutil.rmtree(user_data_dir, ignore_errors=True)
        logger.info(f"Deleted user data directory: {user_data_dir}")
    if is_self:
        # Factory reset: clear auth cookie so browser redirects to setup
        resp = Response(content='{"ok":true,"reset":true}', media_type="application/json")
        resp.delete_cookie("token", path="/")
        return resp
    return {"ok": True}


@router.get("/api/admin/logfiles")
async def admin_list_logfiles(request: Request):
    _require_admin(request)
    files = []
    if LOG_DIR.exists():
        for f in sorted(LOG_DIR.iterdir(), key=lambda p: p.stat().st_mtime, reverse=True):
            if f.is_file():
                stat = f.stat()
                files.append({
                    "name": f.name,
                    "path": str(f),
                    "size": stat.st_size,
                    "modified": datetime.fromtimestamp(stat.st_mtime).isoformat(),
                    "is_current": f.name == "server.log",
                })
    return {"dir": str(LOG_DIR), "files": files}


@router.get("/api/admin/logfiles/{filename}")
async def admin_get_logfile(filename: str, request: Request, tail: int = 0):
    """Read a log file. tail=0 means all lines."""
    _require_admin(request)
    safe_name = Path(filename).name
    log_path = (LOG_DIR / safe_name).resolve()
    if not log_path.is_relative_to(LOG_DIR.resolve()):
        raise HTTPException(403, "Access denied")
    if not log_path.exists() or not log_path.is_file():
        raise HTTPException(404, "Log file not found")
    lines = log_path.read_text(encoding="utf-8", errors="replace").splitlines()
    total = len(lines)
    if tail > 0:
        lines = lines[-tail:]
    is_current = safe_name == "server.log"
    return {"path": str(log_path), "name": safe_name, "lines": lines,
            "total_lines": total, "is_current": is_current}


@router.delete("/api/admin/logfiles/{filename}")
async def admin_delete_logfile(filename: str, request: Request):
    """Delete a rotated log file (cannot delete the current server.log)."""
    _require_admin(request)
    safe_name = Path(filename).name
    if safe_name == "server.log":
        raise HTTPException(400, "Cannot delete the active log file")
    log_path = (LOG_DIR / safe_name).resolve()
    if not log_path.is_relative_to(LOG_DIR.resolve()):
        raise HTTPException(403, "Access denied")
    if not log_path.exists() or not log_path.is_file():
        raise HTTPException(404, "Log file not found")
    size = log_path.stat().st_size
    log_path.unlink()
    logger.info(f"Admin deleted log file: {safe_name} ({size} bytes)")
    # Save admin-only notification
    user = getattr(request.state, "user", None)
    username = user.get("username", "admin") if user else "admin"
    conn = await db.get_db()
    try:
        # Notify all admin users
        cursor = await conn.execute("SELECT id FROM users WHERE role = 'admin'")
        admins = await cursor.fetchall()
        for admin in admins:
            await conn.execute(
                "INSERT INTO notification_history (label, detail, status, link, finished_at, user_id) "
                "VALUES (?, ?, 'done', '/admin', ?, ?)",
                ("Log deleted", f"{username} deleted {safe_name} ({size / 1024:.0f} KB)",
                 datetime.now().isoformat(), admin[0])
            )
        await conn.commit()
    finally:
        await conn.close()
    return {"ok": True, "deleted": safe_name}
