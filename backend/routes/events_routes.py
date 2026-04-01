"""Events endpoints — multi-event management (races, goals)."""

from datetime import datetime

from fastapi import APIRouter, HTTPException, Request

import database as db
from config import EVENT_TYPE_PRESETS
from routes.deps import _uid

router = APIRouter()


@router.get("/api/race")
async def race_get_legacy(request: Request):
    """Backward-compatible: returns the primary event as race info."""
    conn = await db.get_db()
    try:
        ev = await db.events_get_primary(conn, _uid(request))
        if not ev:
            # Fallback to old race_info table
            ev = await db.race_get(conn)
        if ev:
            # Normalize field names for backward compat
            info = {**ev}
            info.setdefault("race_name", ev.get("event_name", ""))
            info.setdefault("race_date", ev.get("event_date", ""))
            try:
                race_dt = datetime.strptime(info.get("race_date") or info.get("event_date", ""), "%Y-%m-%d")
                info["days_until"] = max(0, (race_dt - datetime.now()).days)
            except ValueError:
                info["days_until"] = None
            return info
        return None
    finally:
        await conn.close()


@router.get("/api/events")
async def events_list(request: Request):
    conn = await db.get_db()
    try:
        events = await db.events_get_all(conn, _uid(request))
        for ev in events:
            try:
                ev_dt = datetime.strptime(ev["event_date"], "%Y-%m-%d")
                ev["days_until"] = max(0, (ev_dt - datetime.now()).days)
            except ValueError:
                ev["days_until"] = None
        return events
    finally:
        await conn.close()


@router.get("/api/events/presets")
async def events_presets():
    return EVENT_TYPE_PRESETS


@router.get("/api/events/{event_id}")
async def events_get_one(event_id: int, request: Request):
    conn = await db.get_db()
    try:
        ev = await db.events_get(conn, event_id, _uid(request))
        if not ev:
            raise HTTPException(404, "Event not found")
        try:
            ev_dt = datetime.strptime(ev["event_date"], "%Y-%m-%d")
            ev["days_until"] = max(0, (ev_dt - datetime.now()).days)
        except ValueError:
            ev["days_until"] = None
        return ev
    finally:
        await conn.close()


@router.post("/api/events")
async def events_create(request: Request):
    data = await request.json()
    uid = _uid(request)
    conn = await db.get_db()
    try:
        # Auto-set as primary if this is the first event
        existing = await db.events_get_all(conn, user_id=uid)
        if not existing:
            data["is_primary"] = True
        eid = await db.events_create(conn, data, user_id=uid)
        return {"ok": True, "id": eid}
    finally:
        await conn.close()


@router.put("/api/events/{event_id}")
async def events_update(event_id: int, request: Request):
    data = await request.json()
    uid = _uid(request)
    conn = await db.get_db()
    try:
        await db.events_update(conn, event_id, data, user_id=uid)
        return {"ok": True}
    finally:
        await conn.close()


@router.delete("/api/events/{event_id}")
async def events_remove(event_id: int, request: Request):
    uid = _uid(request)
    conn = await db.get_db()
    try:
        await db.events_delete(conn, event_id, user_id=uid)
        return {"ok": True}
    finally:
        await conn.close()


@router.put("/api/events/{event_id}/primary")
async def events_set_primary(event_id: int, request: Request):
    uid = _uid(request)
    conn = await db.get_db()
    try:
        await db.events_set_primary(conn, event_id, user_id=uid)
        return {"ok": True}
    finally:
        await conn.close()
