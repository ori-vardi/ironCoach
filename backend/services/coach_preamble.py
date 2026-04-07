"""Build coach preamble with athlete profile and context."""

import asyncio
from datetime import datetime
import database as db
from config import INSIGHT_COACH_PREAMBLE_TEMPLATE, TRAINING_DATA, logger
from data_processing import (
    _load_summary, _filter_hidden, _compute_recovery_timeline,
    _recovery_label, _load_recovery_data, _safe_float,
    resolve_hr_settings,
)

_HE_DAYS = ["שני", "שלישי", "רביעי", "חמישי", "שישי", "שבת", "ראשון"]


def _get_current_recovery(user_data_dir, user_id: int = 1,
                          hr_rest: float = None, hr_max: float = None, hr_lthr: float = None) -> dict | None:
    """Compute current recovery stats from workout + recovery CSVs."""
    if not user_data_dir.exists():
        return None
    workouts = _load_summary(user_data_dir)
    workouts = _filter_hidden(workouts, user_id)
    result = _compute_recovery_timeline(workouts, hr_rest=hr_rest, hr_max=hr_max, hr_lthr=hr_lthr)
    if not result["timeline"]:
        return None
    last = result["timeline"][-1]
    label, _ = _recovery_label(last["recovery"])
    stats = {
        "recovery": round(last["recovery"]),
        "fitness": round(last["fitness"]),
        "fatigue": round(last["fatigue"]),
        "tsb": round(last.get("form", last["recovery"] - 50)),
        "label": label,
    }

    # Latest recovery data (sleep, RHR, HRV) — tag with date so LLM knows freshness
    recovery_raw = _load_recovery_data(user_data_dir)
    if recovery_raw:
        today = datetime.now().strftime("%Y-%m-%d")
        data_date = today if today in recovery_raw else max(recovery_raw.keys())
        r = recovery_raw[data_date]
        stats["recovery_data_date"] = data_date
        if r.get("resting_hr"):
            stats["resting_hr"] = round(_safe_float(r["resting_hr"]))
        if r.get("hrv_sdnn_ms"):
            stats["hrv_ms"] = round(_safe_float(r["hrv_sdnn_ms"]))
        if r.get("sleep_total_min"):
            mins = _safe_float(r["sleep_total_min"])
            stats["sleep_hours"] = f"{int(mins // 60)}h{int(mins % 60)}m"

    return stats


def _format_now(lang: str = "en") -> str:
    """Format current date/time with day name in the given language."""
    now = datetime.now()
    if lang == "he":
        day_name = f"יום {_HE_DAYS[now.weekday()]}"
    else:
        day_name = now.strftime("%A")  # e.g. "Thursday"
    return f"{now.strftime('%Y-%m-%d %H:%M')} ({day_name})"


async def _build_coach_preamble(user_id: int = 1, agent_name: str = None, lang: str = "en") -> str:
    """Build coach preamble with dynamic athlete profile and events."""
    conn = await db.get_db()
    try:
        profile = await db.user_get_profile(conn, user_id)
        events = await db.events_get_all(conn, user_id)
        memories = await db.memory_get_all(conn, user_id)
        agent_memories = []
        if agent_name:
            agent_memories = await db.agent_memory_get_all(conn, user_id, agent_name)
    finally:
        await conn.close()

    # Current date/time so agents know "now"
    parts = [f"- **Current date/time**: {_format_now(lang)}"]
    if profile:
        name = profile.get("display_name") or profile.get("username") or "Athlete"
        parts.append(f"- Name: {name}")
        sex = profile.get("sex") or "male"
        birth = profile.get("birth_date") or ""
        height = profile.get("height_cm") or 0
        info_line = f"- {sex.capitalize()}"
        if birth:
            try:
                born_year = datetime.strptime(birth, "%Y-%m-%d").year
                info_line += f", born {born_year}"
            except ValueError:
                pass
        if height:
            info_line += f", {int(height)} cm"
        parts.append(info_line)
    else:
        parts.append("- Profile not set")

    # Add events info
    if events:
        for ev in events:
            primary = " **(PRIMARY)**" if ev.get("is_primary") else ""
            name = ev.get("event_name", "Unnamed Event")
            date = ev.get("event_date", "TBD")
            etype = ev.get("event_type", "").replace("_", " ").title()
            try:
                days = max(0, (datetime.strptime(date, "%Y-%m-%d") - datetime.now()).days)
                date_str = f"{date} ({days} days away)"
            except ValueError:
                date_str = date
            parts.append(f"- Event: **{name}** ({etype}) — {date_str}{primary}")
            swim = ev.get("swim_km", 0)
            bike = ev.get("bike_km", 0)
            run = ev.get("run_km", 0)
            dist_parts = []
            if swim:
                dist_parts.append(f"Swim {swim}km")
            if bike:
                dist_parts.append(f"Bike {bike}km")
            if run:
                dist_parts.append(f"Run {run}km")
            if dist_parts:
                parts.append(f"  Distances: {', '.join(dist_parts)}")
            goal = ev.get("goal", "")
            if goal:
                parts.append(f"  Goal: {goal}")
            notes = ev.get("notes", "")
            if notes:
                parts.append(f"  {notes}")

    # Add coaching memory
    if memories:
        parts.append("\n### Coach Memory (athlete-provided context)")
        total_mem_len = 0
        for m in memories:
            entry = m['content'][:500]
            if total_mem_len + len(entry) > 5000:
                break
            parts.append(f"- {entry}")
            total_mem_len += len(entry)

    # Add agent-specific memory
    if agent_memories:
        parts.append(f"\n### {agent_name} Memory (agent-specific context)")
        total_len = 0
        for m in agent_memories:
            entry = m['content'][:500]
            if total_len + len(entry) > 5000:
                break
            parts.append(f"- {entry}")
            total_len += len(entry)

    # Add current recovery status (live-computed, not cached)
    user_data_dir = TRAINING_DATA / "users" / str(user_id)
    # Load per-user HR settings for recovery computation + preamble
    hr_rest_val = hr_max_val = hr_lthr_val = None
    hr_settings = None
    try:
        conn_hr = await db.get_db()
        try:
            hr_db = await db.hr_settings_get(conn_hr, user_id)
        finally:
            await conn_hr.close()
        hr_settings = resolve_hr_settings(hr_db, profile)
        if hr_settings.get("hr_max", 0) > 0:
            hr_rest_val = hr_settings["hr_rest"]
            hr_max_val = hr_settings["hr_max"]
            hr_lthr_val = hr_settings["hr_lthr"]
    except Exception:
        pass
    try:
        loop = asyncio.get_event_loop()
        recovery_stats = await loop.run_in_executor(
            None, _get_current_recovery, user_data_dir, user_id,
            hr_rest_val, hr_max_val, hr_lthr_val)
        if recovery_stats:
            today = datetime.now().strftime("%Y-%m-%d")
            data_date = recovery_stats.get("recovery_data_date", "")
            is_stale = data_date and data_date != today
            header = "\n### Current Recovery Status (live data — use THESE numbers, not old insights)"
            if is_stale:
                header += f"\n**WARNING: Sleep/RHR/HRV data is from {data_date}, NOT today ({today}). Tell the athlete this data is not from today when referencing it.**"
            parts.append(header)
            parts.append(f"- Recovery: **{recovery_stats['recovery']}%** ({recovery_stats['label']})")
            parts.append(f"- Fitness (CTL): **{recovery_stats['fitness']}**")
            parts.append(f"- Fatigue (ATL): **{recovery_stats['fatigue']}**")
            parts.append(f"- Form (TSB): **{recovery_stats['tsb']}**")
            if recovery_stats.get('resting_hr'):
                parts.append(f"- Resting HR: **{recovery_stats['resting_hr']} bpm** (from {data_date})")
            if recovery_stats.get('hrv_ms'):
                parts.append(f"- HRV: **{recovery_stats['hrv_ms']} ms** (from {data_date})")
            if recovery_stats.get('sleep_hours'):
                parts.append(f"- Sleep: **{recovery_stats['sleep_hours']}** (from {data_date})")
    except Exception as e:
        logger.debug("Failed to inject recovery stats into preamble: %s", e)

    # Add HR zone data so all coaches know the athlete's zones
    if hr_settings and hr_settings.get("hr_max", 0) > 0:
        parts.append("\n### HR Zones")
        parts.append(f"- HR Max: **{int(hr_settings['hr_max'])} bpm** | HR Rest: **{int(hr_settings['hr_rest'])} bpm** | LTHR: **{int(hr_settings['hr_lthr'])} bpm**")
        zones = hr_settings.get("hr_zones", [])
        if zones:
            zone_strs = []
            for name, lo, hi in zones:
                if hi >= 999:
                    zone_strs.append(f"{name}: {lo}+")
                else:
                    zone_strs.append(f"{name}: {lo}-{hi}")
            parts.append(f"- Zones: {' | '.join(zone_strs)}")
        src = hr_settings.get("source", "unknown")
        locked = hr_settings.get("locked", False)
        parts.append(f"- Source: {src}{' (locked — manual override)' if locked else ' (auto-updated)'}")

    # Add data directory info for file access
    if user_data_dir.exists():
        parts.append("\n### Data files")
        parts.append(f"- User data directory: `{user_data_dir}`")
        parts.append(f"- Workout summary: `{user_data_dir}/00_workouts_summary.csv`")
        parts.append(f"- Per-workout time-series CSVs (~3s resolution): `{user_data_dir}/workouts/YYYY-MM/workout_NNN_DATE_TYPE.csv`")
        parts.append(f"- Apple splits (km/mile): `{user_data_dir}/workouts/YYYY-MM/workout_NNN_DATE_TYPE.splits.json`")
        parts.append(f"- Recovery data: `{user_data_dir}/recovery_data.csv`")
        parts.append(f"- Body metrics: `{user_data_dir}/body_metrics.csv`")
        parts.append("- For interval analysis, READ the raw time-series CSV — it has HR/pace at ~3s resolution vs per-km summaries")

    athlete_info = "\n".join(parts)
    return INSIGHT_COACH_PREAMBLE_TEMPLATE.format(athlete_info=athlete_info)
