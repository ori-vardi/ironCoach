"""CSV data loaders for body metrics, recovery, and daily aggregates."""

import csv
from pathlib import Path

from config import TRAINING_DATA
from .helpers import _safe_float


def _load_recovery_data(data_dir: Path = None):
    """Load recovery_data.csv from training_data/."""
    csv_path = (data_dir or TRAINING_DATA) / "recovery_data.csv"
    if not csv_path.exists():
        return {}
    data = {}
    with open(csv_path, newline="") as f:
        reader = csv.DictReader(f)
        for row in reader:
            data[row["date"]] = row
    return data



def _load_daily_aggregates(data_dir: Path = None):
    """Load daily_aggregates.csv from training_data/."""
    csv_path = (data_dir or TRAINING_DATA) / "daily_aggregates.csv"
    if not csv_path.exists():
        return {}
    data = {}
    with open(csv_path, newline="") as f:
        reader = csv.DictReader(f)
        for row in reader:
            data[row["date"]] = row
    return data



def _load_body_metrics(data_dir: Path = None):
    """Load body_metrics.csv from training_data/."""
    csv_path = (data_dir or TRAINING_DATA) / "body_metrics.csv"
    if not csv_path.exists():
        return []
    rows = []
    with open(csv_path, newline="") as f:
        reader = csv.DictReader(f)
        for row in reader:
            rows.append(dict(row))
    return rows



def _workout_csv_filename(w: dict) -> str:
    """Derive the CSV filename for a workout from summary data."""
    wnum = int(w.get("workout_num", 0))
    wdate = w.get("startDate", "")[:10]
    wtype = w.get("type", "")
    return f"workout_{wnum:03d}_{wdate}_{wtype}.csv"



def _build_workout_data_summary(w: dict, csv_path: Path) -> str:
    """Build a text summary of workout data from the summary row + CSV file."""
    wnum = int(w.get("workout_num", 0))
    wdate = w.get("startDate", "")[:10]
    wtype = w.get("type", "")
    dur = _safe_float(w.get("duration_min"))
    dist = w.get("distance_km", 0) or 0
    hr_avg = _safe_float(w.get("HeartRate_average"))
    hr_max = _safe_float(w.get("HeartRate_max"))
    cals = _safe_float(w.get("ActiveEnergyBurned_sum"))
    elev = _safe_float(w.get("meta_ElevationAscended"))

    merged_nums = w.get("merged_nums")
    merge_note = ""
    if merged_nums and len(merged_nums) > 1:
        merge_note = f" [MERGED from #{', #'.join(str(n) for n in merged_nums)} — treat as ONE continuous session]"

    lines = [
        f"Workout #{wnum} — {wtype} — {wdate}{merge_note}",
        f"Duration: {dur:.1f} min | Distance: {dist:.2f} km | HR avg: {hr_avg:.0f} / max: {hr_max:.0f}",
        f"Calories: {cals:.0f} | Elevation: {elev/100:.0f}m",
    ]

    # Collect CSV paths: primary + any merged workout CSVs
    csv_paths = [csv_path]
    if merged_nums and len(merged_nums) > 1:
        dd = csv_path.parent
        for mnum in merged_nums[1:]:  # skip first (already in csv_path)
            # Find matching CSV for merged workout number
            pattern = f"workout_{int(mnum):03d}_*"
            matches = list(dd.glob(pattern + ".csv"))
            if matches:
                csv_paths.append(matches[0])

    # Add key columns from CSV (first/last few rows + stats)
    all_rows = []
    for cp in csv_paths:
        if cp.exists():
            try:
                with open(cp, "r") as f:
                    reader = csv.DictReader(f)
                    all_rows.extend(list(reader))
            except Exception as e:
                lines.append(f"(CSV read error for {cp.name}: {e})")

    if all_rows:
        cols = [c for c in all_rows[0].keys() if any(all_rows[i].get(c) for i in range(min(3, len(all_rows))))]
        lines.append(f"\nCSV: {len(all_rows)} data points, columns: {', '.join(cols[:15])}")
        # Sample first 3 and last 3 rows for key metrics
        sample_rows = all_rows[:3] + (all_rows[-3:] if len(all_rows) > 6 else [])
        key_cols = [c for c in cols if any(k in c.lower() for k in
                   ['heart', 'speed', 'pace', 'power', 'cadence', 'distance', 'altitude', 'lat', 'lon'])][:8]
        if key_cols:
            lines.append(f"Key columns: {', '.join(key_cols)}")
            for i, r in enumerate(sample_rows):
                vals = " | ".join(f"{c}={r.get(c,'')}" for c in key_cols if r.get(c))
                if vals:
                    lines.append(f"  row {i}: {vals}")

    return "\n".join(lines)

