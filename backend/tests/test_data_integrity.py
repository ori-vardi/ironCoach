"""Tests for data processing correctness.

These tests protect against wrong coaching decisions caused by bad calculations.
Each test exists because of a real bug that was found.
"""
import sys
from pathlib import Path

# Add parent dir so we can import server modules
sys.path.insert(0, str(Path(__file__).parent.parent))


class TestSafeFloat:
    """_safe_float is called on every CSV value. If it breaks, everything breaks."""

    def test_unit_suffix_cm(self):
        """DP-001: Apple Health stores elevation as '7695 cm' — must strip suffix."""
        from server import _safe_float
        assert _safe_float("7695 cm") == 7695.0

    def test_unit_suffix_degF(self):
        from server import _safe_float
        assert _safe_float("57.4765 degF") == 57.4765

    def test_plain_number_string(self):
        from server import _safe_float
        assert _safe_float("42.5") == 42.5

    def test_integer_passthrough(self):
        """E-017: Fast path — int/float should not go through str.split()."""
        from server import _safe_float
        assert _safe_float(42) == 42.0
        assert _safe_float(3.14) == 3.14

    def test_zero_not_treated_as_falsy(self):
        """Zero is a valid value, not missing data."""
        from server import _safe_float
        assert _safe_float(0) == 0.0
        assert _safe_float(0.0) == 0.0

    def test_empty_and_none_return_default(self):
        from server import _safe_float
        assert _safe_float("") == 0.0
        assert _safe_float(None) == 0.0
        assert _safe_float(None, default=-1.0) == -1.0

    def test_garbage_returns_default(self):
        from server import _safe_float
        assert _safe_float("not a number") == 0.0
        assert _safe_float("abc xyz") == 0.0


class TestWorkoutMerge:
    """Merge combines consecutive same-discipline workouts.
    Wrong merge = wrong total distance/duration = wrong coaching advice.
    """

    def _make_workout(self, num, disc_type, duration, distance, hr_avg, hr_max, hr_min, start, end):
        return {
            "workout_num": num,
            "type": disc_type,
            "duration_min": str(duration),
            "DistanceWalkingRunning_sum": str(distance),
            "HeartRate_average": str(hr_avg),
            "HeartRate_maximum": str(hr_max),
            "HeartRate_minimum": str(hr_min),
            "startDate": start,
            "endDate": end,
        }

    def test_sum_distance_and_duration(self):
        """Merged workout must have total distance and duration."""
        from server import _merge_nearby_workouts, _safe_float
        w1 = self._make_workout(1, "Running", 30, 5.0, 150, 170, 120,
                                "2026-03-10 07:00:00 +0200", "2026-03-10 07:30:00 +0200")
        w2 = self._make_workout(2, "Running", 20, 3.5, 155, 175, 125,
                                "2026-03-10 07:32:00 +0200", "2026-03-10 07:52:00 +0200")
        merged = _merge_nearby_workouts([w1, w2])
        assert len(merged) == 1
        m = merged[0]
        assert _safe_float(m["duration_min"]) == 50.0
        assert _safe_float(m["DistanceWalkingRunning_sum"]) == 8.5

    def test_max_hr_takes_highest(self):
        """DP-002: _maximum must use max(), not first workout's value."""
        from server import _merge_nearby_workouts, _safe_float
        w1 = self._make_workout(1, "Running", 30, 5.0, 150, 170, 120,
                                "2026-03-10 07:00:00 +0200", "2026-03-10 07:30:00 +0200")
        w2 = self._make_workout(2, "Running", 20, 3.5, 155, 180, 110,
                                "2026-03-10 07:32:00 +0200", "2026-03-10 07:52:00 +0200")
        merged = _merge_nearby_workouts([w1, w2])
        m = merged[0]
        assert _safe_float(m["HeartRate_maximum"]) == 180.0

    def test_min_hr_takes_lowest(self):
        """DP-002: _minimum must use min(), not first workout's value."""
        from server import _merge_nearby_workouts, _safe_float
        w1 = self._make_workout(1, "Running", 30, 5.0, 150, 170, 120,
                                "2026-03-10 07:00:00 +0200", "2026-03-10 07:30:00 +0200")
        w2 = self._make_workout(2, "Running", 20, 3.5, 155, 180, 110,
                                "2026-03-10 07:32:00 +0200", "2026-03-10 07:52:00 +0200")
        merged = _merge_nearby_workouts([w1, w2])
        m = merged[0]
        assert _safe_float(m["HeartRate_minimum"]) == 110.0

    def test_avg_hr_weighted_by_duration(self):
        """DP-009: Average HR must be weighted, not simple average."""
        from server import _merge_nearby_workouts, _safe_float
        w1 = self._make_workout(1, "Running", 30, 5.0, 150, 170, 120,
                                "2026-03-10 07:00:00 +0200", "2026-03-10 07:30:00 +0200")
        w2 = self._make_workout(2, "Running", 20, 3.5, 160, 175, 125,
                                "2026-03-10 07:32:00 +0200", "2026-03-10 07:52:00 +0200")
        merged = _merge_nearby_workouts([w1, w2])
        m = merged[0]
        expected = (150 * 30 + 160 * 20) / 50  # 154.0
        assert abs(_safe_float(m["HeartRate_average"]) - expected) < 0.1

    def test_different_disciplines_not_merged(self):
        """A run and a bike should never merge."""
        from server import _merge_nearby_workouts
        w1 = self._make_workout(1, "Running", 30, 5.0, 150, 170, 120,
                                "2026-03-10 07:00:00 +0200", "2026-03-10 07:30:00 +0200")
        w2 = self._make_workout(2, "Cycling", 60, 25.0, 140, 165, 100,
                                "2026-03-10 07:32:00 +0200", "2026-03-10 08:32:00 +0200")
        merged = _merge_nearby_workouts([w1, w2])
        assert len(merged) == 2

    def test_gap_over_10min_not_merged(self):
        """Workouts more than 10 min apart are separate sessions."""
        from server import _merge_nearby_workouts
        w1 = self._make_workout(1, "Running", 30, 5.0, 150, 170, 120,
                                "2026-03-10 07:00:00 +0200", "2026-03-10 07:30:00 +0200")
        w2 = self._make_workout(2, "Running", 20, 3.0, 155, 175, 125,
                                "2026-03-10 07:45:00 +0200", "2026-03-10 08:05:00 +0200")
        merged = _merge_nearby_workouts([w1, w2])
        assert len(merged) == 2
