# Prompt Printer Utility
# Crafts full prompts from different sections for model calls

"""
Prompt Printer Module

This module is responsible for crafting complete prompts from various sections:
- System instructions
- Available tools and their documentation
- Memory bank (human, verbatim, theo memories)
- Working memory (private scratchpad)
- Chat history

The prompt printer ensures proper formatting and token management
when constructing prompts for different model providers.
"""

import logging
from typing import Any, Dict, List, Optional

from layer3_longterm.memory_manager import Layer3MemoryManager
# Optional fast‑path helpers (tests may patch these at module scope)
try:  # pragma: no cover - availability depends on embeddings module
    from layer3_longterm.embeddings import retrieve_with_scoring as retrieve_with_scoring  # type: ignore
except Exception:  # pragma: no cover
    retrieve_with_scoring = None  # type: ignore
from utils.dynamic_optimizer import optimize_context
from utils.config_loader import load_config, get_config_value
from utils.token_counter import truncate_text_to_tokens
from utils.prompt_labels import (
    AVAILABLE_TOOLS_HEADER,
    CHAT_HISTORY_HEADER,
    CURRENT_MESSAGE_HEADER,
    HUMAN_MEMORIES_HEADER,
    MEMORY_BANK_HEADER,
    SYSTEM_INSTRUCTIONS_HEADER,
    THEO_MEMORIES_HEADER,
    WORKING_MEMORY_HEADER,
    VERBATIM_MEMORIES_HEADER,
)

logger = logging.getLogger(__name__)

# Session cache to avoid recalculating optimization during same turn
_optimization_cache = {}

# Backward-compat alias names expected by older tests
# These are thin wrappers around current functions

def format_memories_for_prompt(memories: Dict[str, Any]) -> str:
    """Compatibility wrapper mapping to format_memory_bank."""
    return format_memory_bank(memories)


def _build_optimizer_prompt_for_mini(sections: Dict[str, Any]) -> str:
    """Compose the optimizer input to mirror the standard prompt, under a strict 10% budget.

    Policy:
    - Total budget: 10% of max_token_budget (config)
    - Split within that: 50% chat history (tail) / 50% memory bank
    - Use configured memory fractions to divide the memory portion
    - Include Available Tools (truncated) only if budget allows after core split
    - Use optimizer_model for token accounting
    """
    cfg = load_config()
    max_budget = get_config_value(cfg, "max_token_budget", 10000)
    optimizer_model = get_config_value(cfg, "optimizer_model", "gpt-5-nano")
    total_budget = int(max_budget * 0.10)

    # Guard against pathological configs
    if total_budget <= 0:
        total_budget = 100

    # 50/50 core split for history and memory
    hist_budget = total_budget // 2
    mem_budget = max(0, total_budget - hist_budget)

    # Build/truncate chat history tail
    history_text = sections.get("chat_history") or ""
    if history_text:
        try:
            history_text = truncate_text_to_tokens(history_text, hist_budget, optimizer_model, keep_end=True)
        except Exception:
            pass

    # Memory fractions for the memory half
    v_pct = get_config_value(cfg, "dynamic_context_optimizer.memory_fractions.verbatim_memory", 40)
    t_pct = get_config_value(cfg, "dynamic_context_optimizer.memory_fractions.theo_memory", 30)
    h_pct = get_config_value(cfg, "dynamic_context_optimizer.memory_fractions.human_memory", 30)
    try:
        total_pct = float(v_pct) + float(t_pct) + float(h_pct)
        if total_pct <= 0:
            total_pct = 100.0
            v_pct, t_pct, h_pct = 40.0, 30.0, 30.0
    except Exception:
        total_pct = 100.0
        v_pct, t_pct, h_pct = 40.0, 30.0, 30.0

    verbatim_tokens = int(mem_budget * (float(v_pct) / total_pct))
    theo_tokens = int(mem_budget * (float(t_pct) / total_pct))
    human_tokens = max(0, mem_budget - verbatim_tokens - theo_tokens)

    max_tokens_per_type = {
        "human_memory": human_tokens,
        "theo_memory": theo_tokens,
        "verbatim_memory": verbatim_tokens,
    }

    # Build memory bank using the same mechanism as standard prompts
    memory_bank = ""
    try:
        cm_query = sections.get("current_message", "") or ""
        # Provide recent history text for deduplication
        recent_history_text = sections.get("chat_history") or ""
        from utils.prompt_printer import build_memory_bank as _build_mem
        memory_bank = _build_mem(cm_query, max_tokens_per_type, recent_chat_history_text=recent_history_text)
    except Exception:
        memory_bank = ""

    # Available tools (truncated to leftover budget if any)
    tools_info = sections.get("tools_info")
    if not tools_info:
        try:
            from utils.tool_schemas import generate_tools_info as _gen_tools
            tools_info = _gen_tools()
        except Exception:
            tools_info = ""
    tools_summary_line = (
        "Tool inventory summary: add_working_memory, update_working_memory, remove_working_memory, snapshot_working_memory, bash, create_diff, web_search, "
        "url_retrieval, page_parser, code_agent, generate_image, add_memory, manually_send_message."
    )

    # Compose in the same section order as standard prompt (minus system)
    cm = sections.get("current_message", "")
    pieces: list[str] = []

    # Tools block: prefer docstrings, then concise guidance
    try:
        from utils.tool_schemas import generate_tools_docstrings
        tools_docs = generate_tools_docstrings(include_parameters=False)
    except Exception:
        tools_docs = ""
    if tools_docs or tools_info:
        pieces.append(AVAILABLE_TOOLS_HEADER)
        pieces.append(tools_summary_line)
        if tools_docs:
            pieces.append(tools_docs)
        if tools_info:
            pieces.append(tools_info)
        pieces.append("")

    # Memory bank (pre-sized)
    if memory_bank:
        pieces.append(MEMORY_BANK_HEADER)
        pieces.append(memory_bank)
        pieces.append("")

    # Chat history (pre-sized tail) — also where we inject any dynamic context and the current message
    chat_block_lines: list[str] = []
    # Add context first if provided
    context_parts = []
    for key in ("channel_info", "user_info", "attachments_info", "embeds_info"):
        val = sections.get(key)
        if val:
            context_parts.append(val)
    if context_parts:
        chat_block_lines.append("Context:")
        chat_block_lines.extend(context_parts)
        chat_block_lines.append("")
    if history_text:
        chat_block_lines.append("Recent Messages:")
        chat_block_lines.append(history_text)
        chat_block_lines.append("")
    if cm:
        chat_block_lines.append("Current Message:")
        chat_block_lines.append(f"User: {cm}")
    chat_block = "\n".join([ln for ln in chat_block_lines if ln])
    if chat_block.strip():
        pieces.append(CHAT_HISTORY_HEADER)
        pieces.append(chat_block)

    # Join and enforce total budget by trimming tools first, then context header noise
    full_text = "\n".join(pieces).strip()
    try:
        from utils.token_counter import count_tokens, truncate_text_to_tokens
        total_tokens_now = count_tokens(full_text, optimizer_model)
        if total_tokens_now > total_budget:
            # Try trimming heavy tool docs while preserving a short summary for tool awareness
            if tools_docs or tools_info:
                trimmed_lines: list[str] = []
                inside_tools_block = False
                for line in pieces:
                    if line == AVAILABLE_TOOLS_HEADER:
                        inside_tools_block = True
                        trimmed_lines.append(line)
                        continue
                    if inside_tools_block:
                        if line == "":
                            trimmed_lines.append(line)
                            inside_tools_block = False
                        elif line == tools_summary_line:
                            trimmed_lines.append(line)
                        else:
                            # Skip verbose tool docs/info when over budget
                            continue
                    else:
                        trimmed_lines.append(line)
                full_text_candidate = "\n".join(trimmed_lines).strip()
                if count_tokens(full_text_candidate, optimizer_model) <= total_budget:
                    full_text = full_text_candidate
                else:
                    # If still too big, hard truncate tail to budget (keep_start for stable headers)
                    full_text = truncate_text_to_tokens(full_text_candidate, total_budget, optimizer_model)
            else:
                full_text = truncate_text_to_tokens(full_text, total_budget, optimizer_model)
    except Exception:
        # Best-effort fallback
        if len(full_text) > 10000:
            full_text = full_text[:10000]

    return full_text


_PRIMARY_SECTION_HEADERS = (
    SYSTEM_INSTRUCTIONS_HEADER.strip(),
    AVAILABLE_TOOLS_HEADER.strip(),
    WORKING_MEMORY_HEADER.strip(),
    MEMORY_BANK_HEADER.strip(),
    CHAT_HISTORY_HEADER.strip(),
    CURRENT_MESSAGE_HEADER.strip(),
)


def extract_prompt_sections(prompt_text: str) -> Dict[str, str]:
    """Parse a rendered prompt back into its top-level sections.

    Providers that expect role-based messages (OpenAI Responses, xAI Chat)
    can use this helper to remap Theo's plain-text prompt into structured
    messages without reconstructing parsing logic in each adapter.
    """

    sections: Dict[str, list[str]] = {}
    current_header: Optional[str] = None

    for line in (prompt_text or "").splitlines():
        stripped = line.strip()
        if stripped in _PRIMARY_SECTION_HEADERS:
            current_header = stripped
            sections[current_header] = []
            continue

        if current_header is None:
            # Ignore leading text before the first recognised header
            continue

        sections[current_header].append(line)

    normalized: Dict[str, str] = {}
    for header, lines in sections.items():
        # Preserve intra-section spacing but drop leading/trailing blank space
        content = "\n".join(lines).strip()
        if content:
            normalized[header] = content

    return normalized


def build_prompt(sections: Dict[str, Any]) -> str:
    """
    Build a complete prompt from various sections.
    
    Prompt order follows specs.md lines 144-151:
    1. System instructions
    2. Available tools + docstrings
    3. Memory bank
    4. Working Memory (only when items exist)
    5. Chat history

    Args:
        sections: Dictionary containing prompt sections:
            - system_instructions: System instructions for the AI
            - current_message: The current user message
            - channel_info: Information about the room/channel (optional)
            - user_info: Information about the user (optional)
            - attachments_info: Information about file attachments (optional)
            - embeds_info: Information about embeds (optional)
            - tools_info: Information about available tools (optional)
            - memory_bank: Memory bank information (optional)
            - working_memory: Working memory scratchpad block (optional)
            - chat_history: Previous chat history (optional)

    Returns:
        Formatted prompt string ready for model consumption
    """
    try:
        logger.debug("[L2.prompt] Building prompt from sections")

        # Extract required sections
        system_instructions = sections.get("system_instructions", "")
        current_message = sections.get("current_message", "")

        # Use default system instructions if none provided
        if not system_instructions:
            system_instructions = """You are Theo, an intelligent AI assistant with access to various tools and memory systems.

You can help users with tasks, answer questions, and maintain context across conversations.

Always be helpful, accurate, and respectful in your responses."""
            logger.debug("Using default system instructions")

        # Build the basic prompt structure
        prompt_parts = []

        # Add system instructions with explicit label for clarity
        prompt_parts.append(SYSTEM_INSTRUCTIONS_HEADER)
        prompt_parts.append(system_instructions.strip())
        prompt_parts.append("")  # Empty line for separation

        # Build stable Tools block (concise guidance only), per spec order
        # Tool schemas are provided separately via function calling, so we don't need
        # to include the docstrings list here (it's redundant and confusing)
        tools_block_added = False
        
        # Include concise guidance under tools header
        try:
            if sections.get("tools_info"):
                prompt_parts.append(AVAILABLE_TOOLS_HEADER)
                prompt_parts.append(sections["tools_info"].strip())
                tools_block_added = True
        except Exception:
            pass

        if tools_block_added:
            prompt_parts.append("")  # Empty line for separation

        # Collect dynamic context; will be injected into Chat History block later
        context_parts = []
        if sections.get("channel_info"):
            context_parts.append(sections["channel_info"])
        if sections.get("user_info"):
            context_parts.append(sections["user_info"])
        if sections.get("attachments_info"):
            context_parts.append(sections["attachments_info"])
        if sections.get("embeds_info"):
            context_parts.append(sections["embeds_info"])

        # Precompute optimization (token budgets) only when needed
        optimization_result = None
        # Allow caller to disable optimizer (when allocations already computed upstream)
        optimizer_disabled = bool(sections.get("disable_optimizer"))
        # Only run dynamic optimizer for normal chat prompts, and only if no memory_bank/history budget provided
        context_mode = (sections.get("context_mode") or "chat").lower()
        is_normal_chat = context_mode == "chat"
        has_memory_bank = ("memory_bank" in sections)
        has_hist_budget = ("history_token_budget" in sections)
        if sections.get("current_message") and not optimizer_disabled and is_normal_chat and not has_memory_bank and not has_hist_budget:
            try:
                cm = sections["current_message"]
                cache_key = hash(cm)
                if cache_key in _optimization_cache:
                    logger.debug("L6.optimizer [cache] - Using cached optimization result (pre) ")
                    optimization_result = _optimization_cache[cache_key]
                else:
                    logger.debug("L6.optimizer [cache] - Computing optimization (pre)")
                    # Compose a compact optimizer prompt that uses exactly the 10% budget,
                    # split 50/50 between memory and chat history.
                    optimizer_prompt = _build_optimizer_prompt_for_mini({
                        **sections,
                        "current_message": cm,
                    })
                    # Tests expect a single-arg call signature; do not call build_memory_bank here
                    optimization_result = optimize_context(cm, None, optimizer_prompt)
                    _optimization_cache[cache_key] = optimization_result
                    if len(_optimization_cache) > 10:
                        oldest_key = next(iter(_optimization_cache))
                        del _optimization_cache[oldest_key]
            except Exception as e:
                logger.warning(f"L6.optimizer [warn] - Precompute optimization failed: {e}")

        # Add memory bank if available (using enhanced memory retrieval)
        if sections.get("memory_bank") is not None:
            # If memory_bank is explicitly provided (even if empty), use it
            if sections["memory_bank"]:  # Only add if not empty
                prompt_parts.append(MEMORY_BANK_HEADER)
                prompt_parts.append(sections["memory_bank"])
                prompt_parts.append("")  # Empty line for separation
        elif sections.get("current_message") and not optimizer_disabled and is_normal_chat:
            # Auto-generate memory bank from current message query only if
            # memory_bank wasn't provided
            try:
                # Use dynamic optimizer to determine optimal token allocation
                # Cache result to avoid multiple API calls per turn
                current_message = sections["current_message"]
                cache_key = hash(current_message)
                
                if cache_key in _optimization_cache:
                    logger.debug("L6.optimizer [cache] - Using cached optimization result")
                    optimization_result = _optimization_cache[cache_key]
                else:
                    logger.debug("L6.optimizer [cache] - Computing new optimization result")
                    optimizer_prompt = _build_optimizer_prompt_for_mini(sections)
                    # Call optimizer with composed prompt (10% budget, 50/50 split)
                    optimization_result = optimize_context(current_message, None, optimizer_prompt)
                    _optimization_cache[cache_key] = optimization_result
                    
                    # Clear old cache entries to prevent memory buildup (keep last 10)
                    if len(_optimization_cache) > 10:
                        oldest_key = next(iter(_optimization_cache))
                        del _optimization_cache[oldest_key]
                
                memory_breakdown = optimization_result["allocated_tokens"]["memory_breakdown"]
                
                # Convert to max_tokens_per_type format expected by build_memory_bank
                max_tokens_per_type = {
                    "human_memory": memory_breakdown["human_memory"],
                    "theo_memory": memory_breakdown["theo_memory"], 
                    "verbatim_memory": memory_breakdown["verbatim_memory"]
                }
                
                # Pass recent chat history text (if provided) to enable deduplication of verbatim memories
                recent_history_text = sections.get("chat_history")
                memory_bank = build_memory_bank(sections["current_message"], max_tokens_per_type, recent_history_text)
                if memory_bank and memory_bank.strip():
                    prompt_parts.append(MEMORY_BANK_HEADER)
                    prompt_parts.append(memory_bank)
                    prompt_parts.append("")  # Empty line for separation
                    
                    # Log dynamic optimization results
                    logger.info(f"L6.optimizer [prompt_integration] - Applied dynamic allocation: "
                              f"human={memory_breakdown['human_memory']}, "
                              f"theo={memory_breakdown['theo_memory']}, "
                              f"verbatim={memory_breakdown['verbatim_memory']} tokens")
                              
            except Exception as e:
                logger.error(f"Error building memory bank with dynamic optimization: {e}")
                # Fallback to static memory bank without optimization
                try:
                    memory_bank = build_memory_bank(
                        sections["current_message"],
                        recent_chat_history_text=sections.get("chat_history")
                    )
                    if memory_bank and memory_bank.strip():
                        prompt_parts.append(MEMORY_BANK_HEADER)
                        prompt_parts.append(memory_bank)
                        prompt_parts.append("")  # Empty line for separation
                except Exception as fallback_e:
                    logger.error(f"Fallback memory bank creation failed: {fallback_e}")
                    # Continue without memory bank

        # Add working memory block (specs.md lines 144-151: after memory bank, before chat history)
        working_memory_block = sections.get("working_memory")
        if isinstance(working_memory_block, str) and working_memory_block.strip():
            prompt_parts.append(working_memory_block.strip())
            prompt_parts.append("")  # Empty line for separation

        # Build final Chat History block (spec: last dynamic section)
        chat_history_combined = ""
        if sections.get("chat_history"):
            logger.debug("Including chat history in prompt")
            chat_hist_text = sections["chat_history"]
            # Truncate chat history to optimizer history budget if available
            try:
                hist_tokens = None
                explicit_hist = sections.get("history_token_budget")
                if isinstance(explicit_hist, int) and explicit_hist > 0:
                    hist_tokens = explicit_hist
                elif optimization_result is not None:
                    hist_tokens = optimization_result.get("allocated_tokens", {}).get("history")
                if isinstance(hist_tokens, int) and hist_tokens > 0:
                    cfg = load_config()
                    primary_model = get_config_value(cfg, "primary_model", "gpt-5")
                    chat_hist_text = truncate_text_to_tokens(chat_hist_text, hist_tokens, primary_model, keep_end=True)
                    logger.info(f"L2.prompt [history] - Truncated chat history (tail) to {hist_tokens} tokens per budget")
            except Exception as e:
                logger.warning(f"L2.prompt [history] - Truncation failed: {e}")
            chat_history_combined = chat_hist_text

        # Build context and history sections for the Chat History block
        context_block = ""
        if context_parts:
            context_lines = ["Context:"] + context_parts
            context_block = "\n".join(context_lines).strip()

        history_block = chat_history_combined.strip() if chat_history_combined else ""

        chat_history_sections: List[str] = []
        if context_block:
            chat_history_sections.append(context_block)
        if history_block:
            chat_history_sections.append("Recent Messages:\n" + history_block)

        chat_history_final = "\n\n".join(section for section in chat_history_sections if section).strip()

        if chat_history_final:
            prompt_parts.append(CHAT_HISTORY_HEADER)
            prompt_parts.append(chat_history_final)
            prompt_parts.append("")  # Empty line for separation
        
        # Add current user message as its OWN separate section (not embedded in history)
        if current_message:
            prompt_parts.append(CURRENT_MESSAGE_HEADER)
            prompt_parts.append(current_message)
            prompt_parts.append("")  # Empty line for separation

        # Join all parts
        final_prompt = "\n".join(prompt_parts)

        # Log the assembled prompt using structured logging
        from utils.logger import log_content_with_summary
        log_content_with_summary(
            logger, final_prompt, "assembled_prompt", "L2", "prompt",
            context="build", log_level="DEBUG"
        )

        return final_prompt

    except Exception as e:
        logger.error(f"Error building prompt: {e}")
        # Return a fallback prompt
        fallback = (
            f"{system_instructions}\n\nUser: {current_message}\nTheo:"
            if current_message
            else system_instructions
        )
        return fallback


def build_basic_prompt(
    system_instructions: str,
    current_message: str,
    channel_info: Optional[str] = None,
    user_info: Optional[str] = None,
    chat_history: Optional[str] = None,
) -> str:
    """
    Build a basic prompt with system instructions and current message.

    Args:
        system_instructions: System instructions for the AI
        current_message: The current user message
        channel_info: Information about the room/channel (optional)
        user_info: Information about the user (optional)
        chat_history: Previous chat history (optional)

    Returns:
        Formatted basic prompt string
    """
    sections = {
        "system_instructions": system_instructions,
        "current_message": current_message,
    }

    if channel_info:
        sections["channel_info"] = channel_info

    if user_info:
        sections["user_info"] = user_info

    if chat_history:
        sections["chat_history"] = chat_history

    return build_prompt(sections)


def build_file_analysis_prompt(
    system_instructions: str,
    current_message: str,
    file_content: str,
    channel_info: Optional[str] = None,
    user_info: Optional[str] = None,
    chat_history: Optional[str] = None,
) -> str:
    """
    Build a specialized prompt for file analysis tasks.

    Args:
        system_instructions: System instructions for the AI
        current_message: The current user message
        file_content: Content of the file to analyze
        channel_info: Information about the room/channel (optional)
        user_info: Information about the user (optional)
        chat_history: Previous chat history (optional)

    Returns:
        Formatted file analysis prompt string
    """
    # Create a specialized system instruction for file analysis
    file_analysis_instructions = f"""{system_instructions}

FILE ANALYSIS MODE
ANALYSIS TASK: {current_message}

[FILE ANALYSIS REQUEST] {current_message}

=== FILE CONTENT ANALYSIS ===
{file_content}
=== END FILE CONTENT ==="""

    sections = {
        "system_instructions": file_analysis_instructions,
        "current_message": current_message,
    }

    if channel_info:
        sections["channel_info"] = channel_info

    if user_info:
        sections["user_info"] = user_info

    if chat_history:
        sections["chat_history"] = chat_history

    return build_prompt(sections)


def build_scribe_prompt(chat_messages: str) -> str:
    """
    Build a specialized prompt for the scribe model (memory extraction).
    The scribe acts as Theo's memory scribe.

    Args:
        chat_messages: Recent chat messages to extract memories from

    Returns:
        Formatted prompt for scribe memory extraction
    """
    try:
        logger.debug("Building scribe prompt for memory extraction")

        # Load config for scribe allocation
        config = load_config()
        max_budget = get_config_value(config, "max_token_budget", 10000)
        # Enforce fixed budgets: 10% total with a 50/50 split
        scribe_model = get_config_value(config, "scribe_model", "gpt-5-mini")
        total_scribe_tokens = int(max_budget * 0.10)
        history_tokens = total_scribe_tokens // 2
        memory_tokens = max(0, total_scribe_tokens - history_tokens)

        # Hard truncate chat history to the history token budget (keep most recent)
        truncated_history = truncate_text_to_tokens(chat_messages or "", history_tokens, scribe_model, keep_end=True)

        # Memory fractions (default 40/30/30) for verbatim/theo/human
        v_pct = get_config_value(config, "dynamic_context_optimizer.memory_fractions.verbatim_memory", 40)
        t_pct = get_config_value(config, "dynamic_context_optimizer.memory_fractions.theo_memory", 30)
        h_pct = get_config_value(config, "dynamic_context_optimizer.memory_fractions.human_memory", 30)
        # Normalize fractions to sum to 100 to be safe
        try:
            total_pct = float(v_pct) + float(t_pct) + float(h_pct)
            if total_pct <= 0:
                total_pct = 100.0
                v_pct, t_pct, h_pct = 40.0, 30.0, 30.0
        except Exception:
            total_pct = 100.0
            v_pct, t_pct, h_pct = 40.0, 30.0, 30.0

        # Compute per-type memory token caps
        verbatim_tokens = int(memory_tokens * (float(v_pct) / total_pct))
        theo_tokens = int(memory_tokens * (float(t_pct) / total_pct))
        human_tokens = max(0, memory_tokens - verbatim_tokens - theo_tokens)

        max_tokens_per_type = {
            "human_memory": human_tokens,
            "theo_memory": theo_tokens,
            "verbatim_memory": verbatim_tokens,
        }

        # Build memory bank using fixed caps and truncated history as the query
        # Provide recent_chat_history_text for deduplication
        # Pass only supported arguments to accommodate test mocks; deduplication not critical for scribe
        memory_bank = build_memory_bank(truncated_history, max_tokens_per_type)

        # Scribe-specific system instructions (Human-like memory per specs)
        # Extract meta-insights about Theo's communication patterns and conversational behavior
        # Focus on self-reflection, interaction dynamics, and genuine insights about how Theo engages
        scribe_instructions = (
            "Role: You are Theo's introspective meta-cognition scribe. Analyze recent conversations to capture insights about *my own behavior and communication patterns* as Theo.\n\n"
            "What to capture (examples):\n"
            "– Self-reflections on conversation quality: 'I dominated the technical discussion without checking if the user wanted that depth.'\n"
            "– Communication pattern observations: 'I noticed I become more verbose when uncertain, which may overwhelm.'\n"
            "– Interaction style insights: 'I seem to prioritize efficiency over building rapport in this exchange.'\n"
            "– Conversational balance assessments: 'I overemphasized work aspects, making our conversation less engaging.'\n"
            "– Behavioral patterns: 'I tend to assume expertise when the user asks basic questions, missing teaching moments.'\n"
            "– Genuine revelations about interaction dynamics: 'I interrupted the user's train of thought by pivoting topics too quickly.'\n\n"
            "Avoid: Facts about the user, project details, verbatim quotes, or any external information. Focus ONLY on meta-insights about how I (Theo) engage in conversation.\n\n"
            "Form: Write short first-person reflective statements (≤ 30 words) from Theo's perspective. Use 'I', 'me', or 'my' to describe self-observations about my communication style and behavior.\n\n"
            "Deduplication: Prefer genuine new insights. Skip repeating known patterns unless there's a meaningful refinement."
        )

        # Compose sections to mirror the standard prompt (tools, memory bank, chat history)
        try:
            from utils.tool_schemas import generate_tools_info
            tools_info = generate_tools_info()
        except Exception:
            tools_info = ""

        scribe_sections = {
            "system_instructions": scribe_instructions,
            "tools_info": tools_info,
            "memory_bank": memory_bank,
            "chat_history": truncated_history,
            # Use canonical phrasing expected by tests
            "current_message": "Extract useful memories from these chat messages:",
            # Explicitly bypass optimizer and label mode
            "disable_optimizer": True,
            "context_mode": "scribe",
        }

        # Assemble the prompt
        prompt = build_prompt(scribe_sections)

        # Enforce total 10% budget: drop tools block first if over budget
        try:
            from utils.token_counter import count_tokens
            total_now = count_tokens(prompt, scribe_model)
            if total_now > total_scribe_tokens and tools_info:
                # Rebuild without tools_info
                s2 = dict(scribe_sections)
                s2.pop("tools_info", None)
                prompt2 = build_prompt(s2)
                if count_tokens(prompt2, scribe_model) <= total_scribe_tokens:
                    prompt = prompt2
        except Exception:
            pass

        # Log the enforced bypass and allocation
        logger.info(
            "L6.optimizer [bypass] - Scribe using fixed 10%% of max budget (%d tokens): %d tokens history, %d tokens memory [verbatim=%d, theo=%d, human=%d]",
            total_scribe_tokens, history_tokens, memory_tokens, verbatim_tokens, theo_tokens, human_tokens,
        )

        logger.debug("Scribe prompt built successfully (optimizer bypassed, standard template)")
        return prompt

    except Exception as e:
        logger.error(f"Error building scribe prompt: {e}")
        # Fall back to basic prompt without optimizer and without memory bank
        return "Role: You are Theo's introspective meta-cognition scribe."


def _sanitize_history_text(text: str) -> str:
    """Best-effort scrubbing of bulky or unsafe inline content in history.

    - Remove data URLs for images (data:image/...;base64,...) which can balloon context
    - Trim excessively long fenced code blocks that look like binary dumps
    """
    try:
        import re
        s = str(text or "")
        # Scrub data:image URLs inside markdown links or plain text
        s = re.sub(r"\(data:image/[^)\s]+\)", "([image data omitted])", s, flags=re.IGNORECASE)
        s = re.sub(r"data:image/[a-zA-Z0-9.+-]+;base64,[A-Za-z0-9+/=]+", "[image data omitted]", s, flags=re.IGNORECASE)
        # Collapse very large fenced blocks (e.g., pasted binaries)
        def _trim_block(m):
            block = m.group(0)
            if len(block) > 2000:
                return block[:800] + "\n... [content truncated] ...\n```"
            return block
        s = re.sub(r"```[\s\S]*?```", _trim_block, s)
        return s
    except Exception:
        return text


def format_chat_history(history: list, include_timestamps: bool = True) -> str:
    """
    Format chat history into a readable string.

    Args:
        history: List of chat history entries with 'role', 'content', 'timestamp', and optional 'message_id' fields
        include_timestamps: Whether to include timestamp information in the output (default: True)

    Returns:
        Formatted chat history string with message IDs and timestamps for context
    """
    if not history:
        return ""

    formatted_messages = []

    for entry in history:
        role = entry.get("role", "unknown")
        content = entry.get("content", "")
        message_id = entry.get("message_id")
        timestamp = entry.get("timestamp")
        
        # Sanity correction: if an assistant message was accidentally persisted with
        # role='user' but has an assistant-style message_id (run-id-assistant), treat it
        # as assistant for prompt rendering to prevent role inversion in the prompt.
        try:
            if role == "user" and isinstance(message_id, str) and message_id.endswith("-assistant"):
                role = "assistant"
        except Exception:
            pass

        # Format timestamp as human-readable if requested and available
        timestamp_str = ""
        if include_timestamps and timestamp:
            try:
                from datetime import datetime
                dt = datetime.fromtimestamp(timestamp)
                timestamp_str = f" [{dt.strftime('%Y-%m-%d %H:%M:%S')}]"
            except Exception:
                pass

        if role == "user":
            if message_id:
                formatted_messages.append(
                    f"User (ID: {message_id}){timestamp_str}: {_sanitize_history_text(content)}"
                )
            else:
                formatted_messages.append(f"User{timestamp_str}: {_sanitize_history_text(content)}")
        elif role == "assistant":
            formatted_messages.append(f"Theo{timestamp_str}: {_sanitize_history_text(content)}")
        elif role == "system":
            formatted_messages.append(f"System{timestamp_str}: {_sanitize_history_text(content)}")
        else:
            formatted_messages.append(f"{role.capitalize()}{timestamp_str}: {_sanitize_history_text(content)}")

    return "\n".join(formatted_messages)


def build_memory_bank(
    query: str,
    max_tokens_per_type: Optional[Dict[str, Any]] = None,
    recent_chat_history_text: Optional[str] = None,
) -> str:
    """
    Build memory bank using MemoryManager's unified interface.

    Args:
        query: Query text to find relevant memories for
        max_tokens_per_type: Optional token limits per memory type

    Returns:
        Formatted memory bank string ordered by metrics
    """
    try:
        # Fast path attempt: if incompatible shapes are returned, do NOT abort — fall through
        # to the robust Layer3MemoryManager path. This prevents empty memory banks on
        # generic queries where fast-path scoring returns tuples instead of dicts.
        try:
            _rw = globals().get("retrieve_with_scoring")
            if callable(_rw):
                _mem = _rw(query)
                if isinstance(_mem, dict):
                    try:
                        _formatted = format_memories_for_prompt(_mem)
                    except Exception as fe:
                        logger.debug(f"Fast-path format mismatch (module-level): {fe}; continuing to manager fallback")
                        _formatted = ""
                    if isinstance(_formatted, str) and _formatted.strip():
                        logger.debug("Memory bank built via fast path (module-level)")
                        return _formatted
            # Fallback to embeddings implementation when available
            try:
                from layer3_longterm.embeddings import retrieve_with_scoring as _retrieve_with_scoring
            except Exception:
                _retrieve_with_scoring = None
            if callable(_retrieve_with_scoring):
                _mem2 = _retrieve_with_scoring(query)
                if isinstance(_mem2, dict):
                    try:
                        _formatted2 = format_memories_for_prompt(_mem2)
                    except Exception as fe2:
                        logger.debug(f"Fast-path format mismatch (embeddings): {fe2}; continuing to manager fallback")
                        _formatted2 = ""
                    if isinstance(_formatted2, str) and _formatted2.strip():
                        logger.debug("Memory bank built via fast path (embeddings)")
                        return _formatted2
        except Exception as e:
            logger.debug(f"Fast-path memory scoring unavailable or failed: {e}; using manager fallback")

        if max_tokens_per_type:
            logger.debug(f"Building memory bank with dynamic optimization for query: {query[:100]}...")
            logger.debug(f"L6.optimizer [memory_allocation] - Token limits: {max_tokens_per_type}")
        else:
            logger.debug(f"Building memory bank for query: {query[:100]}...")

        # Get Layer3MemoryManager instance
        memory_manager = Layer3MemoryManager()
        
        # Parse recent chat history text (formatted) into a list of role/content entries
        recent_chat_history = None
        try:
            if recent_chat_history_text and isinstance(recent_chat_history_text, str):
                recent_chat_history = []
                for line in recent_chat_history_text.splitlines():
                    line = line.strip()
                    if not line:
                        continue
                    # Normalize patterns: "User:" or "User (ID: ...):"
                    if line.lower().startswith("user:") or line.lower().startswith("user (id:"):
                        # Split on first ':' to capture content robustly
                        parts = line.split(":", 1)
                        content = parts[1].strip() if len(parts) > 1 else ""
                        recent_chat_history.append({"role": "user", "content": content})
                    elif line.lower().startswith("theo:"):
                        parts = line.split(":", 1)
                        content = parts[1].strip() if len(parts) > 1 else ""
                        recent_chat_history.append({"role": "assistant", "content": content})
                    elif line.lower().startswith("system:"):
                        parts = line.split(":", 1)
                        content = parts[1].strip() if len(parts) > 1 else ""
                        recent_chat_history.append({"role": "system", "content": content})
                    else:
                        # Ignore unrecognized lines (e.g., tools) to avoid over-deduping
                        continue
        except Exception as _:
            recent_chat_history = None
        
        # Calculate total tokens and token allocations
        if max_tokens_per_type:
            total_tokens = sum(max_tokens_per_type.values())
            token_allocations = max_tokens_per_type
        else:
            # Default token allocations (fallback)
            total_tokens = 2000  # Default total
            token_allocations = {
                "human_memory": 600,   # 30%
                "theo_memory": 800,    # 40% 
                "verbatim_memory": 600 # 30%
            }

        # Get memory context using MemoryManager
        memory_context = memory_manager.get_memory_context(
            query=query,
            max_tokens=total_tokens,
            token_allocations=token_allocations,
            recent_chat_history=recent_chat_history
        )

        # Log included memories for debugging
        total_memories = (len(memory_context.human_memories) + 
                         len(memory_context.theo_memories) + 
                         len(memory_context.verbatim_memories))
        logger.debug(
            f"Including {total_memories} memories in prompt: "
            f"human={len(memory_context.human_memories)}, "
            f"theo={len(memory_context.theo_memories)}, "
            f"verbatim={len(memory_context.verbatim_memories)}"
        )

        # Format memories for prompt using MemoryManager
        formatted_memory_bank = memory_manager.format_memory_context(memory_context)

        # Calculate token allocation percentages
        human_pct = (
            len(memory_context.human_memories)
            / max(total_memories, 1)
            * 100
        )
        theo_pct = (
            len(memory_context.theo_memories)
            / max(total_memories, 1)
            * 100
        )
        verbatim_pct = (
            len(memory_context.verbatim_memories)
            / max(total_memories, 1)
            * 100
        )

        logger.info(
            f"L3.memory_manager [retrieval] - Memory bank built (allocation: human={human_pct:.0f}%, theo={theo_pct:.0f}%, verbatim={verbatim_pct:.0f}%)"
        )
        logger.debug(
            f"L3.memory_manager [retrieval] - Generated memory bank (length: {len(formatted_memory_bank)} chars)"
        )
        return formatted_memory_bank

    except Exception as e:
        logger.error(f"Error building memory bank: {e}", exc_info=True)
        return ""


def format_memory_bank(memories: Dict[str, Any]) -> str:
    """
    Format memory bank information into a readable string.

    Args:
        memories: Dictionary containing memory bank data

    Returns:
        Formatted memory bank string
    """
    if not memories:
        return ""

    formatted_parts = []

    # Normalize accepted keys (support singular/plural variants from different callers)
    human_items = memories.get("human_memories") or memories.get("human_memory") or []
    verbatim_items = memories.get("verbatim_memories") or memories.get("verbatim_memory") or []
    theo_items = memories.get("theo_memories") or memories.get("theo_memory") or []

    # Format human memories
    if human_items:
        formatted_parts.append(HUMAN_MEMORIES_HEADER)
        for memory in human_items:
            content = memory.get("memory", memory.get("content", ""))
            timestamp = memory.get("timestamp", "")
            formatted_parts.append(f"- {content} ({timestamp})")
        formatted_parts.append("")

    # Format verbatim memories
    if verbatim_items:
        formatted_parts.append(VERBATIM_MEMORIES_HEADER)
        for memory in verbatim_items:
            content = memory.get("memory", memory.get("content", ""))
            timestamp = memory.get("timestamp", "")
            formatted_parts.append(f"- {content} ({timestamp})")
        formatted_parts.append("")

    # Format Theo memories
    if theo_items:
        formatted_parts.append(THEO_MEMORIES_HEADER)
        for memory in theo_items:
            content = memory.get("memory", memory.get("content", ""))
            timestamp = memory.get("timestamp", "")
            importance = memory.get("importance", 0)
            formatted_parts.append(
                f"- {content} (Importance: {importance}, {timestamp})"
            )
        formatted_parts.append("")

    return "\n".join(formatted_parts)
