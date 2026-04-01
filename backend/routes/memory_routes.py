"""Memory endpoints — Coach Memory (shared) + Agent Memory (per-agent)."""

from fastapi import APIRouter, HTTPException, Request

import database as db
from routes.deps import _uid

router = APIRouter()


# ── Coach Memory (shared across all coaches) ──

@router.get("/api/memory")
async def memory_list(request: Request):
    uid = _uid(request)
    conn = await db.get_db()
    try:
        return await db.memory_get_all(conn, user_id=uid)
    finally:
        await conn.close()


@router.post("/api/memory")
async def memory_create(request: Request):
    data = await request.json()
    content = data.get("content", "").strip()
    if not content:
        raise HTTPException(400, "content is required")
    uid = _uid(request)
    conn = await db.get_db()
    try:
        mem_id = await db.memory_add(conn, content, user_id=uid)
    finally:
        await conn.close()
    return {"id": mem_id}


@router.put("/api/memory/{mem_id}")
async def memory_edit(mem_id: int, request: Request):
    data = await request.json()
    content = data.get("content", "").strip()
    if not content:
        raise HTTPException(400, "content is required")
    uid = _uid(request)
    conn = await db.get_db()
    try:
        await db.memory_update(conn, mem_id, content, user_id=uid)
    finally:
        await conn.close()
    return {"ok": True}


@router.delete("/api/memory/{mem_id}")
async def memory_remove(mem_id: int, request: Request):
    uid = _uid(request)
    conn = await db.get_db()
    try:
        await db.memory_delete(conn, mem_id, user_id=uid)
    finally:
        await conn.close()
    return {"ok": True}


@router.get("/api/memory/all")
async def memory_all(request: Request):
    """Return all memories grouped: coach_memory + per-agent memories."""
    uid = _uid(request)
    conn = await db.get_db()
    try:
        coach = await db.memory_get_all(conn, user_id=uid)
        agent_types = await db.agent_memory_get_all_types(conn, uid)
        groups = [{"scope": "all-coaches", "label": "All Coaches", "memories": coach}]
        for agent_type in sorted(agent_types.keys()):
            mems = await db.agent_memory_get_all(conn, uid, agent_type)
            groups.append({"scope": agent_type, "label": agent_type, "memories": mems})
        return groups
    finally:
        await conn.close()


# ── Agent Memory (per agent type) ──

@router.get("/api/memory/agent")
async def agent_memory_types(request: Request):
    uid = _uid(request)
    conn = await db.get_db()
    try:
        return await db.agent_memory_get_all_types(conn, uid)
    finally:
        await conn.close()


@router.get("/api/memory/agent/{agent_type}")
async def agent_memory_list(agent_type: str, request: Request):
    uid = _uid(request)
    conn = await db.get_db()
    try:
        return await db.agent_memory_get_all(conn, uid, agent_type)
    finally:
        await conn.close()


@router.post("/api/memory/agent/{agent_type}")
async def agent_memory_create(agent_type: str, request: Request):
    data = await request.json()
    content = data.get("content", "").strip()
    if not content:
        raise HTTPException(400, "content is required")
    uid = _uid(request)
    conn = await db.get_db()
    try:
        mem_id = await db.agent_memory_add(conn, uid, agent_type, content)
    finally:
        await conn.close()
    return {"id": mem_id}


@router.put("/api/memory/agent/{mem_id}")
async def agent_memory_edit(mem_id: int, request: Request):
    data = await request.json()
    content = data.get("content", "").strip()
    if not content:
        raise HTTPException(400, "content is required")
    uid = _uid(request)
    conn = await db.get_db()
    try:
        await db.agent_memory_update(conn, mem_id, content, uid)
    finally:
        await conn.close()
    return {"ok": True}


@router.delete("/api/memory/agent/{mem_id}")
async def agent_memory_remove(mem_id: int, request: Request):
    uid = _uid(request)
    conn = await db.get_db()
    try:
        await db.agent_memory_delete(conn, mem_id, uid)
    finally:
        await conn.close()
    return {"ok": True}
