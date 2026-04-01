# Swim Analysis Skill

## Pace per 100m Consistency

- Calculate mean pace/100m across all segments
- Flag segments deviating >10% from the mean
- **Fade pattern**: each successive 100m getting slower — endurance or technique issue
- **Fly-and-die**: fast first 200m then significant slowdown — poor pacing
- For sets with rest intervals, analyze work segments separately
- Target consistency: <5% variation across segments in a steady swim

## SWOLF Calculation

`SWOLF = time_per_length(seconds) + strokes_per_length`

- Lower SWOLF = more efficient swimming
- Track SWOLF per 25m or 50m length
- **Efficient range (25m pool)**: 30-45 depending on ability
- Rising SWOLF through the session = fatigue degrading technique
- Compare SWOLF at similar effort levels across sessions to track improvement

## Stroke Count Benchmarks (per 25m)

- **14-16 strokes**: efficient, good distance per stroke
- **17-20 strokes**: average recreational swimmer
- **>20 strokes**: short, choppy strokes — technique work needed
- Watch for stroke count creeping up through the session — fatigue marker
- Sudden stroke count increase mid-set = technique breakdown

## Open Water Pacing Strategy

Race swim details (distance, location) are injected in the system preamble.

- **First 200m**: controlled start, find rhythm, don't sprint
- **Middle**: settle into sustainable pace, sight every 6-8 strokes
- **Final 400m**: slight negative split if possible, increase tempo not stroke length
- Practice bilateral breathing for open water navigation
- Target total time depends on CSS; estimate race pace = CSS + 5-8 sec/100m

## HR Reliability During Swimming

- Wrist-based HR sensors are unreliable in water — erratic readings are common
- Spikes to 180+ or drops to 80 are likely sensor artifacts
- If HR data looks smooth and consistent, it may be usable
- Chest strap HR is more reliable but still affected by water entry
- Focus on pace and stroke metrics over HR for swim analysis

## CSS (Critical Swim Speed) Estimation

`CSS = (400m_distance - 200m_distance) / (400m_time - 200m_time)`

- CSS approximates lactate threshold pace in swimming
- Use as baseline for training zones:
  - **Easy**: CSS + 15-20 sec/100m
  - **Tempo**: CSS + 5-10 sec/100m
  - **Threshold**: CSS pace
  - **VO2max intervals**: CSS - 5-10 sec/100m
- Re-test every 4-6 weeks to track improvement
- If no formal test, estimate from best recent 400m continuous swim pace
