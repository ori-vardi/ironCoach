"""Workout data endpoints — summary, time-series, sections, bricks, stats, recovery."""

import asyncio
from collections import defaultdict
from datetime import datetime, timedelta

from fastapi import APIRouter, HTTPException, Query, Request

from config import logger
from data_processing import (
    _load_summary,
    _merge_nearby_workouts,
    _filter_hidden,
    _classify_type,
    _enrich_workouts,
    _workout_distance,
    _compute_sections,
    _load_precomputed_sections,
    _load_gps_segments,
    _load_workout_timeseries,
    _detect_and_fix_gps,
    _detect_brick_sessions,
    _safe_float,
    _compute_recovery_timeline,
    _recovery_label,
    _load_vo2max_history,
    _load_recovery_data,
    _compute_trimp,
    _compute_hrtss,
    _training_phase,
    _compute_risk_alerts,
    _compute_readiness_score,
    _compute_weekly_load_change,
)
from data_processing.helpers import _extract_vo2max
import database as db
from routes.deps import _uid, _user_data_dir
from services.weather import _get_first_gps, _fetch_external_weather

router = APIRouter()

STALE_WORKOUT_DAYS = 7
DEFAULT_RECOVERY_WEEKS = 8



@router.get("/api/workout/{num}/sections")
async def get_workout_sections(num: int, request: Request):
    dd = _user_data_dir(request)
    # Fast path: load pre-computed sections + GPS segments from disk
    precomputed = _load_precomputed_sections(num, dd)
    if precomputed:
        gps_segments = _load_gps_segments(num, dd)
        if gps_segments is not None:
            precomputed["hr_colored_segments"] = gps_segments
        else:
            precomputed["hr_colored_segments"] = []
        return precomputed
    # Fallback: compute from raw data
    result = _compute_sections(num, dd, force_full=True)
    if result is None:
        raise HTTPException(404, f"No section data for workout {num}")
    return result


@router.get("/api/summary")
async def get_summary(request: Request, show_hidden: bool = False,
                      limit: int = Query(default=None, ge=1, le=500),
                      offset: int = Query(default=0, ge=0)):
    dd = _user_data_dir(request)
    uid = _uid(request)
    loop = asyncio.get_event_loop()
    workouts = _enrich_workouts(_load_summary(dd))
    workouts = await loop.run_in_executor(None, _merge_nearby_workouts, workouts, uid)
    if not show_hidden:
        workouts = await loop.run_in_executor(None, _filter_hidden, workouts, uid)
    if limit is not None or offset:
        total = len(workouts)
        if offset:
            workouts = workouts[offset:]
        if limit is not None:
            workouts = workouts[:limit]
        return {"total": total, "data": workouts}
    return workouts


@router.get("/api/bricks")
async def get_bricks(request: Request, from_date: str = "", to_date: str = ""):
    """Detect brick (multi-discipline back-to-back) sessions."""
    dd = _user_data_dir(request)
    loop = asyncio.get_event_loop()
    workouts = await loop.run_in_executor(None, _filter_hidden, _enrich_workouts(_load_summary(dd)), _uid(request))

    # Optional date filtering on the raw workouts before detection
    if from_date:
        workouts = [w for w in workouts if w.get("startDate", "")[:10] >= from_date]
    if to_date:
        workouts = [w for w in workouts if w.get("startDate", "")[:10] <= to_date]

    bricks = _detect_brick_sessions(workouts)
    # Return newest first
    bricks.sort(key=lambda b: b["date"], reverse=True)
    # Re-number brick_ids after sort (so 1 = most recent)
    for i, b in enumerate(bricks, 1):
        b["brick_id"] = i
    return bricks


@router.get("/api/workout/{num}")
async def get_workout(num: int, request: Request, merge_with: str = ""):
    """Load workout time-series. If merge_with is given (comma-separated nums),
    concatenate data from all listed workouts."""
    dd = _user_data_dir(request)
    nums = [num]
    if merge_with:
        try:
            nums = [int(n) for n in merge_with.split(",")]
        except ValueError:
            pass

    all_data = []
    columns = []
    metadata = []
    filenames = []
    for n in nums:
        ts = _load_workout_timeseries(n, dd)
        if ts:
            all_data.extend(ts["data"])
            if not columns:
                columns = ts["columns"]
            metadata.extend(ts["metadata"])
            filenames.append(ts["filename"])

    if not all_data:
        raise HTTPException(404, f"Workout {num} not found")

    # Load summary once for both GPS and weather
    summary = _load_summary(dd)
    w_sum = next((w for w in summary if int(w.get("workout_num", 0)) == num), None)
    workout_type = w_sum.get("type", "") if w_sum else ""

    # Detect and fix GPS anomalies
    gps_info = _detect_and_fix_gps(all_data, workout_type)

    vo2max = _extract_vo2max(all_data) if "VO2Max" in columns else None

    # Compute training load metrics
    trimp = None
    hrtss = None
    if w_sum:
        trimp = round(_compute_trimp(w_sum), 1)
        hrtss_val = _compute_hrtss(w_sum)
        if hrtss_val is not None:
            hrtss = round(hrtss_val, 1)

    result = {
        "filename": " + ".join(filenames),
        "metadata": metadata,
        "columns": columns,
        "data": all_data,
        "point_count": len(all_data),
    }
    if trimp is not None:
        result["trimp"] = trimp
    if hrtss is not None:
        result["hrtss"] = hrtss
    if vo2max is not None:
        result["vo2max"] = vo2max
    if gps_info.get("corrected_count", 0) > 0:
        result["gps_corrections"] = gps_info

    # Fetch external weather (wind, rain) for outdoor workouts with GPS
    try:
        if w_sum and str(w_sum.get("meta_IndoorWorkout", "")).strip() != "1":
            gps = _get_first_gps(num, dd)
            if gps:
                wdate = w_sum.get("startDate", "")[:10]
                start_hour = int(w_sum.get("startDate", "T12:")[11:13] or 12)
                ext = await _fetch_external_weather(gps[0], gps[1], wdate, start_hour)
                if ext:
                    result["external_weather"] = ext
    except Exception as e:
        logger.debug("External weather fetch error for workout %d: %s", num, e)

    return result


@router.get("/api/workouts/by-type/{workout_type}")
async def get_workouts_by_type(workout_type: str, request: Request, show_hidden: bool = False):
    loop = asyncio.get_event_loop()
    uid = _uid(request)
    workouts = _enrich_workouts(_load_summary(_user_data_dir(request)))
    workouts = await loop.run_in_executor(None, _merge_nearby_workouts, workouts, uid)
    if not show_hidden:
        workouts = await loop.run_in_executor(None, _filter_hidden, workouts, uid)
    return [w for w in workouts if w.get("discipline") == workout_type]


@router.get("/api/stats/weekly")
async def get_weekly_stats(request: Request):
    uid = _uid(request)
    loop = asyncio.get_event_loop()
    workouts = _enrich_workouts(_load_summary(_user_data_dir(request)))
    workouts = await loop.run_in_executor(None, _merge_nearby_workouts, workouts, uid)
    workouts = await loop.run_in_executor(None, _filter_hidden, workouts, uid)
    weeks = defaultdict(lambda: {
        "swim_min": 0, "bike_min": 0, "run_min": 0, "strength_min": 0, "other_min": 0,
        "swim_km": 0, "bike_km": 0, "run_km": 0,
        "count": 0,
    })
    for w in workouts:
        start_str = w.get("startDate", "")[:10]
        if not start_str:
            continue
        try:
            dt = datetime.strptime(start_str, "%Y-%m-%d")
        except ValueError:
            continue
        year, week, _ = dt.isocalendar()
        key = f"{year}-W{week:02d}"
        disc = w.get("discipline") or _classify_type(w.get("type", ""))
        dur = _safe_float(w.get("duration_min"))
        dist = w.get("distance_km") if "distance_km" in w else _workout_distance(w)

        weeks[key][f"{disc}_min"] = weeks[key].get(f"{disc}_min", 0) + dur
        if disc in ("swim", "bike", "run"):
            weeks[key][f"{disc}_km"] += dist
        weeks[key]["count"] += 1

    # Convert to sorted list
    result = []
    for wk in sorted(weeks.keys()):
        entry = {"week": wk}
        entry.update(weeks[wk])
        result.append(entry)
    return result


@router.get("/api/recovery")
async def get_recovery(request: Request, from_date: str = "", to_date: str = ""):
    dd = _user_data_dir(request)
    uid = _uid(request)
    loop = asyncio.get_event_loop()
    workouts = _load_summary(dd)
    workouts = await loop.run_in_executor(None, _filter_hidden, workouts, uid)
    from routes.deps import _load_user_hr
    hr = await _load_user_hr(uid)
    result = _compute_recovery_timeline(workouts, hr_rest=hr["hr_rest"], hr_max=hr["hr_max"], hr_lthr=hr["hr_lthr"])
    if not result["timeline"]:
        return {"current": None, "timeline": [], "per_workout": {}, "disciplines": {}, "recovery_data": []}

    # Current state = last timeline entry
    last = result["timeline"][-1]
    label, color = _recovery_label(last["recovery"])

    # Staleness warning: if no workout in 7+ days, recovery % is misleading
    days_since_last_workout = 0
    for t in reversed(result["timeline"]):
        if t["day_trimp"] > 0:
            days_since_last_workout = (datetime.now().date() - datetime.strptime(t["date"], "%Y-%m-%d").date()).days
            break
    stale = None
    if days_since_last_workout >= STALE_WORKOUT_DAYS:
        stale = {
            "days": days_since_last_workout,
            "message": f"No workouts in {days_since_last_workout} days. Recovery is high but fitness is declining.",
        }

    current = {
        "recovery": last["recovery"],
        "fatigue": last["fatigue"],
        "fitness": last["fitness"],
        "label": label,
        "color": color,
        "stale": stale,
    }

    # Filter timeline to date window (default: last 8 weeks)
    if not to_date:
        to_date = datetime.now().strftime("%Y-%m-%d")
    if not from_date:
        from_date = (datetime.now() - timedelta(weeks=DEFAULT_RECOVERY_WEEKS)).strftime("%Y-%m-%d")

    filtered = [t for t in result["timeline"] if from_date <= t["date"] <= to_date]

    # Pre-classify workout types once, then group by discipline
    workout_discs = {id(w): _classify_type(w.get("type", "")) for w in workouts}
    disciplines = {}
    for disc in ["swim", "bike", "run", "strength"]:
        disc_workouts = [w for w in workouts if workout_discs[id(w)] == disc]
        if not disc_workouts:
            disciplines[disc] = {"last_workout": None, "days_since": None, "week_count": 0, "week_duration": 0, "week_distance": 0}
            continue
        disc_workouts.sort(key=lambda w: w.get("startDate", ""), reverse=True)
        last_date_str = disc_workouts[0].get("startDate", "")[:10]
        try:
            days_since = (datetime.now().date() - datetime.strptime(last_date_str, "%Y-%m-%d").date()).days
        except ValueError:
            days_since = None

        week_ago = (datetime.now() - timedelta(days=7)).strftime("%Y-%m-%d")
        week_workouts = [w for w in disc_workouts if w.get("startDate", "")[:10] >= week_ago]
        week_count = len(week_workouts)
        week_duration = sum(_safe_float(w.get("duration_min")) for w in week_workouts)
        week_distance = sum(_workout_distance(w) for w in week_workouts)

        disciplines[disc] = {
            "last_workout": last_date_str,
            "days_since": days_since,
            "week_count": week_count,
            "week_duration": round(week_duration),
            "week_distance": round(week_distance, 1),
        }

    # Recovery data (sleep, RHR, HRV)
    recovery_raw = _load_recovery_data(dd)
    recovery_data = []
    for d in sorted(recovery_raw.keys()):
        if d < from_date or d > to_date:
            continue
        r = recovery_raw[d]
        entry = {"date": d}
        if r.get("resting_hr"):
            entry["resting_hr"] = _safe_float(r["resting_hr"])
        if r.get("hrv_sdnn_ms"):
            entry["hrv_ms"] = _safe_float(r["hrv_sdnn_ms"])
        if r.get("sleep_total_min"):
            entry["sleep_total"] = _safe_float(r["sleep_total_min"])
            entry["sleep_deep"] = _safe_float(r.get("sleep_deep_min", 0))
            entry["sleep_core"] = _safe_float(r.get("sleep_core_min", 0))
            entry["sleep_rem"] = _safe_float(r.get("sleep_rem_min", 0))
            entry["sleep_awake"] = _safe_float(r.get("sleep_awake_min", 0))
        recovery_data.append(entry)

    # VO2Max data (pass already-loaded summary to avoid redundant CSV parse)
    vo2max_data = _load_vo2max_history(dd, summary=workouts)
    vo2max_filtered = [v for v in vo2max_data if from_date <= v["date"] <= to_date]

    # Training phase from primary event
    phase = None
    try:
        conn = await db.get_db()
        try:
            events = await db.events_get_all(conn, _uid(request))
        finally:
            await conn.close()
    except Exception:
        events = []
    if events:
        primary = next((e for e in events if e.get("is_primary")), None)
        if primary and primary.get("event_date"):
            try:
                race_date = datetime.strptime(primary["event_date"], "%Y-%m-%d").date()
                days_to_race = (race_date - datetime.now().date()).days
                if days_to_race >= 0:
                    phase = _training_phase(days_to_race)
            except ValueError:
                pass

    # Risk alerts
    risk_alerts = _compute_risk_alerts(filtered, recovery_data, phase)

    # Readiness score
    readiness = None
    if current:
        readiness = _compute_readiness_score(
            current["recovery"], current["fatigue"], current["fitness"],
            recovery_data, datetime.now().strftime("%Y-%m-%d")
        )

    # Weekly load change
    weekly_load = _compute_weekly_load_change(filtered)

    response = {
        "current": current,
        "timeline": filtered,
        "per_workout": result["per_workout"],
        "disciplines": disciplines,
        "recovery_data": recovery_data,
        "vo2max": vo2max_filtered,
        "risk_alerts": risk_alerts,
    }
    if readiness:
        response["readiness"] = readiness
    if weekly_load:
        response["weekly_load"] = weekly_load
    if phase:
        response["phase"] = phase
    return response
