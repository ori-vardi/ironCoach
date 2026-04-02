# IronCoach — Feature Documentation

> **Version 0.1.0** | Last updated: 2026-03-31

Detailed documentation of all features, architecture, and special behaviors.

## Table of Contents

- [Importing Data](#importing-data)
- [Workout Analytics](#workout-analytics-free)
- [GPS Accuracy & Anomaly Detection](#gps-accuracy--anomaly-detection)
- [Workout Merging](#workout-merging)
- [Hidden Workouts](#hidden-workouts)
- [Brick Detection](#brick-detection)
- [AI Coaching Chat](#ai-coaching-chat)
- [AI Workout Insights](#ai-workout-insights)
- [Post-Import Insight Selection](#post-import-insight-selection)
- [Nutrition Tracking](#nutrition-tracking)
- [Training Plan](#training-plan)
- [Events / Races](#events--races)
- [Recovery Dashboard](#recovery-dashboard)
- [Body Metrics](#body-metrics)
- [Coach Memory](#coach-memory)
- [Multi-User & Auth](#multi-user--auth)
- [Agent Model Override](#agent-model-override-admin)
- [AI Session & Cost Architecture](#ai-session--cost-architecture)
- [Token Usage Tracking](#token-usage-tracking)
- [Claude Code Integration](#claude-code-integration)
- [Detailed Workout Data](#detailed-workout-data)
- [Workout Deletion](#workout-deletion)
- [Limitations & Missing Features](#limitations--missing-features)

---

## Importing Data

1. On your iPhone: **Settings > Health > Export All Health Data**
2. Transfer the exported `apple_health_export` folder to your machine
3. In the dashboard sidebar, click **Import**
4. Enter the path to the folder containing `export.xml`
5. The server processes workouts incrementally — only new data is added

**Multi-user support**: Each user imports their own Apple Health export. Data is stored per-user in `training_data/users/{uid}/`. Users have isolated data: workouts, insights, nutrition, chat sessions, token usage, and coach memory.

Re-import anytime after new workouts. Only new data is processed (tracked via `.export_state.json`). Body metrics and daily aggregates are always regenerated to catch updates.

**Server-side task tracking**: Import progress is tracked server-side (`_register_task`/`_unregister_task`) so the "Processing..." notification survives page refresh. Completion notification (with elapsed time) is saved to DB, not generated client-side. All notification updates use custom DOM events (`notification-poll-now`, `pending-import-changed`) for immediate UI feedback without waiting for poll intervals.

### Performance Optimizations

The export pipeline (`scripts/export_to_csv.py`) is optimized for large Apple Health exports (750MB+, 1.7M+ records):

- **Single-pass XML scan** — all record types (training, body metrics, daily aggregates, recovery) collected in one `iterparse` loop with bit-flag routing, instead of 4 separate scans
- **Streaming parsing** — `iterparse` + `elem.clear()` keeps memory constant regardless of XML size (no OOM)
- **Fast date parsing** — manual string slicing (~10x faster than `datetime.strptime`) with timezone cache
- **Bisect workout matching** — O(log N) binary search instead of O(N) linear scan per record
- **String-based early skip** — training-only records outside the workout time window are rejected via string comparison without date parsing
- **Parallel CSV writing** — `ThreadPoolExecutor` writes per-workout CSVs in parallel (4 workers)

---

## Workout Analytics (Free)

Every workout page shows data computed directly from Apple Health CSV data — **zero AI cost**:

| Discipline  | Metrics Available                                                              |
|:------------|:-------------------------------------------------------------------------------|
| **Running** | Per-km splits, pace trend, HR zones, cadence, ground contact time, vertical oscillation, power |
| **Cycling** | Per-km splits, avg speed, elevation profile, cadence, power, HR response       |
| **Swimming**| Apple Watch auto sets with per-100m and per-25m splits, stroke count, SWOLF, rest intervals |
| **All**     | Filterable table with detail modals, full time-series charts                   |

- **Apple Fitness exact match** — all values match Apple Fitness app: pace from distance/time, `Math.floor` for power and calories, distance truncated to 2 decimals, duration shows seconds precision
- **Cardiac drift** uses pre-computed `hr_summary.drift_pct` from raw time-series data (not per-km split averages) for accuracy
- Tables are sorted newest-first, date format DD/MM
- Discipline colors: swim = blue (#65bcff), bike = green (#c3e88d), run = orange (#ff966c)
- **Sidebar page order** is customizable — drag to reorder pages in the left navigation
- **No heuristic data** — only real sensor readings from Apple Watch are displayed or stored

### Unit Conventions

| Metric               | Unit              | Notes                                    |
|:---------------------|:------------------|:-----------------------------------------|
| Running distance     | **km**            | `DistanceWalkingRunning_sum`             |
| Swimming distance    | **meters**        | `DistanceSwimming_sum` (NOT km!)         |
| Cycling distance     | **km**            | `DistanceCycling_sum` (indoor = no data) |
| Elevation            | **cm** in CSV     | `meta_ElevationAscended` — divide by 100 |

---

## GPS Accuracy & Anomaly Detection

GPS data comes from the Apple Watch, which can produce anomalies when it loses satellite signal and falls back to WiFi/cell-tower positioning. This commonly happens near buildings or tunnels, creating sudden "jumps" of 5-20 km to your home address.

### How the Detection Works

The system runs a **multi-pass anomaly detection algorithm** (`_detect_and_fix_gps()`) on every workout with GPS data:

| Pass   | What It Does                                                                       |
|:-------|:-----------------------------------------------------------------------------------|
| **1**  | Flags points with impossible speed (>50 km/h between consecutive GPS readings) or impossible elevation (>2000m or <-500m) |
| **1.5**| Flood-fill: marks all points between jump pairs that are >5 km from the route median. Also flags points after the last bad point that are still far from median |
| **2**  | Expands bad points by +/-2 neighbors (GPS drift lasts several seconds)             |
| **3**  | Nulls out lat/lon/elevation/speed for all flagged points                           |

### What Gets Reported

For each workout, the system calculates and stores:

- **Original GPS distance** — total distance including bad points
- **Corrected GPS distance** — total distance excluding flagged anomalies
- **Original elevation gain** — including false elevation from jumps
- **Corrected elevation gain** — excluding flagged anomalies
- **Anomaly count** — number of flagged GPS points

On workout maps (Leaflet), flagged GPS points are excluded so the route displays cleanly. Indoor workouts have no GPS data and show no map.

### GPS from GPX Route Files

When a workout's CSV has no GPS data (common when data is split across files), the system falls back to loading GPS coordinates from matching `.gpx` route files in `training_data/workout-routes/`. This is handled by `_load_gpx_route()` in `workout_analysis.py`:

- Matches GPX files by workout date
- Parses standard GPX XML format to extract `(timestamp, lat, lon, elevation)` tuples
- GPS data is used for overview maps, interval maps, and HR-colored route segments
- HR zone coloring on GPX-derived routes uses nearest-neighbor timestamp matching (30s tolerance) to merge HR data from the CSV with GPS positions from the GPX file

This keeps GPS data out of the main workout CSV (reducing LLM token cost when raw data is sent to coaches) while still enabling full map functionality.

---

## Workout Merging

When Apple Watch creates multiple workout records for a single session (e.g., pausing mid-ride), IronCoach can merge them:

| Merge Type    | Gap         | How It Works                                              |
|:--------------|:------------|:----------------------------------------------------------|
| **Auto-merge**| Configurable (default 10 min) | Same discipline, automatically merged on data load. Gap threshold configurable in Admin > Settings. |
| **Suggested** | 10-30 min   | Same discipline, shown in PostImportModal + WorkoutDetailModal for user approval |
| **Manual**    | Any         | Multi-select in All Workouts page, or merge button in workout detail |

Merged workouts sum duration, distance, and calories. The earlier start and later end times are kept. Original workout numbers are tracked for data combination.

**Merge UI locations:**
- **All Workouts page**: Select 2+ same-discipline workouts via checkboxes → bottom action bar with Merge button
- **Workout Detail modal**: Detects adjacent same-discipline workouts within 30 min → shows merge suggestion banner
- **PostImportModal**: After import, suggests merges for 10-30 min gap workouts

After merging, insight regeneration runs automatically (best-effort — works without Claude CLI).

## Hidden Workouts

Hide workouts you don't want to see in regular views (e.g., accidental recordings, test workouts):

- **How to hide**: Select workouts via checkboxes on any page (All Workouts, Running, Cycling, Swimming, Overview) → action bar shows "Hide" button
- **Where hidden**: Filtered from all user-facing views — summary, discipline pages, weekly stats, bricks, recovery
- **NOT hidden from**: Internal processing (insights, merges) — to preserve data integrity
- **Show hidden toggle**: All Workouts page has a "Show hidden" toggle to reveal hidden workouts (shown at 50% opacity with a tag)
- **Unhide**: Select hidden workouts when visible → action bar shows "Unhide" button
- Stored in `app_settings` as `hidden_workouts` (JSON array of workout_nums)

---

## Brick Detection

A "brick" session is when you do two different disciplines back-to-back (e.g., bike followed immediately by a run — common in triathlon training).

IronCoach **auto-detects bricks** by finding workouts of different disciplines that start within 30 minutes of each other. This is code-based detection (no AI involved):

- Detected bricks are displayed as grouped sessions in the workout tables
- **Combined insight**: all workouts in a brick share ONE combined insight that analyzes the full session — transitions, fatigue management, and per-discipline observations
- Brick detection is view-only — it doesn't merge or modify the underlying workout data
- **Post-import brick display**: detected bricks are shown in the post-import modal (purple border) before merge candidates, so you can see which sessions will get combined analysis

---

## AI Coaching Chat

The chat panel (right side) connects you to coaching agents:

| Agent                  | Role                                                            |
|:-----------------------|:----------------------------------------------------------------|
| **IronCoach (main)**   | Primary coach. Knows your race, training history, all insights. Delegates to specialists automatically. |
| **Run Coach**          | Running specialist. Analyzes splits, cadence, HR drift, power.  |
| **Swim Coach**         | Swimming specialist. Analyzes sets, stroke count, SWOLF, pace.  |
| **Bike Coach**         | Cycling specialist. Analyzes speed, power, cadence, elevation.  |
| **Nutrition Coach**    | Sports nutrition. Log meals via photo/text (Hebrew or English). |

### How Chat Sessions Work

- Click "New Chat" to start a fresh conversation
- Each chat session is backed by a Claude CLI session (JSONL file)
- Specialist coaches share their session between chat and insights — so the run-coach remembers your recent run analyses
- **Sessions auto-rotate at configurable threshold** (default 800KB, adjustable in Admin > Settings) to control costs. After rotation, the coach receives context from the database:
  - Discipline coaches: last 5 workout insights for that discipline
  - Nutrition coach: last 15 meals
  - Main coach: reads `insights_summary.md` file for overall context
  - Chat history: configurable in Admin > Sessions > Settings:
    - **AI Summary** (default): Haiku summarizes the last 10 messages into bullet points (~$0.001)
    - **Last 10 Messages**: sends raw messages as-is (free, less concise)

### Multi-Agent Delegation

The main-coach can delegate questions to specialist coaches automatically:
1. Main-coach uses `ToolSearch` to discover available tools
2. Then uses the `Agent` tool to delegate to the appropriate specialist
3. The specialist runs with its own tools and context
4. Main-coach synthesizes the specialist's response

### Developer Chat (Admin Only)

A separate code-focused chat mode for making changes to the app:
- Opened via the terminal icon (▸_) in the topbar (admin users only)
- **Lead Dev (main-dev)** — orchestrator agent that delegates to all dev specialists (like main-coach for coaching)
- Uses development agents: frontend-dev, backend-dev, data-pipeline, code-simplifier, and review agents
- **No session rotation** — dev sessions can grow without size limit
- **No coach preamble** — no athlete data or workout insights injected
- **Full code tools** — Edit, Write, Glob, Bash (can modify the codebase)
- Session switching, renaming, and deletion — same as coaching chat
- Sessions are filtered by mode (coach sessions don't appear in dev mode and vice versa)

### Agent Memory

Per-agent-type persistent memory, similar to Coach Memory but scoped to individual agents:
- Each agent type (e.g., run-coach, frontend-dev) has its own memory space
- Per-user — each user has their own agent memories
- Accessible from the Sessions panel in chat (collapsible section at the bottom)
- CRUD: add, edit, delete memory entries per agent
- Automatically injected into that agent's prompts (both coach and dev modes)
- Agents can also read/write their own memory via ACTION blocks (executed server-side by chat handler)

---

## AI Workout Insights

On the Insights page, generate per-workout AI analysis:

### Generation Flow

```
Discipline coach (run/swim/bike) analyzes the workout data
  + Nutrition coach checks fueling for that workout
    -> Main coach synthesizes both into a final insight
```

Each insight includes: summary, key observations, nutrition note, improvement tip, and plan comparison (if a training plan exists).

### Batch Cancel

Cancelling insight generation (X button in notification bell):
- **Immediate feedback**: Button shows pulsing clock icon and disables while cancelling
- **Kill propagation**: All running Claude CLI subprocesses are killed (double sweep with 0.5s delay). Cancel flag checked in retry loops and parallel discipline queues — no wasted retries
- **Cancel notification**: A "Cancelled by user" notification is saved (orange "Cancelled" badge) — the existing partial progress is preserved as a separate entry
- **Ownership check**: Only the user who started the batch can cancel it

### Three Insight Modes

| Tab              | What It Does                                                     |
|:-----------------|:-----------------------------------------------------------------|
| **Single**       | Generate insight for one workout (with date filter)              |
| **All Pending**  | Batch generate all workouts missing insights                     |
| **Period**       | Overall training assessment for a date range (not per-workout)   |

### Post-Import Flow

After importing new workouts, a modal shows:

1. **Brick sessions** (purple border) — detected multi-discipline sessions (e.g., bike → run). Informational — these get combined insights automatically. Each workout is clickable for preview.
2. **Merge suggestions** (green border) — same-discipline workouts with 10-30 min gap. Must be exact same type (Running+Walking won't be suggested). Check to merge.
3. **Nutrition reminder** (blue banner) — dates without any logged meals, with link to Nutrition page.
4. **Workout selection** — checkboxes to pick which workouts get AI insights. Each row shows workout type, date, start–end times, duration, and distance.
5. **Include detailed data** — per-workout checkbox to include raw CSV time-series data in the AI prompt. Off by default. Selecting this gives the coach more granular data (every heartbeat, every GPS point) at the cost of more tokens. Only selected workouts send raw data — not a global toggle.
6. **Notes + photos per workout** — text inputs for context (e.g., "felt tired", "first brick session") and photo attachments (e.g., food photos, route photos). Discipline coaches always see attached images. Nutrition coach only receives images when the text mentions food with a photo reference (e.g., "ate banana see pic"). Nutrition coach is skipped entirely when there's no nutrition data and no food-related notes — saving tokens.
7. **Dismiss system** — workouts you skip (uncheck) are remembered and won't reappear in the next post-import modal. Stored in `app_settings` as `dismissed_insights_{uid}`.
8. **Cost confirmation** — selecting more than 10 workouts triggers a confirmation step showing estimated cost before generating.
9. **Reopen without re-import** — closing the modal via ESC/X (without Skip or Generate) saves the data. A pulsing yellow icon appears in the topbar to reopen the modal without re-importing.
10. **Multi-file accumulation** — uploading a second file before acting on pending import merges both sets of workouts, merge candidates, and brick sessions. Duplicates are deduplicated by workout number.

If more than 10 new workouts are imported, none are selected by default and a warning banner shows the estimated cost. For 10 or fewer, all are selected by default.

**ESC behavior**: pressing ESC while previewing a workout detail only closes the preview, not the post-import modal.

---

## Nutrition Tracking

Three ways to log meals:

1. **AI Analysis**: Type what you ate (or attach a photo) in the text box and click Analyze. Claude extracts macros. Supports Hebrew and English. One-shot call (`--no-session-persistence`) — cheapest AI path.
2. **Recent Items**: As you type, a dropdown shows food items from past meals with per-unit macros. Click to select, adjust quantity with +/- buttons, then Save — no LLM call ($0.00). Items are normalized by `base_name` (e.g. "ביצה מקושקשת") so different phrasings of the same food deduplicate automatically. Different cooking methods keep separate entries (scrambled egg vs boiled egg = different macros).
3. **Manual**: Add meals directly on the Nutrition page with custom macros.

Meals are grouped by meal type (breakfast, lunch, dinner, etc.) with collapsible sections and per-group macro totals.

**Recent items are not a separate cache** — they're derived from the last 200 entries in `nutrition_log` (zero extra storage).

### Progress Rings & Daily Targets

The Nutrition page shows five progress rings (calories, protein, carbs, fat, water) comparing daily intake against configurable targets. Click the gear icon to open the Daily Nutrition Targets modal:

- **Manual targets**: Set your own daily goals for each macro
- **Smart Suggest (AI)**: One-click AI suggestion based on your actual body metrics (weight, body fat% from scale), training volume, race proximity, and training phase. Uses a one-shot prompt (no session) for minimal cost.
- **Auto-update weekly**: Opt-in toggle to automatically recalculate targets every Sunday at 06:00 using AI. Requires both admin-level AI toggle and per-user opt-in.
- **Per-field InfoTip**: Each macro field has an (i) icon with goal-specific recommendations (endurance, muscle building, weight loss)

The AI suggestion uses real body metrics from Apple Health (not estimates) and explains its reasoning (e.g., "At 65.8kg with 12.5% body fat, targeting 1.6g/kg protein...").

### Energy Balance Calculation

The Nutrition page shows daily energy balance:

| Component     | Source                                                          |
|:--------------|:----------------------------------------------------------------|
| **BMR**       | Basal metabolic rate (calculated from profile)                  |
| **Workout**   | Calories from tracked workouts                                  |
| **NEAT**      | Non-exercise activity thermogenesis = Apple active calories minus workout calories. Includes steps, fidgeting, daily movement. |
| **TEF**       | Thermic effect of food (~10% of intake)                         |

### When Nutrition Triggers Insight Regeneration

This feature is **configurable in Admin > Settings** (`nutrition_regen_enabled`, on by default). When enabled, logging a meal checks if any existing workout insights should be updated with the new nutrition data. The fueling window (pre/post hours) is also configurable in Admin > Settings.

An insight is regenerated only when **both** conditions are met:

1. **The meal was logged after the insight was generated** — if the insight already had the meal data, no update needed
2. **The meal is relevant to the workout timing** — the meal must fall within the workout's fueling window:

```
|----4h before----|---WORKOUT---|---2h after---|
     ↑ pre-workout window         ↑ post-workout window
     (configurable 2-6h)           (configurable 1-4h)
```

For example: if a workout starts at 07:00 and lasts 1 hour (ends 08:00), with default windows (4h pre, 2h post):
- Meals from 03:00-10:00 are considered relevant
- A dinner at 20:00 would NOT trigger regeneration

If a meal is outside this window, the insight is not regenerated. After submitting a meal, a feedback banner tells you whether any insights will be updated and which ones.

### Post-Import Nutrition Check

When you import new workouts, the system checks which dates have nutrition data logged. Dates without any meals show a blue reminder banner in the PostImportModal, encouraging you to log meals on the Nutrition page for better workout insights.

---

## Training Plan

Upload or create a training plan on the Plan page. The phase bar shows a proportional timeline with four training phases and a "Today" marker. When insights are generated, they automatically compare planned vs actual (distance, duration, intensity).

### Training Phases

The phase bar calculates proportional phases based on your primary race date:

| Phase | Duration | Description |
|:------|:---------|:------------|
| **Taper** | Last 14 days | Reduced volume, race preparation |
| **Peak** | Days 15-28 | Race-specific intensity |
| **Mid** | 40% of remaining | Maintaining fitness, moderate volume |
| **Build** | 60% of remaining | Base building, increasing volume |

Phases are proportional — a 6-month plan has a longer build phase than a 2-month plan, while taper and peak stay fixed at 14 days each. The shared `trainingPhase()` utility is used by Overview, Recovery, and Training Plan pages for consistent phase-aware behavior.

---

## Events / Races

Track multiple events on the Events page:
- Set one as "primary" — it appears in the sidebar countdown
- The coach uses the primary event for context (race distance, cutoffs, target date)
- First event created is automatically set as primary

---

## Recovery Dashboard

The Recovery page shows (all computed from Apple Health data — free):

| Metric         | What It Shows                                                   |
|:---------------|:----------------------------------------------------------------|
| **CTL/ATL/TSB**| Chronic Training Load / Acute Training Load / Training Stress Balance (fitness/fatigue/form) |
| **TRIMP**      | Training Impulse — HR-based training load per workout           |
| **hrTSS**      | Heart Rate Training Stress Score — normalized to lactate threshold HR |
| **VO2max**     | Extracted from Apple Watch (running/walking/hiking only)        |
| **Sleep**      | Sleep duration and quality trends                               |
| **Resting HR** | Resting heart rate over time                                    |
| **HRV**        | Heart rate variability trends                                   |

### Race Day Readiness

Shared `RaceReadinessBar` component used on both Overview and Recovery pages:
- **TSB bar** with 4 body-state zones: Fatigued (red), Loaded (orange), Fresh (yellow), Peaked (green)
- White marker shows current TSB position on the bar
- Zone labels visible inside the bar; hover for detailed description with TSB ranges
- **Overview** shows only the nearest upcoming event; **Recovery** shows all events
- **Phase-aware recommendations**: static translation keys selected by TSB zone x time phase (not LLM-generated). Evaluates whether your current body state aligns with race timeline (e.g., fresh is good near race day, concerning 6 months out)
- Recommendation color: green = aligned, yellow = slightly off, red = misaligned
- Info tip explains TSB concept and all zone colors

### Readiness Score

The Overview page shows a Readiness Score (0–100) that combines multiple recovery signals into a single number:

| Component | Weight | Data Window | What It Measures |
|:----------|:-------|:------------|:-----------------|
| **TSB (Form)** | 30% | CTL 42-day, ATL 7-day | Training stress balance — fitness minus fatigue (Banister impulse-response model) |
| **HRV** | 25% | 7-day trend | Heart rate variability trend vs baseline |
| **Sleep** | 20% | 3-day average | Sleep duration relative to 7-8h target |
| **Resting HR** | 15% | 7-day trend | Resting heart rate trend vs baseline |
| **Training Load** | 10% | 7-day TRIMP | Recent training load appropriateness |

The score uses the Banister impulse-response model for TSB: CTL (fitness) uses a 42-day exponential decay, ATL (fatigue) uses a 7-day decay. These windows are standard in sports science for modeling the delayed fitness response vs rapid fatigue response to training.

### Phase-Aware Recovery Status

Recovery and fatigue KPI colors adjust based on training phase:
- **Build/Mid phase**: Higher fatigue is expected — thresholds are more lenient (e.g., recovery 50%+ is green)
- **Taper phase**: Recovery should be improving — tighter thresholds (70%+ for green)
- **Peak/Race phase**: Full recovery expected — strictest thresholds (80%+ for green)

The Overview page shows a recovery gauge with percentage and label (Fresh/Moderate/Fatigued) that reflects both the current recovery value and the training phase context, surrounded by orbiting KPI cards for fitness, fatigue, sleep, HRV, resting HR, VO2max, TRIMP, and hrTSS.

### Risk Alerts

The Overview page shows dismissible risk alerts based on training load patterns and recovery metrics:

| Alert | Trigger | Severity |
|:------|:--------|:---------|
| **Load spike** | Weekly TRIMP increased >30% vs previous 7 days | Warning (30-50%), Danger (>50%) |
| **Low recovery** | Recovery below 30% for 3+ consecutive days | Danger |
| **Poor sleep** | Average sleep under 6h over last 3 days | Warning |
| **Elevated resting HR** | Resting HR >5 bpm above 7-day rolling average | Warning |
| **Low HRV** | HRV >15% below 7-day rolling average | Warning |

Load spike alerts are **normalized by training days** — a partial current week is only compared to the previous week if it has at least as many training days, preventing false alarms mid-week.

---

## Body Metrics

Weight, body fat%, muscle mass, lean mass, BMI — all from Apple Health (including smart scale data like LeaOne/eufy Life). Reference range bands with cited sources are shown on charts.

---

## Coach Memory

Each user can save persistent memories accessible from the user avatar menu:
- Memories are injected into **all** coaching prompts (chat and insights)
- Use to set preferences: "always align tables", "I prefer morning runs", "my pool is 25m"
- Coaches can also save memories during conversations via API
- Full CRUD: create, read, update, delete via the UI or API

---

## Multi-User & Auth

- Admin creates the first account on setup
- Other users can self-register (as `user` role)
- Each user has isolated data: insights, nutrition log, chat sessions, token usage, coach memory
- **Per-user API isolation**: All API endpoints use JWT-derived `user_id` (via `_uid(request)`). No endpoint falls back to a default user. Insight batch status, streaming sessions, and notifications are filtered by owner. Chat stop verifies session ownership. Settings use an allowlist (`_ALLOWED_USER_SETTINGS`).
- Admin page shows all users with cost/token breakdowns

### Password Reset

| Scenario | How To |
|:---------|:-------|
| **Regular user** | User avatar menu > "Change Password" |
| **Admin resets user** | Admin > Users page > edit user account |
| **Admin locked out** | Delete `backend/data/dashboard.db` and restart. Creates fresh DB — first visitor creates new admin. (To preserve data: use SQLite CLI to update the password hash directly.) |

### Auth Details

| Feature             | Implementation                                      |
|:--------------------|:----------------------------------------------------|
| **Tokens**          | JWT with HMAC-SHA256, httpOnly cookie, 72h expiry   |
| **Session switch**  | Multiple sessions stored per browser                |
| **Agent actions**   | Agents use `[ACTION:...]` blocks — server executes with user's auth context |

### Browser Storage

| Storage          | Key                     | Purpose                                       |
|:-----------------|:------------------------|:----------------------------------------------|
| **localStorage** | `lang`                  | UI language (en/he)                           |
| **localStorage** | `dateFrom`              | Date range filter start date                  |
| **localStorage** | `insightLang`           | Language for AI insight generation             |
| **localStorage** | `chatWidth`             | Chat panel width (persists resize)            |
| **localStorage** | `navOrder`              | Sidebar page order (drag-to-reorder)          |
| **localStorage** | `auth_sessions`         | Multi-user session tokens for account switch  |
| **localStorage** | `nutrition_suggestion_{uid}` | Last AI-suggested nutrition targets (per user) |
| **sessionStorage** | `chat-session-id`     | Current chat session UUID                     |
| **sessionStorage** | `chat-session-agent`  | Current chat agent (main-coach/specialist)    |
| **sessionStorage** | `chat-open`           | Chat panel open/closed state                  |
| **sessionStorage** | `chat-draft`          | Unsent chat message (cleared on send)         |
| **sessionStorage** | `nutrition-draft`     | Unsent meal text (cleared on submit)          |
| **Cookies**      | `token`                 | JWT httpOnly cookie, 72h expiry (server-set)  |

No IndexedDB, Cache Storage, or Service Workers used.

---

## Admin Settings

All settings are in **Admin > Settings** tab:

| Setting | What It Controls |
|:--------|:-----------------|
| **AI Features** | Enable/disable all AI features (chat, insights, nutrition analysis). Disabled by default — must be enabled manually. Shows cost warning on enable. |
| **Agent Model** | Override Claude model for all AI calls (chat, insights, nutrition) |
| **Session Rotation Size** | JSONL rotation threshold in KB (default 800KB). Controls when sessions are archived and restarted. |
| **Session Rotation Context** | AI summary vs raw messages when chat session rotates |
| **LLM Credential Check** | Quick preflight validation before LLM calls after idle time. Fails fast on expired tokens instead of waiting minutes. Default: 6h. Set to Off to disable. |
| **Auto-Merge** | Enable/disable + gap threshold (5-30 min) for same-discipline workout merging |
| **Manual Merges** | View count + clear all user-approved merge pairs |
| **Auto-regen insights on meal** | Toggle automatic insight regeneration when meals are logged |
| **Nutrition in insights** | Include/exclude nutrition context when generating workout insights |
| **Fueling window** | Pre-workout (2-6h) and post-workout (1-4h) window for meal relevance |
| **Auto-suggest nutrition targets** | Enable/disable weekly AI nutrition target suggestions (Sunday 06:00). Per-user opt-in required. |

### Agent Model Override

How it works:
- The setting appends a `--model` flag to **every** Claude CLI invocation (`_run_agent_cli()` and chat WS)
- This affects **all** AI calls: chat, insights, nutrition analysis
- **Only short aliases accepted**: `sonnet`, `opus`, `haiku` — the CLI resolves these to the correct provider-specific model ID (works with Bedrock, Vertex, and direct API)
- Any other value (e.g. `claude-sonnet-4-6`) is auto-normalized to the matching alias, or ignored if no match
- **Delegated agents inherit the model**: when main-coach delegates to run-coach via the Agent tool, run-coach uses the same model as the parent
- **Direct agent calls also get it**: insight generation calling run-coach directly gets its own `--model` flag from the same setting
- **One-shot calls too**: meal analysis (with `--no-session-persistence`) also gets the override
- It does **not** change your global Claude CLI default — it's per-invocation only
- Set to empty to use the Claude CLI's default model

In short: the override affects everything. There are no "fixed" calls that bypass it.

---

## AI Session & Cost Architecture

Understanding this helps you control costs.

### CLI Sessions = JSONL Files

Every agent conversation is stored as a JSONL file. When you resume a session (`--resume`), the **entire file** is sent to the Claude API as input tokens. Bigger file = more tokens = more cost per message.

### How IronCoach Controls Cost

| Mechanism              | What It Does                                                     |
|:-----------------------|:-----------------------------------------------------------------|
| **Session rotation**   | Session file exceeds threshold (default 800KB, configurable in Admin → Sessions) -> archived, fresh session starts. Context summary injected. Notification created. |
| **Batch rotation**     | During "Generate All": discipline coaches rotate every 5 workouts, nutrition rotates daily. Prevents giant sessions. |
| **`--max-turns`**      | Limits tool-use loops: specialists = 3 rounds, synthesis = 1 round. Prevents runaway agent costs. |
| **One-shot calls**     | Meal analysis uses `--no-session-persistence` — no file created, no accumulated cost. |
| **Python analytics**   | All charts, tables, splits, HR zones computed in Python. Zero AI cost for viewing data. |
| **Code-based titles**  | Chat session titles generated by code, not LLM. |
| **No FYI calls**       | No fire-and-forget LLM calls. Main-coach reads `insights_summary.md` for context instead. |

### Context Injected After Session Rotation

| Agent              | Context Injected                                              |
|:-------------------|:--------------------------------------------------------------|
| Run/Swim/Bike coach| Last 5 workout insights for that discipline (truncated)       |
| Nutrition coach    | Last 15 meals with macros                                     |
| Main coach (chat)  | Pointer to `insights_summary.md` file                         |
| Any chat session   | Last 10 chat messages (for conversation continuity)           |

---

## Token Usage Tracking

All AI calls are tracked with: source, agent name, input/output tokens, cache tokens, cost, model, user ID.

- **Topbar indicator** (`$ 0.12 | 45.2Kt`): Shows your **cumulative total** cost and token count across all AI activity (chat, insights, nutrition, everything). Updates in real-time on LLM events only — checking is free (reads local SQLite).
- **Admin page**: Per-user breakdown with totals across all users.
- **Detailed popup** (click the topbar indicator): Four tabs:
  - **Summary**: Total cost, tokens, cache breakdown (read/write/base) with InfoTip explanations
  - **Per Agent**: Cost per agent grouped by model. Active model marked with `*`
  - **Daily**: Per-day totals. Click any row to expand and see per-agent+model breakdown for that date
  - **Per Model**: Cost breakdown by model (useful when switching between models)

**Important:** Token counts and cost estimates are approximate — they are parsed from Claude CLI output and may not exactly match your actual API billing. Always check your official Anthropic/AWS/GCP billing dashboard for accurate charges.

---

## Special Features

### Workout Merging
Same-discipline workouts with less than 10 minutes gap are auto-merged. Workouts with 10-30 min gap are suggested for user approval in the PostImportModal. User-approved merges are stored and applied on subsequent data loads. Code-based, not AI.

### Swimming Detail
Swimming workouts use real Apple Watch event data (segments + laps), not heuristic calculations:
- Auto Sets with per-100m and per-25m splits
- Stroke count per length
- SWOLF (Swim Golf) efficiency metric
- Rest interval detection between sets

### HR Zone Calculation
Heart rate zone time only counts intervals between consecutive HR readings, with a maximum 120-second gap. This prevents inflated zone times from sparse HR data.

### VO2max Tracking
VO2max values are extracted from per-workout CSVs (Apple Watch estimates, running/walking/hiking only). Displayed in the Recovery page KPI card and trend chart, and in workout detail modals.

### RTL Support
Full right-to-left support for Hebrew:
- CSS logical properties throughout
- `dir="auto"` on user/AI text content
- BiDi detection on input fields
- Interface language toggle (EN/HE)

### Post-Import Athlete Notes & Photos
After importing workouts, you can add context notes and attach photos per workout before generating insights. Notes like "first outdoor swim", "recovering from cold", or "deliberately easy pace" are injected into the AI analysis prompt so the coach understands your intent. Attached photos (e.g., food, route screenshots) are passed as image references — discipline coaches always see them. The nutrition coach only receives photos when the text mentions food with a photo reference (e.g., "ate banana see pic", "אכלתי בננה ראה תמונה"). If there's no nutrition data logged and no food in the notes, the nutrition coach is skipped entirely to save tokens.

**Food in notes is auto-saved**: If your note mentions food (e.g. "ate banana and energy gel before the run"), the system automatically extracts it, checks if it's already logged for that date, and saves new meals to the nutrition log. The insight then includes the newly saved nutrition data.

---

## Claude Code Integration

IronCoach is built with **Claude Code** (Anthropic's CLI for Claude). The project uses agents, skills, and a CLAUDE.md file to power both the dashboard AI features and the development workflow.

### CLAUDE.md

The root `CLAUDE.md` file is auto-loaded by Claude Code on every conversation. It contains a lean project overview: structure, how to run, tests, and pointers to skills for deeper reference. Backend and frontend each have their own `CLAUDE.md` with directory-specific context. This acts as persistent memory for any Claude Code session working on the project.

### Agents (`.claude/agents/`)

Agents are specialist Claude Code sub-agents with their own system prompts, tool access, and behaviors. They run as Claude CLI subprocesses.

#### Coaching Agents (used by the dashboard)

| Agent | File | Purpose | Used By |
|:------|:-----|:--------|:--------|
| **IronCoach (main)** | `main-coach.md` | Primary triathlon coach. Chat, training questions, insight synthesis. Delegates to specialists via `Agent` tool. | Chat panel, insight synthesis |
| **Run Coach** | `run-coach.md` | Running specialist. Per-km splits, cadence, HR drift, power, ground contact time. | Chat, run workout insights |
| **Swim Coach** | `swim-coach.md` | Swimming specialist. Per-100m segments, stroke count, SWOLF, pace consistency. | Chat, swim workout insights |
| **Bike Coach** | `bike-coach.md` | Cycling specialist. Per-segment speed, power, cadence, HR response. | Chat, cycling workout insights |
| **Nutrition Coach** | `nutrition-coach.md` | Sports nutrition. Meal analysis (photo/text), fueling strategy, recovery nutrition. Can save meals via API. | Chat, meal analysis, nutrition insights |

#### Development Agents (used for building/maintaining the project)

| Agent | File | Purpose |
|:------|:-----|:--------|
| **Lead Dev (main)** | `main-dev.md` | Orchestrator for dev chat. Delegates to all dev agents (like main-coach for coaching). |
| **Frontend Dev** | `frontend-dev.md` | React/Vite specialist. Components, pages, CSS, charts, i18n, state management. |
| **Backend Dev** | `backend-dev.md` | FastAPI specialist. API endpoints, SQLite, data processing, Claude CLI integration. |
| **Data Pipeline** | `data-pipeline.md` | Apple Health XML parsing, CSV generation, incremental processing. |

#### Code Review Agents (used by `/ic-code-review` skill)

| Agent | File | What It Reviews |
|:------|:-----|:----------------|
| **Security Reviewer** | `security-reviewer.md` | Auth, injection, secrets, OWASP top 10, data exposure. |
| **Frontend Reviewer** | `frontend-reviewer.md` | Component patterns, performance, accessibility, state management. |
| **Backend Reviewer** | `backend-reviewer.md` | API design, error handling, data validation, performance. |
| **Data Reviewer** | `data-reviewer.md` | CSV parsing, data integrity, GPS anomaly logic, edge cases. |

### Skills (`.claude/skills/`)

Skills are reusable prompt templates invoked with `/skill-name` in Claude Code. They combine instructions with agent delegation for complex tasks.

#### Code Quality Skills

| Skill | Command | What It Does |
|:------|:--------|:-------------|
| **Code Review** | `/ic-code-review` | Full audit across 4 domains (security, frontend, backend, data pipeline). Runs 4 review agents in parallel, produces categorized report. |
| **Cleanup** | `/ic-cleanup` | Quick review + auto-fix on recently changed files. 3 agents check for code reuse, quality, and efficiency. |

#### Coaching Analysis Skills

| Skill | Command | What It Does |
|:------|:--------|:-------------|
| **Run Analysis** | `/run-analysis` | Running workout analysis: pace splits, cadence, HR zones, power. |
| **Swim Analysis** | `/swim-analysis` | Swimming analysis: per-100m segments, stroke count, SWOLF. |
| **Bike Analysis** | `/bike-analysis` | Cycling analysis: speed/power segments, elevation, cadence. |
| **Nutrition Analysis** | `/nutrition-analysis` | Meal/nutrition review: macro balance, fueling timing. |
| **Coaching Overview** | `/coaching-overview` | Training overview: periodization, load balance, race readiness. |

#### Architecture & Reference Skills

| Skill | Command | What It Does |
|:------|:--------|:-------------|
| **Frontend Architecture** | `/frontend-architecture` | Component map, CSS patterns, storage reference, formatters. |
| **Backend Architecture** | `/backend-architecture` | Full API map, DB schema, helper functions, auth details. |
| **Data Model** | `/data-model` | CSV schemas, unit conventions, workout types, stroke codes. |
| **Project Patterns** | `/project-patterns` | All architectural decisions and implementation rules (53 patterns). |

### How Agents Work in the Dashboard

1. **Chat**: User sends a message via the chat panel. The WebSocket handler spawns a Claude CLI subprocess with the appropriate agent's session file. Main-coach can delegate to specialists using `ToolSearch` + `Agent` tools.

2. **Insights**: `_call_agent()` in `services/claude_cli.py` runs specialist agents directly (not through main-coach). Each specialist analyzes the workout data, then main-coach synthesizes the results.

3. **Meal Analysis**: One-shot call with `--no-session-persistence`. No session file created — cheapest path.

4. **Session sharing**: Chat and insight generation share the same CLI session for discipline specialists (e.g., `run-coach-user1`). This means the run-coach remembers recent analyses during chat.

---

## Detailed Workout Data

The workout detail modal has a **Detailed Data** tab that shows pre-computed interval analysis, heart rate profiles, and elevation profiles — all computed in Python with **zero AI cost**.

### Interval Detection

The system detects work/rest intervals by analyzing Apple Health speed data:

- **Speed threshold**: 80% of average speed separates work from rest
- **Smoothing**: 10-second rolling average to filter noise
- **Minimum duration**: 10 seconds (shorter segments are merged into adjacent intervals)
- Intervals are typed as **Work** or **Rest** based on speed relative to threshold

Each interval shows: type (work/rest), duration, distance (KM), average HR, average power, and average cadence (when available).

### Interval Map

A popup map shows the workout route colored by interval type:

| Color | Meaning |
|:------|:--------|
| **Blue (#2196F3)** | Work intervals |
| **Orange (#FF5722)** | Rest intervals |

- The map is always colored by work/rest — clicking a row's locate button pans to that interval's location on the map (adds a marker, does not re-color)
- Expand button (↗) opens the map as a near-fullscreen overlay
- Available whenever the workout has GPS data (from CSV or GPX route files)

### HR & Elevation Profiles

Pre-computed charts in the Detailed Data tab:

- **HR Over Time**: Heart rate profile across the workout duration
- **Elevation Over Time**: Elevation changes throughout the workout

### Summary Cards

- Min/max heart rate
- Total ascent and descent
- Min/max elevation

All data comes from `.sections.json` files pre-computed during data processing.

---

## Workout Deletion

Delete workouts permanently from the All Workouts page:

1. Select workouts via checkboxes
2. Click the Delete button in the action bar (requires two clicks for confirmation)
3. The system removes all associated data:

| Location | What's Deleted |
|:---------|:---------------|
| **Per-workout files** | CSV, splits JSON, sections JSON, HR summary JSON |
| **Summary CSV** | Rows for those workout numbers |
| **Database** | Workout insights, hidden/dismissed entries |
| **Export state** | `.export_state.json` entries (prevents "no new workouts" on re-import) |
| **Pending import** | Deleted workouts removed from pending import data (merge candidates, brick sessions too). Icon cleared if no workouts remain. |

---

## Limitations & Missing Features

### Current Limitations

| Limitation                          | Details                                                   |
|:------------------------------------|:----------------------------------------------------------|
| **Apple Health required**           | Data must be in Apple Health (Apple Watch, Garmin, Polar, etc. via sync). No manual workout entry. |
| **Manual export**                   | Re-export from iPhone + re-import each time. No auto-sync.|
| **Localhost only**                  | No HTTPS, no cloud deployment. Designed for local use.    |
| **Claude CLI required for AI**      | Dashboard works without it (charts + data), but no chat/insights. |
| **No offline AI**                   | AI calls require internet. Analytics still work offline.  |
| **Swimming detail needs Apple Watch** | Auto sets and per-lap splits require Apple Watch swim data. |
| **One export per user**             | Each user imports one Apple Health export. Multi-user supported with isolated data per user. |

### Features Not Yet Implemented

- **Direct Garmin / Strava import** — currently requires syncing to Apple Health first
- **Automatic sync** — requires manual export/import cycle
- **Workout manual entry** — no way to add workouts that aren't in Apple Health
- **HTTPS / remote access** — localhost only, no SSL support
- **Mobile-responsive layout** — designed for desktop browsers
- **Workout comparison** — side-by-side comparison of two workouts
- **Goal tracking** — no weekly/monthly goal targets
- **Social features** — single-user or small-group, no community
