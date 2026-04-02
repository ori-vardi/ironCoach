"""Claude CLI integration for agent execution and session management."""

import asyncio
import json
import os
import re
import shutil
import time
from pathlib import Path

from config import _SESSIONS_DIR, PROJECT_ROOT, logger, coach_session_id, normalize_model
import database as db


def _parse_stream_json(raw: str) -> tuple[str, dict | None]:
    """Parse stream-json NDJSON output. Returns (text, result_event)."""
    text_parts = []
    result_event = None
    for line in raw.strip().splitlines():
        try:
            event = json.loads(line)
        except json.JSONDecodeError:
            continue
        if event.get("type") == "assistant":
            for block in event.get("message", {}).get("content", []):
                if block.get("type") == "text":
                    text_parts.append(block["text"])
        elif event.get("type") == "result":
            result_event = event
            if not text_parts and event.get("result"):
                text_parts.append(event["result"])
    return "\n\n".join(text_parts), result_event


def _generate_session_title(user_message: str) -> str:
    """Generate a short fallback session title from the first user message."""
    text = user_message.strip()
    text = re.sub(r'\[Files?:.*?\]', '', text).strip()
    if not text:
        return ""
    first_line = text.split('\n')[0].strip()
    for sep in ['. ', '? ', '! ', '؟ ', '。']:
        if sep in first_line:
            first_line = first_line[:first_line.index(sep) + 1]
            break
    if len(first_line) > 55:
        cut = first_line[:55].rfind(' ')
        first_line = first_line[:cut] + '...' if cut > 20 else first_line[:55] + '...'
    return first_line


async def _generate_ai_title(session_id: str, user_message: str, assistant_text: str, user_id: int):
    """Generate a short session title from the user message (code-based, no LLM)."""
    msg = re.sub(r'\[Files?:.*?\]', '', user_message).strip()
    if not msg:
        return
    # Take first sentence or first N words
    first_line = msg.split('\n')[0].strip()
    # Remove "Let's discuss workout #..." boilerplate
    first_line = re.sub(r"^Let'?s discuss workout #\d+\s*\(([^)]+)\)\.?\s*", r"\1: ", first_line)
    # Truncate to ~40 chars at word boundary
    if len(first_line) > 40:
        first_line = first_line[:40].rsplit(' ', 1)[0] + '...'
    title = first_line.strip()
    if title and len(title) >= 3:
        try:
            conn = await db.get_db()
            try:
                await db.chat_set_title(conn, session_id, title, user_id=user_id)
                logger.debug(f"Code-title [{session_id[:8]}]: {title}")
            finally:
                await conn.close()
        except Exception as e:
            logger.warning(f"Code-title failed [{session_id[:8]}]: {e}")


async def _generate_subagent_title(session_uuid: str, context_key: str, user_id: int):
    """Generate a short title for a sub-agent session (code-based, no LLM)."""
    title = context_key[:40].split('\n')[0].strip()
    if len(title) > 40:
        title = title[:40].rsplit(' ', 1)[0] + '...'
    if title and len(title) >= 3:
        try:
            conn = await db.get_db()
            try:
                await db.chat_set_title(conn, session_uuid, title, user_id=user_id)
            finally:
                await conn.close()
        except Exception:
            pass


def _find_claude_cli() -> str | None:
    """Find the claude CLI binary.

    Checks CLAUDE_CLI env var first, then falls back to 'claude' in PATH.
    """
    # Env var override (e.g. CLAUDE_CLI=/usr/local/bin/ai)
    env_cli = os.environ.get("CLAUDE_CLI", "").strip()
    if env_cli:
        path = shutil.which(env_cli)
        if path:
            return path
        # Try as absolute path
        if os.path.isfile(env_cli) and os.access(env_cli, os.X_OK):
            return env_cli
    # Default: look for 'claude' in PATH
    path = shutil.which("claude")
    if path:
        return path
    return None


def _build_cli_env() -> dict:
    """Build env dict for CLI subprocesses.

    Strips CLAUDECODE so the CLI can run inside a Claude Code parent session.
    """
    return {k: v for k, v in os.environ.items() if k != "CLAUDECODE"}


# --- LLM preflight credential check ---
_preflight_last_ok: float = 0.0  # timestamp of last successful LLM interaction
_PREFLIGHT_TIMEOUT = 15  # seconds for the check CLI call

async def _llm_preflight_check() -> str | None:
    """Quick CLI call to verify credentials are valid. Returns error string or None if OK.

    Only runs if enough time has passed since the last successful LLM call
    (controlled by admin setting 'llm_preflight_hours', default 1 hour).
    """
    global _preflight_last_ok
    try:
        conn = await db.get_db()
        try:
            hours = float(await db.setting_get(conn, "llm_preflight_hours", "6"))
        finally:
            await conn.close()
    except Exception:
        hours = 1.0

    if hours <= 0:
        return None  # disabled

    elapsed = time.time() - _preflight_last_ok
    if elapsed < hours * 3600:
        return None  # recently verified

    cli = _find_claude_cli()
    if not cli:
        return "Claude CLI not found"

    env = _build_cli_env()
    try:
        proc = await asyncio.create_subprocess_exec(
            cli, "--bare", "-p", "hi", "--output-format", "stream-json",
            "--no-session-persistence", "--model", "haiku", "--max-turns", "1",
            stdout=asyncio.subprocess.PIPE, stderr=asyncio.subprocess.PIPE,
            cwd=str(PROJECT_ROOT), env=env,
        )
        stdout, stderr = await asyncio.wait_for(proc.communicate(), timeout=_PREFLIGHT_TIMEOUT)
        if proc.returncode == 0:
            _preflight_last_ok = time.time()
            return None  # credentials OK
        err = stderr.decode("utf-8", errors="replace").strip()
        # Check for auth-related errors
        auth_keywords = ("security token", "expired", "403", "authentication", "credentials", "not authorized")
        if any(kw in err.lower() for kw in auth_keywords):
            return f"Failed to authenticate. {err[:200]}"
        # Non-auth error — let the main call handle it, mark as OK to avoid blocking
        _preflight_last_ok = time.time()
        return None
    except asyncio.TimeoutError:
        # Timeout on preflight — don't block, let main call proceed
        return None
    except Exception as e:
        logger.warning(f"Preflight check error: {e}")
        return None


def _preflight_mark_ok():
    """Mark that a successful LLM interaction just happened."""
    global _preflight_last_ok
    _preflight_last_ok = time.time()


async def _get_model_override() -> str | None:
    """Read admin model override from DB settings. Returns short alias or None."""
    try:
        conn = await db.get_db()
        try:
            model_raw = await db.setting_get(conn, "agent_model", "")
        finally:
            await conn.close()
        return normalize_model(model_raw) or None
    except Exception:
        return None


async def _track_usage(result_json: dict, source: str, agent_name: str = "",
                       session_id: str = "", user_id: int = 1):
    """Extract usage/cost from Claude CLI JSON result and save to DB."""
    _preflight_mark_ok()  # successful LLM interaction
    try:
        usage = result_json.get("usage", {})
        cost = result_json.get("total_cost_usd", 0) or 0
        duration = result_json.get("duration_ms", 0) or 0
        input_tokens = usage.get("input_tokens", 0) or 0
        output_tokens = usage.get("output_tokens", 0) or 0
        cache_read = usage.get("cache_read_input_tokens", 0) or 0
        cache_creation = usage.get("cache_creation_input_tokens", 0) or 0
        # Detect model from modelUsage keys
        model_usage = result_json.get("modelUsage", {})
        model = next(iter(model_usage), "") if model_usage else ""
        conn = await db.get_db()
        try:
            await db.usage_track(conn, source, agent_name, session_id,
                                 input_tokens, output_tokens, cache_read, cache_creation,
                                 cost, model, duration, user_id)
        finally:
            await conn.close()
    except Exception as e:
        logger.warning(f"Failed to track usage: {e}")


async def _build_rotation_context(agent_name: str, user_id: int) -> str:
    """Build context prefix for a rotated specialist session.

    Injects last 5 workout insights for the agent's discipline so the
    coach remembers recent analyses after session rotation.
    """
    # Map agent to workout types
    agent_types = {
        "run-coach": ("Running", "Walking"),
        "swim-coach": ("Swimming",),
        "bike-coach": ("Cycling",),
    }
    workout_types = agent_types.get(agent_name)

    # Nutrition coach: inject last 5 days of meals
    if agent_name == "nutrition-coach":
        try:
            conn = await db.get_db()
            try:
                cursor = await conn.execute(
                    "SELECT date, meal_type, description, calories, protein_g, carbs_g, fat_g "
                    "FROM nutrition_log WHERE user_id = ? "
                    "ORDER BY date DESC, meal_time DESC LIMIT 15",
                    (user_id,)
                )
                meals = await cursor.fetchall()
            finally:
                await conn.close()
            if not meals:
                return ""
            lines = []
            for m in reversed(meals):
                lines.append(
                    f"- {m['date']} {m['meal_type']}: {m['description']} "
                    f"({m['calories']}cal, P{m['protein_g']}g C{m['carbs_g']}g F{m['fat_g']}g)"
                )
            return (
                "[SESSION ROTATED — here are recent meals for context. "
                "Continue coaching based on this history.]\n\n"
                + "\n".join(lines)
            )
        except Exception as e:
            logger.warning(f"Failed to build nutrition rotation context: {e}")
            return ""

    if not workout_types:
        # main-coach: no discipline-specific context (reads insights_summary.md)
        return ""

    try:
        conn = await db.get_db()
        try:
            placeholders = ",".join("?" for _ in workout_types)
            cursor = await conn.execute(
                f"SELECT workout_num, workout_date, workout_type, insight "
                f"FROM workout_insights "
                f"WHERE user_id = ? AND workout_type IN ({placeholders}) "
                f"ORDER BY workout_date DESC LIMIT 5",
                (user_id, *workout_types)
            )
            rows = await cursor.fetchall()
        finally:
            await conn.close()

        if not rows:
            return ""

        lines = []
        for r in reversed(rows):  # oldest first
            # Truncate each insight to ~300 chars to keep context small
            snippet = r["insight"][:300].rsplit(" ", 1)[0] + "..." if len(r["insight"]) > 300 else r["insight"]
            lines.append(f"### #{r['workout_num']} {r['workout_type']} ({r['workout_date']})\n{snippet}")

        return (
            "[SESSION ROTATED — here are your last 5 analyses for context. "
            "Continue coaching based on this history.]\n\n"
            + "\n\n".join(lines)
        )
    except Exception as e:
        logger.warning(f"Failed to build rotation context for {agent_name}: {e}")
        return ""


async def _summarize_chat_context(messages: list[dict], user_id: int = 1) -> str:
    """Use a cheap Claude call to summarize recent chat messages for rotation context.

    Returns a concise summary string, or falls back to raw messages on error.
    """
    if not messages:
        return ""

    raw_lines = []
    for msg in messages:
        role_label = "User" if msg["role"] == "user" else "Coach"
        text = msg["content"][:500]
        raw_lines.append(f"{role_label}: {text}")
    raw_text = "\n\n".join(raw_lines)

    cli = _find_claude_cli()
    if not cli:
        return raw_text

    # Preflight check (cached — near-zero cost if recently verified)
    preflight_err = await _llm_preflight_check()
    if preflight_err:
        logger.warning(f"Chat summary skipped — preflight failed: {preflight_err}")
        return raw_text

    prompt = (
        "Summarize this coaching conversation in 3-5 bullet points. "
        "Focus on: topics discussed, decisions made, advice given, and any pending questions. "
        "Be concise — this summary will be injected into a new session for context continuity.\n\n"
        f"--- CONVERSATION ---\n{raw_text}\n--- END ---"
    )
    try:
        env = _build_cli_env()
        cmd = [cli, "--bare", "-p", prompt, "--output-format", "stream-json", "--verbose",
               "--no-session-persistence", "--model", "haiku",
               "--max-turns", "1"]
        proc = await asyncio.create_subprocess_exec(
            *cmd, stdout=asyncio.subprocess.PIPE, stderr=asyncio.subprocess.PIPE,
            cwd=str(PROJECT_ROOT), env=env,
        )
        stdout, stderr = await asyncio.wait_for(proc.communicate(), timeout=30)
        if proc.returncode == 0:
            summary, result_event = _parse_stream_json(stdout.decode("utf-8", errors="replace"))
            summary = summary.strip()
            if summary:
                if result_event:
                    asyncio.create_task(_track_usage(result_event, "system", "chat-summary", "one-shot", user_id))
                logger.debug(f"Chat summary generated ({len(summary)} chars)")
                return summary
        logger.warning(f"Chat summary failed (rc={proc.returncode}), falling back to raw messages")
    except (asyncio.TimeoutError, Exception) as e:
        logger.warning(f"Chat summary error: {e}, falling back to raw messages")
    return raw_text


async def _call_agent(agent_name: str, prompt: str, session_name: str,
                      resume: bool = False, user_id: int = 1,
                      max_turns: int = 0) -> tuple[str | None, str]:
    """Call a Claude agent and return (result_text, session_uuid).

    Args:
        agent_name: Name of agent in .claude/agents/ (without .md)
        prompt: The prompt to send
        session_name: Human-readable name for deterministic UUID
        resume: If True, force --resume. Auto-detected if session file exists.
        max_turns: Max tool-use rounds (0 = unlimited). Use 1-3 for programmatic calls.
    """
    cli = _find_claude_cli()
    if not cli:
        logger.error("Claude CLI not found — cannot call agent")
        return None, ""

    session_uuid = coach_session_id(session_name)

    # Auto-detect: if session file exists on disk, resume it
    session_file = _SESSIONS_DIR / f"{session_uuid}.jsonl"
    session_exists = session_file.exists()

    # Read settings in a single DB connection (rotation threshold + model override)
    rotated_context = ""
    conn_r = await db.get_db()
    try:
        rotation_kb = int(await db.setting_get(conn_r, "session_rotation_kb", "800"))
        model_raw = await db.setting_get(conn_r, "agent_model", "")
    finally:
        await conn_r.close()
    model_override = normalize_model(model_raw) or ""
    rotation_bytes = rotation_kb * 1024
    session_size = session_file.stat().st_size if session_exists else 0
    if session_exists and session_size > rotation_bytes:
        old_size_kb = session_size / 1024
        ts = int(time.time())
        rotated = session_file.with_suffix(f".{ts}.jsonl.bak")
        while rotated.exists():
            ts += 1
            rotated = session_file.with_suffix(f".{ts}.jsonl.bak")
        logger.info(f"Agent [{agent_name}] rotated oversized session ({old_size_kb:.0f}KB, limit {rotation_kb}KB)")
        session_file.rename(rotated)
        cutoff = time.time() - 30 * 86400
        for old_bak in rotated.parent.glob("*.bak"):
            if old_bak.stat().st_mtime < cutoff:
                old_bak.unlink()
                logger.info(f"Removed old .bak file: {old_bak.name}")
        # Also rotate subagents dir if present
        subagents_dir = _SESSIONS_DIR / session_uuid
        if subagents_dir.exists():
            subagents_dir.rename(subagents_dir.with_suffix(f".{int(time.time())}.bak"))
        session_exists = False

        # Notify user about session rotation
        try:
            conn_n = await db.get_db()
            try:
                await db.notification_add(conn_n,
                    f"Session rotated: {agent_name}",
                    f"Agent session '{agent_name}' was {old_size_kb:.0f}KB (limit {rotation_kb}KB). "
                    f"Session reset with recent context injected.",
                    user_id=user_id)
            finally:
                await conn_n.close()
        except Exception as e:
            logger.warning(f"Failed to add rotation notification: {e}")

        # Inject last 5 workout insights for this discipline as context
        rotated_context = await _build_rotation_context(agent_name, user_id)

    should_resume = resume or session_exists

    # Prepend rotation context to prompt if we just rotated
    if rotated_context:
        prompt = rotated_context + "\n\n" + prompt

    return await _run_agent_cli(cli, agent_name, prompt, session_name,
                                session_uuid, should_resume, user_id,
                                max_turns=max_turns,
                                model_override=model_override)


async def _run_agent_cli(cli: str, agent_name: str, prompt: str, session_name: str,
                         session_uuid: str, should_resume: bool, user_id: int,
                         is_retry: bool = False, max_turns: int = 0,
                         model_override: str | None = None) -> tuple[str | None, str]:
    """Execute Claude CLI for an agent. Retries with fresh session on stale resume."""
    # Quick credential check before expensive CLI call
    preflight_err = await _llm_preflight_check()
    if preflight_err:
        logger.error(f"Agent [{agent_name}] preflight failed: {preflight_err}")
        return None, session_uuid

    cmd = [cli, "--bare", "--agent", agent_name]
    if should_resume:
        cmd += ["--resume", session_uuid]
    else:
        cmd += ["--session-id", session_uuid]
    cmd += ["-p", prompt, "--output-format", "stream-json", "--verbose"]
    if max_turns > 0:
        cmd += ["--max-turns", str(max_turns)]

    if model_override is None:
        model_override = await _get_model_override()
    if model_override:
        cmd += ["--model", model_override]

    env = _build_cli_env()
    prompt_preview = prompt[:120].replace("\n", " ")
    logger.debug(f"Agent call [{agent_name}] session={session_name}: {prompt_preview}...")
    start = time.time()

    _MAX_RETRIES = 3
    _RETRY_DELAY = 2
    _TRANSIENT_KEYWORDS = ("connection", "timeout", "rate limit", "503", "529", "overloaded")

    from services.task_tracker import _insight_active_procs

    try:
        for _attempt in range(_MAX_RETRIES):
            proc = await asyncio.create_subprocess_exec(
                *cmd,
                stdout=asyncio.subprocess.PIPE,
                stderr=asyncio.subprocess.PIPE,
                cwd=str(PROJECT_ROOT),
                env=env,
            )
            _insight_active_procs.add(proc)
            try:
                stdout, stderr = await proc.communicate()
            finally:
                _insight_active_procs.discard(proc)
            elapsed = time.time() - start

            # If batch was cancelled, don't retry — bail immediately
            import services.task_tracker as _tracker
            if _tracker._insight_batch_cancel:
                logger.info(f"Agent [{agent_name}] cancelled by user — not retrying")
                break
            if proc.returncode != 0 and _attempt < _MAX_RETRIES - 1:
                err_text = stderr.decode("utf-8", errors="replace") if stderr else ""
                if any(kw in err_text.lower() for kw in _TRANSIENT_KEYWORDS):
                    logger.warning(f"Agent [{agent_name}] transient error (attempt {_attempt+1}/{_MAX_RETRIES}): {err_text[:200]}")
                    await asyncio.sleep(_RETRY_DELAY * (_attempt + 1))
                    continue
            break

        if proc.returncode == 0:
            text, result_event = _parse_stream_json(stdout.decode("utf-8", errors="replace"))
            logger.info(f"Agent [{agent_name}] success ({elapsed:.1f}s, {len(text)} chars)")
            if result_event:
                asyncio.create_task(_track_usage(result_event, "agent", agent_name, session_uuid, user_id))

            # Save session record
            conn = await db.get_db()
            try:
                await db.session_save(conn, session_uuid, agent_name, session_name, user_id=user_id)
            finally:
                await conn.close()

            return text, session_uuid

        stderr_text = stderr.decode("utf-8", errors="replace").strip()
        logger.error(f"Agent [{agent_name}] failed (rc={proc.returncode}, {elapsed:.1f}s): {stderr_text[:300]}")

        # If resume failed with stale session, delete the JSONL and retry with fresh session
        if should_resume and not is_retry and "No conversation found" in stderr_text:
            logger.info(f"Agent [{agent_name}] stale session — removing JSONL and retrying with fresh session")
            session_file = _SESSIONS_DIR / f"{session_uuid}.jsonl"
            if session_file.exists():
                session_file.unlink()
            return await _run_agent_cli(cli, agent_name, prompt, session_name,
                                        session_uuid, False, user_id, is_retry=True,
                                        max_turns=max_turns, model_override=model_override)

        # If "session already in use" with --session-id, try resume instead
        if not should_resume and not is_retry and "already in use" in stderr_text:
            logger.info(f"Agent [{agent_name}] session exists — retrying with --resume")
            return await _run_agent_cli(cli, agent_name, prompt, session_name,
                                        session_uuid, True, user_id, is_retry=True,
                                        max_turns=max_turns, model_override=model_override)

        return None, session_uuid
    except Exception as e:
        elapsed = time.time() - start
        logger.error(f"Agent [{agent_name}] exception ({elapsed:.1f}s): {e}")
        return None, session_uuid
