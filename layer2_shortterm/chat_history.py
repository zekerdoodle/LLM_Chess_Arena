"""
Chat History Storage for Layer 2 - Short Term Memory

Provides per-channel chat history storage with JSON persistence in the vault.
Supports role, timestamp, content, and channel tracking with token budget management.
"""

import json
import os
import time
import threading
import queue
from pathlib import Path
from typing import Dict, List, Optional, Tuple

from utils.logger import get_logger
from utils.token_counter import count_tokens

logger = get_logger(__name__)


class ChatHistory:
    """
    Per-channel chat history storage with JSON persistence.

    Stores chat messages with role, timestamp, content, and channel information.
    Manages token budget by truncating oldest messages when limit exceeded.
    """

    def __init__(
        self,
        vault_path: Optional[str] = None,
        max_tokens: int = 10000,
        default_model: str = "gpt-5",
    ):
        """
        Initialize chat history storage.

        Args:
            vault_path: Path to vault directory for JSON storage
            max_tokens: Maximum tokens allowed in chat history before truncation
            default_model: Default model for token counting
        """
        # Resolve vault root using centralized utility anchored to canonical vault
        try:
            from utils.vault_paths import get_vault_root, rebase_legacy_vault_path

            if vault_path is None:
                resolved_root = get_vault_root()
            else:
                resolved_root = rebase_legacy_vault_path(vault_path)
            self.vault_path = str(Path(resolved_root))
        except Exception:
            # Fall back to canonical vault when resolution fails
            fallback_root = Path(vault_path) if vault_path else Path("vault")
            self.vault_path = str(fallback_root)
        self.max_tokens = max_tokens
        self.default_model = default_model
        # Always derive paths from self.vault_path
        self.vault_file = os.path.join(self.vault_path, "chat_history.json")
        self.rooms_dir = os.path.join(self.vault_path, "chats")
        self._history: Dict[str, List[Dict]] = {}
        # Rolling token totals per channel (content-only) for O(1) budget checks
        self._token_totals: Dict[str, int] = {}
        # Background writer for per-room JSON saves (avoid blocking event loop)
        self._writer_queue: "queue.Queue[Tuple[str, List[Dict]]]" = queue.Queue(maxsize=256)
        self._writer_thread: Optional[threading.Thread] = None
        # Lock to coordinate between drain operations and background writer
        self._writer_lock: threading.Lock = threading.Lock()

        # Ensure vault directory exists
        os.makedirs(self.vault_path, exist_ok=True)

        # Ensure per-room directory exists
        os.makedirs(self.rooms_dir, exist_ok=True)

        # Start background writer (daemon)
        try:
            self._start_writer()
        except Exception:
            pass

        # Load existing history (with migration to per-room files)
        self._load_history()
        # Precompute token totals once at startup for faster operations
        try:
            self._recompute_token_totals()
        except Exception:
            pass

        logger.debug(
            f"L2.chat_history [vault] - Initialized (max_tokens: {max_tokens}, vault_path: {vault_path})"
        )

    def add_message(
        self,
        role: str,
        content: str,
        channel: str,
        timestamp: Optional[float] = None,
        message_id: Optional[str] = None,
        attachments: Optional[List[Dict]] = None,
        hidden: bool = False,
        extras: Optional[Dict] = None,
    ) -> None:
        """
        Add a message to chat history.

        Args:
            role: Message role ('user', 'assistant', 'system')
            content: Message content
            channel: Channel ID or name
            timestamp: Unix timestamp (defaults to current time)
            message_id: Message ID (optional, for user messages)
            attachments: Optional list of attachment descriptors to persist
        """
        if timestamp is None:
            timestamp = time.time()

        message = {
            "role": role,
            "content": content,
            "channel": channel,
            "timestamp": timestamp,
        }
        # Mark as hidden from UI when requested; still included in model prompts/history
        if hidden:
            try:
                message["hidden"] = True
            except Exception:
                pass

        # Add message ID if provided (typically for user messages)
        if message_id is not None:
            message["message_id"] = str(message_id)

        # Persist sanitized attachments when provided
        try:
            if attachments and isinstance(attachments, list):
                sanitized: List[Dict] = []
                for a in attachments:
                    if not isinstance(a, dict):
                        continue
                    sanitized.append(
                        {
                            "filename": str(a.get("filename", "")),
                            "url": str(a.get("url", "")),
                            "content_type": str(a.get("content_type", "")),
                            "size": int(a.get("size", 0) or 0),
                        }
                    )
                if sanitized:
                    message["attachments"] = sanitized
        except Exception:
            # Non-fatal; ignore malformed attachments
            pass

        # Merge extra metadata (e.g., response durations) into the message payload
        if extras and isinstance(extras, dict):
            try:
                for key, value in extras.items():
                    if key in {"role", "content", "channel", "timestamp", "message_id", "attachments", "hidden"}:
                        # Reserved keys are handled explicitly above; skip overriding them from extras.
                        continue
                    message[key] = value
            except Exception:
                pass

        # Initialize channel history if not exists
        if channel not in self._history:
            self._history[channel] = []

        # Guard: if a message with the same message_id already exists in this channel,
        # update it in place rather than appending a duplicate. This prevents double
        # assistant entries due to race conditions across transports (e.g., run stream
        # vs. history watcher) and ensures idempotency for persistence.
        try:
            if message_id is not None:
                mid = str(message_id)
                for idx, existing in enumerate(self._history[channel]):
                    if str(existing.get("message_id")) == mid:
                        # Update content, timestamp, and attachments if provided
                        try:
                            # Calculate token delta for rolling total correction
                            old_content = existing.get("content", "")
                            old_tokens = 0
                            try:
                                # Try to use cached token count
                                if isinstance(existing.get("_tok"), int):
                                    old_tokens = int(existing.get("_tok") or 0)
                                else:
                                    old_tokens = count_tokens(old_content, self.default_model)
                            except Exception as e:
                                logger.debug(
                                    f"L2.chat_history [ch:{channel}] - Token counting failed for old content during update: {e}"
                                )
                                old_tokens = 0
                            
                            new_tokens = 0
                            try:
                                new_tokens = count_tokens(content, self.default_model)
                            except Exception as e:
                                logger.debug(
                                    f"L2.chat_history [ch:{channel}] - Token counting failed for new content during update: {e}"
                                )
                                new_tokens = 0
                            
                            # Update message fields
                            existing["content"] = content
                            existing["timestamp"] = timestamp
                            existing["_tok"] = new_tokens
                            
                            if attachments is not None:
                                # sanitize again to be safe
                                sanitized: List[Dict] = []
                                for a in attachments:
                                    if not isinstance(a, dict):
                                        continue
                                    sanitized.append(
                                        {
                                            "filename": str(a.get("filename", "file")),
                                            "url": str(a.get("url", "")),
                                            "content_type": str(a.get("content_type", "")),
                                            "size": int(a.get("size", 0) or 0),
                                        }
                                    )
                                if sanitized:
                                    existing["attachments"] = sanitized
                            
                            # Replace in list to ensure reference is updated
                            self._history[channel][idx] = existing
                            
                            # Update rolling token total with the delta
                            token_delta = new_tokens - old_tokens
                            if token_delta != 0:
                                try:
                                    self._token_totals[channel] = max(0, int(self._token_totals.get(channel, 0)) + token_delta)
                                    logger.debug(
                                        f"L2.chat_history [ch:{channel}] - Updated token total by {token_delta:+d} (old: {old_tokens}, new: {new_tokens})"
                                    )
                                except Exception:
                                    # If rolling total update fails, recompute from scratch
                                    self._token_totals[channel] = self.get_total_tokens(channel)
                        except Exception:
                            pass
                        # Persist the overwrite strictly for this room
                        try:
                            self._enqueue_room_save(channel)
                        except Exception:
                            try:
                                self._save_history(overwrite_rooms={channel})
                            except Exception:
                                pass
                        logger.debug(
                            f"L2.chat_history [ch:{channel}] - Updated existing message id={mid} (dedup)"
                        )
                        return
        except Exception:
            # Fall through to append path if any error occurs
            pass

        # Add message to channel history
        self._history[channel].append(message)

        # Cache token count and update rolling total
        try:
            tok = count_tokens(content, self.default_model)
        except Exception:
            tok = 0
        try:
            # in-memory only hint to avoid recounting during truncation
            message["_tok"] = tok
        except Exception:
            pass
        try:
            self._token_totals[channel] = int(self._token_totals.get(channel, 0)) + int(tok)
        except Exception:
            self._token_totals[channel] = tok

        # Check token budget and truncate if needed
        self._truncate_if_needed(channel)

        # Save per-room file asynchronously (strict overwrite for this room only)
        try:
            self._enqueue_room_save(channel)
        except Exception:
            # Fallback to sync save of this room only
            try:
                self._save_history(overwrite_rooms={channel})
            except Exception:
                pass

        logger.debug(
            f"L2.chat_history [ch:{channel}] - Added {role} message (content: {len(content)} chars)"
        )
        logger.debug(
            f"L2.chat_history [ch:{channel}] - Channel history size: {len(self._history[channel])} messages"
        )

    def get_history(
        self, channel: str, max_messages: Optional[int] = None, exclude_message_id: Optional[str] = None
    ) -> List[Dict]:
        """
        Get chat history for a specific channel.

        Args:
            channel: Channel ID or name
            max_messages: Maximum number of messages to return (None for all)
            exclude_message_id: Optional message ID to exclude from history (used to avoid
                duplicating the current message that was just added for crash recovery)

        Returns:
            List of message dictionaries with role, content, channel, timestamp
        """
        if channel not in self._history:
            return []

        history = self._history[channel]
        
        # Filter out the excluded message (typically the current message being processed)
        if exclude_message_id:
            history = [msg for msg in history if msg.get("message_id") != exclude_message_id]

        if max_messages is not None:
            history = history[-max_messages:]

        logger.debug(
            f"L2.chat_history [ch:{channel}] - Retrieved {len(history)} messages (newest: {(time.time() - history[-1]['timestamp'])//60:.0f}m ago)"
            if history
            else f"L2.chat_history [ch:{channel}] - Retrieved 0 messages"
        )
        return history

    def get_all_channels(self) -> List[str]:
        """
        Get list of all channels with history.

        Returns:
            List of channel IDs/names
        """
        return list(self._history.keys())

    def clear_channel(self, channel: str) -> int:
        """
        Clear chat history for a specific channel.

        Args:
            channel: Channel ID or name to clear
            
        Returns:
            Number of messages that were cleared
        """
        if channel in self._history:
            message_count = len(self._history[channel])
            del self._history[channel]
            # Remove per-room file to ensure UI/history reflect true clear
            try:
                room_path = os.path.join(self.rooms_dir, f"{channel}.json")
                if os.path.exists(room_path):
                    os.remove(room_path)
            except Exception as re:
                logger.debug(f"L2.chat_history [ch:{channel}] - Could not remove room file during clear: {re}")
            self._save_history()
            logger.info(f"L2.chat_history [ch:{channel}] - Cleared chat history ({message_count} messages)")
            return message_count
        else:
            logger.debug(f"L2.chat_history [ch:{channel}] - No history to clear")
            return 0

    def revoke_from_message(self, channel: str, message_id: str, include_target: bool = True) -> int:
        """
        Revoke (remove) a message and all subsequent messages from a channel's history.

        Args:
            channel: Channel ID or name
            message_id: The message_id to revoke from (typically a user message UUID)
            include_target: If True, also remove the target message; otherwise start after it

        Returns:
            Number of messages removed

        Notes:
            - If the message_id is not found, no messages are removed
            - System messages and assistant messages following the target are also removed
        """
        try:
            if channel not in self._history:
                logger.debug(f"L2.chat_history [ch:{channel}] - Revoke requested but no history present")
                return 0
            history = self._history[channel]
            target_index = -1
            for idx, msg in enumerate(history):
                if str(msg.get("message_id")) == str(message_id):
                    target_index = idx
                    break
            if target_index < 0:
                logger.debug(
                    f"L2.chat_history [ch:{channel}] - Revoke: message_id {message_id} not found"
                )
                return 0
            # Compute slice index to keep before the target (or excluding target)
            keep_upto = target_index if include_target else (target_index + 1)
            removed_count = len(history) - keep_upto
            # Try to capture the run_id of the target message to remove any hidden entries linked to this run
            try:
                target_run_id = str(history[target_index].get("message_id"))
                # For assistant messages, strip possible "-assistant" suffix to get base run id
                if target_run_id.endswith("-assistant"):
                    target_run_id = target_run_id[:-10]
            except Exception:
                target_run_id = None

            # Build the kept history slice
            new_history = history[:keep_upto]

            # Also, ensure that any hidden messages with matching run_id are removed entirely
            if target_run_id:
                try:
                    # When external tools appended hidden messages with run_id, drop those
                    new_history = [m for m in new_history if str(m.get("run_id") or "") != target_run_id]
                except Exception:
                    pass

            self._history[channel] = new_history
            # Overwrite this room's file to ensure any externally-appended entries are removed
            self._save_history(overwrite_rooms={channel})
            logger.info(
                f"L2.chat_history [ch:{channel}] - Revoked {removed_count} message(s) from message_id {message_id}"
            )
            return removed_count
        except Exception as e:
            logger.error(
                f"L2.chat_history [ch:{channel}] - Failed to revoke from message {message_id}: {e}",
                exc_info=True,
            )
            return 0

    def clear_all(self) -> None:
        """Clear all chat history."""
        self._history = {}
        self._save_history()
        logger.info("L2.chat_history [vault] - Cleared all chat history")

    def get_total_tokens(self, channel: str) -> int:
        """
        Get total tokens for a channel's history.

        Args:
            channel: Channel ID or name

        Returns:
            Total token count for channel history
        """
        if channel not in self._history:
            return 0
        # Use rolling total when available
        try:
            return int(self._token_totals.get(channel, 0))
        except Exception:
            pass
        # Compute once and cache
        total = 0
        for m in self._history.get(channel, []):
            try:
                if isinstance(m.get("_tok"), int):
                    total += int(m.get("_tok") or 0)
                else:
                    total += count_tokens(m.get("content", ""), self.default_model)
            except Exception:
                continue
        self._token_totals[channel] = total
        return total

    def get_channel_stats(self, channel: str) -> Dict[str, int]:
        """
        Get statistics for a specific channel.

        Args:
            channel: Channel ID or name

        Returns:
            Dictionary with message count and token count
        """
        if channel not in self._history:
            return {
                "message_count": 0,
                "token_count": 0,
                "max_tokens": self.max_tokens,
            }

        message_count = len(self._history[channel])
        token_count = self.get_total_tokens(channel)

        return {
            "message_count": message_count,
            "token_count": token_count,
            "max_tokens": self.max_tokens,
        }

    def get_recent_messages(self, channel: str, count: int = 10) -> List[Dict]:
        """
        Get the most recent messages from a channel.

        Args:
            channel: Channel ID or name
            count: Number of recent messages to retrieve

        Returns:
            List of recent message dictionaries
        """
        if channel not in self._history:
            return []

        history = self._history[channel]
        return history[-count:] if len(history) > count else history

    def add_system_message(self, content: str, channel: str) -> None:
        """
        Add a system message to chat history.

        Args:
            content: System message content
            channel: Channel ID or name
        """
        self.add_message("system", content, channel)

    def get_stats(self) -> Dict[str, int]:
        """
        Get statistics about chat history.

        Returns:
            Dictionary with total channels, total messages, and total tokens
        """
        total_channels = len(self._history)
        total_messages = sum(
            len(messages) for messages in self._history.values()
        )
        total_tokens = sum(
            self.get_total_tokens(channel) for channel in self._history.keys()
        )

        return {
            "total_channels": total_channels,
            "total_messages": total_messages,
            "total_tokens": total_tokens,
        }

    def get_truncation_stats(self, channel: str) -> Dict[str, int]:
        """
        Get truncation statistics for a specific channel.

        Args:
            channel: Channel ID or name

        Returns:
            Dictionary with current tokens, max tokens, and truncation status
        """
        if channel not in self._history:
            return {
                "current_tokens": 0,
                "max_tokens": self.max_tokens,
                "is_truncated": False,
                "available_tokens": self.max_tokens,
            }

        current_tokens = self.get_total_tokens(channel)
        is_truncated = current_tokens > self.max_tokens

        return {
            "current_tokens": current_tokens,
            "max_tokens": self.max_tokens,
            "is_truncated": is_truncated,
            "available_tokens": max(0, self.max_tokens - current_tokens),
        }

    def _truncate_if_needed(self, channel: str) -> None:
        """
        Truncate channel history if it exceeds token limit.

        Args:
            channel: Channel ID or name
        """
        if channel not in self._history:
            return

        current_tokens = self.get_total_tokens(channel)
        if current_tokens <= self.max_tokens:
            return

        # Log truncation event
        logger.warning(
            f"L2.chat_history [ch:{channel}] - Token limit exceeded ({current_tokens}/{self.max_tokens}), truncating oldest messages"
        )

        # Remove oldest messages until we're under the limit
        removed_count = 0
        total_removed_tokens = 0
        count_failures = 0
        while (
            current_tokens > self.max_tokens
            and len(self._history[channel]) > 1
        ):
            removed_message = self._history[channel].pop(0)
            # Use cached token hint when available to avoid recounting
            removed_tokens = 0
            try:
                if removed_message.get("_tok") is not None:
                    removed_tokens = int(removed_message.get("_tok"))
                else:
                    removed_tokens = count_tokens(removed_message.get("content", ""), self.default_model)
            except Exception as tok_err:
                # If token counting fails, estimate conservatively (assume average message size)
                # to prevent drift. Log the error for visibility.
                logger.warning(
                    f"L2.chat_history [ch:{channel}] - Token counting failed for removed message: {tok_err}"
                )
                count_failures += 1
                # Improved fallback: Use character-based estimation with model-aware ratio
                # GPT models typically average 4 chars/token, use 3.5 for conservative estimate
                try:
                    content = removed_message.get("content", "")
                    char_count = len(str(content))
                    # More conservative: 3.5 chars per token rounded up
                    removed_tokens = max(1, (char_count * 10 + 34) // 35)
                except Exception:
                    removed_tokens = 20  # Raised from 15 for more conservative fallback
            
            current_tokens -= removed_tokens
            total_removed_tokens += removed_tokens
            removed_count += 1
            logger.debug(
                f"L2.chat_history [ch:{channel}] - Removed message (tokens: {removed_tokens})"
            )

        # Update rolling total with actual removed tokens to prevent drift
        try:
            self._token_totals[channel] = max(0, int(self._token_totals.get(channel, 0)) - int(total_removed_tokens))
        except Exception:
            # If rolling total update fails, recompute from scratch to self-correct
            count_failures += 1
            try:
                self._token_totals[channel] = self.get_total_tokens(channel)
            except Exception:
                self._token_totals[channel] = 0
        
        # If multiple count failures occurred, force recomputation to prevent cumulative drift
        if count_failures >= 2:
            logger.warning(
                f"L2.chat_history [ch:{channel}] - Multiple token counting failures ({count_failures}), recomputing totals"
            )
            try:
                actual_total = 0
                for m in self._history[channel]:
                    try:
                        actual_total += count_tokens(m.get("content", ""), self.default_model)
                    except Exception:
                        # Use conservative estimate for failed counts (3.5 chars/token)
                        char_count = len(str(m.get("content", "")))
                        actual_total += max(1, (char_count * 10 + 34) // 35)
                self._token_totals[channel] = actual_total
                current_tokens = actual_total
                logger.info(f"L2.chat_history [ch:{channel}] - Recomputed token total: {actual_total}")
            except Exception as recomp_err:
                logger.error(f"L2.chat_history [ch:{channel}] - Failed to recompute token total: {recomp_err}")
        
        # Validate token totals after truncation to detect any drift
        try:
            self._validate_token_totals(channel)
        except Exception:
            pass
        
        final_count = len(self._history[channel])
        logger.warning(
            f"L2.chat_history [ch:{channel}] - Truncation complete (removed: {removed_count} messages, kept: {final_count}, tokens: {current_tokens})"
        )
        # Persist strict overwrite for this room asynchronously
        try:
            self._enqueue_room_save(channel)
        except Exception:
            try:
                self._save_history(overwrite_rooms={channel})
            except Exception:
                pass

    def _save_history(self, overwrite_rooms: Optional[set] = None) -> None:
        """Save chat history to disk.

        Args:
            overwrite_rooms: Optional set of room ids for which the per-room file should be
                overwritten exactly with the in-memory messages (no merge). When None or empty,
                existing behavior (merge) is used for all rooms.
        """
        try:
            overwrite_rooms = overwrite_rooms or set()
            total_channels = len(self._history)
            total_messages = sum(
                len(messages) for messages in self._history.values()
            )

            with open(self.vault_file, "w", encoding="utf-8") as f:
                json.dump(self._history, f, indent=2, ensure_ascii=False)

            # Also persist per-room files for better tooling and web rooms
            # If doing strict overwrites for some rooms, drain any pending async writes
            # for those rooms to avoid interleaving with the synchronous save below.
            try:
                if overwrite_rooms:
                    self._drain_writer_for_rooms(set(overwrite_rooms))
            except Exception:
                pass

            for room, messages in list(self._history.items()):
                room_path = os.path.join(self.rooms_dir, f"{room}.json")
                if room in overwrite_rooms:
                    # Strict overwrite for this room: write exactly the in-memory messages
                    try:
                        tmp_path = room_path + ".tmp"
                        with open(tmp_path, "w", encoding="utf-8") as rf:
                            json.dump(list(messages), rf, indent=2, ensure_ascii=False)
                        # Atomic replace to avoid interleaved content from background writer
                        os.replace(tmp_path, room_path)
                    except IOError as e:
                        logger.error(
                            f"L2.chat_history [vault] - Failed to overwrite room file {room_path}: {e}"
                        )
                    # Ensure in-memory remains authoritative
                    self._history[room] = list(messages)
                    continue

                # Default behavior: merge with any existing messages written externally (e.g., web tools)
                combined = []
                existing_keys = set()
                try:
                    if os.path.exists(room_path):
                        with open(room_path, "r", encoding="utf-8") as rf:
                            existing = json.load(rf)
                            if isinstance(existing, list):
                                for m in existing:
                                    key = str(m.get("message_id") or f"{m.get('role')}|{m.get('content')}|{m.get('timestamp')}")
                                    combined.append(m)
                                    existing_keys.add(key)
                except Exception as re:
                    logger.debug(f"L2.chat_history [vault] - Could not read existing room file {room_path}: {re}")
                # Append in-memory messages that aren't already present
                for m in messages:
                    key = str(m.get("message_id") or f"{m.get('role')}|{m.get('content')}|{m.get('timestamp')}")
                    if key not in existing_keys:
                        combined.append(m)
                        existing_keys.add(key)
                # Update in-memory copy to keep them in sync
                self._history[room] = combined
                try:
                    tmp_path = room_path + ".tmp"
                    with open(tmp_path, "w", encoding="utf-8") as rf:
                        json.dump(combined, rf, indent=2, ensure_ascii=False)
                    os.replace(tmp_path, room_path)
                except IOError as e:
                    logger.error(
                        f"L2.chat_history [vault] - Failed to save room file {room_path}: {e}"
                    )

            logger.debug(
                f"L2.chat_history [vault] - Saved chat history (channels: {total_channels}, total_messages: {total_messages})"
            )

        except IOError as e:
            logger.error(
                f"L2.chat_history [vault] - Failed to save history: {e}"
            )
        except Exception as e:
            logger.error(
                f"L2.chat_history [vault] - Error saving history: {e}",
                exc_info=True,
            )

    def _load_history(self) -> None:
        """Load chat history from disk."""
        # First, try to load per-room files. If empty, fall back to aggregate and migrate.
        loaded_any = False
        try:
            if os.path.isdir(self.rooms_dir):
                for fname in os.listdir(self.rooms_dir):
                    if not fname.endswith(".json"):
                        continue
                    room = fname[:-5]
                    room_path = os.path.join(self.rooms_dir, fname)
                    try:
                        with open(room_path, "r", encoding="utf-8") as rf:
                            msgs = json.load(rf)
                            if isinstance(msgs, list):
                                self._history[room] = msgs
                                loaded_any = True
                    except Exception as re:
                        logger.warning(f"L2.chat_history [vault] - Failed to load room file {room_path}: {re}")

            if loaded_any:
                total_channels = len(self._history)
                total_messages = sum(len(m) for m in self._history.values())
                logger.debug(
                    f"L2.chat_history [vault] - Loaded per-room history (channels: {total_channels}, total_messages: {total_messages})"
                )
                return

            # Fallback: load aggregate file and then migrate to per-room
            if not os.path.exists(self.vault_file):
                logger.debug(
                    f"L2.chat_history [vault] - No existing history file found"
                )
                return

            with open(self.vault_file, "r", encoding="utf-8") as f:
                loaded_data = json.load(f)

            if isinstance(loaded_data, dict):
                self._history = loaded_data
            else:
                logger.warning(
                    f"L2.chat_history [vault] - Invalid history format (expected dict, got {type(loaded_data)}), starting fresh"
                )
                self._history = {}

            # Migrate to per-room files on next save
            logger.info("L2.chat_history [vault] - Loaded aggregate history; will mirror to per-room files on save")

        except json.JSONDecodeError as e:
            logger.error(
                f"L2.chat_history [vault] - Corrupted history file, starting fresh: {e}"
            )
            self._history = {}
        except IOError as e:
            logger.error(
                f"L2.chat_history [vault] - Failed to load history: {e}"
            )
            self._history = {}
        except Exception as e:
            logger.error(
                f"L2.chat_history [vault] - Error loading history: {e}",
                exc_info=True,
            )
            self._history = {}

    # ---------- Internal performance helpers ----------
    def _recompute_token_totals(self) -> None:
        totals: Dict[str, int] = {}
        for ch, msgs in self._history.items():
            t = 0
            for m in msgs:
                try:
                    if isinstance(m.get("_tok"), int):
                        t += int(m.get("_tok") or 0)
                    else:
                        t += count_tokens(m.get("content", ""), self.default_model)
                except Exception:
                    continue
            totals[ch] = t
        self._token_totals = totals

    def _validate_token_totals(self, channel: str, threshold: float = 0.1) -> bool:
        """Validate token totals for a channel and correct if drift detected.
        
        Args:
            channel: Channel ID to validate
            threshold: Maximum allowed drift ratio (default 10%)
            
        Returns:
            True if totals are valid or were corrected, False on error
        """
        if channel not in self._history:
            return True
        
        try:
            cached_total = self._token_totals.get(channel, 0)
            actual_total = 0
            for m in self._history.get(channel, []):
                try:
                    if isinstance(m.get("_tok"), int):
                        actual_total += int(m.get("_tok") or 0)
                    else:
                        actual_total += count_tokens(m.get("content", ""), self.default_model)
                except Exception:
                    continue
            
            # Check for drift
            if cached_total > 0:
                drift_ratio = abs(actual_total - cached_total) / cached_total
                if drift_ratio > threshold:
                    logger.warning(
                        f"L2.chat_history [ch:{channel}] - Token total drift detected: "
                        f"cached={cached_total}, actual={actual_total}, drift={drift_ratio:.1%}"
                    )
                    self._token_totals[channel] = actual_total
                    return True
            elif actual_total != cached_total:
                # No cached total or zero cached, just update
                self._token_totals[channel] = actual_total
            
            return True
        except Exception as e:
            logger.error(f"L2.chat_history [ch:{channel}] - Token validation failed: {e}")
            return False

    def _start_writer(self) -> None:
        if self._writer_thread and self._writer_thread.is_alive():
            return
        def _worker():
            while True:
                try:
                    room_path, messages = self._writer_queue.get()
                    # Acquire lock to coordinate with drain operations
                    with self._writer_lock:
                        # Strip in-memory token hints before persisting
                        cleaned = []
                        for m in messages:
                            if isinstance(m, dict):
                                d = dict(m)
                                d.pop("_tok", None)
                                cleaned.append(d)
                        # Atomic write: write to temp then replace
                        tmp_path = room_path + ".tmp"
                        with open(tmp_path, "w", encoding="utf-8") as rf:
                            json.dump(cleaned, rf, indent=2, ensure_ascii=False)
                        try:
                            os.replace(tmp_path, room_path)
                        except Exception:
                            # Fallback if replace fails (e.g., cross-device): direct write
                            with open(room_path, "w", encoding="utf-8") as rf:
                                json.dump(cleaned, rf, indent=2, ensure_ascii=False)
                except Exception:
                    # Avoid tight spin on unexpected errors
                    time.sleep(0.01)
        t = threading.Thread(target=_worker, name="chat_history_writer", daemon=True)
        t.start()
        self._writer_thread = t

    def _enqueue_room_save(self, channel: str) -> None:
        room_path = os.path.join(self.rooms_dir, f"{channel}.json")
        os.makedirs(self.rooms_dir, exist_ok=True)
        # Prepare a merged view that preserves externally-written hidden messages
        inmem = list(self._history.get(channel, []))
        # Strip in-memory token hints
        cleaned_inmem: List[Dict] = []
        for m in inmem:
            if isinstance(m, dict):
                d = dict(m)
                d.pop("_tok", None)
                cleaned_inmem.append(d)
        try:
            # Load existing on-disk messages for this room (if any)
            existing: List[Dict] = []
            if os.path.exists(room_path):
                try:
                    with open(room_path, "r", encoding="utf-8") as rf:
                        ex = json.load(rf) or []
                        if isinstance(ex, list):
                            existing = ex
                except Exception:
                    existing = []
            # Build a combined list preserving existing entries and appending new unique ones from memory
            combined = []
            existing_keys = set()
            def _k(m: Dict) -> str:
                return str(m.get("message_id") or f"{m.get('role')}|{m.get('content')}|{m.get('timestamp')}")
            for m in existing:
                combined.append(m)
                try:
                    existing_keys.add(_k(m))
                except Exception:
                    pass
            for m in cleaned_inmem:
                try:
                    k = _k(m)
                except Exception:
                    k = ""
                if k and k in existing_keys:
                    continue
                combined.append(m)
                if k:
                    existing_keys.add(k)
            payload = combined
        except Exception:
            payload = cleaned_inmem
        # Enqueue write; fallback to sync on saturation
        try:
            self._writer_queue.put_nowait((room_path, payload))
        except queue.Full:
            with open(room_path, "w", encoding="utf-8") as rf:
                json.dump(payload, rf, indent=2, ensure_ascii=False)

    def _drain_writer_for_rooms(self, rooms: "set[str]") -> None:
        """Best-effort: remove any pending async writes for the specified rooms.

        Prevents races where the background writer overwrites or interleaves
        with a synchronous save requested by tests or critical paths.
        """
        try:
            if not rooms:
                return
            # Acquire lock to prevent writer thread from processing while we drain
            with self._writer_lock:
                # Drain queue and requeue non-matching items to preserve others
                remaining: list[tuple[str, list[dict]] | tuple] = []
                while True:
                    try:
                        item = self._writer_queue.get_nowait()
                    except queue.Empty:
                        break
                    try:
                        room_path, payload = item  # type: ignore[misc]
                        name = os.path.splitext(os.path.basename(room_path))[0]
                        if name not in rooms:
                            remaining.append((room_path, payload))
                    except Exception:
                        # If item is malformed, drop it
                        continue
                # Requeue remaining items (drop order stability; acceptable for background writes)
                for it in remaining:
                    try:
                        self._writer_queue.put_nowait(it)
                    except Exception:
                        # If queue is full, fallback: write synchronously to avoid loss
                        try:
                            rp, pl = it  # type: ignore[misc]
                            with open(rp, "w", encoding="utf-8") as rf:
                                json.dump(pl, rf, indent=2, ensure_ascii=False)
                        except Exception:
                            pass
        except Exception:
            # Non-fatal
            pass
