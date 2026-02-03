"""Working Memory tool implementations."""

from __future__ import annotations

from datetime import datetime
from typing import Any, Iterable, Optional, Sequence, Tuple

from layer3_longterm.theo_memory import add_memory as add_theo_memory
from layer5_features.working_memory import (
    WorkingMemoryAppendTooLong,
    WorkingMemoryDuplicateError,
    WorkingMemoryError,
    WorkingMemoryIndexError,
    WorkingMemoryItem,
    WorkingMemoryVersionMismatch,
    get_manager,
)
from layer5_features.working_memory.deadline_utils import (
    DeadlineParseError,
    parse_iso8601_deadline,
)
from utils.logger import get_logger

logger = get_logger(__name__)


class WorkingMemoryToolContextError(ValueError):
    """Raised when a working memory tool is invoked without conversation context."""


def _resolve_context(context: Optional[dict]) -> Tuple[str, Optional[str]]:
    if not isinstance(context, dict):
        raise WorkingMemoryToolContextError("Working memory tools require conversation context.")
    conversation_id = context.get("conversation_id")
    topic_id = context.get("topic_id")
    if not conversation_id:
        raise WorkingMemoryToolContextError("Working memory tools require a conversation identifier.")
    return str(conversation_id), str(topic_id) if topic_id is not None else None


def _find_display_index(items: Sequence[WorkingMemoryItem], item_id: str) -> int:
    for idx, item in enumerate(items, start=1):
        if item.item_id == item_id:
            return idx
    return -1


def _parse_deadline(
    deadline_str: Optional[str],
    default_tz: str = "America/Chicago",
) -> Optional[datetime]:
    """Parse deadline string to datetime, returning None if invalid or not provided."""
    if not deadline_str:
        return None
    try:
        return parse_iso8601_deadline(deadline_str, default_timezone=default_tz)
    except DeadlineParseError as err:
        logger.warning("Failed to parse deadline '%s': %s", deadline_str, err)
        raise WorkingMemoryError(f"Invalid deadline format: {err}") from err


def add_working_memory(
    content: str,
    *,
    tag: Optional[str] = None,
    ttl_exchanges: Optional[int] = None,
    pinned: Optional[bool] = None,
    pin_rank: Optional[int] = None,
    deadline_at: Optional[str] = None,
    remind_before: Optional[str] = None,
    deadline_type: Optional[str] = None,
    snooze_until: Optional[str] = None,
    context: Optional[dict] = None,
) -> Tuple[str, dict]:
    """Add a working memory note with optional pinning and deadline support."""
    try:
        conversation_id, topic_id = _resolve_context(context)
    except WorkingMemoryToolContextError as err:
        return str(err), {"success": False}

    # Extract run_id from context for regeneration tracking
    run_id = (context or {}).get("run_id")
    if isinstance(run_id, str):
        run_id = run_id.strip() or None
    else:
        run_id = None

    # Parse deadline timestamps
    try:
        deadline_dt = _parse_deadline(deadline_at)
        snooze_dt = _parse_deadline(snooze_until)
    except WorkingMemoryError as err:
        return str(err), {"success": False}

    manager = get_manager()
    try:
        item, store = manager.add(
            conversation_id,
            topic_id=topic_id,
            content=content,
            tag=tag,
            ttl=ttl_exchanges,
            pinned=pinned or False,
            pin_rank=pin_rank or 1,
            deadline_at=deadline_dt,
            remind_before=remind_before,
            deadline_type=deadline_type or "soft",
            snooze_until=snooze_dt,
            run_id=run_id,
        )
        items = store.list_items()
        index = _find_display_index(items, item.item_id)
        
        # Build response message
        msg_parts = [f"Added working memory item #{index}."]
        if item.pinned:
            msg_parts.append(f"Pinned (rank {item.pin_rank}).")
        if item.deadline_at:
            msg_parts.append(f"Deadline set.")
        
        message = " ".join(msg_parts)
        
        return message, {
            "success": True,
            "item_id": item.item_id,
            "wm_version": store.version,
            "index": index,
            "ttl_remaining": item.ttl_remaining,
            "pinned": item.pinned,
            "has_deadline": item.deadline_at is not None,
        }
    except WorkingMemoryDuplicateError as err:
        return str(err), {"success": False, "duplicate": True}
    except WorkingMemoryError as err:
        logger.debug("WM tool add error: %s", err)
        return str(err), {"success": False}
    except Exception as exc:  # pragma: no cover - defensive guard
        logger.error("WM tool add failure: %s", exc, exc_info=True)
        return "Working memory add failed due to an unexpected error.", {"success": False}


def remove_working_memory(
    index: int,
    *,
    wm_version: Optional[str] = None,
    context: Optional[dict] = None,
) -> Tuple[str, dict]:
    """Remove a working memory note by display index."""
    try:
        conversation_id, topic_id = _resolve_context(context)
    except WorkingMemoryToolContextError as err:
        return str(err), {"success": False}

    manager = get_manager()
    try:
        removed, store = manager.remove(
            conversation_id,
            topic_id=topic_id,
            index=int(index),
            expected_version=wm_version,
        )
        return f"Removed working memory item #{index}.", {
            "success": True,
            "item_id": removed.item_id,
            "wm_version": store.version,
        }
    except WorkingMemoryVersionMismatch as err:
        current_version = manager.get_version(conversation_id, topic_id=topic_id)
        payload: dict[str, Any] = {"success": False}
        if current_version is not None:
            payload["wm_version"] = current_version
        return str(err), payload
    except WorkingMemoryIndexError as err:
        return str(err), {"success": False}
    except WorkingMemoryError as err:
        logger.debug("WM tool remove error: %s", err)
        return str(err), {"success": False}
    except Exception as exc:  # pragma: no cover - defensive guard
        logger.error("WM tool remove failure: %s", exc, exc_info=True)
        return "Working memory remove failed due to an unexpected error.", {"success": False}


def update_working_memory(
    index: int,
    *,
    content: Optional[str] = None,
    append: Optional[str] = None,
    ttl_exchanges: Optional[int] = None,
    tag: Optional[str] = None,
    pinned: Optional[bool] = None,
    pin_rank: Optional[int] = None,
    deadline_at: Optional[str] = None,
    remind_before: Optional[str] = None,
    deadline_type: Optional[str] = None,
    snooze_until: Optional[str] = None,
    wm_version: Optional[str] = None,
    context: Optional[dict] = None,
) -> Tuple[str, dict]:
    """Update an existing working memory note with optional pinning and deadline support."""
    try:
        conversation_id, topic_id = _resolve_context(context)
    except WorkingMemoryToolContextError as err:
        return str(err), {"success": False}

    # Parse deadline timestamps
    try:
        deadline_dt = _parse_deadline(deadline_at) if deadline_at else None
        snooze_dt = _parse_deadline(snooze_until) if snooze_until else None
    except WorkingMemoryError as err:
        return str(err), {"success": False}

    manager = get_manager()
    try:
        item, store = manager.update(
            conversation_id,
            topic_id=topic_id,
            index=int(index),
            new_content=content,
            append=append,
            ttl=ttl_exchanges,
            tag=tag,
            pinned=pinned,
            pin_rank=pin_rank,
            deadline_at=deadline_dt,
            remind_before=remind_before,
            deadline_type=deadline_type,
            snooze_until=snooze_dt,
            expected_version=wm_version,
        )
        items = store.list_items()
        new_index = _find_display_index(items, item.item_id)
        
        # Build response message
        msg_parts = [f"Updated working memory item #{new_index}."]
        if item.pinned:
            msg_parts.append(f"Pinned (rank {item.pin_rank}).")
        if item.deadline_at:
            msg_parts.append(f"Deadline set.")
        
        response = " ".join(msg_parts)
        
        return response, {
            "success": True,
            "item_id": item.item_id,
            "wm_version": store.version,
            "index": new_index,
            "ttl_remaining": item.ttl_remaining,
            "pinned": item.pinned,
            "has_deadline": item.deadline_at is not None,
        }
    except WorkingMemoryVersionMismatch as err:
        current_version = manager.get_version(conversation_id, topic_id=topic_id)
        payload: dict = {"success": False}
        if current_version is not None:
            payload["wm_version"] = current_version
        return str(err), payload
    except WorkingMemoryIndexError as err:
        return str(err), {"success": False}
    except WorkingMemoryAppendTooLong as err:
        return str(err), {"success": False}
    except WorkingMemoryError as err:
        logger.debug("WM tool update error: %s", err)
        return str(err), {"success": False}
    except Exception as exc:  # pragma: no cover - defensive guard
        logger.error("WM tool update failure: %s", exc, exc_info=True)
        return "Working memory update failed due to an unexpected error.", {"success": False}


def snapshot_working_memory(
    *,
    importance: int,
    scope: str = "visible",
    tag: Optional[str] = None,
    indices: Optional[Iterable[int]] = None,
    title: Optional[str] = None,
    context: Optional[dict] = None,
) -> Tuple[str, dict]:
    """Persist selected working memory items into Theo memories."""
    try:
        conversation_id, topic_id = _resolve_context(context)
    except WorkingMemoryToolContextError as err:
        return str(err), {"success": False}

    manager = get_manager()
    safe_indices = None
    if indices is not None:
        try:
            safe_indices = [int(value) for value in indices]
        except Exception:
            return "Working memory indices must be integers.", {"success": False}

    try:
        items, store = manager.select_items(
            conversation_id,
            topic_id=topic_id,
            scope=scope,
            indices=safe_indices,
            tag=tag,
        )
    except WorkingMemoryIndexError as err:
        return str(err), {"success": False}
    except WorkingMemoryError as err:
        logger.debug("WM tool snapshot selection error: %s", err)
        return str(err), {"success": False}
    except Exception as exc:  # pragma: no cover - defensive guard
        logger.error("WM tool snapshot selection failure: %s", exc, exc_info=True)
        return "Failed to gather working memory items for snapshot.", {"success": False}

    if not items:
        return "No working memory items matched the selection.", {"success": False}

    clamped_importance = max(0, min(int(importance), 100))
    summary = _compose_snapshot_summary(items)
    memory_content = summary
    if title:
        title_clean = title.strip()
        memory_content = f"{title_clean}\n{summary}" if summary else title_clean

    metadata = {
        "source": "snapshot_working_memory",
        "conversation_id": conversation_id,
        "wm_item_ids": [item.item_id for item in items],
    }

    try:
        memory_id = add_theo_memory(memory_content, clamped_importance, metadata=metadata)
    except Exception as exc:  # pragma: no cover - defensive guard
        logger.error("WM tool snapshot persistence failure: %s", exc, exc_info=True)
        return "Failed to create Theo memory snapshot.", {"success": False}

    if not memory_id:
        return "Failed to create Theo memory snapshot.", {"success": False}

    return "Captured working memory snapshot.", {
        "success": True,
        "memory_id": memory_id,
        "wm_version": store.version,
        "captured_items": len(items),
    }


def _compose_snapshot_summary(items: Iterable[WorkingMemoryItem]) -> str:
    lines: list[str] = []
    total_chars = 0
    for item in items:
        text = item.content.strip()
        if not text:
            continue
        normalized = text.replace("\n", " / ")
        lines.append(f"- {normalized}")
        total_chars += len(normalized)

    if not lines:
        return ""

    if total_chars <= 600 and len(lines) <= 6:
        return "\n".join(lines)

    condensed: list[str] = []
    for item in items:
        base = item.content.strip().splitlines()[0]
        tag = item.tag or "note"
        snippet = base[:140] + ("…" if len(base) > 140 else "")
        condensed.append(f"- [{tag}] {snippet}")
    return "\n".join(condensed)
