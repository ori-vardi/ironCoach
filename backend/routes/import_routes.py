"""
Import-related routes.
Extracted from server.py for better code organization.
"""

import asyncio
import json
import os
import shutil
import sys
import time
from datetime import datetime
from pathlib import Path

from fastapi import APIRouter, HTTPException, Request

import database as db
from config import PROJECT_ROOT, logger, _MERGEABLE_DISCIPLINES, INSIGHT_CUTOFF_DATE
from routes.deps import _uid, _user_data_dir
from services.task_tracker import _register_task, _unregister_task

_EXPORT_XML_NAMES = {"export.xml", "ייצוא.xml"}
_MERGE_CANDIDATE_GAP_MIN = 10
_MERGE_CANDIDATE_GAP_MAX = 30


def _find_export_xml(folder: Path) -> Path | None:
    """Find Apple Health export XML in a folder tree.
    Supports English (export.xml), Hebrew (ייצוא.xml), and mojibake variants
    (zip files may encode Hebrew filenames in CP437)."""
    # Try known names first
    for name in _EXPORT_XML_NAMES:
        for p in folder.rglob(name):
            return p
    # Fallback: find any .xml that starts with Apple Health DOCTYPE
    for p in folder.rglob("*.xml"):
        if p.name == "export_cda.xml":
            continue
        try:
            with open(p, "r", encoding="utf-8") as f:
                head = f.read(200)
            if "HealthData" in head:
                return p
        except Exception:
            continue
    return None


from data_processing import (
    _load_summary, _apply_gps_corrections_to_summary,
    _safe_float, _classify_type, _workout_distance, _load_manual_merges,
    _detect_brick_sessions,
)


router = APIRouter()


def _wnum(w: dict) -> int:
    return int(w.get("workout_num", 0))


def _workout_summary(w: dict) -> dict:
    return {
        "workout_num": _wnum(w),
        "type": w.get("type", ""),
        "duration_min": round(_safe_float(w.get("duration_min")), 1),
        "distance_km": _workout_distance(w),
        "start_time": w.get("startDate", "")[:19],
        "end_time": w.get("endDate", "")[:19],
        "tz": w.get("meta_TimeZone", ""),
    }


@router.get("/api/pick-folder")
async def pick_folder():
    """Open native macOS picker that accepts both folders and .zip files."""
    script = '''
tell application "System Events"
    activate
end tell
set chosenPath to choose folder with prompt "Select Apple Health Export folder"
return POSIX path of chosenPath
'''
    try:
        result = await asyncio.wait_for(
            asyncio.create_subprocess_exec(
                "osascript", "-e", script,
                stdout=asyncio.subprocess.PIPE,
                stderr=asyncio.subprocess.PIPE,
            ),
            timeout=120,
        )
        stdout, stderr = await result.communicate()
        if result.returncode == 0:
            folder = stdout.decode().strip()
            return {"path": folder}
        return {"path": "", "error": "Cancelled"}
    except asyncio.TimeoutError:
        return {"path": "", "error": "Timed out"}
    except Exception as e:
        return {"path": "", "error": str(e)}


@router.post("/api/import/upload")
async def import_upload(request: Request):
    """Import Apple Health data from an uploaded zip file (drag-and-drop)."""
    import zipfile
    import tempfile
    form = await request.form()
    upload = form.get("file")
    if not upload:
        raise HTTPException(400, "No file uploaded")
    fname = upload.filename or ""
    if not fname.lower().endswith(".zip"):
        raise HTTPException(400, "Only .zip files supported")

    # Save to temp dir and extract
    tmp_dir = Path(tempfile.mkdtemp(prefix="ironcoach_import_"))
    zip_path = tmp_dir / fname
    try:
        content = await upload.read()
        zip_path.write_bytes(content)
        with zipfile.ZipFile(zip_path, "r") as zf:
            for member in zf.namelist():
                if ".." in member or member.startswith("/"):
                    shutil.rmtree(tmp_dir, ignore_errors=True)
                    raise HTTPException(400, "Invalid zip file structure")
            zf.extractall(tmp_dir)
        zip_path.unlink()

        # Find the export XML (may be nested like apple_health_export/)
        # Supports English (export.xml) and Hebrew (ייצוא.xml)
        export_xml = _find_export_xml(tmp_dir)
        if not export_xml:
            shutil.rmtree(tmp_dir, ignore_errors=True)
            raise HTTPException(400, "No export.xml found in the zip file")
        folder_path = str(export_xml.parent)
        logger.info(f"Upload import: extracted to {folder_path}")
    except zipfile.BadZipFile:
        shutil.rmtree(tmp_dir, ignore_errors=True)
        raise HTTPException(400, "Invalid zip file")

    force = str(form.get("force", "")).lower() == "true"
    result = await _do_import(request, folder_path, force=force)

    # Clean up temp dir after import
    shutil.rmtree(tmp_dir, ignore_errors=True)
    return result


@router.post("/api/import")
async def import_data(request: Request):
    """Import new Apple Health export data from a folder path."""
    data = await request.json()
    folder_path = data.get("folder_path", "").strip()
    force_rebuild = data.get("force", False)
    logger.info(f"Data import requested: {folder_path} (force={force_rebuild})")

    if not folder_path:
        raise HTTPException(400, "folder_path is required")

    # If user selected a .zip via Browse, extract it first
    fp = Path(folder_path)
    if fp.is_file() and fp.suffix.lower() == ".zip":
        import zipfile
        import tempfile
        tmp_dir = Path(tempfile.mkdtemp(prefix="ironcoach_import_"))
        try:
            with zipfile.ZipFile(fp, "r") as zf:
                for member in zf.namelist():
                    if ".." in member or member.startswith("/"):
                        shutil.rmtree(tmp_dir, ignore_errors=True)
                        raise HTTPException(400, "Invalid zip file structure")
                zf.extractall(tmp_dir)
        except zipfile.BadZipFile:
            shutil.rmtree(tmp_dir, ignore_errors=True)
            raise HTTPException(400, "Invalid zip file")
        # Find export XML inside extracted content (English or Hebrew)
        export_xml = _find_export_xml(tmp_dir)
        if not export_xml:
            shutil.rmtree(tmp_dir, ignore_errors=True)
            raise HTTPException(400, "No export.xml found in the zip file")
        folder_path = str(export_xml.parent)
        logger.info(f"Browse zip: extracted to {folder_path}")
        result = await _do_import(request, folder_path, force=force_rebuild)
        shutil.rmtree(tmp_dir, ignore_errors=True)
        return result

    return await _do_import(request, folder_path, force=force_rebuild)


async def _do_import(request: Request, folder_path: str, force: bool = False):
    """Shared import logic used by both path-based and upload-based import."""
    import_path = Path(folder_path)
    if not import_path.is_dir():
        raise HTTPException(400, "Invalid folder path")
    # Find export XML — supports English, Hebrew, and mojibake variants
    source_xml = _find_export_xml(import_path)
    if not source_xml:
        raise HTTPException(400, "No export.xml found in the specified folder")

    source = import_path
    uid = _uid(request)
    dd = _user_data_dir(request)
    dd.mkdir(parents=True, exist_ok=True)

    _import_start = time.time()
    task_id = f"import-data-{uid}"
    await _register_task(task_id, "Data Import", "/")

    # Copy export.xml into per-user dir (not symlink — source may be temp dir)
    dest_xml = dd / "export.xml"
    if dest_xml.exists():
        dest_xml.unlink()
    logger.info(f"Copying export.xml ({source_xml.stat().st_size / 1024 / 1024:.0f}MB) to {dest_xml}")
    shutil.copy2(str(source_xml), str(dest_xml))

    # Copy workout-routes folder into per-user dir (needed for GPX data)
    source_routes = source / "workout-routes"
    dest_routes = dd / "workout-routes"
    if source_routes.exists():
        if dest_routes.exists():
            shutil.rmtree(dest_routes)
        shutil.copytree(str(source_routes), str(dest_routes))

    # Run the export_to_csv.py script
    script = PROJECT_ROOT / "scripts" / "export_to_csv.py"
    if not script.exists():
        raise HTTPException(500, "scripts/export_to_csv.py not found")

    env = {**os.environ}
    env["IRONCOACH_OUT_DIR"] = str(dd)

    cmd = [sys.executable, str(script)]
    if force:
        cmd.append("--force")
    proc = await asyncio.create_subprocess_exec(
        *cmd,
        stdout=asyncio.subprocess.PIPE,
        stderr=asyncio.subprocess.PIPE,
        cwd=str(PROJECT_ROOT),
        env=env,
    )
    try:
        stdout, stderr = await asyncio.wait_for(proc.communicate(), timeout=600)
    except asyncio.TimeoutError:
        proc.kill()
        await _unregister_task(task_id)
        raise HTTPException(504, "Import timed out after 10 minutes")

    success = proc.returncode == 0
    import_output = stdout.decode("utf-8", errors="replace")
    import_errors = stderr.decode("utf-8", errors="replace")

    if success:
        logger.info(f"Import success (user={uid}, dir={dd}): {import_output[:200]}")
    else:
        logger.error(f"Import failed (user={uid}): {import_errors[:300]}")

    # Apply GPS corrections to any new workouts in summary
    if success:
        _apply_gps_corrections_to_summary(dd)

    # Generate pre-computed sections for new workouts
    sections_generated = 0
    if success:
        try:
            from data_processing.workout_analysis import _generate_all_sections
            sections_generated = _generate_all_sections(dd)
            if sections_generated:
                logger.info(f"Generated {sections_generated} pre-computed .sections.json files")
        except Exception as e:
            logger.warning(f"Failed to generate pre-computed sections: {e}")

    # Load summary ONCE — reused for stale cleanup, merge candidates, and new workout detection
    all_workouts = []
    if success:
        try:
            all_workouts = _load_summary(dd)
        except Exception as e:
            logger.warning(f"Failed to load summary after import: {e}")

    # Auto-update HR settings from imported data (if not locked by user)
    if success and all_workouts:
        try:
            from data_processing.hr_zones import (
                detect_hr_max_from_workouts, detect_hr_rest_from_recovery,
                compute_default_hr_lthr, compute_zones_from_hr,
                compute_default_hr_max, compute_default_hr_rest,
                zone_boundaries, _age_from_profile,
            )
            from data_processing import _load_recovery_data
            conn_hr = await db.get_db()
            try:
                current_hr = await db.hr_settings_get(conn_hr, uid)
                if not current_hr or not current_hr.get("locked"):
                    det_max = detect_hr_max_from_workouts(all_workouts)
                    recovery_raw = _load_recovery_data(dd)
                    det_rest = detect_hr_rest_from_recovery(recovery_raw)
                    if det_max or det_rest:
                        # Fall back to profile-calculated values for missing detections
                        profile = await db.user_get_profile(conn_hr, uid)
                        age = _age_from_profile(profile)
                        sex = (profile or {}).get("sex", "male")
                        hr_max = det_max or compute_default_hr_max(age, sex)
                        hr_rest = det_rest or compute_default_hr_rest(sex)
                        hr_lthr = compute_default_hr_lthr(hr_max)
                        zones = compute_zones_from_hr(hr_max, hr_rest)
                        from datetime import timezone as tz
                        await db.hr_settings_upsert(conn_hr, uid, {
                            "hr_max": hr_max, "hr_rest": hr_rest, "hr_lthr": hr_lthr,
                            **zone_boundaries(zones),
                            "locked": 0,
                            "source": "apple_health",
                            "updated_at": datetime.now(tz=tz.utc).isoformat(),
                        })
                        logger.info(f"Auto-updated HR settings for user {uid}: max={hr_max}, rest={hr_rest}")
            finally:
                await conn_hr.close()
        except Exception as e:
            logger.warning(f"Failed to auto-update HR settings: {e}")

    # Clean up stale insights (workout_num no longer matches date/type after renumbering)
    stale_cleaned = 0
    has_new = success and "No new workouts found" not in import_output
    existing_nums = set()
    dismissed_nums = set()
    if success and all_workouts:
        try:
            wmap = {_wnum(w): w for w in all_workouts}
            conn = await db.get_db()
            try:
                all_insights = await db.insight_get_all(conn, user_id=uid)
                stale_nums = []
                for ins in all_insights:
                    inum = ins.get("workout_num")
                    idate = ins.get("workout_date", "")
                    itype = ins.get("workout_type", "")
                    w = wmap.get(inum)
                    if not w:
                        stale_nums.append(inum)
                    elif w.get("startDate", "")[:10] != idate or w.get("type", "") != itype:
                        stale_nums.append(inum)
                if stale_nums:
                    stale_cleaned = await db.insight_delete_many(conn, stale_nums, user_id=uid)
                    logger.info(f"Cleaned {stale_cleaned} stale insights after import: {stale_nums}")

                # Load existing insight nums once — reused for merge candidates and new workouts
                existing_nums = await db.insight_get_existing_nums(conn, user_id=uid) if has_new else set()

                # Load dismissed workout nums (user skipped insight generation for these before)
                if has_new:
                    val = await db.setting_get(conn, f"dismissed_insights_{uid}", "[]")
                    dismissed_nums = set(json.loads(val))
            finally:
                await conn.close()
        except Exception as e:
            logger.warning(f"Failed to clean stale insights: {e}")

    # Build set of genuinely new workout nums (no insight, not dismissed)
    # Used for both merge candidate filtering and new_workouts response
    resolved_nums = existing_nums | dismissed_nums
    new_nums = set()
    if has_new and all_workouts:
        for w in all_workouts:
            wn = _wnum(w)
            wdate = w.get("startDate", "")[:10]
            if wdate >= INSIGHT_CUTOFF_DATE and wn not in resolved_nums:
                new_nums.add(wn)
    logger.info(f"Import post-process: has_new={has_new}, all_workouts={len(all_workouts)}, "
                f"new_nums={len(new_nums)}, existing={len(existing_nums)}, dismissed={len(dismissed_nums)}")

    # Detect merge candidates: same discipline, 10-30 min gap (not auto-merged but likely same session)
    # Only when there are genuinely new workouts — re-importing the same file shouldn't show stale candidates
    merge_candidates = []
    if new_nums and all_workouts:
        try:
            sorted_all = sorted(all_workouts, key=lambda w: w.get("startDate", ""))
            already_merged = _load_manual_merges(uid)
            for i in range(1, len(sorted_all)):
                prev, curr = sorted_all[i - 1], sorted_all[i]
                prev_num = _wnum(prev)
                curr_num = _wnum(curr)
                if prev_num not in new_nums and curr_num not in new_nums:
                    continue
                if (min(prev_num, curr_num), max(prev_num, curr_num)) in already_merged:
                    continue
                if prev.get("startDate", "")[:10] != curr.get("startDate", "")[:10]:
                    continue
                prev_disc = _classify_type(prev.get("type", ""))
                curr_disc = _classify_type(curr.get("type", ""))
                if prev_disc != curr_disc or prev_disc not in _MERGEABLE_DISCIPLINES:
                    continue
                # Must be exact same workout type (e.g. don't merge Running + Walking)
                if prev.get("type", "") != curr.get("type", ""):
                    continue
                try:
                    prev_end = datetime.strptime(prev.get("endDate", "")[:19], "%Y-%m-%d %H:%M:%S")
                    curr_start = datetime.strptime(curr.get("startDate", "")[:19], "%Y-%m-%d %H:%M:%S")
                    gap_min = (curr_start - prev_end).total_seconds() / 60
                except (ValueError, TypeError):
                    continue
                if _MERGE_CANDIDATE_GAP_MIN <= gap_min <= _MERGE_CANDIDATE_GAP_MAX:
                    prev_s = _workout_summary(prev)
                    curr_s = _workout_summary(curr)
                    merge_candidates.append({
                        "workout_a": prev_num,
                        "workout_b": curr_num,
                        "type": prev.get("type", ""),
                        "gap_min": round(gap_min, 1),
                        "date": prev.get("startDate", "")[:10],
                        **{f"a_{k}": v for k, v in prev_s.items() if k not in ("workout_num", "type")},
                        **{f"b_{k}": v for k, v in curr_s.items() if k not in ("workout_num", "type")},
                    })
        except Exception as e:
            logger.warning(f"Failed to detect merge candidates: {e}")

    # Check for new workouts that need insights (uses pre-computed new_nums)
    new_workouts = []
    if new_nums and all_workouts:
        try:
            for w in all_workouts:
                if _wnum(w) in new_nums:
                    s = _workout_summary(w)
                    s["date"] = w.get("startDate", "")[:10]
                    new_workouts.append(s)
            logger.info(f"Import found {len(new_workouts)} new workouts pending insights")
        except Exception as e:
            logger.warning(f"Failed to enumerate new workouts: {e}")

    # Detect brick sessions among new workouts (different disciplines, <30min gap)
    # Only include bricks where at least one member is genuinely new (no insight, not dismissed)
    brick_sessions = []
    if new_nums and all_workouts:
        try:
            bricks = _detect_brick_sessions(all_workouts)
            for b in bricks:
                brick_nums = {_wnum(bw) for bw in b["workouts"]}
                if not (brick_nums & new_nums):
                    continue
                sorted_bw = sorted(b["workouts"], key=lambda w: w.get("startDate", ""))
                brick_sessions.append({
                    "brick_type": b.get("brick_type", ""),
                    "transition_times": b.get("transition_times", []),
                    "date": sorted_bw[0].get("startDate", "")[:10],
                    "workouts": [_workout_summary(bw) for bw in sorted_bw],
                })
        except Exception as e:
            logger.warning(f"Failed to detect brick sessions: {e}")

    # Check which workout dates have nutrition data logged + persist pending import
    dates_with_nutrition = set()
    if new_workouts or merge_candidates or brick_sessions:
        try:
            conn = await db.get_db()
            try:
                if new_workouts:
                    workout_dates = list({w["date"] for w in new_workouts})
                    placeholders = ",".join("?" for _ in workout_dates)
                    cursor = await conn.execute(
                        f"SELECT DISTINCT date FROM nutrition_log WHERE user_id = ? AND date IN ({placeholders})",
                        (uid, *workout_dates)
                    )
                    dates_with_nutrition = {row["date"] for row in await cursor.fetchall()}

                existing_raw = await db.setting_get(conn, f"pending_import_{uid}", "")
                if existing_raw:
                    existing = json.loads(existing_raw)
                    pending_nums = {_wnum(w) for w in existing.get("workouts", [])}
                    # Append only truly new workouts (avoid duplicates from re-import)
                    for w in new_workouts:
                        if _wnum(w) not in pending_nums:
                            existing["workouts"].append(w)
                    existing["datesWithNutrition"] = list(set(existing.get("datesWithNutrition", [])) | dates_with_nutrition)
                    def _mc_key(mc):
                        a, b = mc.get("workout_a", 0), mc.get("workout_b", 0)
                        return (min(a, b), max(a, b))
                    existing_mc_keys = {_mc_key(mc) for mc in existing.get("mergeCandidates", [])}
                    for mc in merge_candidates:
                        if _mc_key(mc) not in existing_mc_keys:
                            existing["mergeCandidates"].append(mc)
                    # Merge brick sessions — also filter out stale bricks (all members dismissed/insighted)
                    def _bs_key(bs):
                        return tuple(sorted(_wnum(w) for w in bs.get("workouts", [])))
                    existing_bs_keys = {_bs_key(bs) for bs in existing.get("brickSessions", [])}
                    for bs in brick_sessions:
                        if _bs_key(bs) not in existing_bs_keys:
                            existing["brickSessions"].append(bs)
                    # Remove stale entries: workouts now dismissed/insighted, bricks fully resolved
                    existing["workouts"] = [
                        w for w in existing["workouts"]
                        if _wnum(w) not in resolved_nums
                    ]
                    existing["brickSessions"] = [
                        bs for bs in existing.get("brickSessions", [])
                        if not all(
                            _wnum(w) in resolved_nums
                            for w in bs.get("workouts", [])
                        )
                    ]
                    merged = existing
                else:
                    merged = {
                        "workouts": new_workouts,
                        "datesWithNutrition": list(dates_with_nutrition),
                        "mergeCandidates": merge_candidates,
                        "brickSessions": brick_sessions,
                    }
                await db.setting_set(conn, f"pending_import_{uid}", json.dumps(merged))
            finally:
                await conn.close()
        except Exception as e:
            logger.warning(f"Failed to persist pending import: {e}")

    await _unregister_task(task_id)

    # Save completion notification server-side (survives page refresh)
    elapsed = int(time.time() - _import_start)
    time_str = f"{elapsed // 60}m {elapsed % 60}s" if elapsed > 60 else f"{elapsed}s"
    notif_status = "done" if success else "error"
    notif_detail = f"Completed in {time_str}" if success else f"Failed in {time_str}"
    try:
        conn_n = await db.get_db()
        try:
            await db.notification_add(conn_n, "Data Import", notif_detail, status=notif_status, link="/", user_id=uid)
        finally:
            await conn_n.close()
        # Also update in-memory history
        from services.task_tracker import _insight_status, _insight_status_lock
        entry = {"label": "Data Import", "detail": notif_detail, "status": notif_status, "link": "/",
                 "finished_at": datetime.now().strftime("%Y-%m-%d %H:%M"), "user_id": uid}
        async with _insight_status_lock:
            _insight_status["history"].insert(0, entry)
            _insight_status["history"] = _insight_status["history"][:50]
    except Exception:
        pass

    return {
        "success": success,
        "output": import_output,
        "errors": import_errors,
        "new_workouts": new_workouts,
        "dates_with_nutrition": list(dates_with_nutrition),
        "merge_candidates": merge_candidates,
        "brick_sessions": brick_sessions,
        "stale_insights_cleaned": stale_cleaned,
    }


@router.get("/api/import/pending")
async def get_pending_import(request: Request):
    """Get pending import data for current user (survives browser cache clear)."""
    uid = _uid(request)
    conn = await db.get_db()
    try:
        val = await db.setting_get(conn, f"pending_import_{uid}", "")
        if val:
            return json.loads(val)
        return None
    finally:
        await conn.close()


@router.delete("/api/import/pending")
async def clear_pending_import(request: Request):
    """Clear pending import data (user completed or skipped insight generation)."""
    uid = _uid(request)
    conn = await db.get_db()
    try:
        await db.setting_set(conn, f"pending_import_{uid}", "")
    finally:
        await conn.close()
    return {"ok": True}
