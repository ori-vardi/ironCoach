"""
Chat WebSocket handler and related functions.
Extracted from server.py for better code organization.
"""

import asyncio
import json
import time
from pathlib import Path

from fastapi import APIRouter, WebSocket, WebSocketDisconnect

MAX_FILE_CHARS = 50000
CHAT_HISTORY_LIMIT = 10
CHAT_CONTENT_PREVIEW_CHARS = 500

SUBPROCESS_LINE_BUFFER = 10 * 1024 * 1024  # 10MB
CHAT_STALE_TIMEOUT_SEC = 7200  # 2 hours

_BACKEND_DIR = Path(__file__).parent.parent
_UPLOAD_DIR = (_BACKEND_DIR / "data" / "uploads").resolve()
_TRAINING_DATA_DIR = (_BACKEND_DIR.parent / "training_data").resolve()
_ALLOWED_PARENTS = (_UPLOAD_DIR, _TRAINING_DATA_DIR)

IMAGE_SUFFIXES = frozenset({".jpg", ".jpeg", ".png", ".gif", ".webp", ".bmp", ".heic"})
TEXT_SUFFIXES = frozenset({
    ".csv", ".txt", ".md", ".json", ".py", ".js", ".html", ".css",
    ".xml", ".yaml", ".yml", ".toml", ".ini", ".cfg", ".log", ".tsv",
    ".sh", ".bash", ".zsh", ".sql", ".gpx", ".env",
})

import database as db
from config import (
    _SESSIONS_DIR, PROJECT_ROOT,
    logger, coach_session_id,
)
from services.task_tracker import (
    _chat_procs, _chat_streaming, _chat_ws_registry, _chat_bg_tasks,
)
from services.claude_cli import (
    _find_claude_cli, _build_cli_env, _track_usage, _get_model_override,
    _build_rotation_context, _summarize_chat_context,
    _generate_session_title, _generate_ai_title,
    _llm_preflight_check,
)
from services.coach_preamble import _build_coach_preamble
from services.agent_actions import extract_actions, execute_action, FOLLOWUP_ACTIONS
from auth import decode_jwt


def _detect_lang(text: str) -> str:
    """Detect language from text: 'he' if first strong letter is Hebrew, else 'en'."""
    if not text:
        return "en"

    for ch in text:
        # Hebrew Unicode ranges
        if "\u0590" <= ch <= "\u07FF" or "\uFB1D" <= ch <= "\uFDFF":
            return "he"
        if ch.isalpha():
            return "en"
    return "en"


# ── File Reading Utilities ───────────────────────────────────────────────────

def _truncate(content: str, max_chars: int) -> str:
    if len(content) <= max_chars:
        return content
    return content[:max_chars] + f"\n... [truncated — {len(content)} chars, showing first {max_chars}]"


def _read_docx(file_path: Path) -> str:
    """Extract text from a .docx file using built-in zipfile + XML parsing."""
    import zipfile
    import xml.etree.ElementTree as ET

    text_parts = []
    with zipfile.ZipFile(file_path, "r") as z:
        # Main document body
        for xml_name in ("word/document.xml", "word/document2.xml"):
            if xml_name in z.namelist():
                tree = ET.parse(z.open(xml_name))
                root = tree.getroot()
                ns = {"w": "http://schemas.openxmlformats.org/wordprocessingml/2006/main"}
                for para in root.iter("{http://schemas.openxmlformats.org/wordprocessingml/2006/main}p"):
                    runs = para.findall(".//w:t", ns)
                    line = "".join(r.text or "" for r in runs)
                    text_parts.append(line)
    return "\n".join(text_parts)


def _read_pdf(file_path: Path) -> str:
    """Extract text from a PDF — best-effort using pdfminer if available."""
    try:
        from pdfminer.high_level import extract_text
        return extract_text(str(file_path))
    except ImportError:
        return "[PDF file — install pdfminer.six to extract text: pip install pdfminer.six]"
    except Exception as e:
        return f"[Error reading PDF: {e}]"


def _read_attached_file(file_path: str, max_chars: int = MAX_FILE_CHARS) -> tuple[str, str]:
    """Read an attached file and return (filename, content_or_notice).

    Supports text files, .docx, .pdf, and images (base64-encoded).
    """
    p = Path(file_path)
    name = p.name
    # Restrict file access to safe directories (resolve symlinks)
    resolved = p.resolve(strict=False)
    if not any(resolved.is_relative_to(ap) for ap in _ALLOWED_PARENTS):
        logger.warning(f"Blocked file access outside allowed dirs: {file_path}")
        return name, f"[Access denied: file outside allowed directories]"
    if not p.exists():
        logger.warning(f"Attached file not found: {file_path}")
        return name, f"[File not found: {file_path}]"

    suffix = p.suffix.lower()

    if suffix in IMAGE_SUFFIXES:
        size_kb = p.stat().st_size / 1024
        logger.debug(f"Image attached: {name} ({size_kb:.1f} KB) at {file_path}")
        return name, f"[IMAGE FILE — read this file to see the image: {file_path}]"

    # Word documents
    if suffix in (".docx", ".doc"):
        try:
            content = _read_docx(p)
            if not content.strip():
                return name, f"[Empty or unreadable .docx file: {name}]"
            content = _truncate(content, max_chars)
            logger.debug(f"Read .docx file: {name} ({len(content)} chars)")
            return name, content
        except Exception as e:
            logger.error(f"Error reading .docx {name}: {e}")
            return name, f"[Error reading .docx: {e}]"

    # PDF
    if suffix == ".pdf":
        try:
            content = _truncate(_read_pdf(p), max_chars)
            logger.debug(f"Read PDF file: {name} ({len(content)} chars)")
            return name, content
        except Exception as e:
            logger.error(f"Error reading PDF {name}: {e}")
            return name, f"[Error reading PDF: {e}]"

    if suffix in TEXT_SUFFIXES:
        try:
            content = _truncate(p.read_text(encoding="utf-8", errors="replace"), max_chars)
            logger.debug(f"Read text file: {name} ({len(content)} chars)")
            return name, content
        except Exception as e:
            logger.error(f"Error reading text file {name}: {e}")
            return name, f"[Error reading file: {e}]"

    # Unknown extension — try reading as text, fall back to binary notice
    try:
        content = _truncate(p.read_text(encoding="utf-8", errors="strict"), max_chars)
        logger.debug(f"Read file as text (unknown ext): {name} ({len(content)} chars)")
        return name, content
    except (UnicodeDecodeError, Exception):
        size_kb = p.stat().st_size / 1024
        logger.warning(f"Binary file cannot be read inline: {name} ({size_kb:.1f} KB)")
        return name, f"[Binary file: {name}, {size_kb:.1f} KB — cannot display content inline]"


async def _build_chat_prompt(session_id: str, new_message: str,
                              attachments: list[dict] | None = None) -> str:
    """Build chat prompt — just the new message + attachments.
    Agent session handles history via --resume."""
    parts = []
    if attachments:
        parts.append("ATTACHED FILES:\n")
        for att in attachments:
            fp = att.get("file_path", "")
            original_name = att.get("filename", Path(fp).name)
            _, content = _read_attached_file(fp)
            parts.append(f"\n--- FILE: {original_name} ---\n{content}\n--- END FILE ---\n")
            logger.debug(f"Embedded attached file: {original_name} ({len(content)} chars)")
        parts.append("\n")
    parts.append(new_message)
    return "".join(parts)


async def _handle_chat_message(websocket: WebSocket, data: dict, ws_user_id: int, ws_lock: asyncio.Lock, ws_role: str = "user"):
    """Process a single chat message (runs as a concurrent task)."""
    message = data.get("message", "").strip()
    session_id = data.get("session_id", "default")
    chat_mode = data.get("mode", "coach")  # "coach" or "dev"

    # Dev mode requires admin
    if chat_mode == "dev" and ws_role != "admin":
        async def _ws_send_err(msg):
            msg["session_id"] = session_id
            async with ws_lock:
                await websocket.send_json(msg)
        await _ws_send_err({"type": "error", "text": "Developer chat requires admin role"})
        return

    async def ws_send(msg):
        msg["session_id"] = session_id
        # Try the original WS first, fall back to registry (new WS after refresh)
        candidates = [(websocket, ws_lock)]
        registry_entry = _chat_ws_registry.get(ws_user_id)
        if registry_entry and registry_entry[0] is not websocket:
            candidates.append(registry_entry)
        for ws, lock in candidates:
            try:
                async with lock:
                    await ws.send_json(msg)
                return
            except Exception:
                continue

    # Support attachments [{file_path, filename}], file_paths [str], or file_path str
    attachments = data.get("attachments") or []
    if not attachments:
        file_paths = data.get("file_paths") or []
        if not file_paths and data.get("file_path"):
            file_paths = [data["file_path"]]
        attachments = [{"file_path": fp, "filename": Path(fp).name} for fp in file_paths]

    if not message and not attachments:
        return

    file_info = f" (+{len(attachments)} files: {', '.join(a.get('filename','?') for a in attachments)})" if attachments else ""
    logger.debug(f"Chat message [{session_id[:8]}]: {message[:100]}...{file_info}")

    cli = _find_claude_cli()
    agent_name = data.get("agent_name") or "main-coach"

    # Save user message + look up agent/session/settings in one DB connection
    conn = await db.get_db()
    try:
        await db.chat_save(conn, session_id, "user", message,
                           attachments[0]["file_path"] if attachments else None,
                           user_id=ws_user_id)

        if not cli:
            logger.error("Claude CLI not found for chat")
            await ws_send({"type": "error", "text": "Claude CLI not found. Install it with: npm install -g @anthropic-ai/claude-code"})
            await db.chat_save(conn, session_id, "assistant",
                               "[Error: Claude CLI not found]",
                               user_id=ws_user_id)
            return

        # Quick credential check (only if idle for configured hours)
        preflight_err = await _llm_preflight_check()
        if preflight_err:
            logger.error(f"LLM preflight failed [{session_id[:8]}]: {preflight_err}")
            await ws_send({"type": "error", "text": preflight_err})
            await db.chat_save(conn, session_id, "assistant",
                               f"[Error: {preflight_err}]",
                               user_id=ws_user_id)
            return

        stored_agent = await db.chat_get_agent(conn, session_id)
        if stored_agent and stored_agent != "main-coach":
            agent_name = stored_agent

        if agent_name == "main-coach":
            agent_session_uuid = coach_session_id(f"main-coach-{session_id}")
        else:
            agent_session_uuid = coach_session_id(f"{agent_name}-user{ws_user_id}")

        existing = await db.session_get(conn, agent_session_uuid)
        rotation_kb = int(await db.setting_get(conn, "session_rotation_kb", "800"))
    finally:
        await conn.close()

    prompt = await _build_chat_prompt(session_id, message, attachments or None)
    if not existing:
        existing = (_SESSIONS_DIR / f"{agent_session_uuid}.jsonl").exists()

    # Rotate oversized CLI sessions — threshold from admin settings, skip for dev mode
    cli_jsonl = _SESSIONS_DIR / f"{agent_session_uuid}.jsonl"
    rotation_bytes = rotation_kb * 1024
    cli_size = cli_jsonl.stat().st_size if (chat_mode != "dev" and existing and cli_jsonl.exists()) else 0
    if cli_size > rotation_bytes:
        old_size_kb = cli_size / 1024
        ts = int(time.time())
        rotated = cli_jsonl.with_suffix(f".{ts}.jsonl.bak")
        while rotated.exists():
            ts += 1
            rotated = cli_jsonl.with_suffix(f".{ts}.jsonl.bak")
        cli_jsonl.rename(rotated)
        # Also rotate subagents dir if present
        subagents_dir = _SESSIONS_DIR / agent_session_uuid
        if subagents_dir.exists():
            subagents_dir.rename(subagents_dir.with_suffix(f".{int(time.time())}.bak"))
        existing = False
        logger.info(f"Rotated oversized CLI session [{session_id[:8]}] ({old_size_kb:.0f}KB, limit {rotation_kb}KB) -> fresh session")

        # Notify user about chat session rotation
        try:
            conn_n = await db.get_db()
            try:
                await db.notification_add(conn_n,
                    f"Chat session rotated: {agent_name}",
                    f"Chat session for '{agent_name}' was {old_size_kb:.0f}KB (limit {rotation_kb}KB). "
                    f"Session reset with recent context injected.",
                    user_id=ws_user_id)
            finally:
                await conn_n.close()
        except Exception as e:
            logger.warning(f"Failed to add rotation notification: {e}")

    # Ensure prompt doesn't start with '-' (CLI would misinterpret as flag)
    safe_prompt = prompt if not prompt.startswith("-") else " " + prompt

    # Agent-specific CLI configuration
    if chat_mode == "dev":
        # Dev agents: full toolset for code changes
        allowed_tools = "ToolSearch,Read,Edit,Write,Grep,Glob,Bash,Agent"
    elif agent_name == "main-coach":
        allowed_tools = "ToolSearch,Read,Grep,Bash,Agent"
    else:
        # Specialist agents: direct tool access, no delegation
        allowed_tools = "Read,Grep,Bash"
    # Deny destructive commands — agents must not delete/modify files outside project scope
    disallowed_tools = "Bash(rm *),Bash(rm -rf *),Bash(rmdir *),Bash(mv *),Bash(git rm *),Bash(git rm -rf *)"

    bare_flag = ["--bare"] if chat_mode != "dev" else []

    # Always prepend current date/time so the LLM never has to guess
    msg_lang = _detect_lang(message)
    from services.coach_preamble import _format_now
    date_prefix = f"[Current date/time: {_format_now(msg_lang)}]\n\n"

    if existing:
        cmd = [cli, *bare_flag, "--resume", agent_session_uuid, "-p", date_prefix + safe_prompt,
               "--output-format", "stream-json", "--verbose",
               "--allowed-tools", allowed_tools,
               "--disallowed-tools", disallowed_tools]
    else:
        # New or rotated session: inject recent chat history so coach has context
        history_prefix = ""
        try:
            conn2 = await db.get_db()
            try:
                cursor = await conn2.execute(
                    "SELECT role, content FROM chat_history "
                    "WHERE session_id = ? AND user_id = ? ORDER BY id DESC LIMIT ?",
                    (session_id, ws_user_id, CHAT_HISTORY_LIMIT)
                )
                recent = await cursor.fetchall()
                # Check chat summary mode setting
                summary_mode = await db.setting_get(conn2, "chat_summary_mode", "ai")
            finally:
                await conn2.close()
            if recent and len(recent) > 1:
                recent.reverse()  # oldest first
                messages = [{"role": msg["role"], "content": msg["content"]} for msg in recent]

                if summary_mode == "ai":
                    # AI-generated summary of the conversation
                    summary = await _summarize_chat_context(messages, user_id=ws_user_id)
                    history_prefix = (
                        "[PREVIOUS CONVERSATION SUMMARY — this is a fresh CLI session but the user "
                        "has been chatting with you before. Here is a summary of the recent conversation. "
                        "Continue naturally from where you left off.]\n\n"
                        + summary
                        + "\n\n---\n\n"
                    )
                else:
                    # Raw messages mode (free)
                    lines = []
                    for msg in messages:
                        role_label = "User" if msg["role"] == "user" else "Coach"
                        text = msg["content"][:CHAT_CONTENT_PREVIEW_CHARS]
                        lines.append(f"{role_label}: {text}")
                    history_prefix = (
                        "[PREVIOUS CONVERSATION CONTEXT — this is a fresh CLI session but the user "
                        "has been chatting with you before. Here are the last messages for context. "
                        "Continue naturally from where you left off.]\n\n"
                        + "\n\n".join(lines)
                        + "\n\n---\n\n"
                    )
        except Exception as e:
            logger.warning(f"Could not load chat history for context: {e}")

        if chat_mode == "dev":
            # Dev mode: agent-specific memory (no coach preamble, no workout context)
            # Date/time already prepended via date_prefix for all sessions
            try:
                conn_am = await db.get_db()
                try:
                    memories = await db.agent_memory_get_all(conn_am, ws_user_id, agent_name)
                finally:
                    await conn_am.close()
                if memories:
                    mem_lines = [f"- {m['content'][:CHAT_CONTENT_PREVIEW_CHARS]}" for m in memories]
                    mem_text = "\n".join(mem_lines)[:CHAT_CONTENT_PREVIEW_CHARS * CHAT_HISTORY_LIMIT]
                    history_prefix = f"[AGENT MEMORY for {agent_name}]\n{mem_text}\n\n" + history_prefix
            except Exception as e:
                logger.warning(f"Could not load agent memory: {e}")
        else:
            # Coach mode: inject workout insights / meals (same as _call_agent rotation)
            specialist_context = await _build_rotation_context(agent_name, ws_user_id)
            if specialist_context:
                history_prefix = specialist_context + "\n\n" + history_prefix

            # Inject athlete preamble for new/rotated sessions
            try:
                chat_preamble = await _build_coach_preamble(ws_user_id, agent_name=agent_name, lang=msg_lang)
                if chat_preamble:
                    history_prefix = f"[ATHLETE CONTEXT]\n{chat_preamble}\n\n" + history_prefix
            except Exception as e:
                logger.warning(f"Could not build chat preamble: {e}")

        # Inject recent insights directly for coach sessions (no file I/O)
        if chat_mode != "dev" and agent_name == "main-coach":
            from services.insights_engine import get_recent_insights_text
            try:
                insights_text = await get_recent_insights_text(ws_user_id)
                if insights_text:
                    safe_prompt = insights_text + "\n\n" + safe_prompt
            except Exception as e:
                logger.warning(f"Could not load recent insights: {e}")
        safe_prompt = history_prefix + safe_prompt
        cmd = [cli, *bare_flag, "--agent", agent_name, "--session-id", agent_session_uuid,
               "-p", safe_prompt, "--output-format", "stream-json", "--verbose",
               "--allowed-tools", allowed_tools,
               "--disallowed-tools", disallowed_tools]

    chat_model = await _get_model_override()
    if chat_model:
        cmd += ["--model", chat_model]

    full_response = []
    followup_results = []  # Results from actions that need to be fed back to the agent
    first_delta = True
    skip_preamble = existing  # When resuming, skip any "Continue from..." preamble
    preamble_buf = ""
    action_buf = ""  # Buffer for detecting [ACTION:...] blocks
    env = _build_cli_env()
    chat_start = time.time()

    # Mark session as streaming
    _chat_streaming[session_id] = {"mode": chat_mode, "agent_name": agent_name, "user_id": ws_user_id}

    # Tell frontend we're waiting for Claude to think
    await ws_send({"type": "status", "text": "thinking"})
    logger.debug(f"Chat CLI starting [{session_id[:8]}]")

    try:
        proc = await asyncio.create_subprocess_exec(
            *cmd,
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE,
            cwd=str(PROJECT_ROOT),
            env=env,
            limit=SUBPROCESS_LINE_BUFFER,
        )
        _chat_procs[session_id] = proc

        # Background task: read stderr and detect fatal errors early (auth, rate limit, etc.)
        _stderr_lines = []
        _fatal_error = None
        _FATAL_PATTERNS = ("API Error:", "Please run /login", "authentication failed",
                           "security token", "rate limit", "Could not connect")
        async def _read_stderr():
            nonlocal _fatal_error
            while True:
                line = await proc.stderr.readline()
                if not line:
                    break
                text = line.decode("utf-8", errors="replace").strip()
                _stderr_lines.append(text)
                # Detect known fatal CLI errors and kill immediately
                if not _fatal_error and any(pat.lower() in text.lower() for pat in _FATAL_PATTERNS):
                    _fatal_error = text
                    logger.error(f"Chat CLI fatal error [{session_id[:8]}]: {text[:200]}")
                    proc.kill()  # Kills process → stdout EOF → main loop exits
        stderr_task = asyncio.create_task(_read_stderr())

        while True:
            try:
                line = await asyncio.wait_for(proc.stdout.readline(), timeout=CHAT_STALE_TIMEOUT_SEC)
            except asyncio.TimeoutError:
                logger.warning(f"Chat CLI stale [{session_id[:8]}] — no output for {CHAT_STALE_TIMEOUT_SEC}s, killing")
                proc.kill()
                break
            if not line:
                break  # EOF (normal end or killed by stderr reader)
            line_str = line.decode("utf-8", errors="replace").strip()
            if not line_str:
                continue
            try:
                event = json.loads(line_str)
            except (json.JSONDecodeError, ValueError):
                continue

            etype = event.get("type", "")

            # Notify frontend when CLI is doing tool calls (so user sees activity)
            if etype == "content_block_start":
                cb = event.get("content_block", {})
                if cb.get("type") == "tool_use":
                    tool_name = cb.get("name", "tool")
                    await ws_send({"type": "status", "text": f"using {tool_name}"})

            # Handle streaming text deltas
            if etype == "content_block_delta":
                text = event.get("delta", {}).get("text", "")
                if text:
                    # Filter "Continue from where you left off" preamble on resume
                    if skip_preamble:
                        preamble_buf += text
                        # Check once we have enough text
                        if len(preamble_buf) > 10:
                            if "ontinue" in preamble_buf[:50] and "left off" in preamble_buf[:80]:
                                # Skip until we find a double newline (end of preamble)
                                nl_pos = preamble_buf.find("\n\n")
                                if nl_pos >= 0:
                                    skip_preamble = False
                                    remainder = preamble_buf[nl_pos + 2:]
                                    preamble_buf = ""
                                    if remainder.strip():
                                        text = remainder
                                    else:
                                        continue
                                else:
                                    continue
                            else:
                                # Not a preamble — flush buffer
                                skip_preamble = False
                                text = preamble_buf
                                preamble_buf = ""
                        else:
                            continue

                    # Action block detection: buffer text that may contain [ACTION:...]
                    # Actions arrive across multiple deltas, so we buffer when we see '['
                    action_buf += text
                    if "[ACTION:" in action_buf:
                        # Check if we have a complete action block
                        if "]" in action_buf[action_buf.index("[ACTION:"):]:
                            clean_text, actions = extract_actions(action_buf)
                            action_buf = ""
                            # Execute detected actions
                            for act_name, act_params in actions:
                                try:
                                    result = await execute_action(act_name, act_params, ws_user_id)
                                    await ws_send({"type": "action_result", "action": act_name, "result": result})
                                    if act_name in FOLLOWUP_ACTIONS:
                                        followup_results.append((act_name, result))
                                except Exception as e:
                                    logger.error(f"Action execution error: {act_name}: {e}")
                                    await ws_send({"type": "action_result", "action": act_name,
                                                   "result": {"ok": False, "error": str(e)}})
                            # Send remaining clean text to user
                            text = clean_text.strip()
                            if not text:
                                continue
                        else:
                            # Incomplete action block — keep buffering
                            # But flush any text before the [ACTION: marker to avoid delay
                            marker_pos = action_buf.index("[ACTION:")
                            if marker_pos > 0:
                                pre_text = action_buf[:marker_pos]
                                action_buf = action_buf[marker_pos:]
                                text = pre_text
                            else:
                                continue
                    else:
                        # No action marker — flush buffer as normal text
                        text = action_buf
                        action_buf = ""

                    if first_delta:
                        first_delta = False
                        think_time = time.time() - chat_start
                        logger.debug(f"Chat first token [{session_id[:8]}] after {think_time:.1f}s")
                        await ws_send({"type": "status", "text": "writing"})
                    full_response.append(text)
                    await ws_send({"type": "delta", "text": text})

            # Handle the final result
            elif etype == "result":
                result_text = event.get("result", "")
                if result_text and not full_response:
                    full_response.append(result_text)
                    await ws_send({"type": "delta", "text": result_text})
                # Track token usage from the result event
                cost = event.get("total_cost_usd", 0)
                if cost:
                    asyncio.create_task(_track_usage(
                        event, "chat", agent_name or "main-coach", session_id, ws_user_id))
                    await ws_send({"type": "usage", "cost": cost})

        await proc.wait()
        stderr_task.cancel()
        _chat_procs.pop(session_id, None)
        # Flush any buffered preamble that wasn't a "Continue..." message
        if preamble_buf:
            full_response.append(preamble_buf)
            await ws_send({"type": "delta", "text": preamble_buf})
            preamble_buf = ""
        # Flush any incomplete action buffer (action block was never closed)
        if action_buf:
            # Try to extract any complete actions even if buffer has trailing text
            clean_text, actions = extract_actions(action_buf)
            for act_name, act_params in actions:
                try:
                    result = await execute_action(act_name, act_params, ws_user_id)
                    await ws_send({"type": "action_result", "action": act_name, "result": result})
                    if act_name in FOLLOWUP_ACTIONS:
                        followup_results.append((act_name, result))
                except Exception as e:
                    logger.error(f"Action execution error: {act_name}: {e}")
            remaining = clean_text.strip()
            if remaining:
                full_response.append(remaining)
                await ws_send({"type": "delta", "text": remaining})
            action_buf = ""
        elapsed = time.time() - chat_start
        # Use already-collected stderr lines + any remaining
        remaining_stderr = (await proc.stderr.read()).decode("utf-8", errors="replace").strip()
        if remaining_stderr:
            _stderr_lines.append(remaining_stderr)
        stderr_out = "\n".join(_stderr_lines).strip()
        was_killed = proc.returncode and proc.returncode < 0

        # Fallback: if CLI produced no stdout output, try reading response from session JSONL
        # Only use if the file was modified AFTER we started (i.e. CLI wrote a new response)
        if not full_response:
            jsonl_path = _SESSIONS_DIR / f"{agent_session_uuid}.jsonl"
            if jsonl_path.exists() and jsonl_path.stat().st_mtime > chat_start:
                try:
                    last_assistant = ""
                    found_our_msg = False
                    with open(jsonl_path, "r", encoding="utf-8", errors="replace") as f:
                        for raw_line in f:
                            raw_line = raw_line.strip()
                            if not raw_line:
                                continue
                            try:
                                entry = json.loads(raw_line)
                                # Look for our user message, then the assistant response after it
                                if entry.get("type") in ("human", "user") and not found_our_msg:
                                    content = entry.get("message", {}).get("content", "")
                                    if isinstance(content, list):
                                        content = " ".join(b.get("text", "") for b in content if b.get("type") == "text")
                                    if isinstance(content, str) and message[:20] in content:
                                        found_our_msg = True
                                        last_assistant = ""  # Reset — only want response after our msg
                                elif entry.get("type") == "assistant" and found_our_msg:
                                    content = entry.get("message", {}).get("content", "")
                                    if isinstance(content, list):
                                        content = "".join(b.get("text", "") for b in content if b.get("type") == "text")
                                    if content:
                                        last_assistant = content
                            except json.JSONDecodeError:
                                pass
                    if last_assistant:
                        logger.debug(f"Chat fallback from JSONL [{session_id[:8]}] ({elapsed:.1f}s, {len(last_assistant)} chars)")
                        full_response.append(last_assistant)
                        await ws_send({"type": "delta", "text": last_assistant})
                    elif found_our_msg:
                        logger.warning(f"Chat JSONL fallback [{session_id[:8]}]: found our message but no new response")
                except Exception as fallback_err:
                    logger.warning(f"Chat JSONL fallback failed [{session_id[:8]}]: {fallback_err}")

        if _fatal_error and not full_response:
            # Fatal error detected by stderr reader (auth, rate limit, etc.)
            logger.error(f"Chat CLI fatal [{session_id[:8]}] ({elapsed:.1f}s): {_fatal_error[:200]}")
            await ws_send({"type": "error", "text": _fatal_error})
            full_response.append(f"[Error: {_fatal_error}]")
        elif proc.returncode != 0 and not full_response and not was_killed:
            error_msg = stderr_out or f"Claude CLI exited with code {proc.returncode}"
            logger.error(f"Chat CLI error [{session_id[:8]}] ({elapsed:.1f}s): {error_msg[:200]}")
            await ws_send({"type": "error", "text": error_msg})
            full_response.append(f"[Error: {error_msg}]")
        elif was_killed and not full_response:
            logger.debug(f"Chat CLI stopped [{session_id[:8]}] ({elapsed:.1f}s), no response recovered")
            await ws_send({"type": "error", "text": "Coach session timed out. Try starting a new chat session."})
        else:
            logger.debug(f"Chat response [{session_id[:8]}] complete ({elapsed:.1f}s, {len(''.join(full_response))} chars)")

    except (asyncio.CancelledError, KeyboardInterrupt, SystemExit):
        raise
    except Exception as e:
        logger.error(f"Chat exception [{session_id[:8]}]: {e}")
        await ws_send({"type": "error", "text": str(e)})
        full_response.append(f"[Error: {e}]")
    finally:
        _chat_procs.pop(session_id, None)
        _chat_streaming.pop(session_id, None)

    await ws_send({"type": "done"})

    # Save assistant response and update session record
    assistant_text = "".join(full_response)
    conn = await db.get_db()
    try:
        if assistant_text:
            await db.chat_save(conn, session_id, "assistant", assistant_text,
                               user_id=ws_user_id)
        await db.session_save(conn, agent_session_uuid, agent_name, agent_name, user_id=ws_user_id)
        # Auto-generate session title from first user message if not set yet
        existing_title = await db.chat_get_title(conn, session_id)
        if not existing_title and message:
            # Set quick fallback title immediately
            fallback = _generate_session_title(message)
            if fallback:
                await db.chat_set_title(conn, session_id, fallback, user_id=ws_user_id, agent_name=agent_name, mode=chat_mode)
                logger.debug(f"Auto-title [{session_id[:8]}]: {fallback}")
            # Then fire AI title generation in background (will overwrite fallback)
            asyncio.create_task(_generate_ai_title(session_id, message, assistant_text, ws_user_id))
    finally:
        await conn.close()

    # Follow-up: send action results back to the agent as a new message
    if followup_results:
        followup_parts = []
        for act_name, result in followup_results:
            followup_parts.append(f"[ACTION RESULT: {act_name}]\n{json.dumps(result, ensure_ascii=False)}")
        followup_msg = "\n\n".join(followup_parts)
        followup_msg += (
            "\n\nThe action results above were executed by the server. "
            "Present the results to the athlete and continue the conversation. "
            "If the analysis returned meals, show them clearly and ask if the athlete wants to save them."
        )
        logger.info(f"Sending follow-up with {len(followup_results)} action results [{session_id[:8]}]")
        followup_data = {
            "message": followup_msg,
            "session_id": session_id,
            "mode": chat_mode,
            "agent_name": agent_name,
        }
        await _handle_chat_message(websocket, followup_data, ws_user_id, ws_lock, ws_role)


# ── WebSocket Route ──────────────────────────────────────────────────────────

chat_router = APIRouter()

@chat_router.websocket("/ws/chat")
async def chat_ws(websocket: WebSocket):
    await websocket.accept()
    ws_user_id = None
    ws_role = "user"
    token = websocket.cookies.get("token")
    if token:
        payload = decode_jwt(token)
        if payload and "user_id" in payload:
            ws_user_id = payload["user_id"]
            ws_role = payload.get("role", "user")
    if ws_user_id is None:
        await websocket.close(code=4001, reason="Authentication required")
        return
    # Check if AI features are enabled
    try:
        from routes.deps import _require_ai
        await _require_ai()
    except Exception:
        await websocket.close(code=4003, reason="AI features are disabled. An admin can enable them in Admin > Settings.")
        return
    logger.info(f"WebSocket chat connected (user_id={ws_user_id})")
    ws_lock = asyncio.Lock()
    # Register this WS so running tasks can find the new connection after refresh
    _chat_ws_registry[ws_user_id] = (websocket, ws_lock)
    tasks: set[asyncio.Task] = set()
    MAX_WS_CONCURRENT = 3
    last_msg_time: float = 0
    try:
        while True:
            raw = await websocket.receive_text()
            data = json.loads(raw)
            # Rate limit: minimum 1 second between messages
            now = time.monotonic()
            if now - last_msg_time < 1.0:
                await websocket.send_json({"type": "error", "content": "Please slow down."})
                continue
            last_msg_time = now
            # Concurrent task limit
            if len(tasks) >= MAX_WS_CONCURRENT:
                await websocket.send_json({"type": "error", "content": "Too many concurrent requests. Please wait."})
                continue
            task = asyncio.create_task(_handle_chat_message(websocket, data, ws_user_id, ws_lock, ws_role))
            tasks.add(task)
            task.add_done_callback(tasks.discard)
    except WebSocketDisconnect:
        logger.info(f"WebSocket chat disconnected ({len(tasks)} tasks still running)")
        # Only remove from registry if this is still the registered WS (not replaced by a new one)
        if _chat_ws_registry.get(ws_user_id, (None,))[0] is websocket:
            _chat_ws_registry.pop(ws_user_id, None)
        # Move orphaned tasks to global set to prevent GC — they'll clean themselves up on completion
        for t in tasks:
            _chat_bg_tasks.add(t)
            t.add_done_callback(_chat_bg_tasks.discard)
