"""
Room selection helpers for web/server components.

Provides utilities to consistently pick the most relevant room for
background messages or wake-up flows per specs:
- Prefer the most recently active non-default room
- Fall back to scanning per-room chat files when ChatHistory is unavailable
- Optionally fall back to the stored active room selection
"""

from __future__ import annotations

from typing import Optional, Any
import os
import json
import time

from utils.logger import get_logger
from utils.vault_paths import get_vault_root

logger = get_logger(__name__)


def select_latest_non_default_room(
    chat_history: Optional[Any] = None,
    *,
    active_room_fallback: bool = True,
    default_room: str = "web",
) -> str:
    """Select the most recently active non-default room.

    Args:
        chat_history: Optional ChatHistory instance; when provided, it is used to
            gather rooms and read latest timestamps from memory (with best-effort
            on-disk reload). When not provided or empty, falls back to scanning
            per-room JSON files in the vault's `chats/` directory.
        active_room_fallback: When True, if no recent messages are found, use the
            active room hint stored by the web app.
        default_room: Room id to return when no better candidate is found.

    Returns:
        The selected room id.
    """
    latest_room: Optional[str] = None
    latest_ts: float = -1

    # First, try via ChatHistory
    try:
        if chat_history is not None:
            try:
                # Reload from disk if available to reflect external writes
                getattr(chat_history, "_load_history", lambda: None)()
            except Exception:
                pass
            for r in chat_history.get_all_channels():
                if r == default_room:
                    continue
                try:
                    hist = chat_history.get_history(r)
                    if hist:
                        ts = hist[-1].get("timestamp") or 0
                        if ts > latest_ts:
                            latest_ts = ts
                            latest_room = r
                except Exception:
                    continue
    except Exception as e:
        logger.debug(f"rooms.select: history pass failed: {e}")

    # Fallback: scan per-room files
    if latest_room is None:
        try:
            rooms_dir = os.path.join(str(get_vault_root()), "chats")
        except Exception:
            rooms_dir = os.path.join("vault", "chats")
        try:
            if os.path.isdir(rooms_dir):
                for fname in os.listdir(rooms_dir):
                    if not fname.endswith('.json'):
                        continue
                    room_name = fname[:-5]
                    if room_name == default_room:
                        continue
                    fpath = os.path.join(rooms_dir, fname)
                    try:
                        with open(fpath, 'r', encoding='utf-8') as rf:
                            msgs = json.load(rf)
                        if isinstance(msgs, list) and msgs:
                            ts = (msgs[-1].get('timestamp') or 0)
                            if ts > latest_ts:
                                latest_ts = ts
                                latest_room = room_name
                    except Exception:
                        continue
        except Exception as e:
            logger.debug(f"rooms.select: file scan failed: {e}")

    # Active-room hint fallback
    if latest_room is None and active_room_fallback:
        try:
            from utils.active_room import get_active_room
            ar = get_active_room()
            if isinstance(ar, str) and ar:
                latest_room = ar
        except Exception:
            pass

    return latest_room or default_room


__all__ = ["select_latest_non_default_room"]

def generate_new_room_id(prefix: str = "r") -> str:
    """Generate a new unique room id and ensure its chat file path is unused.

    Uses the same naming style as the rooms API: r<epoch_ms>[optional _i] to avoid collisions.
    Does not create any files; just returns an available id.
    """
    try:
        import time, os
        from utils.vault_paths import get_vault_root as _get_root
        root = str(_get_root())
    except Exception:
        import time, os
        root = os.path.abspath("vault")
    chats = os.path.join(root, "chats")
    try:
        os.makedirs(chats, exist_ok=True)
    except Exception:
        pass
    base = f"{prefix}{int(time.time()*1000)}"
    candidate = base
    i = 0
    while True:
        p = os.path.join(chats, f"{candidate}.json")
        if not os.path.exists(p):
            return candidate
        i += 1
        candidate = f"{base}_{i}"

__all__.append("generate_new_room_id")
