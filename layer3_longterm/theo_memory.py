"""
Theo Memory Management for Layer 3 - Long Term Memory

Provides CRUD operations for Theo's self-managed memories including string content,
timestamps, importance rankings (0-100), and embeddings. Theo can manage these
memories through tools (to be added later).

SPECS ALIGNMENT (docs/specs.md lines 28-32):
- Memory storage: content, timestamp, importance (0-100), embedding
- Importance=100 memories are ALWAYS included in context window (guaranteed)
- Retrieval ranking: 1) Relevance (similarity), 2) Recency, 3) Importance
- Auto-generates last_modified timestamp (not a user-provided parameter)
- Stored in vault/theo_memories.json
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
class TheoMemory:
    """Data class for storing Theo's self-managed memory entries."""

    content: str
    timestamp: float
    importance: int  # 0-100 scale
    memory_id: str  # Unique identifier
    embedding_id: Optional[str] = None
    last_modified: Optional[float] = None
    metadata: Optional[Dict[str, Any]] = None


class TheoMemoryManager:
    """Manages Theo's self-managed memories with CRUD operations."""

    def __init__(self, vault_path: Optional[str] = None):
        """Initialize the Theo memory manager.

        Args:
            vault_path: Path to vault directory for storing memories
        """
        # Resolve vault root centrally (with legacy fallback)
        try:
            if vault_path is None:
                from utils.vault_paths import get_vault_root
                self.vault_path = Path(get_vault_root())
            else:
                self.vault_path = Path(vault_path)
        except Exception:
            self.vault_path = Path(vault_path or "vault")
        self.memory_file = self.vault_path / "theo_memories.json"

        # Memory storage
        self.memories: List[TheoMemory] = []

        # Load existing memories
        self._load_memories()

        logger.debug(
            f"TheoMemoryManager initialized with {len(self.memories)} existing memories"
        )

    def add_memory(
        self,
        content: str,
        importance: int = 50,
        memory_id: Optional[str] = None,
        metadata: Optional[Dict[str, Any]] = None,
    ) -> str:
        """Add a new Theo memory.

        Args:
            content: Memory content string
            importance: Importance ranking (0-100, default 50)
            memory_id: Optional custom memory ID (will be generated if not provided)

        Returns:
            memory_id of the added memory
        """
        try:
            # Handle list input (Gemini 3 sometimes passes lists instead of strings)
            if isinstance(content, list):
                content = " ".join(str(item) for item in content if item)
            
            # Validate content - prevent empty memories
            if not content or not str(content).strip():
                logger.warning(f"Attempted to create memory with empty content, skipping")
                return ""
            
            # Ensure content is a string
            content = str(content).strip()
            
            # Validate importance
            if (
                not isinstance(importance, int)
                or importance < 0
                or importance > 100
            ):
                logger.warning(
                    f"Invalid importance value: {importance}, clamping to 0-100 range"
                )
                importance = max(0, min(100, int(importance)))

            # Generate memory ID if not provided
            current_time = time.time()
            if memory_id is None:
                memory_id = f"theo_{int(current_time)}_{len(self.memories)}"

            # Check for duplicate memory ID
            if any(m.memory_id == memory_id for m in self.memories):
                logger.warning(
                    f"Memory ID {memory_id} already exists, generating new one"
                )
                memory_id = (
                    f"theo_{int(current_time)}_{len(self.memories)}_dup"
                )

            # Create memory entry
            try:
                memory = TheoMemory(
                    content=content,
                    timestamp=current_time,
                    importance=importance,
                    memory_id=memory_id,
                    last_modified=current_time,
                    metadata=dict(metadata) if isinstance(metadata, dict) else None,
                )
            except TypeError:
                # Support lightweight stubs used in tests that only accept positional args
                memory = TheoMemory(content, current_time, importance)  # type: ignore[arg-type,call-arg]
                setattr(memory, "memory_id", memory_id)
                setattr(memory, "last_modified", current_time)
                setattr(memory, "metadata", dict(metadata) if isinstance(metadata, dict) else None)
                if not hasattr(memory, "embedding_id"):
                    setattr(memory, "embedding_id", None)

            # Generate and store embedding
            memory_metadata = {
                "type": "theo_memory",
                "memory_id": memory_id,
                "timestamp": current_time,
                "importance": importance,
            }
            if isinstance(metadata, dict):
                for key, value in metadata.items():
                    memory_metadata[f"metadata.{key}"] = value

            embed(content, metadata=memory_metadata)
            logger.debug(f"Generated embedding for Theo memory: {memory_id}")

            # Store memory
            self.memories.append(memory)
            self._save_memories()

            logger.info(
                f"Added Theo memory: {memory_id} (importance: {importance})"
            )
            logger.debug(f"Memory content: {content[:100]}...")

            return memory_id

        except Exception as e:
            logger.error(f"Error adding Theo memory: {e}", exc_info=True)
            return ""

    def update_memory(
        self,
        memory_id: str,
        content: Optional[str] = None,
        importance: Optional[int] = None,
    ) -> bool:
        """Update an existing Theo memory.

        Args:
            memory_id: ID of memory to update
            content: New content (if provided)
            importance: New importance (if provided)

        Returns:
            True if memory was found and updated, False otherwise
        """
        try:
            # Find memory by ID
            memory = None
            for m in self.memories:
                if m.memory_id == memory_id:
                    memory = m
                    break

            if memory is None:
                logger.warning(f"Memory not found for update: {memory_id}")
                return False

            # Track changes for logging
            changes = []

            # Update content if provided
            if content is not None:
                memory.content
                memory.content = content
                changes.append(f"content updated")

                # Re-generate embedding if content changed
                memory_metadata = {
                    "type": "theo_memory",
                    "memory_id": memory_id,
                    "timestamp": memory.timestamp,
                    "importance": memory.importance,
                }
                embed(content, metadata=memory_metadata)
                logger.debug(
                    f"Re-generated embedding for updated Theo memory: {memory_id}"
                )

            # Update importance if provided
            if importance is not None:
                if (
                    not isinstance(importance, int)
                    or importance < 0
                    or importance > 100
                ):
                    logger.warning(
                        f"Invalid importance value: {importance}, clamping to 0-100 range"
                    )
                    importance = max(0, min(100, int(importance)))

                old_importance = memory.importance
                memory.importance = importance
                changes.append(f"importance: {old_importance} -> {importance}")

            # Update last modified timestamp
            memory.last_modified = time.time()

            # Save changes
            self._save_memories()

            logger.info(
                f"Updated Theo memory {memory_id}: {', '.join(changes)}"
            )
            return True

        except Exception as e:
            logger.error(f"Error updating Theo memory: {e}", exc_info=True)
            return False

    def delete_memory(self, memory_id: str) -> bool:
        """Delete a Theo memory.

        Args:
            memory_id: ID of memory to delete

        Returns:
            True if memory was found and deleted, False otherwise
        """
        try:
            # Find and remove memory
            initial_count = len(self.memories)
            self.memories = [
                m for m in self.memories if m.memory_id != memory_id
            ]

            if len(self.memories) == initial_count:
                logger.warning(f"Memory not found for deletion: {memory_id}")
                return False

            # Save changes
            self._save_memories()

            logger.info(f"Deleted Theo memory: {memory_id}")
            return True

        except Exception as e:
            logger.error(f"Error deleting Theo memory: {e}", exc_info=True)
            return False

    def get_memory(self, memory_id: str) -> Optional[TheoMemory]:
        """Get a specific Theo memory by ID.

        Args:
            memory_id: ID of memory to retrieve

        Returns:
            TheoMemory object if found, None otherwise
        """
        for memory in self.memories:
            if memory.memory_id == memory_id:
                return memory
        return None

    def get_memories(
        self,
        limit: Optional[int] = None,
        min_importance: Optional[int] = None,
        sort_by: str = "importance",
    ) -> List[TheoMemory]:
        """Get stored Theo memories with optional filtering and sorting.

        Args:
            limit: Maximum number of memories to return
            min_importance: Only return memories with importance >= this value
            sort_by: Sort order - "importance", "timestamp", or "recent" (default: importance)

        Returns:
            List of TheoMemory objects
        """
        filtered_memories = self.memories.copy()

        # Filter by minimum importance
        if min_importance is not None:
            filtered_memories = [
                m for m in filtered_memories if m.importance >= min_importance
            ]

        # Sort memories
        if sort_by == "importance":
            filtered_memories.sort(
                key=lambda m: (m.importance, m.timestamp), reverse=True
            )
        elif sort_by == "timestamp":
            filtered_memories.sort(key=lambda m: m.timestamp, reverse=True)
        elif sort_by == "recent":
            # Sort by last_modified or timestamp if never modified
            filtered_memories.sort(
                key=lambda m: m.last_modified or m.timestamp, reverse=True
            )
        else:
            logger.warning(
                f"Unknown sort_by value: {sort_by}, using importance"
            )
            filtered_memories.sort(
                key=lambda m: (m.importance, m.timestamp), reverse=True
            )

        # Apply limit
        if limit:
            filtered_memories = filtered_memories[:limit]

        return filtered_memories

    def search_memories(
        self,
        query: str,
        k: int = 5,
        similarity_threshold: float = 0.7,
        min_importance: Optional[int] = None,
    ) -> List[Tuple[TheoMemory, float]]:
        """Search Theo memories using semantic similarity.

        Args:
            query: Search query
            k: Number of results to return
            similarity_threshold: Minimum similarity score
            min_importance: Only search memories with importance >= this value

        Returns:
            List of tuples (TheoMemory, similarity_score) sorted by relevance
        """
        try:
            # Search using embeddings
            results = retrieve(
                query, k=k, similarity_threshold=similarity_threshold
            )

            # Filter for Theo memories and match with stored objects
            theo_results = []
            for text, similarity, metadata in results:
                if metadata.get("type") == "theo_memory":
                    memory_id = metadata.get("memory_id")

                    # Find matching memory object
                    for memory in self.memories:
                        if memory.memory_id == memory_id:
                            # Apply importance filter if specified
                            if (
                                min_importance is None
                                or memory.importance >= min_importance
                            ):
                                theo_results.append((memory, similarity))
                            break

            # Sort by combined score: similarity + importance boost
            # Higher importance memories get a small boost to similarity score
            def combined_score(item):
                memory, similarity = item
                importance_boost = (
                    memory.importance / 1000.0
                )  # Small boost (0-0.1)
                return similarity + importance_boost

            theo_results.sort(key=combined_score, reverse=True)

            logger.debug(
                f"Found {len(theo_results)} Theo memories for query: {query[:50]}..."
            )
            return theo_results

        except Exception as e:
            logger.error(f"Error searching Theo memories: {e}", exc_info=True)
            return []

    def get_memory_stats(self) -> Dict[str, Any]:
        """Get statistics about stored Theo memories.

        Returns:
            Dictionary with memory statistics
        """
        if not self.memories:
            return {
                "total_memories": 0,
                "average_importance": 0.0,
                "importance_distribution": {},
                "oldest_memory": None,
                "newest_memory": None,
                "high_importance_count": 0,  # importance >= 80
            }

        importance_values = [m.importance for m in self.memories]
        average_importance = sum(importance_values) / len(importance_values)

        # Importance distribution (by ranges)
        importance_distribution = {
            "0-20": sum(1 for i in importance_values if 0 <= i <= 20),
            "21-40": sum(1 for i in importance_values if 21 <= i <= 40),
            "41-60": sum(1 for i in importance_values if 41 <= i <= 60),
            "61-80": sum(1 for i in importance_values if 61 <= i <= 80),
            "81-100": sum(1 for i in importance_values if 81 <= i <= 100),
        }

        oldest_timestamp = min(m.timestamp for m in self.memories)
        newest_timestamp = max(m.timestamp for m in self.memories)
        high_importance_count = sum(
            1 for m in self.memories if m.importance >= 80
        )

        return {
            "total_memories": len(self.memories),
            "average_importance": average_importance,
            "importance_distribution": importance_distribution,
            "oldest_memory": oldest_timestamp,
            "newest_memory": newest_timestamp,
            "high_importance_count": high_importance_count,
        }

    def get_guaranteed_memories(self) -> List[TheoMemory]:
        """Get memories with importance = 100 (guaranteed for context window).

        Returns:
            List of TheoMemory objects with importance = 100
        """
        guaranteed = [m for m in self.memories if m.importance == 100]
        guaranteed.sort(
            key=lambda m: m.timestamp, reverse=True
        )  # Newest first
        return guaranteed

    def cleanup_empty_memories(self) -> int:
        """Remove all empty memories (content is empty or whitespace only).
        
        Returns:
            Number of empty memories removed
        """
        try:
            initial_count = len(self.memories)
            
            # Filter out empty memories
            self.memories = [
                m for m in self.memories 
                if m.content and m.content.strip()
            ]
            
            removed_count = initial_count - len(self.memories)
            
            if removed_count > 0:
                # Save changes
                self._save_memories()
                logger.info(f"Cleaned up {removed_count} empty Theo memories")
            
            return removed_count
            
        except Exception as e:
            logger.error(f"Error cleaning up empty memories: {e}", exc_info=True)
            return 0

    def revoke_by_run_id(self, run_id: str) -> int:
        """Remove all memories created during a specific run.

        Used when regenerating a response to undo any memories created during
        the original response generation.

        Args:
            run_id: The run identifier whose memories should be revoked

        Returns:
            Number of memories removed
        """
        if not run_id:
            return 0

        try:
            initial_count = len(self.memories)
            removed_memory_ids: List[str] = []

            # Find memories with matching run_id in metadata
            kept_memories: List[TheoMemory] = []
            for memory in self.memories:
                metadata = getattr(memory, "metadata", None) or {}
                memory_run_id = metadata.get("run_id") if isinstance(metadata, dict) else None
                if memory_run_id == run_id:
                    removed_memory_ids.append(memory.memory_id)
                else:
                    kept_memories.append(memory)

            if not removed_memory_ids:
                return 0

            self.memories = kept_memories
            self._save_memories()

            # Purge embeddings for removed memories
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
                            md.get("type") == "theo_memory"
                            and md.get("memory_id") in removed_memory_ids
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
                    f"L3.theo_memory [revoke] - Failed to purge embeddings cleanly: {embed_err}"
                )

            removed_count = initial_count - len(self.memories)
            logger.info(
                f"L3.theo_memory [revoke] - Removed {removed_count} memory(ies) for run {run_id}"
            )
            return removed_count

        except Exception as e:
            logger.error(
                f"L3.theo_memory [revoke] - Failed to revoke run {run_id}: {e}",
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
                    try:
                        memory = TheoMemory(
                            content=mem_data["content"],
                            timestamp=mem_data["timestamp"],
                            importance=mem_data["importance"],
                            memory_id=mem_data["memory_id"],
                            embedding_id=mem_data.get("embedding_id"),
                            last_modified=mem_data.get("last_modified"),
                            metadata=mem_data.get("metadata"),
                        )
                    except TypeError:
                        memory = TheoMemory(  # type: ignore[call-arg]
                            mem_data["content"],
                            mem_data["timestamp"],
                            mem_data["importance"],
                        )
                        setattr(memory, "memory_id", mem_data["memory_id"])
                        setattr(memory, "embedding_id", mem_data.get("embedding_id"))
                        setattr(memory, "last_modified", mem_data.get("last_modified"))
                        setattr(memory, "metadata", mem_data.get("metadata"))
                    self.memories.append(memory)

                logger.debug(
                    f"Loaded {len(self.memories)} Theo memories from {self.memory_file}"
                )
            else:
                self.memories = []
                logger.debug("No existing Theo memory file found")

        except Exception as e:
            logger.error(f"Error loading Theo memories: {e}", exc_info=True)
            self.memories = []

    def _save_memories(self) -> None:
        """Save memories to JSON file."""
        try:
            # Convert memories to JSON-serializable format
            data = []
            for memory in self.memories:
                mem_data = {
                    "content": memory.content,
                    "timestamp": memory.timestamp,
                    "importance": memory.importance,
                    "memory_id": memory.memory_id,
                    "embedding_id": memory.embedding_id,
                    "last_modified": memory.last_modified,
                    "metadata": memory.metadata,
                }
                data.append(mem_data)

            # Ensure vault directory exists
            self.vault_path.mkdir(parents=True, exist_ok=True)

            # Save to file
            with open(self.memory_file, "w", encoding="utf-8") as f:
                json.dump(data, f, indent=2, ensure_ascii=False)

            logger.debug(
                f"Saved {len(self.memories)} Theo memories to {self.memory_file}"
            )

        except Exception as e:
            logger.error(f"Error saving Theo memories: {e}", exc_info=True)


# Global instance for easy import
_theo_manager = None


def get_theo_manager() -> TheoMemoryManager:
    """Get or create the global Theo memory manager instance."""
    global _theo_manager
    if _theo_manager is None:
        _theo_manager = TheoMemoryManager()
    return _theo_manager


def add_memory(
    content: str,
    importance: int = 50,
    memory_id: Optional[str] = None,
    metadata: Optional[Dict[str, Any]] = None,
) -> str:
    """Add a new Theo memory.

    Args:
        content: Memory content string
        importance: Importance ranking (0-100, default 50)
        memory_id: Optional custom memory ID

    Returns:
        memory_id of the added memory
    """
    return get_theo_manager().add_memory(content, importance, memory_id, metadata)


def update_memory(
    memory_id: str,
    content: Optional[str] = None,
    importance: Optional[int] = None,
) -> bool:
    """Update an existing Theo memory.

    Args:
        memory_id: ID of memory to update
        content: New content (if provided)
        importance: New importance (if provided)

    Returns:
        True if memory was found and updated, False otherwise
    """
    return get_theo_manager().update_memory(memory_id, content, importance)


def delete_memory(memory_id: str) -> bool:
    """Delete a Theo memory.

    Args:
        memory_id: ID of memory to delete

    Returns:
        True if memory was found and deleted, False otherwise
    """
    return get_theo_manager().delete_memory(memory_id)


def cleanup_empty_memories() -> int:
    """Remove all empty memories (content is empty or whitespace only).
    
    Returns:
        Number of empty memories removed
    """
    return get_theo_manager().cleanup_empty_memories()


def revoke_by_run_id(run_id: str) -> int:
    """Remove all memories created during a specific run.

    Used when regenerating a response to undo any memories created during
    the original response generation.

    Args:
        run_id: The run identifier whose memories should be revoked

    Returns:
        Number of memories removed
    """
    return get_theo_manager().revoke_by_run_id(run_id)
