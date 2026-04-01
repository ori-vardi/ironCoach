# Frontend Architecture Skill

## Component Map

### Pages (`frontend/src/pages/`)
| Page | Route | Data Source | Key Features |
|---|---|---|---|
| OverviewPage | `/` | `/api/summary`, `/api/stats/weekly` | KPI cards, weekly charts, recent workouts |
| RunningPage | `/running` | `/api/workouts/by-type/run` | Table + pace/HR/distance charts |
| CyclingPage | `/cycling` | `/api/workouts/by-type/bike` | Table + speed/power/elevation charts |
| SwimmingPage | `/swimming` | `/api/workouts/by-type/swim` | Table + pace/distance/stroke charts |
| AllWorkoutsPage | `/workouts` | `/api/summary` | Filterable table, all workout types |
| InsightsPage | `/insights` | `/api/insights/*` | AI insights per workout + general |
| TrainingPlanPage | `/plan` | `/api/plan` | CRUD training plan |
| NutritionPage | `/nutrition` | `/api/nutrition/*` | Meal log + AI analysis + energy balance |
| BodyMetricsPage | `/body` | `/api/body-metrics` | Weight/fat/BMI/muscle charts + table |
| RecoveryPage | `/recovery` | `/api/recovery` | TSB, TRIMP, sleep, HRV, RHR |
| BricksPage | `/bricks` | `/api/bricks` | Brick workout sessions |
| RacePage | `/race` | `/api/race` | Race info editor |
| SessionsPage | `/sessions` | `/api/sessions` | Coaching agent sessions viewer |
| SettingsPage | `/settings` | `/api/sessions`, `/api/agents` | Athlete profile, agents & skills |
| AdminPage | `/admin` | `/api/admin/*` | Users, Sessions (Chat+Agent), Agent Defs, CLI Sessions, Logs |

### Components (`frontend/src/components/`)
| Component | Purpose |
|---|---|
| Layout.jsx | Sidebar nav + main content + chat panel |
| WorkoutDetailModal.jsx | Workout detail: Overview, Splits & Zones, Analysis tabs |
| ImportModal.jsx | Apple Health data import |
| PostImportModal.jsx | Post-import: brick sessions, merge candidates, insight selection, reopen support |
| NotificationBell.jsx | LLM task notifications + history |
| chat/ChatPanel.jsx | WebSocket chat with IronCoach |

### Common Components (`components/common/`)
| Component | Props | Usage |
|---|---|---|
| KpiCard | `value, label, sublabel, info, style` | Stat card with optional InfoTip |
| Modal | `title, onClose, wide, children` | Centered modal overlay |
| Badge | `type, text` | Colored discipline badge |
| LoadingSpinner | (none) | Centered spinner |
| InfoTip | `text` | (i) icon with markdown tooltip |
| DataTable | `columns, data, onRowClick` | Generic sortable table |
| ConfirmDialog | `message, onConfirm, onCancel` | Confirmation modal |

## State Management

### AppContext (`context/AppContext.jsx`)
```jsx
const { dateFrom, dateTo, workouts, setWorkouts } = useApp()
```
- `dateFrom` — persisted in localStorage
- `dateTo` — always resets to today on load
- `workouts` — cached summary data

### ChatContext (`context/ChatContext.jsx`)
```jsx
const { setChatOpen, switchSession, messages, sendMessage } = useChat()
```

## i18n Pattern
```jsx
import { useI18n } from '../i18n/I18nContext'
const { t, lang } = useI18n()
// t('key') returns translated string
// lang is 'en' or 'he'
```

All translations in `frontend/src/i18n/translations.js`:
```js
export const translations = {
  en: { key: 'English text', ... },
  he: { key: 'Hebrew text', ... },
}
```

## CSS Architecture (theme.css)

Single file: `frontend/src/styles/theme.css`

### Layout structure
```
.app-layout
  .sidebar (fixed, 220px wide)
  .main-content (flex, scrollable)
  .chat-panel (fixed right, 380px wide, collapsible)
```

### Card patterns
```css
.card       { background: var(--bg-2); border: 1px solid var(--border); border-radius: var(--radius); padding: 16px; }
.card-grid  { display: grid; grid-template-columns: repeat(auto-fit, minmax(180px, 1fr)); gap: 12px; }
```

### Table patterns
Always use real `<table>` elements with `data-table` class:
```css
.data-table         { width: 100%; border-collapse: collapse; }
.data-table th      { text-align: start; padding: 8px 12px; font-size: 11px; color: var(--text-dim); border-bottom: 1px solid var(--border); }
.data-table td      { padding: 8px 12px; border-bottom: 1px solid var(--border); }
.data-table tr.clickable:hover { background: var(--bg-3); cursor: pointer; }
```

### Responsive chart grid
```css
.chart-grid-2col { display: grid; grid-template-columns: repeat(auto-fit, minmax(440px, 1fr)); gap: 16px; }
```

### RTL support
- Use logical properties: `margin-inline-start`, `padding-inline-end`, `inset-inline-end`
- Numbers/data stay LTR: `direction: ltr; unicode-bidi: isolate`
- Never use physical properties (`margin-left`, `margin-right`) for layout

## Notification Pattern
```jsx
import { notifyLlmStart, notifyLlmEnd } from './NotificationBell'
// Start: notifyLlmStart('unique-id', 'Task Label', '/target-route')
// End:   notifyLlmEnd('unique-id')
```

## Plotly Chart Defaults
```js
// Always spread PLOTLY_LAYOUT and use PLOTLY_CONFIG
layout={{ ...PLOTLY_LAYOUT, yaxis: { ...PLOTLY_LAYOUT.yaxis, title: 'unit' } }}
config={PLOTLY_CONFIG}
```
- Background: transparent (cards provide background)
- Grid color: #313450
- Font: #c8d3f5, size 11
- Always add `useResizeHandler` and `style={{ width: '100%', height: N }}`

## Key Formatters (utils/formatters.js)
| Function | Input | Output |
|---|---|---|
| fmtDur(min) | 75 | "1h 15m" |
| fmtDist(km) | 5.123 | "5.12" (or "10.1" if >=10) |
| fmtDateShort(iso) | "2026-03-10T..." | "10 Mar" |
| fmtTime(iso) | "2026-03-10T09:08..." | "09:08" |
| fmtPace(kmh) | 12 | "5:00/km" |
| safef(val, default) | "3.5" | 3.5 (safe float parse) |

## Key Classifiers (utils/classifiers.js)
| Function | Input | Output |
|---|---|---|
| hrZone(hr) | 145 | "Z3" |
| classifyType(type) | "Running" | "run" |
| recoveryColor(score) | 80 | "#c3e88d" |
| trainingPhase(date, events) | — | { phase, progress } |

## Frontend Storage
- **localStorage**: `lang` (en/he), `dateFrom` (date range start), `insightLang` (insight language), `chatWidth` (chat panel width), `navOrder` (sidebar page order), `auth_sessions` (multi-user session tokens), `nutrition_suggestion_{uid}` (per-user AI-suggested targets)
- **sessionStorage**: `chat-session-id`, `chat-session-agent`, `chat-mode` (coach/dev), `chat-open` (panel state), `chat-draft` (unsent message), `nutrition-draft` (unsent meal text)
- **Cookies**: `token` (JWT httpOnly, 72h expiry, set by server)
- No IndexedDB or Cache Storage used
