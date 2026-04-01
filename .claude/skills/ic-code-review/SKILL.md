# Code Review Skill

Run a comprehensive code review across all domains: security, frontend, backend, and data pipeline.

## How to Use
Invoke `/ic-code-review` to run all 4 review agents in parallel. Each produces a structured report.

## Execution Plan

1. Launch 4 review agents in parallel:
   - `security-reviewer` — OWASP, auth, injection, secrets
   - `frontend-reviewer` — React patterns, performance, a11y, state
   - `backend-reviewer` — API design, error handling, validation, DB
   - `data-reviewer` — CSV pipeline, GPS logic, edge cases, data integrity

2. Collect all 4 reports

3. Produce a unified summary:
   - Total findings by severity (Critical / High / Medium / Low)
   - Cross-cutting concerns (issues that span multiple domains)
   - Top 5 priority fixes
   - Good practices found

## Agent Instructions

When executing this skill, launch all 4 agents using the Agent tool with `run_in_background: true` for parallelism:

```
Agent(subagent_type="security-reviewer", prompt="Run a full security audit of the IronCoach codebase. Read auth.py, server.py, database.py, and api.js. Produce the report format specified in your agent definition.")

Agent(subagent_type="frontend-reviewer", prompt="Run a full frontend code review of the IronCoach React dashboard. Read pages, components, context, utils, and CSS. Produce the report format specified in your agent definition.")

Agent(subagent_type="backend-reviewer", prompt="Run a full backend code review of the IronCoach FastAPI server. Read server.py (in chunks), database.py, auth.py. Produce the report format specified in your agent definition.")

Agent(subagent_type="data-reviewer", prompt="Run a full data pipeline review of the IronCoach project. Read scripts/export_to_csv.py, server.py GPS/sections logic, and sample CSV data. Produce the report format specified in your agent definition.")
```

After all complete, synthesize into a single unified report saved to `tmp/code-review-report.md`.
