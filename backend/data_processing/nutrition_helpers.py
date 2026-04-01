"""Nutrition-related helper functions for insights."""

from datetime import datetime, timedelta
from pathlib import Path

from config import logger
from .helpers import _safe_float, _load_settings_dict
from .csv_loaders import _load_recovery_data
from .recovery import _compute_recovery_timeline, _recovery_label


def _build_recovery_sleep_context(workout_date_str: str, workouts: list, data_dir: Path = None) -> str:
    """Build recovery (CTL/ATL/TSB) and sleep context for a workout date.

    Returns a text block to inject into insight prompts, or empty string if
    no data is available.
    """
    lines = []

    # Recovery status (CTL/ATL/TSB) computed from all workouts up to this date
    try:
        recovery = _compute_recovery_timeline(workouts)
        per_workout = recovery.get("per_workout", {})
        # Find workouts on this date to get their recovery scores
        for w2 in workouts:
            if w2.get("startDate", "")[:10] == workout_date_str:
                wnum = str(w2.get("workout_num", ""))
                rec = per_workout.get(wnum)
                if rec:
                    label, _ = _recovery_label(rec["before"])
                    lines.append(
                        f"Recovery before workout: {rec['before']:.0f}% ({label}), "
                        f"TRIMP load: {rec['trimp']:.0f}"
                    )
                    break
        # Also get the timeline entry for this date to show fitness/fatigue
        for t in recovery.get("timeline", []):
            if t["date"] == workout_date_str:
                lines.append(
                    f"Fitness (CTL): {t['fitness']:.1f}, "
                    f"Fatigue (ATL): {t['fatigue']:.1f}, "
                    f"Form (TSB): {t['fitness'] - t['fatigue']:.1f}"
                )
                break
    except Exception as e:
        logger.warning(f"Could not compute recovery for {workout_date_str}: {e}")

    # Sleep data from recovery_data.csv (last 3 nights before the workout)
    try:
        recovery_data = _load_recovery_data(data_dir)
        workout_dt = datetime.strptime(workout_date_str, "%Y-%m-%d")

        # Check nights at day-1, day-2, day-3 (also check workout date itself for day-1)
        sleep_totals = []
        rhr_val = None
        hrv_val = None
        for days_back in range(1, 4):
            night_date = (workout_dt - timedelta(days=days_back)).strftime("%Y-%m-%d")
            # For the most recent night (day-1), also check workout date itself
            check_dates = [night_date]
            if days_back == 1:
                check_dates.append(workout_date_str)
            for check_date in check_dates:
                row = recovery_data.get(check_date)
                if not row:
                    continue
                sleep_total = _safe_float(row.get("sleep_total_min"))
                if sleep_total > 0:
                    sleep_deep = _safe_float(row.get("sleep_deep_min"))
                    sleep_rem = _safe_float(row.get("sleep_rem_min"))
                    sleep_core = _safe_float(row.get("sleep_core_min"))
                    sleep_awake = _safe_float(row.get("sleep_awake_min"))
                    label = f"Sleep ({days_back} night{'s' if days_back > 1 else ''} before)"
                    sleep_line = f"{label}: {sleep_total:.0f} min total"
                    parts = []
                    if sleep_deep:
                        parts.append(f"deep {sleep_deep:.0f}m")
                    if sleep_core:
                        parts.append(f"core {sleep_core:.0f}m")
                    if sleep_rem:
                        parts.append(f"REM {sleep_rem:.0f}m")
                    if sleep_awake:
                        parts.append(f"awake {sleep_awake:.0f}m")
                    if parts:
                        sleep_line += f" ({', '.join(parts)})"
                    lines.append(sleep_line)
                    sleep_totals.append(sleep_total)

                    # Resting HR and HRV from most recent available night only
                    if rhr_val is None:
                        rhr = _safe_float(row.get("resting_hr"))
                        if rhr:
                            rhr_val = rhr
                    if hrv_val is None:
                        hrv = _safe_float(row.get("hrv_sdnn_ms"))
                        if hrv:
                            hrv_val = hrv
                    break  # Found data for this night, move to next night

        # 3-night average total sleep
        if len(sleep_totals) > 1:
            avg_sleep = sum(sleep_totals) / len(sleep_totals)
            lines.append(f"Sleep avg ({len(sleep_totals)} nights): {avg_sleep:.0f} min/night")

        if rhr_val:
            lines.append(f"Resting HR: {rhr_val:.0f} bpm")
        if hrv_val:
            lines.append(f"HRV (SDNN): {hrv_val:.0f} ms")
    except Exception as e:
        logger.warning(f"Could not load sleep data for {workout_date_str}: {e}")

    if not lines:
        return ""
    return "RECOVERY & SLEEP CONTEXT:\n" + "\n".join(lines)



_DEFAULT_PRE_HOURS = 4
_DEFAULT_POST_HOURS = 2


def _load_nutrition_window() -> tuple:
    """Load nutrition fueling window settings from DB (sync). Returns (pre_hours, post_hours)."""
    try:
        settings = _load_settings_dict(["nutrition_pre_hours", "nutrition_post_hours"])
        return (
            int(settings.get("nutrition_pre_hours", str(_DEFAULT_PRE_HOURS))),
            int(settings.get("nutrition_post_hours", str(_DEFAULT_POST_HOURS))),
        )
    except Exception:
        return (_DEFAULT_PRE_HOURS, _DEFAULT_POST_HOURS)


def _load_nutrition_settings() -> dict:
    """Load nutrition-related settings from DB (sync)."""
    try:
        settings = _load_settings_dict(["nutrition_regen_enabled", "nutrition_pre_insight"])
        return {
            "regen_enabled": settings.get("nutrition_regen_enabled", "1") != "0",
            "pre_insight": settings.get("nutrition_pre_insight", "1") != "0",
        }
    except Exception:
        return {"regen_enabled": True, "pre_insight": True}



def _meal_relevant_to_workout(meal_time_str: str, meal_type: str,
                              workout_start: str, duration_min: float,
                              nutrition_window: tuple = None) -> bool:
    """Check if a meal is nutritionally relevant to a workout (code-based, no LLM).

    Relevant if:
    - Pre-workout: meal eaten 0-4 hours before workout start
    - Post-workout: meal eaten 0-2 hours after workout end
    - Workout meal types (pre_workout, during_workout, post_workout) always relevant
    - No meal_time and generic type → not relevant (can't determine timing)

    Pass nutrition_window=(pre_h, post_h) to avoid repeated DB reads in loops.
    """
    if meal_type in ("pre_workout", "during_workout", "post_workout"):
        return True

    if not workout_start:
        return False
    if not meal_time_str:
        return True

    try:
        ws = datetime.fromisoformat(workout_start.replace("Z", "+00:00").replace("T", " ").split("+")[0].strip())
        workout_end = ws + timedelta(minutes=duration_min or 60)

        meal_date = ws.date()
        h, m = map(int, meal_time_str.split(":"))
        meal_dt = datetime.combine(meal_date, datetime.min.time().replace(hour=h, minute=m))

        pre_h, post_h = nutrition_window or _load_nutrition_window()
        pre_window_start = ws - timedelta(hours=pre_h)
        post_window_end = workout_end + timedelta(hours=post_h)

        return pre_window_start <= meal_dt <= post_window_end
    except (ValueError, TypeError):
        return False

