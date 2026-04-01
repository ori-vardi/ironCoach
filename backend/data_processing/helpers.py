"""Helper functions for data processing."""

import json
import logging
import re
import sqlite3
from pathlib import Path

from config import BASE_DIR

logger = logging.getLogger(__name__)

MIN_VO2MAX = 10
MAX_VO2MAX = 100

_CONTROL_CHAR_RE = re.compile(r'[\x00-\x08\x0b\x0c\x0e-\x1f\x7f]')


def _strip_control(s):
    return _CONTROL_CHAR_RE.sub('', s) if isinstance(s, str) else s


def _parse_json_array_response(raw: str) -> list | None:
    """Parse a JSON array from an LLM response, stripping markdown fences.

    Returns parsed list on success, or None if no valid JSON found.
    """
    cleaned = raw.strip()
    if cleaned.startswith("```"):
        cleaned = cleaned.split("\n", 1)[-1].rsplit("```", 1)[0].strip()
    try:
        parsed = json.loads(cleaned)
    except json.JSONDecodeError:
        match = re.search(r'\[.*\]', cleaned, re.DOTALL)
        if match:
            try:
                parsed = json.loads(match.group())
            except json.JSONDecodeError:
                return None
        else:
            match = re.search(r'\{[^{}]+\}', cleaned)
            if match:
                try:
                    parsed = [json.loads(match.group())]
                except json.JSONDecodeError:
                    return None
            else:
                return None
    if isinstance(parsed, dict):
        parsed = [parsed]
    return parsed


def _extract_vo2max(rows: list) -> float | None:
    """Extract first valid VO2max value from time-series rows."""
    for row in rows:
        v = row.get("VO2Max") if isinstance(row, dict) else None
        if v is not None and v != "":
            try:
                vo2_val = round(float(v), 1)
                if MIN_VO2MAX <= vo2_val <= MAX_VO2MAX:
                    return vo2_val
            except (ValueError, TypeError):
                pass
    return None


def _safe_float(val, default=0.0):
    try:
        if not val and val != 0:
            return default
        if isinstance(val, (int, float)):
            return float(val)
        s = str(val).split()[0]  # strip unit suffix like "cm", "degF"
        return float(s)
    except (ValueError, TypeError) as e:
        logger.debug(f"_safe_float: could not parse '{val}' (type: {type(val).__name__}), returning {default}")
        return default


def _classify_type(workout_type: str) -> str:
    """Map Apple Health workout types to disciplines."""
    t = workout_type.lower()
    if "running" in t or "walking" in t:
        return "run"
    if "cycling" in t:
        return "bike"
    if "swimming" in t:
        return "swim"
    if "strength" in t or "functional" in t:
        return "strength"
    return "other"


def _enrich_workouts(workouts: list) -> list:
    """Add discipline and distance_km to each workout dict (in-place). Returns the list."""
    for w in workouts:
        w["discipline"] = _classify_type(w.get("type", ""))
        w["distance_km"] = _workout_distance(w)
    return workouts


def _workout_distance(w: dict) -> float:
    """Extract the primary distance for a workout, always returned in km.

    IMPORTANT: CSV stores swimming distance in METERS (DistanceSwimming_sum with unit="m"),
    while running/cycling are stored in km. This function converts all to km for consistency.
    """
    conversions = [
        ("DistanceWalkingRunning_sum", "DistanceWalkingRunning_unit"),
        ("DistanceCycling_sum", "DistanceCycling_unit"),
        ("DistanceSwimming_sum", "DistanceSwimming_unit"),  # CSV stores in meters!
    ]
    for val_col, unit_col in conversions:
        val = _safe_float(w.get(val_col))
        if val > 0:
            unit = (w.get(unit_col) or "km").strip().lower()
            if unit == "m":
                return val / 1000.0  # Convert meters to km
            return val
    return 0.0


# Per-user cache for hidden workouts (invalidated on change)
_hidden_workouts_cache = {}


def _get_hidden_workouts(user_id: int = 1) -> set:
    global _hidden_workouts_cache
    if user_id not in _hidden_workouts_cache:
        _hidden_workouts_cache[user_id] = _load_hidden_workouts(user_id)
    return _hidden_workouts_cache[user_id]


def _invalidate_hidden_cache(user_id: int = None):
    global _hidden_workouts_cache
    if user_id is not None:
        _hidden_workouts_cache.pop(user_id, None)
    else:
        _hidden_workouts_cache = {}


def _filter_hidden(workouts: list, user_id: int = 1) -> list:
    """Remove hidden workouts from a list (per-user)."""
    hidden = _get_hidden_workouts(user_id)
    if not hidden:
        return workouts
    return [w for w in workouts if int(w.get("workout_num", 0)) not in hidden]


def _load_settings_dict(keys: list[str]) -> dict[str, str]:
    """Load multiple raw string values from app_settings (sync). Returns {key: value}."""
    try:
        db_path = BASE_DIR / "data" / "dashboard.db"
        if not db_path.exists():
            return {}
        placeholders = ",".join("?" for _ in keys)
        conn = sqlite3.connect(str(db_path))
        cursor = conn.execute(
            f"SELECT key, value FROM app_settings WHERE key IN ({placeholders})", keys
        )
        result = dict(cursor.fetchall())
        conn.close()
        return result
    except Exception:
        return {}


def _load_setting_json(key: str, default=None):
    """Load a JSON-encoded value from app_settings (sync). Returns parsed value or default."""
    settings = _load_settings_dict([key])
    if key in settings:
        try:
            return json.loads(settings[key])
        except (json.JSONDecodeError, ValueError):
            pass
    return default


_manual_merges_cache: dict[int, tuple[float, set]] = {}
_auto_merge_cache: tuple[float, tuple] | None = None
_SETTINGS_CACHE_TTL = 5.0


def _load_manual_merges(user_id: int = 1) -> set:
    """Load user-approved manual merge pairs from settings (sync, per-user, cached 5s)."""
    import time
    now = time.monotonic()
    cached = _manual_merges_cache.get(user_id)
    if cached and (now - cached[0]) < _SETTINGS_CACHE_TTL:
        return cached[1]
    pairs = _load_setting_json(f"manual_merges_{user_id}", [])
    result = {(min(int(a), int(b)), max(int(a), int(b))) for a, b in pairs}
    _manual_merges_cache[user_id] = (now, result)
    return result


def _load_hidden_workouts(user_id: int = 1) -> set:
    """Load hidden workout numbers from settings (sync, per-user)."""
    return set(_load_setting_json(f"hidden_workouts_{user_id}", []))


def _load_auto_merge_settings() -> tuple:
    """Load auto-merge settings from DB (sync, cached 5s). Returns (enabled, gap_minutes)."""
    global _auto_merge_cache
    import time
    now = time.monotonic()
    if _auto_merge_cache and (now - _auto_merge_cache[0]) < _SETTINGS_CACHE_TTL:
        return _auto_merge_cache[1]
    try:
        settings = _load_settings_dict(["auto_merge_enabled", "auto_merge_gap"])
        enabled = settings.get("auto_merge_enabled", "1") != "0"
        gap = int(settings.get("auto_merge_gap", "10"))
        result = (enabled, gap)
    except Exception:
        result = (True, 10)
    _auto_merge_cache = (now, result)
    return result
