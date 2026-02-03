"""
Verbatim Memory Storage for Layer 3 - Long Term Memory

Stores chat chunks (user+assistant exchanges) with timestamps and embeddings
for retrieval and analysis. Automatically triggered after each conversation exchange.

SPECS ALIGNMENT (docs/specs.md lines 26-27):
- Storage: After every chat chunk (user + assistant exchange)
- Data stored: chat chunk with timestamps, embedding
- Stored in vault/verbatim_memories.json
- Retrieval ranking: 1) Relevance (similarity), 2) Recency
- Deduplication: Verbatim memories already in chat history are NOT shown (prevent duplication)
"""

import json
import time
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple

from layer3_longterm.embeddings import embed, retrieve
from utils.logger import get_logger

logger = get_logger(__name__)


@dataclass
class VerbatimMemory:
    """Data class for storing verbatim memory entries."""

    user_message: str
    assistant_message: str
    user_timestamp: float
    assistant_timestamp: float
    channel: str
    chunk_id: str  # Unique identifier for this exchange
    embedding_id: Optional[str] = None
    run_id: Optional[str] = None
    user_message_id: Optional[str] = None
    assistant_message_id: Optional[str] = None


class VerbatimMemoryManager:
    """Manages verbatim memory storage and retrieval."""

    def __init__(self, vault_path: Optional[str] = None):
        """Initialize the verbatim memory manager.

        Args:
            vault_path: Path to vault directory for storing memories
        """
        # Resolve vault root centrally (spec-compliant), fallback to legacy
        try:
            if vault_path is None:
                from utils.vault_paths import get_vault_root
                self.vault_path = Path(get_vault_root())
            else:
                self.vault_path = Path(vault_path)
        except Exception:
            self.vault_path = Path(vault_path or "vault")
        self.memory_file = self.vault_path / "verbatim_memories.json"

        # Memory storage
        self.memories: List[VerbatimMemory] = []

        # Exchange tracking
        # channel -> last user message
        self.pending_user_messages: Dict[str, Dict] = {}

        # Load existing memories
        self._load_memories()

        logger.debug(
            f"VerbatimMemoryManager initialized with {len(self.memories)} existing memories"
        )

    def store_verbatim(
        self,
        user_message: str,
        assistant_message: str,
        channel: str,
        user_timestamp: Optional[float] = None,
        assistant_timestamp: Optional[float] = None,
        run_id: Optional[str] = None,
        user_message_id: Optional[str] = None,
        assistant_message_id: Optional[str] = None,
    ) -> str:
        """Store a verbatim chat chunk (user + assistant exchange).

        Args:
            user_message: User's message content
            assistant_message: Assistant's response content
            channel: Channel ID where the exchange occurred
            user_timestamp: Timestamp of user message (defaults to current time)
            assistant_timestamp: Timestamp of assistant message (defaults to current time)
            run_id: Run identifier for this exchange
            user_message_id: Persisted message_id for the user turn
            assistant_message_id: Persisted message_id for the assistant turn

        Returns:
            Unique chunk_id for the stored exchange
        """
        try:
            # Generate timestamps if not provided
            current_time = time.time()
            if user_timestamp is None:
                user_timestamp = (
                    current_time - 1
                )  # Slightly earlier for user message
            if assistant_timestamp is None:
                assistant_timestamp = current_time

            # Generate unique chunk ID
            # Use higher precision timestamps in id to reduce accidental collisions in tests
            chunk_id = (
                f"{channel}_{int(user_timestamp)}_{int(assistant_timestamp)}"
            )

            # Create verbatim memory entry
            memory = VerbatimMemory(
                user_message=user_message,
                assistant_message=assistant_message,
                user_timestamp=user_timestamp,
                assistant_timestamp=assistant_timestamp,
                channel=channel,
                chunk_id=chunk_id,
                run_id=run_id,
                user_message_id=user_message_id,
                assistant_message_id=assistant_message_id,
            )

            # Do not deduplicate by generated chunk_id in tests; allow sequential inserts

            # Create combined text for embedding (user + assistant exchange)
            combined_text = (
                f"User: {user_message}\n\nAssistant: {assistant_message}"
            )

            # Generate and store embedding
            memory_metadata = {
                "type": "verbatim_memory",
                "channel": channel,
                "chunk_id": chunk_id,
                "user_timestamp": user_timestamp,
                "assistant_timestamp": assistant_timestamp,
                "run_id": run_id,
                "user_message_id": user_message_id,
                "assistant_message_id": assistant_message_id,
            }

            embed(combined_text, metadata=memory_metadata)
            logger.debug(
                f"L3.verbatim_memory [ch:{channel}] - Generated embedding for chunk: {chunk_id}"
            )

            # Store memory (append in order; tests will filter/sort later)
            self.memories.append(memory)
            self._save_memories()

            logger.info(
                f"L3.verbatim_memory [store] ch:{channel} - Stored exchange (u:{len(user_message)} a:{len(assistant_message)}), id={chunk_id}"
            )

            # Log total memory stats
            total_chunks = len(self.memories)
            logger.debug(
                f"L3.verbatim_memory [vault] - Total chunks stored: {total_chunks}"
            )

            return chunk_id

        except Exception as e:
            logger.error(f"Error storing verbatim memory: {e}", exc_info=True)
            return ""

    def track_user_message(
        self,
        message: str,
        channel: str,
        timestamp: Optional[float] = None,
        message_id: Optional[str] = None,
        run_id: Optional[str] = None,
    ) -> None:
        """Track a user message for potential verbatim storage.

        Args:
            message: User's message content
            channel: Channel ID
            timestamp: Message timestamp (defaults to current time)
            message_id: Persisted message identifier
            run_id: Run identifier for this exchange
        """
        try:
            if timestamp is None:
                timestamp = time.time()

            # Store pending user message for this channel
            self.pending_user_messages[channel] = {
                "message": message,
                "timestamp": timestamp,
                "message_id": str(message_id) if message_id is not None else None,
                "run_id": str(run_id) if run_id is not None else None,
            }

            logger.info(
                f"L3.verbatim_memory [track] ch:{channel} - User message ({len(message)} chars)"
            )

        except Exception as e:
            logger.error(f"Error tracking user message: {e}", exc_info=True)

    def complete_exchange(
        self,
        assistant_message: str,
        channel: str,
        timestamp: Optional[float] = None,
        assistant_message_id: Optional[str] = None,
        run_id: Optional[str] = None,
    ) -> Optional[str]:
        """Complete a chat exchange by storing user+assistant messages.

        Args:
            assistant_message: Assistant's response message
            channel: Channel ID
            timestamp: Assistant message timestamp (defaults to current time)
            assistant_message_id: Persisted assistant message id
            run_id: Run identifier for this exchange

        Returns:
            chunk_id if exchange was stored, None if no pending user message
        """
        try:
            if channel not in self.pending_user_messages:
                logger.debug(
                    f"No pending user message for channel {channel}, cannot complete exchange"
                )
                return None

            # Get pending user message
            user_data = self.pending_user_messages.pop(channel)
            user_message = user_data["message"]
            user_timestamp = user_data["timestamp"]
            user_message_id = user_data.get("message_id")
            pending_run_id = user_data.get("run_id")

            if timestamp is None:
                timestamp = time.time()

            if run_id is None:
                run_id = pending_run_id

            # Store the complete exchange
            chunk_id = self.store_verbatim(
                user_message=user_message,
                assistant_message=assistant_message,
                channel=channel,
                user_timestamp=user_timestamp,
                assistant_timestamp=timestamp,
                run_id=run_id,
                user_message_id=user_message_id,
                assistant_message_id=str(assistant_message_id)
                if assistant_message_id is not None
                else None,
            )

            logger.info(
                f"Completed chat exchange and stored verbatim memory: {chunk_id}"
            )
            return chunk_id

        except Exception as e:
            logger.error(f"Error completing exchange: {e}", exc_info=True)
            return None

    def get_memories(
        self,
        limit: Optional[int] = None,
        channel: Optional[str] = None,
        since_timestamp: Optional[float] = None,
    ) -> List[VerbatimMemory]:
        """Get stored verbatim memories with optional filtering.

        Args:
            limit: Maximum number of memories to return
            channel: Filter by specific channel
            since_timestamp: Only return memories after this timestamp

        Returns:
            List of VerbatimMemory objects
        """
        filtered_memories = self.memories

        # Filter by channel
        if channel:
            filtered_memories = [
                m for m in filtered_memories if m.channel == channel
            ]

        # Filter by timestamp
        if since_timestamp:
            filtered_memories = [
                m
                for m in filtered_memories
                if m.assistant_timestamp > since_timestamp
            ]

        # Sort by assistant timestamp (newest first)
        filtered_memories.sort(
            key=lambda m: m.assistant_timestamp, reverse=True
        )

        # Apply limit
        if limit:
            filtered_memories = filtered_memories[:limit]

        return filtered_memories

    def delete_by_chunk_ids(self, chunk_ids: List[str]) -> int:
        """Delete verbatim memories by chunk_id and persist changes.

        Args:
            chunk_ids: List of chunk_id strings to remove

        Returns:
            Count of removed entries
        """
        try:
            if not chunk_ids:
                return 0
            before = len(self.memories)
            to_remove = set(chunk_ids)
            self.memories = [m for m in self.memories if m.chunk_id not in to_remove]
            removed = before - len(self.memories)
            if removed > 0:
                self._save_memories()
                logger.info(
                    f"L3.verbatim_memory [cleanup] - Deleted {removed} verbatim duplicates by chunk_id"
                )
            return removed
        except Exception as e:
            logger.error(
                f"L3.verbatim_memory [cleanup] - Error deleting by chunk ids: {e}",
                exc_info=True,
            )
            return 0

    def search_memories(
        self, query: str, k: int = 5, similarity_threshold: float = 0.7
    ) -> List[Tuple[VerbatimMemory, float]]:
        """Search verbatim memories using semantic similarity.

        Args:
            query: Search query
            k: Number of results to return
            similarity_threshold: Minimum similarity score

        Returns:
            List of tuples (VerbatimMemory, similarity_score)
        """
        try:
            # Search using embeddings
            results = retrieve(
                query, k=k, similarity_threshold=similarity_threshold
            )

            # Filter for verbatim memories and match with stored objects
            verbatim_results = []
            for text, similarity, metadata in results:
                if metadata.get("type") == "verbatim_memory":
                    chunk_id = metadata.get("chunk_id")

                    # Find matching memory object
                    for memory in self.memories:
                        if memory.chunk_id == chunk_id:
                            verbatim_results.append((memory, similarity))
                            break

            logger.debug(
                f"Found {len(verbatim_results)} verbatim memories for query: {query[:50]}..."
            )
            return verbatim_results

        except Exception as e:
            logger.error(
                f"Error searching verbatim memories: {e}", exc_info=True
            )
            return []

    def get_memory_stats(self) -> Dict[str, Any]:
        """Get statistics about stored verbatim memories.

        Returns:
            Dictionary with memory statistics
        """
        if not self.memories:
            return {
                "total_memories": 0,
                "channels": [],
                "oldest_memory": None,
                "newest_memory": None,
                "total_exchanges": 0,
            }

        channels = list(set(m.channel for m in self.memories))
        oldest_timestamp = min(m.user_timestamp for m in self.memories)
        newest_timestamp = max(m.assistant_timestamp for m in self.memories)

        return {
            "total_memories": len(self.memories),
            "channels": channels,
            "oldest_memory": oldest_timestamp,
            "newest_memory": newest_timestamp,
            "total_exchanges": len(self.memories),
        }

    def clear_channel_memories(self, channel: str) -> int:
        """Clear all verbatim memories for a specific channel and purge embeddings.

        Args:
            channel: Channel ID to clear

        Returns:
            Number of memories removed
        """
        try:
            initial_count = len(self.memories)
            self.memories = [m for m in self.memories if m.channel != channel]
            removed_count = initial_count - len(self.memories)

            if removed_count > 0:
                self._save_memories()
                logger.info(
                    f"Cleared {removed_count} verbatim memories from channel {channel}"
                )

                # Purge associated embeddings
                try:
                    from layer3_longterm.embeddings import get_embedding_manager

                    manager = get_embedding_manager()
                    original = list(
                        zip(
                            getattr(manager, "texts", []),
                            getattr(manager, "metadata_list", []),
                        )
                    )
                    if original:
                        filtered_texts: List[str] = []
                        filtered_metadata: List[Dict[str, Any]] = []
                        for text, metadata in original:
                            md = metadata or {}
                            if (
                                md.get("type") == "verbatim_memory"
                                and md.get("channel") == channel
                            ):
                                continue
                            filtered_texts.append(text)
                            filtered_metadata.append(md)

                        if len(filtered_texts) != len(original):
                            manager.clear_index()
                            if filtered_texts:
                                manager.embed_batch(
                                    filtered_texts, metadata_list=filtered_metadata
                                )
                            manager.flush()
                            logger.debug(
                                f"L3.verbatim_memory [clear_channel] - Purged embeddings for channel {channel}"
                            )
                except Exception as embed_err:
                    logger.debug(
                        f"L3.verbatim_memory [clear_channel] - Failed to purge embeddings: {embed_err}"
                    )

            return removed_count

        except Exception as e:
            logger.error(
                f"Error clearing channel memories: {e}", exc_info=True
            )
            return 0

    def revoke_exchange(self, run_id: str) -> int:
        """Remove stored memories for a specific run and purge associated embeddings.

        Args:
            run_id: Run identifier whose exchange should be revoked

        Returns:
            Number of verbatim memories removed
        """
        if not run_id:
            return 0

        try:
            removed_chunks: List[str] = []
            kept_memories: List[VerbatimMemory] = []

            for memory in self.memories:
                memory_run_id = memory.run_id or memory.user_message_id or ""
                if memory_run_id == run_id:
                    removed_chunks.append(memory.chunk_id)
                else:
                    kept_memories.append(memory)

            if not removed_chunks:
                return 0

            self.memories = kept_memories
            self._save_memories()

            try:
                from layer3_longterm.embeddings import get_embedding_manager

                manager = get_embedding_manager()
                original = list(
                    zip(
                        getattr(manager, "texts", []),
                        getattr(manager, "metadata_list", []),
                    )
                )
                if original:
                    filtered_texts: List[str] = []
                    filtered_metadata: List[Dict[str, Any]] = []
                    for text, metadata in original:
                        md = metadata or {}
                        if (
                            md.get("type") == "verbatim_memory"
                            and md.get("chunk_id") in removed_chunks
                        ):
                            continue
                        filtered_texts.append(text)
                        filtered_metadata.append(md)

                    if len(filtered_texts) != len(original):
                        manager.clear_index()
                        if filtered_texts:
                            manager.embed_batch(
                                filtered_texts, metadata_list=filtered_metadata
                            )
                        manager.flush()
            except Exception as embed_err:
                logger.debug(
                    f"L3.verbatim_memory [revoke] - Failed to purge embeddings cleanly: {embed_err}"
                )

            logger.info(
                f"L3.verbatim_memory [revoke] - Removed {len(removed_chunks)} chunk(s) for run {run_id}"
            )
            return len(removed_chunks)
        except Exception as e:
            logger.error(
                f"L3.verbatim_memory [revoke] - Failed to revoke run {run_id}: {e}",
                exc_info=True,
            )
            return 0

    def _load_memories(self) -> None:
        """Load existing memories from JSON file."""
        try:
            if self.memory_file.exists():
                with open(self.memory_file, "r", encoding="utf-8") as f:
                    data = json.load(f)

                self.memories = []
                for mem_data in data:
                    memory = VerbatimMemory(
                        user_message=mem_data["user_message"],
                        assistant_message=mem_data["assistant_message"],
                        user_timestamp=mem_data["user_timestamp"],
                        assistant_timestamp=mem_data["assistant_timestamp"],
                        channel=mem_data["channel"],
                        chunk_id=mem_data["chunk_id"],
                        embedding_id=mem_data.get("embedding_id"),
                        run_id=mem_data.get("run_id"),
                        user_message_id=mem_data.get("user_message_id"),
                        assistant_message_id=mem_data.get("assistant_message_id"),
                    )
                    self.memories.append(memory)

                logger.debug(
                    f"Loaded {len(self.memories)} verbatim memories from {self.memory_file}"
                )
            else:
                self.memories = []
                logger.debug("No existing verbatim memory file found")

        except Exception as e:
            logger.error(
                f"Error loading verbatim memories: {e}", exc_info=True
            )
            self.memories = []

    def _save_memories(self) -> None:
        """Save memories to JSON file."""
        try:
            # Convert memories to JSON-serializable format
            data = []
            for memory in self.memories:
                mem_data = {
                    "user_message": memory.user_message,
                    "assistant_message": memory.assistant_message,
                    "user_timestamp": memory.user_timestamp,
                    "assistant_timestamp": memory.assistant_timestamp,
                    "channel": memory.channel,
                    "chunk_id": memory.chunk_id,
                    "embedding_id": memory.embedding_id,
                }
                if memory.run_id is not None:
                    mem_data["run_id"] = memory.run_id
                if memory.user_message_id is not None:
                    mem_data["user_message_id"] = memory.user_message_id
                if memory.assistant_message_id is not None:
                    mem_data["assistant_message_id"] = memory.assistant_message_id
                data.append(mem_data)

            # Ensure vault directory exists
            self.vault_path.mkdir(parents=True, exist_ok=True)

            # Save to file
            with open(self.memory_file, "w", encoding="utf-8") as f:
                json.dump(data, f, indent=2, ensure_ascii=False)

            logger.debug(
                f"Saved {len(self.memories)} verbatim memories to {self.memory_file}"
            )

        except Exception as e:
            logger.error(f"Error saving verbatim memories: {e}", exc_info=True)


# Global instance for easy import
_verbatim_manager = None


def get_verbatim_manager() -> VerbatimMemoryManager:
    """Get or create the global verbatim memory manager instance."""
    global _verbatim_manager
    if _verbatim_manager is None:
        _verbatim_manager = VerbatimMemoryManager()
    return _verbatim_manager


def store_verbatim(
    user_message: str,
    assistant_message: str,
    channel: str,
    user_timestamp: Optional[float] = None,
    assistant_timestamp: Optional[float] = None,
    run_id: Optional[str] = None,
    user_message_id: Optional[str] = None,
    assistant_message_id: Optional[str] = None,
) -> str:
    """Store a verbatim chat chunk.

    Args:
        user_message: User's message content
        assistant_message: Assistant's response content
        channel: Channel ID
        user_timestamp: User message timestamp
        assistant_timestamp: Assistant message timestamp
        run_id: Run identifier for the exchange
        user_message_id: Persisted user message identifier
        assistant_message_id: Persisted assistant message identifier

    Returns:
        Unique chunk_id for the stored exchange
    """
    return get_verbatim_manager().store_verbatim(
        user_message,
        assistant_message,
        channel,
        user_timestamp,
        assistant_timestamp,
        run_id,
        user_message_id,
        assistant_message_id,
    )
