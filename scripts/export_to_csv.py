#!/usr/bin/env python3
"""
Apple Health Export to CSV converter for Ironman 70.3 training analysis.

Produces per-workout CSV files with all associated health data (heart rate,
cadence, power, pace, GPS route, etc.). Output goes into a fixed folder
called 'training_data'.

Supports incremental runs: on re-run after a new Apple Health export,
only new workouts are processed. A state file tracks what's been done.

Usage:
    python3 export_to_csv.py           # full or incremental run
    python3 export_to_csv.py --force   # force full rebuild

Exports Apple Health data to CSV files for analysis.
"""

import bisect
import csv
import json
import os
import sys
import xml.etree.ElementTree as ET
from collections import defaultdict
from concurrent.futures import ThreadPoolExecutor, as_completed
from datetime import datetime, timedelta, timezone
from pathlib import Path

# ── Config ──────────────────────────────────────────────────────────────────
BASE_DIR = Path(__file__).resolve().parent.parent  # ironCoach root (script lives in scripts/)
OUT_DIR = Path(os.environ.get("IRONCOACH_OUT_DIR", "")) if os.environ.get("IRONCOACH_OUT_DIR") else BASE_DIR / "training_data" / "users" / "1"
# Per-user: export.xml and workout-routes live inside OUT_DIR (copied there by import)
EXPORT_XML = OUT_DIR / "export.xml"
ROUTES_DIR = OUT_DIR / "workout-routes"
STATE_FILE = OUT_DIR / ".export_state.json"

SUMMARY_MAX_SIZE_MB = 97
SUMMARY_MAX_LINES = 1_000_000
GPS_TIME_BUFFER_MINUTES = 2
RECORD_SCAN_BATCH_SIZE = 200_000
MAX_WORKOUT_WRITERS = 4
# Record types that benefit from the time buffer (GPS sensor records)
_GPS_BUFFERED_TYPES = {
    "HKQuantityTypeIdentifierDistanceWalkingRunning",
    "HKQuantityTypeIdentifierDistanceCycling",
    "HKQuantityTypeIdentifierDistanceSwimming",
}

# Record types relevant to training (time-series data we want per workout)
TRAINING_RECORD_TYPES = {
    "HKQuantityTypeIdentifierHeartRate",
    "HKQuantityTypeIdentifierRunningSpeed",
    "HKQuantityTypeIdentifierRunningPower",
    "HKQuantityTypeIdentifierRunningStrideLength",
    "HKQuantityTypeIdentifierRunningGroundContactTime",
    "HKQuantityTypeIdentifierRunningVerticalOscillation",
    "HKQuantityTypeIdentifierCyclingPower",
    "HKQuantityTypeIdentifierCyclingCadence",
    "HKQuantityTypeIdentifierDistanceWalkingRunning",
    "HKQuantityTypeIdentifierDistanceCycling",
    "HKQuantityTypeIdentifierDistanceSwimming",
    "HKQuantityTypeIdentifierSwimmingStrokeCount",
    "HKQuantityTypeIdentifierStepCount",
    "HKQuantityTypeIdentifierActiveEnergyBurned",
    "HKQuantityTypeIdentifierBasalEnergyBurned",
    "HKQuantityTypeIdentifierRespiratoryRate",
    "HKQuantityTypeIdentifierVO2Max",
    "HKQuantityTypeIdentifierWaterTemperature",
    "HKQuantityTypeIdentifierOxygenSaturation",
    "HKQuantityTypeIdentifierWalkingSpeed",
    "HKQuantityTypeIdentifierWalkingStepLength",
    "HKQuantityTypeIdentifierPhysicalEffort",
    "HKQuantityTypeIdentifierFlightsClimbed",
    "HKQuantityTypeIdentifierHeartRateVariabilitySDNN",
}

# Body metrics, daily aggregates, recovery types
BODY_METRIC_TYPES = {
    "HKQuantityTypeIdentifierBodyMass",
    "HKQuantityTypeIdentifierBodyFatPercentage",
    "HKQuantityTypeIdentifierBodyMassIndex",
    "HKQuantityTypeIdentifierLeanBodyMass",
}

DAILY_AGGREGATE_TYPES = {
    "HKQuantityTypeIdentifierStepCount",
    "HKQuantityTypeIdentifierActiveEnergyBurned",
    "HKQuantityTypeIdentifierBasalEnergyBurned",
    "HKQuantityTypeIdentifierDistanceWalkingRunning",
}

RECOVERY_TYPES = {
    "HKQuantityTypeIdentifierRestingHeartRate",
    "HKQuantityTypeIdentifierHeartRateVariabilitySDNN",
    "HKCategoryTypeIdentifierSleepAnalysis",
}

# Union of all record types we care about
ALL_RECORD_TYPES = TRAINING_RECORD_TYPES | BODY_METRIC_TYPES | DAILY_AGGREGATE_TYPES | RECOVERY_TYPES

# Pre-compute routing: which collectors each type goes to (a record can hit multiple)
_TYPE_ROUTING = {}
for _t in ALL_RECORD_TYPES:
    _routes = 0  # bit flags: 1=training, 2=body, 4=daily, 8=recovery
    if _t in TRAINING_RECORD_TYPES:
        _routes |= 1
    if _t in BODY_METRIC_TYPES:
        _routes |= 2
    if _t in DAILY_AGGREGATE_TYPES:
        _routes |= 4
    if _t in RECOVERY_TYPES:
        _routes |= 8
    _TYPE_ROUTING[_t] = _routes

# Daily aggregate metric mapping
_DAILY_METRIC_MAP = {
    "HKQuantityTypeIdentifierStepCount": "steps",
    "HKQuantityTypeIdentifierActiveEnergyBurned": "active_cal",
    "HKQuantityTypeIdentifierBasalEnergyBurned": "basal_cal",
    "HKQuantityTypeIdentifierDistanceWalkingRunning": "walk_run_km",
}

DATE_FMT = "%Y-%m-%d %H:%M:%S %z"

# Pre-computed timezone cache for fast_parse_date
_TZ_CACHE = {}

# ── Helpers ─────────────────────────────────────────────────────────────────

def parse_date(s):
    """Fast date parser for Apple Health format: '2025-12-08 08:21:42 +0300'.

    ~10x faster than datetime.strptime by using direct string slicing.
    Falls back to strptime for unexpected formats.
    """
    if not s:
        return None
    try:
        # Expected: "YYYY-MM-DD HH:MM:SS +HHMM" (len 25)
        if len(s) >= 25 and s[4] == '-' and s[10] == ' ':
            tz_str = s[20:25]  # e.g. "+0300"
            tz = _TZ_CACHE.get(tz_str)
            if tz is None:
                sign = 1 if tz_str[0] == '+' else -1
                tz = timezone(timedelta(hours=sign * int(tz_str[1:3]), minutes=sign * int(tz_str[3:5])))
                _TZ_CACHE[tz_str] = tz
            return datetime(
                int(s[0:4]), int(s[5:7]), int(s[8:10]),
                int(s[11:13]), int(s[14:16]), int(s[17:19]),
                tzinfo=tz
            )
        return datetime.strptime(s, DATE_FMT)
    except (ValueError, TypeError, IndexError):
        return None


def short_type(t):
    for prefix in ("HKQuantityTypeIdentifier", "HKCategoryTypeIdentifier", "HKDataType"):
        if t.startswith(prefix):
            return t[len(prefix):]
    return t


def format_workout_type(t):
    return t.replace("HKWorkoutActivityType", "")


def workout_key(w):
    """Unique key for a workout: startDate + type + duration."""
    return f"{w.get('startDate', '')}|{w.get('workoutActivityType', '')}|{w.get('duration', '')}"


def workout_filename(global_idx, workout):
    """Deterministic path for a workout: workouts/YYYY-MM/workout_NNN_DATE_TYPE.csv"""
    w_type = format_workout_type(workout.get("workoutActivityType", "Unknown"))
    w_start = workout.get("startDate", "")[:10]
    month_dir = w_start[:7]  # YYYY-MM
    return f"workouts/{month_dir}/workout_{global_idx + 1:03d}_{w_start}_{w_type}.csv"


def _is_watch_source(source_name: str) -> bool:
    """Check if a source name is from Apple Watch."""
    return "watch" in (source_name or "").lower()


# ── State management ────────────────────────────────────────────────────────

def load_state():
    """Load previous run state, or return empty state."""
    if STATE_FILE.exists():
        try:
            with open(STATE_FILE) as f:
                return json.load(f)
        except (json.JSONDecodeError, IOError):
            pass
    return {"processed_workouts": {}, "summary_lines": 0, "last_run": None}


def save_state(state):
    """Save state atomically to prevent corruption on crash."""
    import tempfile
    STATE_FILE.parent.mkdir(exist_ok=True)
    tmp_fd, tmp_path = tempfile.mkstemp(dir=STATE_FILE.parent, suffix=".json")
    try:
        with os.fdopen(tmp_fd, 'w') as f:
            json.dump(state, f, indent=2)
        os.replace(tmp_path, STATE_FILE)
    except Exception:
        # Clean up temp file on error
        try:
            os.unlink(tmp_path)
        except Exception:
            pass
        raise


# ── GPX parser ──────────────────────────────────────────────────────────────

def parse_gpx(gpx_path):
    ns = {"gpx": "http://www.topografix.com/GPX/1/1"}
    points = []
    try:
        tree = ET.parse(gpx_path)
    except Exception as e:
        print(f"  Warning: could not parse {gpx_path}: {e}")
        return points

    root = tree.getroot()
    for trkpt in root.findall(".//gpx:trkpt", ns):
        pt = {
            "lat": trkpt.get("lat"),
            "lon": trkpt.get("lon"),
        }
        ele = trkpt.find("gpx:ele", ns)
        if ele is not None and ele.text:
            pt["elevation_m"] = ele.text
        time_el = trkpt.find("gpx:time", ns)
        if time_el is not None and time_el.text:
            pt["time"] = time_el.text
        ext = trkpt.find("gpx:extensions", ns)
        if ext is not None:
            for child in ext:
                tag = child.tag.split("}")[-1] if "}" in child.tag else child.tag
                if child.text:
                    pt[tag] = child.text
        points.append(pt)
    return points


# ── Pass 1: Parse workouts ──────────────────────────────────────────────────

def parse_workouts():
    """Stream export.xml and extract all Workout elements."""
    print("Pass 1: Scanning workouts from export.xml ...")
    workouts = []

    for event, elem in ET.iterparse(str(EXPORT_XML), events=("end",)):
        if elem.tag != "Workout":
            if elem.tag in ("Record", "ActivitySummary", "Correlation"):
                elem.clear()
            continue

        w = dict(elem.attrib)
        w["metadata"] = {}
        w["events"] = []
        w["statistics"] = []
        w["route_file"] = None

        for child in elem:
            if child.tag == "MetadataEntry":
                w["metadata"][child.get("key", "")] = child.get("value", "")
            elif child.tag == "WorkoutEvent":
                ev = dict(child.attrib)
                ev["_metadata"] = {}
                for sub in child:
                    if sub.tag == "MetadataEntry":
                        ev["_metadata"][sub.get("key", "")] = sub.get("value", "")
                w["events"].append(ev)
            elif child.tag == "WorkoutStatistics":
                w["statistics"].append(dict(child.attrib))
            elif child.tag == "WorkoutRoute":
                for sub in child:
                    if sub.tag == "FileReference":
                        w["route_file"] = sub.get("path", "")
        workouts.append(w)
        elem.clear()

    print(f"  Found {len(workouts)} workouts total")
    return workouts


# ── Pass 2: Single-pass record scan (training + body + daily + recovery) ──

def scan_all_records(workouts, target_indices):
    """Single-pass scan of all Record elements in export.xml.

    Routes each record to the appropriate collector(s) based on type.
    Streaming: uses iterparse + elem.clear() — never loads full XML into memory.

    Returns:
        (workout_records, body_metrics, daily_aggregates, recovery_data)
    """
    # ── Training collector setup ──
    has_targets = bool(target_indices)
    target_set = set(target_indices) if has_targets else set()
    BUFFER = timedelta(minutes=GPS_TIME_BUFFER_MINUTES)
    workout_ranges = []
    if has_targets:
        for i, w in enumerate(workouts):
            if i not in target_set:
                continue
            start = parse_date(w.get("startDate"))
            end = parse_date(w.get("endDate"))
            if start and end:
                workout_ranges.append((start, end, start - BUFFER, end + BUFFER, i))
        workout_ranges.sort(key=lambda x: x[2])
    earliest = workout_ranges[0][2] if workout_ranges else None
    # Pre-compute sorted buf_start list for bisect (O(log N) lookup instead of linear)
    _range_buf_starts = [r[2] for r in workout_ranges]
    # String prefix of earliest date for fast pre-filter (avoids parse_date for old records)
    # Apple Health dates sort lexicographically within same timezone
    _earliest_str = earliest.strftime("%Y-%m-%d %H:%M:%S") if earliest else ""
    # Latest buffered end for fast upper-bound skip
    _latest_end = workout_ranges[-1][3] if workout_ranges else None
    _latest_str = _latest_end.strftime("%Y-%m-%d %H:%M:%S") if _latest_end else ""

    # ── Accumulators ──
    workout_records = defaultdict(list)
    body_metrics = []
    daily_raw = defaultdict(lambda: defaultdict(list))
    rhr_daily = defaultdict(list)
    hrv_daily = defaultdict(list)
    sleep_segments = defaultdict(list)

    targets_str = f" ({len(target_indices)} new workouts)" if has_targets else ""
    print(f"Pass 2: Single-pass scan of all records{targets_str} ...")
    if earliest:
        print(f"  Earliest new workout: {earliest.strftime(DATE_FMT)}")

    processed = 0

    for event, elem in ET.iterparse(str(EXPORT_XML), events=("end",)):
        tag = elem.tag
        if tag != "Record":
            if tag in ("Workout", "ActivitySummary", "Correlation"):
                elem.clear()
            continue

        processed += 1
        rtype = elem.get("type", "")
        routes = _TYPE_ROUTING.get(rtype)

        if not routes:
            if processed % RECORD_SCAN_BATCH_SIZE == 0:
                print(f"  Scanned {processed:,} records ...")
            elem.clear()
            continue

        start_str = elem.get("startDate", "")

        # Fast skip: training-only records outside workout time range (string compare, no date parse)
        # Safe because we still check every record — just avoids expensive parse_date call
        if routes == 1 and start_str:
            date_prefix = start_str[:19]  # "YYYY-MM-DD HH:MM:SS"
            if date_prefix < _earliest_str or date_prefix > _latest_str:
                if processed % RECORD_SCAN_BATCH_SIZE == 0:
                    print(f"  Scanned {processed:,} records ...")
                elem.clear()
                continue

        # ── Training records (route flag bit 1) ──
        if routes & 1 and has_targets and start_str:
            rec_start = parse_date(start_str)
            if rec_start and earliest and rec_start >= earliest:
                use_buffer = rtype in _GPS_BUFFERED_TYPES
                # Use bisect to find candidate range (O(log N) instead of O(N))
                pos = bisect.bisect_right(_range_buf_starts, rec_start) - 1
                # Check nearby ranges — workouts can overlap (back-to-back sessions, ±2 distance)
                for j in range(max(0, pos - 2), min(pos + 3, len(workout_ranges))):
                    exact_start, exact_end, buf_start, buf_end, w_idx = workout_ranges[j]
                    lo = buf_start if use_buffer else exact_start
                    hi = buf_end if use_buffer else exact_end
                    if rec_start < lo or rec_start > hi:
                        continue
                    rec_data = {
                        "type": rtype,
                        "value": elem.get("value", ""),
                        "unit": elem.get("unit", ""),
                        "startDate": start_str,
                        "endDate": elem.get("endDate", ""),
                        "sourceName": elem.get("sourceName", ""),
                    }
                    if rtype == "HKQuantityTypeIdentifierSwimmingStrokeCount":
                        for child in elem:
                            if child.tag == "MetadataEntry" and child.get("key") == "HKSwimmingStrokeStyle":
                                rec_data["stroke_style"] = child.get("value", "")
                    workout_records[w_idx].append(rec_data)
                    break

        # ── Body metrics (route flag bit 2) ──
        if routes & 2:
            body_metrics.append({
                "date": start_str[:10],
                "datetime": start_str,
                "type": short_type(rtype),
                "value": elem.get("value", ""),
                "unit": elem.get("unit", ""),
                "sourceName": elem.get("sourceName", ""),
            })

        # ── Daily aggregates (route flag bit 4) ──
        if routes & 4:
            metric = _DAILY_METRIC_MAP.get(rtype)
            if metric:
                date = start_str[:10]
                val = float(elem.get("value", 0) or 0)
                if metric == "walk_run_km":
                    unit = elem.get("unit", "km")
                    if unit == "m":
                        val /= 1000
                is_watch = _is_watch_source(elem.get("sourceName"))
                daily_raw[date][metric].append((start_str, elem.get("endDate", ""), val, is_watch))

        # ── Recovery (route flag bit 8) ──
        if routes & 8:
            if rtype == "HKQuantityTypeIdentifierRestingHeartRate":
                date = start_str[:10]
                val = float(elem.get("value", 0) or 0)
                if val > 0:
                    rhr_daily[date].append(val)
            elif rtype == "HKQuantityTypeIdentifierHeartRateVariabilitySDNN":
                date = start_str[:10]
                val = float(elem.get("value", 0) or 0)
                if val > 0:
                    hrv_daily[date].append(val)
            elif rtype == "HKCategoryTypeIdentifierSleepAnalysis":
                end_str = elem.get("endDate", "")
                value = elem.get("value", "")
                if start_str and end_str:
                    start = parse_date(start_str)
                    end = parse_date(end_str)
                    if start and end:
                        date = end_str[:10]
                        stage = value.replace("HKCategoryValueSleepAnalysis", "")
                        duration_min = (end - start).total_seconds() / 60
                        sleep_segments[date].append((start_str, end_str, stage, duration_min))

        if processed % RECORD_SCAN_BATCH_SIZE == 0:
            print(f"  Scanned {processed:,} records ...")
        elem.clear()

    matched = sum(len(v) for v in workout_records.values())
    print(f"  Scanned {processed:,} records, matched {matched:,} to new workouts")
    print(f"  Body: {len(body_metrics)}, Daily: {len(daily_raw)} days, "
          f"Recovery: RHR {len(rhr_daily)}, HRV {len(hrv_daily)}, Sleep {len(sleep_segments)}")

    # ── Post-process daily aggregates ──
    daily_agg = {}
    for date, metrics in daily_raw.items():
        daily_agg[date] = {
            "steps": int(_dedup_records(metrics.get("steps", []))),
            "active_cal": round(_dedup_records(metrics.get("active_cal", [])), 1),
            "basal_cal": round(_dedup_records(metrics.get("basal_cal", [])), 1),
            "walk_run_km": round(_dedup_records(metrics.get("walk_run_km", [])), 2),
        }

    # ── Post-process recovery ──
    recovery = {}
    all_recovery_dates = set(rhr_daily.keys()) | set(hrv_daily.keys()) | set(sleep_segments.keys())
    for date in sorted(all_recovery_dates):
        row = {"date": date}
        if date in rhr_daily:
            vals = rhr_daily[date]
            row["resting_hr"] = round(sum(vals) / len(vals), 1)
        if date in hrv_daily:
            vals = hrv_daily[date]
            row["hrv_sdnn_ms"] = round(sum(vals) / len(vals), 1)
        if date in sleep_segments:
            segs = sleep_segments[date]
            total = deep = core = rem = awake = 0
            for _, _, stage, dur in segs:
                if "InBed" in stage:
                    continue
                if "Awake" in stage:
                    awake += dur
                elif "Deep" in stage:
                    deep += dur
                    total += dur
                elif "REM" in stage:
                    rem += dur
                    total += dur
                elif "Core" in stage or "Asleep" in stage:
                    core += dur
                    total += dur
            if total > 0:
                row["sleep_total_min"] = round(total)
                row["sleep_deep_min"] = round(deep)
                row["sleep_core_min"] = round(core)
                row["sleep_rem_min"] = round(rem)
                row["sleep_awake_min"] = round(awake)
        recovery[date] = row

    return workout_records, body_metrics, daily_agg, recovery


# ── Segment chain extraction ───────────────────────────────────────────────

# Segment chain matching constants
SEGMENT_TIME_MATCH_THRESHOLD_SEC = 5
SEGMENT_TIME_MATCH_TOLERANCE_SEC = 1
MIN_DURATION_FOR_RATIO = 0.001
INITIAL_BEST_DIFF = 999999
INITIAL_BEST_DUR_RATIO = 999999

def _extract_segment_chains(segments):
    """Separate interleaved km and mile segment splits from Apple Watch.

    Apple exports km and mile splits interleaved, sorted by start time.
    We separate them by building chains where each segment's start = prev start + prev duration.
    Returns dict with 'km' and 'mile' keys, each a list of {duration_min, date}.
    """
    def _parse_seg_time(s):
        try:
            return datetime.strptime(s[:19], "%Y-%m-%d %H:%M:%S")
        except (ValueError, TypeError):
            return None

    if not segments:
        return None

    # Build chains: greedily assign each segment to the chain whose expected next start is closest
    chains = []  # list of lists of segments

    for seg in segments:
        seg_start = _parse_seg_time(seg["date"])
        if not seg_start:
            continue
        seg_dur = seg["duration_min"]

        # Try to find a chain whose expected next time matches this segment's start
        # When multiple chains match (e.g. km and 5km boundaries coincide),
        # prefer the chain whose avg duration is most compatible with this segment
        best_chain = None
        best_diff = INITIAL_BEST_DIFF
        best_dur_ratio = INITIAL_BEST_DUR_RATIO
        for chain in chains:
            last = chain[-1]
            expected = _parse_seg_time(last["date"]) + timedelta(minutes=last["duration_min"])
            diff = abs((seg_start - expected).total_seconds())
            if diff >= SEGMENT_TIME_MATCH_THRESHOLD_SEC:
                continue
            avg_dur = sum(c["duration_min"] for c in chain) / len(chain)
            min_d = min(seg_dur, avg_dur) if min(seg_dur, avg_dur) > 0 else MIN_DURATION_FOR_RATIO
            dur_ratio = max(seg_dur, avg_dur) / min_d
            if diff < best_diff - SEGMENT_TIME_MATCH_TOLERANCE_SEC:  # clearly better time match
                best_diff = diff
                best_dur_ratio = dur_ratio
                best_chain = chain
            elif abs(diff - best_diff) <= SEGMENT_TIME_MATCH_TOLERANCE_SEC and dur_ratio < best_dur_ratio:
                best_diff = diff
                best_dur_ratio = dur_ratio
                best_chain = chain

        if best_chain is not None:
            best_chain.append(seg)
        else:
            # Start a new chain
            chains.append([seg])

    if not chains:
        return None

    # Sort chains by length (descending) — longest = km splits
    chains.sort(key=len, reverse=True)

    result = {}
    if len(chains) >= 1:
        result["km"] = chains[0]
    if len(chains) >= 2:
        result["mile"] = chains[1]

    return result


# ── Write per-workout CSV ───────────────────────────────────────────────────

def _deduplicate_records_by_source(records):
    """Remove duplicate records from secondary sources (iPhone) when Watch data exists.

    Deduplicates distance and step count types — these are the ones where iPhone sends
    duplicate data that inflates totals. Other types (HeartRate, Power, Cadence, etc.)
    are kept from all sources since they don't have the duplication problem.
    """
    DEDUP_TYPES = {
        "HKQuantityTypeIdentifierDistanceWalkingRunning",
        "HKQuantityTypeIdentifierDistanceCycling",
        "HKQuantityTypeIdentifierDistanceSwimming",
        "HKQuantityTypeIdentifierStepCount",
    }

    by_type = {}
    for rec in records:
        rtype = rec["type"]
        if rtype not in DEDUP_TYPES:
            continue
        is_watch = _is_watch_source(rec.get("sourceName"))
        if rtype not in by_type:
            by_type[rtype] = {"has_watch": False, "non_watch_ids": set()}
        if is_watch:
            by_type[rtype]["has_watch"] = True
        else:
            by_type[rtype]["non_watch_ids"].add(id(rec))

    drop_ids = set()
    for rtype, info in by_type.items():
        if info["has_watch"] and info["non_watch_ids"]:
            drop_ids.update(info["non_watch_ids"])

    if drop_ids:
        print(f"    Source dedup: dropped {len(drop_ids)} non-Watch records (Watch data preferred)")

    return [rec for rec in records if id(rec) not in drop_ids]


def write_workout_csv(out_dir, global_idx, workout, records, gpx_points):
    w_type = format_workout_type(workout.get("workoutActivityType", "Unknown"))
    fname = workout_filename(global_idx, workout)
    fpath = out_dir / fname
    fpath.parent.mkdir(parents=True, exist_ok=True)

    # Deduplicate records: prefer Watch over iPhone for same record types
    records = _deduplicate_records_by_source(records)

    # Build unified time-series
    time_series = {}

    for pt in gpx_points:
        t = pt.get("time", "")
        if t not in time_series:
            time_series[t] = {"timestamp": t}
        time_series[t]["lat"] = pt.get("lat", "")
        time_series[t]["lon"] = pt.get("lon", "")
        time_series[t]["elevation_m"] = pt.get("elevation_m", "")
        time_series[t]["speed_mps"] = pt.get("speed", "")
        time_series[t]["course_deg"] = pt.get("course", "")
        time_series[t]["h_accuracy"] = pt.get("hAcc", "")
        time_series[t]["v_accuracy"] = pt.get("vAcc", "")

    for rec in records:
        t = rec["startDate"]
        if t not in time_series:
            time_series[t] = {"timestamp": t}
        col_name = short_type(rec["type"])
        time_series[t][col_name] = rec["value"]
        time_series[t][f"{col_name}_unit"] = rec["unit"]
        # Add stroke style for swim stroke count records
        if "stroke_style" in rec:
            time_series[t]["SwimmingStrokeStyle"] = rec["stroke_style"]

    def _parse_any_ts(ts):
        """Parse Apple-format or ISO-format timestamps for sorting."""
        if not ts:
            return None
        # Try Apple Health format first (most records)
        parsed = parse_date(ts)
        if parsed:
            return parsed
        # Try ISO format (GPX points: 2026-02-16T09:13:30Z)
        try:
            return datetime.fromisoformat(ts.replace("Z", "+00:00"))
        except (ValueError, AttributeError):
            return None

    def _sort_key(r):
        parsed = _parse_any_ts(r.get("timestamp", ""))
        if parsed:
            return (0, parsed)
        # Unparseable timestamps sort last
        return (1, datetime.min)
    rows = sorted(time_series.values(), key=_sort_key)

    all_cols = set()
    for r in rows:
        all_cols.update(r.keys())

    priority = ["timestamp", "lat", "lon", "elevation_m", "speed_mps",
                "course_deg", "h_accuracy", "v_accuracy"]
    ordered_cols = [c for c in priority if c in all_cols]
    remaining = sorted(all_cols - set(ordered_cols))
    value_cols = [c for c in remaining if not c.endswith("_unit")]
    unit_cols = [c for c in remaining if c.endswith("_unit")]
    ordered_cols += value_cols + unit_cols

    # Summary header rows
    summary_rows = []
    summary_rows.append({"timestamp": "## WORKOUT SUMMARY"})
    summary_rows.append({"timestamp": f"## Type: {w_type}"})
    summary_rows.append({"timestamp": f"## Start: {workout.get('startDate', '')}"})
    summary_rows.append({"timestamp": f"## End: {workout.get('endDate', '')}"})
    summary_rows.append({"timestamp": f"## Duration: {workout.get('duration', '')} {workout.get('durationUnit', '')}"})
    summary_rows.append({"timestamp": f"## Source: {workout.get('sourceName', '')}"})

    for stat in workout.get("statistics", []):
        stype = short_type(stat.get("type", ""))
        parts = []
        for k in ("sum", "average", "minimum", "maximum"):
            if stat.get(k):
                parts.append(f"{k}={stat[k]}")
        summary_rows.append({
            "timestamp": f"## Stat: {stype} ({stat.get('unit', '')}) {', '.join(parts)}"
        })

    for k, v in workout.get("metadata", {}).items():
        key_short = k.replace("HK", "")
        summary_rows.append({"timestamp": f"## Meta: {key_short} = {v}"})

    segments = [e for e in workout.get("events", [])
                if e.get("type") == "HKWorkoutEventTypeSegment"]
    pauses = [e for e in workout.get("events", [])
              if e.get("type") == "HKWorkoutEventTypePause"]
    if segments:
        summary_rows.append({"timestamp": f"## Segments: {len(segments)}"})
    if pauses:
        summary_rows.append({"timestamp": f"## Pauses: {len(pauses)}"})

    summary_rows.append({"timestamp": f"## Data points: {len(rows)}"})
    summary_rows.append({"timestamp": "## ---"})

    all_rows = summary_rows + rows

    with open(fpath, "w", newline="") as f:
        writer = csv.DictWriter(f, fieldnames=ordered_cols, extrasaction="ignore")
        writer.writeheader()
        for row in all_rows:
            writer.writerow(row)

    # Write swim events (segments + laps) as JSON for swim workouts
    if "Swimming" in workout.get("workoutActivityType", ""):
        events = workout.get("events", [])
        if events:
            swim_events = []
            for ev in events:
                entry = {
                    "type": ev.get("type", ""),
                    "date": ev.get("date", ""),
                    "duration_min": float(ev.get("duration", 0)),
                    "durationUnit": ev.get("durationUnit", "min"),
                }
                meta = ev.get("_metadata", {})
                if meta.get("HKSwimmingStrokeStyle"):
                    entry["stroke_style"] = int(meta["HKSwimmingStrokeStyle"])
                if meta.get("HKSWOLFScore"):
                    entry["swolf"] = round(float(meta["HKSWOLFScore"]))
                swim_events.append(entry)
            events_path = fpath.with_suffix(".events.json")
            with open(events_path, "w") as ef:
                json.dump(swim_events, ef, indent=2)

    # Write km/mile segment splits (Apple Watch provides exact splits for distance workouts)
    events = workout.get("events", [])
    segments = [ev for ev in events if ev.get("type") == "HKWorkoutEventTypeSegment"]
    if segments:
        seg_list = [{"date": ev.get("date", ""), "duration_min": float(ev.get("duration", 0))}
                    for ev in segments]
        chains = _extract_segment_chains(seg_list)
        if chains:
            splits_path = fpath.with_name(fpath.stem + ".splits.json")
            with open(splits_path, "w") as sf:
                json.dump(chains, sf, indent=2)

    return fname, len(rows)


# ── Write / update workouts summary ────────────────────────────────────────

def build_summary_row(global_idx, w):
    """Build one summary dict for a workout."""
    row = {
        "workout_num": global_idx + 1,
        "type": format_workout_type(w.get("workoutActivityType", "")),
        "startDate": w.get("startDate", ""),
        "endDate": w.get("endDate", ""),
        "duration_min": w.get("duration", ""),
        "sourceName": w.get("sourceName", ""),
        "has_route": "yes" if w.get("route_file") else "no",
    }
    for stat in w.get("statistics", []):
        stype = short_type(stat.get("type", ""))
        for k in ("sum", "average", "minimum", "maximum"):
            if stat.get(k):
                row[f"{stype}_{k}"] = stat[k]
        if stat.get("unit"):
            row[f"{stype}_unit"] = stat["unit"]
    for k, v in w.get("metadata", {}).items():
        key_short = k.replace("HK", "")
        row[f"meta_{key_short}"] = v
    return row


def write_full_summary(out_dir, workouts):
    """Write the complete summary CSV from scratch."""
    fpath = out_dir / "00_workouts_summary.csv"
    rows = [build_summary_row(i, w) for i, w in enumerate(workouts)]

    all_cols = set()
    for r in rows:
        all_cols.update(r.keys())
    priority = ["workout_num", "type", "startDate", "endDate", "duration_min",
                "sourceName", "has_route"]
    ordered = [c for c in priority if c in all_cols]
    ordered += sorted(all_cols - set(ordered))

    with open(fpath, "w", newline="") as f:
        writer = csv.DictWriter(f, fieldnames=ordered, extrasaction="ignore")
        writer.writeheader()
        writer.writerows(rows)

    line_count = len(rows) + 1  # +1 for header
    print(f"  Written: {fpath.name} ({len(rows)} workouts)")
    return line_count


def can_append_summary(out_dir, new_count, workouts=None, new_indices=None):
    """Check if appending to summary stays within limits."""
    fpath = out_dir / "00_workouts_summary.csv"
    if not fpath.exists():
        return False  # Need to write from scratch

    size_mb = fpath.stat().st_size / (1024 * 1024)
    if size_mb > SUMMARY_MAX_SIZE_MB:
        return False

    with open(fpath) as f:
        existing_header = f.readline().strip().split(',')
        existing_lines = 1 + sum(1 for _ in f)
    if existing_lines + new_count > SUMMARY_MAX_LINES:
        return False

    if workouts is not None and new_indices is not None:
        new_cols = set()
        for i in new_indices:
            new_cols.update(build_summary_row(i, workouts[i]).keys())
        if not new_cols.issubset(set(existing_header)):
            extra = new_cols - set(existing_header)
            print(f"  CSV column mismatch — {len(extra)} new columns detected, forcing full rewrite")
            return False

    return True


def append_to_summary(out_dir, workouts, new_indices):
    """Append only new workout rows to the existing summary."""
    fpath = out_dir / "00_workouts_summary.csv"

    # Read existing header to get column order
    with open(fpath) as f:
        reader = csv.DictReader(f)
        existing_cols = list(reader.fieldnames) if reader.fieldnames else []

    # Build new rows
    new_rows = [build_summary_row(i, workouts[i]) for i in sorted(new_indices)]

    # Check if new rows introduce new columns
    new_cols = set()
    for r in new_rows:
        new_cols.update(r.keys())
    extra_cols = new_cols - set(existing_cols)

    if extra_cols:
        # New columns detected — must rewrite the whole file
        print(f"  Summary has new columns, rewriting full file ...")
        line_count = write_full_summary(out_dir, workouts)
        return line_count

    # Append
    with open(fpath, "a", newline="") as f:
        writer = csv.DictWriter(f, fieldnames=existing_cols, extrasaction="ignore")
        for row in new_rows:
            writer.writerow(row)

    with open(fpath) as f:
        line_count = sum(1 for _ in f)
    print(f"  Appended {len(new_rows)} workouts to {fpath.name} (total {line_count - 1} workouts)")
    return line_count


# ── Write body metrics ──────────────────────────────────────────────────────

def write_body_metrics(out_dir, metrics):
    """Write body_metrics.csv. Preserves rows from non-Apple-Health sources (e.g. IronCoach)."""
    if not metrics:
        return
    fpath = out_dir / "body_metrics.csv"
    cols = ["date", "datetime", "type", "value", "unit", "sourceName"]

    # Preserve manually-added rows (from IronCoach, etc.) that aren't in Apple Health
    apple_sources = {"eufy Life", "Health", "Apple Watch", "iPhone"}
    preserved = []
    if fpath.exists():
        with open(fpath, newline="") as f:
            reader = csv.DictReader(f)
            for row in reader:
                if row.get("sourceName", "") not in apple_sources:
                    preserved.append(dict(row))

    all_rows = metrics + preserved
    with open(fpath, "w", newline="") as f:
        writer = csv.DictWriter(f, fieldnames=cols)
        writer.writeheader()
        writer.writerows(sorted(all_rows, key=lambda r: r.get("datetime", "")))
    extra = f" (+{len(preserved)} preserved)" if preserved else ""
    print(f"  Written: {fpath.name} ({len(all_rows)} records{extra})")


# ── Write daily aggregates ──────────────────────────────────────────────────

def _dedup_records(records):
    """Deduplicate health records by source priority (Watch > others).

    Records: list of (start_str, end_str, value, is_watch).
    Returns deduplicated sum. Watch records always kept; others kept only
    if they don't overlap with any Watch time interval.
    """
    watch_intervals = []
    watch_sum = 0.0
    other_records = []

    for start_s, end_s, val, is_watch in records:
        if is_watch:
            watch_sum += val
            try:
                s = datetime.strptime(start_s[:19], "%Y-%m-%d %H:%M:%S")
                e = datetime.strptime(end_s[:19], "%Y-%m-%d %H:%M:%S")
                watch_intervals.append((s, e))
            except (ValueError, TypeError):
                pass
        else:
            other_records.append((start_s, end_s, val))

    if not watch_intervals:
        return sum(r[2] for r in other_records)

    # Sort watch intervals for efficient overlap check
    watch_intervals.sort()

    # Merge overlapping watch intervals
    merged = [watch_intervals[0]]
    for s, e in watch_intervals[1:]:
        if s <= merged[-1][1]:
            merged[-1] = (merged[-1][0], max(merged[-1][1], e))
        else:
            merged.append((s, e))

    # Add non-watch records that don't overlap with any watch interval
    other_sum = 0.0
    for start_s, end_s, val in other_records:
        try:
            s = datetime.strptime(start_s[:19], "%Y-%m-%d %H:%M:%S")
            e = datetime.strptime(end_s[:19], "%Y-%m-%d %H:%M:%S")
        except (ValueError, TypeError):
            continue
        overlaps = False
        for ws, we in merged:
            if s < we and e > ws:
                overlaps = True
                break
        if not overlaps:
            other_sum += val

    return watch_sum + other_sum


def write_daily_aggregates(out_dir, daily):
    """Write daily_aggregates.csv."""
    if not daily:
        return
    fpath = out_dir / "daily_aggregates.csv"
    cols = ["date", "steps", "active_cal", "basal_cal", "walk_run_km"]
    rows = sorted([{"date": d, **v} for d, v in daily.items()], key=lambda r: r["date"])
    with open(fpath, "w", newline="") as f:
        writer = csv.DictWriter(f, fieldnames=cols)
        writer.writeheader()
        for row in rows:
            row["active_cal"] = round(row["active_cal"], 1)
            row["basal_cal"] = round(row["basal_cal"], 1)
            row["walk_run_km"] = round(row["walk_run_km"], 2)
            writer.writerow(row)
    print(f"  Written: {fpath.name} ({len(rows)} days)")


# ── Write recovery data ────────────────────────────────────────────────────

def write_recovery_data(out_dir, daily):
    """Write recovery_data.csv."""
    if not daily:
        return
    fpath = out_dir / "recovery_data.csv"
    cols = ["date", "resting_hr", "hrv_sdnn_ms", "sleep_total_min", "sleep_deep_min",
            "sleep_core_min", "sleep_rem_min", "sleep_awake_min"]
    rows = sorted(daily.values(), key=lambda r: r["date"])
    with open(fpath, "w", newline="") as f:
        writer = csv.DictWriter(f, fieldnames=cols, extrasaction="ignore")
        writer.writeheader()
        for row in rows:
            writer.writerow(row)
    print(f"  Written: {fpath.name} ({len(rows)} days)")


# ── Per-workout processing (for parallel execution) ────────────────────────

def _process_one_workout(out_dir, i, workout, records, base_dir):
    """Process a single workout: parse GPX + write CSV. Thread-safe."""
    gpx_points = []
    route_file = workout.get("route_file")
    if route_file:
        gpx_path = out_dir / route_file.lstrip("/")
        if not gpx_path.exists():
            gpx_path = base_dir / "training_data" / route_file.lstrip("/")
        if gpx_path.exists():
            gpx_points = parse_gpx(str(gpx_path))

    fname, npoints = write_workout_csv(out_dir, i, workout, records, gpx_points)
    return i, fname, npoints, len(records), len(gpx_points)


# ── Main ────────────────────────────────────────────────────────────────────

def main():
    force = "--force" in sys.argv

    if not EXPORT_XML.exists():
        print(f"Error: {EXPORT_XML} not found")
        sys.exit(1)

    OUT_DIR.mkdir(exist_ok=True)
    state = load_state()

    if force:
        print("Force mode: rebuilding everything from scratch\n")
        state = {"processed_workouts": {}, "summary_lines": 0, "last_run": None}

    previously_processed = set(state.get("processed_workouts", {}).keys())
    if previously_processed:
        print(f"Previous run found: {len(previously_processed)} workouts already processed")
    else:
        print("No previous state found — full processing")
    print(f"Output folder: {OUT_DIR.name}\n")

    # ── Pass 1: Scan all workouts ───────────────────────────────────
    workouts = parse_workouts()

    # Determine which are new
    new_indices = []
    for i, w in enumerate(workouts):
        key = workout_key(w)
        if key not in previously_processed:
            new_indices.append(i)

    if new_indices:
        print(f"  New workouts to process: {len(new_indices)}")
        for i in new_indices:
            w = workouts[i]
            wt = format_workout_type(w.get("workoutActivityType", ""))
            print(f"    #{i+1:3d} {w.get('startDate','')} {wt}")

    # ── Pass 2: Single-pass scan of all records ─────────────────────
    # Collects training records for new workouts + body metrics +
    # daily aggregates + recovery data in ONE XML scan
    workout_records, body_metrics, daily_agg, recovery = scan_all_records(
        workouts, new_indices
    )

    # ── Write aggregate outputs ─────────────────────────────────────
    print("\nWriting output files ...")
    write_body_metrics(OUT_DIR, body_metrics)
    write_daily_aggregates(OUT_DIR, daily_agg)
    write_recovery_data(OUT_DIR, recovery)

    if not new_indices:
        print("\nNo new workouts found. Everything is up to date.")
        save_state(state)
        return

    # ── Write / update summary ──────────────────────────────────────
    summary_path = OUT_DIR / "00_workouts_summary.csv"
    can_append = (
        not force
        and previously_processed
        and summary_path.exists()
        and len(new_indices) < len(workouts)
        and can_append_summary(OUT_DIR, len(new_indices), workouts, new_indices)
    )
    if can_append:
        state["summary_lines"] = append_to_summary(OUT_DIR, workouts, new_indices)
    else:
        state["summary_lines"] = write_full_summary(OUT_DIR, workouts)

    # ── Write per-workout CSVs (parallelized) ──────────────────────
    total_points = 0
    n_workers = min(MAX_WORKOUT_WRITERS, len(new_indices))

    if n_workers > 1:
        with ThreadPoolExecutor(max_workers=n_workers) as executor:
            futures = {
                executor.submit(
                    _process_one_workout, OUT_DIR, i, workouts[i],
                    workout_records.get(i, []), BASE_DIR
                ): i for i in new_indices
            }
            for future in as_completed(futures):
                try:
                    i, fname, npoints, nrecs, ngps = future.result()
                    total_points += npoints
                    w_type = format_workout_type(workouts[i].get("workoutActivityType", ""))
                    route_str = f" + {ngps} GPS pts" if ngps else ""
                    print(f"  Written: {fname} ({nrecs} records{route_str}, {npoints} data points)")
                    state["processed_workouts"][workout_key(workouts[i])] = fname
                except Exception as e:
                    idx = futures[future]
                    print(f"  Error processing workout #{idx+1}: {e}")
    else:
        for i in new_indices:
            i, fname, npoints, nrecs, ngps = _process_one_workout(
                OUT_DIR, i, workouts[i], workout_records.get(i, []), BASE_DIR
            )
            total_points += npoints
            w_type = format_workout_type(workouts[i].get("workoutActivityType", ""))
            route_str = f" + {ngps} GPS pts" if ngps else ""
            print(f"  Written: {fname} ({nrecs} records{route_str}, {npoints} data points)")
            state["processed_workouts"][workout_key(workouts[i])] = fname

    state["last_run"] = datetime.now().isoformat()
    save_state(state)

    # ── Summary ─────────────────────────────────────────────────────
    print(f"\n{'='*60}")
    print(f"Done! Output: {OUT_DIR}")
    print(f"  {len(new_indices)} new workout files written")
    print(f"  {total_points:,} data points in new workouts")
    print(f"  {len(state['processed_workouts'])} total workouts tracked")

    # Check total folder size (recursive)
    total_size = 0
    file_count = 0
    for f in OUT_DIR.rglob("*"):
        if f.is_file() and not f.name.startswith("."):
            total_size += f.stat().st_size
            file_count += 1
    print(f"  {file_count} files, total size: {total_size / 1024 / 1024:.1f} MB")

    if total_size > 100 * 1024 * 1024:
        print(f"\n  Note: Total output exceeds 100MB.")


if __name__ == "__main__":
    main()
