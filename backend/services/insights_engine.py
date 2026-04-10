"""Insight generation engine — prompt builders and multi-agent orchestration."""

import asyncio
import csv
import json
import re
import time
from collections import defaultdict
from datetime import datetime, timedelta, timezone
from pathlib import Path

import database as db
from config import (
    TRAINING_DATA, PROJECT_ROOT,
    logger,
    PERIOD_CATEGORIES, _CATEGORY_AGENTS, _CATEGORY_TYPES,
    INSIGHT_COACH_PREAMBLE_TEMPLATE,
)
from services.task_tracker import (
    _insight_status, _insight_status_lock,
    _active_tasks, _active_tasks_lock,
)
from services.claude_cli import _find_claude_cli, _track_usage, _call_agent, _build_cli_env, _parse_stream_json, _get_model_override, _llm_preflight_check
from services.coach_preamble import _build_coach_preamble
from services.weather import _format_weather, _get_first_gps, _fetch_external_weather, _format_external_weather
from data_processing import (
    _safe_float, _classify_type, _enrich_workouts, _workout_distance, _load_summary,
    _compute_sections, _find_workout_file,
    _workout_csv_filename, _build_workout_data_summary,
    _build_recovery_sleep_context, _load_nutrition_window, _load_nutrition_settings,
    _detect_brick_sessions, _merge_nearby_workouts,
    _meal_relevant_to_workout,
)
from routes.deps import _user_data_dir


# Food keywords (EN + HE) and photo reference patterns
_FOOD_KEYWORDS = re.compile(
    r'(?:ate|eat|food|meal|snack|breakfast|lunch|dinner|banana|gel|bar|drink|drank|coffee|'
    r'אכלתי|אוכל|ארוחה|בננה|ג.ל|חטיף|שתיתי|קפה|לחם|ביצ|חביתה|שייק|פיתה)',
    re.IGNORECASE
)
_PHOTO_REF = re.compile(
    r'(?:see\s+(?:pic|photo|image|attached)|pic|photo|תמונה|ראה\s+תמונה|ראה\s+צילום|attached)',
    re.IGNORECASE
)


def _lang_label(lang: str) -> str:
    """Return display name for a language code."""
    return "Hebrew" if lang == "he" else "English"


def _lang_prefix(lang: str) -> str:
    """Return the standard language instruction prefix for prompts."""
    return f"⚠️ LANGUAGE: Respond ENTIRELY in **{_lang_label(lang)}**.\n\n"


def _note_mentions_food_with_photo(text: str) -> bool:
    """Check if text mentions food AND references a photo."""
    if not text:
        return False
    return bool(_FOOD_KEYWORDS.search(text) and _PHOTO_REF.search(text))


def _build_same_day_context(workouts: list, wdate: str, exclude_num: int) -> str:
    """Build context block listing other workouts on the same day."""
    same_day = [aw for aw in workouts
                if aw.get("startDate", "")[:10] == wdate and int(aw.get("workout_num", 0)) != exclude_num]
    if not same_day:
        return ""
    sd_lines = []
    for sd in same_day:
        sd_dur = float(sd.get("duration_min", 0))
        sd_dist = float(sd.get("distance_km", 0) or 0)
        sd_lines.append(
            f"- #{sd.get('workout_num')} {sd.get('type', '')} "
            f"({sd_dur:.0f}min{f', {sd_dist:.1f}km' if sd_dist else ''}) "
            f"at {sd.get('startDate', '')[11:16]}"
        )
    return (
        f"\n\n## OTHER WORKOUTS TODAY\n"
        + "\n".join(sd_lines)
        + "\nDo NOT claim any discipline was skipped if it appears here."
    )


def _split_plan_comparison(text: str) -> tuple[str, str]:
    """Split plan comparison section from insight text. Returns (main_text, plan_cmp)."""
    if "**Plan comparison**" not in text:
        return text, ""
    parts = text.split("**Plan comparison**")
    return parts[0].strip(), "**Plan comparison**" + parts[1].strip()


def _build_workout_prompt(w: dict, plans: list, preamble: str = "") -> str:
    """Build a concise prompt for one workout insight."""
    disc = _classify_type(w.get("type", ""))
    lines = [
        f"Type: {w.get('type', 'Unknown')}",
        f"Date: {w.get('startDate', '')[:19]}",
        f"Duration: {float(w.get('duration_min', 0)):.1f} min",
    ]
    dist = _workout_distance(w)
    if dist > 0:
        if disc == "swim":
            lines.append(f"Distance: {dist*1000:.0f} m")
        else:
            lines.append(f"Distance: {dist:.2f} km")
    hr_avg = _safe_float(w.get("HeartRate_average"))
    hr_max = _safe_float(w.get("HeartRate_maximum"))
    if hr_avg:
        lines.append(f"Heart Rate: avg {hr_avg:.0f}, max {hr_max:.0f} bpm")
    if _safe_float(w.get("RunningSpeed_average")):
        spd = _safe_float(w["RunningSpeed_average"])
        pace_min = 60 / spd if spd > 0 else 0
        lines.append(f"Avg Speed: {spd:.1f} km/h (pace {int(pace_min)}:{int(pace_min%1*60):02d}/km)")
    if _safe_float(w.get("RunningPower_average")):
        lines.append(f"Running Power: avg {_safe_float(w['RunningPower_average']):.0f}W")
    if _safe_float(w.get("CyclingPower_average")):
        lines.append(f"Cycling Power: avg {_safe_float(w['CyclingPower_average']):.0f}W")
    if _safe_float(w.get("CyclingCadence_average")):
        lines.append(f"Cadence: {_safe_float(w['CyclingCadence_average']):.0f} rpm")
    if _safe_float(w.get("SwimmingStrokeCount_sum")):
        lines.append(f"Stroke Count: {_safe_float(w['SwimmingStrokeCount_sum']):.0f}")
    elev = _safe_float(w.get("meta_ElevationAscended"))
    if elev:
        lines.append(f"Elevation Gain: {elev/100:.0f} m")
    cal = _safe_float(w.get("ActiveEnergyBurned_sum"))
    if cal:
        lines.append(f"Calories: {cal:.0f}")
    steps = _safe_float(w.get("StepCount_sum"))
    dur = _safe_float(w.get("duration_min"))
    if steps and dur:
        lines.append(f"Cadence: {steps/dur:.0f} steps/min")
    weather = _format_weather(w)
    if weather:
        lines.append(f"Weather: {weather}")

    prompt = (
        preamble +
        "\n## Task: Analyze this single workout\n"
        "Be direct, specific with numbers, no fluff. "
        "Evaluate the effort in the context of 70.3 training.\n\n"
        f"WORKOUT DATA:\n" + "\n".join(lines)
    )

    if plans:
        plan_lines = []
        for p in plans:
            plan_lines.append(
                f"- {p.get('discipline','').upper()}: {p.get('title','')} | "
                f"{p.get('description','')} | "
                f"Duration: {p.get('duration_planned_min',0)} min, "
                f"Distance: {p.get('distance_planned_km',0)} km, "
                f"Intensity: {p.get('intensity','')}"
            )
        prompt += (
            "\n\n## PLANNED WORKOUT (training plan for this day)\n" + "\n".join(plan_lines) +
            "\n\nIMPORTANT: Compare every metric (distance, duration, intensity, pace, HR zones) "
            "against the plan. State clearly what was hit, missed, or exceeded."
        )

    prompt += (
        "\n\nFormat your response as:\n"
        "**Summary**: one sentence\n"
        "**Observations**:\n- bullet 1\n- bullet 2\n- bullet 3\n"
        "**Improve next time**: one specific tip\n"
    )
    if plans:
        prompt += (
            "**Plan comparison**: For each planned metric (distance, duration, intensity), "
            "state the planned value, the actual value, and whether the target was hit, "
            "missed, or exceeded. Be specific with numbers.\n"
        )

    return prompt


def _build_nutrition_prompt(w: dict, nutrition_entries: list) -> str:
    """Build prompt for nutrition coach analysis with meals categorized by timing."""
    w_start_str = w.get("startDate", "")
    dur_min = float(w.get("duration_min", 0) or 0)
    lines = [
        f"Workout: {w.get('type', 'Unknown')}",
        f"Date: {w_start_str[:19]}",
        f"Duration: {dur_min:.1f} min",
        f"Start time: {w_start_str[11:16]}",
    ]
    cal = _safe_float(w.get("ActiveEnergyBurned_sum"))
    if cal:
        lines.append(f"Active calories burned: {cal:.0f} kcal")
    weather = _format_weather(w)
    if weather:
        lines.append(f"Weather: {weather}")

    if not nutrition_entries:
        lines.append("\nNO NUTRITION DATA LOGGED FOR THIS DAY.")
        lines.append("Flag this clearly — the athlete should be tracking nutrition for 70.3 training.")
        lines.append("\nAnalyze the nutrition in context of this workout. Be specific and actionable.")
        return "\n".join(lines)

    # Parse workout start/end for meal categorization
    pre_meals, during_meals, post_meals, other_meals = [], [], [], []
    try:
        ws = datetime.fromisoformat(w_start_str.replace("Z", "+00:00").replace("T", " ").split("+")[0].strip())
        we = ws + timedelta(minutes=dur_min or 60)
        pre_h, post_h = _load_nutrition_window()

        for entry in nutrition_entries:
            meal_time = entry.get("meal_time", "")
            meal_type = entry.get("meal_type", "")
            if meal_type in ("during_workout",):
                during_meals.append(entry)
            elif meal_type == "pre_workout":
                pre_meals.append(entry)
            elif meal_type == "post_workout":
                post_meals.append(entry)
            elif meal_time:
                try:
                    h, m = map(int, meal_time.split(":"))
                    meal_dt = datetime.combine(ws.date(), datetime.min.time().replace(hour=h, minute=m))
                    if meal_dt < ws and meal_dt >= ws - timedelta(hours=pre_h):
                        pre_meals.append(entry)
                    elif ws <= meal_dt <= we:
                        during_meals.append(entry)
                    elif meal_dt > we and meal_dt <= we + timedelta(hours=post_h):
                        post_meals.append(entry)
                    else:
                        other_meals.append(entry)
                except (ValueError, TypeError):
                    other_meals.append(entry)
            else:
                other_meals.append(entry)
    except (ValueError, TypeError):
        other_meals = nutrition_entries

    def _fmt_entry(entry):
        mt = entry.get("meal_time", "")
        time_str = f"[{mt}] " if mt else ""
        return (
            f"- {time_str}{entry.get('meal_type', 'unknown')}: {entry.get('description', '')} | "
            f"Cal: {entry.get('calories', 0)}, P: {entry.get('protein_g', 0)}g, "
            f"C: {entry.get('carbs_g', 0)}g, F: {entry.get('fat_g', 0)}g, "
            f"Hydration: {entry.get('hydration_ml', 0)}ml"
        )

    if pre_meals:
        lines.append(f"\nPRE-WORKOUT MEALS (0-{pre_h}h before):")
        for e in pre_meals:
            lines.append(_fmt_entry(e))
    if during_meals:
        lines.append("\nDURING-WORKOUT FUELING:")
        for e in during_meals:
            lines.append(_fmt_entry(e))
    if post_meals:
        lines.append(f"\nPOST-WORKOUT RECOVERY MEALS (0-{post_h}h after):")
        for e in post_meals:
            lines.append(_fmt_entry(e))
    if other_meals:
        lines.append("\nOTHER MEALS THIS DAY:")
        for e in other_meals:
            lines.append(_fmt_entry(e))
    if not pre_meals and not during_meals and not post_meals:
        lines.append("\n⚠️ No meals logged in the workout fueling window (pre/during/post).")

    lines.append("\nAnalyze pre-workout fueling, during-workout fueling, and post-workout recovery nutrition. Be specific and actionable.")
    return "\n".join(lines)


def _build_specialist_prompt(w: dict, sections: dict, plans: list, preamble: str = "",
                              data_dir: Path = None, include_raw_data: bool = False) -> str:
    """Build a discipline-specific specialist prompt with per-section data (data only).

    The system prompt is now in the agent .md file; this only builds the data payload.
    Includes raw CSV and splits file paths so the agent can Read them for deeper analysis
    (e.g. interval detection, variable-effort segments).
    """
    disc = sections["discipline"]
    if disc not in ("run", "swim", "bike"):
        return ""

    lines = [
        f"Type: {w.get('type', 'Unknown')}",
        f"Date: {w.get('startDate', '')[:19]}",
        f"Duration: {float(w.get('duration_min', 0)):.1f} min",
        f"Total Distance: {sections['total_distance_km']:.2f} km",
    ]
    hr_avg = _safe_float(w.get("HeartRate_average"))
    hr_max = _safe_float(w.get("HeartRate_maximum"))
    if hr_avg:
        lines.append(f"Heart Rate: avg {hr_avg:.0f}, max {hr_max:.0f} bpm")
    elev = _safe_float(w.get("meta_ElevationAscended"))
    if elev:
        lines.append(f"Elevation Gain: {elev/100:.0f} m")
    weather = _format_weather(w)
    if weather:
        lines.append(f"Weather: {weather}")

    # Section data table
    lines.append("")
    # Check if any section has elevation data
    _has_elev = any(s.get("elev_gain_m") is not None for s in sections["sections"])

    if disc == "run":
        lines.append("PER-KM SPLITS:")
        header = "km | pace/km | HR(avg/min/max) | cadence | power | GCT(ms) | stride(m)"
        if _has_elev:
            header += " | elev_gain(m)"
        lines.append(header)
        for s in sections["sections"]:
            hr_str = str(s.get('avg_hr', '-'))
            if s.get('hr_min') is not None and s.get('hr_max') is not None:
                hr_str = f"{s['avg_hr']}/{int(s['hr_min'])}-{int(s['hr_max'])}"
            line = (
                f"  {s['km']} | {s.get('pace_str','-')} | "
                f"{hr_str} | {s.get('avg_cadence','-')} | "
                f"{s.get('avg_power','-')}W | {s.get('avg_gct','-')} | "
                f"{s.get('avg_stride','-')}"
            )
            if _has_elev:
                eg = s.get('elev_gain_m')
                line += f" | {eg:.1f}" if eg is not None else " | -"
            lines.append(line)
    elif disc == "swim":
        lines.append("PER-100M SEGMENTS:")
        lines.append("segment | swim pace/100m | rest(s) | HR(avg/min/max) | strokes")
        for s in sections["sections"]:
            rest = f"{s.get('rest_sec', 0):.0f}" if s.get("rest_sec") else "0"
            hr_str = str(s.get('avg_hr', '-'))
            if s.get('hr_min') is not None and s.get('hr_max') is not None:
                hr_str = f"{s['avg_hr']}/{int(s['hr_min'])}-{int(s['hr_max'])}"
            lines.append(
                f"  {s.get('segment_m','-')}m | {s.get('pace_str','-')} | "
                f"rest {rest}s | {hr_str} | {s.get('stroke_count','-')}"
            )
    elif disc == "bike":
        lines.append("PER-KM SEGMENTS:")
        header = "km | speed(km/h) | HR(avg/min/max) | power | cadence"
        if _has_elev:
            header += " | elev_gain(m)"
        lines.append(header)
        for s in sections["sections"]:
            hr_str = str(s.get('avg_hr', '-'))
            if s.get('hr_min') is not None and s.get('hr_max') is not None:
                hr_str = f"{s['avg_hr']}/{int(s['hr_min'])}-{int(s['hr_max'])}"
            line = (
                f"  {s.get('km_marker','-')}km | {s.get('avg_speed_kmh','-')} | "
                f"{hr_str} | {s.get('avg_power','-')}W | "
                f"{s.get('avg_cadence','-')}"
            )
            if _has_elev:
                eg = s.get('elev_gain_m')
                line += f" | {eg:.1f}" if eg is not None else " | -"
            lines.append(line)

    # HR zone distribution
    lines.append("")
    lines.append("HR ZONE DISTRIBUTION:")
    for zone in ("Z1", "Z2", "Z3", "Z4", "Z5"):
        zd = sections["hr_zones"].get(zone, {})
        secs = zd.get("seconds", 0)
        pct = zd.get("pct", 0)
        mm = int(secs // 60)
        ss = int(secs % 60)
        lines.append(f"  {zone}: {mm}:{ss:02d} ({pct:.1f}%)")

    # Plan context — prominent placement so specialist always compares
    plan_block = ""
    if plans:
        plan_lines = []
        for p in plans:
            plan_lines.append(
                f"- {p.get('discipline','').upper()}: {p.get('title','')} | "
                f"{p.get('description','')} | Duration: {p.get('duration_planned_min',0)} min, "
                f"Distance: {p.get('distance_planned_km',0)} km, Intensity: {p.get('intensity','')}"
            )
        plan_block = (
            "\n\n## PLANNED WORKOUT (training plan for this day)\n"
            + "\n".join(plan_lines)
            + "\n\nIMPORTANT: Compare every metric (distance, duration, intensity, pace, HR zones) "
            "against this plan. State clearly what was hit, missed, or exceeded."
        )

    # Pre-computed intervals and profiles (from .sections.json)
    has_intervals = bool(sections.get("intervals"))
    if has_intervals:
        lines.append("")
        lines.append("DETECTED INTERVALS (work/rest segments from speed analysis):")
        for iv in sections["intervals"]:
            iv_parts = [f"{iv['type'].upper()}: {iv['duration_sec']}s"]
            if iv.get("pace_str"):
                iv_parts.append(iv["pace_str"])
            elif iv.get("avg_speed_kmh"):
                iv_parts.append(f"{iv['avg_speed_kmh']} km/h")
            if iv.get("avg_hr"):
                hr_str = f"HR {iv['avg_hr']}"
                if iv.get("hr_min") is not None and iv.get("hr_max") is not None:
                    hr_str += f" ({iv['hr_min']}-{iv['hr_max']})"
                iv_parts.append(hr_str)
            if iv.get("avg_power"):
                iv_parts.append(f"{iv['avg_power']}W")
            if iv.get("distance_m"):
                iv_parts.append(f"{iv['distance_m']}m")
            lines.append(f"  {' | '.join(iv_parts)}")

    # VO2max and active calories from sections data
    if sections.get("vo2max"):
        lines.append(f"VO2max: {sections['vo2max']} (from this workout)")
    if sections.get("active_calories"):
        lines.append(f"Active calories: {sections['active_calories']} kcal")

    if sections.get("hr_summary"):
        hs = sections["hr_summary"]
        lines.append("")
        lines.append(
            f"HR CARDIAC DRIFT: 1st half avg {hs['first_half_avg']} → 2nd half avg {hs['second_half_avg']} "
            f"(drift {hs['drift_pct']:+.1f}%) | range {hs['min']}-{hs['max']}"
        )

    if sections.get("elevation_summary"):
        es = sections["elevation_summary"]
        lines.append(
            f"ELEVATION: ascent {es['total_ascent_m']:.0f}m, descent {es['total_descent_m']:.0f}m | "
            f"range {es['min_m']:.0f}m-{es['max_m']:.0f}m"
        )

    # Raw file paths for deeper analysis (use only when pre-computed data is insufficient)
    wnum = int(w.get("workout_num", 0))
    variable_effort = has_intervals
    if data_dir:
        csv_file = _find_workout_file(wnum, ".csv", data_dir)
        splits_file = _find_workout_file(wnum, ".splits.json", data_dir)
        events_file = _find_workout_file(wnum, ".events.json", data_dir) if disc == "swim" else None
        raw_lines = []
        if csv_file:
            raw_lines.append(f"- Time-series CSV (~3s resolution): {csv_file}")
        if splits_file:
            raw_lines.append(f"- Apple splits (km/mile durations): {splits_file}")
        if events_file:
            raw_lines.append(f"- Swim events (laps/sets from Apple Watch): {events_file}")

        # Detect variable-effort pattern from per-km splits (only if no pre-computed intervals)
        if not has_intervals and disc in ("run", "bike") and sections.get("sections"):
            speeds = []
            for s in sections["sections"]:
                v = s.get("avg_speed_kmh") if disc == "bike" else None
                if v is None and s.get("pace_str"):
                    try:
                        pm, ps = s["pace_str"].split(":")
                        secs_per_km = int(pm) * 60 + int(ps)
                        if secs_per_km > 0:
                            v = 3600 / secs_per_km
                    except (ValueError, ZeroDivisionError):
                        pass
                if v and isinstance(v, (int, float)) and v > 0:
                    speeds.append(float(v))
            if len(speeds) >= 3:
                avg_spd = sum(speeds) / len(speeds)
                max_var = (max(speeds) - min(speeds)) / avg_spd if avg_spd > 0 else 0
                if max_var > 0.20:
                    variable_effort = True

        if raw_lines:
            lines.append("")
            if variable_effort and not has_intervals:
                lines.append("⚠️ VARIABLE EFFORT DETECTED — per-km splits show >20% speed variation.")
                lines.append("Consider using the Read tool to open the raw time-series CSV below for sub-interval resolution.")
            lines.append("RAW DATA FILES (use ONLY if the pre-computed data above doesn't answer your question):")
            lines.extend(raw_lines)

    # Include raw time-series data when explicitly requested by athlete
    if include_raw_data and data_dir:
        if not csv_file:
            csv_file = _find_workout_file(wnum, ".csv", data_dir)
        if csv_file and csv_file.exists():
            try:
                with open(csv_file, newline="") as f:
                    all_lines = f.readlines()
                # Limit to ~500 lines; if longer, sample every Nth line to cover full workout
                MAX_LINES = 500
                if len(all_lines) <= MAX_LINES + 1:  # +1 for header
                    raw_sample = "".join(all_lines)
                else:
                    header = all_lines[0]
                    data_lines = all_lines[1:]
                    step = max(1, len(data_lines) // MAX_LINES)
                    sampled = [data_lines[i] for i in range(0, len(data_lines), step)][:MAX_LINES]
                    raw_sample = header + "".join(sampled)
                lines.append("")
                lines.append("The athlete requested detailed raw data for this analysis. Use it for precise interval/stride analysis.")
                lines.append(f"DETAILED RAW DATA (requested by athlete for more accurate analysis, {len(all_lines)-1} rows, {'sampled every ' + str(step) + ' rows' if len(all_lines) > MAX_LINES + 1 else 'full'}):")
                lines.append(raw_sample.rstrip())
            except Exception as e:
                logger.warning(f"Failed to read raw CSV for include_raw_data: {e}")

    closing = (
        "\n\nAnalyze this workout split-by-split. Be specific with numbers from every split above. "
        "HR ranges show min-max within each split — use these to identify HR spikes, cardiac drift, and effort variation. "
        "If effort varies significantly between splits (intervals, hills), analyze the pattern explicitly."
    )
    if has_intervals:
        closing += (
            "\n\nPre-computed work/rest intervals are provided above. Use them to analyze the interval structure "
            "with specific durations, paces, and HR values. Only read raw CSV if you need finer sub-interval resolution."
        )
    elif variable_effort:
        closing += (
            "\n\n**NOTE**: This appears to be a variable-effort workout but interval detection was not available. "
            "Consider using the Read tool to open the raw CSV file for detailed interval analysis."
        )
    if plans:
        closing += (
            "\n\nAfter the split analysis, add a dedicated section:\n"
            "**Plan vs Actual**: compare planned distance/duration/intensity "
            "against actual. State each target and whether it was met."
        )

    return (
        "## Workout data\n" + "\n".join(lines) +
        plan_block +
        closing
    )


def _build_synthesis_prompt(w: dict, specialist_analysis: str, plans: list, preamble: str = "") -> str:
    """Build the head coach synthesis prompt using the specialist's analysis."""
    lines = [
        f"Type: {w.get('type', 'Unknown')}",
        f"Date: {w.get('startDate', '')[:19]}",
        f"Duration: {float(w.get('duration_min', 0)):.1f} min",
    ]

    plan_block = ""
    if plans:
        plan_lines = []
        for p in plans:
            plan_lines.append(
                f"- {p.get('discipline','').upper()}: {p.get('title','')} | "
                f"{p.get('description','')} | Duration: {p.get('duration_planned_min',0)} min, "
                f"Distance: {p.get('distance_planned_km',0)} km, Intensity: {p.get('intensity','')}"
            )
        plan_block = (
            "\n\nPLANNED WORKOUT FOR THIS DAY:\n" + "\n".join(plan_lines) + "\n"
        )

    prompt = (
        preamble +
        "\n## Task: Synthesize specialist analysis into final insight\n"
        "A discipline specialist has analyzed this workout in detail. "
        "Your job is to synthesize their analysis into a concise, actionable insight "
        "for the athlete's 70.3 training context.\n\n"
        f"WORKOUT: {', '.join(lines)}\n\n"
        f"SPECIALIST ANALYSIS:\n{specialist_analysis}\n"
        + plan_block +
        "\nFormat your response as:\n"
        "**Summary**: one sentence\n"
        "**Observations**:\n- bullet 1\n- bullet 2\n- bullet 3\n"
        "**Improve next time**: one specific tip\n"
    )
    if plans:
        prompt += (
            "**Plan comparison**: Compare actual workout execution against the planned "
            "workout above. For each planned metric (distance, duration, intensity), state "
            "the planned value, the actual value, and whether the target was hit, missed, "
            "or exceeded. Be specific with numbers.\n"
        )

    return prompt


def _build_general_prompt(workouts: list, race_info: dict, preamble: str = "") -> str:
    """Build prompt for general training insights across recent workouts."""
    # Group by week and compute totals per discipline in a single pass
    weeks = defaultdict(lambda: {"swim": 0, "bike": 0, "run": 0, "strength": 0,
                                  "swim_km": 0, "bike_km": 0, "run_km": 0, "count": 0})
    disc_totals = {}
    for w in workouts:
        start = w.get("startDate", "")[:10]
        disc = _classify_type(w.get("type", ""))
        dur = _safe_float(w.get("duration_min"))
        dist = _workout_distance(w)

        if disc not in disc_totals:
            disc_totals[disc] = {"count": 0, "min": 0, "km": 0}
        disc_totals[disc]["count"] += 1
        disc_totals[disc]["min"] += dur
        disc_totals[disc]["km"] += dist

        if not start:
            continue
        try:
            dt = datetime.strptime(start, "%Y-%m-%d")
        except ValueError:
            continue
        yr, wk, _ = dt.isocalendar()
        key = f"{yr}-W{wk:02d}"
        weeks[key][disc] = weeks[key].get(disc, 0) + dur
        if disc in ("swim", "bike", "run"):
            weeks[key][f"{disc}_km"] += dist
        weeks[key]["count"] += 1

    week_summary = []
    for wk in sorted(weeks.keys()):
        d = weeks[wk]
        parts = []
        for disc in ("swim", "bike", "run", "strength"):
            if d.get(disc, 0) > 0:
                km = d.get(f"{disc}_km", 0)
                km_str = f" {km:.1f}km" if km > 0 else ""
                parts.append(f"{disc} {d[disc]:.0f}min{km_str}")
        week_summary.append(f"{wk}: {d['count']} sessions — {', '.join(parts)}")

    totals_str = "\n".join(
        f"- {d.upper()}: {v['count']} sessions, {v['min']:.0f} min, {v['km']:.1f} km"
        for d, v in disc_totals.items()
    )

    # Build event context from race_info (which is now the primary event)
    event_str = "No event set"
    days_to_race = "?"
    if race_info:
        name = race_info.get("event_name") or race_info.get("race_name", "Unknown Event")
        etype = race_info.get("event_type", "").replace("_", " ").title()
        try:
            rd = datetime.strptime(race_info.get("event_date") or race_info.get("race_date", ""), "%Y-%m-%d")
            days_to_race = max(0, (rd - datetime.now()).days)
        except ValueError:
            pass
        dist_parts = []
        swim = race_info.get("swim_km", 0)
        bike = race_info.get("bike_km", 0)
        run = race_info.get("run_km", 0)
        if swim:
            dist_parts.append(f"Swim {swim}km")
        if bike:
            dist_parts.append(f"Bike {bike}km")
        if run:
            dist_parts.append(f"Run {run}km")
        event_str = f"{name} ({etype}), {days_to_race} days away"
        if dist_parts:
            event_str += f"\n{', '.join(dist_parts)}"

    return (
        preamble +
        "\n## Task: Overall training block assessment\n"
        "Analyze this training block honestly. "
        "Praise what's earned, flag what's lacking.\n\n"
        f"EVENT: {event_str}\n\n"
        f"TOTALS:\n{totals_str}\n\n"
        f"WEEKLY BREAKDOWN:\n" + "\n".join(week_summary) +
        "\n\nProvide:\n"
        "1. **Volume assessment**: is training volume sufficient for the target event?\n"
        "2. **Balance**: discipline distribution — any gaps?\n"
        "3. **Progression**: is load building appropriately?\n"
        "4. **Key strengths** in the data\n"
        "5. **Red flags** or concerns\n"
        "6. **Recommendations** for the remaining weeks\n"
        "7. **Race readiness**: honest assessment (1-10) with explanation\n"
    )


async def _call_claude_for_insight(prompt: str, allowed_tools: list[str] | None = None,
                                   user_id: int = 1) -> str | None:
    """Call Claude CLI with a prompt and return the text response."""
    cli = _find_claude_cli()
    if not cli:
        logger.error("Claude CLI not found — cannot generate insight")
        return None
    # Preflight check (cached — near-zero cost if recently verified)
    preflight_err = await _llm_preflight_check()
    if preflight_err:
        logger.error(f"Insight call skipped — preflight failed: {preflight_err}")
        return None
    cmd = [cli, "--bare", "-p", prompt, "--output-format", "stream-json", "--verbose", "--no-session-persistence"]
    if allowed_tools:
        for tool in allowed_tools:
            cmd.extend(["--allowedTools", tool])
    model = await _get_model_override()
    if model:
        cmd += ["--model", model]
    env = _build_cli_env()
    prompt_preview = prompt[:120].replace("\n", " ")
    logger.debug(f"Claude CLI call starting: {prompt_preview}...")
    from services.task_tracker import _insight_active_procs
    start = time.time()
    try:
        proc = await asyncio.create_subprocess_exec(
            *cmd,
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE,
            cwd=str(PROJECT_ROOT),
            env=env,
        )
        _insight_active_procs.add(proc)
        try:
            stdout, stderr = await asyncio.wait_for(proc.communicate(), timeout=300)
        except asyncio.TimeoutError:
            proc.kill()
            await proc.wait()
            elapsed = time.time() - start
            logger.warning(f"Claude CLI timeout ({elapsed:.1f}s) — killed process")
            return None
        finally:
            _insight_active_procs.discard(proc)
        elapsed = time.time() - start
        if proc.returncode == 0:
            text, result_event = _parse_stream_json(stdout.decode("utf-8", errors="replace"))
            logger.debug(f"Claude CLI success ({elapsed:.1f}s, {len(text)} chars)")
            if result_event:
                asyncio.create_task(_track_usage(result_event, "insight", user_id=user_id))
            return text
        stderr_text = stderr.decode("utf-8", errors="replace").strip()
        logger.error(f"Claude CLI failed (rc={proc.returncode}, {elapsed:.1f}s): {stderr_text[:300]}")
        return None
    except Exception as e:
        elapsed = time.time() - start
        logger.error(f"Claude CLI exception ({elapsed:.1f}s): {e}")
        return None


async def get_recent_insights_text(user_id: int = 1) -> str:
    """Return a compact text summary of the last 2 weeks of workout insights from DB.

    Used to inject context into coach chat prompts directly (no file I/O).
    """
    conn = await db.get_db()
    try:
        cutoff = (datetime.now() - timedelta(days=14)).strftime("%Y-%m-%d")
        insights = await db.insight_get_all(conn, user_id=user_id, since_date=cutoff)
    finally:
        await conn.close()

    if not insights:
        return ""

    lines = ["[RECENT WORKOUT INSIGHTS (last 2 weeks)]"]
    for ins in insights:
        lines.append(f"## #{ins['workout_num']} — {ins['workout_type']} — {ins['workout_date']}")
        lines.append(ins["insight"])
        if ins.get("plan_comparison"):
            lines.append(ins["plan_comparison"])
        lines.append("")
    return "\n".join(lines)


async def _generate_insights_batch(since_date: str, to_date: str = "", user_id: int = 1, user_context: dict = None, user_files: dict = None, lang: str = "en", workout_nums: list = None, include_raw_data: bool = False, include_raw_data_nums: set = None):
    """Background task: generate insights for workouts in a date range.

    Server-orchestrated with parallel queues:
    Phase 1 — Specialist analysis (parallel per discipline + nutrition):
      Queue run-coach:    all running workouts sequentially
      Queue bike-coach:   all cycling workouts sequentially
      Queue swim-coach:   all swimming workouts sequentially
      Queue nutrition:    all workouts sequentially
    Phase 2 — Synthesis (sequential):
      main-coach synthesizes each workout from specialist outputs
    """
    import services.task_tracker as tracker
    tracker._insight_batch_cancel = False
    tracker._insight_batch_user = user_id
    async with _insight_status_lock:
        _insight_status.update({"running": True, "total": 0, "completed": 0, "failed": 0, "current": "", "user_id": user_id, "history": _insight_status.get("history", [])})
    logger.info(f"Batch insight generation starting (since={since_date}, to={to_date or 'now'})")

    dd = _user_data_dir(user_id)
    try:
        workouts = _enrich_workouts(_load_summary(dd))

        # Apply merges so insights see combined data (not split workouts)
        loop = asyncio.get_event_loop()
        workouts = await loop.run_in_executor(None, _merge_nearby_workouts, workouts, user_id)

        # Filter by specific workout numbers if provided, otherwise by date range
        if workout_nums:
            workout_nums_set = set(int(n) for n in workout_nums)
            eligible = [w for w in workouts if int(w.get("workout_num", 0)) in workout_nums_set]
        else:
            eligible = [w for w in workouts if w.get("startDate", "")[:10] >= since_date]
            if to_date:
                eligible = [w for w in eligible if w.get("startDate", "")[:10] <= to_date]

        conn = await db.get_db()
        try:
            existing = await db.insight_get_existing_nums(conn, user_id=user_id)
        finally:
            await conn.close()

        # When specific workout_nums are provided (e.g. merge), always regenerate
        if workout_nums:
            to_generate = eligible
        else:
            to_generate = [w for w in eligible if int(w.get("workout_num", 0)) not in existing]
        async with _insight_status_lock:
            _insight_status["total"] = len(to_generate)

        if not to_generate:
            logger.info("No new workouts to generate insights for")
            return

        # Prepare workout context for each
        # Pre-fetch plans and nutrition for all unique dates in one DB connection
        workout_ctx = {}  # wnum -> {w, csv_summary, plan_ctx, nutrition, specialist_prompt, nutrition_prompt}
        unique_dates = list({w.get("startDate", "")[:10] for w in to_generate})
        plans_by_date = {}
        nutrition_by_date = {}
        conn = await db.get_db()
        try:
            for wdate in unique_dates:
                plans_by_date[wdate] = await db.plan_get_by_date(conn, wdate, user_id=user_id)
                nutrition_by_date[wdate] = await db.nutrition_get_day(conn, wdate, user_id=user_id)
        finally:
            await conn.close()

        for w in to_generate:
            wnum = int(w["workout_num"])
            wdate = w.get("startDate", "")[:10]
            csv_path = _find_workout_file(wnum, ".csv", dd) or (dd / _workout_csv_filename(w))
            csv_summary = _build_workout_data_summary(w, csv_path)

            plans = plans_by_date.get(wdate, [])
            nutrition = nutrition_by_date.get(wdate, [])

            plan_ctx = ""
            if plans:
                plan_ctx = "\n".join(
                    f"Plan: {p.get('discipline','').upper()} — {p.get('title','')} | "
                    f"{p.get('duration_planned_min',0)} min, {p.get('distance_planned_km',0)} km, "
                    f"intensity: {p.get('intensity','')}"
                    for p in plans
                )

            # User-provided context (e.g. "felt tired", "new shoes", etc.)
            user_note_text = (user_context or {}).get(str(wnum), "")
            # Append image file references if any
            wnum_files = (user_files or {}).get(str(wnum), [])
            img_refs = ""
            if wnum_files:
                img_refs = "\n".join(f"[IMAGE — use Read tool to view: {fp}]" for fp in wnum_files)
            # Discipline coach always gets images (trail, conditions, food, etc.)
            user_note = f"{user_note_text}\n\n{img_refs}" if img_refs else user_note_text
            # Nutrition coach only gets images if text mentions food + photo reference
            user_note_nutrition = user_note_text
            if img_refs and _note_mentions_food_with_photo(user_note_text):
                user_note_nutrition = f"{user_note_text}\n\n{img_refs}"

            # Extract and save any food mentioned in athlete notes
            if user_note:
                saved_ids = await _extract_and_save_nutrition_from_notes(user_note, wdate, user_id=user_id)
                if saved_ids:
                    logger.info(f"Batch: extracted {len(saved_ids)} meal(s) from notes for #{wnum}")
                    # Reload nutrition to include newly saved meals
                    conn = await db.get_db()
                    try:
                        nutrition = await db.nutrition_get_day(conn, wdate, user_id=user_id)
                    finally:
                        await conn.close()

            # Build specialist prompt with per-km/per-100m sections (same as single-generate)
            disc = w.get("discipline", "")
            merged_nums = w.get("merged_nums")
            sections = _compute_sections(wnum, dd, merged_nums=merged_nums) if disc in ("run", "swim", "bike") else None
            specialist_prompt = ""
            use_raw = include_raw_data or (include_raw_data_nums and wnum in include_raw_data_nums)
            if sections and sections.get("sections"):
                specialist_prompt = _build_specialist_prompt(w, sections, plans, data_dir=dd, include_raw_data=use_raw)
            # Build nutrition prompt with actual meal data
            nutrition_prompt = _build_nutrition_prompt(w, nutrition)

            workout_ctx[wnum] = {
                "w": w, "csv_summary": csv_summary,
                "plan_ctx": plan_ctx, "nutrition": nutrition,
                "user_note": user_note,
                "user_note_nutrition": user_note_nutrition,
                "specialist_prompt": specialist_prompt,
                "nutrition_prompt": nutrition_prompt,
            }

        # ── Phase 1: Parallel specialist queues ──────────────────────────
        coach_map = {"run": "run-coach", "swim": "swim-coach", "bike": "bike-coach"}
        discipline_results = {}  # wnum -> response text
        nutrition_results = {}   # wnum -> response text

        # Group by discipline coach
        discipline_queues = defaultdict(list)  # coach_name -> [wnum, ...]
        strength_wnums = []
        for w in to_generate:
            wnum = int(w["workout_num"])
            disc = w["discipline"]
            coach_name = coach_map.get(disc)
            if coach_name:
                discipline_queues[coach_name].append(wnum)
            else:
                strength_wnums.append(wnum)
                discipline_results[wnum] = ""  # no specialist for strength/other

        # Pre-compute same-day workout context and external weather for each workout
        for wnum_key, ctx in workout_ctx.items():
            w_ = ctx["w"]
            wdate_ = w_.get("startDate", "")[:10]
            ctx["same_day_context"] = _build_same_day_context(workouts, wdate_, wnum_key)

            # External weather (wind, rain) for outdoor workouts
            ctx["external_weather"] = ""
            if str(w_.get("meta_IndoorWorkout", "")).strip() != "1":
                gps = _get_first_gps(wnum_key, dd)
                if gps:
                    start_hour = int(w_.get("startDate", "T12:")[11:13] or 12)
                    ext = await _fetch_external_weather(gps[0], gps[1], wdate_, start_hour)
                    ext_str = _format_external_weather(ext)
                    if ext_str:
                        ctx["external_weather"] = f"\nExternal weather data: {ext_str}"

        async def _run_discipline_queue(coach_name: str, wnums: list):
            """Process all workouts for one discipline coach sequentially.
            Rotates session every 5 workouts to keep context small."""
            SESSION_ROTATE = 5
            for i, wnum in enumerate(wnums):
                if tracker._insight_batch_cancel:
                    return
                # Rotate session: coach_name, coach_name-2, coach_name-3, ...
                batch_num = i // SESSION_ROTATE
                base_name = f"{coach_name}-user{user_id}"
                session_name = base_name if batch_num == 0 else f"{base_name}-{batch_num + 1}"
                ctx = workout_ctx[wnum]
                w = ctx["w"]
                same_day_block = ctx.get("same_day_context", "")
                ext_weather = ctx.get("external_weather", "")
                user_note_block = f"\n\n## ATHLETE NOTES\n{ctx['user_note']}" if ctx.get("user_note") else ""
                preamble_block = f"[ATHLETE CONTEXT]\n{batch_preamble}\n\n" if batch_preamble else ""
                # Use specialist prompt with per-km/per-100m sections when available
                data_block = ctx.get("specialist_prompt") or ctx["csv_summary"]
                prompt = (
                    f"{preamble_block}"
                    f"{_lang_prefix(lang)}"
                    f"{data_block}\n\n"
                    f"{same_day_block}{ext_weather}{user_note_block}\n\n"
                    f"Analyze split-by-split. Be specific with numbers."
                )
                _insight_status["current"] = f"#{wnum} → {coach_name}"
                result, _ = await _call_agent(coach_name, prompt, session_name, max_turns=3, user_id=user_id)
                discipline_results[wnum] = result or "(no response)"

        async def _run_nutrition_queue(wnums: list):
            """Process all workouts for nutrition coach sequentially.
            Rotates session per day — each date gets its own session."""
            last_date = ""
            date_idx = 0
            for wnum in wnums:
                if tracker._insight_batch_cancel:
                    return
                ctx = workout_ctx[wnum]
                w = ctx["w"]
                wdate = w.get("startDate", "")[:10]
                if wdate != last_date:
                    last_date = wdate
                    date_idx += 1
                base_name = f"nutrition-coach-user{user_id}"
                session_name = base_name if date_idx <= 1 else f"{base_name}-d{date_idx}"
                preamble_block = f"[ATHLETE CONTEXT]\n{batch_preamble}\n\n" if batch_preamble else ""
                nutri_note = ctx.get("user_note_nutrition") or ""
                user_note_block = f"\n\n## ATHLETE NOTES\n{nutri_note}" if nutri_note else ""
                prompt = (
                    f"{preamble_block}"
                    f"{_lang_prefix(lang)}"
                    f"{ctx['nutrition_prompt']}{user_note_block}"
                )
                _insight_status["current"] = f"#{wnum} → nutrition-coach"
                result, _ = await _call_agent("nutrition-coach", prompt, session_name, max_turns=3, user_id=user_id)
                nutrition_results[wnum] = result or "(no response)"

        all_wnums = [int(w["workout_num"]) for w in to_generate]

        # Build coach preamble once for all prompts (athlete profile + events + memory)
        try:
            batch_preamble = await _build_coach_preamble(user_id, lang=lang)
        except Exception as e:
            logger.warning(f"Could not build batch preamble: {e}")
            batch_preamble = ""

        # Launch all queues in parallel
        _insight_status["current"] = "Phase 1: specialist analysis (parallel)"
        logger.info(f"Batch phase 1: {len(discipline_queues)} discipline queues + nutrition ({len(all_wnums)} workouts)")

        # Filter nutrition queue: only workouts with nutrition data or food-related notes
        ns = _load_nutrition_settings()
        nutrition_wnums = []
        if ns["pre_insight"]:
            for wnum in all_wnums:
                ctx = workout_ctx[wnum]
                has_nutrition = bool(ctx.get("nutrition"))
                has_food_note = bool(ctx.get("user_note_nutrition", "").strip())
                if has_nutrition or has_food_note:
                    nutrition_wnums.append(wnum)
        skipped = len(all_wnums) - len(nutrition_wnums)
        if skipped:
            logger.debug(f"Nutrition coach: skipping {skipped} workouts (no nutrition data or food notes)")
            for wnum in all_wnums:
                if wnum not in nutrition_wnums:
                    nutrition_results[wnum] = ""

        tasks = []
        for coach_name, wnums in discipline_queues.items():
            logger.debug(f"  Queue {coach_name}: {len(wnums)} workouts")
            tasks.append(_run_discipline_queue(coach_name, wnums))
        if nutrition_wnums:
            tasks.append(_run_nutrition_queue(nutrition_wnums))

        await asyncio.gather(*tasks)

        # ── Phase 2: main-coach synthesis (sequential) ───────────────────
        _insight_status["current"] = "Phase 2: main-coach synthesis"
        logger.info(f"Batch phase 2: main-coach synthesizing {len(to_generate)} workouts")

        # Pre-detect brick groups so we synthesize them together
        bricks = _detect_brick_sessions(workouts)
        brick_by_num = {}  # wnum -> brick group
        for b in bricks:
            for bw in b["workouts"]:
                brick_by_num[int(bw.get("workout_num", 0))] = b
        brick_already_done = set()  # track which brick groups were already synthesized

        for w in to_generate:
            if tracker._insight_batch_cancel:
                logger.info("Batch insight generation cancelled by user")
                break
            wnum = int(w["workout_num"])
            wdate = w.get("startDate", "")[:10]
            wtype = w.get("type", "")

            # Check if this workout is part of a brick
            brick = brick_by_num.get(wnum)
            if brick:
                brick_key = tuple(sorted(int(bw.get("workout_num", 0)) for bw in brick["workouts"]))
                if brick_key in brick_already_done:
                    # Already synthesized as part of the brick group
                    async with _insight_status_lock:
                        _insight_status["completed"] += 1
                    continue
                brick_already_done.add(brick_key)

                # Synthesize entire brick as one combined insight
                brick_workouts = brick["workouts"]
                brick_nums = list(brick_key)
                sorted_bw = sorted(brick_workouts, key=lambda x: x.get("startDate", ""))
                brick_type = " → ".join(bw.get("type", "") for bw in sorted_bw)

                # Compute transitions
                trans_parts = []
                for ti in range(1, len(sorted_bw)):
                    try:
                        pe = datetime.strptime(sorted_bw[ti-1].get("endDate", "")[:19], "%Y-%m-%d %H:%M:%S")
                        cs = datetime.strptime(sorted_bw[ti].get("startDate", "")[:19], "%Y-%m-%d %H:%M:%S")
                        trans_parts.append(f"{(cs - pe).total_seconds() / 60:.0f}min")
                    except (ValueError, TypeError):
                        pass
                trans_str = ", ".join(trans_parts) if trans_parts else "unknown"

                total_dur = sum(_safe_float(bw.get("duration_min")) for bw in sorted_bw)
                total_dist = sum(float(bw.get("distance_km", 0) or 0) for bw in sorted_bw)

                preamble_block = f"[ATHLETE CONTEXT]\n{batch_preamble}\n\n" if batch_preamble else ""

                synthesis_prompt = (
                    f"{preamble_block}"
                    f"{_lang_prefix(lang)}"
                    f"BRICK SESSION: {brick_type} on {wdate}\n"
                    f"Total: {total_dur:.0f} min, {total_dist:.1f} km | Transitions: {trans_str}\n\n"
                )

                # Add each discipline's analysis
                for bw in sorted_bw:
                    bw_num = int(bw.get("workout_num", 0))
                    bw_coach = coach_map.get(bw.get("discipline", ""), "self")
                    bw_disc_resp = discipline_results.get(bw_num, "")
                    bw_dur = _safe_float(bw.get("duration_min"))
                    bw_dist = float(bw.get("distance_km", 0) or 0)
                    synthesis_prompt += (
                        f"## #{bw_num} {bw.get('type', '')} ({bw_dur:.0f}min, {bw_dist:.1f}km)\n"
                        f"**{bw_coach} analysis:**\n{bw_disc_resp}\n\n"
                    )

                # Shared nutrition (use first workout's nutrition result or any available)
                for bw in sorted_bw:
                    bw_num = int(bw.get("workout_num", 0))
                    nutr_resp = nutrition_results.get(bw_num, "")
                    if nutr_resp:
                        synthesis_prompt += f"**nutrition-coach analysis:**\n{nutr_resp}\n\n"
                        break

                # User notes for any brick workout
                for bw in sorted_bw:
                    bw_num = int(bw.get("workout_num", 0))
                    ctx_bw = workout_ctx.get(bw_num, {})
                    if ctx_bw.get("user_note"):
                        synthesis_prompt += f"**Athlete notes for #{bw_num}:** {ctx_bw['user_note']}\n\n"

                synthesis_prompt += (
                    "This is a BRICK SESSION. Synthesize ALL disciplines into ONE combined insight:\n"
                    "**Summary**: one sentence covering the entire brick session\n"
                    "**Per-discipline observations**:\n"
                )
                for bw in sorted_bw:
                    synthesis_prompt += f"  **{bw.get('type', '')}**: 2-3 key observations\n"
                synthesis_prompt += (
                    "**Brick performance**: transition quality, fatigue management\n"
                    "**Nutrition**: fueling strategy for the entire session\n"
                    "**Improve next time**: one specific tip for the brick\n"
                )

                _insight_status["current"] = f"Brick #{'/'.join(str(n) for n in brick_nums)} → main-coach synthesis"
                brick_session = f"insight-synthesis-brick-{'-'.join(str(n) for n in brick_nums)}-user{user_id}"
                result_text, _ = await _call_agent("main-coach", synthesis_prompt, brick_session, max_turns=3, user_id=user_id)

                if result_text:
                    result_text, plan_cmp = _split_plan_comparison(result_text)

                    # Save same insight under ALL brick workout_nums
                    conn = await db.get_db()
                    try:
                        for bw in brick_workouts:
                            bw_num = int(bw.get("workout_num", 0))
                            bw_date = bw.get("startDate", "")[:10]
                            await db.insight_save(conn, bw_num, bw_date, bw.get("type", ""),
                                                  result_text, plan_cmp, user_id=user_id)
                    finally:
                        await conn.close()

                    async with _insight_status_lock:
                        _insight_status["completed"] += len(brick_nums)
                else:
                    logger.warning(f"Brick insight empty for #{'/'.join(str(n) for n in brick_nums)} — agent returned no text")
                    async with _insight_status_lock:
                        _insight_status["failed"] = _insight_status.get("failed", 0) + len(brick_nums)
                continue

            # Regular (non-brick) workout synthesis
            ctx = workout_ctx[wnum]
            disc = w["discipline"]
            coach_name = coach_map.get(disc, "self")

            disc_resp = discipline_results.get(wnum, "")
            nutr_resp = nutrition_results.get(wnum, "")
            plan_section = f"**Plan:**\n{ctx['plan_ctx']}\n\n" if ctx["plan_ctx"] else ""

            dur = _safe_float(w.get("duration_min"))
            dist = w.get("distance_km", 0) or 0
            hr_avg = _safe_float(w.get("HeartRate_average"))

            same_day_block = ctx.get("same_day_context", "")
            user_note_block = f"**Athlete notes:** {ctx['user_note']}\n\n" if ctx.get("user_note") else ""
            preamble_block = f"[ATHLETE CONTEXT]\n{batch_preamble}\n\n" if batch_preamble else ""
            synthesis_prompt = (
                f"{preamble_block}"
                f"{_lang_prefix(lang)}"
                f"Synthesize insight for workout #{wnum} — {wtype} on {wdate} "
                f"({dur:.0f}min, {dist:.1f}km, HR avg {hr_avg:.0f})\n\n"
                + (f"{same_day_block}\n\n" if same_day_block else "")
                + user_note_block
                + f"**{coach_name} analysis:**\n{disc_resp}\n\n"
                f"**nutrition-coach analysis:**\n{nutr_resp}\n\n"
                f"{plan_section}"
                f"Output format:\n"
                f"## #{wnum}\n"
                f"**Summary**: one sentence\n"
                f"**Observations**: 3 bullets (most important findings)\n"
                f"**Nutrition**: pre-workout fueling, during-workout fueling, and post-workout recovery — what was good, what was missing\n"
                f"**Improve next time**: one specific tip\n"
                f"**Plan comparison**: (only if plan exists) planned vs actual"
            )

            _insight_status["current"] = f"#{wnum} → main-coach synthesis"
            synthesis_session = f"insight-synthesis-{wnum}-user{user_id}"
            result_text, _ = await _call_agent("main-coach", synthesis_prompt, synthesis_session, max_turns=3, user_id=user_id)

            if result_text:
                result_text, plan_cmp = _split_plan_comparison(result_text)

                conn = await db.get_db()
                try:
                    await db.insight_save(conn, wnum, wdate, wtype, result_text, plan_cmp, user_id=user_id)
                finally:
                    await conn.close()

                async with _insight_status_lock:
                    _insight_status["completed"] += 1
            else:
                logger.warning(f"Insight empty for #{wnum} — agent returned no text")
                async with _insight_status_lock:
                    _insight_status["failed"] = _insight_status.get("failed", 0) + 1

    except Exception as e:
        import traceback
        logger.error(f"Batch insight generation error: {e}\n{traceback.format_exc()}")
    finally:
        was_cancelled = tracker._insight_batch_cancel
        async with _insight_status_lock:
            completed = _insight_status['completed']
            total = _insight_status['total']
            failed = _insight_status.get('failed', 0)
            logger.info(f"Batch done: {completed}/{total} (failed: {failed}, cancelled: {was_cancelled})")
            _insight_status["running"] = False
            _insight_status["current"] = ""
            # Skip completion notification if cancelled — stop endpoint already saved one
            if not was_cancelled:
                detail = f"{completed}/{total} workouts"
                if failed:
                    detail += f" ({failed} failed)"
                entry = {
                    "label": "Insight Generation",
                    "detail": detail,
                    "status": "done" if failed == 0 else ("error" if completed == 0 else "warning"),
                    "link": "/insights",
                    "finished_at": datetime.now().strftime("%Y-%m-%d %H:%M"),
                }
                _insight_status["history"].insert(0, entry)
                _insight_status["history"] = _insight_status["history"][:50]
        # Persist to DB (skip if cancelled)
        if not was_cancelled:
            try:
                conn = await db.get_db()
                try:
                    await db.notification_add(conn, entry["label"], entry["detail"], status=entry["status"], link="/insights", user_id=user_id)
                finally:
                    await conn.close()
            except Exception:
                pass


async def _maybe_regenerate_insight_for_date(date_str: str, meal_data: dict = None, user_id: int = 1):
    """Regenerate workout insight if relevant nutrition was added AFTER the insight was generated.

    Only regenerates when:
    1. Setting nutrition_regen_enabled is not disabled
    2. No insight generation is already running (batch or single) for this user
    3. Nutrition entry was created AFTER the insight was generated
    4. The meal is relevant to the workout timing (pre/post workout window)
    """
    try:
        # Skip if insight generation is already running — the active generation
        # will already include the latest nutrition data
        if _insight_status.get("running") and _insight_status.get("user_id") == user_id:
            logger.debug(f"Skipping nutrition regen for {date_str} — insight batch already running for user {user_id}")
            return
        async with _active_tasks_lock:
            for tid in _active_tasks:
                if tid.startswith("insight-") and tid.endswith(f"-user{user_id}"):
                    logger.debug(f"Skipping nutrition regen for {date_str} — insight task {tid} already active")
                    return

        ns = _load_nutrition_settings()
        if not ns["regen_enabled"]:
            logger.debug(f"Nutrition insight regen disabled by setting, skipping for {date_str}")
            return
        dd = _user_data_dir(user_id)
        workouts = _enrich_workouts(_load_summary(dd))
        day_workouts = [
            w for w in workouts
            if w.get("startDate", "")[:10] == date_str
        ]
        if not day_workouts:
            return
        bricks = _detect_brick_sessions(workouts)
        reason_str = "relevant nutrition data was added/updated after the original insight was generated"

        conn = await db.get_db()
        try:
            regenerated_wnums = set()
            for w in day_workouts:
                wnum = int(w.get("workout_num", 0))
                existing = await db.insight_get(conn, wnum, user_id=user_id)
                if not existing:
                    continue  # No insight to regenerate

                generated_at = existing.get("generated_at", "")
                if not generated_at:
                    continue

                # Check if any nutrition entries for this date were created
                # AFTER the insight was generated
                cursor = await conn.execute(
                    "SELECT meal_time, meal_type, created_at FROM nutrition_log "
                    "WHERE date = ? AND created_at > ? AND user_id = ?",
                    (date_str, generated_at, user_id)
                )
                newer_meals = await cursor.fetchall()
                if not newer_meals:
                    continue  # No newer nutrition data — skip

                # Check if any of the newer meals are relevant to this workout's timing
                w_start = w.get("startDate", "")
                w_dur = float(w.get("duration_min", 0) or 0)
                nw = _load_nutrition_window()
                has_relevant = any(
                    _meal_relevant_to_workout(
                        row["meal_time"], row["meal_type"], w_start, w_dur,
                        nutrition_window=nw
                    )
                    for row in newer_meals
                )
                if not has_relevant:
                    logger.debug(
                        f"Skipping insight regen for #{wnum} — "
                        f"{len(newer_meals)} newer meal(s) not in workout time window"
                    )
                    continue

                logger.debug(
                    f"Regenerating insight for workout #{wnum} on {date_str} "
                    f"(relevant nutrition added after insight at {generated_at})"
                )

                wdate = w.get("startDate", "")[:10]

                # Check if this workout is part of a brick
                brick_for_w = None
                for b in bricks:
                    bnums = [int(bw.get("workout_num", 0)) for bw in b["workouts"]]
                    if wnum in bnums:
                        brick_for_w = b
                        break

                if brick_for_w:
                    # Regenerate combined brick insight
                    brick_workouts = brick_for_w["workouts"]
                    brick_nums_list = [int(bw.get("workout_num", 0)) for bw in brick_workouts]
                    plans_map = {}
                    for bw in brick_workouts:
                        bw_num = int(bw.get("workout_num", 0))
                        bw_date = bw.get("startDate", "")[:10]
                        plans_map[bw_num] = await db.plan_get_by_date(conn, bw_date, user_id=user_id)

                    insight_text, plan_cmp = await _generate_brick_insight(
                        brick_workouts, plans_map, dd, user_id, reason=reason_str,
                        all_workouts=workouts
                    )
                    if insight_text:
                        for bw in brick_workouts:
                            bw_num = int(bw.get("workout_num", 0))
                            bw_date = bw.get("startDate", "")[:10]
                            await db.insight_save(conn, bw_num, bw_date, bw.get("type", ""),
                                                  insight_text, plan_cmp, user_id=user_id)
                            regenerated_wnums.add(bw_num)
                        logger.debug(f"Regenerated brick insight for workouts {brick_nums_list}")
                        entry = {
                            "label": f"Brick insight #{'/'.join(str(n) for n in brick_nums_list)} regenerated",
                            "detail": "nutrition updated",
                            "status": "done",
                            "link": f"/insights#workout-{brick_nums_list[0]}",
                            "finished_at": datetime.now().strftime("%Y-%m-%d %H:%M"),
                        }
                        async with _insight_status_lock:
                            _insight_status["history"].insert(0, entry)
                            _insight_status["history"] = _insight_status["history"][:50]
                        try:
                            await db.notification_add(conn, entry["label"], entry["detail"], link=entry["link"], user_id=user_id)
                        except Exception:
                            pass
                else:
                    # Regular single-workout regeneration
                    plans = await db.plan_get_by_date(conn, wdate, user_id=user_id)
                    insight_text, plan_cmp = await _generate_insight_for_workout(
                        w, plans, dd, user_id, reason=reason_str,
                        all_workouts=workouts
                    )
                    if insight_text:
                        await db.insight_save(
                            conn, wnum, wdate, w.get("type", ""),
                            insight_text, plan_cmp, user_id=user_id
                        )
                        logger.debug(f"Regenerated insight for workout #{wnum}")
                        wtype = w.get("type", "Workout")
                        entry = {
                            "label": f"Insight #{wnum} regenerated",
                            "detail": f"{wtype} — nutrition updated",
                            "status": "done",
                            "link": f"/insights#workout-{wnum}",
                            "finished_at": datetime.now().strftime("%Y-%m-%d %H:%M"),
                        }
                        async with _insight_status_lock:
                            _insight_status["history"].insert(0, entry)
                            _insight_status["history"] = _insight_status["history"][:50]
                        try:
                            await db.notification_add(conn, entry["label"], entry["detail"], link=entry["link"], user_id=user_id)
                        except Exception:
                            pass
                        regenerated_wnums.add(wnum)

                # No separate brick partner regen needed — brick insight covers all partners
        finally:
            await conn.close()
    except Exception as e:
        logger.error(f"Error in _maybe_regenerate_insight_for_date({date_str}): {e}")


async def _extract_and_save_nutrition_from_notes(user_note: str, wdate: str, user_id: int = 1) -> list[int]:
    """If user_note mentions food, analyze it and save to nutrition_log (if not already there).
    Returns list of new nutrition_log IDs created."""
    if not user_note or not user_note.strip():
        return []

    # Ask Claude to decide if the note contains food and extract it
    prompt = (
        "You are an expert sports nutrition analyst for a triathlon athlete (male, 180cm). "
        "The input may be in Hebrew, English, or mixed. Understand both languages fully.\n\n"
        "The athlete wrote the following context note for a workout. "
        "If the note mentions ANY food or meals (before, during, or after the workout), "
        "extract them. If the note does NOT mention food at all, return an empty array [].\n\n"
        "Return ONLY a valid JSON array (no markdown fences, no extra text). "
        "Each element represents one distinct meal:\n"
        '[{"meal_type":"breakfast|lunch|dinner|snack|pre_workout|during_workout|post_workout",'
        '"meal_time":"HH:MM (24h format) or empty string if not mentioned",'
        '"description":"short description in the SAME language as the input",'
        '"calories":number,"protein_g":number,"carbs_g":number,"fat_g":number,'
        '"hydration_ml":number,'
        '"items":[{"name":"item name","calories":number,"protein_g":number,"carbs_g":number,"fat_g":number}]}]\n\n'
        "Rules:\n"
        "- If no food is mentioned at all, return []\n"
        "- Group foods eaten in one sitting as one meal with multiple items\n"
        "- Each meal MUST have an 'items' array\n"
        "- The meal-level totals MUST equal the sum of all items\n"
        "- Use typical portion sizes for an athletic male (180cm)\n"
        "- Common Israeli portions: hummus plate ~300g, pita ~80g, schnitzel ~200g\n"
        "- hydration_ml: only liquid beverages, 0 if no drink\n"
        "- Always return an array\n\n"
        f"ATHLETE NOTE: {user_note}"
    )

    try:
        result = await _call_claude_for_insight(prompt, user_id=user_id)
        if not result:
            return []
    except Exception as e:
        logger.warning(f"Failed to extract nutrition from notes: {e}")
        return []

    from data_processing.helpers import _parse_json_array_response
    parsed = _parse_json_array_response(result)
    if not parsed:
        return []

    # Load existing nutrition and save new meals in a single connection
    conn = await db.get_db()
    try:
        existing = await db.nutrition_get_day(conn, wdate, user_id=user_id)
        existing_descs = {(m.get("meal_type", ""), m.get("description", "").lower().strip()) for m in existing}

        new_ids = []
        for meal in parsed:
            desc = (meal.get("description") or "").strip()
            mtype = meal.get("meal_type", "snack")
            if (mtype, desc.lower()) in existing_descs:
                logger.debug(f"Skipping duplicate nutrition: {mtype} / {desc}")
                continue
            items = meal.get("items", [])
            notes_json = json.dumps(items, ensure_ascii=False) if items else "[]"
            data = {
                "date": wdate,
                "meal_time": meal.get("meal_time", ""),
                "meal_type": mtype,
                "description": desc,
                "calories": meal.get("calories", 0),
                "protein_g": meal.get("protein_g", 0),
                "carbs_g": meal.get("carbs_g", 0),
                "fat_g": meal.get("fat_g", 0),
                "hydration_ml": meal.get("hydration_ml", 0),
                "notes": notes_json,
                "created_at": datetime.now(tz=timezone.utc).isoformat(),
            }
            new_id = await db.nutrition_create(conn, data, user_id=user_id)
            new_ids.append(new_id)
            logger.debug(f"Saved nutrition from notes: {data['meal_type']} / {data['description']} (id={new_id}) for {wdate}")
    finally:
        await conn.close()

    return new_ids


async def _generate_insight_for_workout(w: dict, plans: list, data_dir: Path = None, user_id: int = 1, reason: str = "", user_note: str = "", lang: str = "en", all_workouts: list = None, include_raw_data: bool = False) -> tuple[str, str]:
    """Generate insight using multi-agent pipeline.

    Flow:
    1. Determine discipline via _classify_type()
    2. Call discipline specialist + nutrition-coach in parallel
    3. Head coach synthesizes all specialist outputs
    4. Brief summary sent to main-coach session
    """
    wnum = int(w.get("workout_num", 0))
    disc = _classify_type(w.get("type", ""))
    wdate = w.get("startDate", "")[:10]

    # Extract and save any food mentioned in athlete notes (before loading nutrition)
    if user_note:
        saved_ids = await _extract_and_save_nutrition_from_notes(user_note, wdate, user_id=user_id)
        if saved_ids:
            logger.debug(f"Extracted {len(saved_ids)} meal(s) from notes for workout #{wnum} on {wdate}")

    preamble = await _build_coach_preamble(user_id, lang=lang)
    merged_nums = w.get("merged_nums")
    sections = _compute_sections(wnum, data_dir, merged_nums=merged_nums)

    # Build workout data for specialists
    if include_raw_data:
        logger.debug(f"Workout #{wnum}: including raw CSV data in specialist prompt (athlete requested)")
    if sections and sections["sections"]:
        specialist_data = _build_specialist_prompt(w, sections, plans, preamble, data_dir=data_dir, include_raw_data=include_raw_data)
    else:
        specialist_data = _build_workout_prompt(w, plans, preamble)

    specialist_data = _lang_prefix(lang) + specialist_data

    # If this is a regeneration, prepend context so agents don't get confused
    if reason:
        specialist_data = (
            f"⚠️ REGENERATION: This is an UPDATE to a previously generated insight for workout #{wnum}. "
            f"Reason: {reason}. "
            f"Produce a complete fresh analysis — do NOT refer to or skip anything because you analyzed it before. "
            f"The previous insight will be fully replaced.\n\n"
        ) + specialist_data

    # User-provided context (e.g. "felt tired", "new shoes", etc.)
    if user_note:
        specialist_data += f"\n\n## ATHLETE NOTES\n{user_note}"

    # Fetch external weather (wind, rain) from Open-Meteo if outdoor workout with GPS
    if str(w.get("meta_IndoorWorkout", "")).strip() != "1":
        gps = _get_first_gps(wnum, data_dir)
        if gps:
            start_hour = int(w.get("startDate", "T12:")[11:13] or 12)
            ext_weather = await _fetch_external_weather(gps[0], gps[1], wdate, start_hour)
            ext_str = _format_external_weather(ext_weather)
            if ext_str and specialist_data:
                specialist_data += f"\nExternal weather data: {ext_str}"

    # Build recovery & sleep context (with per-user HR settings)
    if all_workouts is None:
        all_workouts = _enrich_workouts(_load_summary(data_dir))
    hr_kw = {}
    try:
        conn_hr = await db.get_db()
        try:
            hr_db = await db.hr_settings_get(conn_hr, user_id)
        finally:
            await conn_hr.close()
        if hr_db and hr_db.get("hr_max", 0) > 0:
            hr_kw = {"hr_rest": hr_db["hr_rest"], "hr_max": hr_db["hr_max"], "hr_lthr": hr_db["hr_lthr"]}
    except Exception:
        pass
    recovery_context = _build_recovery_sleep_context(wdate, all_workouts, data_dir, **hr_kw)
    if recovery_context and specialist_data:
        specialist_data += "\n\n" + recovery_context

    same_day_context = _build_same_day_context(all_workouts, wdate, wnum)
    if same_day_context and specialist_data:
        specialist_data += same_day_context

    # Detect if this workout is part of a brick session
    brick_context = ""
    bricks = _detect_brick_sessions(all_workouts)
    for brick in bricks:
        brick_nums = [int(bw.get("workout_num", 0)) for bw in brick["workouts"]]
        if wnum in brick_nums:
            other_workouts = [bw for bw in brick["workouts"] if int(bw.get("workout_num", 0)) != wnum]
            parts = []
            for bw in other_workouts:
                parts.append(f"#{bw.get('workout_num')} {bw.get('type','')} "
                           f"({float(bw.get('duration_min',0)):.0f}min, "
                           f"{float(bw.get('distance_km',0) or 0):.1f}km)")
            transition = brick.get("transition_times", [])
            trans_str = f", transition: {', '.join(f'{t:.0f}min' for t in transition)}" if transition else ""
            brick_context = (
                f"\n\n## BRICK SESSION\n"
                f"This workout is part of a brick session ({brick.get('brick_type', '')}).\n"
                f"Other workouts in this brick: {'; '.join(parts)}{trans_str}\n"
                f"Analyze this workout considering the brick context — fatigue from the preceding "
                f"discipline, transition quality, and overall brick performance."
            )
            break
    if brick_context and specialist_data:
        specialist_data += brick_context

    # Build nutrition data
    conn = await db.get_db()
    try:
        nutrition = await db.nutrition_get_day(conn, wdate, user_id=user_id)
    finally:
        await conn.close()

    # Check if nutrition should be included in insight — skip if no data AND no food notes
    ns = _load_nutrition_settings()
    has_food_note = bool(user_note and _FOOD_KEYWORDS.search(user_note))
    include_nutrition = ns["pre_insight"] and (bool(nutrition) or has_food_note)

    nutrition_prompt = _lang_prefix(lang) + _build_nutrition_prompt(w, nutrition)
    # Only pass text notes to nutrition coach (no images unless food+photo detected)
    if user_note:
        # Strip image refs unless food is mentioned with photo reference
        note_for_nutrition = user_note
        if "[IMAGE" in user_note and not _note_mentions_food_with_photo(user_note.split("[IMAGE")[0]):
            note_for_nutrition = user_note.split("\n\n[IMAGE")[0].strip()
        if note_for_nutrition:
            nutrition_prompt += f"\n\n## ATHLETE NOTES\n{note_for_nutrition}"

    # Determine which specialist to call
    agent_map = {"run": "run-coach", "swim": "swim-coach", "bike": "bike-coach"}
    specialist_agent = agent_map.get(disc)

    # Call specialists in parallel (per-user persistent session)
    tasks = []
    if specialist_agent and specialist_data:
        tasks.append(_call_agent(specialist_agent, specialist_data, f"{specialist_agent}-user{user_id}", max_turns=3, user_id=user_id))
    if include_nutrition:
        tasks.append(_call_agent("nutrition-coach", nutrition_prompt, f"nutrition-coach-user{user_id}", max_turns=3, user_id=user_id))
    else:
        logger.debug(f"Skipping nutrition analysis for workout #{w.get('workout_num')} (disabled or no data)")

    results = await asyncio.gather(*tasks, return_exceptions=True)

    # Collect specialist outputs
    specialist_outputs = []
    for i, r in enumerate(results):
        if isinstance(r, Exception):
            logger.error(f"Specialist task {i} failed: {r}")
            continue
        text, _ = r
        if text:
            specialist_outputs.append(text)

    if not specialist_outputs:
        # Fallback to single-stage
        logger.warning(f"All specialists failed for #{wnum}, falling back to single-stage")
        fallback_prompt = _build_workout_prompt(w, plans)
        if recovery_context:
            fallback_prompt += "\n\n" + recovery_context
        text = await _call_claude_for_insight(fallback_prompt, user_id=user_id)
        if not text:
            return "", ""
        if plans:
            text, plan_cmp = _split_plan_comparison(text)
            return text, plan_cmp
        return text, ""

    # Head coach synthesis
    synthesis_input = f"WORKOUT: {w.get('type', 'Unknown')} on {w.get('startDate', '')[:10]}, {float(w.get('duration_min', 0)):.1f} min\n\n"

    if same_day_context:
        synthesis_input += same_day_context.lstrip("\n") + "\n\n"

    if brick_context:
        synthesis_input += brick_context + "\n\n"
    if recovery_context:
        synthesis_input += f"## {recovery_context}\n\n"
    for i, output in enumerate(specialist_outputs):
        label = "DISCIPLINE ANALYSIS" if i == 0 and specialist_agent else "NUTRITION ANALYSIS"
        synthesis_input += f"## {label}\n{output}\n\n"

    if plans:
        plan_lines = []
        for p in plans:
            plan_lines.append(
                f"- {p.get('discipline','').upper()}: {p.get('title','')} | "
                f"{p.get('description','')} | Duration: {p.get('duration_planned_min',0)} min, "
                f"Distance: {p.get('distance_planned_km',0)} km, Intensity: {p.get('intensity','')}"
            )
        synthesis_input += "## PLANNED WORKOUT\n" + "\n".join(plan_lines) + "\n\n"

    synthesis_input += (
        f"Respond ENTIRELY in **{_lang_label(lang)}**.\n\n"
        "Synthesize into:\n"
        "**Summary**: one sentence\n"
        "**Observations**:\n- bullet 1\n- bullet 2\n- bullet 3\n"
        "**Nutrition**: pre-workout fueling, during-workout fueling, and post-workout recovery — what was good, what was missing\n"
        "**Improve next time**: one specific tip\n"
    )
    if plans:
        synthesis_input += "**Plan comparison**: planned vs actual for each metric\n"

    # Use unique session per workout to avoid duplicate detection from persistent memory
    synthesis_session = f"insight-synthesis-{wnum}-user{user_id}"
    synthesis_text, _ = await _call_agent("main-coach", synthesis_input, synthesis_session, max_turns=2, user_id=user_id)

    if not synthesis_text:
        # Fallback: use first specialist output directly
        synthesis_text = specialist_outputs[0]

    if plans:
        synthesis_text, plan_cmp = _split_plan_comparison(synthesis_text)
    else:
        plan_cmp = ""

    return synthesis_text, plan_cmp


async def _generate_brick_insight(brick_workouts: list[dict], plans_map: dict, data_dir: Path, user_id: int = 1,
                                   reason: str = "", user_notes: dict = None, lang: str = "en", all_workouts: list = None, include_raw_data: bool = False) -> tuple[str, str]:
    """Generate a single combined insight for a brick session.

    Calls each discipline specialist for their workout, nutrition once for the date,
    then synthesizes everything into ONE combined brick insight.
    """
    user_notes = user_notes or {}
    preamble = await _build_coach_preamble(user_id, lang=lang)
    agent_map = {"run": "run-coach", "swim": "swim-coach", "bike": "bike-coach"}

    # Shared date (brick workouts are same day)
    wdate = brick_workouts[0].get("startDate", "")[:10]

    # Extract nutrition from notes for all brick workouts
    for bw in brick_workouts:
        bw_num = int(bw.get("workout_num", 0))
        note = user_notes.get(str(bw_num), "")
        if note:
            await _extract_and_save_nutrition_from_notes(note, wdate, user_id=user_id)

    # Build transition info
    transition_times = []
    sorted_bw = sorted(brick_workouts, key=lambda x: x.get("startDate", ""))
    for i in range(1, len(sorted_bw)):
        try:
            prev_end = datetime.strptime(sorted_bw[i-1].get("endDate", "")[:19], "%Y-%m-%d %H:%M:%S")
            curr_start = datetime.strptime(sorted_bw[i].get("startDate", "")[:19], "%Y-%m-%d %H:%M:%S")
            transition_times.append((curr_start - prev_end).total_seconds() / 60)
        except (ValueError, TypeError):
            pass

    brick_type = " → ".join(bw.get("type", "") for bw in sorted_bw)
    trans_str = ", ".join(f"{t:.0f}min" for t in transition_times) if transition_times else "unknown"

    # Phase 1: Call discipline specialists in parallel
    tasks = []
    task_labels = []
    for bw in sorted_bw:
        bw_num = int(bw.get("workout_num", 0))
        disc = _classify_type(bw.get("type", ""))
        agent = agent_map.get(disc)
        if not agent:
            continue

        merged_nums = bw.get("merged_nums")
        sections = _compute_sections(bw_num, data_dir, merged_nums=merged_nums)
        plans = plans_map.get(bw_num, [])

        if sections and sections.get("sections"):
            prompt = _build_specialist_prompt(bw, sections, plans, preamble, data_dir=data_dir, include_raw_data=include_raw_data)
        else:
            prompt = _build_workout_prompt(bw, plans, preamble)

        prompt = _lang_prefix(lang) + prompt

        if reason:
            prompt = (
                f"⚠️ REGENERATION: UPDATE to a previously generated insight. Reason: {reason}. "
                f"Produce a complete fresh analysis.\n\n"
            ) + prompt

        note = user_notes.get(str(bw_num), "")
        if note:
            prompt += f"\n\n## ATHLETE NOTES\n{note}"

        prompt += (
            f"\n\n## BRICK SESSION\n"
            f"This is part of a brick session: {brick_type} (transitions: {trans_str}).\n"
            f"Analyze considering the brick context."
        )

        tasks.append(_call_agent(agent, prompt, f"{agent}-user{user_id}", max_turns=3, user_id=user_id))
        task_labels.append(f"#{bw_num} {bw.get('type', '')}")

    # Nutrition (once for the date)
    conn = await db.get_db()
    try:
        nutrition = await db.nutrition_get_day(conn, wdate, user_id=user_id)
    finally:
        await conn.close()

    ns = _load_nutrition_settings()
    if ns["pre_insight"] and nutrition:
        # Use the first workout for nutrition context (covers the whole session)
        nutrition_prompt = _lang_prefix(lang)
        nutrition_prompt += _build_nutrition_prompt(sorted_bw[0], nutrition)
        nutrition_prompt += (
            f"\n\nThis is a brick session ({brick_type}). "
            f"Analyze fueling for the ENTIRE brick, not just one leg."
        )
        tasks.append(_call_agent("nutrition-coach", nutrition_prompt, f"nutrition-coach-user{user_id}", max_turns=3, user_id=user_id))
        task_labels.append("Nutrition")

    results = await asyncio.gather(*tasks, return_exceptions=True)

    # Collect specialist outputs with labels
    specialist_outputs = []
    for i, r in enumerate(results):
        if isinstance(r, Exception):
            logger.error(f"Brick specialist {task_labels[i]} failed: {r}")
            continue
        text, _ = r
        if text:
            specialist_outputs.append((task_labels[i], text))

    if not specialist_outputs:
        return "", ""

    # Build recovery context (with per-user HR settings)
    if all_workouts is None:
        all_workouts = _enrich_workouts(_load_summary(data_dir))
    hr_kw = {}
    try:
        conn_hr = await db.get_db()
        try:
            hr_db = await db.hr_settings_get(conn_hr, user_id)
        finally:
            await conn_hr.close()
        if hr_db and hr_db.get("hr_max", 0) > 0:
            hr_kw = {"hr_rest": hr_db["hr_rest"], "hr_max": hr_db["hr_max"], "hr_lthr": hr_db["hr_lthr"]}
    except Exception:
        pass
    recovery_context = _build_recovery_sleep_context(wdate, all_workouts, data_dir, **hr_kw)

    # Phase 2: Head coach synthesis — ONE combined brick insight
    total_dur = sum(float(bw.get("duration_min", 0)) for bw in sorted_bw)
    total_dist = sum(float(bw.get("distance_km", 0) or 0) for bw in sorted_bw)
    synthesis_input = (
        f"BRICK SESSION: {brick_type} on {wdate}\n"
        f"Total: {total_dur:.0f} min, {total_dist:.1f} km | Transitions: {trans_str}\n\n"
    )

    if recovery_context:
        synthesis_input += f"## {recovery_context}\n\n"

    for label, output in specialist_outputs:
        synthesis_input += f"## {label.upper()} ANALYSIS\n{output}\n\n"

    all_plans = []
    for bw in sorted_bw:
        bw_num = int(bw.get("workout_num", 0))
        all_plans.extend(plans_map.get(bw_num, []))
    if all_plans:
        plan_lines = [
            f"- {p.get('discipline','').upper()}: {p.get('title','')} | "
            f"{p.get('duration_planned_min',0)} min, {p.get('distance_planned_km',0)} km"
            for p in all_plans
        ]
        synthesis_input += "## PLANNED WORKOUT\n" + "\n".join(plan_lines) + "\n\n"

    synthesis_input += (
        f"Respond ENTIRELY in **{_lang_label(lang)}**.\n\n"
        "This is a BRICK SESSION insight. Synthesize ALL disciplines into ONE combined insight:\n"
        "**Summary**: one sentence covering the entire brick session\n"
        "**Per-discipline observations**:\n"
    )
    for bw in sorted_bw:
        synthesis_input += f"  **{bw.get('type', '')}**: 2-3 key observations\n"
    synthesis_input += (
        "**Brick performance**: transition quality, fatigue management, overall brick execution\n"
        "**Nutrition**: fueling strategy for the entire session\n"
        "**Improve next time**: one specific tip for the brick session\n"
    )
    if all_plans:
        synthesis_input += "**Plan comparison**: planned vs actual for each leg\n"

    wnums = [int(bw.get("workout_num", 0)) for bw in sorted_bw]
    synthesis_session = f"insight-synthesis-brick-{'-'.join(str(n) for n in wnums)}-user{user_id}"
    synthesis_text, _ = await _call_agent("main-coach", synthesis_input, synthesis_session, max_turns=3, user_id=user_id)

    if not synthesis_text:
        synthesis_text = specialist_outputs[0][1]

    if all_plans:
        synthesis_text, plan_cmp = _split_plan_comparison(synthesis_text)
    else:
        plan_cmp = ""

    return synthesis_text, plan_cmp


def _load_recovery_data_range(data_dir: Path = None, from_date: str = "", to_date: str = "") -> list[dict]:
    """Load recovery data (sleep, HR, HRV) for a date range as a list."""
    csv_path = (data_dir or TRAINING_DATA) / "recovery_data.csv"
    if not csv_path.exists():
        return []
    rows = []
    with open(csv_path, newline="") as f:
        for row in csv.DictReader(f):
            d = row.get("date", "")
            if from_date and d < from_date:
                continue
            if to_date and d > to_date:
                continue
            rows.append(row)
    return rows


def _build_period_prompt(category: str, insights: list, workouts: list,
                         recovery: list, nutrition: list, preamble: str,
                         from_date: str, to_date: str, specialist_outputs: dict = None) -> str:
    """Build prompt for a period insight category."""
    date_label = f"{from_date} to {to_date}"

    if category in ("run", "swim", "bike"):
        types = _CATEGORY_TYPES[category]
        disc_workouts = [w for w in workouts if w.get("type") in types]
        disc_insights = [i for i in insights if i.get("workout_type") in types]

        if not disc_workouts:
            return ""

        # Build workout summary
        w_lines = []
        for w in disc_workouts[-15:]:  # last 15 max
            dur = _safe_float(w.get("duration_min"))
            dist = w.get("distance_km", 0) or 0
            hr = _safe_float(w.get("HeartRate_average"))
            w_lines.append(
                f"- #{w.get('workout_num')} {w.get('startDate','')[:10]} "
                f"{dur:.0f}min {float(dist):.1f}km HR:{hr:.0f}"
            )

        # Build insight snippets
        i_lines = []
        for i in disc_insights[-10:]:
            snippet = i["insight"][:250].rsplit(" ", 1)[0] + "..." if len(i["insight"]) > 250 else i["insight"]
            i_lines.append(f"### #{i['workout_num']} ({i['workout_date']})\n{snippet}")

        return (
            f"{preamble}\n\n"
            f"## {category.upper()} PERIOD SUMMARY ({date_label})\n\n"
            f"### Workouts ({len(disc_workouts)} total)\n" + "\n".join(w_lines) + "\n\n"
            f"### Per-Workout Insights\n" + "\n\n".join(i_lines) + "\n\n"
            "Provide a period summary:\n"
            "1. **Volume & consistency**: total sessions, distance, duration. Gaps?\n"
            "2. **Progression**: is performance improving? (pace, power, SWOLF)\n"
            "3. **Patterns**: recurring strengths or weaknesses across workouts\n"
            "4. **Key concern**: biggest issue to address\n"
            "5. **Recommendation**: one specific action for the next period\n"
        )

    elif category == "nutrition":
        if not nutrition:
            return ""
        meal_lines = []
        for m in nutrition[-30:]:
            meal_lines.append(
                f"- {m.get('date','')} {m.get('meal_type','')}: {m.get('description','')} "
                f"({m.get('calories',0)}cal P{m.get('protein_g',0)}g C{m.get('carbs_g',0)}g F{m.get('fat_g',0)}g)"
            )
        workout_summary = f"{len(workouts)} workouts in period"
        total_cal_burned = sum(_safe_float(w.get("ActiveEnergyBurned_sum")) for w in workouts)

        return (
            f"{preamble}\n\n"
            f"## NUTRITION PERIOD SUMMARY ({date_label})\n\n"
            f"Training load: {workout_summary}, ~{total_cal_burned:.0f} active calories total\n\n"
            f"### Meals logged ({len(nutrition)} entries)\n" + "\n".join(meal_lines) + "\n\n"
            "Analyze nutrition patterns:\n"
            "1. **Calorie balance**: intake vs expenditure trend\n"
            "2. **Macros**: protein/carb/fat ratios — adequate for training load?\n"
            "3. **Timing**: pre/during/post workout fueling patterns\n"
            "4. **Gaps**: missed meals, under-fueling days, hydration\n"
            "5. **Recommendation**: one specific nutrition change\n"
        )

    elif category == "recovery":
        if not recovery:
            return ""
        rec_lines = []
        for r in recovery[-14:]:
            sleep_h = _safe_float(r.get("sleep_total_min")) / 60
            deep = _safe_float(r.get("sleep_deep_min"))
            rhr = _safe_float(r.get("resting_hr"))
            hrv = _safe_float(r.get("hrv_sdnn_ms"))
            rec_lines.append(
                f"- {r.get('date','')} Sleep:{sleep_h:.1f}h (deep:{deep:.0f}min) RHR:{rhr:.0f} HRV:{hrv:.0f}"
            )
        workout_days = set(w.get("startDate", "")[:10] for w in workouts)
        rest_days_count = 0
        if from_date and to_date:
            d = datetime.strptime(from_date, "%Y-%m-%d")
            end = datetime.strptime(to_date, "%Y-%m-%d")
            while d <= end:
                if d.strftime("%Y-%m-%d") not in workout_days:
                    rest_days_count += 1
                d += timedelta(days=1)

        return (
            f"{preamble}\n\n"
            f"## RECOVERY & SLEEP SUMMARY ({date_label})\n\n"
            f"Training days: {len(workout_days)}, Rest days: {rest_days_count}\n\n"
            f"### Recovery Data\n" + "\n".join(rec_lines) + "\n\n"
            "Analyze recovery patterns:\n"
            "1. **Sleep quality**: total hours, deep sleep adequacy (target >60min)\n"
            "2. **Sleep consistency**: variation in sleep duration\n"
            "3. **HRV trend**: improving, declining, or stable?\n"
            "4. **Resting HR**: any upward trends indicating fatigue?\n"
            "5. **Rest days**: adequate recovery between hard sessions?\n"
            "6. **Recommendation**: one specific recovery improvement\n"
        )

    elif category == "overall":
        if not specialist_outputs:
            return ""
        parts = []
        for cat, text in specialist_outputs.items():
            if text:
                parts.append(f"## {cat.upper()} ANALYSIS\n{text}")

        return (
            f"{preamble}\n\n"
            f"## OVERALL PERIOD ASSESSMENT ({date_label})\n\n"
            + "\n\n".join(parts) + "\n\n"
            "Synthesize all specialist analyses into an overall assessment:\n"
            "1. **Period summary**: one paragraph overview\n"
            "2. **Biggest wins**: what went well\n"
            "3. **Biggest concerns**: what needs attention\n"
            "4. **Balance**: are all three disciplines progressing? Any neglected?\n"
            "5. **Race readiness**: honest update (if a race is approaching)\n"
            "6. **Top 3 priorities**: for the next training period\n"
        )

    return ""
