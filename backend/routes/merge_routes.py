"""Workout merge, hidden workout management, and delete endpoints."""

import asyncio
import csv
import json

from fastapi import APIRouter, HTTPException, Request

import database as db
from config import logger
from data_processing.helpers import _invalidate_hidden_cache
from data_processing import _find_workout_file
from routes.deps import _uid, _user_data_dir

router = APIRouter()

_merge_lock = asyncio.Lock()


@router.post("/api/merges")
async def save_manual_merges(request: Request):
    """Save user-approved merge pairs (from PostImportModal)."""
    uid = _uid(request)
    key = f"manual_merges_{uid}"
    async with _merge_lock:
        data = await request.json()
        new_pairs = data.get("pairs", [])  # [[wnum_a, wnum_b], ...]
        conn = await db.get_db()
        try:
            existing_json = await db.setting_get(conn, key, "[]")
            try:
                existing = json.loads(existing_json)
            except json.JSONDecodeError:
                existing = []
            # Normalize: always store as [min, max] to avoid duplicates
            existing_set = {(min(a, b), max(a, b)) for a, b in existing}
            for pair in new_pairs:
                if len(pair) == 2:
                    existing_set.add((min(pair[0], pair[1]), max(pair[0], pair[1])))
            merged_list = sorted(existing_set)
            await db.setting_set(conn, key, json.dumps(merged_list))
            return {"ok": True, "total_merges": len(merged_list)}
        finally:
            await conn.close()


@router.get("/api/workouts/hidden")
async def get_hidden_workouts(request: Request):
    uid = _uid(request)
    conn = await db.get_db()
    try:
        val = await db.setting_get(conn, f"hidden_workouts_{uid}", "[]")
        return json.loads(val)
    finally:
        await conn.close()


@router.post("/api/workouts/hide")
async def hide_workouts(request: Request):
    """Hide workout_nums (add to hidden list)."""
    uid = _uid(request)
    key = f"hidden_workouts_{uid}"
    data = await request.json()
    nums = data.get("workout_nums", [])
    if not nums:
        raise HTTPException(400, "workout_nums required")
    conn = await db.get_db()
    try:
        existing = set(json.loads(await db.setting_get(conn, key, "[]")))
        existing.update(int(n) for n in nums)
        await db.setting_set(conn, key, json.dumps(sorted(existing)))
        _invalidate_hidden_cache(uid)
        return {"ok": True, "hidden_count": len(existing)}
    finally:
        await conn.close()


@router.post("/api/workouts/unhide")
async def unhide_workouts(request: Request):
    """Unhide workout_nums (remove from hidden list)."""
    uid = _uid(request)
    key = f"hidden_workouts_{uid}"
    data = await request.json()
    nums = set(int(n) for n in data.get("workout_nums", []))
    if not nums:
        raise HTTPException(400, "workout_nums required")
    conn = await db.get_db()
    try:
        existing = set(json.loads(await db.setting_get(conn, key, "[]")))
        existing -= nums
        await db.setting_set(conn, key, json.dumps(sorted(existing)))
        _invalidate_hidden_cache(uid)
        return {"ok": True, "hidden_count": len(existing)}
    finally:
        await conn.close()


@router.post("/api/workouts/delete")
async def delete_workouts(request: Request):
    """Permanently delete workouts: per-workout files, summary row, DB insight, dismissed entry."""
    data = await request.json()
    nums = data.get("workout_nums", [])
    if not nums:
        raise HTTPException(400, "workout_nums required")
    uid = _uid(request)
    dd = _user_data_dir(request)
    nums_int = [int(n) for n in nums]
    deleted_files = 0

    # 1. Delete per-workout files (.csv, .splits.json, .sections.json, .gps_segments.json)
    for num in nums_int:
        for suffix in [".csv", ".splits.json", ".sections.json", ".gps_segments.json"]:
            f = _find_workout_file(num, suffix, dd)
            if f and f.exists():
                f.unlink()
                deleted_files += 1

    # 2. Remove rows from 00_workouts_summary.csv
    summary_path = dd / "00_workouts_summary.csv"
    removed_count = 0
    if summary_path.exists():
        nums_str = {str(n) for n in nums_int}
        rows_kept = []
        with open(summary_path, newline="") as f:
            reader = csv.reader(f)
            for row in reader:
                if row and row[0] not in nums_str:
                    rows_kept.append(row)
                else:
                    removed_count += 1
        with open(summary_path, "w", newline="") as f:
            writer = csv.writer(f)
            writer.writerows(rows_kept)

    # 3. Clean .export_state.json so re-import detects workout as new
    state_path = dd / ".export_state.json"
    if state_path.exists():
        try:
            state = json.loads(state_path.read_text())
            processed = state.get("processed_workouts", {})
            # Find and remove entries whose value contains "workout_NNN_"
            keys_to_remove = []
            for k, v in processed.items():
                for num in nums_int:
                    if f"workout_{num:03d}_" in str(v):
                        keys_to_remove.append(k)
                        break
            for k in keys_to_remove:
                del processed[k]
            if keys_to_remove:
                state["processed_workouts"] = processed
                # Adjust summary_lines count
                state["summary_lines"] = max(0, state.get("summary_lines", 0) - removed_count)
                state_path.write_text(json.dumps(state, ensure_ascii=False, indent=2))
        except Exception as e:
            logger.warning(f"Failed to clean export state: {e}")

    # 4. Delete insights from DB + clean up hidden/dismissed/pending lists
    conn = await db.get_db()
    try:
        for num in nums_int:
            await conn.execute(
                "DELETE FROM workout_insights WHERE workout_num = ? AND user_id = ?",
                (num, uid)
            )
        await conn.commit()

        for setting_key in [f"hidden_workouts_{uid}", f"dismissed_insights_{uid}"]:
            existing = set(json.loads(await db.setting_get(conn, setting_key, "[]")))
            if existing & set(nums_int):
                existing -= set(nums_int)
                await db.setting_set(conn, setting_key, json.dumps(sorted(existing)))

        # 5. Remove deleted workouts from pending import data
        pending_raw = await db.setting_get(conn, f"pending_import_{uid}", "")
        if pending_raw:
            pending = json.loads(pending_raw)
            deleted_set = set(nums_int)
            pending["workouts"] = [w for w in pending.get("workouts", []) if int(w.get("workout_num", 0)) not in deleted_set]
            pending["mergeCandidates"] = [mc for mc in pending.get("mergeCandidates", [])
                                          if not deleted_set.intersection(int(n) for n in mc.get("workout_nums", []))]
            pending["brickSessions"] = [bs for bs in pending.get("brickSessions", [])
                                        if not deleted_set.intersection(int(w.get("workout_num", 0)) for w in bs.get("workouts", []))]
            if pending["workouts"]:
                await db.setting_set(conn, f"pending_import_{uid}", json.dumps(pending))
            else:
                await db.setting_set(conn, f"pending_import_{uid}", "")
    finally:
        await conn.close()

    _invalidate_hidden_cache(uid)
    logger.info(f"Deleted workouts {nums_int}: {deleted_files} files removed")
    return {"ok": True, "deleted_files": deleted_files, "workout_nums": nums_int}
