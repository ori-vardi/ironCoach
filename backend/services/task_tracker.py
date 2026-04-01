"""Active task tracking for LLM operations.

Tracks chat processes, insight batch status, and active LLM tasks
so frontend can display progress and survive page refreshes.
"""

import asyncio
from datetime import datetime
from fastapi import WebSocket


# Chat/streaming state
_chat_procs: dict[str, asyncio.subprocess.Process] = {}   # session_id -> proc
_chat_streaming: dict[str, dict] = {}  # session_id -> {mode, agent_name}
_chat_ws_registry: dict[int, tuple[WebSocket, asyncio.Lock]] = {}  # user_id -> (websocket, lock)
_chat_bg_tasks: set[asyncio.Task] = set()  # prevent GC of orphaned chat tasks
_insight_batch_cancel = False
_insight_batch_user: int | None = None  # user_id of who started the current batch
_insight_active_procs: set[asyncio.subprocess.Process] = set()  # active CLI procs for batch insights
_pending_title_gen: set[str] = set()  # Guard against duplicate background title generation

# Insight status
_insight_status = {"running": False, "total": 0, "completed": 0, "current": "", "history": []}
_insight_status_lock = asyncio.Lock()

# Active LLM tasks
_active_tasks: dict[str, dict] = {}  # id -> {label, link, started_at}
_active_tasks_lock = asyncio.Lock()


async def _register_task(task_id: str, label: str, link: str = ""):
    """Register an active LLM task."""
    async with _active_tasks_lock:
        _active_tasks[task_id] = {
            "id": task_id,
            "label": label,
            "link": link,
            "started_at": datetime.now().isoformat()
        }


async def _unregister_task(task_id: str):
    """Unregister a completed LLM task."""
    async with _active_tasks_lock:
        _active_tasks.pop(task_id, None)
