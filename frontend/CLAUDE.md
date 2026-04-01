# IronCoach — Frontend

For root project info, unit conventions, and key patterns see `../CLAUDE.md`.
For full component map, CSS patterns, and storage reference load `frontend-architecture` skill.

## Project Structure
```
frontend/
├── src/
│   ├── App.jsx                      # Routes + auth gate
│   ├── api.js                       # API helper (401 -> reload)
│   ├── context/                     # AuthContext, AppContext, ChatContext
│   ├── components/                  # Layout, Chat, Modals, common UI
│   ├── pages/                       # 16 pages
│   ├── utils/                       # classifiers, formatters, useCountUp
│   ├── styles/theme.css             # Dark theme (Moonlight-inspired)
│   └── i18n/                        # EN + HE translations
└── dist/                            # Build output (gitignored)
```

## Build
```bash
npm run build
```
