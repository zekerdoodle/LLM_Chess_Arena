"""
Active Room Tracker

Provides simple, safe persistence of the currently active room for the web UI,
so that background system messages and wakeups can target a sensible place.

Storage location: <vault_root>/active_room.json

Notes:
- Validates room id to be conservative (alphanum, dashes, underscores).
- Checks that the room file exists before returning it as active.
- Provides get/set helpers for other modules to use.
"""

from __future__ import annotations

import json
import os
import re
import time
from typing import Optional, Tuple

from utils.logger import get_logger
from utils.vault_paths import get_vault_root

logger = get_logger(__name__)

try:
    _VAULT_ROOT = str(get_vault_root())
except Exception:
    _VAULT_ROOT = "vault"
_ACTIVE_PATH = os.path.join(_VAULT_ROOT, "active_room.json")
_ROOMS_DIR = os.path.join(_VAULT_ROOT, "chats")

_ROOM_RE = re.compile(r"^[A-Za-z0-9_\-]+$")


def _ensure_dirs() -> None:
    try:
        os.makedirs(os.path.dirname(_ACTIVE_PATH), exist_ok=True)
        os.makedirs(_ROOMS_DIR, exist_ok=True)
    except Exception:
        pass


def _room_file(room_id: str) -> str:
    return os.path.join(_ROOMS_DIR, f"{room_id}.json")


def _is_valid_room(room_id: str) -> bool:
    try:
        if not isinstance(room_id, str) or not room_id:
            return False
        if not _ROOM_RE.match(room_id):
            return False
        # Only return valid if the room exists on disk
        return os.path.exists(_room_file(room_id))
    except Exception:
        return False


def set_active_room(room_id: str, context: Optional[str] = None) -> Tuple[bool, str]:
    """Persist the active room id with timestamp."""
    _ensure_dirs()
    try:
        if not _is_valid_room(room_id):
            return False, "invalid or unknown room"
        payload = {"room": room_id, "updated_at": int(time.time())}
        if context:
            payload["context"] = str(context)
        else:
            try:
                existing = get_active_payload()
                if existing.get("context"):
                    payload["context"] = existing["context"]
            except Exception:
                pass
        with open(_ACTIVE_PATH, "w", encoding="utf-8") as f:
            json.dump(payload, f)
        logger.debug(f"Active room set to {room_id} (context={payload.get('context')})")
        return True, "ok"
    except Exception as e:
        logger.debug(f"set_active_room failed: {e}")
        return False, str(e)
def set_active_context(context: str, room_id: Optional[str] = None) -> Tuple[bool, str]:
    """Store the active UI context (e.g., tasks views) with optional room hint."""
    _ensure_dirs()
    try:
        payload = get_active_payload()
    except Exception:
        payload = {"room": None, "updated_at": None, "context": None}
    payload["context"] = str(context) if context else None
    if room_id and _is_valid_room(room_id):
        payload["room"] = room_id
    payload["updated_at"] = int(time.time())
    try:
        with open(_ACTIVE_PATH, "w", encoding="utf-8") as f:
            json.dump(payload, f)
        if payload.get("context"):
            try:
                from utils.forms_store import enqueue_ui_event

                enqueue_ui_event(
                    {
                        "action": "notification.clear",
                        "target": str(payload["context"]),
                    }
                )
            except Exception as notify_exc:  # pragma: no cover - notification best effort
                logger.debug(f"set_active_context notify skipped: {notify_exc}")
        return True, "ok"
    except Exception as exc:
        logger.debug(f"set_active_context failed: {exc}")
        return False, str(exc)




def get_active_room() -> Optional[str]:
    """Return the currently active room id if valid and present.

    Returns:
        The room id or None if not set/valid.

    Example:
        >>> get_active_room() in (None, "r123")
        True
    """
    _ensure_dirs()
    try:
        if not os.path.exists(_ACTIVE_PATH):
            return None
        with open(_ACTIVE_PATH, "r", encoding="utf-8") as f:
            data = json.load(f) or {}
        room = str(data.get("room") or "").strip()
        if _is_valid_room(room):
            return room
        return None
    except Exception as e:
        logger.debug(f"get_active_room failed: {e}")
        return None


def get_active_payload() -> dict:
    """Return the raw active-room payload for API responses.

    Ensures keys are present even if file missing.
    Automatically cleans up stale room references.
    """
    _ensure_dirs()
    try:
        if not os.path.exists(_ACTIVE_PATH):
            return {"room": None, "updated_at": None, "context": None}
        with open(_ACTIVE_PATH, "r", encoding="utf-8") as f:
            data = json.load(f) or {}
        # Validate structure
        room = data.get("room")
        updated_at = data.get("updated_at")
        context = data.get("context")
        
        # Check if room is valid; if not, clean it up
        if room is not None:
            if not isinstance(room, str) or not _is_valid_room(room):
                logger.debug(f"Cleaning up stale active room reference: {room}")
                room = None
                # Clean up the file to remove the stale reference
                try:
                    cleaned_data = {"room": None, "updated_at": int(time.time()), "context": context}
                    with open(_ACTIVE_PATH, "w", encoding="utf-8") as f:
                        json.dump(cleaned_data, f)
                except Exception as cleanup_exc:
                    logger.debug(f"Failed to clean up stale room reference: {cleanup_exc}")
        
        if not isinstance(updated_at, int):
            try:
                updated_at = int(updated_at)
            except Exception:
                updated_at = None
        if context is not None:
            context = str(context)
        return {"room": room, "updated_at": updated_at, "context": context}
    except Exception as e:
        logger.debug(f"get_active_payload failed: {e}")
        return {"room": None, "updated_at": None, "context": None}
