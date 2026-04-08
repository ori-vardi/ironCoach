# Project Patterns & Decisions

Complete list of architectural decisions and implementation patterns for the IronCoach dashboard.

## Core Principles (1-6)
1. **No heuristic data** — NEVER use heuristic/estimated data for storage or display. Only real sensor data.
2. **Python-local analysis** — all chart data computed in Python, not via Claude (saves tokens)
3. **Direct agent calls for programmatic tasks** — `_call_agent()` from Python, NOT through main-coach. Lower latency, lower cost, reliable parsing.
4. **Agent code deduplication** — agents must use server API endpoints for business logic (single source of truth)
5. **No Docker** — SQLite file database, localhost only
6. **Honest coach** — prompt explicitly instructs against sugarcoating

## UI Patterns (7-12)
7. **Tables above graphs** on discipline pages. All tables sorted newest-first.
8. **Date format DD/MM** — UI uses `en-GB` locale
9. **RTL support** — CSS logical properties, `dir="auto"` on user/AI text, `detectDir()` on inputs
10. **401 reload loop guard** — `api.js` `_reloading` flag + `AppContext` auth guard
11. **SQLite integer booleans in React** — always use `!!value` or ternary, never `{0 && <JSX>}`
12. **No native alerts** — use `setError(e.message)`, never `alert()`

## Auth & API (13)
13. **Agent Action System** — agents output `[ACTION:name {...}]` blocks in responses; chat handler intercepts, executes server-side with user's auth context, strips from output.

## Nutrition & Insights (14, 23, 33, 36, 39, 49)
14. **Nutrition auto-regeneration** — meal relevance check (4h pre / 2h post window) before re-generating insights
23. **Post-import context flow** — after import, modal shows brick sessions, merge candidates (exact type match only), then workout selection with start/end times (timezone-aware via `meta_TimeZone`). >10 workouts = none selected + cost confirmation step. Dismissable with reopen support.
33. **Post-import nutrition check** — `/api/import` returns `dates_with_nutrition`. PostImportModal shows blue banner for dates missing nutrition data.
36. **Insight nutrition check** — InsightsPage checks if selected workout date has nutrition data. Blue banner warns to log meals first for better insights.
39. **Batch insight preamble injection** — `_build_coach_preamble()` called once before batch queues, injected as `[ATHLETE CONTEXT]` into all discipline, nutrition, and synthesis prompts. Same as single insight and chat paths.
49. **Nutrition from athlete notes** — `_extract_and_save_nutrition_from_notes()` runs before insight generation (both single and batch). Uses Claude to detect food in user notes, checks for duplicates (same meal_type + description), saves new meals to `nutrition_log`. The insight then sees the newly saved nutrition data.

## Chat & Streaming (15-16, 30-31)
15. **Parallel coach streaming** — per-session state (Maps/Sets), `asyncio.Lock` for concurrent WS sends
16. **Chat streaming preservation** — `sendBeacon` saves in-progress text on `beforeunload`
30. **Chat summary mode** — admin setting `chat_summary_mode`: "ai" (default) uses Haiku to summarize last 10 messages on rotation, "raw" sends them as-is. Setting in Admin > Sessions > Settings.
31. **WS early error detection** — chat WS reads stderr in background task. Pattern-matches fatal errors ("API Error:", "token expired", "/login", "rate limit") and kills process immediately. No arbitrary timeout — process runs as long as needed for thinking.

## Token Usage & Sessions (17-21, 24, 45-48)
17. **Token usage is free to check** — reads local SQLite, never calls Claude API. Updates on LLM events only.
18. **All LLM calls track user_id** — `_call_agent()`, `_call_claude_for_insight()`, chat WS all pass correct user_id. No defaults to user_id=1.
19. **Session rotation** — both `_call_agent()` and chat WS rotate JSONL files at configurable threshold (default 800KB, `session_rotation_kb` setting). Prevents unbounded input token cost growth.
20. **One-shot LLM calls use `--no-session-persistence`** — meal analysis creates no session file. Cheapest path.
21. **No FYI LLM calls** — removed wasted fire-and-forget calls. Main-coach gets last 2 weeks of insights from DB directly (no file I/O).
24. **Session rotation notifications** — both `_call_agent()` and chat WS create notification events when sessions are rotated, showing agent name and old size.
45. **Token usage model tracking** — `model` column in `token_usage` enables per-model cost breakdown. Per Agent and Daily tabs group by agent+model. ModelBadge marks active model with `*`.
46. **Daily drill-down** — clicking a daily row in Token Usage expands to show per-agent+model cost breakdown for that date. Uses `/api/usage/daily-agents` endpoint.
48. **Configurable session rotation** — `session_rotation_kb` in `app_settings` (Admin → Sessions → Settings). Default 800KB. Both `_call_agent()` and chat WS read from DB.

## UI Components (22, 25, 28, 47)
22. **Dashed border = tooltip** — elements with tooltips use `border-style: dashed` so users know to hover for more info
25. **Insights page unified tabs** — single card with 3 tabs: Single Workout (with date filter), All Pending (batch), Period Insights (date range presets)
28. **Phase bar today arrow** — Training Plan phase bar shows Today marker at current date position. Info row below with dashed-border phase labels + tooltips.
47. **InfoTip portal rendering** — `createPortal(popup, document.body)` prevents z-index stacking context issues. Click-to-pin and hover-over-popup for scrollable content.

## Memory & Agents (26-27, 29, 34-35, 40-44)
26. **Coach Memory per-user** — `coach_memory` table, CRUD API at `/api/memory`. Accessible from user avatar menu. Injected into all coaching prompts via `_build_coach_preamble()`. Agents read/write via ACTION blocks.
27. **Agent model override** — admin sets model in Admin > Sessions > Settings. `--model` flag appended per-invocation to `_run_agent_cli()` and chat WS. Does NOT change global CLI default.
29. **Table formatting in agents** — all coaching agent `.md` files include formatting section: always align columns with consistent padding and proper header separators.
34. **CLI binary auto-detection** — `_find_claude_cli()` tries `claude` then `ai` in PATH. Users can create an `ai` wrapper script for custom auth (e.g. AWS Bedrock).
35. **Environment config** — `.env` file in `backend/` loaded by `_load_dotenv()`. `.env.example` documents available vars. `CLAUDE_CLI` env var overrides CLI binary name.
40. **Admin tab order** — Settings (first, default), Users, Agent Definitions, Sessions, CLI Sessions, Logs. Agent Definitions grouped: Coaching, Development, Review.
41. **Developer Chat** — admin-only, terminal icon in topbar. Uses dev agents including main-dev orchestrator. No session rotation, no coach preamble, full Edit/Write/Glob tools. `mode` field on WS messages and `chat_session_titles` table.
42. **Agent Memory** — `agent_memory` table (user_id, agent_type, content). CRUD API at `/api/memory/agent/{agent_type}`. Injected into coaching prompts via `_build_coach_preamble(agent_name=...)` and into dev prompts via `[AGENT MEMORY]` block. UI in chat Sessions panel (collapsible per-agent section).
43. **Chat mode** — `chatMode` state in ChatContext ("coach"/"dev"), persisted to `sessionStorage['chat-mode']`. Sessions API filters by `?mode=`. Mode passed in WS payload.
44. **Lead Dev agent** — `main-dev` orchestrator delegates to all dev agents (frontend-dev, backend-dev, data-pipeline, code-simplifier, reviewers). Like main-coach for the coaching side.

## Data & Storage (32, 37-38, 50-53)
32. **Manual workout merges** — `manual_merges` in `app_settings` (JSON array of [a,b] pairs). `_load_manual_merges()` reads sync from SQLite. `_merge_nearby_workouts()` applies both auto (<10 min) and manual merges.
37. **Draft persistence** — Nutrition `mealText` persisted in `sessionStorage` (key `nutrition-draft`). Chat input already uses `sessionStorage` (key `chat-draft`). Cleared on successful submit.
38. **Graceful without Claude CLI** — All non-AI features (charts, tables, merges, imports) work without Claude CLI. AI actions (chat, insights, meal analysis) check `_find_claude_cli()` and fail gracefully. Merge/brick actions treat insight regeneration as best-effort (`catch {}` on frontend).
50. **Hidden workouts** — `hidden_workouts` in `app_settings` (JSON array). `_filter_hidden()` on user-facing endpoints only. AllWorkoutsPage "Show hidden" toggle with `?show_hidden=true` param. Action bar shows at 1+ selected (Hide) or 2+ (Merge/Brick). Dropdown menu when multiple actions available. Hidden rows shown at 50% opacity with tag.
51. **Source deduplication** — `_deduplicate_records_by_source()` in `export_to_csv.py` drops non-Watch records (distance + StepCount) when Watch data exists for same record type. Prevents iPhone distance inflation (~60%) and step count duplication in per-workout CSVs. Summary CSV unaffected (Apple Health deduplicates at aggregation).
52. **Apple Fitness exact match** — all displayed values match Apple Fitness app exactly: pace from distance/time (not avg speed), `Math.floor` for power and calories, `Math.round` for HR, distance truncated to 2 decimals, duration shows seconds. Per-split metrics: cadence = accumulated steps / split time, HR = carry-forward averaging, `in_split_window` guard prevents data outside Apple split boundaries. Partial last split pace adjusted for actual distance.
53. **Segment chain detection** — `_extract_segment_chains()` in `export_to_csv.py` groups Apple `WorkoutEventTypeSegment` entries into chains (km, mile, 5km). Tie-breaking by duration compatibility prevents 5km segments (~14 min) from contaminating km chain (~2.7 min avg).

## Token Usage Tracking (detailed)
- `token_usage` table tracks every Claude CLI call: source, agent, tokens, cost, model
- Tracked in ALL LLM paths: chat, agents, insights, nutrition analyze
- All calls pass correct `user_id` — no leaks to default user
- Per-user. Admin sees all users' usage in Users table (Cost, Calls, Tokens + total row)
- Frontend `TokenUsage` component in topbar: `$ $0.12 | 45.2Kt`. Updates on LLM events only (no polling)
- Tabs: Summary, Per Agent (grouped by agent+model), Daily (click to expand per-agent breakdown), Per Model
- Model badges show short model name with `*` for currently active model
- Cache columns: Cache Read ($0.30/MTok), Cache Write ($3.75/MTok), Base ($3/MTok) with InfoTip explanations
- `GET /api/usage` (own), `GET /api/admin/usage` (all users), `GET /api/usage/by-model`, `GET /api/usage/daily-agents`

## CLI Session Cost Control (detailed)
- **Session = JSONL file**. Each `--resume` sends ENTIRE file as input tokens. Bigger file = more cost.
- **Session rotation** (default 800KB, configurable via `session_rotation_kb` in Admin > Sessions) in `_call_agent()` AND chat WebSocket: renames JSONL -> `.bak`, next call creates fresh file. Rotation creates a notification event.
- **Rotation context injection** (unified across `_call_agent()` and chat WS via `_build_rotation_context()`):
  - **run/swim/bike-coach**: last 5 workout insights for that discipline (from `workout_insights` DB)
  - **nutrition-coach**: last 15 meals (from `nutrition_log` DB)
  - **main-coach**: last 2 weeks of workout insights injected from DB into prompt
  - **Chat additionally**: configurable via `chat_summary_mode` setting — "ai" (default) uses Haiku to summarize last 10 messages (~$0.001), "raw" sends last 10 messages as-is (free)
- **`--max-turns`**: specialists=3, synthesis=1, chat=unlimited
- **`--no-session-persistence`**: used for one-shot calls (meal analysis) — no file, cheapest path
- **Batch insight rotation**: discipline coaches every 5 workouts (new UUID suffix `-2`, `-3`), nutrition daily (`-d2`, `-d3`)
- **Session naming**: `{agent}-user{user_id}` — per-user isolation. Chat main-coach: `main-coach-{frontend_session_id}`
- **Shared sessions**: chat + insight share same JSONL for specialists (e.g. `run-coach-user1`). Coach remembers recent analyses in chat.
- **Title generation**: code-based (no LLM). No FYI fire-and-forget calls.
