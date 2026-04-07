"""Per-user HR zone calculation and auto-detection from Apple Health data."""

from datetime import datetime

from config import _HR_MAX, _HR_REST, _HR_LTHR, _HR_ZONES
from .helpers import _safe_float


def compute_default_hr_max(age: int, sex: str = "male") -> float:
    """Tanaka formula: 208 - 0.7 * age (sex-independent)."""
    if age <= 0:
        return _HR_MAX
    return round(208 - 0.7 * age)


def compute_default_hr_rest(sex: str = "male") -> float:
    """Conservative default resting HR by sex."""
    return 55.0 if sex == "male" else 60.0


def compute_default_hr_lthr(hr_max: float) -> float:
    """~89% of HR max for endurance-trained athletes."""
    if hr_max <= 0:
        return _HR_LTHR
    return round(hr_max * 0.89)


def compute_zones_from_hr(hr_max: float, hr_rest: float) -> list[tuple]:
    """Karvonen HRR-based 5-zone model. Returns [(name, lo, hi), ...]."""
    if hr_max <= 0 or hr_rest <= 0 or hr_max <= hr_rest:
        return list(_HR_ZONES)
    hrr = hr_max - hr_rest
    # Zone percentages of HRR
    boundaries = [
        ("Z1", 0, round(hr_rest + 0.60 * hrr)),
        ("Z2", round(hr_rest + 0.60 * hrr), round(hr_rest + 0.70 * hrr)),
        ("Z3", round(hr_rest + 0.70 * hrr), round(hr_rest + 0.80 * hrr)),
        ("Z4", round(hr_rest + 0.80 * hrr), round(hr_rest + 0.90 * hrr)),
        ("Z5", round(hr_rest + 0.90 * hrr), 999),
    ]
    return boundaries


def zones_from_boundaries(z1_upper: float, z2_upper: float,
                          z3_upper: float, z4_upper: float) -> list[tuple]:
    """Reconstruct zone tuples from stored upper boundaries."""
    return [
        ("Z1", 0, int(z1_upper)),
        ("Z2", int(z1_upper), int(z2_upper)),
        ("Z3", int(z2_upper), int(z3_upper)),
        ("Z4", int(z3_upper), int(z4_upper)),
        ("Z5", int(z4_upper), 999),
    ]


def zone_boundaries(zones: list[tuple]) -> dict:
    """Extract upper boundaries from zone tuples for DB storage."""
    by_name = {z[0]: z for z in zones}
    return {
        "zone1_upper": by_name.get("Z1", (0, 0, 0))[2],
        "zone2_upper": by_name.get("Z2", (0, 0, 0))[2],
        "zone3_upper": by_name.get("Z3", (0, 0, 0))[2],
        "zone4_upper": by_name.get("Z4", (0, 0, 0))[2],
    }


def detect_hr_max_from_workouts(workouts: list) -> float | None:
    """Scan all workouts and return the observed max HR."""
    best = 0.0
    for w in workouts:
        val = _safe_float(w.get("HeartRate_maximum"))
        if val > best:
            best = val
    return best if best > 100 else None  # ignore noise below 100


def detect_hr_rest_from_recovery(recovery_data: dict) -> float | None:
    """Return median of last 14 days of resting HR from recovery data."""
    if not recovery_data:
        return None
    # Get recent entries sorted by date
    sorted_dates = sorted(recovery_data.keys(), reverse=True)[:14]
    values = []
    for d in sorted_dates:
        rhr = _safe_float(recovery_data[d].get("resting_hr"))
        if rhr > 30:  # ignore noise
            values.append(rhr)
    if not values:
        return None
    values.sort()
    mid = len(values) // 2
    return round(values[mid])


def _age_from_profile(profile: dict | None) -> int:
    """Calculate age from profile birth_date."""
    if not profile:
        return 0
    birth = profile.get("birth_date", "")
    if not birth:
        return 0
    try:
        born = datetime.strptime(birth, "%Y-%m-%d")
        return int((datetime.now() - born).days / 365.25)
    except ValueError:
        return 0


def resolve_hr_settings(db_settings: dict | None, profile: dict | None) -> dict:
    """Master resolver: DB settings > calculated from profile > config fallbacks.

    Returns complete dict with hr_max, hr_rest, hr_lthr, hr_zones, locked, source.
    """
    if db_settings and db_settings.get("hr_max", 0) > 0:
        zones = zones_from_boundaries(
            db_settings["zone1_upper"], db_settings["zone2_upper"],
            db_settings["zone3_upper"], db_settings["zone4_upper"],
        )
        return {
            "hr_max": db_settings["hr_max"],
            "hr_rest": db_settings["hr_rest"],
            "hr_lthr": db_settings["hr_lthr"],
            "hr_zones": zones,
            "locked": bool(db_settings.get("locked")),
            "source": db_settings.get("source", "manual"),
        }

    # Calculate from profile
    age = _age_from_profile(profile)
    sex = (profile or {}).get("sex", "male")
    hr_max = compute_default_hr_max(age, sex)
    hr_rest = compute_default_hr_rest(sex)
    hr_lthr = compute_default_hr_lthr(hr_max)
    zones = compute_zones_from_hr(hr_max, hr_rest)
    return {
        "hr_max": hr_max,
        "hr_rest": hr_rest,
        "hr_lthr": hr_lthr,
        "hr_zones": zones,
        "locked": False,
        "source": "calculated",
    }
