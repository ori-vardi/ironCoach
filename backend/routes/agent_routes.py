"""
Agent definition routes.
Extracted from server.py for better code organization.
"""

import json
import re
from datetime import datetime
from pathlib import Path

from fastapi import APIRouter, HTTPException, Request

import database as db
from config import PROJECT_ROOT, _SESSIONS_DIR, logger
from routes.deps import _require_admin


router = APIRouter()


@router.get("/api/agents")
async def agents_list(request: Request):
    """List all coaching agents with their definitions and related sessions."""
    agents_dir = Path(__file__).parent.parent.parent / ".claude" / "agents"
    agents = []
    if agents_dir.exists():
        for md_file in sorted(agents_dir.glob("*.md")):
            name = md_file.stem
            content = md_file.read_text(encoding="utf-8", errors="replace")
            # Parse tools line for Agent(...) delegation
            delegates_to = []
            m = re.search(r'tools:.*Agent\(([^)]+)\)', content)
            if m:
                delegates_to = [s.strip() for s in m.group(1).split(',') if s.strip()]
            agents.append({
                "name": name,
                "file_path": str(md_file),
                "definition": content,
                "delegates_to": delegates_to,
            })

    # Compute reverse mapping: which agents delegate to this agent
    for agent in agents:
        agent["delegated_by"] = [
            a["name"] for a in agents if agent["name"] in a.get("delegates_to", [])
        ]

    # Get agent sessions from DB (these have proper agent_name mappings)
    user = getattr(request.state, "user", None)
    is_admin = user and user.get("role") == "admin"
    uid = user["id"] if user else 1
    conn = await db.get_db()
    try:
        db_sessions = await db.session_get_all(conn, user_id=None if is_admin else uid)
    finally:
        await conn.close()

    # Build DB lookup by UUID
    db_by_uuid = {s["session_uuid"]: s for s in db_sessions}

    # Scan all JSONL session files on disk (admin only — CLI sessions are developer artifacts)
    sessions_dir = _SESSIONS_DIR
    all_sessions = []
    if is_admin and sessions_dir.exists():
        for jsonl in sorted(sessions_dir.glob("*.jsonl"), key=lambda p: p.stat().st_mtime, reverse=True):
            uuid = jsonl.stem
            slug = ""
            msg_count = 0
            try:
                stat = jsonl.stat()
                from datetime import timezone
                mtime = datetime.fromtimestamp(stat.st_mtime, tz=timezone.utc).strftime("%Y-%m-%dT%H:%M:%S")
                with open(jsonl, "r", encoding="utf-8", errors="replace") as f:
                    for raw_line in f:
                        raw_line = raw_line.strip()
                        if not raw_line:
                            continue
                        try:
                            entry = json.loads(raw_line)
                            slug = entry.get("slug", "")
                            break
                        except json.JSONDecodeError:
                            break
                with open(jsonl, "r", encoding="utf-8", errors="replace") as f:
                    for line in f:
                        line = line.strip()
                        if not line:
                            continue
                        try:
                            e = json.loads(line)
                            if e.get("type") in ("human", "user", "assistant"):
                                msg_count += 1
                        except json.JSONDecodeError:
                            pass
            except Exception:
                mtime = ""

            # Use DB agent_name if available, otherwise fall back to slug
            db_rec = db_by_uuid.get(uuid)
            agent_name = db_rec["agent_name"] if db_rec else ""
            context_key = db_rec["context_key"] if db_rec else ""

            all_sessions.append({
                "session_uuid": uuid,
                "slug": slug or context_key,
                "agent_name": agent_name,
                "file_path": str(jsonl),
                "file_size": jsonl.stat().st_size if jsonl.exists() else 0,
                "last_used_at": mtime,
                "message_count": msg_count,
            })

    # Scan sub-agent transcripts inside parent session dirs (admin only)
    all_subagent_sessions = []
    if is_admin and sessions_dir.exists():
        for session_jsonl in sessions_dir.glob("*.jsonl"):
            parent_uuid = session_jsonl.stem
            parent_rec = db_by_uuid.get(parent_uuid)
            parent_name = parent_rec["agent_name"] if parent_rec else ""
            subagents_dir = sessions_dir / parent_uuid / "subagents"
            if not subagents_dir.exists():
                continue
            for sa_jsonl in sorted(subagents_dir.glob("agent-*.jsonl"),
                                   key=lambda p: p.stat().st_mtime, reverse=True):
                sa_id = sa_jsonl.stem  # e.g. "agent-af35493"
                meta_file = sa_jsonl.with_suffix(".meta.json")
                agent_type = ""
                if meta_file.exists():
                    try:
                        meta = json.loads(meta_file.read_text())
                        agent_type = meta.get("agentType", "")
                    except Exception:
                        pass
                try:
                    sa_stat = sa_jsonl.stat()
                    sa_mtime = datetime.fromtimestamp(sa_stat.st_mtime).strftime("%Y-%m-%dT%H:%M:%S")
                    sa_msg_count = 0
                    with open(sa_jsonl, "r", encoding="utf-8", errors="replace") as f:
                        for line in f:
                            line = line.strip()
                            if not line:
                                continue
                            try:
                                e = json.loads(line)
                                if e.get("type") in ("human", "user", "assistant"):
                                    sa_msg_count += 1
                            except json.JSONDecodeError:
                                pass
                except Exception:
                    sa_mtime = ""
                    sa_msg_count = 0

                all_subagent_sessions.append({
                    "session_uuid": sa_id,
                    "parent_session": parent_uuid,
                    "parent_agent": parent_name,
                    "agent_type": agent_type,
                    "file_path": str(sa_jsonl),
                    "file_size": sa_jsonl.stat().st_size if sa_jsonl.exists() else 0,
                    "last_used_at": sa_mtime,
                    "message_count": sa_msg_count,
                    "is_subagent": True,
                })

    # Group sessions by agent_name (from DB) or slug match
    for agent in agents:
        agent["sessions"] = [
            s for s in all_sessions
            if s.get("agent_name") == agent["name"]
        ]
        # Attach sub-agent sessions from this agent's parent sessions
        agent_session_uuids = {s["session_uuid"] for s in agent["sessions"]}
        agent["subagent_sessions"] = [
            s for s in all_subagent_sessions
            if s["parent_session"] in agent_session_uuids
        ]
        agent["subagent_transcript_count"] = len(agent["subagent_sessions"])

    # Count transcripts where sub-agents appear (by agent_type in meta.json)
    for agent in agents:
        agent["transcript_appearances"] = [
            s for s in all_subagent_sessions
            if s["agent_type"] == agent["name"]
        ]

    # Unmatched = sessions not assigned to any agent
    matched_uuids = set()
    for a in agents:
        for s in a["sessions"]:
            matched_uuids.add(s["session_uuid"])
    unmatched = [s for s in all_sessions if s["session_uuid"] not in matched_uuids]

    return {"agents": agents, "unmatched_sessions": unmatched}


@router.put("/api/agents/{agent_name}")
async def agent_update(agent_name: str, request: Request):
    """Update an agent definition file. Admin only."""
    user = getattr(request.state, "user", None)
    if not user or user.get("role") != "admin":
        raise HTTPException(403, "Admin only")
    body = await request.json()
    definition = body.get("definition", "")
    if not definition.strip():
        raise HTTPException(400, "Definition cannot be empty")
    agents_dir = Path(__file__).parent.parent.parent / ".claude" / "agents"
    safe_name = Path(agent_name).name  # strip any ../
    agent_file = agents_dir / f"{safe_name}.md"
    if not agent_file.exists():
        raise HTTPException(404, f"Agent '{agent_name}' not found")
    agent_file.write_text(definition, encoding="utf-8")
    return {"ok": True}
