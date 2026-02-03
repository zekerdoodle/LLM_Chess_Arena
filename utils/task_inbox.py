"""Task Inbox feed storage helpers."""

from __future__ import annotations

import json
import os
import uuid
from datetime import datetime
from pathlib import Path
from typing import Any, Dict, List, Optional, Set, Tuple

from utils.logger import get_logger
from utils.vault_paths import get_vault_root

logger = get_logger(__name__)

try:
    VAULT_ROOT = Path(get_vault_root())
except Exception:
    VAULT_ROOT = Path("vault")

INBOX_PATH = VAULT_ROOT / "task_inbox.json"
INBOX_READ_PATH = VAULT_ROOT / "task_inbox_read.json"
MAX_ENTRIES = 200


def _load_entries() -> List[Dict[str, Any]]:
    if not INBOX_PATH.exists():
        return []
    try:
        with open(INBOX_PATH, "r", encoding="utf-8") as f:
            data = json.load(f) or []
        if isinstance(data, list):
            return data
    except Exception as exc:
        logger.debug(f"task_inbox load failed: {exc}")
    return []


def _save_entries(entries: List[Dict[str, Any]]) -> None:
    try:
        INBOX_PATH.parent.mkdir(parents=True, exist_ok=True)
        temp_path = INBOX_PATH.with_suffix(".tmp")
        with open(temp_path, "w", encoding="utf-8") as f:
            json.dump(entries, f, ensure_ascii=False, indent=2)
        os.replace(temp_path, INBOX_PATH)
    except Exception as exc:
        logger.error(f"task_inbox save failed: {exc}")


def _load_read_ids() -> Set[str]:
    if not INBOX_READ_PATH.exists():
        return set()
    try:
        with open(INBOX_READ_PATH, "r", encoding="utf-8") as f:
            data = json.load(f) or []
        if isinstance(data, list):
            return {str(item) for item in data if isinstance(item, str) or isinstance(item, int)}
    except Exception as exc:
        logger.debug(f"task_inbox read load failed: {exc}")
    return set()


def _save_read_ids(read_ids: Set[str]) -> None:
    try:
        INBOX_READ_PATH.parent.mkdir(parents=True, exist_ok=True)
        payload = sorted({str(entry_id) for entry_id in read_ids if entry_id})
        temp_path = INBOX_READ_PATH.with_suffix(".tmp")
        with open(temp_path, "w", encoding="utf-8") as f:
            json.dump(payload, f, ensure_ascii=False, indent=2)
        os.replace(temp_path, INBOX_READ_PATH)
    except Exception as exc:
        logger.error(f"task_inbox read save failed: {exc}")


def append_inbox_entry(
    *,
    source: str,
    room_id: Optional[str],
    message_id: Optional[str],
    text: str,
    task_id: Optional[str],
    extra: Optional[Dict[str, Any]] = None,
) -> Dict[str, Any]:
    """Append an event to the inbox feed and return the stored entry."""
    entry = {
        "id": str(uuid.uuid4()),
        "source": source,
        "room_id": room_id,
        "message_id": message_id,
        "task_id": task_id,
        "text": text,
        "created_at": datetime.now().isoformat(),
    }
    if extra and isinstance(extra, dict):
        entry.update(extra)

    entries = _load_entries()
    entries.append(entry)
    if len(entries) > MAX_ENTRIES:
        entries = entries[-MAX_ENTRIES:]
    _save_entries(entries)
    return entry


def _entry_sort_key(entry: Dict[str, Any]) -> float:
    created = entry.get("created_at")
    if isinstance(created, str):
        try:
            return datetime.fromisoformat(created).timestamp()
        except Exception:
            pass
    return 0.0


def load_inbox_snapshot(limit: int = 50, include_read: bool = False) -> Tuple[List[Dict[str, Any]], int]:
    """Return inbox entries sorted newest-first (optionally unread only)."""
    entries = _load_entries()
    read_ids = _load_read_ids() if not include_read else set()

    deduped: List[Dict[str, Any]] = []
    seen: Set[str] = set()
    for entry in sorted(entries, key=_entry_sort_key, reverse=True):
        entry_id = str(entry.get("id") or "").strip()
        if not entry_id or entry_id in seen:
            continue
        if not include_read and entry_id in read_ids:
            continue
        seen.add(entry_id)
        deduped.append(entry)

    total = len(deduped)
    if limit and limit > 0:
        deduped = deduped[:limit]
    return deduped, total


def mark_inbox_entry_read(entry_id: str) -> Optional[Dict[str, Any]]:
    """Persist an inbox entry as read. Returns the entry if it exists."""
    if not entry_id:
        return None
    target_id = str(entry_id)
    entries = _load_entries()
    target = None
    for entry in entries:
        if str(entry.get("id") or "") == target_id:
            target = entry
            break
    if target is None:
        return None

    read_ids = _load_read_ids()
    if target_id not in read_ids:
        read_ids.add(target_id)
        _save_read_ids(read_ids)
        # Touch the inbox file so SSE watchers notice the change
        try:
            _save_entries(entries)
        except Exception:
            pass
    return target


__all__ = ["append_inbox_entry", "load_inbox_snapshot", "mark_inbox_entry_read", "INBOX_PATH", "INBOX_READ_PATH"]
