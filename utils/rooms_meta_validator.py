"""
Rooms Metadata Validator

Validates and repairs room metadata on startup to ensure all room files have
corresponding metadata entries. This prevents the issue where rooms appear
without names after system updates or crashes.

Functions:
- validate_and_repair() -> tuple[bool, str]: Check and fix missing metadata
- enable_atomic_saves() -> None: Make metadata saves crash-resistant
"""

from __future__ import annotations

import glob
import json
import os
import tempfile
import time
from typing import Dict, List, Tuple

from utils.logger import get_logger
from utils.vault_paths import get_vault_root
from utils.rooms_meta import load, save, rooms_meta_path

logger = get_logger(__name__)


def _get_all_room_ids() -> List[str]:
    """Get list of all room IDs from vault/chats/*.json files."""
    try:
        chats_dir = os.path.join(str(get_vault_root()), "chats")
        os.makedirs(chats_dir, exist_ok=True)
        files = glob.glob(os.path.join(chats_dir, "*.json"))
        room_ids = [os.path.splitext(os.path.basename(f))[0] for f in files]
        return sorted(room_ids)
    except Exception as e:
        logger.warning(f"RoomsMetaValidator: Failed to list room files: {e}")
        return []


def _extract_title_from_history(room_id: str) -> str:
    """Extract a reasonable title from room's chat history.
    
    Returns the first user message (truncated) or "New Chat" if empty.
    """
    try:
        chats_dir = os.path.join(str(get_vault_root()), "chats")
        room_file = os.path.join(chats_dir, f"{room_id}.json")
        
        with open(room_file, 'r', encoding='utf-8') as f:
            messages = json.load(f)
        
        if not isinstance(messages, list):
            return "New Chat"
        
        # Find first user message
        for msg in messages:
            if not isinstance(msg, dict):
                continue
            if msg.get('role') == 'user':
                content = msg.get('content', '')
                if isinstance(content, str) and content.strip():
                    # Clean and truncate
                    title = content.strip()
                    title = ' '.join(title.split())  # Normalize whitespace
                    if len(title) > 50:
                        title = title[:47] + '...'
                    return title
        
        return "New Chat"
    except Exception:
        return "New Chat"


def _get_room_timestamp(room_id: str) -> int:
    """Get the timestamp for a room from its last message or file modification time.
    
    This ensures each room has a unique timestamp based on actual activity,
    not a mass-assigned timestamp. Returns the timestamp of the last message,
    or falls back to the file modification time.
    """
    try:
        chats_dir = os.path.join(str(get_vault_root()), "chats")
        room_file = os.path.join(chats_dir, f"{room_id}.json")
        
        # Try to get timestamp from last message
        try:
            with open(room_file, 'r', encoding='utf-8') as f:
                messages = json.load(f)
            
            if isinstance(messages, list) and messages:
                # Get the last message's timestamp
                for msg in reversed(messages):
                    if isinstance(msg, dict):
                        ts = msg.get('timestamp')
                        if ts:
                            try:
                                return int(ts)
                            except (ValueError, TypeError):
                                pass
        except Exception:
            pass
        
        # Fallback to file modification time
        return int(os.path.getmtime(room_file))
    except Exception:
        # Last resort: current time
        return int(time.time())


def validate_and_repair() -> Tuple[bool, str]:
    """Validate that all room files have metadata entries.
    
    If any rooms are missing metadata, creates entries with titles extracted
    from chat history. Returns (success, message).
    """
    try:
        # Get all room IDs from files
        all_rooms = _get_all_room_ids()
        if not all_rooms:
            return True, "No rooms found"
        
        # Load current metadata
        meta = load()
        
        # Find rooms without metadata
        missing = [rid for rid in all_rooms if rid not in meta]
        
        if not missing:
            logger.info(f"RoomsMetaValidator: All {len(all_rooms)} rooms have metadata ✓")
            return True, f"All {len(all_rooms)} rooms validated"
        
        # Repair missing metadata
        logger.warning(f"RoomsMetaValidator: Found {len(missing)} rooms without metadata")
        logger.info(f"RoomsMetaValidator: Repairing: {', '.join(missing)}")
        
        for room_id in missing:
            title = _extract_title_from_history(room_id)
            timestamp = _get_room_timestamp(room_id)
            meta[room_id] = {
                "title": title,
                "updated_at": timestamp
            }
            logger.info(f"RoomsMetaValidator: Added metadata for {room_id}: '{title}' (ts: {timestamp})")
        
        # Save repaired metadata
        save(meta)
        
        return True, f"Repaired {len(missing)} missing metadata entries"
        
    except Exception as e:
        logger.error(f"RoomsMetaValidator: Validation failed: {e}", exc_info=True)
        return False, f"Validation error: {e}"


def atomic_save(meta: Dict) -> None:
    """Save metadata atomically to prevent corruption during crashes.
    
    Writes to a temp file first, then atomically renames it to the target.
    This prevents partial writes that could corrupt the metadata file.
    """
    try:
        target_path = rooms_meta_path()
        dir_path = os.path.dirname(target_path)
        os.makedirs(dir_path, exist_ok=True)
        
        # Write to temporary file in same directory
        # (same directory ensures atomic rename on same filesystem)
        fd, temp_path = tempfile.mkstemp(
            dir=dir_path,
            prefix=".chats_meta_tmp_",
            suffix=".json",
            text=True
        )
        
        try:
            # Write JSON to temp file
            with os.fdopen(fd, 'w', encoding='utf-8') as f:
                json.dump(meta or {}, f, indent=2, ensure_ascii=False)
            
            # Atomically replace target file
            # On POSIX systems, rename is atomic
            os.replace(temp_path, target_path)
            
        except Exception:
            # Clean up temp file if something went wrong
            try:
                os.unlink(temp_path)
            except Exception:
                pass
            raise
            
    except Exception as e:
        logger.error(f"RoomsMetaValidator: Atomic save failed: {e}")
        raise


__all__ = [
    "validate_and_repair",
    "atomic_save",
]

