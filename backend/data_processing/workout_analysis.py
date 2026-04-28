"""Workout time-series analysis and per-section computation."""

import csv
import json
import math
import re
import xml.etree.ElementTree as ET
from bisect import bisect_left
from collections import Counter
from datetime import datetime, timedelta, timezone
from pathlib import Path

from config import TRAINING_DATA, _HR_ZONES, _HR_ZONE_COLORS, logger
from .helpers import _safe_float, _classify_type, _workout_distance, _extract_vo2max
from .gps import _detect_and_fix_gps

_DEFAULT_UTC_OFFSET_H = 2  # Israel Standard Time


_STROKE_NAMES = {
    "0": "Unknown", "1": "Mixed", "2": "Freestyle", "3": "Backstroke",
    "4": "Breaststroke", "5": "Butterfly", "6": "Kickboard"
}


def _get_stroke_name(code: str) -> str:
    """Map stroke style code to name."""
    return _STROKE_NAMES.get(str(code), "Unknown")


def _load_gpx_route(workout_num: int, data_dir: Path = None) -> list:
    """Load GPS points from GPX route file matching this workout's date.

    Returns list of (datetime_utc, lat, lon, elev_m) tuples, or empty list.
    """
    base = data_dir or TRAINING_DATA
    routes_dir = base / "workout-routes"
    if not routes_dir.exists():
        return []

    # Get workout date from CSV filename (workout_NNN_YYYY-MM-DD_Type.csv)
    csv_file = _find_workout_file(workout_num, ".csv", base)
    if not csv_file:
        return []
    # Extract date from filename: workout_211_2026-03-31_Running.csv -> 2026-03-31
    parts = csv_file.stem.split("_")
    if len(parts) < 3:
        return []
    workout_date = parts[2]  # YYYY-MM-DD

    # Find GPX files matching the workout date
    gpx_files = sorted(routes_dir.glob(f"route_{workout_date}_*.gpx"))
    if not gpx_files:
        return []

    # If multiple GPX files for same date, find closest by time
    # For now use first match (usually only one per workout)
    gpx_path = gpx_files[0]

    ns = {"gpx": "http://www.topografix.com/GPX/1/1"}
    try:
        tree = ET.parse(str(gpx_path))
    except ET.ParseError:
        return []

    points = []
    for trkpt in tree.getroot().findall(".//gpx:trkpt", ns):
        lat = float(trkpt.get("lat", 0))
        lon = float(trkpt.get("lon", 0))
        if not lat or not lon:
            continue
        elev_el = trkpt.find("gpx:ele", ns)
        elev = float(elev_el.text) if elev_el is not None and elev_el.text else None
        time_el = trkpt.find("gpx:time", ns)
        ts = None
        if time_el is not None and time_el.text:
            try:
                ts = datetime.fromisoformat(time_el.text.replace("Z", "+00:00"))
                # Convert to naive UTC for consistent comparison
                ts = ts.replace(tzinfo=None)
            except ValueError:
                pass
        if ts:
            points.append((ts, lat, lon, elev))

    return points


def _find_workout_file(workout_num: int, suffix: str, data_dir: Path = None) -> Path | None:
    """Find a workout file by number, checking workouts/ subfolders then root (legacy)."""
    base = data_dir or TRAINING_DATA
    pattern = f"workout_{workout_num:03d}_*{suffix}"
    # Check workouts/ subfolders first (new structure)
    files = list((base / "workouts").glob(f"*/{pattern}")) if (base / "workouts").exists() else []
    if not files:
        # Fallback to flat root (legacy)
        files = list(base.glob(pattern))
    return files[0] if files else None


def _load_workout_timeseries(workout_num: int, data_dir: Path = None):
    """Load time-series data for a specific workout CSV."""
    filepath = _find_workout_file(workout_num, ".csv", data_dir)
    if not filepath:
        return None
    metadata_lines = []
    data_rows = []
    columns = []

    with open(filepath, newline="") as f:
        reader = csv.DictReader(f)
        columns = list(reader.fieldnames) if reader.fieldnames else []
        for row in reader:
            ts = row.get("timestamp", "")
            if ts.startswith("##"):
                metadata_lines.append(ts)
            else:
                data_rows.append(dict(row))

    return {
        "filename": filepath.name,
        "metadata": metadata_lines,
        "columns": columns,
        "data": data_rows,
        "point_count": len(data_rows),
    }


def _hr_zone(hr: float, zones: list = None) -> str:
    for name, lo, hi in (zones or _HR_ZONES):
        if lo <= hr < hi:
            return name
    return "Z5"


def _detect_utc_offset(rows: list) -> int:
    """Detect UTC offset (hours) from the first row with an explicit +HHMM offset."""
    for r in rows:
        ts = r.get("timestamp", "")
        m = re.search(r'[+\-](\d{2})(\d{2})\s*$', ts)
        if m:
            h = int(m.group(1))
            sign = 1 if '+' in ts[10:] else -1
            return sign * h
    return _DEFAULT_UTC_OFFSET_H


def _parse_ts(ts_str: str, utc_offset_h: int = _DEFAULT_UTC_OFFSET_H):
    """Parse timestamp string to naive local datetime.

    GPS rows use ISO/Z (UTC): 2026-02-16T09:13:30Z
    Other rows use local +offset: 2026-02-16 11:13:29 +0200
    We convert UTC timestamps to local using utc_offset_h.
    """
    ts_str = ts_str.strip()
    if ts_str.endswith("Z"):
        try:
            utc_dt = datetime.strptime(ts_str[:19], "%Y-%m-%dT%H:%M:%S")
            return utc_dt + timedelta(hours=utc_offset_h)
        except ValueError:
            pass
    for fmt in ("%Y-%m-%d %H:%M:%S", "%Y-%m-%dT%H:%M:%S"):
        try:
            return datetime.strptime(ts_str[:19], fmt)
        except ValueError:
            continue
    return None


def _detect_intervals(rows: list, disc: str, utc_off: int) -> list | None:
    """Detect work/rest intervals from speed or power changes.

    Returns a list of interval dicts, or None if the workout is steady-state
    (speed variation <= 20%).

    For swim: returns None (swim sets from events.json are used instead).
    """
    if disc == "swim":
        return None

    # Determine which column to use for interval detection
    if disc == "run":
        speed_col = "RunningSpeed"  # km/h
        power_col = "RunningPower"
        cadence_col = None  # cadence from StepCount, not per-row
    elif disc == "bike":
        speed_col = "speed_mps"  # m/s -> convert to km/h
        power_col = "CyclingPower"
        cadence_col = "CyclingCadence"
    else:
        return None

    # Build a time-series of speed values with timestamps
    speed_series = []  # (datetime, speed_kmh, row_dict)
    for row in rows:
        ts_str = row.get("timestamp", "")
        if not ts_str or ts_str.startswith("##") or ts_str.startswith('"##'):
            continue
        ts = _parse_ts(ts_str, utc_off)
        if not ts:
            continue
        if disc == "run":
            spd = _safe_float(row.get(speed_col))
            # Check if RunningSpeed unit is m/s instead of km/h
            unit = row.get("RunningSpeed_unit", "")
            if unit == "m/s" and spd > 0:
                spd *= 3.6
        else:
            spd = _safe_float(row.get(speed_col)) * 3.6  # m/s -> km/h
        if spd > 0:
            speed_series.append((ts, spd, row))

    if len(speed_series) < 10:
        return None

    # Check speed variation — skip if <= 20%
    speeds_only = [s[1] for s in speed_series]
    avg_spd = sum(speeds_only) / len(speeds_only)
    if avg_spd <= 0:
        return None
    max_var = (max(speeds_only) - min(speeds_only)) / avg_spd
    if max_var <= 0.20:
        return None  # Steady-state workout

    # Apply rolling 10-second smoothing window (short to preserve strides/sprints)
    smoothed = []
    for i, (ts, spd, row) in enumerate(speed_series):
        window_vals = []
        for j in range(max(0, i - 15), min(len(speed_series), i + 16)):
            gap = abs((speed_series[j][0] - ts).total_seconds())
            if gap <= 5:
                window_vals.append(speed_series[j][1])
        smoothed.append(sum(window_vals) / len(window_vals) if window_vals else spd)

    # Classify each point as work or rest based on 80% of average speed threshold
    threshold = avg_spd * 0.80
    labels = ["work" if s >= threshold else "rest" for s in smoothed]

    # Build intervals by grouping consecutive same-label points
    raw_intervals = []
    current_label = labels[0]
    start_idx = 0
    for i in range(1, len(labels)):
        if labels[i] != current_label:
            raw_intervals.append((current_label, start_idx, i - 1))
            current_label = labels[i]
            start_idx = i
    raw_intervals.append((current_label, start_idx, len(labels) - 1))

    # Merge intervals shorter than 10 seconds into neighbors (short to capture strides)
    MIN_INTERVAL_SEC = 10
    merged = []
    for label, si, ei in raw_intervals:
        dur = (speed_series[ei][0] - speed_series[si][0]).total_seconds()
        if merged and dur < MIN_INTERVAL_SEC:
            # Merge into previous interval
            prev = merged[-1]
            merged[-1] = (prev[0], prev[1], ei)
        else:
            merged.append((label, si, ei))

    if len(merged) < 2:
        return None  # No meaningful intervals detected

    # Pre-index ALL rows by timestamp for metric lookups (HR, distance, power
    # may be on different rows than speed in Apple Health exports)
    dist_col = "DistanceWalkingRunning" if disc == "run" else "DistanceCycling"
    all_rows_ts = []  # (datetime, row_dict)
    for row in rows:
        ts_str = row.get("timestamp", "")
        if not ts_str or ts_str.startswith("##") or ts_str.startswith('"##'):
            continue
        ts = _parse_ts(ts_str, utc_off)
        if ts:
            all_rows_ts.append((ts, row))
    all_rows_ts.sort(key=lambda x: x[0])
    all_rows_times = [x[0] for x in all_rows_ts]

    # Build interval result dicts
    intervals = []
    first_ts = speed_series[0][0]
    for label, si, ei in merged:
        start_ts = speed_series[si][0]
        end_ts = speed_series[ei][0]
        dur_sec = (end_ts - start_ts).total_seconds()
        if dur_sec <= 0:
            continue
        start_offset = (start_ts - first_ts).total_seconds()

        # Collect speed from speed_series
        int_speeds = [speed_series[idx][1] for idx in range(si, ei + 1)]

        # Collect HR, power, distance, cadence from ALL rows in the time range
        # Use bisect to narrow to rows within [start_ts, end_ts] instead of scanning all
        int_hrs = []
        int_powers = []
        int_cadences = []
        int_dist = 0.0
        lo = bisect_left(all_rows_times, start_ts)
        hi = bisect_left(all_rows_times, end_ts, lo)
        for idx in range(lo, min(hi + 1, len(all_rows_ts))):
            ts, row = all_rows_ts[idx]
            if ts > end_ts:
                break
            hr = _safe_float(row.get("HeartRate"))
            if hr > 0:
                int_hrs.append(hr)
            pw = _safe_float(row.get(power_col))
            if pw > 0:
                int_powers.append(pw)
            if cadence_col:
                cad = _safe_float(row.get(cadence_col))
                if cad > 0:
                    int_cadences.append(cad)
            d = _safe_float(row.get(dist_col))
            if d > 0:
                unit = (row.get(f"{dist_col}_unit") or "").strip().lower()
                int_dist += d * (0.001 if unit == "m" else 1.0)

        avg_speed = sum(int_speeds) / len(int_speeds) if int_speeds else 0
        interval = {
            "type": label,
            "start_offset_sec": round(start_offset),
            "duration_sec": round(dur_sec),
            "avg_speed_kmh": round(avg_speed, 1),
            "distance_m": round(int_dist * 1000),
        }
        if disc == "run" and avg_speed > 0:
            pace = 60 / avg_speed
            interval["avg_pace_min_km"] = round(pace, 2)
            interval["pace_str"] = f"{int(pace)}:{round((pace % 1) * 60):02d}/km"
        if int_hrs:
            interval["avg_hr"] = round(sum(int_hrs) / len(int_hrs), 1)
            interval["hr_min"] = round(min(int_hrs))
            interval["hr_max"] = round(max(int_hrs))
        if int_powers:
            interval["avg_power"] = round(sum(int_powers) / len(int_powers))
        if int_cadences:
            interval["avg_cadence"] = round(sum(int_cadences) / len(int_cadences))

        intervals.append(interval)

    return intervals if len(intervals) >= 2 else None


def _sample_profiles(rows: list, disc: str, utc_off: int) -> dict:
    """Extract HR and elevation profiles sampled every ~30 seconds.

    Returns a dict with hr_profile, elevation_profile, hr_summary, elevation_summary.
    """
    # Build full HR and elevation timelines
    hr_points = []  # (datetime, hr)
    elev_points = []  # (datetime, elev_m)

    for row in rows:
        ts_str = row.get("timestamp", "")
        if not ts_str or ts_str.startswith("##") or ts_str.startswith('"##'):
            continue
        ts = _parse_ts(ts_str, utc_off)
        if not ts:
            continue
        hr = _safe_float(row.get("HeartRate"))
        if hr > 0:
            hr_points.append((ts, hr))
        elev = _safe_float(row.get("elevation_m"), default=None)
        if elev is not None:
            elev_points.append((ts, elev))

    result = {}

    # Sample HR at ~30s intervals
    if hr_points:
        hr_points.sort(key=lambda x: x[0])
        first_ts = hr_points[0][0]
        last_ts = hr_points[-1][0]
        total_sec = (last_ts - first_ts).total_seconds()

        hr_profile = []
        sample_interval = 30
        current_time = first_ts
        hr_idx = 0
        while current_time <= last_ts:
            offset = (current_time - first_ts).total_seconds()
            # Find nearest HR reading
            while hr_idx < len(hr_points) - 1 and hr_points[hr_idx + 1][0] <= current_time:
                hr_idx += 1
            hr_val = hr_points[hr_idx][1]
            hr_profile.append({"t": round(offset), "hr": round(hr_val, 1)})
            current_time += timedelta(seconds=sample_interval)

        result["hr_profile"] = hr_profile

        # HR summary for cardiac drift analysis
        all_hrs = [p[1] for p in hr_points]
        mid = len(hr_points) // 2
        first_half = [p[1] for p in hr_points[:mid]] if mid > 0 else all_hrs
        second_half = [p[1] for p in hr_points[mid:]] if mid > 0 else all_hrs
        first_avg = sum(first_half) / len(first_half) if first_half else 0
        second_avg = sum(second_half) / len(second_half) if second_half else 0
        drift_pct = ((second_avg - first_avg) / first_avg * 100) if first_avg > 0 else 0

        result["hr_summary"] = {
            "first_half_avg": round(first_avg, 1),
            "second_half_avg": round(second_avg, 1),
            "drift_pct": round(drift_pct, 1),
            "max": round(max(all_hrs)),
            "min": round(min(all_hrs)),
        }

    # Sample elevation at ~30s intervals
    if elev_points:
        elev_points.sort(key=lambda x: x[0])
        first_ts_e = elev_points[0][0]
        last_ts_e = elev_points[-1][0]

        elev_profile = []
        sample_interval = 30
        current_time = first_ts_e
        elev_idx = 0
        while current_time <= last_ts_e:
            offset = (current_time - first_ts_e).total_seconds()
            while elev_idx < len(elev_points) - 1 and elev_points[elev_idx + 1][0] <= current_time:
                elev_idx += 1
            ev = elev_points[elev_idx][1]
            elev_profile.append({"t": round(offset), "elev_m": round(ev, 1)})
            current_time += timedelta(seconds=sample_interval)

        result["elevation_profile"] = elev_profile

        # Elevation summary
        all_elevs = [p[1] for p in elev_points]
        total_ascent = 0.0
        total_descent = 0.0
        for i in range(1, len(elev_points)):
            diff = elev_points[i][1] - elev_points[i - 1][1]
            if diff > 0:
                total_ascent += diff
            else:
                total_descent += abs(diff)

        result["elevation_summary"] = {
            "total_ascent_m": round(total_ascent, 1),
            "total_descent_m": round(total_descent, 1),
            "min_m": round(min(all_elevs), 1),
            "max_m": round(max(all_elevs), 1),
        }

    return result


def _save_precomputed_sections(workout_num: int, data_dir: Path = None, merged_nums: list = None) -> bool:
    """Compute and save pre-computed sections data as .sections.json.

    Returns True if a file was saved, False otherwise.
    """
    # Compute full sections (force_full=True to bypass cache)
    sections = _compute_sections(workout_num, data_dir, merged_nums=merged_nums, force_full=True)
    if not sections or not sections.get("sections"):
        return False

    disc = sections["discipline"]

    # Load timeseries for interval detection and profile sampling
    nums_to_load = merged_nums if merged_nums and len(merged_nums) > 1 else [workout_num]
    rows = []
    for num in nums_to_load:
        ts_data = _load_workout_timeseries(int(num), data_dir)
        if ts_data and ts_data["data"]:
            rows.extend(ts_data["data"])

    utc_off = _detect_utc_offset(rows) if rows else 2

    # Detect intervals (run/bike only)
    intervals = _detect_intervals(rows, disc, utc_off) if rows else None

    # Build GPS point list: prefer CSV data, fallback to GPX route file
    gps_points = []  # (datetime_naive_utc, lat, lon)
    for row in rows:
        ts_str = row.get("timestamp", "")
        if not ts_str or ts_str.startswith("##") or ts_str.startswith('"##'):
            continue
        lat_val = _safe_float(row.get("lat"))
        if lat_val != 0:
            lon_val = _safe_float(row.get("lon"))
            ts = _parse_ts(ts_str, utc_off)
            if ts:
                gps_points.append((ts, lat_val, lon_val))

    gpx_route = None
    if not gps_points:
        # No GPS in CSV — try loading from GPX route file
        gpx_route = _load_gpx_route(workout_num, data_dir)
        if gpx_route:
            # GPX timestamps are UTC — convert to local time (same as _parse_ts)
            gps_points = [(p[0] + timedelta(hours=utc_off), p[1], p[2]) for p in gpx_route]
            logger.debug(f"Workout #{workout_num}: loaded {len(gps_points)} GPS points from GPX route")

    # Pre-extract sorted GPS timestamps for bisect lookups
    gps_times = [g[0] for g in gps_points] if gps_points else []

    def _bisect_nearest_gps(target_ts):
        if not gps_times:
            return None
        idx = bisect_left(gps_times, target_ts)
        best_idx = idx
        if idx >= len(gps_points):
            best_idx = len(gps_points) - 1
        elif idx > 0:
            d1 = abs((gps_times[idx - 1] - target_ts).total_seconds())
            d2 = abs((gps_times[idx] - target_ts).total_seconds())
            best_idx = idx - 1 if d1 < d2 else idx
        return best_idx

    # Assign GPS coordinates to interval start points (for map markers)
    if intervals and gps_points:
        first_ts = gps_points[0][0]
        for iv in intervals:
            target_sec = iv["start_offset_sec"]
            target_ts = first_ts + timedelta(seconds=target_sec)
            bi = _bisect_nearest_gps(target_ts)
            if bi is not None and abs((gps_points[bi][0] - target_ts).total_seconds()) <= 60:
                iv["start_lat"] = round(gps_points[bi][1], 6)
                iv["start_lon"] = round(gps_points[bi][2], 6)

    # Assign GPS coordinates to per-km sections too
    if gps_points and sections.get("sections"):
        first_gps_ts = gps_points[0][0]
        for sec in sections["sections"]:
            if sec.get("start_lat"):
                continue  # Already has GPS
            sec_km = sec.get("km", 0)
            if sec_km and not sec.get("start_lat"):
                total_gps_dur = (gps_points[-1][0] - first_gps_ts).total_seconds()
                total_dist = sections.get("total_distance_km", 1) or 1
                target_sec = (sec_km - 1) / total_dist * total_gps_dur
                target_ts = first_gps_ts + timedelta(seconds=target_sec)
                bi = _bisect_nearest_gps(target_ts)
                if bi is not None:
                    sec["start_lat"] = round(gps_points[bi][1], 6)
                    sec["start_lon"] = round(gps_points[bi][2], 6)

    # Sample HR and elevation profiles
    profiles = _sample_profiles(rows, disc, utc_off) if rows else {}

    vo2max = _extract_vo2max(rows)

    # Sum ActiveEnergyBurned from time-series rows
    active_calories_sum = 0.0
    for row in rows:
        ae = _safe_float(row.get("ActiveEnergyBurned"))
        if ae > 0:
            active_calories_sum += ae
    active_calories = round(active_calories_sum, 1) if active_calories_sum > 0 else None

    # Build the pre-computed result (exclude hr_colored_segments — too large)
    result = {
        "discipline": disc,
        "sections": sections["sections"],
        "hr_zones": sections["hr_zones"],
        "total_sections": sections["total_sections"],
        "total_distance_km": sections["total_distance_km"],
        "vo2max": vo2max,
        "active_calories": active_calories,
    }
    if sections.get("swim_sets"):
        result["swim_sets"] = sections["swim_sets"]
    if sections.get("swim_laps"):
        result["swim_laps"] = sections["swim_laps"]
    result["intervals"] = intervals
    result["hr_profile"] = profiles.get("hr_profile")
    result["elevation_profile"] = profiles.get("elevation_profile")
    result["hr_summary"] = profiles.get("hr_summary")
    result["elevation_summary"] = profiles.get("elevation_summary")

    # Save to .sections.json alongside the workout CSV
    sections_file = _find_workout_file(workout_num, ".csv", data_dir)
    if not sections_file:
        return False
    out_path = sections_file.with_suffix(".sections.json")
    out_path.write_text(json.dumps(result, ensure_ascii=False, separators=(",", ":")), encoding="utf-8")

    # Save GPS colored segments to a separate file (keeps .sections.json small)
    hr_segs = sections.get("hr_colored_segments")
    if not hr_segs and gpx_route:
        # Build HR-colored GPS segments from GPX route + CSV HR data
        # Collect HR time-series from CSV rows
        hr_ts = []  # (datetime_local, hr_value)
        for row in rows:
            ts_str = row.get("timestamp", "")
            if not ts_str or ts_str.startswith("##") or ts_str.startswith('"##'):
                continue
            hr = _safe_float(row.get("HeartRate"))
            if hr > 0:
                ts = _parse_ts(ts_str, utc_off)
                if ts:
                    hr_ts.append((ts, hr))

        # Sort HR time-series and extract timestamps for bisect lookups
        hr_ts.sort(key=lambda x: x[0])
        hr_ts_times = [h[0] for h in hr_ts]

        # Sample GPX to ~2000 points max
        step = max(1, len(gpx_route) // 2000)
        hr_segs = []
        for i in range(0, len(gpx_route), step):
            ts_utc, lat, lon, elev = gpx_route[i]
            ts_local = ts_utc + timedelta(hours=utc_off)
            seg = {"lat": round(lat, 6), "lon": round(lon, 6)}
            if elev is not None:
                seg["elevation"] = round(elev, 1)
            if hr_ts:
                idx = bisect_left(hr_ts_times, ts_local)
                best_idx = idx
                if idx >= len(hr_ts):
                    best_idx = len(hr_ts) - 1
                elif idx > 0:
                    d1 = abs((hr_ts_times[idx - 1] - ts_local).total_seconds())
                    d2 = abs((hr_ts_times[idx] - ts_local).total_seconds())
                    best_idx = idx - 1 if d1 < d2 else idx
                if abs((hr_ts[best_idx][0] - ts_local).total_seconds()) <= 30:
                    seg["hr"] = round(hr_ts[best_idx][1])
                    seg["zone"] = _hr_zone(hr_ts[best_idx][1], zones)
            hr_segs.append(seg)
        logger.debug(f"Workout #{workout_num}: built {len(hr_segs)} HR-colored GPS segments from GPX route")
    _save_gps_segments(workout_num, data_dir, hr_segs)

    return True


def _load_precomputed_sections(workout_num: int, data_dir: Path = None) -> dict | None:
    """Load pre-computed sections from .sections.json if it exists."""
    sections_file = _find_workout_file(workout_num, ".sections.json", data_dir)
    if not sections_file:
        return None
    try:
        with open(sections_file) as f:
            return json.load(f)
    except (json.JSONDecodeError, IOError):
        return None


def _save_gps_segments(workout_num: int, data_dir: Path = None, hr_colored_segments: list = None) -> bool:
    """Save HR-colored GPS segments to a separate .gps_segments.json file.

    Returns True if a file was saved, False otherwise (e.g. indoor workout with no GPS).
    """
    if not hr_colored_segments:
        return False
    csv_file = _find_workout_file(workout_num, ".csv", data_dir)
    if not csv_file:
        return False
    out_path = csv_file.with_suffix(".gps_segments.json")
    out_path.write_text(
        json.dumps(hr_colored_segments, ensure_ascii=False, separators=(",", ":")),
        encoding="utf-8",
    )
    return True


def _load_gps_segments(workout_num: int, data_dir: Path = None) -> list | None:
    """Load pre-computed GPS segments from .gps_segments.json if it exists."""
    seg_file = _find_workout_file(workout_num, ".gps_segments.json", data_dir)
    if not seg_file:
        return None
    try:
        with open(seg_file) as f:
            return json.load(f)
    except (json.JSONDecodeError, IOError):
        return None


def _generate_all_sections(data_dir: Path, workout_nums: list = None) -> int:
    """Batch generate .sections.json for workouts (incremental).

    If workout_nums is provided, generate only for those (even if already exists).
    Otherwise, generate for all workouts that don't have a .sections.json yet.

    Returns the number of sections files generated.
    """
    from .summary import _load_summary
    from .helpers import _classify_type
    workouts = _load_summary(data_dir)
    count = 0

    for w in workouts:
        wnum = int(w.get("workout_num", 0))
        disc = _classify_type(w.get("type", ""))
        if disc not in ("run", "bike", "swim"):
            continue

        if workout_nums:
            if wnum not in workout_nums:
                continue
        else:
            # Skip if .sections.json already exists
            existing = _find_workout_file(wnum, ".sections.json", data_dir)
            if existing:
                continue

        try:
            if _save_precomputed_sections(wnum, data_dir):
                count += 1
        except Exception as e:
            logger.warning(f"Failed to generate sections for workout {wnum}: {e}")

    return count


def _compute_sections(workout_num: int, data_dir: Path = None, merged_nums: list = None, force_full: bool = False, zones: list = None) -> dict | None:
    """Compute per-section splits, HR zones, and colored GPS segments.

    merged_nums: if provided, load and concatenate timeseries from all workout numbers.
    force_full: if True, always compute from raw data (needed for GPS segments).
    """
    # Fast path: load pre-computed data when available
    if not force_full and not (merged_nums and len(merged_nums) > 1):
        precomputed = _load_precomputed_sections(workout_num, data_dir)
        if precomputed:
            return precomputed
    # Load summary to get discipline
    from .summary import _load_summary  # Local import to avoid circular dependency
    workouts = _load_summary(data_dir)
    w = next((x for x in workouts if int(x.get("workout_num", 0)) == workout_num), None)
    if not w:
        return None
    disc = _classify_type(w.get("type", ""))
    if disc not in ("run", "bike", "swim"):
        return None

    total_dist_km = _workout_distance(w)

    # Load timeseries — concatenate if merged
    nums_to_load = merged_nums if merged_nums and len(merged_nums) > 1 else [workout_num]
    rows = []
    for num in nums_to_load:
        ts_data = _load_workout_timeseries(int(num), data_dir)
        if ts_data and ts_data["data"]:
            rows.extend(ts_data["data"])
    if not rows:
        return None

    # Save original elevation values before GPS fix (for showing orig in splits)
    orig_elevations = {i: r.get("elevation_m", "") for i, r in enumerate(rows)}

    # Fix GPS anomalies before computing sections
    gps_result = _detect_and_fix_gps(rows, w.get("type", ""))

    # Determine distance column and split size
    _DISC_DIST_CONFIG = {
        "run":  ("DistanceWalkingRunning", 1.0),
        "swim": ("DistanceSwimming", 0.1),   # per-100m in km
        "bike": ("DistanceCycling", 1.0),
    }
    dist_col, split_km = _DISC_DIST_CONFIG[disc]
    dist_unit_mult = 1.0
    for r in rows:
        unit = (r.get(f"{dist_col}_unit") or "").strip().lower()
        if unit:
            break
    else:
        unit = ""
    if unit == "m":
        dist_unit_mult = 0.001

    # Detect timezone offset for consistent timestamp parsing
    utc_off = _detect_utc_offset(rows)

    # Pre-collect GPS points indexed by time for later assignment to sections.
    # GPS and distance data are on separate rows, often non-overlapping in order.
    gps_points = []  # list of (datetime, lat, lon)
    for row in rows:
        ts_str = row.get("timestamp", "")
        if not ts_str or ts_str.startswith("##") or ts_str.startswith('"##'):
            continue
        lat_val = _safe_float(row.get("lat"))
        if lat_val != 0:
            lon_val = _safe_float(row.get("lon"))
            ts = _parse_ts(ts_str, utc_off)
            if ts:
                gps_points.append((ts, lat_val, lon_val))

    gps_point_times = [g[0] for g in gps_points] if gps_points else []

    def _nearest_gps(target_ts, max_gap_sec=60):
        """Find GPS point closest in time to target_ts, within max_gap_sec."""
        if not gps_points or not target_ts:
            return None, None
        idx = bisect_left(gps_point_times, target_ts)
        best_idx = idx
        if idx >= len(gps_points):
            best_idx = len(gps_points) - 1
        elif idx > 0:
            d1 = abs((gps_point_times[idx - 1] - target_ts).total_seconds())
            d2 = abs((gps_point_times[idx] - target_ts).total_seconds())
            best_idx = idx - 1 if d1 < d2 else idx
        if abs((gps_points[best_idx][0] - target_ts).total_seconds()) > max_gap_sec:
            return None, None
        return round(gps_points[best_idx][1], 6), round(gps_points[best_idx][2], 6)

    # Pre-collect HR timeline for time-based lookup (sorted by time)
    hr_timeline = []  # list of (datetime, hr_value)
    for row in rows:
        ts_str = row.get("timestamp", "")
        if not ts_str or ts_str.startswith("##") or ts_str.startswith('"##'):
            continue
        hr_val = _safe_float(row.get("HeartRate"))
        if hr_val > 0:
            ts = _parse_ts(ts_str, utc_off)
            if ts:
                hr_timeline.append((ts, hr_val))
    hr_timeline.sort(key=lambda x: x[0])
    hr_times = [h[0] for h in hr_timeline]

    def _hr_at_time(target_ts):
        """Find HR value closest in time to target_ts using binary search."""
        if not hr_timeline or not target_ts:
            return 0
        idx = bisect_left(hr_times, target_ts)
        # Check nearest of idx-1 and idx
        best_idx = idx
        if idx >= len(hr_timeline):
            best_idx = len(hr_timeline) - 1
        elif idx > 0:
            d1 = abs((hr_times[idx - 1] - target_ts).total_seconds())
            d2 = abs((hr_times[idx] - target_ts).total_seconds())
            best_idx = idx - 1 if d1 < d2 else idx
        return hr_timeline[best_idx][1]

    stroke_style_timeline = []
    if disc == "swim":
        for row in rows:
            ts_str = row.get("timestamp", "")
            if not ts_str or ts_str.startswith("##") or ts_str.startswith('"##'):
                continue
            ss_val = row.get("SwimmingStrokeStyle", "")
            if ss_val:
                ts = _parse_ts(ts_str, utc_off)
                if ts:
                    stroke_style_timeline.append((ts, _get_stroke_name(ss_val)))
        stroke_style_timeline.sort(key=lambda x: x[0])

    def _stroke_style_in_range(start_ts, end_ts):
        """Find dominant stroke style in a time range."""
        if not stroke_style_timeline or not start_ts or not end_ts:
            return None
        styles = Counter()
        for ts, name in stroke_style_timeline:
            if ts >= start_ts and ts <= end_ts:
                styles[name] += 1
        return styles.most_common(1)[0][0] if styles else None

    def _time_weighted_avg(vals_with_ts):
        """Compute time-weighted average from list of (value, timestamp) pairs.
        Each value is weighted by the time until the next reading."""
        if not vals_with_ts:
            return None
        if len(vals_with_ts) == 1:
            return vals_with_ts[0][0]
        total_weight = 0.0
        weighted_sum = 0.0
        for i in range(len(vals_with_ts)):
            val, ts = vals_with_ts[i]
            if i < len(vals_with_ts) - 1:
                dt = (vals_with_ts[i + 1][1] - ts).total_seconds()
                if dt <= 0:
                    dt = 1.0  # same timestamp, use 1s weight
            else:
                # Last reading: weight by average gap or 1s
                if len(vals_with_ts) > 1:
                    total_span = (vals_with_ts[-1][1] - vals_with_ts[0][1]).total_seconds()
                    dt = total_span / (len(vals_with_ts) - 1) if total_span > 0 else 1.0
                else:
                    dt = 1.0
            weighted_sum += val * dt
            total_weight += dt
        return weighted_sum / total_weight if total_weight > 0 else None

    # Try Apple's exact splits first (.splits.json from export)
    # Skip for swim: Apple only produces a single "km" split covering the entire workout,
    # so distance-based 100m accumulation gives better per-100m sections.
    apple_splits = None
    if disc != "swim" and not (merged_nums and len(merged_nums) > 1):  # Don't use for merged workouts
        splits_file = _find_workout_file(workout_num, ".splits.json", data_dir)
        if splits_file:
            try:
                with open(splits_file) as sf:
                    apple_splits = json.load(sf).get("km", [])
            except (json.JSONDecodeError, IOError):
                pass

    # Main pass: compute HR zones, GPS segments, and per-section metrics
    last_hr = 0.0
    last_hr_ts = None
    last_ts = None
    cumulative_dist_km = 0.0
    section_num = 1
    sections = []

    sec_hr_vals = []
    sec_speed_vals = []
    sec_cadence_vals = []
    sec_power_vals = []
    sec_gct_vals = []
    sec_stride_vals = []
    sec_stroke_vals = []
    sec_step_count = 0.0  # accumulate raw step count for running cadence
    sec_elev_vals = []  # altitude readings per section for elevation gain calc
    sec_elev_orig = []  # original elevation readings (pre-GPS-fix) for comparison
    has_gps_corrections = gps_result.get("corrected_count", 0) > 0
    sec_start_ts = None
    sec_dist_start = 0.0

    hr_zone_secs = {"Z1": 0.0, "Z2": 0.0, "Z3": 0.0, "Z4": 0.0, "Z5": 0.0}

    # Precompute Apple split boundaries (start, end, duration) if available
    apple_split_ends = []  # list of (end_datetime, duration_min)
    apple_split_starts = []  # list of start_datetime for each split
    if apple_splits:
        for sp in apple_splits:
            sp_start = _parse_ts(sp["date"], utc_off)
            if sp_start:
                sp_end = sp_start + timedelta(minutes=sp["duration_min"])
                apple_split_ends.append((sp_end, sp["duration_min"]))
                apple_split_starts.append(sp_start)

    # Colored GPS segments for map
    hr_colored_segments = []
    gps_sample_counter = 0

    for row_idx, row in enumerate(rows):
        ts_str = row.get("timestamp", "")
        if not ts_str or ts_str.startswith("##") or ts_str.startswith('"##'):
            continue

        current_ts = _parse_ts(ts_str, utc_off)

        # Update carried-forward HR and track zone time
        hr_val = _safe_float(row.get("HeartRate"))
        if hr_val > 0:
            # Count time between consecutive HR readings (not all data points)
            if last_hr > 0 and last_hr_ts and current_ts:
                dt_sec = (current_ts - last_hr_ts).total_seconds()
                if 0 < dt_sec < 120:  # reasonable gap between HR samples
                    hr_zone_secs[_hr_zone(hr_val, zones)] += dt_sec
            last_hr = hr_val
            last_hr_ts = current_ts

        # GPS segment sampling for map
        lat_val = _safe_float(row.get("lat"))
        lon_val = _safe_float(row.get("lon"))
        if lat_val != 0 and lon_val != 0:
            gps_sample_counter += 1
            if gps_sample_counter % 5 == 0:
                elev = _safe_float(row.get("elevation_m"), default=None)
                spd = _safe_float(row.get("speed_mps"))
                pace = (1000 / 60 / spd) if spd > 0 else 0
                # Use time-based HR lookup for accurate coloring
                seg_hr = _hr_at_time(current_ts) if current_ts else last_hr
                hr_colored_segments.append({
                    "lat": round(lat_val, 6),
                    "lon": round(lon_val, 6),
                    "hr": round(seg_hr, 1) if seg_hr > 0 else None,
                    "pace": round(pace, 2) if pace > 0 else None,
                    "elevation": round(elev, 1) if elev is not None else None,
                    "zone": _hr_zone(seg_hr, zones) if seg_hr > 0 else None,
                })

        # Accumulate distance (iPhone duplicates already removed at CSV level by source dedup)
        dist_val = _safe_float(row.get(dist_col))
        if dist_val > 0:
            cumulative_dist_km += dist_val * dist_unit_mult

        # Section accumulation — only accumulate data within the Apple split window
        in_split_window = True
        if apple_split_starts and section_num <= len(apple_split_starts):
            if current_ts and current_ts < apple_split_starts[section_num - 1]:
                in_split_window = False

        if sec_start_ts is None and current_ts and in_split_window:
            sec_start_ts = current_ts
            sec_dist_start = cumulative_dist_km

        if in_split_window and last_hr > 0:
            sec_hr_vals.append(last_hr)

        if disc == "run" and in_split_window:
            rs = _safe_float(row.get("RunningSpeed"))
            if rs > 0:
                if row.get("RunningSpeed_unit", "") == "m/s":
                    rs *= 3.6
                sec_speed_vals.append(rs)
            sc = _safe_float(row.get("StepCount"))
            if sc > 0:
                sec_step_count += sc
            rp = _safe_float(row.get("RunningPower"))
            if rp > 0:
                sec_power_vals.append(rp)
            gct = _safe_float(row.get("RunningGroundContactTime"))
            if gct > 0:
                sec_gct_vals.append(gct)
            sl = _safe_float(row.get("RunningStrideLength"))
            if sl > 0:
                sec_stride_vals.append(sl)
        elif disc == "swim" and in_split_window:
            strokes = _safe_float(row.get("SwimmingStrokeCount"))
            if strokes > 0:
                sec_stroke_vals.append(strokes)
        elif disc == "bike" and in_split_window:
            spd = _safe_float(row.get("speed_mps"))
            if spd > 0:
                sec_speed_vals.append(spd * 3.6)
            cp = _safe_float(row.get("CyclingPower"))
            if cp > 0:
                sec_power_vals.append(cp)
            cc = _safe_float(row.get("CyclingCadence"))
            if cc > 0:
                sec_cadence_vals.append(cc)

        # Collect elevation readings for per-section elevation gain
        elev_reading = _safe_float(row.get("elevation_m"), default=None)
        if elev_reading is not None:
            sec_elev_vals.append(elev_reading)
        # Also collect original (pre-GPS-fix) elevation for comparison
        if has_gps_corrections:
            orig_elev_str = orig_elevations.get(row_idx, "")
            orig_elev_val = _safe_float(orig_elev_str, default=None)
            if orig_elev_val is not None:
                sec_elev_orig.append(orig_elev_val)

        # Check if we've completed a split
        # Apple splits: use exact time boundaries; Computed: use distance accumulation
        split_completed = False
        if apple_split_ends and section_num <= len(apple_split_ends):
            split_end, split_dur_min = apple_split_ends[section_num - 1]
            if current_ts and current_ts >= split_end:
                split_completed = True
                elapsed_sec = split_dur_min * 60
        else:
            section_dist = cumulative_dist_km - sec_dist_start
            if section_dist >= split_km and sec_start_ts and current_ts:
                split_completed = True
                elapsed_sec = (current_ts - sec_start_ts).total_seconds()
                if elapsed_sec <= 0:
                    elapsed_sec = 1

        if split_completed:

            section = {"num": section_num}

            if disc == "run":
                section["km"] = section_num
                section["duration_sec"] = round(elapsed_sec, 1)
                mm = int(elapsed_sec // 60)
                ss = round(elapsed_sec % 60)
                if ss == 60:
                    mm += 1; ss = 0
                section["time_str"] = f"{mm}:{ss:02d}"
                # Pace: adjust for partial last split
                actual_km = split_km
                if apple_split_ends and section_num == len(apple_split_ends) and total_dist_km > 0:
                    remaining = total_dist_km - (section_num - 1) * split_km
                    if 0 < remaining < split_km:
                        actual_km = remaining
                pace_min_km = (elapsed_sec / 60) / actual_km if actual_km > 0 else 0
                if not apple_split_ends:
                    # Computed splits: use RunningSpeed for more accurate pace
                    avg_spd = (sum(sec_speed_vals) / len(sec_speed_vals)) if sec_speed_vals else 0
                    if avg_spd == 0 and elapsed_sec > 0:
                        avg_spd = split_km / (elapsed_sec / 3600)
                    if avg_spd > 0:
                        pace_min_km = 60 / avg_spd
                section["avg_pace_min_km"] = round(pace_min_km, 2)
                section["pace_str"] = f"{int(pace_min_km)}:{round((pace_min_km % 1) * 60):02d}" if pace_min_km > 0 else "-"
                section["avg_hr"] = round(sum(sec_hr_vals) / len(sec_hr_vals), 1) if sec_hr_vals else None
                section["hr_min"] = round(min(sec_hr_vals), 0) if sec_hr_vals else None
                section["hr_max"] = round(max(sec_hr_vals), 0) if sec_hr_vals else None
                section["avg_cadence"] = round(sec_step_count / (elapsed_sec / 60)) if sec_step_count > 0 and elapsed_sec > 0 else None
                section["avg_power"] = round(sum(sec_power_vals) / len(sec_power_vals), 0) if sec_power_vals else None
                section["avg_gct"] = round(sum(sec_gct_vals) / len(sec_gct_vals), 0) if sec_gct_vals else None
                section["avg_stride"] = round(sum(sec_stride_vals) / len(sec_stride_vals), 2) if sec_stride_vals else None
            elif disc == "swim":
                section["segment_m"] = section_num * 100
                section["duration_sec"] = round(elapsed_sec, 1)
                pace_per_100 = elapsed_sec
                section["pace_per_100m_sec"] = round(pace_per_100, 1)
                mm = int(pace_per_100 // 60)
                ss = int(pace_per_100 % 60)
                section["pace_str"] = f"{mm}:{ss:02d}/100m"
                section["avg_hr"] = round(sum(sec_hr_vals) / len(sec_hr_vals), 1) if sec_hr_vals else None
                section["hr_min"] = round(min(sec_hr_vals), 0) if sec_hr_vals else None
                section["hr_max"] = round(max(sec_hr_vals), 0) if sec_hr_vals else None
                strokes_100 = round(sum(sec_stroke_vals)) if sec_stroke_vals else None
                section["stroke_count"] = strokes_100
                section["stroke_style"] = _stroke_style_in_range(sec_start_ts, current_ts)
                # SWOLF per 25m = time_per_25 + strokes_per_25
                # SWOLF per 100m = time_per_100 + strokes_per_100
                if strokes_100 and elapsed_sec > 0:
                    t25 = elapsed_sec / 4
                    s25 = strokes_100 / 4
                    section["swolf"] = round(t25 + s25)
                    section["swolf_100"] = round(elapsed_sec + strokes_100)
                    section["pace_strokes_str"] = f"{mm}'{ss:02d}\"{strokes_100}"
                else:
                    section["swolf"] = None
                    section["swolf_100"] = None
                    section["pace_strokes_str"] = f"{mm}'{ss:02d}\""
            elif disc == "bike":
                section["km_marker"] = section_num
                section["duration_sec"] = round(elapsed_sec, 1)
                mm = int(elapsed_sec // 60)
                ss = round(elapsed_sec % 60)
                if ss == 60:
                    mm += 1; ss = 0
                section["time_str"] = f"{mm}:{ss:02d}"
                # Speed: adjust for partial last split
                actual_km = split_km
                if apple_split_ends and section_num == len(apple_split_ends) and total_dist_km > 0:
                    remaining = total_dist_km - (section_num - 1) * split_km
                    if 0 < remaining < split_km:
                        actual_km = remaining
                if apple_split_ends and elapsed_sec > 0:
                    avg_spd = actual_km / (elapsed_sec / 3600)
                else:
                    avg_spd = (sum(sec_speed_vals) / len(sec_speed_vals)) if sec_speed_vals else 0
                    if avg_spd == 0 and elapsed_sec > 0:
                        avg_spd = split_km / (elapsed_sec / 3600)
                section["avg_speed_kmh"] = round(avg_spd, 1)
                section["avg_hr"] = round(sum(sec_hr_vals) / len(sec_hr_vals), 1) if sec_hr_vals else None
                section["hr_min"] = round(min(sec_hr_vals), 0) if sec_hr_vals else None
                section["hr_max"] = round(max(sec_hr_vals), 0) if sec_hr_vals else None
                section["avg_power"] = round(sum(sec_power_vals) / len(sec_power_vals), 0) if sec_power_vals else None
                section["avg_cadence"] = round(sum(sec_cadence_vals) / len(sec_cadence_vals), 0) if sec_cadence_vals else None

            # Compute elevation gain for this section from sequential altitude readings
            elev_gain = 0.0
            if len(sec_elev_vals) >= 2:
                for i in range(1, len(sec_elev_vals)):
                    diff = sec_elev_vals[i] - sec_elev_vals[i - 1]
                    if diff > 0:
                        elev_gain += diff
            section["elev_gain_m"] = round(elev_gain, 1) if sec_elev_vals else None

            # Original elevation (pre-GPS-fix) for sections where correction changed the value
            if has_gps_corrections and sec_elev_orig:
                orig_gain = 0.0
                for i in range(1, len(sec_elev_orig)):
                    diff = sec_elev_orig[i] - sec_elev_orig[i - 1]
                    if diff > 0:
                        orig_gain += diff
                orig_gain = round(orig_gain, 1)
                if orig_gain != section.get("elev_gain_m"):
                    section["elev_gain_m_original"] = orig_gain

            # Assign GPS coords by finding nearest GPS point to section start time
            slat, slon = _nearest_gps(sec_start_ts)
            section["start_lat"] = slat
            section["start_lon"] = slon
            section["hr_zone"] = _hr_zone(section.get("avg_hr") or 0, zones) if section.get("avg_hr") else None
            section["_start_ts"] = sec_start_ts
            section["_end_ts"] = current_ts

            sections.append(section)
            section_num += 1

            # Reset accumulators
            sec_hr_vals = []
            sec_speed_vals = []
            sec_cadence_vals = []
            sec_power_vals = []
            sec_gct_vals = []
            sec_stride_vals = []
            sec_step_count = 0.0
            sec_stroke_vals = []
            sec_elev_vals = []
            sec_elev_orig = []
            sec_start_ts = current_ts
            sec_dist_start = cumulative_dist_km

        last_ts = current_ts

    # Post-process: compute elevation gain per section from ALL elevation data
    # (elevation and distance rows may not overlap in the CSV — different sensors)
    # Only use elevation data within the workout's actual time range (first to last section)
    if sections and any(s.get("elev_gain_m") is None for s in sections):
        workout_start = sections[0].get("_start_ts")
        workout_end = sections[-1].get("_end_ts")
        elev_timeline = []
        for row in rows:
            ts_str = row.get("timestamp", "")
            if not ts_str or ts_str.startswith("##") or ts_str.startswith('"##'):
                continue
            ev = _safe_float(row.get("elevation_m"), default=None)
            if ev is not None:
                ts = _parse_ts(ts_str, utc_off)
                if ts and workout_start and workout_end and workout_start <= ts <= workout_end:
                    elev_timeline.append((ts, ev))
        if elev_timeline:
            elev_timeline.sort(key=lambda x: x[0])
            for sec in sections:
                s_ts, e_ts = sec.get("_start_ts"), sec.get("_end_ts")
                if not s_ts or not e_ts:
                    continue
                sec_vals = [ev for ts, ev in elev_timeline if s_ts <= ts <= e_ts]
                if len(sec_vals) >= 2:
                    gain = sum(max(0, sec_vals[j] - sec_vals[j-1]) for j in range(1, len(sec_vals)))
                    sec["elev_gain_m"] = round(gain, 1)

    # Drop partial last split (< 30 seconds — typically a fraction of a km at end)
    if sections and apple_split_ends and len(sections) == len(apple_split_ends):
        last = sections[-1]
        if last.get("duration_sec", 0) < 30:
            sections.pop()

    # Compute HR zone percentages
    total_hr_sec = sum(hr_zone_secs.values())
    hr_zones = {}
    for zone in ("Z1", "Z2", "Z3", "Z4", "Z5"):
        secs = hr_zone_secs[zone]
        hr_zones[zone] = {
            "seconds": round(secs, 1),
            "pct": round(secs / total_hr_sec * 100, 1) if total_hr_sec > 0 else 0,
            "color": _HR_ZONE_COLORS[zone],
        }

    # Build swim sets + individual laps from real Apple Watch events (.events.json)
    swim_sets = []
    swim_individual_laps = []  # individual 25m laps for /25M view
    pool_length_m = 25  # default, overridden from metadata for swim workouts
    if disc == "swim":
        # Find the events.json file for this workout
        events_file = _find_workout_file(workout_num, ".events.json", data_dir)
        if events_file:
            with open(events_file) as ef:
                raw_events = json.load(ef)

            # Separate segments and laps
            segments = [e for e in raw_events if e["type"] == "HKWorkoutEventTypeSegment"]
            laps = [e for e in raw_events if e["type"] == "HKWorkoutEventTypeLap"]

            # Parse segment timestamps for time-range matching
            def _parse_event_ts(date_str):
                """Parse event date like '2026-02-17 20:25:55 +0200'."""
                try:
                    return datetime.strptime(date_str, "%Y-%m-%d %H:%M:%S %z")
                except Exception:
                    return None

            # Assign laps to segments by time range
            # Each segment has a start time and duration; laps within that range belong to it
            seg_lap_groups = []  # list of (segment_dict, [lap_dicts])
            used_lap_indices = set()

            for seg in segments:
                seg_start = _parse_event_ts(seg["date"])
                if not seg_start:
                    continue
                seg_dur_sec = seg["duration_min"] * 60
                seg_end = seg_start + timedelta(seconds=seg_dur_sec)
                seg_laps = []
                for li, lap in enumerate(laps):
                    if li in used_lap_indices:
                        continue
                    lap_ts = _parse_event_ts(lap["date"])
                    if lap_ts and seg_start - timedelta(seconds=1) <= lap_ts <= seg_end:
                        seg_laps.append(lap)
                        used_lap_indices.add(li)
                lap_dur_sum = sum(lap.get("duration_min", 0) * 60 for lap in seg_laps)
                swim_sec = seg["duration_min"] * 60
                if swim_sec > 0 and abs(lap_dur_sum - swim_sec) / swim_sec > 0.20:
                    logger.warning(f"Swim segment duration mismatch: laps={lap_dur_sum:.0f}s vs segment={swim_sec:.0f}s")
                seg_lap_groups.append((seg, seg_laps))

            # Determine pool length from summary metadata, fall back to 25m
            pool_length_m = 25
            lap_len_raw = w.get("meta_LapLength", "")
            if lap_len_raw:
                try:
                    # meta_LapLength can be "25", "50", or "25 m" etc.
                    pool_length_m = int(float(str(lap_len_raw).split()[0]))
                except (ValueError, IndexError):
                    pass

            # Build swim_sets from segments
            for si, (seg, seg_laps) in enumerate(seg_lap_groups):
                seg_start = _parse_event_ts(seg["date"])
                swim_sec = seg["duration_min"] * 60
                n_laps = max(len(seg_laps), 1)
                dist_m = n_laps * pool_length_m

                # Use real SWOLF from segment if available
                swolf = seg.get("swolf")

                style = None
                if seg_laps:
                    style_counts = Counter()
                    for lap in seg_laps:
                        ss = lap.get("stroke_style")
                        if ss is not None:
                            style_counts[_get_stroke_name(ss)] += 1
                    if style_counts:
                        style = style_counts.most_common(1)[0][0]

                # Pace per 100m
                pace_100 = (swim_sec / (dist_m / 100)) if dist_m > 0 else 0
                mm = int(pace_100 // 60)
                ss_val = int(pace_100 % 60)

                # Swim time formatted
                sm = int(swim_sec // 60)
                sss = int(swim_sec % 60)

                # HR: average HR at segment midpoint and at each lap start
                hr_vals = []
                if seg_start:
                    mid_ts = seg_start.replace(tzinfo=None) + timedelta(seconds=swim_sec / 2)
                    mid_hr = _hr_at_time(mid_ts)
                    if mid_hr > 0:
                        hr_vals.append(mid_hr)
                for lap in seg_laps:
                    lap_ts = _parse_event_ts(lap["date"])
                    if lap_ts:
                        lap_hr = _hr_at_time(lap_ts.replace(tzinfo=None))
                        if lap_hr > 0:
                            hr_vals.append(lap_hr)
                avg_hr_val = round(sum(hr_vals) / len(hr_vals), 1) if hr_vals else None

                # Rest after: gap between this segment end and next segment start
                rest_after = 0.0
                if si + 1 < len(seg_lap_groups):
                    next_seg_start = _parse_event_ts(seg_lap_groups[si + 1][0]["date"])
                    if seg_start and next_seg_start:
                        seg_end_ts = seg_start + timedelta(seconds=swim_sec)
                        rest_after = max(0, (next_seg_start - seg_end_ts).total_seconds())

                # Strokes per 25m from individual lap SWOLF values
                strokes_per_25 = None
                if seg_laps:
                    lap_strokes = []
                    for lap in seg_laps:
                        ls = lap.get("swolf")
                        if ls is not None:
                            lap_time_sec = lap["duration_min"] * 60
                            lap_strokes_val = ls - lap_time_sec
                            if lap_strokes_val > 0:
                                lap_strokes.append(lap_strokes_val)
                    if lap_strokes:
                        strokes_per_25 = round(sum(lap_strokes) / len(lap_strokes))

                swim_sets.append({
                    "set_num": si + 1,
                    "laps": n_laps,
                    "distance_m": dist_m,
                    "swim_sec": round(swim_sec, 1),
                    "swim_time_str": f"{sm}:{sss:02d}",
                    "pace_per_100m_sec": round(pace_100, 1),
                    "pace_str": f"{mm}:{ss_val:02d}/100m",
                    "avg_hr": avg_hr_val,
                    "strokes_per_25": strokes_per_25,
                    "swolf": swolf,
                    "stroke_style": style,
                    "rest_after_sec": round(rest_after, 1),
                })

            # Build individual laps for per-lap view
            laps_per_100m = 100 / pool_length_m  # e.g. 4 for 25m, 2 for 50m
            for li, lap in enumerate(laps):
                lap_ts = _parse_event_ts(lap["date"])
                lap_sec = lap["duration_min"] * 60
                pace_100 = lap_sec * laps_per_100m  # scale to pace per 100m
                mm = int(pace_100 // 60)
                ss_val = int(pace_100 % 60)
                lap_sec_int = round(lap_sec)

                style_code = lap.get("stroke_style")
                style_name = _get_stroke_name(style_code) if style_code is not None else None

                # HR at lap time
                lap_hr = None
                if lap_ts:
                    hr_val = _hr_at_time(lap_ts.replace(tzinfo=None))
                    if hr_val > 0:
                        lap_hr = round(hr_val, 1)

                # Strokes = SWOLF - time_in_seconds (for one pool-length lap)
                lap_swolf = lap.get("swolf")
                lap_strokes = round(lap_swolf - lap_sec) if lap_swolf and lap_sec > 0 else None

                swim_individual_laps.append({
                    "lap_num": li + 1,
                    "distance_m": pool_length_m,
                    "duration_sec": round(lap_sec, 1),
                    "pace_per_100m_sec": round(pace_100, 1),
                    "pace_str": f"{mm}:{ss_val:02d}/100m",
                    "swolf": lap_swolf,
                    "strokes": lap_strokes,
                    "stroke_style": style_name,
                    "avg_hr": lap_hr,
                    "hr_zone": _hr_zone(lap_hr, zones) if lap_hr else None,
                })

    # Build /100M sections from events.json laps (groups of N laps = 100m)
    # More accurate than CSV distance accumulation; matches Apple Fitness display.
    # Falls back to CSV-based sections if no laps or only 1 section would result.
    laps_per_100m_int = max(1, round(100 / pool_length_m)) if pool_length_m > 0 else 4
    if swim_individual_laps and len(swim_individual_laps) >= 1:
        swim_100m_sections = []
        for gi in range(0, len(swim_individual_laps) // laps_per_100m_int):
            group = swim_individual_laps[gi * laps_per_100m_int : gi * laps_per_100m_int + laps_per_100m_int]
            total_sec = sum(l["duration_sec"] for l in group)
            pace_100 = total_sec  # N laps × pool_length = 100m, so total time IS the pace/100m
            mm = int(pace_100 // 60)
            ss = int(pace_100 % 60)
            hr_vals = [l["avg_hr"] for l in group if l["avg_hr"]]
            avg_hr = round(sum(hr_vals) / len(hr_vals), 1) if hr_vals else None
            strokes = [l["strokes"] for l in group if l["strokes"] is not None]
            total_strokes = round(sum(strokes)) if strokes else None
            # Dominant stroke style in the group
            styles = Counter(l["stroke_style"] for l in group if l["stroke_style"])
            style = styles.most_common(1)[0][0] if styles else None
            # SWOLF per 100m = time + strokes
            swolf_100 = round(total_sec + total_strokes) if total_strokes else None
            swim_100m_sections.append({
                "num": gi + 1,
                "segment_m": (gi + 1) * 100,
                "duration_sec": round(total_sec, 1),
                "pace_per_100m_sec": round(pace_100, 1),
                "pace_str": f"{mm}:{ss:02d}/100m",
                "avg_hr": avg_hr,
                "hr_zone": _hr_zone(avg_hr, zones) if avg_hr else None,
                "stroke_count": total_strokes,
                "stroke_style": style,
                "swolf_100": swolf_100,
            })
        if len(swim_100m_sections) > 1:
            sections = swim_100m_sections

    # Remove internal timestamps before returning
    for sec in sections:
        sec.pop("_start_ts", None)
        sec.pop("_end_ts", None)

    result = {
        "discipline": disc,
        "sections": sections,
        "hr_zones": hr_zones,
        "hr_colored_segments": hr_colored_segments,
        "total_sections": len(sections),
        "total_distance_km": round(cumulative_dist_km, 2),
    }
    if swim_sets:
        result["swim_sets"] = swim_sets
    if swim_individual_laps:
        result["swim_laps"] = swim_individual_laps
    return result


def _compute_peak_efforts(rows: list, disc: str) -> dict | None:
    """Compute peak sustained efforts at key durations from time-series data.

    Uses a rolling-window approach over the time-series to find the best
    average power, HR, and pace at standard durations (5s, 1m, 5m, 20m, 60m).
    Returns dict with {durations: [{label, seconds, power, hr, pace_str}]}.
    """
    if not rows or disc not in ("run", "bike"):
        return None

    if disc == "run":
        speed_col = "RunningSpeed"
        power_col = "RunningPower"
    else:
        speed_col = "speed_mps"
        power_col = "CyclingPower"

    timestamps = []
    powers = []
    hrs = []
    speeds = []

    for r in rows:
        ts = r.get("timestamp", "")
        if ts.startswith("##"):
            continue
        p = _safe_float(r.get(power_col))
        h = _safe_float(r.get("HeartRate"))
        s = _safe_float(r.get(speed_col))
        if disc == "bike" and s > 0:
            s = s * 3.6
        elif disc == "run" and s > 0:
            pass  # already km/h
        powers.append(p)
        hrs.append(h)
        speeds.append(s)
        timestamps.append(ts)

    n = len(powers)
    if n < 5:
        return None

    # Estimate sample interval from data
    sample_interval = 3  # default ~3s
    target_durations = [
        ("5s", 5), ("1min", 60), ("5min", 300), ("20min", 1200), ("60min", 3600)
    ]

    results = []
    for label, dur_s in target_durations:
        window = max(1, dur_s // sample_interval)
        if window > n:
            continue

        best_power = 0.0
        best_hr = 0.0
        best_speed = 0.0

        # Rolling window for power
        power_vals = [p for p in powers if p > 0]
        if len(power_vals) >= window:
            running_sum = sum(powers[:window])
            best_power = running_sum / window
            for i in range(1, n - window + 1):
                running_sum += powers[i + window - 1] - powers[i - 1]
                avg = running_sum / window
                if avg > best_power:
                    best_power = avg

        # Rolling window for HR
        hr_vals = [h for h in hrs if h > 0]
        if len(hr_vals) >= window:
            running_sum = sum(hrs[:window])
            best_hr = running_sum / window
            for i in range(1, n - window + 1):
                running_sum += hrs[i + window - 1] - hrs[i - 1]
                avg = running_sum / window
                if avg > best_hr:
                    best_hr = avg

        # Rolling window for speed (best = fastest)
        speed_vals = [s for s in speeds if s > 0]
        if len(speed_vals) >= window:
            running_sum = sum(speeds[:window])
            best_speed = running_sum / window
            for i in range(1, n - window + 1):
                running_sum += speeds[i + window - 1] - speeds[i - 1]
                avg = running_sum / window
                if avg > best_speed:
                    best_speed = avg

        entry = {"label": label, "seconds": dur_s}
        if best_power > 0:
            entry["power"] = round(best_power)
        if best_hr > 0:
            entry["hr"] = round(best_hr)
        if best_speed > 0:
            if disc == "run" and best_speed > 0:
                secs_per_km = 3600 / best_speed
                m = int(secs_per_km // 60)
                s = int(secs_per_km % 60)
                entry["pace_str"] = f"{m}:{s:02d}/km"
            else:
                entry["speed_kmh"] = round(best_speed, 1)
        if len(entry) > 2:
            results.append(entry)

    if not results:
        return None

    peak = {"durations": results}

    # Estimate FTP from 20-min peak power (bike only)
    for r in results:
        if r["seconds"] == 1200 and r.get("power") and disc == "bike":
            peak["estimated_ftp"] = round(r["power"] * 0.95)
            break

    return peak


def _search_similar_intervals(data_dir: Path, disc: str, *,
                              min_dur_s: int = 0, max_dur_s: int = 9999,
                              interval_type: str = None,
                              days_back: int = 30) -> list:
    """Search precomputed .sections.json files for matching intervals.

    Scans recent workouts and returns intervals matching the filter criteria.
    Useful for finding all VO2max intervals, tempo segments, etc. across history.
    """
    from datetime import datetime, timedelta
    cutoff = (datetime.now() - timedelta(days=days_back)).strftime("%Y-%m-%d")

    workouts_dir = data_dir / "workouts"
    if not workouts_dir.exists():
        return []

    results = []
    for sections_file in sorted(workouts_dir.glob("**/workout_*.sections.json"), reverse=True):
        # Extract date from filename pattern: workout_NNN_YYYY-MM-DD_Type
        fname = sections_file.stem.replace(".sections", "")
        parts = fname.split("_")
        if len(parts) < 3:
            continue
        wnum = parts[1]
        wdate = parts[2] if len(parts) >= 3 else ""
        if wdate < cutoff:
            break  # sorted descending, so stop early

        try:
            with open(sections_file) as f:
                data = json.load(f)
        except (json.JSONDecodeError, OSError):
            continue

        if data.get("discipline") != disc:
            continue

        intervals = data.get("intervals", [])
        for iv in intervals:
            dur = iv.get("duration_sec", 0)
            if dur < min_dur_s or dur > max_dur_s:
                continue
            if interval_type and iv.get("type") != interval_type:
                continue
            results.append({
                "workout_num": int(wnum) if wnum.isdigit() else wnum,
                "date": wdate,
                "type": iv.get("type", ""),
                "duration_sec": dur,
                "pace_str": iv.get("pace_str"),
                "avg_speed_kmh": iv.get("avg_speed_kmh"),
                "avg_hr": iv.get("avg_hr"),
                "avg_power": iv.get("avg_power"),
                "distance_m": iv.get("distance_m"),
            })

    return results
