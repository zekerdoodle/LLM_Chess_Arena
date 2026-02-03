"""
Rooms Meta Utilities

Centralizes storage and access for room metadata (titles and updated_at) under the
vault directory. This replaces ad hoc logic previously embedded in server modules.

Functions:
- rooms_meta_path() -> str
- load() -> dict
- sanitize(meta: dict) -> dict
- save(meta: dict) -> None
- bump(room_id: str) -> None

All paths are resolved via utils.vault_paths.get_vault_root() with legacy fallback
handled inside that utility. Callers should not reimplement fallbacks locally.
"""

from __future__ import annotations

from typing import Any, Dict, Optional
import json
import os
import time

from utils.logger import get_logger
from utils.vault_paths import get_vault_root

logger = get_logger(__name__)

ACTIVE_RUN_STALE_SECS = 3600  # seconds before we drop stale active_run hints


def rooms_meta_path() -> str:
    """Return the absolute path to chats_meta.json under the vault root."""
    try:
        return os.path.join(str(get_vault_root()), "chats_meta.json")
    except Exception:
        # As a last-resort path; creation handled by callers
        return os.path.abspath(os.path.join("vault", "chats_meta.json"))


def load() -> Dict:
    """Load room metadata as a dict mapping room_id -> {title, updated_at}.

    Returns an empty dict if the file does not exist or cannot be parsed.
    """
    path = rooms_meta_path()
    if not os.path.exists(path):
        return {}
    try:
        with open(path, "r", encoding="utf-8") as f:
            data = json.load(f) or {}
        return data if isinstance(data, dict) else {}
    except Exception:
        return {}


def _coerce_int(value: Any, default: Optional[int] = None) -> Optional[int]:
    try:
        if value is None:
            return default
        return int(value)
    except Exception:
        return default


def _coerce_str(value: Any, default: Optional[str] = None) -> Optional[str]:
    if value is None:
        return default
    try:
        s = str(value).strip()
    except Exception:
        return default
    return s or default


def sanitize(meta: Dict) -> Dict:
    """Coerce meta.title to a safe string and normalized structure for all rooms.

    Prevents clients from rendering non-string titles (e.g., "[object Object]").
    Returns a shallow-copied, sanitized dict.
    """
    try:
        if not isinstance(meta, dict):
            return {}
        out: Dict = {}
        now = int(time.time())
        stale_cutoff = now - ACTIVE_RUN_STALE_SECS if ACTIVE_RUN_STALE_SECS > 0 else None
        for k, v in (meta or {}).items():
            if not isinstance(v, dict):
                out[k] = {"title": "New Chat", "updated_at": now}
                continue
            title = v.get("title")
            if isinstance(title, str):
                safe_title = title
            else:
                try:
                    safe_title = json.dumps(title) if title is not None else "New Chat"
                except Exception:
                    safe_title = "New Chat"
            try:
                ut = int(v.get("updated_at") or now)
            except Exception:
                ut = now
            entry: Dict[str, Any] = {"title": (safe_title or '').strip() or "New Chat", "updated_at": ut}
            
            # Preserve room_type field, default to "standard" if not set
            room_type = v.get("room_type")
            if room_type in ("standard", "task"):
                entry["room_type"] = room_type
            else:
                # Infer from room ID pattern if not explicitly set
                if k.startswith("task"):
                    entry["room_type"] = "task"
                else:
                    entry["room_type"] = "standard"
            
            active_raw = v.get("active_run")
            if isinstance(active_raw, dict):
                run_id = _coerce_str(active_raw.get("id"))
                updated_raw = _coerce_int(active_raw.get("updated_at"), now)
                is_stale = bool(
                    run_id
                    and stale_cutoff is not None
                    and isinstance(updated_raw, int)
                    and updated_raw < stale_cutoff
                )
                if run_id and not is_stale:
                    active: Dict[str, Any] = {
                        "id": run_id,
                        "status": _coerce_str(active_raw.get("status")),
                        "phase": _coerce_str(active_raw.get("phase")),
                        "visible": bool(active_raw.get("visible", True)),
                        "updated_at": updated_raw if isinstance(updated_raw, int) else now
                    }
                    last_seq_val = _coerce_int(active_raw.get("last_seq"))
                    if last_seq_val is not None:
                        active["last_seq"] = last_seq_val
                    if not active["status"]:
                        active.pop("status", None)
                    if not active.get("phase"):
                        active.pop("phase", None)
                    entry["active_run"] = active
            for extra_key, extra_value in v.items():
                if extra_key in ("active_run", "room_type"):
                    continue
                if extra_key not in entry:
                    entry[extra_key] = extra_value
            out[k] = entry
        return out
    except Exception:
        return {}


def save(meta: Dict) -> None:
    """Persist sanitized room metadata under the vault root.
    
    Uses atomic writes to prevent corruption during crashes or interruptions.
    """
    try:
        # Ensure metadata is clean/normalized before saving
        # This guarantees fields like room_type are always present/inferred
        clean_meta = sanitize(meta)
        
        # Try atomic save first (prevents corruption)
        try:
            from utils.rooms_meta_validator import atomic_save
            atomic_save(clean_meta)
            return
        except ImportError:
            # Fallback if validator not available
            pass
        
        # Fallback to regular save
        p = rooms_meta_path()
        os.makedirs(os.path.dirname(p), exist_ok=True)
        with open(p, "w", encoding="utf-8") as f:
            json.dump(clean_meta or {}, f, indent=2, ensure_ascii=False)
    except Exception as e:
        logger.error(f"RoomsMeta: failed to save: {e}")


def bump(room_id: str) -> None:
    """Update updated_at for a room to now, creating entry if missing."""
    try:
        p = rooms_meta_path()
        os.makedirs(os.path.dirname(p), exist_ok=True)
        meta = load()
        meta.setdefault(room_id, {})
        meta[room_id]["updated_at"] = int(time.time())
        save(meta)
    except Exception:
        # Best-effort only
        pass


def set_active_run(
    room_id: str,
    run_id: str,
    *,
    status: Optional[str] = None,
    phase: Optional[str] = None,
    last_seq: Optional[int] = None,
    visible: bool = True,
) -> None:
    """Persist the active run metadata for a room."""
    if not room_id or not run_id:
        return
    try:
        meta = load()
        entry = meta.setdefault(room_id, {})
        now = int(time.time())
        entry["active_run"] = {
            "id": str(run_id),
            "status": _coerce_str(status),
            "phase": _coerce_str(phase),
            "visible": bool(visible),
            "updated_at": now,
        }
        if last_seq is not None:
            try:
                entry["active_run"]["last_seq"] = int(last_seq)
            except Exception:
                pass
        if not entry.get("title"):
            entry["title"] = "New Chat"
        entry["updated_at"] = now
        save(meta)
    except Exception:
        logger.debug("RoomsMeta: failed to set active run", exc_info=True)


def clear_active_run(room_id: str, run_id: Optional[str] = None) -> None:
    """Remove active run metadata when a run completes."""
    if not room_id:
        return
    try:
        meta = load()
        entry = meta.get(room_id)
        if not isinstance(entry, dict):
            return
        active = entry.get("active_run")
        if not isinstance(active, dict):
            return
        if run_id and str(active.get("id")) != str(run_id):
            return
        entry.pop("active_run", None)
        entry["updated_at"] = int(time.time())
        save(meta)
    except Exception:
        logger.debug("RoomsMeta: failed to clear active run", exc_info=True)


__all__ = [
    "rooms_meta_path",
    "load",
    "sanitize",
    "save",
    "bump",
    "set_active_run",
    "clear_active_run",
]
