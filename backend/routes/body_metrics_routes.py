"""Body metrics and energy balance endpoints."""

import csv
from datetime import datetime, timedelta
from pathlib import Path

from fastapi import APIRouter, HTTPException, Request

import database as db
from data_processing import _load_body_metrics, _load_daily_aggregates, _load_summary, _safe_float
from routes.deps import _uid, _user_data_dir

router = APIRouter()


@router.get("/api/body-metrics")
async def body_metrics_get(request: Request):
    rows = _load_body_metrics(_user_data_dir(request))
    # Group by type and pivot into per-date records
    by_date = {}
    for r in rows:
        d = r["date"]
        if d not in by_date:
            by_date[d] = {"date": d, "source": r.get("sourceName", "")}
        t = r["type"]
        val = _safe_float(r["value"])
        # BodyFatPercentage stored as decimal (0.124 = 12.4%)
        if t == "BodyFatPercentage" and val < 1:
            val = round(val * 100, 1)
        by_date[d][t] = round(val, 2)

    result = sorted(by_date.values(), key=lambda x: x["date"])
    return result


@router.post("/api/body-metrics")
async def body_metrics_add(request: Request):
    """Add body metrics entry. Appends to body_metrics.csv.
    Expects JSON with date + any metrics. Supported fields:
      weight_kg, body_fat_pct, bmi, lean_mass_kg, muscle_mass_kg,
      muscle_rate_pct, bone_mass_kg, body_water_pct, protein_pct,
      visceral_fat, bmr_kcal, body_age, fat_mass_kg, source
    """
    data = await request.json()
    date_str = data.get("date", "")
    if not date_str:
        raise HTTPException(400, "date is required (YYYY-MM-DD)")

    source = data.get("source", "LeaOne (via IronCoach)")
    dt_str = f"{date_str} 00:00:00 +0200"

    dd = _user_data_dir(request)
    csv_path = dd / "body_metrics.csv"
    rows_to_add = []

    # Map of API field -> (CSV type, unit, transform)
    # Transform: None = store as-is, "pct_decimal" = divide by 100 if >1
    field_map = [
        ("weight_kg",       "BodyMass",          "kg",    None),
        ("body_fat_pct",    "BodyFatPercentage", "%",     "pct_decimal"),
        ("bmi",             "BodyMassIndex",     "count", None),
        ("lean_mass_kg",    "LeanBodyMass",      "kg",    None),
        ("muscle_mass_kg",  "MuscleMass",        "kg",    None),
        ("muscle_rate_pct", "MuscleRate",        "%",     None),
        ("bone_mass_kg",    "BoneMass",          "kg",    None),
        ("body_water_pct",  "BodyWater",         "%",     None),
        ("protein_pct",     "ProteinRate",       "%",     None),
        ("visceral_fat",    "VisceralFat",       "index", None),
        ("bmr_kcal",        "BMR",               "kcal",  None),
        ("body_age",        "BodyAge",           "years", None),
        ("fat_mass_kg",     "FatMass",           "kg",    None),
    ]

    for field, csv_type, unit, transform in field_map:
        val = data.get(field)
        if val is not None:
            v = float(val)
            if transform == "pct_decimal" and v > 1:
                v = v / 100
            rows_to_add.append({
                "date": date_str, "datetime": dt_str, "type": csv_type,
                "value": str(round(v, 4) if transform == "pct_decimal" else v),
                "unit": unit, "sourceName": source,
            })

    if not rows_to_add:
        raise HTTPException(400, "No metric fields provided")

    # Append to CSV
    cols = ["date", "datetime", "type", "value", "unit", "sourceName"]
    file_exists = csv_path.exists()
    with open(csv_path, "a", newline="") as f:
        writer = csv.DictWriter(f, fieldnames=cols)
        if not file_exists:
            writer.writeheader()
        for row in rows_to_add:
            writer.writerow(row)

    return {"ok": True, "entries_added": len(rows_to_add)}


@router.get("/api/energy-balance")
async def energy_balance(date: str, request: Request):
    """Return daily energy balance: BMR, workout calories, TEF."""
    uid = _uid(request)
    dd = _user_data_dir(request)

    # Get user profile for BMR calculation
    conn = await db.get_db()
    try:
        profile = await db.user_get_profile(conn, uid)
    finally:
        await conn.close()

    height_cm = _safe_float(profile.get("height_cm")) if profile else 0
    sex = (profile.get("sex") or "male") if profile else "male"
    birth_str = (profile.get("birth_date") or "") if profile else ""

    # Get weight: use most recent body mass on or before the target date
    metrics = _load_body_metrics(dd)
    target_date = date[:10]
    weight_kg = 0.0
    for row in reversed(metrics):
        if row.get("type") == "BodyMass":
            row_date = (row.get("startDate") or row.get("date") or "")[:10]
            if row_date <= target_date:
                w = _safe_float(row.get("value"))
                if w > 0:
                    weight_kg = w
                    break
    if weight_kg == 0:
        # Fall back to any weight if none before target date
        for row in reversed(metrics):
            if row.get("type") == "BodyMass":
                w = _safe_float(row.get("value"))
                if w > 0:
                    weight_kg = w
                    break
    if weight_kg == 0:
        weight_kg = _safe_float(profile.get("weight_kg")) if profile else 0

    # No weight or height = can't calculate BMR (but measured BMR may still exist)
    # Look for measured BMR from scale (e.g. eufy/LeaOne) on or before target date
    measured_bmr = 0
    for row in reversed(metrics):
        if row.get("type") == "BMR":
            row_date = (row.get("startDate") or row.get("date") or "")[:10]
            if row_date <= target_date:
                v = _safe_float(row.get("value"))
                if v > 0:
                    measured_bmr = round(v)
                    break

    if weight_kg == 0 or height_cm == 0:
        if measured_bmr == 0:
            return {
                "bmr": 0, "weight_kg": 0, "age": 0,
                "workout_calories": 0, "workout_count": 0,
                "neat_calories": 0, "steps": 0,
            }

    try:
        birth = datetime.strptime(birth_str, "%Y-%m-%d")
        target = datetime.strptime(target_date, "%Y-%m-%d")
        age = (target - birth).days / 365.25
    except ValueError:
        age = 30

    # Use measured BMR from scale; fall back to Mifflin-St Jeor formula
    if measured_bmr > 0:
        bmr = measured_bmr
    else:
        sex_offset = 5 if sex == "male" else -161
        bmr = round(10 * weight_kg + 6.25 * height_cm - 5 * age + sex_offset)

    # ── Workout calories for this date ──
    workouts = _load_summary(dd)
    workout_cal = 0
    workout_count = 0
    for w in workouts:
        w_date = w.get("startDate", "")[:10]
        if w_date == date[:10]:
            workout_cal += _safe_float(w.get("ActiveEnergyBurned_sum", 0))
            workout_count += 1
    workout_cal = round(workout_cal)

    # ── NEAT (Non-Exercise Activity Thermogenesis) from daily aggregates ──
    # Apple Health active_cal includes ALL activity (workouts + steps + NEAT).
    # Subtract workout calories to isolate NEAT (steps, fidgeting, daily movement).
    daily_agg = _load_daily_aggregates(dd)
    day_key = date[:10]
    neat_cal = 0
    steps = 0
    if day_key in daily_agg:
        row = daily_agg[day_key]
        total_active = _safe_float(row.get("active_cal", 0))
        steps = int(_safe_float(row.get("steps", 0)))
        neat_cal = max(0, round(total_active - workout_cal))

    return {
        "bmr": bmr,
        "bmr_source": "measured" if measured_bmr > 0 else "formula",
        "weight_kg": round(weight_kg, 1),
        "age": round(age, 1),
        "workout_calories": workout_cal,
        "workout_count": workout_count,
        "neat_calories": neat_cal,
        "steps": steps,
    }


@router.get("/api/energy-balance/range")
async def energy_balance_range(from_date: str, to_date: str, request: Request):
    """Return daily net calories for a date range (calories_in - calories_out)."""
    uid = _uid(request)
    dd = _user_data_dir(request)

    # Profile for BMR
    conn = await db.get_db()
    try:
        profile = await db.user_get_profile(conn, uid)
        nutrition = await db.nutrition_get_range(conn, from_date, to_date, user_id=uid)
    finally:
        await conn.close()

    height_cm = _safe_float(profile.get("height_cm")) if profile else 0
    sex = (profile.get("sex") or "male") if profile else "male"
    birth_str = (profile.get("birth_date") or "") if profile else ""

    # Build weight timeline: sorted list of (date, weight_kg)
    metrics = _load_body_metrics(dd)
    weight_timeline = []
    for row in metrics:
        if row.get("type") == "BodyMass":
            w = _safe_float(row.get("value"))
            row_date = (row.get("startDate") or row.get("date") or "")[:10]
            if w > 0 and row_date:
                weight_timeline.append((row_date, w))
    weight_timeline.sort()

    # Fallback weight
    fallback_weight = 0.0
    if weight_timeline:
        fallback_weight = weight_timeline[-1][1]
    elif profile:
        fallback_weight = _safe_float(profile.get("weight_kg"))

    if fallback_weight == 0 or height_cm == 0:
        return []

    def weight_for_date(d):
        """Get most recent weight on or before date d."""
        w = fallback_weight
        for wd, wv in weight_timeline:
            if wd <= d:
                w = wv
            else:
                break
        return w

    try:
        birth = datetime.strptime(birth_str, "%Y-%m-%d")
    except ValueError:
        birth = None

    sex_offset = 5 if sex == "male" else -161

    # Build measured BMR timeline from scale data (e.g. eufy/LeaOne)
    bmr_timeline = []
    for row in metrics:
        if row.get("type") == "BMR":
            v = _safe_float(row.get("value"))
            row_date = (row.get("startDate") or row.get("date") or "")[:10]
            if v > 0 and row_date:
                bmr_timeline.append((row_date, round(v)))
    bmr_timeline.sort()

    def bmr_for_date(d):
        # Use measured BMR from scale if available on or before date
        measured = 0
        for bd, bv in bmr_timeline:
            if bd <= d:
                measured = bv
            else:
                break
        if measured > 0:
            return measured
        # Fall back to Mifflin-St Jeor formula
        wk = weight_for_date(d)
        if birth:
            age = (datetime.strptime(d, "%Y-%m-%d") - birth).days / 365.25
        else:
            age = 30
        return round(10 * wk + 6.25 * height_cm - 5 * age + sex_offset)

    # Workout calories by date
    workouts = _load_summary(dd)
    workout_by_date = {}
    for w in workouts:
        wd = w.get("startDate", "")[:10]
        if from_date <= wd <= to_date:
            workout_by_date[wd] = workout_by_date.get(wd, 0) + _safe_float(w.get("ActiveEnergyBurned_sum", 0))

    # Daily aggregates
    daily_agg = _load_daily_aggregates(dd)

    # Nutrition by date
    cal_in_by_date = {}
    for m in nutrition:
        d = m["date"]
        cal_in_by_date[d] = cal_in_by_date.get(d, 0) + _safe_float(m.get("calories", 0))

    # Build result for each day
    result = []
    cur = datetime.strptime(from_date[:10], "%Y-%m-%d")
    end = datetime.strptime(to_date[:10], "%Y-%m-%d")
    while cur <= end:
        ds = cur.strftime("%Y-%m-%d")
        cal_in = round(cal_in_by_date.get(ds, 0))
        w_cal = round(workout_by_date.get(ds, 0))

        neat_cal = 0
        if ds in daily_agg:
            row = daily_agg[ds]
            total_active = _safe_float(row.get("active_cal", 0))
            neat_cal = max(0, round(total_active - w_cal))

        # TEF from actual intake macros
        prot = carbs = fat = 0
        for m in nutrition:
            if m["date"] == ds:
                prot += _safe_float(m.get("protein_g", 0))
                carbs += _safe_float(m.get("carbs_g", 0))
                fat += _safe_float(m.get("fat_g", 0))
        tef = round(prot * 4 * 0.25 + carbs * 4 * 0.08 + fat * 9 * 0.03)

        day_bmr = bmr_for_date(ds)
        total_out = day_bmr + w_cal + neat_cal + tef
        net = cal_in - total_out

        result.append({
            "date": ds,
            "calories_in": cal_in,
            "calories_out": total_out,
            "net": net,
            "bmr": day_bmr,
            "workout": w_cal,
            "neat": neat_cal,
            "tef": tef,
        })
        cur += timedelta(days=1)

    return result
