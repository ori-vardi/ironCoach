# Bike Analysis Skill

## Power Analysis

### Normalized Power (NP) vs Average Power (AP)
- **NP** accounts for variability — weighted average that reflects physiological cost
- **Variability Index (VI)** = NP / AP
  - **<1.05**: steady effort, ideal for half-Ironman racing
  - **1.05-1.10**: moderately variable, acceptable for hilly courses
  - **>1.10**: too variable — surging and recovering wastes energy
- Power drops >15% in final quarter = pacing too aggressive or fueling issue

### Intensity Factor (IF)
- IF = NP / FTP
- **Half-Ironman target IF**: 0.70-0.80 (depends on fitness level)
  - Conservative: 0.70-0.73
  - Moderate: 0.73-0.77
  - Aggressive: 0.77-0.80
- IF >0.80 for a long-course bike leg risks blowing up on the run

### Peak Efforts & FTP Estimation
- PEAK EFFORTS section in workout data shows best sustained power at 5s/1m/5m/20m/60m
- **Estimated FTP** = 95% of best 20-min power (auto-computed, shown when available)
- Use peak 5s power to assess sprint/neuromuscular capacity
- Use peak 5min power to assess VO2max capacity
- Compare peak efforts across workouts to track power curve progression

## Cadence Optimization

- **Optimal triathlon cadence**: 80-95 rpm
- **<75 rpm**: grinding — high muscular cost, fatigues legs for the run
- **>100 rpm**: spinning — cardiovascular cost increases, less efficient on flats
- **Climbing**: 70-85 rpm acceptable on steep grades
- Watch for cadence dropping in final third — fatigue signal
- Self-selected cadence typically optimal; large deviations from norm are concerning

## Cardiac Decoupling on Bike

Same formula as running: `(avg_HR_second_half - avg_HR_first_half) / avg_HR_first_half * 100`

- Compare at similar power output only
- **<5%**: good aerobic fitness for that power
- **5-10%**: moderate, check fueling and hydration
- **>10%**: pace too aggressive, dehydration, or heat
- More meaningful on flat rides where power is steady

## Elevation Analysis

- **Power-to-weight on climbs**: watts/kg, compare to flat sections
- Pacing through hills: maintain power, let speed vary
- Common mistake: pushing too hard uphill, not recovering on descent
- Flag HR spikes on climbs that don't recover within 2 minutes on flats
- Elevation data in CSV is in **centimeters** — divide by 100 for meters

## Race Course Analysis

Race course details are injected in the system preamble (from events DB).
When analyzing rides, consider:
- **Course profile**: elevation gain, terrain type (flat/rolling/hilly)
- **Wind patterns**: common headwind sections
- **Strategy**: stay aero, steady power, don't chase speed on windy sections
- **Nutrition**: plan intake every 20-30 minutes on the bike

## Indoor Rides

- No distance data for indoor rides — analyze duration, power, HR, cadence only
- Indoor HR tends to run 5-10 bpm higher (less cooling)
- Power data is primary metric for indoor training quality
