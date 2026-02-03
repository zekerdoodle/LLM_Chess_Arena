"""
Agentic Information Hierarchy Tools

Provides Theo with unified task + messaging scheduler capabilities.
Implements the upgraded task system and presence-aware manual messaging.

Available Tools:
- create_task(name, details, start_time, recurrence=None, silent=False, room_id=None, delivery_mode=None)
- update_task(task_id, **fields)
- list_task(task_id=None, status=None, include_recent_completed=False, recent_completed_limit=10)

Notifications are handled via manually_send_message (see docs/operations/TASKS_SYSTEM_COMPLETE_2025-10-20.md).
Scheduler integrates with time-based execution for tasks and scheduled manual messages.
All data persists in vault JSON files with proper logging and error handling.
"""

import json
import os
import tempfile
import shutil
import uuid
import threading
from datetime import datetime, timedelta
import re
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple

from apscheduler.executors.pool import ThreadPoolExecutor
from apscheduler.schedulers.background import BackgroundScheduler
from apscheduler.triggers.cron import CronTrigger
from apscheduler.triggers.date import DateTrigger
try:  # pragma: no cover - croniter optional in minimal environments
    from croniter import croniter as _croniter_impl  # type: ignore
except ModuleNotFoundError:  # pragma: no cover
    _croniter_impl = None

from utils.logger import get_logger, get_current_room_id
from utils.rooms import generate_new_room_id

logger = get_logger(__name__)

if _croniter_impl is None:
    logger.warning("croniter package unavailable; using limited in-process fallback parser")

    class _FallbackCroniter:
        def __init__(self, expr: str, start_time: datetime | None = None):
            self._fields = self._parse(expr)
            base = start_time or datetime.now()
            self._current = base.replace(second=0, microsecond=0) - timedelta(minutes=1)

        def _parse(self, expr: str) -> Tuple[Optional[List[int]], ...]:
            parts = str(expr or "").strip().split()
            if len(parts) != 5:
                raise ValueError("cron expression must have 5 fields")
            return (
                self._expand(parts[0], 0, 59),
                self._expand(parts[1], 0, 23),
                self._expand(parts[2], 1, 31),
                self._expand(parts[3], 1, 12),
                self._expand(parts[4], 0, 6),
            )

        def _expand(self, part: str, minimum: int, maximum: int) -> Optional[List[int]]:
            token = part.strip()
            if token in ("*", "*/1"):
                return None
            values: List[int] = []
            for section in token.split(","):
                section = section.strip()
                if not section:
                    continue
                if section == "*":
                    return None
                if section.startswith("*/"):
                    step = int(section[2:])
                    values.extend(range(minimum, maximum + 1, max(step, 1)))
                    continue
                if "-" in section:
                    start_str, end_str = section.split("-", 1)
                    start = int(start_str)
                    end = int(end_str)
                    if start > end:
                        raise ValueError("invalid cron range")
                    values.extend(range(start, end + 1))
                    continue
                values.append(int(section))
            cleaned = [v for v in values if minimum <= v <= maximum]
            cleaned.sort()
            return cleaned

        def _matches(self, moment: datetime) -> bool:
            minute, hour, day, month, weekday = moment.minute, moment.hour, moment.day, moment.month, moment.weekday()
            minute_set, hour_set, day_set, month_set, weekday_set = self._fields
            if minute_set is not None and minute not in minute_set:
                return False
            if hour_set is not None and hour not in hour_set:
                return False
            if day_set is not None and day not in day_set:
                return False
            if month_set is not None and month not in month_set:
                return False
            if weekday_set is not None:
                cron_value = (weekday + 1) % 7
                acceptable = {cron_value, weekday}
                if cron_value == 0:
                    acceptable.add(7)
                if not any(val in acceptable for val in weekday_set):
                    return False
            return True

        def get_next(self, return_type=datetime):
            attempts = 0
            candidate = self._current
            while attempts < 525600:  # up to one year of minutes
                candidate = candidate + timedelta(minutes=1)
                candidate = candidate.replace(second=0, microsecond=0)
                if self._matches(candidate):
                    self._current = candidate
                    return candidate
                attempts += 1
            raise ValueError("unable to resolve next cron run within fallback limits")

    def croniter(expr: str, start_time: datetime | None = None):
        return _FallbackCroniter(expr, start_time)
else:
    croniter = _croniter_impl

# Vault paths for data storage
try:
    from utils.vault_paths import get_vault_root
    VAULT_ROOT = Path(get_vault_root())
except Exception:
    VAULT_ROOT = Path("vault")
# Dedicated scheduler stores (distinct from web UI task catalog)
TASKS_FILE = VAULT_ROOT / "scheduled_tasks.json"
SCHEDULED_MESSAGES_FILE = VAULT_ROOT / "scheduled_messages.json"
LEGACY_NOTIFICATIONS_FILE = VAULT_ROOT / "scheduled_notifications.json"
TASK_LOGS_DIR = VAULT_ROOT / "task_logs"

# Global scheduler instance
_scheduler = None
_scheduler_timezone = "America/Chicago"
_scheduler_lock = threading.Lock()
_scheduler_disabled = False  # Set to True to prevent scheduler initialization (for web process)


def disable_scheduler():
    """Disable scheduler initialization. Call this in web process to let worker handle scheduling."""
    global _scheduler_disabled
    _scheduler_disabled = True
    logger.info("L4.tools [scheduler] - Scheduler disabled (handled by worker process)")


def enable_scheduler():
    """Re-enable scheduler initialization if it was previously disabled."""
    global _scheduler_disabled
    if _scheduler_disabled:
        _scheduler_disabled = False
        logger.info("L4.tools [scheduler] - Scheduler enabled")


def _get_scheduler():
    """Get or create the global scheduler instance."""
    global _scheduler
    logger.debug(f"L4.tools [scheduler] - _get_scheduler() called, disabled={_scheduler_disabled}, instance={_scheduler is not None}")
    if _scheduler_disabled:
        # Scheduler is disabled (likely in web process where worker handles scheduling)
        logger.debug("L4.tools [scheduler] - Returning None (scheduler disabled)")
        return None
    if _scheduler is None:
        with _scheduler_lock:
            # Double-check pattern to prevent race conditions
            if _scheduler is None and not _scheduler_disabled:
                executors = {"default": ThreadPoolExecutor(20)}
                # Resolve timezone from config if available
                tz = None
                try:
                    from utils.config_loader import load_config, get_config_value
                    cfg = load_config()
                    configured_tz = get_config_value(cfg, "scheduler.timezone") or get_config_value(cfg, "user_timezone") or _scheduler_timezone
                    try:
                        from pytz import timezone as _tz
                        tz = _tz(str(configured_tz)) if configured_tz else None
                        logger.info(f"L4.tools [scheduler] - Using timezone: {configured_tz}")
                    except Exception:
                        tz = None
                except Exception:
                    tz = None
                _scheduler = BackgroundScheduler(executors=executors, timezone=tz)
                _scheduler.start()
                logger.info("L4.tools [scheduler] - Background scheduler started")
                
                # Restore scheduled tasks and notifications from vault
                _restore_scheduled_items()
        
    return _scheduler


def _restore_scheduled_items():
    """Restore scheduled tasks and scheduled manual messages at startup."""
    os.makedirs(TASK_LOGS_DIR, exist_ok=True)

    tasks_raw = _load_json_data(TASKS_FILE) or {}
    messages_raw = _load_json_data(SCHEDULED_MESSAGES_FILE) or {}

    tasks_sanitized: Dict[str, Dict[str, Any]] = {}
    messages_sanitized: Dict[str, Dict[str, Any]] = {}

    legacy_notifications = _load_json_data(LEGACY_NOTIFICATIONS_FILE)
    if isinstance(legacy_notifications, dict) and legacy_notifications:
        for legacy_id, legacy_payload in legacy_notifications.items():
            try:
                if not isinstance(legacy_payload, dict):
                    continue
                text_content = str(legacy_payload.get('message') or '').strip()
                start_value = legacy_payload.get('start_time')
                if not text_content or not start_value:
                    continue
                record = {
                    'id': str(uuid.uuid4())[:8],
                    'text': text_content,
                    'delivery': 'inbox',
                    'channel_id': legacy_payload.get('room_id'),
                    'start_time': str(start_value),
                    'recurrence': legacy_payload.get('recurrence'),
                    'status': str(legacy_payload.get('status') or 'active'),
                    'created_at': legacy_payload.get('created_at') or datetime.now().isoformat(),
                    'last_sent': legacy_payload.get('last_sent'),
                    'send_count': legacy_payload.get('send_count', 0),
                }
                sanitized = _sanitize_scheduled_message(record['id'], record)
                if sanitized:
                    messages_sanitized[sanitized['id']] = sanitized
            except Exception as exc:
                logger.debug(f"L4.tools [legacy notify] - migrate failed for {legacy_id}: {exc}")
        try:
            LEGACY_NOTIFICATIONS_FILE.rename(LEGACY_NOTIFICATIONS_FILE.with_suffix(LEGACY_NOTIFICATIONS_FILE.suffix + '.bak'))
        except Exception:
            pass

    for task_id, payload in tasks_raw.items():
        data = _sanitize_task_record(task_id, payload)
        if not data:
            continue
        
        # Handle past one-time tasks - run them immediately instead of marking completed
        past_task_scheduled = False
        if data.get("status") == "active" and not data.get("recurrence"):
            try:
                start_time = datetime.fromisoformat(data["start_time"])
                if start_time <= datetime.now():
                    logger.info(
                        f"L4.tools [restore] - Past task {task_id} (scheduled for {start_time}) will run immediately"
                    )
                    # Schedule for immediate execution (1 second from now)
                    scheduler = _get_scheduler()
                    if scheduler is not None:
                        scheduler.add_job(
                            func=_execute_task,
                            trigger=DateTrigger(run_date=datetime.now() + timedelta(seconds=1)),
                            args=[task_id],
                            id=f"task_{task_id}",
                            replace_existing=True,
                        )
                        logger.info(f"L4.tools [restore] - Queued past task {task_id} for immediate execution")
                        past_task_scheduled = True
            except Exception as exc:
                logger.debug(f"L4.tools [restore] - Failed to check task {task_id} start time: {exc}")
        
        # Always add to sanitized dict (don't delete tasks!)
        tasks_sanitized[task_id] = data
        
        # Schedule future tasks normally (skip if already scheduled as past task)
        if data.get("status") == "active" and not past_task_scheduled:
            scheduler = _get_scheduler()
            if scheduler is not None:
                job_id = f"task_{task_id}"
                existing_job = scheduler.get_job(job_id)
                if existing_job is None:
                    _schedule_task_job(data)
                    logger.debug(f"L4.tools [restore] - Scheduled restored task {task_id}")
                else:
                    logger.debug(f"L4.tools [restore] - Skipped scheduling task {task_id} (already scheduled)")
            else:
                logger.debug(f"L4.tools [restore] - Skipped scheduling task {task_id} (no scheduler available)")

    for message_id, payload in messages_raw.items():
        data = _sanitize_scheduled_message(message_id, payload)
        if not data:
            continue
        messages_sanitized[message_id] = data
        if data.get("status") == "active":
            _schedule_scheduled_message_job(data)

    _save_json_data(TASKS_FILE, tasks_sanitized)
    _save_json_data(SCHEDULED_MESSAGES_FILE, messages_sanitized)


def _sync_tasks_from_disk():
    """
    Periodic synchronization function to pick up tasks created via web process.
    
    This function is called every 30 seconds by the worker to check for new tasks
    that were created through the web process (where scheduler is disabled).
    It only schedules tasks that aren't already in the scheduler.
    """
    try:
        tasks_raw = _load_json_data(TASKS_FILE) or {}
        scheduler = _get_scheduler()
        
        if scheduler is None:
            logger.debug("L4.tools [sync] - Scheduler not available, skipping sync")
            return
        
        synced_count = 0
        for task_id, payload in tasks_raw.items():
            data = _sanitize_task_record(task_id, payload)
            if not data or data.get("status") != "active":
                continue
            
            job_id = f"task_{task_id}"
            existing_job = scheduler.get_job(job_id)
            
            if existing_job is not None:
                # Task already scheduled, skip
                continue
            
            # New task found - schedule it
            try:
                start_time = datetime.fromisoformat(data["start_time"])
                
                # If task is in the past, schedule for immediate execution
                if start_time <= datetime.now() and not data.get("recurrence"):
                    logger.info(f"L4.tools [sync] - Found past task {task_id}, scheduling for immediate execution")
                    scheduler.add_job(
                        func=_execute_task,
                        trigger=DateTrigger(run_date=datetime.now() + timedelta(seconds=1)),
                        args=[task_id],
                        id=job_id,
                        replace_existing=True,
                    )
                    synced_count += 1
                else:
                    # Schedule normally
                    success, error = _schedule_task_job(data)
                    if success:
                        logger.info(f"L4.tools [sync] - Synced new task {task_id} ({data.get('name')})")
                        synced_count += 1
                    else:
                        logger.warning(f"L4.tools [sync] - Failed to sync task {task_id}: {error}")
            except Exception as exc:
                logger.error(f"L4.tools [sync] - Error syncing task {task_id}: {exc}")
        
        if synced_count > 0:
            logger.info(f"L4.tools [sync] - Synced {synced_count} new task(s) from disk")
        else:
            logger.debug("L4.tools [sync] - No new tasks to sync")
            
    except Exception as exc:
        logger.error(f"L4.tools [sync] - Sync error: {exc}", exc_info=True)


def _duplicate_room_for_task(source_room_id: str, task_id: str, task_name: str) -> str:
    """Duplicate a room's history for a task, creating a new task room with copied context.
    
    Args:
        source_room_id: The room to copy from
        task_id: Task ID for the new room
        task_name: Task name for metadata
        
    Returns:
        New task room ID
    """
    try:
        from utils.rooms_meta import load as _load_meta, save as _save_meta
    except Exception:
        _load_meta = None
        _save_meta = None

    rooms_dir = VAULT_ROOT / "chats"
    rooms_dir.mkdir(parents=True, exist_ok=True)

    # Generate new task room ID
    try:
        new_room_id = generate_new_room_id(prefix="task")
    except Exception:
        new_room_id = f"task_{task_id}"
    
    source_path = rooms_dir / f"{source_room_id}.json"
    new_room_path = rooms_dir / f"{new_room_id}.json"
    
    # Copy history from source room
    try:
        if source_path.exists():
            with open(source_path, "r", encoding="utf-8") as f:
                history = json.load(f)
            if not isinstance(history, list):
                history = []
        else:
            history = []
            logger.warning(f"L4.tools [duplicate_room] - Source room {source_room_id} not found, creating empty task room")
        
        with open(new_room_path, "w", encoding="utf-8") as f:
            json.dump(history, f, indent=2, ensure_ascii=False)
        
        logger.info(f"L4.tools [duplicate_room] - Duplicated {len(history)} messages from {source_room_id} to {new_room_id}")
    except Exception as e:
        logger.error(f"L4.tools [duplicate_room] - Failed to copy history: {e}")
        # Create empty room as fallback
        new_room_path.write_text("[]", encoding="utf-8")
    
    # Set metadata for new task room
    if _load_meta and _save_meta:
        try:
            meta = _load_meta()
        except Exception:
            meta = {}
        
        entry = {
            "title": f"Task · {task_name}",
            "updated_at": int(datetime.now().timestamp()),
            "room_type": "task",
            "task_id": task_id
        }
        meta[new_room_id] = entry
        
        try:
            _save_meta(meta)
        except Exception as e:
            logger.error(f"L4.tools [duplicate_room] - Failed to save metadata: {e}")
    
    return new_room_id


def _ensure_task_room(task_id: str, task_name: str, preferred: Optional[str]) -> str:
    """Guarantee a room exists for the task and update metadata."""
    try:
        from utils.rooms_meta import load as _load_meta, save as _save_meta
    except Exception:
        _load_meta = None
        _save_meta = None

    rooms_dir = VAULT_ROOT / "chats"
    rooms_dir.mkdir(parents=True, exist_ok=True)

    room_id = None
    if isinstance(preferred, str) and preferred.strip():
        candidate = preferred.strip()
        room_path = rooms_dir / f"{candidate}.json"
        if room_path.exists():
            room_id = candidate
    if room_id is None:
        try:
            room_id = generate_new_room_id(prefix="task")
        except Exception:
            room_id = f"task_{task_id}"
        room_path = rooms_dir / f"{room_id}.json"

    if not room_path.exists():
        room_path.write_text("[]", encoding="utf-8")

    if _load_meta and _save_meta:
        try:
            meta = _load_meta()
        except Exception:
            meta = {}
        entry = meta.get(room_id, {}) if isinstance(meta, dict) else {}
        
        # Update title to reflect task association
        entry["title"] = f"Task · {task_name}".strip()
        entry["updated_at"] = int(datetime.now().timestamp())
        
        # Preserve existing room_type - standard rooms stay standard, task rooms stay task
        # Only set room_type if not already defined
        if not entry.get("room_type"):
            entry["room_type"] = "task"
        
        entry["task_id"] = task_id
        meta[room_id] = entry
        try:
            _save_meta(meta)
        except Exception:
            pass

    return room_id


def _sanitize_task_record(task_id: str, payload: Any) -> Optional[Dict[str, Any]]:
    try:
        if not isinstance(payload, dict):
            raise ValueError("task payload must be object")
        data: Dict[str, Any] = dict(payload)
        data["id"] = str(data.get("id") or task_id)
        data["name"] = str(data.get("name") or "Untitled Task")
        data["details"] = str(data.get("details") or "")
        data["status"] = str(data.get("status") or "active")
        start = data.get("start_time")
        if not start:
            raise ValueError("missing start_time")
        if isinstance(start, datetime):
            start_dt = start
        else:
            start_dt = datetime.fromisoformat(str(start))
        data["start_time"] = start_dt.isoformat()
        recurrence = data.get("recurrence")
        if recurrence:
            recurrence = str(recurrence).strip()
            if not _validate_cron_expression(recurrence):
                raise ValueError("invalid recurrence cron")
            data["recurrence"] = recurrence
        else:
            data["recurrence"] = None
        silent = bool(data.get("silent", False))
        data["silent"] = silent
        delivery_mode_raw = str(data.get("delivery_mode") or "").strip().lower()
        allowed_delivery_modes = {"room_only", "room_and_inbox"}
        if delivery_mode_raw not in allowed_delivery_modes:
            delivery_mode_raw = "room_only" if silent else "room_and_inbox"
        if silent and delivery_mode_raw != "room_only":
            logger.debug(
                "L4.tools [tasks] - Forcing delivery_mode=room_only for silent task %s (was %s)",
                task_id,
                delivery_mode_raw,
            )
            delivery_mode_raw = "room_only"
        data["delivery_mode"] = delivery_mode_raw
        room_id = str(data.get("room_id") or "").strip()
        if not room_id:
            room_id = _ensure_task_room(data["id"], data["name"], None)
        else:
            room_id = _ensure_task_room(data["id"], data["name"], room_id)
        data["room_id"] = room_id
        
        # Preserve source_room_type if provided (for tracking task creation origin)
        source_room_type = data.get("source_room_type")
        if source_room_type in ("standard", "task"):
            data["source_room_type"] = source_room_type
        else:
            data["source_room_type"] = "unknown"
        
        if not data.get("created_at"):
            data["created_at"] = datetime.now().isoformat()
        data.setdefault("last_executed", None)
        data.setdefault("execution_count", 0)
        
        # Error recovery fields
        data.setdefault("retry_count", 0)
        data.setdefault("last_error", None)
        data.setdefault("last_error_time", None)
        data.setdefault("max_retries", 5)  # Max retry attempts before marking as needs attention
        
        return data
    except Exception as exc:
        logger.error(f"L4.tools [tasks] - Failed to sanitize task {task_id}: {exc}")
        return None


def _sanitize_scheduled_message(message_id: str, payload: Any) -> Optional[Dict[str, Any]]:
    try:
        if not isinstance(payload, dict):
            raise ValueError("message payload must be object")
        data: Dict[str, Any] = dict(payload)
        data["id"] = str(data.get("id") or message_id)
        text = data.get("text")
        if not isinstance(text, str) or not text.strip():
            raise ValueError("text required")
        data["text"] = text
        delivery = str(data.get("delivery") or "default").lower()
        if delivery not in ("default", "inbox"):
            delivery = "default"
        data["delivery"] = delivery
        channel = data.get("channel_id")
        data["channel_id"] = str(channel).strip() if isinstance(channel, str) and channel.strip() else None
        schedule = data.get("start_time")
        if not schedule:
            raise ValueError("start_time required")
        if isinstance(schedule, datetime):
            start_dt = schedule
        else:
            start_dt = datetime.fromisoformat(str(schedule))
        data["start_time"] = start_dt.isoformat()
        recurrence = data.get("recurrence")
        if recurrence:
            recurrence = str(recurrence).strip()
            if not _validate_cron_expression(recurrence):
                raise ValueError("invalid recurrence cron")
            data["recurrence"] = recurrence
        else:
            data["recurrence"] = None
        status = str(data.get("status") or "active")
        data["status"] = status
        data.setdefault("created_at", datetime.now().isoformat())
        data.setdefault("last_sent", None)
        data.setdefault("send_count", 0)
        attachments = data.get("attachments")
        if attachments:
            if not isinstance(attachments, list):
                raise ValueError("attachments must be a list")
            sanitized_atts: List[Dict[str, Any]] = []
            for item in attachments:
                if not isinstance(item, dict):
                    continue
                sanitized_atts.append(
                    {
                        "filename": str(item.get("filename") or ""),
                        "url": str(item.get("url") or ""),
                        "content_type": str(item.get("content_type") or ""),
                        "size": int(item.get("size", 0) or 0),
                    }
                )
            data["attachments"] = sanitized_atts
        else:
            data["attachments"] = []
        data["original_text"] = str(data.get("original_text") or data.get("text") or "")
        return data
    except Exception as exc:
        logger.error(f"L4.tools [messages] - Failed to sanitize scheduled message {message_id}: {exc}")
        return None


def _schedule_task_retry(task_id: str, error_message: str, retry_count: int) -> Optional[datetime]:
    """Schedule a task retry with exponential backoff.
    
    Args:
        task_id: Task ID to retry
        error_message: Error message from the failed execution
        retry_count: Current retry count
        
    Returns:
        Next retry time if scheduled, None if max retries exceeded
    """
    try:
        tasks = _load_json_data(TASKS_FILE)
        task = tasks.get(task_id)
        if not task:
            logger.warning(f"L4.tools [retry] - Task {task_id} not found for retry")
            return None
        
        max_retries = task.get("max_retries", 5)
        
        # Check if we've exceeded max retries
        if retry_count >= max_retries:
            logger.error(
                f"L4.tools [retry] - Task {task_id} exceeded max retries ({max_retries}). "
                f"Marking as needs attention."
            )
            task["status"] = "needs_attention"
            task["last_error"] = error_message
            task["last_error_time"] = datetime.now().isoformat()
            tasks[task_id] = task
            _save_json_data(TASKS_FILE, tasks)
            
            # Create a notification for Theo to review this task
            try:
                from utils.message_queue import enqueue_task_message
                enqueue_task_message(
                    f"ALERT: Task '{task['name']}' (ID: {task_id}) has failed {retry_count} times and needs attention.\n\n"
                    f"Last error: {error_message}\n\n"
                    f"Please review the task logs and determine if:\n"
                    f"1. The task needs to be fixed/updated\n"
                    f"2. The task should be archived\n"
                    f"3. The underlying issue has been resolved and it can be retried",
                    source_id=f"task_alert_{task_id}",
                    silent=False,
                    room_id=task.get("room_id"),
                    metadata={
                        "task_id": task_id,
                        "delivery_mode": "room_and_inbox",
                        "silent": False,
                        "alert_type": "task_failure",
                    }
                )
            except Exception as alert_exc:
                logger.error(f"L4.tools [retry] - Failed to send alert for task {task_id}: {alert_exc}")
            
            return None
        
        # Calculate exponential backoff: 2^retry_count minutes (1min, 2min, 4min, 8min, 16min)
        backoff_minutes = 2 ** retry_count
        retry_time = datetime.now() + timedelta(minutes=backoff_minutes)
        
        # Update task metadata
        task["retry_count"] = retry_count
        task["last_error"] = error_message
        task["last_error_time"] = datetime.now().isoformat()
        tasks[task_id] = task
        _save_json_data(TASKS_FILE, tasks)
        
        # Schedule the retry
        scheduler = _get_scheduler()
        scheduler.add_job(
            func=_execute_task,
            trigger=DateTrigger(run_date=retry_time),
            args=[task_id],
            id=f"task_{task_id}_retry_{retry_count}",
            replace_existing=True,
        )
        
        logger.warning(
            f"L4.tools [retry] - Task {task_id} failed (attempt {retry_count}/{max_retries}). "
            f"Scheduled retry in {backoff_minutes} minutes at {retry_time.isoformat()}"
        )
        
        return retry_time
        
    except Exception as exc:
        logger.error(f"L4.tools [retry] - Error scheduling retry for task {task_id}: {exc}", exc_info=True)
        return None


def _execute_task(task_id: str):
    """Scheduler hook that enqueues task execution for Theo."""
    try:
        tasks = _load_json_data(TASKS_FILE)
        task = tasks.get(task_id)
        if not task:
            logger.warning(f"L4.tools [scheduler] - Task {task_id} not found")
            return

        sanitized = _sanitize_task_record(task_id, task)
        if not sanitized:
            return

        status = str(sanitized.get("status", "")).lower()
        if status == "needs_attention":
            logger.info(
                f"L4.tools [scheduler] - Skipping task {task_id} (status=needs_attention, requires manual review)"
            )
            return
        elif status != "active":
            logger.info(
                f"L4.tools [scheduler] - Skipping task {task_id} (status={status})"
            )
            return

        # Reset retry count on successful scheduled execution
        if sanitized.get("retry_count", 0) > 0:
            logger.info(f"L4.tools [scheduler] - Task {task_id} retry attempt successful, resetting retry count")
            sanitized["retry_count"] = 0
            sanitized["last_error"] = None
            sanitized["last_error_time"] = None

        sanitized["last_executed"] = datetime.now().isoformat()
        sanitized["execution_count"] = int(sanitized.get("execution_count", 0)) + 1
        if not sanitized.get("recurrence"):
            sanitized["status"] = "completed"

        tasks[task_id] = sanitized
        _save_json_data(TASKS_FILE, tasks)

        from utils.message_queue import enqueue_task_message

        metadata = {
            "task_id": task_id,
            "delivery_mode": sanitized.get("delivery_mode", "room_only"),
            "silent": bool(sanitized.get("silent", False)),
            "room_id": sanitized.get("room_id"),
            "retry_count": sanitized.get("retry_count", 0),
        }
        enqueue_task_message(
            f"Scheduled Task: '{sanitized['name']}'\n{sanitized['details']}",
            source_id=task_id,
            silent=bool(sanitized.get("silent", False)),
            room_id=sanitized.get("room_id"),
            metadata=metadata,
        )
    except Exception as exc:
        logger.error(f"L4.tools [scheduler] - Error executing task {task_id}: {exc}", exc_info=True)
        # Schedule retry on scheduler-level errors
        try:
            tasks = _load_json_data(TASKS_FILE)
            task = tasks.get(task_id, {})
            current_retry_count = task.get("retry_count", 0) + 1
            _schedule_task_retry(task_id, str(exc), current_retry_count)
        except Exception as retry_exc:
            logger.error(f"L4.tools [scheduler] - Failed to schedule retry for task {task_id}: {retry_exc}")


def _execute_scheduled_message(message_id: str):
    """Scheduler hook for manual messages scheduled via manually_send_message."""
    try:
        messages = _load_json_data(SCHEDULED_MESSAGES_FILE)
        record = messages.get(message_id)
        if not record:
            logger.warning(f"L4.tools [scheduler] - Scheduled message {message_id} not found")
            return

        sanitized = _sanitize_scheduled_message(message_id, record)
        if not sanitized:
            return

        if str(sanitized.get("status", "")).lower() != "active":
            logger.info(
                f"L4.tools [scheduler] - Skipping message {message_id} (status={sanitized.get('status')})"
            )
            return

        sanitized["last_sent"] = datetime.now().isoformat()
        sanitized["send_count"] = int(sanitized.get("send_count", 0)) + 1
        if not sanitized.get("recurrence"):
            sanitized["status"] = "completed"

        messages[message_id] = sanitized
        _save_json_data(SCHEDULED_MESSAGES_FILE, messages)

        from utils.message_queue import enqueue_manual_message

        enqueue_manual_message(
            sanitized["text"],
            delivery=sanitized.get("delivery", "default"),
            channel_id=sanitized.get("channel_id"),
            metadata={
                "scheduled_message_id": message_id,
                "delivery": sanitized.get("delivery", "default"),
                "attachments": sanitized.get("attachments", []),
                "original_text": sanitized.get("original_text"),
            },
        )
    except Exception as exc:
        logger.error(f"L4.tools [scheduler] - Error executing manual message {message_id}: {exc}", exc_info=True)


def _schedule_task_job(task: Dict[str, Any]) -> Tuple[bool, Optional[str]]:
    """Schedule a task job with APScheduler. Returns (success, error_message)."""
    scheduler = _get_scheduler()
    task_id = task["id"]
    recurrence = task.get("recurrence")
    try:
        if recurrence:
            # Use start_time as start_date for CronTrigger if in the future
            start_dt = datetime.fromisoformat(task["start_time"])
            start_date_param = None
            if start_dt > datetime.now():
                start_date_param = start_dt
                logger.debug(f"L4.tools [tasks] - Setting start_date={start_dt} for recurring task {task_id}")
            
            # Create trigger using from_crontab, then pass start_date to add_job
            trigger = CronTrigger.from_crontab(recurrence)
            scheduler.add_job(
                func=_execute_task,
                trigger=trigger,
                args=[task_id],
                id=f"task_{task_id}",
                replace_existing=True,
                next_run_time=start_date_param,  # Use next_run_time instead of start_date
            )
            logger.debug(f"L4.tools [tasks] - Scheduled recurring task {task_id} ({task['name']})")
        else:
            run_date = datetime.fromisoformat(task["start_time"])
            if run_date <= datetime.now():
                logger.info(f"L4.tools [tasks] - Skipping past one-time task {task_id} (scheduled for {run_date})")
                return False, f"Task scheduled for past time: {run_date}"
            scheduler.add_job(
                func=_execute_task,
                trigger=DateTrigger(run_date=run_date),
                args=[task_id],
                id=f"task_{task_id}",
                replace_existing=True,
            )
            logger.debug(f"L4.tools [tasks] - Scheduled one-time task {task_id} for {run_date}")
        return True, None
    except Exception as exc:
        error_msg = f"Failed to schedule task {task_id}: {exc}"
        logger.warning(f"L4.tools [tasks] - {error_msg}")
        return False, error_msg


def _schedule_scheduled_message_job(message: Dict[str, Any]) -> None:
    scheduler = _get_scheduler()
    message_id = message["id"]
    recurrence = message.get("recurrence")
    try:
        if recurrence:
            # Use start_time as start_date for CronTrigger if in the future
            start_dt = datetime.fromisoformat(message["start_time"])
            start_date_param = None
            if start_dt > datetime.now():
                start_date_param = start_dt
                logger.debug(f"L4.tools [messages] - Setting start_date={start_dt} for recurring message {message_id}")
            
            # Create trigger using from_crontab, then pass start_date to add_job
            trigger = CronTrigger.from_crontab(recurrence)
            scheduler.add_job(
                func=_execute_scheduled_message,
                trigger=trigger,
                args=[message_id],
                id=f"message_{message_id}",
                replace_existing=True,
                next_run_time=start_date_param,  # Use next_run_time instead of start_date
            )
            logger.debug(f"L4.tools [messages] - Scheduled recurring message {message_id}")
        else:
            run_date = datetime.fromisoformat(message["start_time"])
            if run_date <= datetime.now():
                return
            scheduler.add_job(
                func=_execute_scheduled_message,
                trigger=DateTrigger(run_date=run_date),
                args=[message_id],
                id=f"message_{message_id}",
                replace_existing=True,
            )
            logger.debug(f"L4.tools [messages] - Scheduled one-time message {message_id} for {run_date}")
    except Exception as exc:
        logger.warning(f"L4.tools [messages] - Failed to schedule message {message_id}: {exc}")

def schedule_manual_message(
    text: str,
    delivery: str = "default",
    start_time: Optional[str] = None,
    recurrence: Optional[str] = None,
    channel_id: Optional[str] = None,
    *,
    attachments: Optional[List[Dict[str, Any]]] = None,
    original_text: Optional[str] = None,
) -> Dict[str, Any]:
    """Create, persist, and schedule a manual message."""
    if not isinstance(text, str) or not text.strip():
        raise ValueError("text is required")
    if not start_time:
        raise ValueError("start_time is required for scheduled messages")

    delivery_normalized = str(delivery or "default").lower().strip()
    if delivery_normalized not in ("default", "inbox"):
        delivery_normalized = "default"

    try:
        start_dt = _parse_time_string(start_time)
    except ValueError as exc:
        raise ValueError(str(exc))

    if recurrence and not _validate_cron_expression(recurrence):
        raise ValueError("invalid cron expression")

    # For recurring messages, calculate and validate next occurrence
    next_run_info = None
    if recurrence:
        next_occurrence = _calculate_next_occurrence(recurrence, datetime.now())
        if next_occurrence:
            hours_until_next = (next_occurrence - datetime.now()).total_seconds() / 3600
            next_run_info = {
                "next_occurrence": next_occurrence.isoformat(),
                "hours_until_next": round(hours_until_next, 1),
            }
            
            # Warn if next occurrence is more than 48 hours away
            if hours_until_next > 48:
                logger.warning(
                    f"L4.tools [messages] - Next occurrence for scheduled message is {hours_until_next:.1f} hours away: {next_occurrence}"
                )

    messages = _load_json_data(SCHEDULED_MESSAGES_FILE)
    message_id = str(uuid.uuid4())[:8]

    record = {
        "id": message_id,
        "text": text,
        "delivery": delivery_normalized,
        "channel_id": channel_id,
        "start_time": start_dt.isoformat(),
        "recurrence": recurrence,
        "status": "active",
        "created_at": datetime.now().isoformat(),
        "last_sent": None,
        "send_count": 0,
        "attachments": attachments if isinstance(attachments, list) else [],
        "original_text": original_text if isinstance(original_text, str) else str(text or ""),
    }

    sanitized = _sanitize_scheduled_message(message_id, record)
    if not sanitized:
        raise ValueError("failed to sanitize scheduled message")

    messages[message_id] = sanitized
    _save_json_data(SCHEDULED_MESSAGES_FILE, messages)
    _schedule_scheduled_message_job(sanitized)
    
    # Add next run info to return value for recurring messages
    if next_run_info:
        sanitized["_next_run_info"] = next_run_info
    
    return sanitized







def _validate_cron_expression(cron_expr: str) -> bool:
    """Validate cron expression format and ensure it's reasonable."""
    try:
        # Basic validation with croniter
        cron = croniter(cron_expr)
        
        # Additional validation: ensure cron has valid fields
        parts = cron_expr.strip().split()
        if len(parts) != 5:
            logger.warning(f"L4.tools [scheduler] - Invalid cron field count: {len(parts)} (expected 5)")
            return False
        
        # Check for common mistakes like using seconds (6 fields) or year (7 fields)
        if any('?' in part for part in parts):
            logger.warning(f"L4.tools [scheduler] - Invalid character '?' in cron expression")
            return False
            
        # Validate that the cron will actually trigger in the future
        next_run = cron.get_next(datetime)
        if next_run > datetime.now() + timedelta(days=365*10):
            logger.warning(f"L4.tools [scheduler] - Cron expression would not trigger for 10+ years")
            return False
            
        return True
    except (ValueError, TypeError) as e:
        logger.warning(f"L4.tools [scheduler] - Invalid cron expression '{cron_expr}': {e}")
        return False


def _calculate_next_occurrence(recurrence: str, start_from: Optional[datetime] = None) -> Optional[datetime]:
    """Calculate the next occurrence of a cron expression from a given time.
    
    Args:
        recurrence: 5-field cron expression
        start_from: Starting point (defaults to now)
    
    Returns:
        Next occurrence datetime, or None if calculation fails
    """
    try:
        from_dt = start_from or datetime.now()
        cron = croniter(recurrence, from_dt)
        return cron.get_next(datetime)
    except Exception as exc:
        logger.warning(f"L4.tools [scheduler] - Failed to calculate next occurrence: {exc}")
        return None


# === PROJECT MANAGEMENT TOOLS ===



def _load_json_data(filepath: Path) -> Dict[str, Any]:
    """Load JSON data from file, create empty dict if file doesn't exist."""
    try:
        if filepath.exists():
            with open(filepath, "r", encoding="utf-8") as f:
                data = json.load(f)
                if isinstance(data, list):
                    logger.warning(f"L4.tools [vault] - Converting {filepath.name} from array to object format")
                    if len(data) == 0:
                        data = {}
                    else:
                        logger.warning(f"L4.tools [vault] - Resetting {filepath.name} due to format change")
                        data = {}
                logger.debug(f"L4.tools [vault] - Loaded {filepath.name} ({len(data)} items)")
                return data
        else:
            logger.debug(f"L4.tools [vault] - Creating new {filepath.name}")
            return {}
    except (json.JSONDecodeError, IOError) as e:
        logger.error(f"L4.tools [vault] - Failed to load {filepath.name}: {e}")
        try:
            backup_path = filepath.with_suffix(filepath.suffix + ".bak")
            if backup_path.exists():
                with open(backup_path, "r", encoding="utf-8") as bf:
                    data = json.load(bf)
                tmp_path = filepath.with_suffix(filepath.suffix + ".tmp")
                with open(tmp_path, "w", encoding="utf-8") as tf:
                    json.dump(data, tf, indent=2, ensure_ascii=False, default=str)
                    tf.flush()
                    os.fsync(tf.fileno())
                os.replace(tmp_path, filepath)
                logger.warning(f"L4.tools [vault] - Restored {filepath.name} from backup {backup_path.name}")
                return data
        except Exception as restore_err:
            logger.error(f"L4.tools [vault] - Backup restore failed for {filepath.name}: {restore_err}", exc_info=True)
        return {}


def _save_json_data(filepath: Path, data: Dict[str, Any]) -> bool:
    """Save JSON data to file atomically with backup."""
    tmp_path = None
    try:
        filepath.parent.mkdir(parents=True, exist_ok=True)
        tmp_fd, tmp_path = tempfile.mkstemp(dir=str(filepath.parent), prefix=filepath.name + ".", suffix=".tmp")
        try:
            with os.fdopen(tmp_fd, "w", encoding="utf-8") as tf:
                json.dump(data, tf, indent=2, ensure_ascii=False, default=str)
                tf.flush()
                os.fsync(tf.fileno())
        except Exception:
            try:
                if tmp_path and os.path.exists(tmp_path):
                    os.unlink(tmp_path)
            except Exception:
                pass
            raise
        try:
            if filepath.exists():
                backup_path = filepath.with_suffix(filepath.suffix + ".bak")
                shutil.copy2(filepath, backup_path)
        except Exception as be:
            logger.warning(f"L4.tools [vault] - Failed to write backup for {filepath.name}: {be}")
        os.replace(tmp_path, filepath)
        try:
            dir_fd = os.open(str(filepath.parent), os.O_DIRECTORY)
            try:
                os.fsync(dir_fd)
            finally:
                os.close(dir_fd)
        except Exception:
            pass
        try:
            backup_path = filepath.with_suffix(filepath.suffix + ".bak")
            shutil.copy2(filepath, backup_path)
        except Exception as be2:
            logger.warning(f"L4.tools [vault] - Failed to refresh backup for {filepath.name}: {be2}")
        logger.debug(f"L4.tools [vault] - Saved {filepath.name} ({len(data)} items) atomically")
        return True
    except (IOError, OSError, TypeError, ValueError) as e:
        logger.error(f"L4.tools [vault] - Failed to save {filepath.name}: {e}", exc_info=True)
        return False


def _suggest_time_format_fix(time_str: str) -> str:
    """Suggest fixes for common time format mistakes."""
    s = time_str.strip().lower()
    
    # Check for abbreviated units without spaces
    if re.match(r'^\d+m$', s):
        return f"Did you mean '{s[:-1]} minutes'?"
    if re.match(r'^\d+h$', s):
        return f"Did you mean '{s[:-1]} hours'?"
    if re.match(r'^\d+d$', s):
        return f"Did you mean '{s[:-1]} days'?"
    
    return ""


def _parse_time_string(time_str: str) -> datetime:
    """Parse time string into datetime object. Always returns timezone-naive datetime."""
    try:
        s = (time_str or "").strip()
        if s.lower() == "now":
            return datetime.now() + timedelta(seconds=5)
        cron_like = re.match(r"^\s*[^\s]+\s+[^\s]+\s+[^\s]+\s+[^\s]+\s+[^\s]+\s*$", s)
        if cron_like:
            raise ValueError(f"Invalid time format: '{s}' looks like a cron expression; pass it as 'recurrence' instead")
        try:
            from datetime import datetime as _dt
            parsed_iso = _dt.fromisoformat(s)
            # Strip timezone info to ensure naive datetime for scheduler compatibility
            # APScheduler handles timezone conversions internally using the configured scheduler timezone
            if parsed_iso.tzinfo is not None:
                parsed_iso = parsed_iso.replace(tzinfo=None)
            return parsed_iso
        except Exception:
            pass
        ampm = re.match(r"^(\d{1,2}):(\d{2})\s*([aApP][mM])(?:\s+[\w\-/]+)?$", s)
        if ampm:
            hour = int(ampm.group(1)) % 12
            minute = int(ampm.group(2))
            if ampm.group(3).lower() == "pm":
                hour += 12
            now = datetime.now()
            dt = now.replace(hour=hour, minute=minute, second=0, microsecond=0)
            if dt <= now:
                dt = dt + timedelta(days=1)
            return dt
        formats = ["%Y-%m-%d %H:%M:%S", "%Y-%m-%d %H:%M", "%Y-%m-%d", "%H:%M:%S", "%H:%M"]
        for fmt in formats:
            try:
                parsed = datetime.strptime(s, fmt)
                if fmt in ["%H:%M:%S", "%H:%M"]:
                    now = datetime.now()
                    parsed = parsed.replace(year=now.year, month=now.month, day=now.day)
                    if parsed < now:
                        parsed += timedelta(days=1)
                return parsed
            except ValueError:
                continue
        lowered = s.lower().strip()
        
        # Handle "X from now" format first (most common case)
        if "from now" in lowered:
            match = re.search(r'(\d+)\s*from now', lowered)
            if match:
                # Default to minutes if no unit specified
                minutes = int(match.group(1))
                return datetime.now() + timedelta(minutes=minutes)
        
        if "min" in lowered:
            # Extract number before "min" with better regex
            match = re.search(r'(\d+)\s*(?:minutes?|min)', lowered)
            if match:
                minutes = int(match.group(1))
                return datetime.now() + timedelta(minutes=minutes)
        if "hour" in lowered:
            # Extract number before "hour" with better regex
            match = re.search(r'(\d+)\s*(?:hours?|hrs?)', lowered)
            if match:
                hours = int(match.group(1))
                return datetime.now() + timedelta(hours=hours)
        if "day" in lowered:
            # Extract number before "day" with better regex
            match = re.search(r'(\d+)\s*days?', lowered)
            if match:
                days = int(match.group(1))
                return datetime.now() + timedelta(days=days)
        if "sec" in lowered or "second" in lowered or (re.match(r'^\d+s$', lowered)):
            # Extract number before "sec" with better regex
            match = re.search(r'(\d+)\s*(?:seconds?|sec)', lowered)
            if match:
                seconds = int(match.group(1))
                return datetime.now() + timedelta(seconds=seconds)
            # Handle "Xs" format
            match = re.match(r'^(\d+)s$', lowered)
            if match:
                seconds = int(match.group(1))
                return datetime.now() + timedelta(seconds=seconds)
        
        # Generate helpful error message with examples
        suggestion = _suggest_time_format_fix(s)
        error_msg = (
            f"Unrecognized time format: '{s}'\n\n"
            f"Supported formats:\n"
            f"  - ISO 8601: '2025-10-20T18:00:00' or '2025-10-20 18:00:00'\n"
            f"  - Date: '2025-10-20'\n"
            f"  - Time: '18:00' or '6:00 PM'\n"
            f"  - Relative: '30 seconds', '2 minutes', '1 hour', '3 days'\n"
            f"  - Immediate: 'now'"
        )
        if suggestion:
            error_msg += f"\n\n{suggestion}"
        raise ValueError(error_msg)
    except (ValueError, TypeError) as e:
        logger.error(f"L4.tools [scheduler] - Time parsing failed for '{time_str}': {e}")
        raise


# === TASK MANAGEMENT TOOLS ===


def create_task(
    name: str,
    details: str,
    start_time: str,
    recurrence: Optional[str] = None,
    silent: bool = False,
    room_id: Optional[str] = None,
    delivery_mode: Optional[str] = "auto",
) -> Tuple[str, Dict[str, Any]]:
    """Create a scheduled task with flexible room routing.
    
    Room routing behavior (room_id parameter):
      - None (default): Smart context preservation
          * From standard room → duplicate room (isolates task context)
          * From task room → same room (chains related tasks)
          * No origin → new empty task room
      - "same": Output to current room (standard or task)
      - "new_room": Create new empty task room
      - "<room_id>": Output to specific room by ID
    
    Standard rooms hosting tasks remain standard (visible in sidebar).
    Task outputs appear as regular messages in the designated room.
    """
    try:
        logger.info(f"L4.tools [tool:create_task] - Creating task: {name}")

        if not name or not details or not start_time:
            return "ERROR: 'name', 'details', and 'start_time' are required", {
                "success": False,
                "error": "missing_required_fields",
                "task_id": None,
            }

        # Validate name and details are non-empty
        if not name.strip():
            return "ERROR: Task name cannot be empty", {
                "success": False,
                "error": "empty_name",
                "task_id": None,
            }

        if not details.strip():
            return "ERROR: Task details cannot be empty", {
                "success": False,
                "error": "empty_details",
                "task_id": None,
            }

        try:
            start_datetime = _parse_time_string(start_time)
        except ValueError as exc:
            return f"ERROR: {exc}", {
                "success": False,
                "error": str(exc),
                "task_id": None,
            }

        if recurrence and not _validate_cron_expression(recurrence):
            return f"ERROR: Invalid recurrence cron expression: {recurrence}", {
                "success": False,
                "error": "invalid_cron",
                "task_id": None,
            }

        # For recurring tasks, calculate and validate next occurrence
        next_run_info = None
        if recurrence:
            next_occurrence = _calculate_next_occurrence(recurrence, datetime.now())
            if next_occurrence:
                hours_until_next = (next_occurrence - datetime.now()).total_seconds() / 3600
                next_run_info = {
                    "next_occurrence": next_occurrence.isoformat(),
                    "hours_until_next": round(hours_until_next, 1),
                }
                
                # Warn if next occurrence is more than 48 hours away
                if hours_until_next > 48:
                    logger.warning(
                        f"L4.tools [tasks] - Next occurrence for task '{name}' is {hours_until_next:.1f} hours away: {next_occurrence}"
                    )

        tasks = _load_json_data(TASKS_FILE)
        task_id = str(uuid.uuid4())[:8]

        # Determine the current/source room
        origin_room: Optional[str] = None
        try:
            origin_room = get_current_room_id(None)
        except Exception:
            origin_room = None
        if not origin_room:
            try:
                from utils.active_room import get_active_room as _get_active
                origin_room = _get_active()
            except Exception:
                origin_room = None

        # Load room metadata to determine source room type
        try:
            from utils.rooms_meta import load as _load_meta
            room_meta = _load_meta()
        except Exception:
            room_meta = {}
        
        source_room_type = "unknown"
        if origin_room:
            if isinstance(room_meta.get(origin_room), dict):
                meta_type = room_meta[origin_room].get("room_type")
                if meta_type in ("standard", "task"):
                    source_room_type = meta_type
                elif origin_room.startswith("task") or origin_room.startswith("r"):
                    # Infer from room ID pattern if metadata is missing or invalid
                    source_room_type = "task" if origin_room.startswith("task") else "standard"
            elif origin_room.startswith("task") or origin_room.startswith("r"):
                # Infer from room ID pattern if no metadata entry
                source_room_type = "task" if origin_room.startswith("task") else "standard"
        
        # Parse room_id parameter for special commands or explicit room
        room_id_param = str(room_id or "").strip().lower() if room_id else ""
        
        # Debug logging for room assignment troubleshooting
        logger.debug(f"L4.tools [create_task] - Room assignment debug: origin_room={origin_room}, source_room_type={source_room_type}, room_id_param='{room_id_param}', room_id_raw={room_id}")
        
        # Determine task room based on assignment logic
        task_room = None
        room_action = "unknown"
        
        if room_id_param == "new_room":
            # Explicit: create new empty task room
            task_room = _ensure_task_room(task_id, name, None)
            room_action = "new_empty"
            logger.info(f"L4.tools [create_task] - Created new empty task room: {task_room}")
            
        elif room_id_param == "same":
            # Explicit: reuse current room
            if origin_room:
                task_room = _ensure_task_room(task_id, name, origin_room)
                room_action = "reused_explicit"
                logger.info(f"L4.tools [create_task] - Explicitly reusing current room: {task_room}")
            else:
                # No current room, create new
                task_room = _ensure_task_room(task_id, name, None)
                room_action = "new_empty"
                logger.warning(f"L4.tools [create_task] - 'same' requested but no current room, created new: {task_room}")
                
        elif room_id_param and room_id_param not in ("", "none", "null"):
            # Explicit room ID provided (original case preserved from room_id parameter)
            explicit_room = str(room_id).strip()
            task_room = _ensure_task_room(task_id, name, explicit_room)
            room_action = "explicit_id"
            logger.info(f"L4.tools [create_task] - Using explicit room: {task_room}")
            
        else:
            # Default logic based on source room type
            if source_room_type == "standard" and origin_room:
                # Standard room → duplicate by default
                task_room = _duplicate_room_for_task(origin_room, task_id, name)
                room_action = "duplicated"
                logger.info(f"L4.tools [create_task] - Duplicated standard room {origin_room} → {task_room}")
                
            elif source_room_type == "task" and origin_room:
                # Task room → reuse same room by default
                # This ensures sub-tasks stay correlated with their parent task
                task_room = _ensure_task_room(task_id, name, origin_room)
                room_action = "reused_default"
                logger.info(
                    f"L4.tools [create_task] - Sub-task '{name}' inheriting parent task room: {task_room}. "
                    f"This ensures task chain isolation (parent room: {origin_room})"
                )
                
            else:
                # Unknown source or no origin room → create new task room
                task_room = _ensure_task_room(task_id, name, None)
                room_action = "new_empty"
                logger.info(f"L4.tools [create_task] - No valid source room, created new: {task_room}")
        
        delivery_mode_value = str(delivery_mode or "auto").strip().lower()
        allowed_delivery_modes = {"auto", "room_only", "room_and_inbox"}
        if delivery_mode_value not in allowed_delivery_modes:
            delivery_mode_value = "auto"
        if delivery_mode_value == "auto":
            resolved_delivery = "room_only" if silent else "room_and_inbox"
        else:
            resolved_delivery = delivery_mode_value
        if silent and resolved_delivery != "room_only":
            logger.warning(
                "L4.tools [create_task] - Silent task '%s' requested delivery_mode=%s; forcing room_only",
                name,
                resolved_delivery,
            )
            resolved_delivery = "room_only"

        task_data = {
            "id": task_id,
            "name": name,
            "details": details,
            "start_time": start_datetime.isoformat(),
            "recurrence": recurrence,
            "silent": bool(silent),
            "delivery_mode": resolved_delivery,
            "room_id": task_room,
            "source_room_type": source_room_type,
            "status": "active",
            "created_at": datetime.now().isoformat(),
            "last_executed": None,
            "execution_count": 0,
        }

        sanitized = _sanitize_task_record(task_id, task_data)
        if not sanitized:
            return f"ERROR: Failed to persist task {task_id}", {
                "success": False,
                "error": "sanitize_failed",
                "task_id": task_id,
            }

        tasks[task_id] = sanitized
        _save_json_data(TASKS_FILE, tasks)

        # Attempt to schedule the task job with APScheduler
        # Check if job already exists (can happen if scheduler was just initialized and restored all tasks)
        scheduler = _get_scheduler()
        if scheduler is None:
            # Scheduler is disabled (web process) - worker will pick up the task from disk
            logger.info(f"L4.tools [create_task] - Scheduler disabled, task {task_id} saved to disk for worker to schedule")
            schedule_success = True
            schedule_error = None
        else:
            job_id = f"task_{task_id}"
            existing_job = scheduler.get_job(job_id)
            
            if existing_job:
                logger.info(f"L4.tools [create_task] - Task {task_id} already scheduled (job exists), skipping duplicate schedule")
                schedule_success = True
                schedule_error = None
            else:
                schedule_success, schedule_error = _schedule_task_job(sanitized)
        
        if not schedule_success:
            # Remove the task from storage since scheduling failed
            tasks.pop(task_id, None)
            _save_json_data(TASKS_FILE, tasks)
            return f"ERROR: Task created but failed to schedule: {schedule_error}", {
                "success": False,
                "error": "schedule_failed",
                "schedule_error": schedule_error,
                "task_id": task_id,
            }

        # Build informative response for Theo
        response_text = f"Created task '{name}' (ID: {task_id})"
        if room_action == "duplicated":
            response_text += f"\n\nI created that task which copied these chats over into a new room! If you want any changes, pop over to the tasks view and open that tasks room!"
        elif room_action == "reused_default":
            response_text += f"\n\nTask will run in the same room ({task_room}) as it was created in, keeping all context together."
        elif room_action == "reused_explicit":
            response_text += f"\n\nExplicitly reusing current room {task_room} for this task."
        elif room_action == "explicit_id":
            response_text += f"\n\nUsing specified room {task_room} for this task."
        elif room_action == "new_empty":
            response_text += f"\n\nCreated new empty task room {task_room} for this task."

        # Add next occurrence info for recurring tasks
        if next_run_info:
            response_text += f"\n\n⚠️ Next occurrence: {next_run_info['next_occurrence']} ({next_run_info['hours_until_next']} hours from now)"
            if next_run_info['hours_until_next'] > 48:
                response_text += "\nNOTE: Next run is more than 48 hours away. If you expected it sooner, check your cron expression."

        return response_text, {
            "success": True,
            "task_id": task_id,
            "task_name": sanitized.get("name", name),
            "room_id": sanitized.get("room_id"),
            "source_room_type": source_room_type,
            "room_action": room_action,
            "delivery_mode": sanitized.get("delivery_mode"),
            "silent": sanitized.get("silent"),
        }

    except Exception as exc:
        logger.error(f"L4.tools [tool:create_task] - Error creating task: {exc}", exc_info=True)
        return f"ERROR: Failed to create task: {exc}", {
            "success": False,
            "error": str(exc),
            "task_id": None,
        }



def update_task(task_id: str, **fields) -> Tuple[str, Dict[str, Any]]:
    """Update task metadata and reschedule as needed."""
    try:
        tasks = _load_json_data(TASKS_FILE)
        if task_id not in tasks:
            # List available task IDs to help find the right one
            available_ids = [tid for tid, t in tasks.items() if t.get("status") == "active"][:5]
            if available_ids:
                id_list = ", ".join(available_ids)
                return f"ERROR: Task not found: {task_id}\n\nAvailable active task IDs: {id_list}", {
                    "success": False,
                    "error": "task_not_found",
                    "task_id": task_id,
                    "available_ids": available_ids,
                }
            return f"ERROR: Task not found: {task_id}", {
                "success": False,
                "error": "task_not_found",
                "task_id": task_id,
            }

        record = dict(tasks[task_id])
        original_silent = bool(record.get("silent", False))
        original_delivery = record.get("delivery_mode")
        allowed = {"name", "details", "start_time", "recurrence", "status", "silent", "delivery_mode", "room_id"}
        changed: Dict[str, tuple[Any, Any]] = {}

        for key, value in fields.items():
            if key not in allowed:
                continue
            before = record.get(key)
            if key == "start_time" and value is not None:
                try:
                    record["start_time"] = _parse_time_string(str(value)).isoformat()
                except ValueError as exc:
                    return f"ERROR: {exc}", {
                        "success": False,
                        "error": str(exc),
                        "task_id": task_id,
                    }
            elif key == "recurrence" and value:
                if not _validate_cron_expression(str(value)):
                    return f"ERROR: Invalid recurrence cron expression: {value}", {
                        "success": False,
                        "error": "invalid_cron",
                        "task_id": task_id,
                    }
                record["recurrence"] = str(value)
            elif key == "silent":
                record["silent"] = bool(value)
            elif key == "delivery_mode":
                record["delivery_mode"] = str(value) if value else None
            elif key == "status":
                old_status = str(before or "").lower()
                new_status = str(value or "").lower()
                record["status"] = value
                # Reset retry count when moving from needs_attention back to active
                if old_status == "needs_attention" and new_status == "active":
                    logger.info(f"L4.tools [update_task] - Task {task_id} status changed from needs_attention to active, resetting retry count")
                    record["retry_count"] = 0
                    record["last_error"] = None
                    record["last_error_time"] = None
            else:
                record[key] = value
            changed[key] = (before, record.get(key))

        if not changed:
            return "ERROR: No valid fields provided", {
                "success": False,
                "error": "no_valid_fields",
                "task_id": task_id,
            }

        record.setdefault("silent", False)
        new_silent = bool(record.get("silent", False))
        delivery_explicit = "delivery_mode" in fields
        allowed_delivery_modes = {"room_only", "room_and_inbox"}
        delivery_raw = str(record.get("delivery_mode") or "").strip().lower()
        if delivery_raw not in allowed_delivery_modes:
            delivery_raw = None

        if new_silent and delivery_explicit and delivery_raw == "room_and_inbox":
            logger.info(
                "L4.tools [update_task] - Silent task %s cannot use room_and_inbox; forcing room_only",
                task_id,
            )
            delivery_raw = "room_only"

        if delivery_explicit:
            resolved_delivery = delivery_raw or ("room_only" if new_silent else "room_and_inbox")
        else:
            if delivery_raw is None or ("silent" in fields and original_silent != new_silent):
                resolved_delivery = "room_only" if new_silent else "room_and_inbox"
            else:
                resolved_delivery = delivery_raw

        if new_silent and resolved_delivery != "room_only":
            resolved_delivery = "room_only"

        record["delivery_mode"] = resolved_delivery

        final_delivery = record.get("delivery_mode")
        if final_delivery != original_delivery:
            changed["delivery_mode"] = (original_delivery, final_delivery)

        room_id = record.get("room_id")
        record["room_id"] = _ensure_task_room(task_id, record.get("name", "Task"), room_id)

        sanitized = _sanitize_task_record(task_id, record)
        if not sanitized:
            return f"ERROR: Failed to sanitize task {task_id}", {
                "success": False,
                "error": "sanitize_failed",
                "task_id": task_id,
            }

        tasks[task_id] = sanitized
        _save_json_data(TASKS_FILE, tasks)

        scheduler = _get_scheduler()
        if scheduler is None:
            # Scheduler disabled - worker will handle rescheduling from disk
            logger.info(f"L4.tools [update_task] - Scheduler disabled, task {task_id} updated in disk for worker")
        else:
            job_id = f"task_{task_id}"
            try:
                scheduler.remove_job(job_id)
            except Exception:
                pass

            status = sanitized.get("status", "active")
            if status == "active":
                # Check if job already exists (can happen if scheduler was just initialized)
                existing_job = scheduler.get_job(job_id)
                if existing_job:
                    logger.info(f"L4.tools [update_task] - Task {task_id} already scheduled (job exists), skipping duplicate schedule")
                else:
                    _schedule_task_job(sanitized)

        delta_desc = ", ".join(f"{k}: {v0} -> {v1}" for k, (v0, v1) in changed.items())
        return f"Updated task {task_id}: {delta_desc}", {
            "success": True,
            "task_id": task_id,
            "updated_fields": {k: v1 for k, (_, v1) in changed.items()},
        }

    except Exception as exc:
        logger.error(f"L4.tools [tool:update_task] - Error updating task {task_id}: {exc}", exc_info=True)
        return f"ERROR: Failed to update task: {exc}", {
            "success": False,
            "error": str(exc),
            "task_id": task_id,
        }



def list_task(
    task_id: str = None,
    status: Optional[str] = None,
    include_recent_completed: bool = False,
    recent_completed_limit: int = 10,
) -> Tuple[str, Dict[str, Any]]:
    """
    List all active tasks, return information about a specific task, or expose
    recent completions on demand.

    Args:
        task_id: Optional specific task ID to get details for
        status: Optional status filter ("active", "completed", "archived", "all")
        include_recent_completed: When True, append a capped list of the most
            recently executed completed tasks (sorted by last_executed)
        recent_completed_limit: Maximum number of completed tasks to include

    Returns:
        Tuple of (result_text, tool_output)
    """
    try:
        logger.debug(
            f"L4.tools [tool:list_task] - Listing tasks (filter: {task_id})"
        )

        # Load existing tasks
        tasks = _load_json_data(TASKS_FILE)

        # Resolve task ID aliases (latest, last, etc.)
        if task_id and task_id.lower() in ["latest", "last", "last created", "last created id"]:
            if tasks:
                # Find the most recently created active task
                active_tasks = [(tid, t) for tid, t in tasks.items() if t.get("status") == "active"]
                if active_tasks:
                    # Sort by created_at timestamp and get the latest
                    task_id = max(active_tasks, key=lambda x: x[1].get('created_at', 0))[0]
                    logger.debug(
                        f"L4.tools [tool:list_task] - Resolved alias to task_id: {task_id}"
                    )
                else:
                    return "No active tasks found", {"success": False, "error": "no_active_tasks"}
            else:
                return "No tasks found", {"success": False, "error": "no_tasks"}

        if task_id:
            # Return specific task details
            if not tasks or task_id not in tasks:
                logger.error(
                    f"L4.tools [tool:list_task] - Task not found: {task_id}"
                )
                # List available task IDs to help find the right one
                available_ids = [tid for tid, t in tasks.items() if t.get("status") == "active"][:5]
                if available_ids:
                    id_list = ", ".join(available_ids)
                    return f"ERROR: Task not found: {task_id}\n\nAvailable active task IDs: {id_list}", {
                        "success": False,
                        "error": "Task not found",
                        "task_id": task_id,
                        "available_ids": available_ids,
                    }
                return f"ERROR: Task not found: {task_id}", {
                    "success": False,
                    "error": "Task not found",
                    "task_id": task_id,
                }

            task = tasks[task_id]
            start_time = datetime.fromisoformat(task["start_time"])
            result = f"""TASK: {task['name']} (ID: {task_id})
Status: {task['status']}
Details: {task['details']}
Start Time: {start_time.strftime('%Y-%m-%d %H:%M:%S')}
Recurrence: {task['recurrence'] if task['recurrence'] else 'None'}
Silent: {task['silent']}
Created: {task['created_at']}
Last Executed: {task.get('last_executed', 'Never')}
Execution Count: {task.get('execution_count', 0)}"""

            logger.debug(
                f"L4.tools [tool:list_task] - Retrieved task {task_id}"
            )
            return result, {
                "success": True,
                "task_id": task_id,
                "task_data": task,
            }
        # No task_id: produce list with optional filters and recent completions
        if not tasks:
            logger.debug(
                "L4.tools [tool:list_task] - No tasks found"
            )
            return "No tasks found.", {
                "success": True,
                "tasks": {},
                "count": 0,
            }

        allowed_status = {"active", "completed", "archived", "all"}
        status_filter = None
        if status is not None:
            status_filter = str(status).strip().lower()
            if status_filter not in allowed_status:
                logger.error(
                    f"L4.tools [tool:list_task] - Invalid status filter: {status}"
                )
                return (
                    "ERROR: Invalid status filter. Use one of: active, completed, archived, all.",
                    {
                        "success": False,
                        "error": "invalid_status",
                        "status": status,
                    },
                )

        # Enforce sane limits for completed slice
        try:
            recent_completed_limit = int(recent_completed_limit)
        except Exception:
            recent_completed_limit = 10
        if recent_completed_limit <= 0:
            recent_completed_limit = 1
        if recent_completed_limit > 50:
            recent_completed_limit = 50

        def _parse_dt(value: Optional[str]) -> datetime:
            if not value:
                raise ValueError("no datetime value")
            try:
                return datetime.fromisoformat(value)
            except Exception as exc:
                raise ValueError(str(exc))

        # Separate into buckets for formatting and output metadata
        active_tasks: Dict[str, Any] = {}
        completed_tasks: Dict[str, Any] = {}
        archived_tasks: Dict[str, Any] = {}

        for tid, task in tasks.items():
            try:
                status_value = str(task.get("status", "active")).strip().lower()
            except Exception:
                status_value = "active"

            if status_value == "completed":
                completed_tasks[tid] = task
            elif status_value == "archived":
                archived_tasks[tid] = task
            else:
                active_tasks[tid] = task

        # Decide which buckets to show based on requested status
        result_sections: List[str] = []
        tool_payload: Dict[str, Any] = {
            "success": True,
            "tasks": {},
            "count": 0,
        }

        def _append_section(title: str, items: Dict[str, Any]) -> None:
            nonlocal result_sections
            if not items:
                return
            result_sections.append(title)
            for item_id, item in items.items():
                start_value = item.get("start_time")
                try:
                    start_dt = _parse_dt(start_value)
                except Exception:
                    start_dt = None
                start_display = (
                    start_dt.strftime("%Y-%m-%d %H:%M")
                    if start_dt
                    else str(start_value or "Unknown")
                )
                result_sections.append(
                    f"- {item.get('name', 'Untitled')} (ID: {item_id}) - {item.get('status', 'unknown')} - {start_display}"
                )

        def _slice_recent_completed(items: Dict[str, Any]) -> List[Tuple[str, Dict[str, Any]]]:
            scored: List[Tuple[str, Dict[str, Any], datetime]] = []
            for item_id, item in items.items():
                last_exec = item.get("last_executed")
                fallback = item.get("start_time")
                dt_obj = None
                for candidate in (last_exec, fallback):
                    if not candidate:
                        continue
                    try:
                        dt_obj = _parse_dt(candidate)
                        break
                    except Exception:
                        continue
                if dt_obj is None:
                    dt_obj = datetime.min
                scored.append((item_id, item, dt_obj))

            scored.sort(key=lambda x: x[2], reverse=True)
            return [(tid, data) for tid, data, _ in scored[:recent_completed_limit]]

        if status_filter in (None, "active"):
            if active_tasks:
                _append_section("ACTIVE TASKS:", active_tasks)
                tool_payload["tasks"] = active_tasks
                tool_payload["count"] = len(active_tasks)
            else:
                result_sections.append("ACTIVE TASKS: (none)")
                tool_payload["tasks"] = {}
                tool_payload["count"] = 0

            if include_recent_completed and completed_tasks:
                sliced_completed = _slice_recent_completed(completed_tasks)
                section_dict = {tid: data for tid, data in sliced_completed}
                tool_payload["recent_completed"] = section_dict
                tool_payload["recent_completed_count"] = len(section_dict)
                result_sections.append("")
                result_sections.append("RECENTLY COMPLETED TASKS:")
                for tid, data in sliced_completed:
                    last_exec = data.get("last_executed")
                    try:
                        dt_obj = _parse_dt(last_exec)
                        timestamp = dt_obj.strftime("%Y-%m-%d %H:%M")
                    except Exception:
                        timestamp = str(last_exec or data.get("start_time") or "Unknown")
                    result_sections.append(
                        f"- {data.get('name', 'Untitled')} (ID: {tid}) - completed - {timestamp}"
                    )
            elif include_recent_completed:
                tool_payload["recent_completed"] = {}
                tool_payload["recent_completed_count"] = 0
                result_sections.append("")
                result_sections.append("RECENTLY COMPLETED TASKS: (none)")

        elif status_filter == "completed":
            if completed_tasks:
                _append_section("COMPLETED TASKS:", completed_tasks)
            else:
                result_sections.append("COMPLETED TASKS: (none)")
            tool_payload["tasks"] = completed_tasks
            tool_payload["count"] = len(completed_tasks)

        elif status_filter == "archived":
            if archived_tasks:
                _append_section("ARCHIVED TASKS:", archived_tasks)
            else:
                result_sections.append("ARCHIVED TASKS: (none)")
            tool_payload["tasks"] = archived_tasks
            tool_payload["count"] = len(archived_tasks)

        elif status_filter == "all":
            combined = {**active_tasks, **completed_tasks, **archived_tasks}
            if combined:
                _append_section("ALL TASKS:", combined)
            else:
                result_sections.append("ALL TASKS: (none)")
            tool_payload["tasks"] = combined
            tool_payload["count"] = len(combined)

        result = "\n".join([line for line in result_sections if line is not None])

        logger.info(
            "L4.tools [tool:list_task] - Listed tasks (status=%s, recent_completed=%s)",
            status_filter or "active",
            include_recent_completed,
        )

        return result or "No tasks found.", tool_payload

    except Exception as e:
        logger.error(
            f"L4.tools [tool:list_task] - Error listing tasks: {e}",
            exc_info=True,
        )
        return f"ERROR: Failed to list tasks: {str(e)}", {
            "success": False,
            "error": str(e),
            "task_id": task_id,
        }



# Export the main functions
__all__ = [
    "create_task",
    "update_task",
    "list_task",
]
