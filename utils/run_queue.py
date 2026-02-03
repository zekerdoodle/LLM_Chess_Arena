"""
Persistent run queue for worker-driven execution.

Each enqueued item: {"run_id","room_id","content","created_at","status":"pending"}
"""

from __future__ import annotations

import contextlib
import json
import os
import threading
import time
from pathlib import Path
from typing import Any, Dict, Iterator, List

from utils.logger import get_logger
from utils.vault_paths import get_vault_root

logger = get_logger(__name__)

_THREAD_LOCK = threading.Lock()
_QUEUE_CAP = 1000
_STALE_IN_PROGRESS_SECS = 600

try:  # pragma: no cover - platform specific
    import fcntl  # type: ignore
except ImportError:  # pragma: no cover - Windows fallback
    fcntl = None  # type: ignore


def _queue_path() -> Path:
    try:
        return Path(get_vault_root()) / "runs_queue.json"
    except Exception:
        return Path("vault/runs_queue.json")


def _ensure_parent(path: Path) -> None:
    try:
        path.parent.mkdir(parents=True, exist_ok=True)
    except Exception:
        pass


def _write_atomic(path: Path, payload: Dict[str, Any]) -> None:
    tmp_path = path.with_suffix(path.suffix + ".tmp")
    try:
        with open(tmp_path, "w", encoding="utf-8") as tmp:
            json.dump(payload, tmp, ensure_ascii=False, indent=2)
            try:
                tmp.flush()
                os.fsync(tmp.fileno())
            except Exception:
                pass
        os.replace(tmp_path, path)
    except Exception as exc:
        logger.error("run_queue: failed to persist queue file: %s", exc)
        try:
            if tmp_path.exists():
                tmp_path.unlink()
        except Exception:
            pass


def _sanitize_items(raw_items: Any) -> List[Dict[str, Any]]:
    if isinstance(raw_items, list):
        filtered: List[Dict[str, Any]] = []
        for entry in raw_items:
            if isinstance(entry, dict):
                filtered.append(dict(entry))
        return filtered
    return []


def _empty_payload() -> Dict[str, Any]:
    return {"items": []}


@contextlib.contextmanager
def _edit_queue() -> Iterator[Dict[str, Any]]:
    path = _queue_path()
    _ensure_parent(path)
    with _THREAD_LOCK:
        with open(path, "a+", encoding="utf-8") as handle:
            if fcntl is not None:  # pragma: no branch - only on POSIX
                fcntl.flock(handle, fcntl.LOCK_EX)
            try:
                handle.seek(0)
                raw = handle.read()
                if not raw.strip():
                    payload = _empty_payload()
                else:
                    try:
                        payload = json.loads(raw) or _empty_payload()
                    except Exception:
                        logger.error("run_queue: detected corrupted queue file, resetting")
                        payload = _empty_payload()
                payload["items"] = _sanitize_items(payload.get("items"))
                yield payload
                _write_atomic(path, payload)
            finally:
                if fcntl is not None:  # pragma: no cover - only on POSIX
                    try:
                        fcntl.flock(handle, fcntl.LOCK_UN)
                    except Exception:
                        pass


def enqueue(run_id: str, room_id: str, content: str) -> None:
    if not run_id:
        return
    now = int(time.time())
    with _edit_queue() as payload:
        items = payload.get("items", [])
        items.append(
            {
                "run_id": run_id,
                "room_id": room_id,
                "content": content,
                "status": "pending",
                "created_at": now,
            }
        )
        payload["items"] = items[-_QUEUE_CAP:]


def dequeue(limit: int = 5) -> List[Dict[str, Any]]:
    if limit <= 0:
        return []
    now = int(time.time())
    result: List[Dict[str, Any]] = []
    with _edit_queue() as payload:
        items = payload.get("items", [])

        # Requeue stale in-progress entries (worker crash/restart scenarios)
        for entry in items:
            status = entry.get("status")
            if status != "in_progress":
                continue
            started_at = entry.get("started_at")
            if not isinstance(started_at, int):
                entry.pop("started_at", None)
                entry["status"] = "pending"
            elif now - started_at > _STALE_IN_PROGRESS_SECS:
                entry["status"] = "pending"
                entry.pop("started_at", None)

        pending = [entry for entry in items if entry.get("status") == "pending"][:limit]
        selected_ids = {entry.get("run_id") for entry in pending if entry.get("run_id")}
        for entry in items:
            if entry.get("run_id") in selected_ids:
                entry["status"] = "in_progress"
                entry["started_at"] = now

        payload["items"] = items
        result = [dict(entry) for entry in pending]
    return result


def mark_done(run_id: str) -> None:
    if not run_id:
        return
    now = int(time.time())
    with _edit_queue() as payload:
        items = payload.get("items", [])
        for entry in items:
            if entry.get("run_id") == run_id:
                entry["status"] = "done"
                entry["finished_at"] = now
                entry.pop("started_at", None)
                break
        payload["items"] = items

