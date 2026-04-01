# IronCoach — Backend

For root project info, unit conventions, and key patterns see `../CLAUDE.md`.
For full API map, DB schema, and helper functions load `backend-architecture` skill.

## Project Structure
```
backend/
├── server.py                        # FastAPI entry point (app, middleware, routers)
├── config.py                        # Shared constants, paths, logging
├── database.py                      # SQLite schema & CRUD (aiosqlite)
├── auth.py                          # JWT auth (HMAC-SHA256), password hashing
├── routes/                          # 16 API route modules + deps.py
├── services/                        # 7 business logic modules
├── data_processing/                 # 7 pure data modules (no FastAPI dependency)
├── tests/                           # pytest tests
└── data/                            # SQLite DB, uploads, logs (gitignored)
```

## Tests
```bash
python3 -m pytest tests/ -v
```
