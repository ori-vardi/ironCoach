"""Data processing functions extracted from server.py for better modularity."""

from .helpers import (
    _safe_float,
    _classify_type,
    _enrich_workouts,
    _workout_distance,
    _filter_hidden,
    _get_hidden_workouts,
    _invalidate_hidden_cache,
    _load_manual_merges,
    _load_hidden_workouts,
    _load_auto_merge_settings,
    _load_settings_dict,
    _extract_vo2max,
    MIN_VO2MAX,
    MAX_VO2MAX,
)
from .summary import (
    _load_summary,
    _apply_gps_corrections_to_summary,
    _merge_nearby_workouts,
    _detect_brick_sessions,
)
from .gps import _detect_and_fix_gps
from .workout_analysis import (
    _find_workout_file,
    _load_workout_timeseries,
    _compute_sections,
    _hr_zone,
    _detect_utc_offset,
    _parse_ts,
    _detect_intervals,
    _sample_profiles,
    _save_precomputed_sections,
    _load_precomputed_sections,
    _save_gps_segments,
    _load_gps_segments,
    _generate_all_sections,
)
from .recovery import (
    _compute_trimp,
    _compute_hrtss,
    _compute_recovery_timeline,
    _recovery_label,
    _load_vo2max_history,
    _training_phase,
    _compute_risk_alerts,
    _compute_readiness_score,
    _compute_weekly_load_change,
)
from .csv_loaders import (
    _load_recovery_data,
    _load_daily_aggregates,
    _load_body_metrics,
    _workout_csv_filename,
    _build_workout_data_summary,
)
from .nutrition_helpers import (
    _build_recovery_sleep_context,
    _load_nutrition_window,
    _load_nutrition_settings,
    _meal_relevant_to_workout,
)
from .hr_zones import (
    compute_default_hr_max,
    compute_default_hr_rest,
    compute_default_hr_lthr,
    compute_zones_from_hr,
    zones_from_boundaries,
    zone_boundaries,
    detect_hr_max_from_workouts,
    detect_hr_rest_from_recovery,
    resolve_hr_settings,
)

__all__ = [
    # helpers
    "_safe_float",
    "_classify_type",
    "_enrich_workouts",
    "_workout_distance",
    "_filter_hidden",
    "_get_hidden_workouts",
    "_invalidate_hidden_cache",
    "_load_manual_merges",
    "_load_hidden_workouts",
    "_load_auto_merge_settings",
    "_load_settings_dict",
    "_extract_vo2max",
    "MIN_VO2MAX",
    "MAX_VO2MAX",
    # summary
    "_load_summary",
    "_apply_gps_corrections_to_summary",
    "_merge_nearby_workouts",
    "_detect_brick_sessions",
    # gps
    "_detect_and_fix_gps",
    # workout_analysis
    "_find_workout_file",
    "_load_workout_timeseries",
    "_compute_sections",
    "_hr_zone",
    "_detect_utc_offset",
    "_parse_ts",
    "_detect_intervals",
    "_sample_profiles",
    "_save_precomputed_sections",
    "_load_precomputed_sections",
    "_save_gps_segments",
    "_load_gps_segments",
    "_generate_all_sections",
    # recovery
    "_compute_trimp",
    "_compute_hrtss",
    "_compute_recovery_timeline",
    "_recovery_label",
    "_load_vo2max_history",
    "_training_phase",
    "_compute_risk_alerts",
    "_compute_readiness_score",
    "_compute_weekly_load_change",
    # csv_loaders
    "_load_recovery_data",
    "_load_daily_aggregates",
    "_load_body_metrics",
    "_workout_csv_filename",
    "_build_workout_data_summary",
    # nutrition_helpers
    "_build_recovery_sleep_context",
    "_load_nutrition_window",
    "_load_nutrition_settings",
    "_meal_relevant_to_workout",
    # hr_zones
    "compute_default_hr_max",
    "compute_default_hr_rest",
    "compute_default_hr_lthr",
    "compute_zones_from_hr",
    "zones_from_boundaries",
    "zone_boundaries",
    "detect_hr_max_from_workouts",
    "detect_hr_rest_from_recovery",
    "resolve_hr_settings",
]

