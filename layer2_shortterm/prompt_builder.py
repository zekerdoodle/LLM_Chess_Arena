"""
Prompt Builder for Layer 2 - Short Term Memory

Provides prompt building logic for both basic prompts and conversation prompts with tool processing.
Handles attachment processing, embed processing, token budget management, and Layer 3 memory integration.
"""

import time
import os
import asyncio
from typing import Any, Dict, List, Optional

from utils.logger import get_logger
from utils.prompt_printer import build_prompt, format_chat_history, build_memory_bank
from utils.token_counter import count_tokens, truncate_context, truncate_text_to_tokens
from utils.config_loader import get_config_value
from utils.dynamic_optimizer import optimize_context
from utils.circuit_breaker import get_circuit_breaker
from layer5_features.working_memory import format_prompt as format_working_memory_prompt

from .chat_history import ChatHistory
from .channel_manager import ChannelManager

logger = get_logger(__name__)


class PromptBuilder:
    """
    Builds prompts for AI model consumption.

    Handles both basic prompts and conversation prompts with tool processing.
    Manages token budget, attachment processing, context building, and Layer 3 memory integration.
    """

    def __init__(
        self,
        chat_history: ChatHistory,
        channel_manager: ChannelManager,
        config: Dict[str, Any],
        memory_manager=None,
    ):
        """
        Initialize prompt builder.

        Args:
            chat_history: ChatHistory instance for accessing chat history
            channel_manager: ChannelManager instance for channel information
            config: Configuration dictionary with model and budget settings
            memory_manager: Layer 3 memory manager for accessing long-term memories
        """
        self.chat_history = chat_history
        self.channel_manager = channel_manager
        self.config = config
        self.memory_manager = memory_manager
        
        # Cache for dynamic optimization results to avoid re-running optimizer for same message
        self._memory_cache = {}
        self._cache_ttl = 300  # 5 minutes TTL for cached optimization results
        self._last_optimization = None  # Store last optimizer output for history budget
        # Track per-turn optimizer attempt to ensure at most one call per chat exchange
        self._optimizer_called_this_turn = False
        self._user_reasoning_override: Optional[str] = None

        logger.debug("L2.prompt_builder [setup] - Initialized prompt builder")
        if memory_manager:
            logger.debug("L2.prompt_builder [setup] - Layer 3 memory manager integrated")

    def clear_memory_cache(self):
        """Clear the memory optimization cache. Should be called for each new user message.
        
        This method:
        1. Clears cached memory optimization results from previous messages
        2. Resets the _optimizer_called_this_turn flag to allow optimizer to run again
        3. Preserves _last_optimization result for use in current turn's budget calculations
        
        Called in two scenarios:
        - At the start of each new user message (by orchestrator)
        - Before fallback model attempts (to ensure clean context for fallback)
        
        IMPORTANT BEHAVIOR NOTE:
        The flag reset is intentional when called during fallback attempts. This allows the 
        optimizer to potentially re-run for fallback models since different models may have
        different capabilities and context requirements. However, this means the optimizer
        could run multiple times per user message if fallback occurs.
        
        If you want to prevent optimizer re-runs after a fallback (to preserve the initial
        optimization), call inject_optimizer_result() AFTER clear_memory_cache() with the
        saved optimization result. This will set the flag and prevent re-runs.
        
        Design rationale: Better to allow re-optimization for fallback models (which may have
        different context windows or capabilities) than to force them to use the primary
        model's optimization, which might not be appropriate.
        """
        self._memory_cache.clear()
        logger.debug("L2.prompt_builder [cache] - Cleared memory optimization cache")
        # Reset optimizer flag for new turn; preserve _last_optimization result if present
        # This flag controls whether optimizer runs THIS turn, and must be reset for each message
        # For fallback scenarios, this allows re-optimization tailored to the fallback model
        self._optimizer_called_this_turn = False

    def inject_optimizer_result(self, optimization: dict | None):
        """Inject a precomputed Dynamic Context Optimizer result.

        Sets internal flags so this instance will not re-run the optimizer
        during this turn and can use provided budgets.
        
        This should be called AFTER clear_memory_cache() if you want to prevent
        the optimizer from running. If called before clear_memory_cache(), the flag
        will be reset and the optimizer may run again.

        Args:
            optimization: The optimizer result dict or None.
        """
        try:
            if optimization and isinstance(optimization, dict):
                self._last_optimization = optimization
                self._optimizer_called_this_turn = True
                logger.debug("L2.prompt_builder [optimizer] - Injected optimizer result, will skip re-run this turn")
            else:
                # Explicitly prevent re-running optimizer this turn even without a result
                self._optimizer_called_this_turn = True
                logger.debug("L2.prompt_builder [optimizer] - Marked optimizer as called (no result), will skip re-run this turn")
        except Exception:
            self._optimizer_called_this_turn = True
            logger.debug("L2.prompt_builder [optimizer] - Exception during injection, marked as called to prevent re-run")
        finally:
            self._apply_user_reasoning_override()

    def set_reasoning_override(self, effort: Optional[str]) -> None:
        """Persist a user-selected reasoning effort for this turn."""
        normalized: Optional[str] = None
        if isinstance(effort, str):
            candidate = effort.strip().lower()
            if candidate in {"minimal", "low", "medium", "high"}:
                normalized = candidate

        self._user_reasoning_override = normalized
        if normalized:
            self._apply_user_reasoning_override()

    def _apply_user_reasoning_override(self) -> None:
        """Ensure the current optimizer snapshot honors the user override."""
        if not self._user_reasoning_override:
            return
        if not isinstance(self._last_optimization, dict):
            self._last_optimization = {}
        self._last_optimization["reasoning_effort"] = self._user_reasoning_override

    def _cleanup_expired_cache_entries(self):
        """Remove expired entries from the memory cache to prevent memory buildup."""
        current_time = time.time()
        expired_keys = [
            key for key, entry in self._memory_cache.items()
            if current_time - entry["timestamp"] > self._cache_ttl
        ]
        
        for key in expired_keys:
            del self._memory_cache[key]
        
        if expired_keys:
            logger.debug(f"L2.prompt_builder [cache] - Cleaned up {len(expired_keys)} expired cache entries")

    async def build_basic_prompt(
        self, content: str, message: Any
    ) -> str:
        """
        Build a basic prompt with context for the model.

        Args:
            content: User message content
            message: Web message context object

        Returns:
            Formatted prompt string
        """
        try:
            logger.debug("L2.prompt_builder [basic] - Creating basic prompt")

            # Get system instructions
            system_instructions = self.config.get(
                "system_instructions",
                "You are Theo, an intelligent AI assistant. Be helpful, accurate, and respectful.",
            )

            # Process attachments and embeds
            attachments_info = await self._process_attachments(message.attachments)
            embeds_info = self._process_embeds(message.embeds)
            
            # Add file analysis mode if there are attachments
            if attachments_info:
                system_instructions += "\n\nFILE ANALYSIS MODE: You have been provided with file attachments. Analyze the content and provide insights based on the files provided."

            # Always append safety policy for self-updates and shell writes
            try:
                system_instructions += "\n\n" + self._get_safety_policy_text()
            except (AttributeError, TypeError) as e:
                logger.warning(f"L2.prompt_builder [basic] - Failed to append safety policy: {e}")

            # Create context information (web)
            channel_info = f"Channel: {message.channel.name}"
            user_info = f"User: {message.author.display_name}"
            server_info = f"Server: {message.guild.name if hasattr(message, 'guild') and message.guild else 'web'}"

            # Get chat history for this channel (excluding current message to avoid duplication)
            channel_id = str(message.channel.id)
            current_message_id = str(message.id) if hasattr(message, 'id') else None
            chat_history_list = self.chat_history.get_history(channel_id, exclude_message_id=current_message_id)
            
            # When suppress_user_message is True (edit/retry flow), the user message is already
            # in history with a different ID. Exclude the last user message to avoid duplication.
            if getattr(message, 'suppress_user_message', False) and chat_history_list:
                # Find and exclude the last user message (the one being retried)
                for i in range(len(chat_history_list) - 1, -1, -1):
                    if chat_history_list[i].get("role") == "user":
                        chat_history_list = chat_history_list[:i] + chat_history_list[i+1:]
                        break
            
            chat_history_formatted = format_chat_history(chat_history_list)

            # Get memory context if memory manager is available
            memory_context = await self._get_memory_context(content, message)

            # Get current model for token counting and provider for tool filtering
            model = self.config.get("primary_model", "gpt-5")
            max_budget = self.config.get("max_token_budget", 50000)
            
            # Determine provider from model for tool filtering
            try:
                from layer1_chatbot.model_selector import ModelSelector
                model_selector = ModelSelector(self.config)
                provider = model_selector.get_provider_from_model(model)
            except (ImportError, AttributeError, KeyError) as e:
                logger.debug(f"L2.prompt_builder [basic] - Could not determine provider: {e}")
                provider = None

            # Build prompt sections
            sections = {
                "system_instructions": system_instructions,
                "tools_info": self._generate_tools_info(provider=provider),
                # Only include memory_bank when non-empty so the prompt_printer may run the optimizer
                "memory_bank": memory_context if (isinstance(memory_context, str) and memory_context.strip()) else None,
                "current_message": content,
                "channel_info": f"{channel_info}\n{user_info}\n{server_info}",
                "attachments_info": attachments_info if attachments_info else None,
                "embeds_info": embeds_info if embeds_info else None,
                "chat_history": chat_history_formatted if chat_history_formatted else None,
            }

            working_memory_block = self._get_working_memory_block(channel_id, getattr(getattr(message, "channel", None), "topic_id", None))
            if working_memory_block:
                sections["working_memory"] = working_memory_block

            # Disable secondary optimizer runs only when we already have an optimization result; otherwise allow it
            try:
                if self._last_optimization and isinstance(self._last_optimization, dict):
                    hist_budget = (
                        (self._last_optimization.get("allocated_tokens") or {}).get("history")
                    )
                    if isinstance(hist_budget, int) and hist_budget > 0:
                        sections["history_token_budget"] = hist_budget
                    sections["disable_optimizer"] = True
                else:
                    sections["disable_optimizer"] = False
            except Exception:
                # Default to allowing optimizer if any exception occurs
                sections["disable_optimizer"] = False

            # Truncate sections to fit budget, then build the prompt
            truncated_sections = truncate_context(sections, max_budget, model)
            prompt = build_prompt(truncated_sections)

            # Log final token count for visibility
            prompt_tokens = count_tokens(prompt, model)
            if prompt_tokens > max_budget:
                logger.warning(
                    f"L2.prompt_builder [basic] - Prompt still exceeds budget after truncation ({prompt_tokens} > {max_budget})"
                )
            logger.debug(f"L2.prompt_builder [basic] - Created prompt ({prompt_tokens} tokens)")

            return prompt

        except Exception as e:
            logger.error(f"L2.prompt_builder [basic] - Error creating prompt: {e}", exc_info=True)
            # Fallback to simple prompt
            return f"You are Theo, an intelligent AI assistant.\n\nUser: {content}\nTheo:"

    async def build_conversation_prompt(
        self,
        original_content: str,
        message: Any,
        conversation_history: List[str],
    ) -> str:
        """
        Build a prompt that includes conversation history from tool processing.

        Args:
            original_content: Original user message content
            message: Message context object (web)
            conversation_history: List of conversation entries

        Returns:
            Formatted prompt string with conversation context
        """
        try:
            logger.debug("L2.prompt_builder [conversation] - Creating conversation prompt")

            # Get system instructions
            system_instructions = self.config.get(
                "system_instructions",
                "You are Theo, an intelligent AI assistant with access to tools. Be helpful, accurate, and respectful.",
            )
            # Append explicit task-chaining guidance to ensure agency on startup
            try:
                system_instructions += (
                    "\n\n ### Task Chaining Technique\n"
                    " - Tasks are central to your agency. Use them to ensure progress continues between sessions. When a task triggers, you:\n"
                    "   1. Review relevant documentation, chat history, and artifacts to see previously completed work.\n"
                    "   2. Complete the current task's work and produce any deliverables.\n"
                    "   3. Create the next task (~2 minutes from now, silent by default) to continue the chain until the objective is met.\n"
                    " - If work remains, always create the next task so momentum is never lost.\n"
                )
            except (AttributeError, TypeError) as e:
                logger.debug(f"L2.prompt_builder [conversation] - Failed to append task guidance: {e}")

            # Determine provider from model for tool filtering
            try:
                from layer1_chatbot.model_selector import ModelSelector
                model_selector = ModelSelector(self.config)
                model = self.config.get("primary_model", "gpt-5")
                provider = model_selector.get_provider_from_model(model)
            except (ImportError, AttributeError, KeyError) as e:
                logger.debug(f"L2.prompt_builder [conversation] - Could not determine provider: {e}")
                provider = None
            
            # Add safety policy and tool instructions to system prompt
            safety_policy = self._get_safety_policy_text()
            tool_instructions = f"""
{self._generate_tools_info(provider=provider)}
"""

            enhanced_system_instructions = f"{system_instructions}\n\n{safety_policy}\n\n{tool_instructions}"

            # Process attachments and embeds
            # Debug: Check if message has attachments
            msg_attachments = getattr(message, 'attachments', [])
            logger.info(f"L2.prompt_builder [message] - Message has {len(msg_attachments) if msg_attachments else 0} attachments: {[getattr(a, 'filename', '?') for a in (msg_attachments or [])]}")
            
            attachments_info = await self._process_attachments(message.attachments)
            embeds_info = self._process_embeds(message.embeds)
            
            logger.info(f"L2.prompt_builder [message] - Generated attachments_info: {attachments_info[:200] if attachments_info else 'None'}")

            # Create context information (web)
            channel_info = f"Channel: {message.channel.name}"
            user_info = f"User: {message.author.display_name}"
            server_info = f"Server: {message.guild.name if hasattr(message, 'guild') and message.guild else 'web'}"
            current_message_info = f"Current Message ID: {message.id}"

            # Get chat history for this channel (excluding current message to avoid duplication)
            # The current message was added to chat history early for crash recovery,
            # but we exclude it here since it's also passed as current_message section
            channel_id = str(message.channel.id)
            current_message_id = str(message.id) if hasattr(message, 'id') else None
            chat_history_list = self.chat_history.get_history(channel_id, exclude_message_id=current_message_id)
            
            # When suppress_user_message is True (edit/retry flow), the user message is already
            # in history with a different ID. Exclude the last user message to avoid duplication.
            if getattr(message, 'suppress_user_message', False) and chat_history_list:
                # Find and exclude the last user message (the one being retried)
                for i in range(len(chat_history_list) - 1, -1, -1):
                    if chat_history_list[i].get("role") == "user":
                        chat_history_list = chat_history_list[:i] + chat_history_list[i+1:]
                        break
            
            chat_history_formatted = format_chat_history(chat_history_list)

            # Get memory context if memory manager is available
            memory_context = await self._get_memory_context(original_content, message)

            # Build prompt sections
            working_memory_block = self._get_working_memory_block(channel_id, getattr(getattr(message, "channel", None), "topic_id", None))

            sections = {
                "system_instructions": enhanced_system_instructions,
                # Only include memory_bank when non-empty so the prompt_printer may run the optimizer
                "memory_bank": memory_context if (isinstance(memory_context, str) and memory_context.strip()) else None,
                "current_message": original_content,
                "channel_info": f"{channel_info}\n{user_info}\n{server_info}\n{current_message_info}",
                "attachments_info": attachments_info if attachments_info else None,
                "embeds_info": embeds_info if embeds_info else None,
                "chat_history": chat_history_formatted if chat_history_formatted else None,
            }
            if working_memory_block:
                sections["working_memory"] = working_memory_block

            # Disable secondary optimizer runs only when we already have an optimization result; otherwise allow it
            try:
                if self._last_optimization and isinstance(self._last_optimization, dict):
                    hist_budget = (
                        (self._last_optimization.get("allocated_tokens") or {}).get("history")
                    )
                    if isinstance(hist_budget, int) and hist_budget > 0:
                        sections["history_token_budget"] = hist_budget
                    sections["disable_optimizer"] = True
                else:
                    sections["disable_optimizer"] = False
            except Exception:
                sections["disable_optimizer"] = False

            # Truncate sections to fit budget, then build the base prompt
            model = self.config.get("primary_model", "gpt-5")
            max_budget = self.config.get("max_token_budget", 50000)
            truncated_sections = truncate_context(sections, max_budget, model)
            prompt = build_prompt(truncated_sections)

            # Add conversation history from current tool processing session (spec-compliant placement)
            if conversation_history:
                prompt += "\n\nCurrent Conversation:\n"
                prompt += "\n".join(conversation_history)
                prompt += "\n"

            # Log final token count for visibility
            prompt_tokens = count_tokens(prompt, model)
            if prompt_tokens > max_budget:
                logger.warning(
                    f"L2.prompt_builder [conversation] - Prompt still exceeds budget after truncation ({prompt_tokens} > {max_budget})"
                )
            logger.debug(f"L2.prompt_builder [conversation] - Created conversation prompt ({prompt_tokens} tokens)")

            return prompt

        except Exception as e:
            logger.error(f"L2.prompt_builder [conversation] - Error creating conversation prompt: {e}", exc_info=True)
            # Fallback to simple prompt
            return f"You are Theo, an intelligent AI assistant.\n\nUser: {original_content}\nTheo:"

    async def _get_memory_context(self, content: str, message: Any) -> str:
        """
        Get memory context from Layer 3 memory manager using Dynamic Optimizer.
        Uses caching to avoid re-running dynamic optimization for the same message.
        
        Args:
            content: Current message content for memory retrieval
            message: Message context object for context
            
        Returns:
            Formatted memory context string or empty string if no memory manager
        """
        # Get circuit breaker for memory system
        memory_breaker = get_circuit_breaker("memory_manager", failure_threshold=3, reset_timeout=300)
        
        try:
            # Run Dynamic Optimizer once per user message (when enabled), regardless of memory manager presence.
            try:
                optimizer_enabled = (self.config.get("dynamic_context_optimizer", {}) or {}).get("enabled", True)
            except Exception:
                optimizer_enabled = True
            if optimizer_enabled and not getattr(self, "_optimizer_called_this_turn", False):
                self._optimizer_called_this_turn = True
                logger.info("L2.prompt_builder [memory] - Running Dynamic Context Optimizer for this turn")
                optimizer_prompt_text = self._build_optimizer_prompt(content, message)
                loop = asyncio.get_event_loop()
                timeout_s = float(get_config_value(self.config, "dynamic_context_optimizer.timeout_seconds", 8))
                import time as _time
                _t0 = _time.time()
                try:
                    optimization = await asyncio.wait_for(
                        loop.run_in_executor(None, optimize_context, content, self.config, optimizer_prompt_text),
                        timeout=timeout_s,
                    )
                except asyncio.TimeoutError:
                    logger.warning(f"L2.prompt_builder [memory] - Optimizer timed out after {timeout_s:.1f}s")
                    optimization = None
                except Exception as oe:
                    logger.debug(f"L2.prompt_builder [memory] - Optimizer failed: {oe}")
                    optimization = None
                finally:
                    _elapsed = _time.time() - _t0
                    try:
                        logger.info(f"L2.prompt_builder [memory] - Optimizer elapsed {_elapsed:.2f}s")
                    except Exception:
                        pass
                if optimization and isinstance(optimization, dict):
                    self._last_optimization = optimization
                    self._apply_user_reasoning_override()

            if not self.memory_manager:
                logger.debug("L2.prompt_builder [memory] - No memory manager available")
                # Without memory manager, we only precompute budgets/reasoning and return empty memory context
                return ""
            
            # Check if memory circuit is open
            if memory_breaker.is_open():
                logger.warning("L2.prompt_builder [memory] - Memory system circuit open, using fallback")
                return self._get_fallback_memory_context(content, message)
            
            # Create cache key based on message ID and content
            cache_key = f"{message.id}_{hash(content)}"
            current_time = time.time()
            
            memory_allocations = None
            memory_budget = None
            
            # Check if we have a cached result that's still valid
            if cache_key in self._memory_cache:
                cache_entry = self._memory_cache[cache_key]
                if current_time - cache_entry["timestamp"] < self._cache_ttl:
                    logger.debug(f"L2.prompt_builder [memory] - Using cached optimization result for message {message.id}")
                    memory_allocations = cache_entry["memory_allocations"]
                    memory_budget = cache_entry["memory_budget"]
                else:
                    # Remove expired cache entry
                    del self._memory_cache[cache_key]
                    logger.debug(f"L2.prompt_builder [memory] - Cache expired for message {message.id}")
            
            # Periodically clean up expired entries (every 10th call)
            if len(self._memory_cache) % 10 == 0:
                self._cleanup_expired_cache_entries()
            
            # Do not use optimizer for memory allocations in tests/integration; prefer config-based allocations
            # This ensures deterministic behavior and matches unit test expectations (45% of budget)
            memory_allocations = None if memory_allocations is None else memory_allocations

            # Prefer dynamic optimizer allocations when available; otherwise deterministic, config-driven allocations
            if memory_allocations is None:
                # Use dynamic optimizer breakdown if available
                alloc = None
                try:
                    if getattr(self, "_last_optimization", None):
                        alloc = (self._last_optimization.get("allocated_tokens") or {})
                except Exception:
                    alloc = None
                if isinstance(alloc, dict) and alloc.get("memory") and alloc.get("memory_breakdown"):
                    try:
                        memory_budget = int(alloc.get("memory") or 0)
                    except Exception:
                        memory_budget = 0
                    breakdown = alloc.get("memory_breakdown") or {}
                    memory_allocations = {
                        "human_memory": int(breakdown.get("human_memory", 0)),
                        "theo_memory": int(breakdown.get("theo_memory", 0)),
                        "verbatim_memory": int(breakdown.get("verbatim_memory", 0)),
                    }
                else:
                    # Deterministic, config-driven fallback
                    pa = (self.config.get("prompt_allocations") or {})
                    pct_allocations = {
                        "human_memory": int(pa.get("human_memory", 0)),
                        "verbatim_memory": int(pa.get("verbatim_memory", 0)),
                        "theo_memory": int(pa.get("theo_memory", 0)),
                    }
                    total_pct = sum(pct_allocations.values())
                    logger.debug(f"L2.prompt_builder [memory] - Using config allocations (percentages): {pct_allocations}")

                    max_budget_cfg = self.config.get("max_token_budget", 50000)
                    if total_pct > 0 and total_pct <= 100:
                        memory_budget = int(max_budget_cfg * (total_pct / 100.0))
                        # Provide raw percentages as weights; L3 normalizes to 'max_tokens'
                        memory_allocations = dict(pct_allocations)
                    elif total_pct > 100:
                        memory_allocations = pct_allocations
                        memory_budget = sum(memory_allocations.values())
                    else:
                        try:
                            dyn_cfg = (self.config.get("dynamic_context_optimizer") or {})
                        except Exception:
                            dyn_cfg = {}
                        try:
                            fractions = (dyn_cfg.get("memory_fractions") or {})
                            verb_pct = int(fractions.get("verbatim_memory", 40))
                            theo_pct = int(fractions.get("theo_memory", 30))
                            human_pct = int(fractions.get("human_memory", 30))
                        except Exception:
                            verb_pct, theo_pct, human_pct = 40, 30, 30
                        if (verb_pct + theo_pct + human_pct) == 0:
                            verb_pct, theo_pct, human_pct = 40, 30, 30
                        try:
                            chat_hist_pct = int((self.config.get("prompt_allocations") or {}).get("chat_history", 55))
                        except Exception:
                            chat_hist_pct = 55
                        memory_pct = max(0, min(100, 100 - chat_hist_pct))
                        try:
                            ceilings = (dyn_cfg.get("allocation_ceilings") or {})
                            max_mem_pct = int(ceilings.get("max_memory_percent", memory_pct))
                            memory_pct = min(memory_pct, max_mem_pct)
                        except Exception:
                            pass
                        max_budget_cfg = self.config.get("max_token_budget", 50000)
                        memory_budget = int(max_budget_cfg * (memory_pct / 100.0))
                        memory_allocations = {
                            "human_memory": human_pct,
                            "verbatim_memory": verb_pct,
                            "theo_memory": theo_pct,
                        }

                # Cache the result (allocations are token counts at this point)
                self._memory_cache[cache_key] = {
                    "memory_allocations": memory_allocations,
                    "memory_budget": memory_budget,
                    "timestamp": current_time
                }
                logger.debug(f"L2.prompt_builder [memory] - Cached memory allocations for message {message.id}: {memory_allocations} (budget {memory_budget})")
                # Keep last optimization for history budgeting in prompt sections
            
            logger.debug(f"L2.prompt_builder [memory] - Memory budget: {memory_budget} tokens")

            # If there are no allocations/budget, skip memory manager call
            if not memory_allocations or memory_budget <= 0:
                return ""

            # Get channel ID for channel-specific memories
            channel_id = str(message.channel.id)
            current_message_id = str(message.id) if hasattr(message, 'id') else None
            
            # Get recent chat history for deduplication: use the same history budget as optimizer
            recent_chat_history = self.chat_history.get_history(channel_id, exclude_message_id=current_message_id)
            try:
                # If we have an optimizer result, derive a truncated, tail-slice history
                hist_budget = None
                if self._last_optimization and isinstance(self._last_optimization, dict):
                    hist_budget = (
                        (self._last_optimization.get('allocated_tokens') or {}).get('history')
                    )
                if isinstance(hist_budget, int) and hist_budget > 0:
                    cfg_model = self.config.get("primary_model", "gpt-5")
                    formatted = format_chat_history(recent_chat_history)
                    truncated_text = truncate_text_to_tokens(formatted, hist_budget, cfg_model, keep_end=True)
                    # Parse truncated text back into list of role/content for dedup
                    parsed = []
                    for line in truncated_text.splitlines():
                        l = line.strip()
                        if not l:
                            continue
                        low = l.lower()
                        if low.startswith("user:") or low.startswith("user (id:"):
                            parts = l.split(":", 1)
                            parsed.append({"role": "user", "content": parts[1].strip() if len(parts) > 1 else ""})
                        elif low.startswith("theo:"):
                            parts = l.split(":", 1)
                            parsed.append({"role": "assistant", "content": parts[1].strip() if len(parts) > 1 else ""})
                        elif low.startswith("system:"):
                            parts = l.split(":", 1)
                            parsed.append({"role": "system", "content": parts[1].strip() if len(parts) > 1 else ""})
                    if parsed:
                        recent_chat_history = parsed
            except Exception:
                # Fall back to full history if any error occurs
                pass
            
            # Retrieve memory context with deduplication
            memory_context = self.memory_manager.get_memory_context(
                query=content,
                max_tokens=memory_budget,
                token_allocations=memory_allocations,
                channel=channel_id,
                recent_chat_history=recent_chat_history
            )
            
            # Format memory context
            formatted_memory = self.memory_manager.format_memory_context(memory_context)
            
            if formatted_memory:
                logger.info(f"L2.prompt_builder [memory] - Retrieved {memory_context.total_tokens} tokens of memory context")
                logger.debug(f"L2.prompt_builder [memory] - Memory breakdown: {memory_context.token_breakdown}")
                memory_breaker.record_success()
            else:
                logger.debug("L2.prompt_builder [memory] - No relevant memories found")
                memory_breaker.record_success()  # No memories is still success
            
            return formatted_memory
            
        except Exception as e:
            memory_breaker.record_failure()
            logger.error(f"L2.prompt_builder [memory] - Error getting memory context: {e}", exc_info=True)
            # On error, use lightweight fallback based on recent chat history
            try:
                return self._get_fallback_memory_context(content, message)
            except Exception:
                # Final guard: never raise from fallback path
                return ""

    async def _process_attachments(
        self, attachments: List[Any]
    ) -> str:
        """
        Process message attachments and return descriptions.

        Args:
            attachments: List of attachment objects

        Returns:
            Formatted string describing the attachments
        """
        # Debug logging to see what's happening with attachments
        logger.info(f"L2.prompt_builder [attachments] - Processing {len(attachments) if attachments else 0} attachments")
        
        if not attachments:
            return ""

        # Handle non-iterables gracefully
        if not hasattr(attachments, "__iter__"):
            return ""

        attachment_descriptions = []

        try:
            for attachment in attachments:
                try:
                    # Get file info (handle Mock objects)
                    filename = getattr(attachment, "filename", "unknown_file")
                    file_size = getattr(attachment, "size", 0)
                    content_type = getattr(attachment, "content_type", "unknown")

                    # Categorize file type
                    file_category = self._categorize_file(filename, content_type)

                    # Read file content for text-based files
                    file_content = None
                    if self._should_read_file_content(file_category, content_type, file_size):
                        file_content = await self._read_attachment_content(attachment)

                    # Create description based on file type
                    if file_category == "image":
                        # For images, provide explicit instructions to call analyze_image
                        url = getattr(attachment, "url", None)
                        if url:
                            description = f"🖼️ IMAGE ATTACHMENT: {filename} - To see this image, call analyze_image(\"{url}\")"
                        else:
                            description = f"- Image: {filename} ({self._format_file_size(file_size)})"
                            width = getattr(attachment, "width", None)
                            height = getattr(attachment, "height", None)
                            if width and height:
                                description += f" - Dimensions: {width}x{height}"
                        # For images, we don't add additional URL since it's already in the instruction
                        attachment_descriptions.append(description)
                        continue
                    elif file_category == "document":
                        description = f"- Document: {filename} ({self._format_file_size(file_size)})"
                    elif file_category == "archive":
                        description = f"- Archive: {filename} ({self._format_file_size(file_size)})"
                    elif file_category == "code":
                        description = f"- Code file: {filename} ({self._format_file_size(file_size)})"
                    else:
                        description = f"- File: {filename} ({self._format_file_size(file_size)}, {content_type})"

                    # Add file content if available
                    if file_content:
                        # Truncate very long content
                        max_content_length = 2000
                        if len(file_content) > max_content_length:
                            truncated_content = file_content[:max_content_length] + "... (truncated)"
                        else:
                            truncated_content = file_content
                        description += f"\nContent:\n```\n{truncated_content}\n```"

                    # Add URL for potential download
                    url = getattr(attachment, "url", "no_url")
                    if url != "no_url":
                        description += f" - URL: {url}"

                    attachment_descriptions.append(description)

                except Exception as e:
                    logger.warning(
                        f"L2.prompt_builder [attachments] - Error processing attachment {getattr(attachment, 'filename', 'unknown')}: {e}"
                    )
                    attachment_descriptions.append(
                        f"- File: {getattr(attachment, 'filename', 'unknown')} (processing error)"
                    )
        except Exception as e:
            logger.warning(f"L2.prompt_builder [attachments] - Error iterating through attachments: {e}")
            return ""

        return "\n".join(attachment_descriptions)

    def _process_embeds(self, embeds: List[Any]) -> str:
        """
        Process message embeds and return descriptions.

        Args:
            embeds: List of embed-like objects

        Returns:
            Formatted string describing the embeds
        """
        if not embeds:
            return ""

        # Handle Mock objects (for testing)
        if not hasattr(embeds, "__iter__"):
            return ""

        embed_descriptions = []

        try:
            for i, embed in enumerate(embeds):
                try:
                    description_parts = []

                    # Title
                    title = getattr(embed, "title", None)
                    if title:
                        description_parts.append(f"Title: {title}")

                    # Description
                    embed_desc = getattr(embed, "description", None)
                    if embed_desc:
                        desc_preview = embed_desc[:100] + "..." if len(embed_desc) > 100 else embed_desc
                        description_parts.append(f"Description: {desc_preview}")

                    # URL
                    url = getattr(embed, "url", None)
                    if url:
                        description_parts.append(f"URL: {url}")

                    # Fields
                    fields = getattr(embed, "fields", [])
                    if fields and hasattr(fields, "__len__"):
                        field_info = f"{len(fields)} field(s)"
                        description_parts.append(f"Fields: {field_info}")

                    # Image
                    image = getattr(embed, "image", None)
                    if image:
                        image_url = getattr(image, "url", None)
                        if image_url:
                            description_parts.append(f"Image: {image_url}")

                    # Thumbnail
                    thumbnail = getattr(embed, "thumbnail", None)
                    if thumbnail:
                        thumb_url = getattr(thumbnail, "url", None)
                        if thumb_url:
                            description_parts.append(f"Thumbnail: {thumb_url}")

                    # Footer
                    footer = getattr(embed, "footer", None)
                    if footer:
                        footer_text = getattr(footer, "text", None)
                        if footer_text:
                            description_parts.append(f"Footer: {footer_text}")

                    embed_description = f"- Embed {i+1}: " + " | ".join(description_parts)
                    embed_descriptions.append(embed_description)

                except Exception as e:
                    logger.warning(f"L2.prompt_builder [embeds] - Error processing embed {i}: {e}")
                    embed_descriptions.append(f"- Embed {i+1}: (processing error)")
        except Exception as e:
            logger.warning(f"L2.prompt_builder [embeds] - Error iterating through embeds: {e}")
            return ""

        if embed_descriptions:
            return "Embeds:\n" + "\n".join(embed_descriptions)
        return ""

    def _build_optimizer_prompt(self, content: str, message: Any) -> str:
        """Compose the optimizer input by reusing the standard-mini builder (10% total, 50/50 split)."""
        try:
            # Build sections consistent with standard prompt
            channel_info = f"Channel: {message.channel.name}"
            user_info = f"User: {message.author.display_name}"
            server_info = f"Server: {message.guild.name if hasattr(message, 'guild') and message.guild else 'web'}"

            channel_id = str(message.channel.id)
            current_message_id = str(message.id) if hasattr(message, 'id') else None
            chat_history_list = self.chat_history.get_history(channel_id, exclude_message_id=current_message_id)
            
            # When suppress_user_message is True (edit/retry flow), exclude last user message
            if getattr(message, 'suppress_user_message', False) and chat_history_list:
                for i in range(len(chat_history_list) - 1, -1, -1):
                    if chat_history_list[i].get("role") == "user":
                        chat_history_list = chat_history_list[:i] + chat_history_list[i+1:]
                        break
            
            chat_history_formatted = format_chat_history(chat_history_list)

            sections = {
                "current_message": content,
                "channel_info": channel_info,
                "user_info": user_info,
                "server_info": server_info,
                "chat_history": chat_history_formatted,
            }
            # Let the shared builder generate tools/memory as needed within budget
            from utils.prompt_printer import _build_optimizer_prompt_for_mini as _mini
            return _mini(sections)
        except Exception as e:
            logger.warning(f"L2.prompt_builder [optimizer] - Failed to build optimizer prompt: {e}")
            return content

    def _should_read_file_content(
        self, file_category: str, content_type: str, file_size: int
    ) -> bool:
        """
        Determine if file content should be read based on type and size.

        Args:
            file_category: Categorized file type
            content_type: MIME content type
            file_size: File size in bytes

        Returns:
            True if content should be read
        """
        # Don't read files larger than 1MB
        max_size = 1024 * 1024  # 1MB
        if file_size > max_size:
            return False

        # Read text-based files
        text_categories = {"document", "code"}
        if file_category in text_categories:
            return True

        # Read specific text content types
        text_content_types = {
            "text/plain",
            "text/markdown",
            "text/csv",
            "application/json",
            "application/xml",
            "text/xml",
        }
        if content_type and content_type.lower() in text_content_types:
            return True

        return False

    async def _read_attachment_content(
        self, attachment: Any
    ) -> Optional[str]:
        """
        Read the content of an attachment.

        Args:
            attachment: Attachment object

        Returns:
            File content as string, or None if unable to read
        """
        try:
            # Prefer reading from local path when present (web uploads)
            path = getattr(attachment, "path", None)
            if isinstance(path, str) and path and os.path.exists(path):
                try:
                    # Try text read first; fallback to bytes
                    with open(path, "rb") as f:
                        file_bytes = f.read()
                except Exception:
                    return None
            elif hasattr(attachment, "read"):
                # Attachment exposes async read()
                file_bytes = await attachment.read()
            else:
                return None

            # Try to decode as text with common encodings
            encodings = ["utf-8", "utf-16", "iso-8859-1", "cp1252"]
            for encoding in encodings:
                try:
                    content = file_bytes.decode(encoding)
                    return content
                except UnicodeDecodeError:
                    continue

            # If all encodings fail, return a safe representation
            logger.warning(f"L2.prompt_builder [attachments] - Could not decode {attachment.filename} as text")
            return f"[Binary file - {len(file_bytes)} bytes]"

        except Exception as e:
            logger.warning(
                f"L2.prompt_builder [attachments] - Error reading attachment content for {getattr(attachment, 'filename', 'unknown')}: {e}"
            )
            return None

    def _categorize_file(self, filename: str, content_type: str) -> str:
        """
        Categorize a file based on its filename and content type.

        Args:
            filename: Name of the file
            content_type: MIME content type

        Returns:
            Category string (image, document, archive, code, etc.)
        """
        filename_lower = filename.lower()
        content_type_lower = content_type.lower() if content_type else ""

        # Image files
        if content_type_lower.startswith("image/") or any(
            filename_lower.endswith(ext)
            for ext in [".jpg", ".jpeg", ".png", ".gif", ".bmp", ".webp", ".svg"]
        ):
            return "image"

        # Document files
        if (
            content_type_lower.startswith("application/pdf")
            or content_type_lower.startswith("application/msword")
            or content_type_lower.startswith("application/vnd.openxmlformats")
            or any(
                filename_lower.endswith(ext)
                for ext in [".pdf", ".doc", ".docx", ".txt", ".rtf", ".odt"]
            )
        ):
            return "document"

        # Archive files
        if (
            content_type_lower.startswith("application/zip")
            or content_type_lower.startswith("application/x-rar")
            or any(
                filename_lower.endswith(ext)
                for ext in [".zip", ".rar", ".7z", ".tar", ".gz"]
            )
        ):
            return "archive"

        # Code files
        if any(
            filename_lower.endswith(ext)
            for ext in [
                ".py",
                ".js",
                ".html",
                ".css",
                ".java",
                ".cpp",
                ".c",
                ".h",
                ".json",
                ".xml",
                ".yaml",
                ".yml",
            ]
        ):
            return "code"

        return "other"

    def _format_file_size(self, size_bytes: int) -> str:
        """
        Format file size in human-readable format.

        Args:
            size_bytes: Size in bytes

        Returns:
            Formatted size string
        """
        if size_bytes < 1024:
            return f"{size_bytes} B"
        elif size_bytes < 1024 * 1024:
            return f"{size_bytes / 1024:.1f} KB"
        elif size_bytes < 1024 * 1024 * 1024:
            return f"{size_bytes / (1024 * 1024):.1f} MB"
        else:
            return f"{size_bytes / (1024 * 1024 * 1024):.1f} GB"

    def _generate_tools_info(self, provider: Optional[str] = None) -> str:
        """
        Generate tools information for prompts.

        Args:
            provider: Optional provider name to filter tool guidance

        Returns:
            Formatted tools information string
        """
        from utils.tool_schemas import generate_tools_info
        return generate_tools_info(provider=provider)

    def _get_safety_policy_text(self) -> str:
        """Return a concise, always-on safety policy for self-updates and shell writes.

        Policy:
        - NEVER modify the live running codebase directly; do all edits in a clone.
        - Use create_clone → work within that clone (including bash) → test → preview_patch/apply_patch.
        - The bash tool is sandboxed to the vault; the project root is write-protected.
        """
        return (
            "SYSTEM SAFETY POLICY:\n"
            "- NEVER modify the live running codebase directly. Perform all self-updates in a clone.\n"
            "- Workflow: create_clone → edit/test within the clone (including bash) → preview_patch → apply_patch.\n"
            "- The bash tool is sandboxed to the vault; the project root is write-protected."
        )

    def _get_working_memory_block(self, channel_id: Optional[str], topic_id: Optional[str]) -> Optional[str]:
        """Return the formatted working memory block for prompt injection."""
        if not channel_id:
            return None
        try:
            block = format_working_memory_prompt(str(channel_id), topic_id=topic_id)
            if isinstance(block, str) and block.strip():
                return block
        except Exception as exc:  # pragma: no cover - defensive log
            logger.debug("L2.prompt_builder [wm] - Failed to build working memory block: %s", exc)
        return None

    def _get_fallback_memory_context(self, content: str, message: Any) -> str:
        """
        Get fallback memory context when primary memory system fails.
        
        Uses chat history as a lightweight memory alternative.
        
        Args:
            content: Current message content for context
            message: Message context object to resolve channel
            
        Returns:
            Fallback memory context string
        """
        try:
            logger.debug("L2.prompt_builder [fallback] - Generating fallback memory context")
            
            # Use recent chat history as fallback memory
            if hasattr(self, 'chat_history') and self.chat_history:
                # Get recent messages (last 5) as basic context
                recent_messages = []
                try:
                    # Resolve channel from message and fetch recent chat entries
                    channel_id = None
                    try:
                        if message is not None and hasattr(message, 'channel') and hasattr(message.channel, 'id'):
                            channel_id = str(message.channel.id)
                    except Exception:
                        channel_id = None

                    if channel_id:
                        chat_data = self.chat_history.get_history(channel_id, max_messages=5)
                        if chat_data:
                            for entry in chat_data:
                                if isinstance(entry, dict) and 'content' in entry:
                                    recent_messages.append(str(entry['content'])[:200])  # Truncate per-item
                except Exception as chat_error:
                    logger.debug(f"L2.prompt_builder [fallback] - Error accessing chat history: {chat_error}")
                
                if recent_messages:
                    fallback_context = "RECENT CONTEXT (fallback memory):\n"
                    for i, msg in enumerate(recent_messages, 1):
                        fallback_context += f"{i}. {msg}\n"
                    
                    logger.debug(f"L2.prompt_builder [fallback] - Generated fallback with {len(recent_messages)} recent messages")
                    return fallback_context
            
            # If no chat history available, return minimal context
            logger.debug("L2.prompt_builder [fallback] - No chat history available, using minimal context")
            return f"CONTEXT: Responding to query about: {content[:100]}...\n"
            
        except Exception as e:
            logger.error(f"L2.prompt_builder [fallback] - Error generating fallback memory: {e}")
            return "" 
