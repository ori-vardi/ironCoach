"""Training Plan endpoints — CRUD operations for planned workouts."""

from fastapi import APIRouter, HTTPException, Request

import database as db
from routes.deps import _uid

router = APIRouter()


@router.get("/api/plan")
async def plan_list(request: Request):
    conn = await db.get_db()
    try:
        return await db.plan_get_all(conn, user_id=_uid(request))
    finally:
        await conn.close()


@router.get("/api/plan/week")
async def plan_week(date: str, request: Request):
    conn = await db.get_db()
    try:
        return await db.plan_get_week(conn, date, user_id=_uid(request))
    finally:
        await conn.close()


@router.post("/api/plan")
async def plan_add(request: Request):
    data = await request.json()
    conn = await db.get_db()
    try:
        new_id = await db.plan_create(conn, data, user_id=_uid(request))
        return {"id": new_id}
    finally:
        await conn.close()


@router.put("/api/plan/{plan_id}")
async def plan_edit(plan_id: int, request: Request):
    data = await request.json()
    conn = await db.get_db()
    try:
        await db.plan_update(conn, plan_id, data, user_id=_uid(request))
        return {"ok": True}
    finally:
        await conn.close()


@router.delete("/api/plan/{plan_id}")
async def plan_remove(plan_id: int, request: Request):
    conn = await db.get_db()
    try:
        await db.plan_delete(conn, plan_id, user_id=_uid(request))
        return {"ok": True}
    finally:
        await conn.close()
