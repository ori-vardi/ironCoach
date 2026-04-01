# IronCoach — TODO

## Planned Features

### Chat Message Star/Pin
- Add a star/pin icon on each chat message (assistant responses)
- Clicking the star saves/bookmarks that message
- Starred messages persist per session (stored in DB)
- Add a new "Starred" icon in the chat header bar
- Clicking the Starred icon shows a list of all starred messages
- Each starred item is clickable — scrolls/jumps to that message in the chat
- Use case: quickly revisit important coaching advice without scrolling through long conversations

### Global Search
- **Chat search**: search within the current chat session messages (highlight matches, jump to result)
- **Cross-session search**: search a word/phrase across all CLI session JSONL files (backend grep, return session + matching lines)
- **Admin file search**: search across admin-managed files (agent definitions, session transcripts, logs)

### E2E Testing with LLM Mock Server
- Build a lightweight mock server that mimics Claude CLI `stream-json` output (deterministic, no API cost)
- Mock returns canned responses per agent type (run-coach, nutrition-coach, main-coach, etc.)
- Configurable: latency simulation, error injection (timeout, 500, rate limit), partial streaming
- Replace `CLAUDE_CLI` env var to point at mock binary/script during tests
- **E2E test suite** using the mock:
  - Import flow: upload export.xml → data processing → summary CSV created
  - Insight generation: post-import → select workouts → generate → verify DB has insights
  - Chat: send message → receive streamed response → verify chat history saved
  - Nutrition: add meal → verify regen triggered → verify insight updated
  - Meal analysis: one-shot call → verify parsed macros returned
  - Session rotation: fill session past threshold → verify rotation + context injection
  - Auth: login → JWT cookie set → protected routes accessible → logout
  - Multi-user: user A data isolated from user B
- No real API calls — runs fast, free, deterministic, CI-friendly

### Versioning & Release Workflow
- Add a `VERSION` file or `version` field in a central config
- Git tags for each release (e.g., `v0.1.0`)
- Changelog file (`CHANGELOG.md`) — notable changes per version
- Bump version in README.md and FEATURES.md headers on release
- Consider a release script: bump version, update changelog, tag, push

### Split project-patterns Skill into Domain Skills
- Current `project-patterns` skill is one big file (101 lines, 53 patterns) covering everything
- Split into focused domain skills that agents/conversations can load selectively:
  - `/fe-patterns` — UI patterns, React conventions, RTL, CSS, components (patterns 7-12, 22, 25, 28, 47)
  - `/be-patterns` — API, auth, DB, data processing, storage (patterns 13, 32, 37-38, 50-53)
  - `/ai-patterns` — LLM calls, sessions, tokens, cost control, agents (patterns 2-3, 15-21, 24, 26-27, 29-31, 34-35, 39-49)
  - `/security-patterns` — agent action system, JWT, file restrictions, OWASP (pattern 13 + expand)
  - `/nutrition-patterns` — meal tracking, regen, fueling window, notes extraction (patterns 14, 23, 33, 36, 49)
- Keep root `project-patterns` as a lean index that references the domain skills
- Update agent definitions to reference relevant domain skill instead of full patterns
- Update `CLAUDE.md` reference from single skill to domain list
- Benefit: agents only load patterns relevant to their domain → fewer tokens per session

### Multiple Chat Sessions for Specialist Agents
- Currently specialist agents (run/swim/bike/nutrition-coach) use a single deterministic session ID (tied to insights)
- Goal: allow users to start new chat sessions with any specialist while keeping insight sessions separate
- Chat sessions generate unique UUIDs (like dev agents), insight generation keeps deterministic IDs
- Sessions panel shows multiple sessions per specialist with "New Session" option

### Training Plan: Select All / Bulk Actions
- Add "Select All" checkbox in the training plan table header
- Individual row checkboxes for multi-select
- Bulk actions toolbar: Delete selected, Mark completed, Mark incomplete
- Use case: quickly clear out a week of planned sessions or bulk-complete past entries

### Nutrition Targets: Python-Based Calculation (No LLM)
- Compute daily targets from actual data without AI: BMR + TDEE multiplier + phase adjustment
- Data: body_metrics.csv (weight, body fat), training volume, user profile, race phase
- Protein 1.4-2.0 g/kg lean mass, carbs 5-10 g/kg by volume, fat as remainder, water 35 ml/kg + training
- New endpoint `GET /api/nutrition/targets/calculated`
- Frontend shows calculated as default, "Smart Suggest (AI)" as upgrade option

---

## Explore: Auto-Sync Apple Health Data

Currently data import requires manually exporting from Apple Health on iPhone and uploading the zip. Best option:

**iPhone companion app (HealthKit API)** — full HealthKit access, background sync, no Watch complexity. A minimal Swift app with:
1. `HKObserverQuery` for new workout notifications
2. `HKAnchoredObjectQuery` for incremental data fetch
3. REST POST to IronCoach server
4. No UI needed beyond a settings screen (server URL, auth token)

Other options considered: Watch app (only adds value for live coaching, not data), iOS Shortcuts (limited, semi-manual), Web HealthKit (doesn't exist).

---

---

## Refactoring

These items require touching 10+ files each and should be done as focused sessions.

| Item | Scope | Risk | Description |
|:-----|:------|:-----|:------------|
| **DB Connection Injection** | 15 route modules | High | Replace manual `get_db(); try/finally: close()` (100+ occurrences) with FastAPI `Depends` |
| **Structured Logging** | All Python files | Medium | Switch f-string logs to structured format with `extra={...}` |
| **Generic CRUD Router** | 4 route modules | Medium | Extract shared CRUD helper from plan/events/nutrition/memory routes (~500 lines) |
| **OpenAPI Documentation** | 15 route modules | Low | Add `summary`, `description`, `response_model` to all ~80 endpoints |
| **Metrics / Observability** | server.py | Low | Add `prometheus-fastapi-instrumentator` for request metrics |
| **Connection Pooling** | database.py + callers | High | aiosqlite creates new connection per call — add pooling or shared connection |
| **Virtual Scrolling** | Multiple pages | Medium | Add `react-window` for long notification/workout lists |

---

## Won't Fix (by design)

| ID | Finding | Reason |
|----|---------|--------|
| SEC-017 | No CORS middleware | Localhost single-origin app |
| SEC-018 | No CSRF tokens | SameSite=lax + rate limiting covers it |
| SEC-026 | SQLite WAL file permissions | Same umask as DB |
| SEC-009 | osascript injection risk | No user input in calls |
| SEC-014 | CSV formula injection | Data from Apple Health, not user input |
| SEC-025 | User enumeration via timing | Localhost app, rate limited |
| FE-020 | `_reloading` flag never resets | Module reinits on page load |
| FE-021 | analyzeMeal empty check | Logic is correct (allows image-only) |
| DP-004/005 | Unit inconsistency (swim=m, elev=cm) | Would break existing CSV data, handled by _workout_distance() |
| DP-007 | Workout number gaps after merge | Cosmetic, no data loss |
| DP-010 | Apple source name fragility | Current list is complete |
| DP-011 | Step count dedup may under-count | Conservative approach avoids inflation |
| DP-017 | Swim stroke codes hardcoded | Apple hasn't changed since watchOS 3 |
| DP-020 | Merged workout CSV integrity | Logging added for missing files |

*Won't Fix items from 2026-03-28 code review.*
