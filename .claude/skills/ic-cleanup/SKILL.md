# Cleanup Skill

Review recently changed code for reuse opportunities, code quality, and efficiency — then auto-fix issues found.

## How to Use
Invoke `/ic-cleanup` after making changes. Agents review only recently modified files and apply fixes directly.

**Prerequisite**: Always **commit your changes first** before running cleanup. The agents edit files directly — a clean commit gives you a safe revert point if needed.

## Execution Plan

1. Identify recently changed files:
   ```
   git diff --name-only HEAD~3 -- '*.py' '*.jsx' '*.js' '*.css'
   ```

2. Launch 3 fixer agents in parallel on those files:
   - `code-simplifier:code-simplifier` with **Reuse** focus — find duplicated code, extract shared helpers, remove dead code
   - `code-simplifier:code-simplifier` with **Quality** focus — fix naming, simplify conditionals, remove unnecessary complexity
   - `code-simplifier:code-simplifier` with **Efficiency** focus — reduce re-renders, optimize loops, eliminate redundant API calls

3. Each agent reads the changed files, makes direct edits, and reports what it fixed.

4. After all complete, summarize total changes made.

## Agent Instructions

When executing this skill, first run the git diff to get the file list, then launch 3 agents in parallel using `run_in_background: true`:

```
# Get recently changed files
Bash("git diff --name-only HEAD~3 -- '*.py' '*.jsx' '*.js' '*.css'")

# Then launch 3 agents with the file list
Agent(subagent_type="code-simplifier:code-simplifier", name="reuse-fixer", prompt="
FOCUS: Code reuse and deduplication.
FILES: {file_list}

Review ONLY these recently changed files. For each file:
1. Read the file
2. Look for: duplicated logic (within file or across files), copy-pasted patterns that should be helpers, dead/unreachable code, unused imports
3. Fix issues directly with Edit tool
4. Report: file, what you changed, why

Do NOT touch files not in the list. Do NOT add comments or docstrings. Keep fixes minimal.")

Agent(subagent_type="code-simplifier:code-simplifier", name="quality-fixer", prompt="
FOCUS: Code quality and clarity.
FILES: {file_list}

Review ONLY these recently changed files. For each file:
1. Read the file
2. Look for: overly complex conditionals, inconsistent naming, unnecessary nesting, missing error handling at boundaries, magic numbers that should be constants
3. Fix issues directly with Edit tool
4. Report: file, what you changed, why

Do NOT touch files not in the list. Do NOT add comments or docstrings. Keep fixes minimal.")

Agent(subagent_type="code-simplifier:code-simplifier", name="efficiency-fixer", prompt="
FOCUS: Performance and efficiency.
FILES: {file_list}

Review ONLY these recently changed files. For each file:
1. Read the file
2. Look for: unnecessary re-renders (React), redundant API calls, N+1 patterns, missing memoization, inefficient loops, large objects recreated on every call
3. Fix issues directly with Edit tool
4. Report: file, what you changed, why

Do NOT touch files not in the list. Do NOT add comments or docstrings. Keep fixes minimal.")
```

After all 3 complete, **verify everything works**:

1. Run backend tests: `cd backend && python3 -m pytest tests/ -v`
2. Build frontend: `cd frontend && npm run build`
3. Fix any failures introduced by the agents (missing imports, syntax errors, broken tests)
4. Re-run tests until all pass

Then summarize: total files touched, total edits, what was improved, what agent errors were fixed. Do NOT commit.
