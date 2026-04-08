# Coaching Overview Skill

## Periodization Phases

| Phase | Duration | Focus |
|---|---|---|
| **Base** | 12-16 weeks | Aerobic foundation, technique, volume building |
| **Build** | 8-12 weeks | Race-specific intensity, brick sessions, longer intervals |
| **Peak** | 2-4 weeks | Race simulations, highest load week, sharpening |
| **Taper** | 2-3 weeks | Volume drops 40-60%, intensity maintained, freshness |

- Typical half-Ironman plan: 20-24 weeks structured training
- Recovery weeks every 3-4 weeks (reduce volume 30-40%, maintain intensity)

## Training Load Concepts

### ATL (Acute Training Load)
- Rolling 7-day average of training stress
- Represents recent fatigue / current training load
- Calculated from: duration x intensity (HR zones, power, pace)

### CTL (Chronic Training Load)
- Rolling 42-day average of training stress
- Represents fitness / long-term training adaptation
- Should increase gradually: max 5-7 TSS/week increase

### TSB (Training Stress Balance)
- TSB = CTL - ATL
- **Positive TSB (>0)**: fresh, possibly under-training
- **TSB -10 to -30**: productive training zone
- **TSB < -30**: very fatigued, injury risk
- **Race day target**: TSB +10 to +25 (tapered and fresh)

## Race-Specific Targets

Race details (distances, cutoffs, targets, course notes) are injected in the system preamble from the events DB — no need to fetch them.

### General Half-Ironman Time Estimates
- Beginner finisher: 6:30-7:30
- Intermediate: 5:30-6:30
- Strong: <5:30
- Include T1 + T2 transitions: 3-8 min each

## Reading Training Data

### Summary CSV (`00_workouts_summary.csv`)
- 73 columns, one row per workout
- Key columns: workoutActivityType, startDate, duration, totalDistance, totalEnergyBurned
- HR columns: HeartRate_average, HeartRate_maximum
- Pace/speed: various per workout type
- Distance units: running=km, swimming=meters, cycling=km
- Elevation: in centimeters (divide by 100)

### Per-Workout CSVs (`workout_NNN_DATE_TYPE.csv`)
- Time-series data: timestamp, GPS, HR, pace, power, cadence
- Use for split analysis, drift calculations, zone distributions

### Workout Insights (DB: `workout_insights` table)
- AI-generated per-workout analyses, queried from DB
- Last 2 weeks injected into main-coach prompt automatically
- Older insights available via DB query on demand

## Athlete Profile

Athlete details are injected at runtime via `_build_coach_preamble()` from the database (profile, events, coach memory).
No hardcoded athlete files needed — all personal data comes from the DB.

## When to Escalate to Medical Advice

Flag and recommend "see a sports medicine doctor" for:
- Persistent pain lasting >1 week that worsens with activity
- Chest pain, dizziness, or fainting during exercise
- Resting HR consistently >10 bpm above normal baseline
- Signs of overtraining syndrome (persistent fatigue, declining performance over weeks, mood changes)
- Any suspected stress fracture (localized bone pain, pain at rest)
- Sudden significant HR anomalies (arrhythmia patterns in data)
- Signs of RED-S (relative energy deficiency in sport)
