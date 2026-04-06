"""Summary CSV loading and workout merging/brick detection."""

import csv
from datetime import datetime
from pathlib import Path

from config import TRAINING_DATA, _MERGEABLE_DISCIPLINES, _TRI_DISCIPLINES, _BRICK_LABEL_MAP, logger
from .helpers import _safe_float, _classify_type, _workout_distance, _load_manual_merges, _load_auto_merge_settings
from .workout_analysis import _load_workout_timeseries
from .gps import _detect_and_fix_gps

# Workout merging and brick detection constants
MIN_WORKOUTS_REQUIRED = 2
BRICK_MAX_GAP_MINUTES = 30

# mtime-based cache for _load_summary: {csv_path_str: (mtime, workouts_list)}
_summary_cache: dict[str, tuple[float, list]] = {}


def _load_summary(data_dir: Path = None):
    """Load all workouts from the summary CSV (cached by file mtime)."""
    csv_path = (data_dir or TRAINING_DATA) / "00_workouts_summary.csv"
    if not csv_path.exists():
        return []
    path_key = str(csv_path)
    try:
        mtime = csv_path.stat().st_mtime
    except OSError:
        return []
    cached = _summary_cache.get(path_key)
    if cached and cached[0] == mtime:
        # Return a shallow copy so callers can mutate dicts without poisoning cache
        return [dict(w) for w in cached[1]]
    workouts = []
    with open(csv_path, newline="") as f:
        reader = csv.DictReader(f)
        for row in reader:
            workouts.append(dict(row))
    _summary_cache[path_key] = (mtime, workouts)
    return [dict(w) for w in workouts]


def _apply_gps_corrections_to_summary(data_dir: Path = None):
    """Scan run/bike workouts with GPS and write corrected values into the summary CSV.

    Adds columns: gps_corrected, gps_original_elevation_cm, gps_corrected_distance_km, gps_anomaly_count.
    Original elevation stays in meta_ElevationAscended (renamed to gps_original_elevation_cm),
    and meta_ElevationAscended is overwritten with the corrected value.
    Skips workouts already processed (gps_corrected column non-empty).
    """
    csv_path = (data_dir or TRAINING_DATA) / "00_workouts_summary.csv"
    if not csv_path.exists():
        return

    with open(csv_path, newline="") as f:
        reader = csv.DictReader(f)
        fieldnames = list(reader.fieldnames)
        workouts = [dict(row) for row in reader]

    # Ensure new columns exist
    new_cols = ["gps_corrected", "gps_original_elevation_cm", "gps_corrected_distance_km", "gps_anomaly_count"]
    for col in new_cols:
        if col not in fieldnames:
            fieldnames.append(col)

    dirty = False
    for w in workouts:
        # Skip already processed
        if w.get("gps_corrected"):
            continue
        wnum = w.get("workout_num", "")
        if not wnum:
            continue
        disc = _classify_type(w.get("type", ""))
        if disc not in ("run", "bike"):
            continue
        if w.get("has_route") != "yes":
            continue

        # Load time-series and detect anomalies
        ts_data = _load_workout_timeseries(int(wnum), data_dir)
        if not ts_data or not ts_data["data"]:
            w["gps_corrected"] = "none"  # no data to check
            dirty = True
            continue

        rows = [dict(r) for r in ts_data["data"]]
        result = _detect_and_fix_gps(rows, w.get("type", ""))

        if result.get("corrected_count", 0) > 0:
            # Save original elevation before overwriting
            w["gps_original_elevation_cm"] = w.get("meta_ElevationAscended", "")
            w["gps_corrected_distance_km"] = str(result["corrected_gps_distance_km"])
            w["gps_anomaly_count"] = str(len(result.get("anomalies", [])))
            # Overwrite elevation with corrected value (stored in cm)
            w["meta_ElevationAscended"] = str(round(result["corrected_elevation_m"] * 100))
            w["gps_corrected"] = "yes"
        else:
            w["gps_corrected"] = "clean"  # checked, no issues
        dirty = True

    if dirty:
        with open(csv_path, "w", newline="") as f:
            writer = csv.DictWriter(f, fieldnames=fieldnames)
            writer.writeheader()
            writer.writerows(workouts)
        logger.debug("GPS corrections applied to summary CSV")


def _merge_nearby_workouts(workouts: list, user_id: int = 1) -> list:
    """Merge consecutive workouts of the same discipline that are close in time.

    Rules:
    - Same discipline (run/bike/swim only, not strength/other)
    - Auto-merge: gap < configured minutes (default 10)
    - Manual merge: user-approved pairs from settings (any gap)
    - Merged workout sums duration, distance, calories; keeps earlier start and later end
    """
    if len(workouts) < MIN_WORKOUTS_REQUIRED:
        return workouts

    auto_enabled, auto_gap = _load_auto_merge_settings()
    manual_merges = _load_manual_merges(user_id)

    if auto_enabled and auto_gap >= BRICK_MAX_GAP_MINUTES:
        logger.warning(f"auto_merge_gap ({auto_gap}) >= brick gap ({BRICK_MAX_GAP_MINUTES}), capping")
        auto_gap = BRICK_MAX_GAP_MINUTES - 5

    if not auto_enabled and not manual_merges:
        return workouts

    # Sort by startDate
    sorted_w = sorted(workouts, key=lambda w: w.get("startDate", ""))
    merged = [sorted_w[0]]

    for w in sorted_w[1:]:
        prev = merged[-1]
        prev_disc = _classify_type(prev.get("type", ""))
        curr_disc = _classify_type(w.get("type", ""))

        # Only merge same discipline in run/bike/swim
        if prev_disc != curr_disc or prev_disc not in _MERGEABLE_DISCIPLINES:
            merged.append(w)
            continue

        # Check time gap
        try:
            prev_end = datetime.strptime(prev.get("endDate", "")[:19], "%Y-%m-%d %H:%M:%S")
            curr_start = datetime.strptime(w.get("startDate", "")[:19], "%Y-%m-%d %H:%M:%S")
            gap_min = (curr_start - prev_end).total_seconds() / 60
        except (ValueError, TypeError):
            merged.append(w)
            continue

        # Check if this is a user-approved manual merge
        prev_num = int(prev.get("workout_num", 0))
        curr_num = int(w.get("workout_num", 0))
        is_manual = (min(prev_num, curr_num), max(prev_num, curr_num)) in manual_merges

        if not is_manual and (not auto_enabled or gap_min < 0 or gap_min > auto_gap):
            merged.append(w)
            continue

        # Merge: combine into prev
        logger.debug(f"Merging workout #{w.get('workout_num')} into #{prev.get('workout_num')} "
                     f"(same {prev_disc}, gap={gap_min:.1f}min)")

        # Capture original durations BEFORE summing for weighted averages
        orig_prev_dur = _safe_float(prev.get("duration_min"))
        orig_curr_dur = _safe_float(w.get("duration_min"))
        orig_total_dur = orig_prev_dur + orig_curr_dur

        # Update metadata
        prev["endDate"] = w.get("endDate", prev["endDate"])
        prev["duration_min"] = str(orig_total_dur)

        # Track merged workout numbers for data combination
        if "merged_nums" not in prev:
            prev["merged_nums"] = [prev.get("workout_num")]
        prev["merged_nums"].append(w.get("workout_num"))

        # Single pass over keys for sum/max/min (cache list once)
        prev_keys = list(prev.keys())
        for key in prev_keys:
            if key.endswith("_sum"):
                prev[key] = str(_safe_float(prev.get(key)) + _safe_float(w.get(key)))
            elif key.endswith("_maximum"):
                prev[key] = str(max(_safe_float(prev.get(key)), _safe_float(w.get(key))))
            elif key.endswith("_minimum"):
                val_prev = _safe_float(prev.get(key))
                val_curr = _safe_float(w.get(key))
                if val_prev > 0 and val_curr > 0:
                    prev[key] = str(min(val_prev, val_curr))
                elif val_curr > 0:
                    prev[key] = w.get(key)
            elif key.endswith("_average") and orig_total_dur > 0:
                prev_avg = _safe_float(prev.get(key))
                curr_avg = _safe_float(w.get(key))
                if prev_avg > 0 and curr_avg > 0:
                    prev[key] = str((prev_avg * orig_prev_dur + curr_avg * orig_curr_dur) / orig_total_dur)

        for key in ("meta_ElevationAscended",):
            v1 = _safe_float(prev.get(key))
            v2 = _safe_float(w.get(key))
            if v1 or v2:
                prev[key] = str(v1 + v2)

    return merged


def _detect_brick_sessions(workouts: list) -> list:
    """Detect brick (multi-discipline) sessions from workout list.

    A brick is 2+ consecutive workouts of DIFFERENT disciplines within
    30 minutes gap.  Must include at least one triathlon discipline
    (swim/bike/run) to qualify — pure strength/other combos are excluded.
    """
    if len(workouts) < MIN_WORKOUTS_REQUIRED:
        return []

    # Sort by startDate
    sorted_w = sorted(workouts, key=lambda w: w.get("startDate", ""))

    # Enrich each workout with discipline + distance
    for w in sorted_w:
        if "discipline" not in w:
            w["discipline"] = _classify_type(w.get("type", ""))
        if "distance_km" not in w:
            w["distance_km"] = _workout_distance(w)

    # Build candidate groups: consecutive workouts within 30-min gap
    # with different disciplines
    groups = []
    current_group = [sorted_w[0]]

    for w in sorted_w[1:]:
        prev = current_group[-1]
        prev_disc = prev["discipline"]
        curr_disc = w["discipline"]

        # Must be different disciplines
        if prev_disc == curr_disc:
            if len(current_group) >= 2:
                groups.append(current_group)
            current_group = [w]
            continue

        # Check time gap
        try:
            prev_end = datetime.strptime(prev.get("endDate", "")[:19], "%Y-%m-%d %H:%M:%S")
            curr_start = datetime.strptime(w.get("startDate", "")[:19], "%Y-%m-%d %H:%M:%S")
            gap_min = (curr_start - prev_end).total_seconds() / 60
        except (ValueError, TypeError):
            if len(current_group) >= 2:
                groups.append(current_group)
            current_group = [w]
            continue

        if gap_min < 0 or gap_min > BRICK_MAX_GAP_MINUTES:
            if len(current_group) >= MIN_WORKOUTS_REQUIRED:
                groups.append(current_group)
            current_group = [w]
            continue

        current_group.append(w)

    # Don't forget the last group
    if len(current_group) >= MIN_WORKOUTS_REQUIRED:
        groups.append(current_group)

    # Filter: only swim/bike/run workouts qualify for bricks
    bricks = []
    brick_id = 1
    for group in groups:
        tri_only = [w for w in group if w["discipline"] in _TRI_DISCIPLINES]
        if len(tri_only) < MIN_WORKOUTS_REQUIRED:
            continue
        group = tri_only
        disciplines = [w["discipline"] for w in group]
        if len(set(disciplines)) < 2:
            continue

        # Compute transition times (minutes between consecutive workouts)
        transitions = []
        for i in range(1, len(group)):
            try:
                prev_end = datetime.strptime(group[i - 1].get("endDate", "")[:19], "%Y-%m-%d %H:%M:%S")
                curr_start = datetime.strptime(group[i].get("startDate", "")[:19], "%Y-%m-%d %H:%M:%S")
                transitions.append(round((curr_start - prev_end).total_seconds() / 60, 1))
            except (ValueError, TypeError):
                transitions.append(None)

        # Build workout summaries
        workout_summaries = []
        total_duration = 0.0
        total_distance = 0.0
        total_calories = 0.0
        for w in group:
            dur = _safe_float(w.get("duration_min"))
            dist = _safe_float(w.get("distance_km")) if "distance_km" in w else _workout_distance(w)
            cal = _safe_float(w.get("ActiveEnergyBurned_sum"))
            total_duration += dur
            total_distance += dist
            total_calories += cal
            workout_summaries.append({
                "workout_num": int(w.get("workout_num", 0)),
                "type": w.get("type", ""),
                "discipline": w["discipline"],
                "duration_min": round(dur, 1),
                "distance_km": round(dist, 2),
                "hr_avg": _safe_float(w.get("HeartRate_average")),
                "hr_max": _safe_float(w.get("HeartRate_maximum")),
                "calories": round(cal),
                "startDate": w.get("startDate", ""),
                "endDate": w.get("endDate", ""),
            })

        # Build label like "Bike -> Run"
        labels = [_BRICK_LABEL_MAP.get(d, d.title()) for d in disciplines]
        brick_type = " → ".join(labels)

        bricks.append({
            "brick_id": brick_id,
            "date": group[0].get("startDate", "")[:10],
            "workouts": workout_summaries,
            "disciplines": disciplines,
            "total_duration_min": round(total_duration, 1),
            "total_distance_km": round(total_distance, 2),
            "total_calories": round(total_calories),
            "transition_times": transitions,
            "brick_type": brick_type,
        })
        brick_id += 1

    return bricks
