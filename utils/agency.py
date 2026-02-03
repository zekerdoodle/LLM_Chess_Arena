"""
Agency utilities: seed meta task and enrich system guidance.

Provides a simple seeding mechanism that ensures a baseline meta task exists
so Theo practices task-chaining on startup.
"""

from __future__ import annotations

from datetime import datetime, timedelta
from typing import Optional

from utils.logger import get_logger
from utils.config_loader import get_config_value, load_config

logger = get_logger(__name__)


META_TASK_NAME = "Meta: How do I agent?"
META_TASK_DETAILS = (
    "Design the framework of agentic behavior that I will follow. "
    "Answer: How will I store and track progress on tasks? How will I define tasks? "
    "What is my task-chaining loop and how do I ensure stable progress occurs reliably? "
    "Deliver a short plan and schedule the next follow-up task automatically."
)


def _choose_silent_window_time(now: Optional[datetime] = None) -> str:
    """Return a local-time string within 00:00–06:00 for the next run.

    We pick 02:17 (2:17am) by default; if already past, schedule for tomorrow.
    The agentic_tools parser will roll over to tomorrow if the time is past today.
    """
    _ = now  # signature kept for potential future TZ-aware logic
    return "2:17am"


def ensure_meta_task_seed() -> Optional[str]:
    """Ensure the meta task exists; create it if missing.

    Returns the task_id if created, or None if it already existed or on error.
    """
    try:
        # Lazy import to avoid heavy deps at import time
        from pathlib import Path
        try:
            from utils.vault_paths import get_vault_root
            vault_root = Path(get_vault_root())
        except Exception:
            vault_root = Path("vault")
        tasks_file = vault_root / "scheduled_tasks.json"

        # Prefer reading via the agentic tools loader to ensure identical roots
        try:
            from layer4_tools.agentic_tools import list_task
            _text, info = list_task()
            tasks = (info or {}).get("tasks") or {}
            for t in (tasks.values() if isinstance(tasks, dict) else []):
                try:
                    if str(t.get("name", "")).strip().lower() == META_TASK_NAME.lower():
                        logger.info("Agency seed - Meta task already present; skipping seed")
                        return None
                except Exception:
                    continue
        except Exception:
            # Fallback to direct file read
            tasks = {}
            if tasks_file.exists():
                try:
                    import json
                    tasks = json.loads(tasks_file.read_text(encoding="utf-8"))
                    if not isinstance(tasks, dict):
                        tasks = {}
                except Exception:
                    tasks = {}
            for t in tasks.values():
                try:
                    if str(t.get("name", "")).strip().lower() == META_TASK_NAME.lower():
                        logger.info("Agency seed - Meta task already present; skipping seed")
                        return None
                except Exception:
                    continue

        # Create the seed task (silent by default)
        # IMPORTANT: Meta task gets its own isolated room via "new_room"
        # All sub-tasks created by the meta task will default to the same room,
        # ensuring the meta task framework stays isolated from user conversations.
        start_when = _choose_silent_window_time()
        try:
            from layer4_tools.agentic_tools import create_task
            result_text, out = create_task(
                name=META_TASK_NAME,
                details=META_TASK_DETAILS,
                start_time=start_when,
                recurrence=None,
                silent=True,
                room_id="new_room",  # Creates dedicated task room with task_* prefix
            )
            if out and out.get("success") and out.get("task_id"):
                task_id = str(out.get("task_id"))
                task_room = out.get("room_id")
                logger.info(
                    "Agency seed - Created meta task '%s' for %s (id=%s, room=%s)",
                    META_TASK_NAME,
                    start_when,
                    task_id,
                    task_room
                )
                logger.info(
                    "Agency seed - Meta task room '%s' will host all agent framework work. "
                    "Sub-tasks will inherit this room for isolation.",
                    task_room
                )
                return task_id
            logger.warning("Agency seed - Failed to create meta task: %s", result_text)
            return None
        except Exception as e:
            logger.error("Agency seed - Error creating meta task: %s", e, exc_info=True)
            return None
    except Exception as e:
        logger.error("Agency seed - Unexpected error: %s", e, exc_info=True)
        return None
