"""Token usage endpoints — cost/token tracking for Claude CLI calls."""

from fastapi import APIRouter, HTTPException, Request

import database as db
from routes.deps import _uid, _require_admin

router = APIRouter()


@router.get("/api/usage")
async def get_usage_summary(request: Request, from_date: str = ""):
    """Get token usage summary (totals). Optional from_date filter."""
    uid = _uid(request)
    conn = await db.get_db()
    try:
        summary = await db.usage_get_summary(conn, user_id=uid, from_date=from_date or None)
        return summary
    finally:
        await conn.close()


@router.get("/api/usage/daily")
async def get_usage_daily(request: Request, from_date: str = ""):
    """Get daily token usage breakdown."""
    uid = _uid(request)
    conn = await db.get_db()
    try:
        rows = await db.usage_get_daily(conn, user_id=uid, from_date=from_date or None)
        return rows
    finally:
        await conn.close()


@router.get("/api/usage/by-model")
async def get_usage_by_model(request: Request):
    """Get token usage breakdown by model."""
    uid = _uid(request)
    conn = await db.get_db()
    try:
        return await db.usage_get_by_model(conn, user_id=uid)
    finally:
        await conn.close()


@router.get("/api/usage/daily-agents")
async def get_usage_daily_agents(request: Request, date: str = ""):
    """Get per-agent+model breakdown for a specific date."""
    uid = _uid(request)
    if not date:
        return {"rows": [], "current_model": ""}
    conn = await db.get_db()
    try:
        rows = await db.usage_get_daily_by_agent(conn, user_id=uid, date=date)
        current_model = await db.setting_get(conn, "agent_model", "")
        return {"rows": rows, "current_model": current_model}
    finally:
        await conn.close()


@router.get("/api/usage/recent")
async def get_usage_recent(request: Request, limit: int = 50):
    """Get recent individual usage records."""
    uid = _uid(request)
    conn = await db.get_db()
    try:
        rows = await db.usage_get_recent(conn, limit=limit, user_id=uid)
        return rows
    finally:
        await conn.close()


@router.get("/api/usage/by-agent")
async def get_usage_by_agent(request: Request):
    """Get token usage breakdown by agent+model for cost analysis."""
    uid = _uid(request)
    conn = await db.get_db()
    try:
        rows = await db.usage_get_by_agent(conn, user_id=uid)
        current_model = await db.setting_get(conn, "agent_model", "")
        return {"rows": rows, "current_model": current_model}
    finally:
        await conn.close()


@router.get("/api/admin/usage")
async def admin_get_usage(request: Request):
    """Admin: get per-user usage breakdown + grand total."""
    _require_admin(request)
    conn = await db.get_db()
    try:
        per_user = await db.usage_get_per_user(conn)
        total = await db.usage_get_summary(conn)
        return {"per_user": per_user, "total": total}
    finally:
        await conn.close()
