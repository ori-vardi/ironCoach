"""
Session management routes.
Extracted from server.py for better code organization.
"""

import asyncio
import json
import re
import shutil
from datetime import datetime, timezone
from pathlib import Path

from fastapi import APIRouter, HTTPException, Request

import database as db
from config import _SESSIONS_DIR, PROJECT_ROOT, logger, coach_session_id
from routes.deps import _require_admin, _uid
from services.claude_cli import _generate_session_title, _generate_subagent_title


router = APIRouter()


# Track in-flight title generation tasks to avoid duplicates
_pending_title_gen: set[str] = set()


def _parse_jsonl_session_info(jsonl_path):
    """Extract summary, message count, and agent type from a JSONL session file.

    For large files (>500KB), reads only the first 200 lines for summary/agent_type
    and estimates msg_count from file size to avoid blocking the event loop.
    """
    msg_count = 0
    summary = ""
    agent_type = ""
    MAX_LINES_FULL = 50_000  # safety cap for full parsing
    try:
        file_size = jsonl_path.stat().st_size
        # For large files, only parse first 200 lines for metadata, estimate msg_count
        large_file = file_size > 500_000  # 500KB
        line_limit = 200 if large_file else MAX_LINES_FULL
        lines_read = 0

        with open(jsonl_path, "r", encoding="utf-8", errors="replace") as f:
            for raw_line in f:
                raw_line = raw_line.strip()
                if not raw_line:
                    continue
                lines_read += 1
                if lines_read > line_limit:
                    break
                try:
                    entry = json.loads(raw_line)
                    entry_type = entry.get("type", "")
                    if entry_type in ("human", "user", "assistant"):
                        msg_count += 1
                    # Detect agent type from init event or tool_use
                    if not agent_type:
                        if entry_type == "system" and entry.get("subtype") == "init":
                            agent_type = entry.get("agent_name", "")
                        elif entry_type == "tool_use" and entry.get("name") == "Agent":
                            inp = entry.get("input", {})
                            agent_type = inp.get("subagent_type", "")
                    # Extract summary from first user message
                    if not summary and entry_type in ("human", "user"):
                        msg = entry.get("message", {})
                        content = msg.get("content", "") if isinstance(msg, dict) else ""
                        if isinstance(content, list):
                            content = " ".join(b.get("text", "") for b in content if isinstance(b, dict) and b.get("type") == "text")
                        if isinstance(content, str) and content.strip():
                            m = re.search(r'summary="([^"]+)"', content)
                            if m:
                                summary = m.group(1)
                            else:
                                first = content.strip().split("\n")[0]
                                first = re.sub(r'\*+', '', first).strip()
                                first = re.sub(r'^(Task \d+:?\s*)', '', first).strip()
                                summary = first[:100] if len(first) > 100 else first
                except json.JSONDecodeError:
                    pass

        # For large files, extrapolate msg_count from sampled portion
        if large_file and lines_read >= line_limit and msg_count > 0:
            # Estimate: bytes_per_line ≈ bytes_read / lines_read, then scale
            # But we only read first N lines, so estimate total lines from file size
            avg_bytes_per_line = file_size / max(lines_read, 1)  # rough
            estimated_total_lines = file_size / avg_bytes_per_line if avg_bytes_per_line > 0 else lines_read
            msg_ratio = msg_count / lines_read
            msg_count = int(estimated_total_lines * msg_ratio)
    except Exception:
        pass
    return {"msg_count": msg_count, "summary": summary, "agent_type": agent_type}


@router.get("/api/sessions")
async def sessions_list(request: Request):
    """List all agent sessions — merges DB records with JSONL files on disk."""
    uid = _uid(request)
    user = getattr(request.state, "user", None)
    is_admin = user and user.get("role") == "admin"
    conn = await db.get_db()
    try:
        # Admin sees all users' sessions; regular users see only their own
        sessions = await db.session_get_all(conn, user_id=None if is_admin else uid)
    finally:
        await conn.close()

    # Build lookup of DB sessions by UUID
    db_uuids = {s["session_uuid"] for s in sessions}

    # Scan disk for JSONL files not in DB (admin only — CLI sessions are developer artifacts)
    sessions_dir = _SESSIONS_DIR
    if is_admin and sessions_dir.exists():
        for jsonl in sorted(sessions_dir.glob("*.jsonl"), key=lambda p: p.stat().st_mtime, reverse=True):
            uuid = jsonl.stem
            if uuid in db_uuids:
                continue
            # Parse JSONL to extract slug, summary, message count
            agent_name = "claude-cli"
            slug = ""
            created_at = ""
            try:
                stat = jsonl.stat()
                created_at = datetime.fromtimestamp(stat.st_mtime, tz=timezone.utc).strftime("%Y-%m-%dT%H:%M:%S")
                # Get slug from first lines
                with open(jsonl, "r", encoding="utf-8", errors="replace") as f:
                    for raw_line in f:
                        raw_line = raw_line.strip()
                        if not raw_line:
                            continue
                        try:
                            entry = json.loads(raw_line)
                            if not slug and entry.get("slug"):
                                slug = entry["slug"]
                                break
                        except json.JSONDecodeError:
                            pass
            except Exception:
                pass
            info = _parse_jsonl_session_info(jsonl)

            sessions.append({
                "id": 0,
                "session_uuid": uuid,
                "agent_name": agent_name,
                "slug": slug,
                "context_key": info["summary"] or slug,
                "created_at": created_at,
                "last_used_at": created_at,
                "message_count": info["msg_count"],
                "notes": "discovered from disk",
            })

    # Scan sub-agent sessions inside parent dirs (admin only)
    if is_admin and sessions_dir.exists():
        for parent_dir in sessions_dir.iterdir():
            if not parent_dir.is_dir():
                continue
            sa_dir = parent_dir / "subagents"
            if not sa_dir.exists():
                continue
            parent_uuid = parent_dir.name
            for sa_jsonl in sorted(sa_dir.glob("*.jsonl"), key=lambda p: p.stat().st_mtime, reverse=True):
                sa_uuid = sa_jsonl.stem
                if sa_uuid in db_uuids:
                    continue
                sa_stat = sa_jsonl.stat()
                sa_created = datetime.fromtimestamp(sa_stat.st_mtime, tz=timezone.utc).strftime("%Y-%m-%dT%H:%M:%S")
                sa_info = _parse_jsonl_session_info(sa_jsonl)
                # Read agent type from .meta.json (written by Claude CLI)
                agent_type = sa_info["agent_type"]
                if not agent_type:
                    meta_file = sa_jsonl.with_suffix(".meta.json")
                    if meta_file.exists():
                        try:
                            agent_type = json.loads(meta_file.read_text()).get("agentType", "")
                        except Exception:
                            pass
                sessions.append({
                    "id": 0,
                    "session_uuid": sa_uuid,
                    "agent_name": agent_type or "sub-agent",
                    "slug": "",
                    "context_key": sa_info["summary"],
                    "created_at": sa_created,
                    "last_used_at": sa_created,
                    "message_count": sa_info["msg_count"],
                    "notes": "sub-agent",
                    "parent_session": parent_uuid,
                })

    # Build a map from agent session UUID → chat session title
    # Main-coach chat sessions have UUID = coach_session_id(f"main-coach-{chat_sid}")
    conn2 = await db.get_db()
    try:
        chat_sessions = await db.chat_get_sessions(conn2, user_id=None if is_admin else uid)
        # Also load sub-agent titles from chat_session_titles
        sub_titles_cur = await conn2.execute("SELECT session_id, title FROM chat_session_titles")
        sub_titles_map = {r["session_id"]: r["title"] for r in await sub_titles_cur.fetchall()}
    finally:
        await conn2.close()
    uuid_to_chat_title = {}
    for cs in chat_sessions:
        agent_uuid = coach_session_id(f"main-coach-{cs['session_id']}")
        uuid_to_chat_title[agent_uuid] = cs.get("title") or cs.get("preview", "")

    # Build set of chat-originating parent UUIDs (main-coach sessions with a chat title)
    chat_parent_uuids = {uuid for uuid, title in uuid_to_chat_title.items() if title}

    # Build UUID → session map for parent name lookups
    uuid_to_session = {s["session_uuid"]: s for s in sessions}

    # Enrich all with file size and file path; filter out empty sessions
    needs_title = []  # sub-agents needing AI title generation
    result = []
    for s in sessions:
        uuid = s["session_uuid"]
        # Add chat session title if this is a main-coach chat session
        s["chat_title"] = uuid_to_chat_title.get(uuid, "") or sub_titles_map.get(uuid, "")
        # Mark sub-agent source: "chat" if parent is a chat session, "insight" otherwise
        parent = s.get("parent_session", "")
        if parent:
            s["source"] = "chat" if parent in chat_parent_uuids else "insight"
            # Add parent session name for tooltip
            parent_s = uuid_to_session.get(parent)
            s["parent_name"] = (
                uuid_to_chat_title.get(parent, "") or
                (parent_s["context_key"] if parent_s else "")
            ) if parent_s or parent in uuid_to_chat_title else ""
            # Generate fallback title if no AI title exists yet
            has_ai_title = uuid in sub_titles_map
            if not s["chat_title"] and s.get("context_key"):
                ts = s.get("created_at", "")[:16].replace("T", " ")
                fallback = _generate_session_title(s["context_key"])
                if fallback:
                    s["chat_title"] = f"{fallback} ({ts})" if ts else fallback
            # Queue AI title generation if no persisted title (and not already queued)
            if not has_ai_title and s.get("context_key") and uuid not in _pending_title_gen:
                needs_title.append((uuid, s["context_key"], s.get("chat_title", ""), uid or 1))
                _pending_title_gen.add(uuid)
        # Check main dir then sub-agent dirs
        jsonl_path = sessions_dir / f"{uuid}.jsonl"
        if not jsonl_path.exists():
            parent = s.get("parent_session", "")
            if parent:
                jsonl_path = sessions_dir / parent / "subagents" / f"{uuid}.jsonl"
        if jsonl_path.exists():
            sz = jsonl_path.stat().st_size
            s["file_size"] = sz
            s["file_path"] = str(jsonl_path)
            # Skip tiny/empty sessions (< 200 bytes = just metadata, no real content)
            if sz < 200 and s.get("id", 0) == 0:
                continue
        else:
            s["file_size"] = 0
            s["file_path"] = ""
        result.append(s)

    # Background: generate AI titles for sub-agents that only have fallback titles
    if needs_title:
        async def _gen_sub_titles(items):
            for sa_uuid, ctx, fallback, u_id in items:
                try:
                    await _generate_subagent_title(sa_uuid, ctx, u_id)
                except Exception:
                    pass
                finally:
                    _pending_title_gen.discard(sa_uuid)
        asyncio.create_task(_gen_sub_titles(needs_title))

    return result


def _parse_jsonl_transcript(jsonl_path: Path, include_tools: bool = False) -> list[dict]:
    """Parse a JSONL session file into a conversation message list.

    Args:
        jsonl_path: Path to the .jsonl file.
        include_tools: If True, include tool_use and tool_result entries.
    """
    messages = []
    with open(jsonl_path, "r", encoding="utf-8", errors="replace") as f:
        for line in f:
            line = line.strip()
            if not line:
                continue
            try:
                entry = json.loads(line)
                msg_type = entry.get("type", "")
                if msg_type in ("human", "user"):
                    msg_content = entry.get("message", {}).get("content", "")
                    if include_tools and isinstance(msg_content, list) and any(b.get("type") == "tool_result" for b in msg_content):
                        for block in msg_content:
                            if block.get("type") == "tool_result":
                                text = ""
                                for c in (block.get("content", []) if isinstance(block.get("content"), list) else []):
                                    if isinstance(c, dict) and c.get("type") == "text":
                                        text += c.get("text", "")
                                    elif isinstance(c, str):
                                        text += c
                                if not text and isinstance(block.get("content"), str):
                                    text = block["content"]
                                messages.append({"role": "tool", "content": str(text)[:2000]})
                    else:
                        if isinstance(msg_content, str):
                            content_text = msg_content
                        elif isinstance(msg_content, list):
                            content_text = " ".join(b.get("text", "") for b in msg_content if isinstance(b, dict) and b.get("type") == "text")
                        else:
                            content_text = str(msg_content)
                        if content_text.strip():
                            messages.append({"role": "user", "content": content_text})
                elif msg_type == "assistant":
                    content_parts = entry.get("message", {}).get("content", [])
                    text_parts = []
                    tool_calls = []
                    for part in content_parts if isinstance(content_parts, list) else []:
                        if part.get("type") == "text":
                            text_parts.append(part.get("text", ""))
                        elif include_tools and part.get("type") == "tool_use":
                            tool_calls.append({
                                "tool": part.get("name", ""),
                                "input": part.get("input", {})
                            })
                    text = "\n".join(text_parts)
                    if text or tool_calls:
                        msg = {"role": "assistant", "content": text}
                        if tool_calls:
                            msg["tool_calls"] = tool_calls
                        messages.append(msg)
                elif include_tools and msg_type == "tool_result":
                    content = entry.get("message", {}).get("content", "")
                    if isinstance(content, list):
                        content = "\n".join(p.get("text", "") for p in content if p.get("type") == "text")
                    messages.append({"role": "tool", "content": str(content)[:2000]})
            except json.JSONDecodeError:
                continue
    return messages


@router.get("/api/sessions/{session_uuid}/transcript")
async def session_transcript(session_uuid: str, request: Request):
    """Parse a session's JSONL file and return conversation as JSON array."""
    _require_admin(request)
    jsonl_path = _SESSIONS_DIR / f"{session_uuid}.jsonl"
    if not jsonl_path.exists():
        for parent_dir in _SESSIONS_DIR.iterdir():
            if parent_dir.is_dir():
                sa_path = parent_dir / "subagents" / f"{session_uuid}.jsonl"
                if sa_path.exists():
                    jsonl_path = sa_path
                    break
        else:
            raise HTTPException(404, f"Session file not found: {session_uuid}")
    return _parse_jsonl_transcript(jsonl_path, include_tools=True)


@router.get("/api/admin/session-file-transcript")
async def admin_session_file_transcript(request: Request, path: str = ""):
    """Read a JSONL session file transcript by path (admin only, restricted to sessions dir)."""
    _require_admin(request)
    if not path:
        raise HTTPException(400, "path parameter required")
    file_path = Path(path).resolve()
    sessions_dir = _SESSIONS_DIR.resolve()
    if not file_path.is_relative_to(sessions_dir):
        raise HTTPException(403, "Path must be inside sessions directory")
    if not file_path.exists():
        raise HTTPException(404, f"File not found: {path}")
    return _parse_jsonl_transcript(file_path, include_tools=False)


@router.delete("/api/admin/session-file")
async def admin_delete_session_file(request: Request, path: str = ""):
    """Delete a session file by path (admin only, restricted to sessions dir)."""
    _require_admin(request)
    if not path:
        raise HTTPException(400, "path parameter required")
    file_path = Path(path).resolve()
    sessions_dir = _SESSIONS_DIR.resolve()
    if not file_path.is_relative_to(sessions_dir):
        raise HTTPException(403, "Path must be inside sessions directory")
    if not file_path.exists():
        raise HTTPException(404, f"File not found")
    file_path.unlink()
    return {"ok": True}


@router.delete("/api/sessions/{session_uuid}")
async def session_delete(session_uuid: str, request: Request):
    """Delete a session from DB and disk."""
    uid = _uid(request)
    conn = await db.get_db()
    try:
        await db.session_delete(conn, session_uuid, user_id=uid)
    finally:
        await conn.close()

    jsonl_path = _SESSIONS_DIR / f"{session_uuid}.jsonl"
    if jsonl_path.exists():
        jsonl_path.unlink()

    # Cascade: delete sub-agent sessions (live in {uuid}/subagents/)
    deleted_subs = []
    subagents_dir = _SESSIONS_DIR / session_uuid / "subagents"
    if subagents_dir.exists():
        conn2 = await db.get_db()
        try:
            for sa_jsonl in subagents_dir.glob("*.jsonl"):
                sa_uuid = sa_jsonl.stem
                try:
                    await db.session_delete(conn2, sa_uuid, user_id=uid)
                except Exception:
                    pass
                sa_jsonl.unlink()
                # Also remove .meta.json if present
                meta = sa_jsonl.with_suffix(".meta.json")
                if meta.exists():
                    meta.unlink()
                deleted_subs.append(sa_uuid)
        finally:
            await conn2.close()
        # Remove the subagents dir and parent dir if empty
        shutil.rmtree(_SESSIONS_DIR / session_uuid, ignore_errors=True)

    return {"deleted": session_uuid, "deleted_subagents": deleted_subs}


@router.delete("/api/sessions")
async def sessions_delete_all(agent: str = "", request: Request = None):
    """Bulk delete sessions, optionally filtered by agent name."""
    uid = _uid(request)
    conn = await db.get_db()
    try:
        if agent:
            sessions = await db.session_get_all(conn, user_id=uid)
            to_delete = [s for s in sessions if s["agent_name"] == agent]
        else:
            to_delete = await db.session_get_all(conn, user_id=uid)

        await db.session_delete_all(conn, agent if agent else None, user_id=uid)
    finally:
        await conn.close()

    # Delete JSONL files
    deleted_count = 0
    for s in to_delete:
        jsonl_path = _SESSIONS_DIR / f"{s['session_uuid']}.jsonl"
        if jsonl_path.exists():
            jsonl_path.unlink()
            deleted_count += 1

    return {"deleted_count": len(to_delete), "files_removed": deleted_count}
