"""
Chat management routes (not the WebSocket — that's in services/chat_handler.py).
Extracted from server.py for better code organization.
"""

import asyncio
import uuid
from pathlib import Path

from fastapi import APIRouter, HTTPException, Request, UploadFile

import database as db
from config import UPLOAD_DIR, _SESSIONS_DIR, logger, coach_session_id
from routes.deps import _uid
from services.task_tracker import _chat_procs, _chat_streaming


router = APIRouter()

_cleanup_lock = asyncio.Lock()


@router.post("/api/browse-folder")
async def browse_folder():
    """Open native macOS Finder folder picker and return the selected path."""
    try:
        proc = await asyncio.create_subprocess_exec(
            "osascript", "-e",
            'POSIX path of (choose folder with prompt "Select Apple Health Export Folder")',
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE,
        )
        stdout, stderr = await proc.communicate()
        if proc.returncode == 0:
            path = stdout.decode().strip().rstrip("/")
            return {"path": path}
        # User cancelled or error
        return {"path": ""}
    except Exception as e:
        logger.error("Folder picker failed: %s", e)
        raise HTTPException(500, "Folder selection failed")


_ALLOWED_EXTENSIONS = {
    '.jpg', '.jpeg', '.png', '.gif', '.webp', '.pdf',
    '.csv', '.txt', '.json', '.xml', '.gpx', '.docx',
    '.doc', '.bmp', '.heic'
}
MAX_UPLOAD_SIZE_MB = 10
MAX_UPLOAD_SIZE_BYTES = MAX_UPLOAD_SIZE_MB * 1024 * 1024


@router.post("/api/chat/upload")
async def chat_upload(file: UploadFile):
    ext = Path(file.filename).suffix.lower() if file.filename else ""
    if ext not in _ALLOWED_EXTENSIONS:
        raise HTTPException(400, f"File type not allowed. Allowed types: {', '.join(sorted(_ALLOWED_EXTENSIONS))}")
    fname = f"{uuid.uuid4().hex[:12]}{ext}"
    dest = UPLOAD_DIR / fname
    content = await file.read()
    if len(content) > MAX_UPLOAD_SIZE_BYTES:
        raise HTTPException(413, f"File too large (max {MAX_UPLOAD_SIZE_MB}MB)")
    with open(dest, "wb") as f:
        f.write(content)
    # Auto-cleanup old uploads if directory exceeds threshold
    await _cleanup_uploads()
    return {"file_path": str(dest), "filename": file.filename}


async def _cleanup_uploads():
    """Delete oldest uploaded files when directory exceeds max size threshold."""
    async with _cleanup_lock:
        try:
            conn = await db.get_db()
            try:
                max_mb = int(await db.setting_get(conn, "upload_max_mb", "200"))
            except (ValueError, TypeError):
                max_mb = 200
            finally:
                await conn.close()
            max_bytes = max_mb * 1024 * 1024
            cleanup_bytes = max(50, max_mb // 4) * 1024 * 1024

            # Collect files sorted by modification time (oldest first)
            files = []
            total = 0
            for f in UPLOAD_DIR.iterdir():
                try:
                    if f.is_file():
                        st = f.stat()
                        files.append((f, st.st_size, st.st_mtime))
                        total += st.st_size
                except (FileNotFoundError, OSError):
                    continue

            if total <= max_bytes:
                return

            files.sort(key=lambda x: x[2])  # oldest first
            freed = 0
            deleted = 0
            for fpath, sz, _ in files:
                if freed >= cleanup_bytes:
                    break
                try:
                    fpath.unlink()
                    freed += sz
                    deleted += 1
                except OSError as e:
                    logger.warning(f"Failed to delete upload {fpath.name}: {e}")
            if deleted:
                logger.info(f"Upload cleanup: deleted {deleted} files, freed {freed / 1024 / 1024:.1f}MB (threshold {max_mb}MB)")
        except Exception as e:
            logger.warning(f"Upload cleanup failed: {e}")


@router.get("/api/chat/sessions")
async def chat_sessions(request: Request, mode: str = None):
    uid = _uid(request)
    conn = await db.get_db()
    try:
        sessions = await db.chat_get_sessions(conn, user_id=uid, mode=mode)
    finally:
        await conn.close()
    # Enrich each session with CLI file size
    for s in sessions:
        agent = s.get("agent_name", "main-coach")
        if agent == "main-coach":
            cli_uuid = coach_session_id(f"main-coach-{s['session_id']}")
        else:
            cli_uuid = coach_session_id(f"{agent}-user{uid}")
        cli_path = _SESSIONS_DIR / f"{cli_uuid}.jsonl"
        s["cli_file_size"] = cli_path.stat().st_size if cli_path.exists() else 0
    return sessions


@router.get("/api/chat/specialist-sessions")
async def chat_specialist_sessions(request: Request):
    """Return specialist agent sessions (from agent_sessions table) for the chat panel."""
    uid = _uid(request)
    conn = await db.get_db()
    try:
        specialists = ['run-coach', 'swim-coach', 'bike-coach', 'nutrition-coach']
        result = {}
        for agent in specialists:
            cursor = await conn.execute(
                "SELECT agent_name, session_uuid, last_used_at, message_count, notes "
                "FROM agent_sessions WHERE agent_name = ? AND user_id = ? "
                "ORDER BY last_used_at DESC LIMIT 1",
                (agent, uid)
            )
            row = await cursor.fetchone()
            if row:
                # Try user-scoped UUID first, fall back to session_uuid from DB
                user_scoped_uuid = coach_session_id(f"{agent}-user{uid}")
                cli_path = _SESSIONS_DIR / f"{user_scoped_uuid}.jsonl"
                if not cli_path.exists():
                    cli_path = _SESSIONS_DIR / f"{row['session_uuid']}.jsonl"
                result[agent] = {
                    "agent_name": row["agent_name"],
                    "last_used_at": row["last_used_at"],
                    "message_count": row["message_count"] or 0,
                    "notes": row["notes"] or "",
                    "file_size": cli_path.stat().st_size if cli_path.exists() else 0,
                }
        return result
    finally:
        await conn.close()


@router.get("/api/chat/history/{session_id}")
async def chat_history(session_id: str, request: Request):
    uid = _uid(request)
    conn = await db.get_db()
    try:
        return await db.chat_get_history(conn, session_id, user_id=uid)
    finally:
        await conn.close()


@router.delete("/api/chat/sessions/{session_id}")
async def chat_delete_session(session_id: str, request: Request):
    uid = _uid(request)
    conn = await db.get_db()
    try:
        await db.chat_delete_session(conn, session_id, user_id=uid)
        return {"ok": True}
    finally:
        await conn.close()


@router.patch("/api/chat/sessions/{session_id}/title")
async def chat_set_title(session_id: str, request: Request):
    uid = _uid(request)
    body = await request.json()
    title = body.get("title", "").strip()[:100]
    conn = await db.get_db()
    try:
        await db.chat_set_title(conn, session_id, title, user_id=uid)
    finally:
        await conn.close()
    return {"ok": True, "title": title}


@router.post("/api/chat/save-partial")
async def chat_save_partial(request: Request):
    """Save a partial streaming message (called via sendBeacon on page unload)."""
    try:
        body = await request.json()
        session_id = body.get("session_id", "")
        content = body.get("content", "").strip()
        if not session_id or not content:
            return {"ok": False}
        uid = _uid(request)
        conn = await db.get_db()
        try:
            await db.chat_save(conn, session_id, "assistant", content + "\n\n*(response interrupted)*", user_id=uid)
        finally:
            await conn.close()
        return {"ok": True}
    except Exception:
        return {"ok": False}


@router.post("/api/chat/stop")
async def chat_stop(request: Request):
    """Kill the running Claude CLI process for a chat session (owner only)."""
    uid = _uid(request)
    data = await request.json()
    sid = data.get("session_id", "")
    # Verify session ownership
    streaming_info = _chat_streaming.get(sid)
    if streaming_info and streaming_info.get("user_id") != uid:
        raise HTTPException(403, "Not your session")
    proc = _chat_procs.get(sid)
    if proc and proc.returncode is None:
        try:
            proc.kill()
            logger.info(f"Chat CLI killed for session [{sid[:8]}]")
        except ProcessLookupError:
            pass
    return {"ok": True}
