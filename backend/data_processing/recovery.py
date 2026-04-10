"""Recovery metrics and VO2max extraction."""

import csv
import math
from collections import defaultdict
from datetime import datetime, timedelta
from pathlib import Path

from config import (_HR_REST, _HR_MAX, _HR_LTHR, _TAU_ATL, _TAU_CTL, _TSB_SCALE,
                   _FLAT_TRIMP, TRAINING_DATA, logger)
from .helpers import _safe_float, _classify_type, MIN_VO2MAX, MAX_VO2MAX
from .workout_analysis import _find_workout_file
from .summary import _load_summary

# TRIMP calculation constants
TRIMP_HR_WEIGHT = 0.64
TRIMP_HR_EXPONENT = 1.92
CALORIES_PER_TRIMP = 6.0
FALLBACK_TRIMP_RATE = 0.8
RECOVERY_CENTER = 50.0
RECOVERY_MIN = 0.0
RECOVERY_MAX = 100.0


def _tsb_to_recovery(tsb: float) -> float:
    """Convert TSB (Training Stress Balance) to recovery score [0-100]."""
    return max(RECOVERY_MIN, min(RECOVERY_MAX, RECOVERY_CENTER + tsb * _TSB_SCALE))


def _compute_trimp(w: dict, *, hr_rest: float = None, hr_max: float = None) -> float:
    """Compute Training Impulse (TRIMP) for a single workout.

    Optional hr_rest/hr_max override the defaults from config (per-user values).
    """
    rest = hr_rest if hr_rest is not None else _HR_REST
    mx = hr_max if hr_max is not None else _HR_MAX
    dur = _safe_float(w.get("duration_min"))
    if dur <= 0:
        return 0.0
    avg_hr = _safe_float(w.get("HeartRate_average"))
    if avg_hr > 0 and avg_hr > rest:
        hr_ratio = (avg_hr - rest) / (mx - rest)
        hr_ratio = max(0.0, min(hr_ratio, 1.0))
        return dur * hr_ratio * TRIMP_HR_WEIGHT * math.exp(TRIMP_HR_EXPONENT * hr_ratio)
    # Fallback: use calories
    cals = _safe_float(w.get("ActiveEnergyBurned_sum"))
    if cals > 0:
        return cals / CALORIES_PER_TRIMP
    # Last resort: flat factor by discipline
    disc = _classify_type(w.get("type", ""))
    return dur * _FLAT_TRIMP.get(disc, FALLBACK_TRIMP_RATE)


def _compute_hrtss(w: dict, *, hr_rest: float = None, hr_max: float = None,
                   hr_lthr: float = None) -> float | None:
    """Compute Heart Rate Training Stress Score (hrTSS).

    hrTSS = (duration_sec × hrIF² × 100) / 3600
    where hrIF (Intensity Factor) = (avg_hr - hr_rest) / (lthr - hr_rest)

    Optional hr_rest/hr_max/hr_lthr override the defaults from config (per-user values).
    Returns None if HR data is unavailable.
    """
    rest = hr_rest if hr_rest is not None else _HR_REST
    lthr = hr_lthr if hr_lthr is not None else _HR_LTHR
    dur = _safe_float(w.get("duration_min"))
    avg_hr = _safe_float(w.get("HeartRate_average"))
    if dur <= 0 or avg_hr <= 0 or avg_hr <= rest:
        return None
    if lthr <= rest:
        return None
    hr_if = (avg_hr - rest) / (lthr - rest)
    hr_if = max(0.0, hr_if)
    return (dur * 60 * hr_if * hr_if * 100) / 3600


def _compute_recovery_timeline(workouts: list, *, hr_rest: float = None,
                               hr_max: float = None, hr_lthr: float = None):
    """Compute ATL/CTL/TSB day-by-day using exponentially weighted moving averages.

    ATL (Acute Training Load)  — short-term fatigue proxy (7-day EWMA)
    CTL (Chronic Training Load) — long-term fitness proxy (42-day EWMA)
    TSB (Training Stress Balance) = CTL - ATL
    Recovery = clamp(50 + TSB × scale, 0, 100)

    Uses gap-aware computation: for gaps > 7 days between workouts, applies
    exponential decay directly instead of iterating day-by-day, preventing
    stale data from early workout periods from distorting recent calculations.

    Optional hr_rest/hr_max/hr_lthr override the defaults from config (per-user values).
    """
    if not workouts:
        return {"timeline": [], "per_workout": {}}

    hr_kwargs = {}
    if hr_rest is not None:
        hr_kwargs["hr_rest"] = hr_rest
    if hr_max is not None:
        hr_kwargs["hr_max"] = hr_max

    hrtss_kwargs = {**hr_kwargs}
    if hr_lthr is not None:
        hrtss_kwargs["hr_lthr"] = hr_lthr

    workout_trimp = {}  # workout_num -> trimp
    workout_hrtss = {}  # workout_num -> hrtss (or None)
    day_load = defaultdict(lambda: {"trimp": 0.0, "hrtss": 0.0, "nums": []})

    for w in workouts:
        start_str = w.get("startDate", "")[:10]
        if not start_str:
            continue
        try:
            datetime.strptime(start_str, "%Y-%m-%d")
        except ValueError:
            continue
        num = w.get("workout_num", "")
        trimp = _compute_trimp(w, **hr_kwargs)
        hrtss = _compute_hrtss(w, **hrtss_kwargs)
        workout_trimp[num] = trimp
        workout_hrtss[num] = hrtss
        day_load[start_str]["trimp"] += trimp
        if hrtss is not None:
            day_load[start_str]["hrtss"] += hrtss
        day_load[start_str]["nums"].append(num)

    if not day_load:
        return {"current": None, "timeline": [], "per_workout": {}}

    sorted_dates = sorted(day_load.keys())
    today = datetime.now().date()

    # EWMA smoothing factors
    alpha_atl = 1.0 - math.exp(-1.0 / _TAU_ATL)   # ~0.133
    alpha_ctl = 1.0 - math.exp(-1.0 / _TAU_CTL)    # ~0.0235
    decay_atl = 1.0 - alpha_atl
    decay_ctl = 1.0 - alpha_ctl

    atl = 0.0   # Acute Training Load (fatigue)
    ctl = 0.0   # Chronic Training Load (fitness)

    timeline = []
    per_workout = {}

    # Phase 1: Process all workout days chronologically to build EWMA state.
    # For gaps > 7 days, fast-forward decay instead of iterating empty days.
    prev_date = None
    for ds in sorted_dates:
        cur = datetime.strptime(ds, "%Y-%m-%d").date()
        if prev_date is not None:
            gap = (cur - prev_date).days
            if gap > 1:
                # Fast-forward decay over the gap (no workouts in between)
                atl *= decay_atl ** (gap - 1)
                ctl *= decay_ctl ** (gap - 1)
        prev_date = cur

        day_trimp = day_load[ds]["trimp"]
        day_nums = day_load[ds]["nums"]

        # Recovery BEFORE today's load
        tsb_before = ctl - atl
        recovery_before = _tsb_to_recovery(tsb_before)

        # Update EWMA with today's load
        atl = atl * decay_atl + day_trimp * alpha_atl
        ctl = ctl * decay_ctl + day_trimp * alpha_ctl

        tsb_after = ctl - atl
        recovery_after = _tsb_to_recovery(tsb_after)

        for num in day_nums:
            pw = {
                "before": round(recovery_before, 1),
                "after": round(recovery_after, 1),
                "trimp": round(workout_trimp.get(num, 0), 1),
            }
            hrtss = workout_hrtss.get(num)
            if hrtss is not None:
                pw["hrtss"] = round(hrtss, 1)
            per_workout[str(num)] = pw

    # Phase 2: Build day-by-day timeline for display (last 12 weeks).
    # Re-run EWMA but only output the recent window.
    timeline_start = today - timedelta(weeks=12)
    atl2 = 0.0
    ctl2 = 0.0
    prev_date2 = None
    for ds in sorted_dates:
        cur = datetime.strptime(ds, "%Y-%m-%d").date()
        if prev_date2 is not None:
            gap = (cur - prev_date2).days
            if gap > 1:
                # Emit timeline entries for days in the display window,
                # applying incremental daily decay so values decrease gradually
                for g in range(1, gap):
                    atl2 *= decay_atl
                    ctl2 *= decay_ctl
                    gd = prev_date2 + timedelta(days=g)
                    if gd >= timeline_start and gd <= today:
                        timeline.append({
                            "date": gd.strftime("%Y-%m-%d"),
                            "recovery": round(_tsb_to_recovery(ctl2 - atl2), 1),
                            "fatigue": round(atl2, 1),
                            "fitness": round(ctl2, 1),
                            "day_trimp": 0.0,
                            "day_hrtss": 0.0,
                            "workout_nums": [],
                        })
        prev_date2 = cur

        day_trimp = day_load[ds]["trimp"]
        day_nums = day_load[ds]["nums"]
        atl2 = atl2 * decay_atl + day_trimp * alpha_atl
        ctl2 = ctl2 * decay_ctl + day_trimp * alpha_ctl
        tsb = ctl2 - atl2
        rec = _tsb_to_recovery(tsb)

        if cur >= timeline_start:
            timeline.append({
                "date": ds,
                "recovery": round(rec, 1),
                "fatigue": round(atl2, 1),
                "fitness": round(ctl2, 1),
                "day_trimp": round(day_trimp, 1),
                "day_hrtss": round(day_load[ds]["hrtss"], 1),
                "workout_nums": day_nums,
            })

    # Fill remaining days from last workout to today
    if prev_date2 and prev_date2 < today:
        gap = (today - prev_date2).days
        for g in range(1, gap + 1):
            gd = prev_date2 + timedelta(days=g)
            atl2 *= decay_atl
            ctl2 *= decay_ctl
            if gd >= timeline_start:
                tsb = ctl2 - atl2
                rec = _tsb_to_recovery(tsb)
                timeline.append({
                    "date": gd.strftime("%Y-%m-%d"),
                    "recovery": round(rec, 1),
                    "fatigue": round(atl2, 1),
                    "fitness": round(ctl2, 1),
                    "day_trimp": 0.0,
                    "day_hrtss": 0.0,
                    "workout_nums": [],
                })

    seen_dates = {}
    for entry in timeline:
        seen_dates[entry["date"]] = entry
    timeline = list(seen_dates.values())

    return {"timeline": timeline, "per_workout": per_workout}


RECOVERY_THRESHOLD_FRESH = 75.0
RECOVERY_THRESHOLD_MODERATE = 50.0
RECOVERY_THRESHOLD_FATIGUED = 25.0

def _recovery_label(score: float):
    """Return label + color for a recovery score."""
    if score >= RECOVERY_THRESHOLD_FRESH:
        return "fresh", "#c3e88d"
    if score >= RECOVERY_THRESHOLD_MODERATE:
        return "moderate", "#ffc777"
    if score >= RECOVERY_THRESHOLD_FATIGUED:
        return "fatigued", "#ff966c"
    return "depleted", "#ff757f"


_VO2MAX_TYPES = {"Running", "Walking", "Hiking"}


def _load_vo2max_history(data_dir: Path = None, summary: list | None = None) -> list[dict]:
    """Extract VO2Max values from per-workout CSVs.

    Apple Watch records VO2Max during running/walking/hiking workouts.
    Returns list of {date, value, workout_num, workout_type} sorted by date.
    Filters out impossible values (must be between 10 and 100 ml/kg/min).
    """
    dd = data_dir or TRAINING_DATA
    if summary is None:
        summary = _load_summary(dd)
    results = []
    for w in summary:
        wnum = w.get("workout_num", "")
        wtype = w.get("type", "")
        wdate = w.get("startDate", "")[:10]
        if not wdate:
            continue
        # Only running/walking/hiking have VO2Max — skip others
        if wtype not in _VO2MAX_TYPES:
            continue
        csv_path = _find_workout_file(int(wnum), ".csv", dd)
        if not csv_path:
            continue
        try:
            with open(csv_path, newline="") as f:
                reader = csv.DictReader(f)
                if "VO2Max" not in (reader.fieldnames or []):
                    continue
                for row in reader:
                    v = row.get("VO2Max", "")
                    if v and v.strip():
                        vo2_val = round(float(v), 1)
                        # Filter impossible values
                        unit = row.get("VO2Max_unit", "")
                        if unit and "kg" not in unit.lower():
                            logger.warning(f"VO2max unit '{unit}' may not be ml/kg/min")
                        if MIN_VO2MAX <= vo2_val <= MAX_VO2MAX:
                            results.append({
                                "date": wdate,
                                "value": vo2_val,
                                "workout_num": int(wnum),
                                "workout_type": wtype,
                            })
                        break  # one VO2Max value per workout
        except (ValueError, OSError):
            continue
    results.sort(key=lambda x: x["date"])
    return results


def _training_phase(days_to_race: int) -> str:
    """Return the current training phase based on days until race.

    Mirrors frontend trainingPhase() in classifiers.js:
    - Taper: final 14 days
    - Peak: 15-28 days out
    - Mid: proportional 40% of remaining
    - Build: proportional 60% of remaining
    """
    if days_to_race <= 14:
        return "taper"
    if days_to_race <= 28:
        return "peak"
    remaining = days_to_race - 28
    mid_days = max(14, round(remaining * 0.4))
    if days_to_race <= 28 + mid_days:
        return "mid"
    return "build"


def _compute_risk_alerts(timeline: list, recovery_data: list, phase: str | None = None) -> list:
    """Compute risk alerts based on training load patterns and recovery metrics.

    Checks for:
    - Rapid load increase (>30% week-over-week TRIMP increase)
    - Low recovery score (<30) persisting 3+ days
    - Poor sleep pattern (avg <360 min over last 3 days)
    - Elevated resting HR (above personal 7-day rolling average by >5 bpm)
    - Low HRV trend (below 7-day rolling average by >15%)

    Returns list of {type, severity, message} dicts.
    """
    alerts = []

    # Check rapid load increase from timeline
    # Compare last 7 days vs previous 7, but normalize by active training days
    # to avoid false alarms when comparing a partial week to a full week
    if len(timeline) >= 14:
        recent_7 = timeline[-7:]
        prev_7 = timeline[-14:-7]
        recent_load = sum(t.get("day_trimp", 0) for t in recent_7)
        prev_load = sum(t.get("day_trimp", 0) for t in prev_7)
        recent_days = sum(1 for t in recent_7 if t.get("day_trimp", 0) > 0)
        prev_days = sum(1 for t in prev_7 if t.get("day_trimp", 0) > 0)
        # Only compare if current period has at least as many training days
        if prev_load > 0 and prev_days > 0 and recent_days >= prev_days and recent_load > prev_load * 1.3:
            pct = round((recent_load / prev_load - 1) * 100)
            alerts.append({
                "type": "load_spike",
                "severity": "warning" if pct < 50 else "danger",
                "message": f"Weekly training load increased {pct}% — injury risk elevated",
            })

    # Check persistent low recovery
    if len(timeline) >= 3:
        last_3 = timeline[-3:]
        if all(t.get("recovery", 100) < 30 for t in last_3):
            alerts.append({
                "type": "low_recovery",
                "severity": "danger",
                "message": "Recovery below 30% for 3+ consecutive days — consider a rest day",
            })

    # Check poor sleep pattern
    if recovery_data:
        recent_sleep = [r.get("sleep_total", 0) for r in recovery_data[-3:] if r.get("sleep_total")]
        if len(recent_sleep) >= 2:
            avg_sleep = sum(recent_sleep) / len(recent_sleep)
            if avg_sleep < 360:  # less than 6 hours
                alerts.append({
                    "type": "poor_sleep",
                    "severity": "warning",
                    "message": f"Average sleep {round(avg_sleep / 60, 1)}h over last {len(recent_sleep)} days — aim for 7-9h",
                })

    # Check elevated resting HR
    if len(recovery_data) >= 7:
        rhr_values = [r.get("resting_hr") for r in recovery_data[-7:] if r.get("resting_hr")]
        if len(rhr_values) >= 5:
            avg_rhr = sum(rhr_values) / len(rhr_values)
            latest_rhr = rhr_values[-1]
            if latest_rhr > avg_rhr + 5:
                alerts.append({
                    "type": "elevated_rhr",
                    "severity": "warning",
                    "message": f"Resting HR {round(latest_rhr)} bpm is {round(latest_rhr - avg_rhr)} bpm above your 7-day average",
                })

    # Check low HRV trend
    if len(recovery_data) >= 7:
        hrv_values = [r.get("hrv_ms") for r in recovery_data[-7:] if r.get("hrv_ms")]
        if len(hrv_values) >= 5:
            avg_hrv = sum(hrv_values) / len(hrv_values)
            latest_hrv = hrv_values[-1]
            if avg_hrv > 0 and latest_hrv < avg_hrv * 0.85:
                drop_pct = round((1 - latest_hrv / avg_hrv) * 100)
                alerts.append({
                    "type": "low_hrv",
                    "severity": "warning",
                    "message": f"HRV dropped {drop_pct}% below your 7-day average — possible accumulated fatigue",
                })

    # Phase-specific alerts
    if phase == "taper" and len(timeline) >= 3:
        recent_load = sum(t.get("day_trimp", 0) for t in timeline[-3:])
        if recent_load > 200:
            alerts.append({
                "type": "taper_load",
                "severity": "warning",
                "message": "Training load is high for taper phase — prioritize rest before race day",
            })

    return alerts


def _compute_readiness_score(
    recovery: float, fatigue: float, fitness: float,
    recovery_data: list, today_str: str
) -> dict:
    """Compute composite readiness score (0-100) from multiple signals.

    Evidence-based weights:
      TSB 30% (Banister 1991), HRV 25% (Plews 2013), Sleep 20% (Halson 2014),
      RHR 15% (Buchheit 2014), ACWR 10% (Gabbett 2016).
    Missing biomarkers redistribute weight proportionally.

    Scoring philosophy: "at baseline" = 75 (normal), not 100 (peak).
      HRV: ratio 0.7 → 0, 1.0 → 75, 1.1 → 100
      RHR: deviation 0 → 75, -2.5bpm → 100, +5bpm → 25

    Returns {score, components} where each component has {score, weight, ...}.
    """
    W_TSB, W_HRV, W_SLEEP, W_RHR, W_LOAD = 0.30, 0.25, 0.20, 0.15, 0.10

    components = {}
    available_weight = 0.0

    # 1. TSB-based recovery (always available)
    tsb_score = max(0, min(100, recovery))
    components["tsb"] = {"score": round(tsb_score, 1), "weight": W_TSB}
    available_weight += W_TSB

    # 2. ATL/CTL ratio (load)
    if fitness > 5:
        ratio = fatigue / fitness
        load_score = max(0, min(100, (2.0 - ratio) / 1.5 * 100))
        components["atl_ctl"] = {"score": round(load_score, 1), "weight": W_LOAD, "ratio": round(ratio, 2)}
        available_weight += W_LOAD

    # 3. RHR trend — at baseline = 75 (normal), below baseline = up to 100 (fresh)
    if len(recovery_data) >= 3:
        rhr_values = [(r.get("resting_hr"), r.get("date", "")) for r in recovery_data[-8:] if r.get("resting_hr")]
        if len(rhr_values) >= 2:
            latest, latest_date = rhr_values[-1]
            baseline = sum(v for v, _ in rhr_values[:-1]) / len(rhr_values[:-1])
            deviation = latest - baseline
            rhr_score = max(0, min(100, 75 - deviation * 10))
            components["rhr"] = {"score": round(rhr_score, 1), "weight": W_RHR, "value": round(latest), "baseline": round(baseline), "date": latest_date}
            available_weight += W_RHR

    # 4. HRV trend — at baseline (ratio=1.0) = 75, above = up to 100 (fresh)
    if len(recovery_data) >= 3:
        hrv_values = [(r.get("hrv_sdnn_ms") or r.get("hrv_ms"), r.get("date", "")) for r in recovery_data[-8:] if r.get("hrv_sdnn_ms") or r.get("hrv_ms")]
        if len(hrv_values) >= 2:
            latest, latest_date = hrv_values[-1]
            baseline = sum(v for v, _ in hrv_values[:-1]) / len(hrv_values[:-1])
            if baseline > 0:
                ratio = latest / baseline
                hrv_score = max(0, min(100, (ratio - 0.7) / 0.4 * 100))
                components["hrv"] = {"score": round(hrv_score, 1), "weight": W_HRV, "value": round(latest), "baseline": round(baseline), "date": latest_date}
                available_weight += W_HRV

    # 5. Sleep quality
    if recovery_data:
        for r in reversed(recovery_data):
            sleep_min = r.get("sleep_total_min") or r.get("sleep_total", 0)
            if sleep_min and sleep_min > 0:
                if sleep_min >= 420:
                    sleep_score = 100.0
                elif sleep_min <= 300:
                    sleep_score = 0.0
                else:
                    sleep_score = (sleep_min - 300) / 120 * 100
                if sleep_min > 540:
                    sleep_score = max(70.0, sleep_score - (sleep_min - 540) / 60 * 10)
                components["sleep"] = {"score": round(sleep_score, 1), "weight": W_SLEEP, "value": round(sleep_min / 60, 1), "date": r.get("date", "")}
                available_weight += W_SLEEP
                break

    # Compute weighted score, redistributing missing weights
    if available_weight <= 0:
        return {"score": round(recovery), "components": components}

    total = 0.0
    for comp in components.values():
        adjusted = comp["weight"] / available_weight
        comp["weight"] = round(adjusted, 3)
        total += comp["score"] * adjusted

    score = round(max(0, min(100, total)))
    return {"score": score, "components": components}


def _compute_weekly_load_change(timeline: list) -> dict | None:
    """Compute week-over-week load change from the timeline.

    Returns {current_week, previous_week, change_pct, direction} or None if insufficient data.
    """
    if len(timeline) < 7:
        return None

    today_str = timeline[-1]["date"] if timeline else None
    if not today_str:
        return None

    try:
        today = datetime.strptime(today_str, "%Y-%m-%d").date()
    except ValueError:
        return None

    # Current week: last 7 days of timeline
    week_start = (today - timedelta(days=6)).strftime("%Y-%m-%d")
    prev_week_start = (today - timedelta(days=13)).strftime("%Y-%m-%d")

    current_load = sum(
        t.get("day_trimp", 0) for t in timeline
        if week_start <= t["date"] <= today_str
    )
    prev_load = sum(
        t.get("day_trimp", 0) for t in timeline
        if prev_week_start <= t["date"] < week_start
    )

    change_pct = 0
    if prev_load > 0:
        change_pct = round((current_load / prev_load - 1) * 100)

    if current_load > prev_load:
        direction = "up"
    elif current_load < prev_load:
        direction = "down"
    else:
        direction = "flat"

    return {
        "current_week": round(current_load, 1),
        "previous_week": round(prev_load, 1),
        "change_pct": change_pct,
        "direction": direction,
    }

