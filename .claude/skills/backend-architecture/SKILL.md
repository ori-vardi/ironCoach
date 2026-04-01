# Backend Architecture Skill

## Server Structure (modular FastAPI)

`server.py` is a thin entry point (~270 lines): app creation, middleware, router registration.
Code is organized into `config.py`, `routes/` (15 modules), `services/` (7 modules), `data_processing/` (7 modules).

### Startup flow
1. `config.py` loads `.env`, sets up logging, defines constants/paths
2. `server.py` lifespan: backup DB, init SQLite, run migrations, apply GPS corrections
3. Routes registered via `app.include_router()` from `routes/*.py`
4. Start uvicorn on port 8000

### Data sources
- **CSV files** (`training_data/`) — workout summaries, time-series, body metrics, daily aggregates, recovery
- **SQLite** (`data/dashboard.db`) — training plan, nutrition, chat history, insights, race info, sessions, notifications

## API Endpoints (complete map)

### Workout Data (CSV-based)
| Method | Path | Description |
|---|---|---|
| GET | `/api/summary` | All workouts summary (merged) |
| GET | `/api/workout/{num}` | Workout time-series (supports `?merge_with=N,N`) |
| GET | `/api/workout/{num}/sections` | Per-100m/km computed sections |
| GET | `/api/workouts/by-type/{type}` | Filter by discipline (run/bike/swim/strength/other) |
| GET | `/api/stats/weekly` | Weekly aggregated stats |
| GET | `/api/recovery` | Recovery page data (TSB, TRIMP, sleep, HRV) |
| GET | `/api/body-metrics` | Body metrics from CSV |
| POST | `/api/body-metrics` | Add body metric entry (appends to CSV) |
| GET | `/api/energy-balance` | Daily energy balance (BMR + active + steps) |

### Training Plan (SQLite)
| Method | Path | Description |
|---|---|---|
| GET | `/api/plan` | All plan entries |
| GET | `/api/plan/week` | Current week's plan |
| POST | `/api/plan` | Create plan entry |
| PUT | `/api/plan/{id}` | Update plan entry |
| DELETE | `/api/plan/{id}` | Delete plan entry |

### Nutrition (SQLite)
| Method | Path | Description |
|---|---|---|
| GET | `/api/nutrition` | Entries for date (`?date=`) |
| GET | `/api/nutrition/range` | Entries for range (`?from=&to=`) |
| POST | `/api/nutrition` | Add meal entry |
| PUT | `/api/nutrition/{id}` | Update meal |
| DELETE | `/api/nutrition/{id}` | Delete meal |
| POST | `/api/nutrition/analyze` | AI meal analysis (returns JSON array) |

### Insights (SQLite + Claude CLI)
| Method | Path | Description |
|---|---|---|
| GET | `/api/insights/workout/{num}` | Get cached workout insight |
| GET | `/api/insights/all` | All workout insights |
| GET | `/api/insights/missing` | Workouts without insights |
| GET | `/api/insights/general` | General training assessment |
| POST | `/api/insights/generate/{num}` | Generate single workout insight |
| POST | `/api/insights/generate-batch` | Batch generate (date range) |
| POST | `/api/insights/general/generate` | Generate general assessment |
| GET | `/api/insights/status` | Batch progress polling |
| POST | `/api/insights/notifications` | Add notification |
| DELETE | `/api/insights/notifications` | Clear all notifications |
| DELETE | `/api/insights/notifications/{id}` | Delete single notification |

### Chat (WebSocket + SQLite)
| Method | Path | Description |
|---|---|---|
| WS | `/ws/chat` | Real-time chat with Claude CLI |
| GET | `/api/chat/sessions` | List chat sessions |
| GET | `/api/chat/history/{sid}` | Chat history for session |
| DELETE | `/api/chat/sessions/{sid}` | Delete session |
| POST | `/api/chat/upload` | Upload file for chat |

### Race Info (SQLite)
| Method | Path | Description |
|---|---|---|
| GET | `/api/race` | Get race config |
| PUT | `/api/race` | Update race config |

### Import & System
| Method | Path | Description |
|---|---|---|
| POST | `/api/import` | Import Apple Health data (returns new_workouts, merge_candidates, brick_sessions with start/end times) |
| POST | `/api/browse-folder` | Browse filesystem folders |
| GET | `/api/pick-folder` | macOS folder picker dialog |
| GET | `/api/agents` | List Claude agents |
| GET | `/api/sessions` | List agent sessions |
| GET | `/api/sessions/{uuid}/transcript` | Session transcript |
| DELETE | `/api/sessions/{uuid}` | Delete session |
| DELETE | `/api/sessions` | Bulk delete sessions |

### Admin (requires admin role)
| Method | Path | Description |
|---|---|---|
| GET | `/api/admin/users` | List all users |
| POST | `/api/admin/users` | Create user |
| DELETE | `/api/admin/users/{id}` | Delete user |
| GET | `/api/admin/chat-sessions` | All users' chat sessions (with Claude session link) |
| GET | `/api/admin/chat-history/{sid}` | Any user's chat history |
| GET | `/api/admin/logfiles` | List server log files |
| GET | `/api/admin/logfiles/{name}` | Read log file (tail 200 lines) |

## Auth & Multi-User
- First-run setup auto-creates admin. Self-registration for `user` role.
- JWT (custom HMAC-SHA256 in `auth.py`), httpOnly cookie, 72h expiry. Session switching via stored tokens.
- Middleware in `server.py`: all `/api/` routes require auth except login/logout/setup/signup/switch/has-users
- Agent Action System: agents output `[ACTION:name {...}]` blocks; chat handler in `chat_handler.py` intercepts, executes via `agent_actions.py`, strips from user output.

## Chat — Multi-Agent Architecture
- Claude CLI agents with persistent sessions. Per-frontend-session CLI sessions.
- **Two chat modes**: coach (training) and dev (code changes, admin-only)
- main-coach delegates to specialists via `Agent` tool (needs `ToolSearch` first)
- Specialist agents (run/swim/bike/nutrition-coach) for direct chat + insight analysis
- **main-dev** orchestrator for dev chat — delegates to all dev agents
- Dev agents: no rotation, no coach preamble, full Edit/Write tools
- Parallel streaming: multiple coaches can run simultaneously
- Agent Memory: per-agent-type, per-user memory injected into prompts
- See `docs/FEATURES.md` for detailed chat internals

## Database Schema (database.py)

```sql
users (id, username, password_hash, display_name, role, token_version, created_at)
server_logs — auto-managed log table
training_plan (id, date, discipline, workout_type, description, duration_min, distance_km, notes, completed, actual_notes)
nutrition_log (id, date, meal_type, description, calories, protein_g, carbs_g, fat_g, hydration_ml, notes, created_at)
chat_history (id, session_id, role, content, timestamp, file_path, user_id)
workout_insights (id, workout_num UNIQUE, insight_text, model, created_at)
general_insights (id, insight_text, period_label, model, created_at)
period_insights (id, insight_text, period_label, from_date, to_date, model, created_at)
race_info (id PRIMARY KEY DEFAULT 1, race_name, race_date, swim_distance_km, bike_distance_km, run_distance_km, ...)
events (id, name, type, date, distances, cutoffs, targets, notes, is_primary)
agent_sessions (id, session_uuid UNIQUE, agent_name, context_key, created_at, last_used_at, message_count, notes)
notification_history (id, label, detail, link, finished_at, user_id)
chat_session_titles (id, session_id, title, mode, user_id)
token_usage (id, user_id, source, agent, input_tokens, output_tokens, cache_read, cache_write, cost, model, created_at)
app_settings (key PRIMARY KEY, value) — global + per-user settings
coach_memory (id, user_id, content, created_at)
agent_memory (id, user_id, agent_type, content, created_at)
```

## Key Helper Functions

### Data loading
- `_load_summary()` → reads `00_workouts_summary.csv`, returns list[dict]
- `_load_workout_timeseries(num)` → reads `workout_NNN_*.csv`
- `_load_body_metrics()` → reads `body_metrics.csv`
- `_load_daily_aggregates()` → reads `daily_aggregates.csv`
- `_load_recovery_data()` → reads `recovery_data.csv`

### Computation
- `_compute_sections(num)` → per-100m/km sections with pace, HR, elevation, SWOLF
- `_compute_trimp(w)` → TRIMP training load from HR + duration
- `_compute_recovery_timeline(workouts)` → CTL/ATL/TSB time series
- `_merge_nearby_workouts(workouts)` → merge same-discipline <10min gap

### Classification
- `_classify_type(type_str)` → 'run'|'bike'|'swim'|'strength'|'other'
- `_workout_distance(w)` → normalize to km (swim meters÷1000 if needed)
- `_hr_zone(hr)` → 'Z1'..'Z5'

### AI / Claude CLI
- `_call_agent(agent, prompt, session_name, ...)` → call Claude CLI agent
- `_call_claude_for_insight(prompt, tools)` → one-shot Claude call
- `_build_workout_prompt(w, plans)` → build prompt for workout insight
- `_build_cli_env()` → clean env for Claude CLI
- `_build_coach_preamble(uid, agent_name)` → dynamic athlete context (profile, events, memory)

## Sections Endpoint Detail

The `/api/workout/{num}/sections` endpoint returns per-discipline data:
- **Run/Bike**: `sections` array with per-km/segment pace, HR, elevation, power, cadence
- **Swim**: additional `swim_sets` (from `.events.json` HKWorkoutEventTypeSegment) and `swim_individual_laps` (from HKWorkoutEventTypeLap)
- **All**: `hr_zones` distribution and `hr_timeline` array

```json
{
  "sections": [...],          // per-100m/km computed sections
  "swim_sets": [...],         // swim only: Apple Watch Auto Sets
  "swim_individual_laps": [...], // swim only: per-25m laps
  "hr_zones": {...},
  "hr_timeline": [...]
}
```

Swim sets and laps are built from real Apple Watch events, NOT heuristics. Run/bike sections use standard CSV time-series data.
