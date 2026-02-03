"""
One-time cross-type memory deduplication utilities.

Removes overlaps between Theo memories and Verbatim memories, keeping Theo memories
as the source of truth and deleting near-duplicate verbatim chunks.

Also provides a full embedding index rebuild after cleanup to remove orphan vectors.
"""

from typing import List

from layer3_longterm.embeddings import (
    retrieve,
    clear_embeddings,
    embed,
    ContentType,
)
from layer3_longterm.verbatim_memory import get_verbatim_manager
from layer3_longterm.theo_memory import get_theo_manager
from layer3_longterm.human_memory import get_memory_extractor
from utils.logger import get_logger

logger = get_logger(__name__)


def run_cross_type_dedupe(similarity_threshold: float = 0.92, preview: bool = False) -> dict:
    """Remove overlaps between Theo and Verbatim memories.

    Policy: Keep Theo memories; remove verbatim chunks that are semantically
    near-duplicates of any Theo memory. Does not delete Theo or Human memories.

    Args:
        similarity_threshold: Threshold for Theo↔Verbatim similarity
        preview: If True, do not modify storage; just report what would change

    Returns:
        Summary dict with counts and lists of affected IDs
    """
    theo = get_theo_manager()
    verb = get_verbatim_manager()

    # Collect target Theo memories
    theo_list = list(getattr(theo, "memories", []) or [])
    remove_chunks: List[str] = []

    logger.info(
        f"[DEDupe] Starting cross-type dedupe: theo={len(theo_list)} vs verbatim={len(verb.memories)}"
    )

    for tm in theo_list:
        try:
            results = retrieve(
                tm.content,
                k=20,
                similarity_threshold=0.0,
                content_type_filter=ContentType.MEMORY,
            )
            for _text, sim, md in results:
                mtype = (md or {}).get("type")
                if mtype != "verbatim_memory":
                    continue
                if float(sim) >= similarity_threshold:
                    cid = md.get("chunk_id")
                    if cid:
                        remove_chunks.append(cid)
        except Exception as e:
            logger.debug(f"[DEDupe] Error while checking Theo memory similarity: {e}")

    # Unique set of chunks to remove
    remove_set = sorted(set(remove_chunks))
    removed_count = 0
    if not preview and remove_set:
        removed_count = verb.delete_by_chunk_ids(remove_set)

    logger.info(
        f"[DEDupe] Identified {len(remove_set)} verbatim overlaps; removed={removed_count} (preview={preview})"
    )

    # Rebuild embedding index to remove orphans and ensure consistency
    # by re-embedding current memories
    if not preview:
        try:
            _rebuild_embedding_index()
        except Exception as e:
            logger.error(f"[DEDupe] Failed to rebuild embedding index: {e}")

    return {
        "identified_verbatim_duplicates": len(remove_set),
        "removed_verbatim_duplicates": removed_count,
        "removed_chunk_ids": remove_set,
        "preview": preview,
    }


def _rebuild_embedding_index() -> None:
    """Rebuild the full embedding index from current memory stores."""
    logger.info("[DEDupe] Rebuilding embedding index from memory stores…")
    clear_embeddings()

    theo = get_theo_manager()
    verb = get_verbatim_manager()
    human = get_memory_extractor(None)

    # Re-embed Theo memories
    for m in getattr(theo, "memories", []) or []:
        metadata = {
            "type": "theo_memory",
            "memory_id": m.memory_id,
            "timestamp": m.timestamp,
            "importance": m.importance,
        }
        embed(m.content, metadata=metadata)

    # Re-embed Verbatim memories
    for m in getattr(verb, "memories", []) or []:
        combined_text = f"User: {m.user_message}\n\nAssistant: {m.assistant_message}"
        metadata = {
            "type": "verbatim_memory",
            "channel": m.channel,
            "chunk_id": m.chunk_id,
            "user_timestamp": m.user_timestamp,
            "assistant_timestamp": m.assistant_timestamp,
        }
        embed(combined_text, metadata=metadata)

    # Re-embed Human memories
    for m in getattr(human, "memories", []) or []:
        metadata = {
            "type": "human_memory",
            "channel": m.channel,
            "timestamp": m.timestamp,
            "confidence": getattr(m, "confidence", 0.8),
        }
        embed(m.content, metadata=metadata)

    logger.info(
        f"[DEDupe] Rebuilt embeddings: theo={len(getattr(theo, 'memories', []) or [])}, "
        f"verbatim={len(getattr(verb, 'memories', []) or [])}, "
        f"human={len(getattr(human, 'memories', []) or [])}"
    )
