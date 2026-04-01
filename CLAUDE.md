# IronCoach — Triathlon Training Dashboard

## Project Structure
```
ironCoach/
├── CLAUDE.md                    # This file
├── scripts/export_to_csv.py     # Data pipeline: XML -> CSVs
├── training_data/users/{uid}/   # Per-user CSVs, splits, events
├── docs/                        # FEATURES.md
├── .claude/agents/              # 13 Claude CLI agent definitions
├── .claude/skills/              # 11 reusable Claude Code skills
├── backend/                     # FastAPI server (has its own CLAUDE.md)
└── frontend/                    # React dashboard (has its own CLAUDE.md)
```

## How to Run
```bash
./setup.sh           # One-time: install deps + build frontend
./start.sh           # Start server → http://localhost:8000
./start.sh --build   # Build frontend + start server
```

## Tests
```bash
cd backend && python3 -m pytest tests/ -v
```

## Code Review
```
/ic-cleanup             # Quick review + auto-fix (commit first!)
/ic-code-review        # Full audit (4 agents)
```

## Skills Reference
- **Data model**: `data-model` — CSV schemas, unit conventions, discipline classification
- **Patterns**: `project-patterns` — all architectural decisions and implementation rules
- **Backend**: `backend-architecture` — full API map, DB schema, helper functions
- **Frontend**: `frontend-architecture` — component map, CSS patterns, storage, formatters
- **Features**: `docs/FEATURES.md` — detailed feature documentation

## Documentation Updates
After any significant change, update relevant docs:
1. `CLAUDE.md` files — architecture overview
2. `docs/FEATURES.md` — user-facing features
3. `README.md` — setup, config, cost, tech stack
4. `.claude/agents/*.md` — agent behavior or tools
