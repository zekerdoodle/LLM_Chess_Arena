"""
Layer 3 Memory Manager - Unified Interface for Long-term Memory

Provides a unified interface for accessing all Layer 3 memory types:
- Human memories (extracted automatically)
- Verbatim memories (chat chunks)
- Theo memories (self-managed)

Handles memory retrieval, context building, and token budget management.

SPECS ALIGNMENT (docs/specs.md lines 29-32):
- Memories surfaced using vector embeddings (relevance-first, blended with freshness)
- Ranking: 1) Relevance (similarity to query), 2) Recency (freshness boost), 3) Importance (Theo only)
- Theo importance=100 memories are ALWAYS included, even if they exceed budget
- Each memory type has a token budget allocation
- Verbatim memories already in chat history are NOT shown (deduplication)
"""

import re
import time
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Dict, List, Optional, Set, Tuple

import layer3_longterm.human_memory as human_memory
import layer3_longterm.verbatim_memory as verbatim_memory
import layer3_longterm.theo_memory as theo_memory
import layer3_longterm.embeddings as embeddings
from utils.logger import get_logger
from utils.prompt_labels import (
    HUMAN_MEMORIES_HEADER,
    THEO_MEMORIES_HEADER,
    VERBATIM_MEMORIES_HEADER,
)
from utils.token_counter import count_tokens, truncate_context

logger = get_logger(__name__)

RECENCY_BOOST = 0.05  # Small freshness weight per specs (relevance remains dominant)
MAX_FALLBACK_PER_TYPE = 5  # Cap fallback lists when embeddings are unavailable
MAX_RECENCY_EXTRAS = 2  # Allow only a couple of recency-only boosters
THEO_MIN_SIMILARITY = 0.18
VERBATIM_CHANNEL_BOOST = 0.05
KEYWORD_MATCH_STEP = 0.04
KEYWORD_MAX_BOOST = 0.16
NAME_QUERY_BONUS = 0.08
MIN_KEYWORD_LENGTH = 4


@dataclass
class MemoryContext:
    """Container for memory context information."""
    
    human_memories: List[human_memory.HumanMemory]
    verbatim_memories: List[verbatim_memory.VerbatimMemory]
    theo_memories: List[theo_memory.TheoMemory]
    total_tokens: int
    token_breakdown: Dict[str, int]


class Layer3MemoryManager:
    """
    Unified interface for all Layer 3 memory types.
    
    Provides memory retrieval, context building, and token budget management
    for human, verbatim, and theo memories.
    """
    
    def __init__(self, model_selector=None, vault_path: Optional[str] = None):
        """
        Initialize the Layer 3 memory manager.
        
        Args:
            model_selector: ModelSelector instance for AI calls (needed for human memory extraction)
            vault_path: Path to vault directory for storing memories
        """
        try:
            if vault_path is None:
                from utils.vault_paths import get_vault_root
                self.vault_path = Path(get_vault_root())
            else:
                self.vault_path = Path(vault_path)
        except Exception:
            self.vault_path = Path(vault_path or "vault")
        self.model_selector = model_selector

        vault_str = str(self.vault_path)

        # Initialize individual memory managers (respect per-instance vault override)
        self.human_memory_extractor = get_memory_extractor(model_selector, vault_str)
        self.verbatim_manager = get_verbatim_manager(vault_str)
        self.theo_manager = get_theo_manager(vault_str)
        
        logger.debug("L3.memory_manager [setup] - Initialized Layer 3 memory manager")
        
        # Safely log memory counts (handle mock objects in tests)
        try:
            human_count = len(self.human_memory_extractor.memories) if hasattr(self.human_memory_extractor, 'memories') else 0
            verbatim_count = len(self.verbatim_manager.memories) if hasattr(self.verbatim_manager, 'memories') else 0
            theo_count = len(self.theo_manager.memories) if hasattr(self.theo_manager, 'memories') else 0
            logger.debug(f"L3.memory_manager [setup] - Human memories: {human_count}")
            logger.debug(f"L3.memory_manager [setup] - Verbatim memories: {verbatim_count}")
            logger.debug(f"L3.memory_manager [setup] - Theo memories: {theo_count}")
        except Exception as e:
            logger.debug(f"L3.memory_manager [setup] - Could not get memory counts (likely test mocks): {e}")
    
    def get_memory_context(
        self,
        query: str,
        max_tokens: int,
        token_allocations: Dict[str, int],
        channel: Optional[str] = None,
        limit_per_type: Optional[int] = None,
        recent_chat_history: Optional[List[Dict]] = None
    ) -> MemoryContext:
        """
        Get memory context for a given query within token budget constraints.
        
        Args:
            query: Query string for memory retrieval
            max_tokens: Maximum total tokens for memory context
            token_allocations: Dict with token allocations for each memory type
                Format: {"human_memory": 15, "verbatim_memory": 10, "theo_memory": 20}
            channel: Optional channel ID for channel-specific memories
            limit_per_type: Optional limit per memory type (default: no limit)
            recent_chat_history: Optional recent chat history to avoid duplication in verbatim memories
            
        Returns:
            MemoryContext with retrieved memories and token information
        """
        try:
            logger.debug(f"L3.memory_manager [retrieval] - Getting memory context for query: {query[:50]}...")
            
            # Calculate token budgets for each memory type
            total_allocation = sum(token_allocations.values())
            if total_allocation == 0:
                logger.warning("L3.memory_manager [retrieval] - No token allocations provided")
                return MemoryContext([], [], [], 0, {})
            
            # Normalize allocations to max_tokens
            token_budgets = {}
            for memory_type, allocation in token_allocations.items():
                token_budgets[memory_type] = int((allocation / total_allocation) * max_tokens)
            
            logger.debug(f"L3.memory_manager [retrieval] - Token budgets: {token_budgets}")
            
            # Retrieve memories for each type
            human_budget = token_budgets.get("human_memory", 0)
            verbatim_budget = token_budgets.get("verbatim_memory", 0)
            theo_budget = token_budgets.get("theo_memory", 0)

            human_memories = (
                self._get_human_memories(query, human_budget, channel, limit_per_type)
                if human_budget > 0
                else []
            )
            verbatim_memories = (
                self._get_verbatim_memories(query, verbatim_budget, channel, limit_per_type, recent_chat_history)
                if verbatim_budget > 0
                else []
            )
            theo_memories = (
                self._get_theo_memories(query, theo_budget, limit_per_type)
                if theo_budget > 0
                else []
            )
            
            # Calculate token usage
            token_breakdown = {
                "human_memory": self._count_memories_tokens(human_memories),
                "verbatim_memory": self._count_memories_tokens(verbatim_memories),
                "theo_memory": self._count_memories_tokens(theo_memories)
            }
            total_tokens = sum(token_breakdown.values())
            
            logger.info(f"L3.memory_manager [retrieval] - Retrieved {len(human_memories)} human, {len(verbatim_memories)} verbatim, {len(theo_memories)} theo memories ({total_tokens} tokens)")
            
            return MemoryContext(
                human_memories=human_memories,
                verbatim_memories=verbatim_memories,
                theo_memories=theo_memories,
                total_tokens=total_tokens,
                token_breakdown=token_breakdown
            )
            
        except Exception as e:
            logger.error(f"L3.memory_manager [retrieval] - Error getting memory context: {e}", exc_info=True)
            return MemoryContext([], [], [], 0, {})
    
    def format_memory_context(self, memory_context: MemoryContext) -> str:
        """
        Format memory context into a string for inclusion in prompts.
        
        Args:
            memory_context: MemoryContext object with retrieved memories
            
        Returns:
            Formatted string representation of memory context
        """
        try:
            sections = []
            
            # Format human memories with explicit labeling and description
            if memory_context.human_memories:
                human_lines = [HUMAN_MEMORIES_HEADER]
                for i, memory in enumerate(memory_context.human_memories, 1):
                    timestamp = time.strftime("%Y-%m-%d %H:%M", time.localtime(memory.timestamp))
                    human_lines.append(f"{i}. [{timestamp}] {memory.content}")
                sections.append("\n".join(human_lines) + "\n")

            # Format verbatim memories with explicit labeling and description
            if memory_context.verbatim_memories:
                verbatim_lines = [VERBATIM_MEMORIES_HEADER]
                for i, memory in enumerate(memory_context.verbatim_memories, 1):
                    timestamp = time.strftime("%Y-%m-%d %H:%M", time.localtime(memory.user_timestamp))
                    verbatim_lines.append(f"{i}. [{timestamp}] User: {memory.user_message}")
                    verbatim_lines.append(f"   Assistant: {memory.assistant_message}")
                sections.append("\n".join(verbatim_lines) + "\n")

            # Format Theo memories with explicit labeling and description
            if memory_context.theo_memories:
                theo_lines = [THEO_MEMORIES_HEADER]
                for i, memory in enumerate(memory_context.theo_memories, 1):
                    timestamp = time.strftime("%Y-%m-%d %H:%M", time.localtime(memory.timestamp))
                    theo_lines.append(
                        f"{i}. [{timestamp}] [Importance: {memory.importance}/100] {memory.content}"
                    )
                sections.append("\n".join(theo_lines) + "\n")
            
            if not sections:
                return ""
            
            return "\n".join(sections)
            
        except Exception as e:
            logger.error(f"L3.memory_manager [formatting] - Error formatting memory context: {e}", exc_info=True)
            return ""
    
    def _get_human_memories(
        self,
        query: str,
        max_tokens: int,
        channel: Optional[str] = None,
        limit: Optional[int] = None
    ) -> List[human_memory.HumanMemory]:
        """Get relevant human memories within token budget using relevance and recency.

        Policy (per specs and user request):
        - Sort strictly by semantic relevance (similarity) first.
        - Break ties by recency (newer first).
        - Always fill the allocated token budget with top-ranked items; no minimum
          similarity threshold is applied during retrieval.
        """
        try:
            # IMPORTANT: Human memories should be shared across rooms
            memories = self.human_memory_extractor.get_memories(limit=limit, channel=None)
            if not memories:
                return []

            # Try embedding-based similarity ranking blended with recency
            try:
                results = embeddings.retrieve(query, k=200, similarity_threshold=None)
            except Exception:
                results = []

            # Map (content,timestamp,channel) -> memory with validation
            by_key = {}
            for m in memories:
                try:
                    key = (m.content, m.timestamp, getattr(m, 'channel', None))
                    by_key[key] = m
                except AttributeError as e:
                    logger.debug(
                        f"L3.memory_manager [human] - Skipping memory with missing attributes: {e}"
                    )
                    continue
            ranked: List[Tuple[HumanMemory, float]] = []
            ts_values = [m.timestamp for m in memories]
            tmin = min(ts_values) if ts_values else 0.0
            span = max(1.0, (max(ts_values) - tmin)) if ts_values else 1.0

            def _recency_fraction(mem: HumanMemory) -> float:
                try:
                    return (getattr(mem, "timestamp", 0.0) - tmin) / span
                except Exception:
                    return 0.0

            if results:
                for text, sim, md in results:
                    if not isinstance(md, dict) or md.get("type") != "human_memory":
                        continue
                    channel_id = md.get("channel")
                    ts = md.get("timestamp")
                    key = (text, ts, channel_id)
                    memory_obj = by_key.get(key)
                    if not memory_obj:
                        # Fallback: match by content only
                        candidates = [mm for mm in memories if mm.content == text and (not channel or mm.channel == channel)]
                        if len(candidates) == 1:
                            memory_obj = candidates[0]
                        elif len(candidates) > 1:
                            # Multiple matches: pick best by priority: same channel > most recent > first
                            logger.debug(
                                "L3.memory_manager [human] - Found %d duplicate memories with content: %s",
                                len(candidates),
                                text[:50] + "..." if len(text) > 50 else text
                            )
                            same_channel = [c for c in candidates if channel and getattr(c, "channel", None) == channel]
                            if same_channel:
                                # Sort by timestamp descending, pick most recent from same channel
                                memory_obj = max(same_channel, key=lambda m: getattr(m, "timestamp", 0.0))
                                logger.debug(
                                    "L3.memory_manager [human] - Selected memory from same channel (timestamp: %s)",
                                    memory_obj.timestamp if memory_obj else "unknown"
                                )
                            else:
                                # No same-channel match: pick most recent overall
                                memory_obj = max(candidates, key=lambda m: getattr(m, "timestamp", 0.0))
                                logger.debug(
                                    "L3.memory_manager [human] - Selected most recent memory across channels (timestamp: %s)",
                                    memory_obj.timestamp if memory_obj else "unknown"
                                )
                    if not memory_obj:
                        continue
                    try:
                        sim_val = float(sim)
                    except Exception:
                        sim_val = 0.0
                    recency_fraction = _recency_fraction(memory_obj)
                    same_room_boost = 0.05 if channel and getattr(memory_obj, "channel", None) == channel else 0.0
                    score = sim_val + (RECENCY_BOOST * recency_fraction) + same_room_boost
                    ranked.append((memory_obj, score))

                # Add any memories not surfaced by retrieve() with a pure recency score so the freshest extras float up
                seen_contents = {m.content for m, _ in ranked}
                extras = [m for m in memories if m.content not in seen_contents]
                extras.sort(key=lambda x: getattr(x, "timestamp", 0.0), reverse=True)
                tail = extras[:MAX_RECENCY_EXTRAS] if ranked else extras[:1]
                for mem in tail:
                    ranked.append((mem, RECENCY_BOOST * _recency_fraction(mem)))

                # Final ordering: similarity desc, then recency desc
                ranked.sort(key=lambda p: (p[1], getattr(p[0], 'timestamp', 0.0)), reverse=True)
                selected: List[HumanMemory] = []
                used = 0
                for m, _ in ranked:
                    tok = count_tokens(m.content, "gpt-5")
                    if used + tok <= max_tokens:
                        selected.append(m)
                        used += tok
                    else:
                        break
                return selected

            # Fallback: recency-first within token budget
            return self._get_relevant_memories(
                query,
                memories,
                max_tokens,
                max_items=MAX_FALLBACK_PER_TYPE,
            )
        except Exception as e:
            logger.error(f"L3.memory_manager [human] - Error retrieving human memories: {e}", exc_info=True)
            return []
    
    def _get_verbatim_memories(
        self,
        query: str,
        max_tokens: int,
        channel: Optional[str] = None,
        limit: Optional[int] = None,
        recent_chat_history: Optional[List[Dict]] = None
    ) -> List[verbatim_memory.VerbatimMemory]:
        """Get relevant verbatim memories sorted by similarity, deduped against current chat history.

        Behavior:
        - Rank all available verbatim memories by semantic similarity to the query.
        - Deduplicate ONLY those that appear in the current chat history (verbatim-only rule).
        - Always fill the allocated token budget with the top-ranked remaining items (no minimum similarity threshold).
        - Break ties by recency.
        """
        try:
            # IMPORTANT: Verbatim memory is global and should persist across rooms.
            # Retrieve across all channels so cross-room recall works.
            memories = self.verbatim_manager.get_memories(limit=limit, channel=None)
            logger.debug(f"L3.memory_manager [verbatim] - Candidates before dedup: {len(memories)}")
            
            if not memories:
                return []
            
            # Apply deduplication against the current chat history if provided
            if recent_chat_history:
                memories = self._deduplicate_verbatim_memories(memories, recent_chat_history)
                logger.debug(f"L3.memory_manager [verbatim] - After deduplication: {len(memories)} memories remain")

            if not memories:
                return []

            # Build index from chunk_id to memory for fast lookup
            by_chunk = {m.chunk_id: m for m in memories}

            def _timestamp(mem: verbatim_memory.VerbatimMemory) -> float:
                return getattr(mem, "assistant_timestamp", None) or getattr(mem, "user_timestamp", 0.0)

            ts_values = [_timestamp(m) for m in memories]
            tmin = min(ts_values) if ts_values else 0.0
            span = max(1.0, (max(ts_values) - tmin)) if ts_values else 1.0

            def _recency_fraction(mem: verbatim_memory.VerbatimMemory) -> float:
                try:
                    return (_timestamp(mem) - tmin) / span
                except Exception:
                    return 0.0

            query_terms = self._extract_query_terms(query)

            def _score_memory(mem: verbatim_memory.VerbatimMemory, similarity: float = 0.0) -> float:
                try:
                    recency_fraction = _recency_fraction(mem)
                except Exception:
                    recency_fraction = 0.0
                base_score = similarity + (RECENCY_BOOST * recency_fraction)
                if channel and getattr(mem, "channel", None) == channel:
                    base_score += VERBATIM_CHANNEL_BOOST
                if query_terms:
                    base_score += self._keyword_overlap_boost(query_terms, mem)
                return base_score

            # Retrieve similarity scores for the query across the embedding store
            try:
                results = embeddings.retrieve(query, k=500, similarity_threshold=None)
            except Exception:
                results = []

            ranked: List[Tuple[VerbatimMemory, float]] = []
            if results:
                # Collect results for verbatim memories in our candidate set
                for text, sim, md in results:
                    if not isinstance(md, dict) or md.get("type") != "verbatim_memory":
                        continue
                    chunk_id = md.get("chunk_id")
                    mem = by_chunk.get(chunk_id)
                    if not mem:
                        continue
                    try:
                        sim_val = float(sim)
                    except Exception:
                        sim_val = 0.0
                    ranked.append((mem, _score_memory(mem, sim_val)))

            # Add any remaining memories not seen in retrieve() with a recency-based score
            seen_ids = {mem.chunk_id for mem, _ in ranked}
            extras = [m for m in memories if m.chunk_id not in seen_ids]
            extras.sort(key=_timestamp, reverse=True)
            tail = extras[:MAX_RECENCY_EXTRAS] if ranked else extras[:1]
            for mem in tail:
                ranked.append((mem, _score_memory(mem, 0.0)))

            # Final ordering: similarity desc, then recency desc
            ranked.sort(key=lambda p: (p[1], _timestamp(p[0])), reverse=True)

            # Fill within token budget
            selected: List[VerbatimMemory] = []
            used = 0
            for m, _ in ranked:
                # Count tokens using combined text
                tok = count_tokens(f"{m.user_message} {m.assistant_message}", "gpt-5")
                if used + tok <= max_tokens:
                    selected.append(m)
                    used += tok
                else:
                    break

            logger.info(f"L3.memory_manager [verbatim] - Selected {len(selected)} verbatim within {max_tokens} tokens")
            return selected
            
        except Exception as e:
            logger.error(f"L3.memory_manager [verbatim] - Error retrieving verbatim memories: {e}", exc_info=True)
            return []
    
    def _get_theo_memories(
        self,
        query: str,
        max_tokens: int,
        limit: Optional[int] = None
    ) -> List[theo_memory.TheoMemory]:
        """Get relevant Theo memories within token budget using importance, relevance, and recency.

        Policy (per specs):
        - Relevance‑first mindset, blended with freshness (recency) and importance weight.
        - All memories with importance == 100 are guaranteed to be included even if
          they exceed the budget (do not count against the budget).
        - Fill the remaining budget with the highest combined score items.
        """
        try:
            # Start from importance ordering for stability, then compute scores
            try:
                memories = self.theo_manager.get_memories(limit=limit, sort_by="importance")
            except TypeError:
                memories = self.theo_manager.get_memories(limit=limit)
            if not memories:
                return []

            try:
                results = embeddings.retrieve(query, k=200, similarity_threshold=None)
            except Exception:
                results = []

            logger.debug(
                "L3.memory_manager [theo] - Embedding retrieve returned %d candidates",
                len(results) if isinstance(results, list) else -1,
            )

            # memory_id -> similarity score (default 0.0)
            sims: Dict[str, float] = {m.memory_id: 0.0 for m in memories}

            if results:
                ts_vals = [m.timestamp for m in memories]
                tmin = min(ts_vals) if ts_vals else 0.0
                span = max(1.0, (max(ts_vals) - tmin)) if ts_vals else 1.0

                for text, sim, md in results:
                    if not isinstance(md, dict) or md.get("type") != "theo_memory":
                        continue
                    mid = md.get("memory_id")
                    if mid in sims:
                        # Keep the max similarity seen for this memory_id
                        try:
                            sim_val = float(sim)
                        except Exception:
                            sim_val = float(sim) if sim is not None else 0.0
                        if sim_val < THEO_MIN_SIMILARITY:
                            sim_val = 0.0
                        sims[mid] = max(sim_val, sims.get(mid, 0.0))

            # Compute combined score: similarity primary, small recency boost, importance adds weight
            def _recency(m: TheoMemory, tmin_val: float, span_val: float) -> float:
                try:
                    return (m.timestamp - tmin_val) / span_val
                except Exception:
                    return 0.0

            ts_vals = [m.timestamp for m in memories]
            tmin = min(ts_vals) if ts_vals else 0.0
            span = max(1.0, (max(ts_vals) - tmin)) if ts_vals else 1.0

            ranked: List[Tuple[TheoMemory, float]] = []
            low_similarity_fallback: List[TheoMemory] = []
            for m in memories:
                sim_val = sims.get(m.memory_id, 0.0)
                imp_raw = float(getattr(m, 'importance', 0) or 0)
                if sim_val < THEO_MIN_SIMILARITY and imp_raw < 100:
                    # Skip low-relevance items for the main ranking; keep for fallback in case nothing else matches
                    low_similarity_fallback.append(m)
                    continue

                rec_val = _recency(m, tmin, span)
                imp_val = imp_raw / 100.0
                # Weights: similarity 1.0, recency 0.05, importance 0.20 (adds weight but never dominates)
                score = (1.0 * float(sim_val)) + (0.05 * rec_val) + (0.20 * imp_val)
                logger.debug(
                    "L3.memory_manager [theo] - Candidate %s score=%.3f (sim=%.3f, rec=%.3f, imp=%.2f)",
                    getattr(m, 'memory_id', '<unknown>'),
                    score,
                    float(sim_val),
                    rec_val,
                    imp_val,
                )
                ranked.append((m, score))

            if not ranked and low_similarity_fallback:
                logger.debug(
                    "L3.memory_manager [theo] - No memories met similarity threshold; falling back to %d low-sim candidates",
                    len(low_similarity_fallback),
                )
                ranked.extend((m, 0.0) for m in low_similarity_fallback)

            # Sort by combined score descending
            ranked.sort(key=lambda p: p[1], reverse=True)

            selected: List[TheoMemory] = []
            used = 0
            guaranteed_ids = set()
            # Include all importance==100 regardless of budget and do not count against it
            for m, _keys in ranked:
                if getattr(m, 'importance', 0) == 100 and m.memory_id not in guaranteed_ids:
                    selected.append(m)
                    guaranteed_ids.add(m.memory_id)

            # Fill remaining budget with best-ranked others
            for m, _score in ranked:
                if m.memory_id in guaranteed_ids:
                    continue
                tok = count_tokens(m.content, "gpt-5")
                if used + tok <= max_tokens:
                    selected.append(m)
                    used += tok
                else:
                    continue

            # If nothing fits the budget, fall back to the top-ranked memory so context is not empty
            if not selected and ranked:
                fallback = ranked[0][0]
                logger.debug(
                    "L3.memory_manager [theo] - No memories fit %d-token budget; including top candidate %s",
                    max_tokens,
                    getattr(fallback, "memory_id", "<unknown>"),
                )
                selected.append(fallback)

            # Ensure we retain at least one high-relevance memory even when only guaranteed items were selected
            selected_ids = {getattr(m, "memory_id", None) for m in selected}
            extra_candidate = None
            if guaranteed_ids:
                extra_candidate = next(
                    (m for m, _ in ranked if getattr(m, "memory_id", None) not in selected_ids),
                    None,
                )

            if extra_candidate is not None:
                logger.debug(
                    "L3.memory_manager [theo] - Adding extra high-relevance memory %s despite budget overrun",
                    getattr(extra_candidate, "memory_id", "<unknown>"),
                )
                selected.append(extra_candidate)
                selected_ids.add(getattr(extra_candidate, "memory_id", None))

            logger.debug(
                "L3.memory_manager [theo] - Final selection: %s",
                [
                    (getattr(m, "memory_id", "<unknown>"), getattr(m, "importance", None))
                    for m in selected
                ],
            )

            return selected
        except Exception as e:
            logger.error(f"L3.memory_manager [theo] - Error retrieving theo memories: {e}", exc_info=True)
            return []
    
    def _get_relevant_memories(
        self,
        query: str,
        memories: List[Any],
        max_tokens: int,
        max_items: Optional[int] = None,
    ) -> List[Any]:
        """
        Get relevant memories using embeddings and respect token budget.
        
        Args:
            query: Query string for relevance
            memories: List of memory objects
            max_tokens: Maximum tokens allowed
            
        Returns:
            List of relevant memories within token budget
        """
        try:
            if not memories:
                return []
            
            # For now, use simple recency prioritization
            def _ts(m):
                return (
                    getattr(m, 'timestamp', None)
                    or getattr(m, 'assistant_timestamp', None)
                    or getattr(m, 'user_timestamp', 0)
                )
            sorted_memories = sorted(memories, key=_ts, reverse=True)
            
            # Build up memories within token budget
            selected_memories = []
            current_tokens = 0
            
            for memory in sorted_memories:
                # Get memory content for token counting
                if hasattr(memory, 'content'):
                    content = memory.content
                elif hasattr(memory, 'user_message') and hasattr(memory, 'assistant_message'):
                    content = f"{memory.user_message} {memory.assistant_message}"
                else:
                    content = str(memory)

                memory_tokens = count_tokens(content, "gpt-5")  # Use default model for counting

                if current_tokens + memory_tokens <= max_tokens:
                    selected_memories.append(memory)
                    current_tokens += memory_tokens
                    if max_items is not None and len(selected_memories) >= max_items:
                        break
                else:
                    break

            return selected_memories
            
        except Exception as e:
            logger.error(f"L3.memory_manager [relevance] - Error getting relevant memories: {e}", exc_info=True)
            return []
    
    def _count_memories_tokens(self, memories: List[Any]) -> int:
        """Count total tokens for a list of memories."""
        try:
            total_tokens = 0
            for memory in memories:
                if hasattr(memory, 'content'):
                    content = memory.content
                elif hasattr(memory, 'user_message') and hasattr(memory, 'assistant_message'):
                    content = f"{memory.user_message} {memory.assistant_message}"
                else:
                    content = str(memory)
                
                total_tokens += count_tokens(content, "gpt-5")
            
            return total_tokens
            
        except Exception as e:
            logger.error(f"L3.memory_manager [counting] - Error counting memory tokens: {e}", exc_info=True)
            return 0
    
    def _deduplicate_verbatim_memories(
        self,
        verbatim_memories: List[verbatim_memory.VerbatimMemory],
        recent_chat_history: List[Dict]
    ) -> List[verbatim_memory.VerbatimMemory]:
        """
        Remove verbatim memories that overlap with recent chat history.
        
        Args:
            verbatim_memories: List of verbatim memory objects
            recent_chat_history: List of recent chat messages
            
        Returns:
            Filtered list of verbatim memories without recent duplicates
        """
        try:
            if not recent_chat_history:
                return verbatim_memories
            
            # Extract user and assistant messages from recent chat history
            recent_exchanges = []
            user_msg = None
            
            for msg in recent_chat_history:
                role = msg.get("role", "")
                content = msg.get("content", "")
                
                if role == "user":
                    user_msg = content.strip()
                elif role == "assistant" and user_msg is not None:
                    # Found a complete exchange
                    recent_exchanges.append((user_msg, content.strip()))
                    user_msg = None
            
            if not recent_exchanges:
                return verbatim_memories
            
            logger.debug(f"L3.memory_manager [dedup] - Found {len(recent_exchanges)} recent exchanges to check against")
            
            # Filter out verbatim memories that match recent exchanges
            filtered_memories = []
            dedup_count = 0
            
            for memory in verbatim_memories:
                # Normalize the memory content for comparison
                memory_user = memory.user_message.strip()
                memory_assistant = memory.assistant_message.strip()
                
                # Check if this memory matches any recent exchange
                is_duplicate = False
                for recent_user, recent_assistant in recent_exchanges:
                    # Use conservative similarity to avoid false positives
                    if (self._is_similar_content(memory_user, recent_user) and
                        self._is_similar_content(memory_assistant, recent_assistant)):
                        is_duplicate = True
                        dedup_count += 1
                        logger.debug(f"L3.memory_manager [dedup] - Filtered duplicate memory: {memory.chunk_id}")
                        break
                
                if not is_duplicate:
                    filtered_memories.append(memory)
            
            if dedup_count > 0:
                logger.info(f"L3.memory_manager [dedup] - Filtered {dedup_count} duplicate verbatim memories from recent chat history")
            
            return filtered_memories
            
        except Exception as e:
            logger.error(f"L3.memory_manager [dedup] - Error deduplicating verbatim memories: {e}", exc_info=True)
            # Return original memories on error to avoid breaking memory retrieval
            return verbatim_memories

    def _extract_query_terms(self, text: str) -> Set[str]:
        """Extract normalized keyword candidates from a query."""
        if not text:
            return set()
        try:
            words = re.findall(r"\b[\w']+\b", text.lower())
        except Exception:
            return set()
        return {w for w in words if len(w) >= MIN_KEYWORD_LENGTH}

    def _keyword_overlap_boost(
        self,
        query_terms: Set[str],
        memory: verbatim_memory.VerbatimMemory,
    ) -> float:
        """Compute a modest boost when query terms appear inside a memory."""
        if not query_terms:
            return 0.0
        try:
            combined = f"{memory.user_message} {memory.assistant_message}".lower()
        except Exception:
            combined = ""
        if not combined:
            return 0.0

        overlap = sum(1 for term in query_terms if term in combined)
        if overlap <= 0:
            return NAME_QUERY_BONUS if "name" in query_terms and "name" in combined else 0.0

        boost = min(KEYWORD_MAX_BOOST, overlap * KEYWORD_MATCH_STEP)
        if "name" in query_terms:
            if any(
                phrase in combined
                for phrase in (" name", "call me", "i'm ", "im ", "i am ", "my name")
            ):
                boost += NAME_QUERY_BONUS
        # Cap total boost to avoid overpowering similarity.
        return min(boost, KEYWORD_MAX_BOOST + NAME_QUERY_BONUS)
    
    def _is_similar_content(self, content1: str, content2: str, similarity_threshold: float = 0.96) -> bool:
        """
        Check if two content strings are similar enough to be considered duplicates.
        
        Args:
            content1: First content string
            content2: Second content string
            similarity_threshold: Minimum similarity ratio (0.0-1.0)
            
        Returns:
            True if contents are similar enough to be duplicates
        """
        try:
            # Normalize: strip, lowercase, remove repeated whitespace
            norm1 = " ".join(content1.strip().lower().split())
            norm2 = " ".join(content2.strip().lower().split())
            
            # Exact match check first (fastest)
            if norm1 == norm2:
                return True
            
            # Early returns
            if not norm1 or not norm2:
                return False
            # Length sanity check - tightened to align with high similarity threshold
            len1, len2 = len(norm1), len(norm2)
            length_ratio = min(len1, len2) / max(len1, len2)
            # Use 0.92 to be consistent with 0.96 similarity (slight tolerance for length variance)
            if length_ratio < 0.92:
                return False
            # Use SequenceMatcher for conservative similarity
            try:
                from difflib import SequenceMatcher
                ratio = SequenceMatcher(None, norm1, norm2).ratio()
                return ratio >= similarity_threshold
            except Exception:
                return False
            
        except Exception as e:
            logger.debug(f"L3.memory_manager [similarity] - Error comparing content similarity: {e}")
            # Fall back to exact string comparison
            return content1.strip() == content2.strip()
    
    def get_memory_stats(self) -> Dict[str, Any]:
        """Get statistics about all memory types."""
        try:
            human_stats = self.human_memory_extractor.get_memory_stats()
            verbatim_stats = self.verbatim_manager.get_memory_stats()
            theo_stats = self.theo_manager.get_memory_stats()

            return {
                "human_memory": human_stats,
                "verbatim_memory": verbatim_stats,
                "theo_memory": theo_stats,
                "total_memories": (
                    human_stats.get("total_memories", 0)
                    + verbatim_stats.get("total_memories", 0)
                    + theo_stats.get("total_memories", 0)
                ),
            }

        except Exception as e:
            logger.error(f"L3.memory_manager [stats] - Error getting memory stats: {e}", exc_info=True)
            return {}


def get_memory_extractor(model_selector=None, vault_path: Optional[str] = None):
    """Expose human_memory.get_memory_extractor with optional vault override."""
    extractor = human_memory.get_memory_extractor(model_selector)
    if vault_path and hasattr(extractor, "vault_path"):
        try:
            current = Path(getattr(extractor, "vault_path"))
            desired = Path(vault_path)
            if current.resolve() != desired.resolve():
                extractor = human_memory.HumanMemoryExtractor(
                    vault_path=str(desired),
                    model_selector=model_selector,
                )
                human_memory._memory_extractor = extractor  # type: ignore[attr-defined]
        except Exception:
            pass
    return extractor


def get_verbatim_manager(vault_path: Optional[str] = None):
    """Expose verbatim_memory.get_verbatim_manager with optional vault override."""
    manager = verbatim_memory.get_verbatim_manager()
    if vault_path and hasattr(manager, "vault_path"):
        try:
            current = Path(getattr(manager, "vault_path"))
            desired = Path(vault_path)
            if current.resolve() != desired.resolve():
                manager = verbatim_memory.VerbatimMemoryManager(vault_path=str(desired))
                verbatim_memory._verbatim_manager = manager  # type: ignore[attr-defined]
        except Exception:
            pass
    return manager


def get_theo_manager(vault_path: Optional[str] = None):
    """Expose theo_memory.get_theo_manager with optional vault override."""
    manager = theo_memory.get_theo_manager()
    if vault_path and hasattr(manager, "vault_path"):
        try:
            current = Path(getattr(manager, "vault_path"))
            desired = Path(vault_path)
            if current.resolve() != desired.resolve():
                manager = theo_memory.TheoMemoryManager(vault_path=str(desired))
                theo_memory._theo_manager = manager  # type: ignore[attr-defined]
        except Exception:
            pass
    return manager


def get_memory_manager(model_selector=None, vault_path: Optional[str] = None) -> Layer3MemoryManager:
    """
    Get a Layer 3 memory manager instance.
    
    Args:
        model_selector: ModelSelector instance for AI calls
        vault_path: Path to vault directory
        
    Returns:
        Layer3MemoryManager instance
    """
    if vault_path is None:
        try:
            from utils.vault_paths import get_vault_root
            vault_path = str(get_vault_root())
        except Exception:
            vault_path = None
    return Layer3MemoryManager(model_selector, vault_path)

# Backward-compatibility aliases for tests that import these from memory_manager
# These map to the concrete classes in the corresponding modules.
HumanMemory = human_memory.HumanMemory  # type: ignore[attr-defined]
TheoMemory = theo_memory.TheoMemory  # type: ignore[attr-defined]
VerbatimMemory = verbatim_memory.VerbatimMemory  # type: ignore[attr-defined]
