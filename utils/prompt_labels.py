"""Centralized prompt section labels used throughout Theo's prompting pipeline.

Having a single source of truth for section headers keeps provider adapters,
prompt builders, and tests aligned as we refine the master prompt layout.
"""

from __future__ import annotations

# Top-level prompt sections (ordered per specs)
SYSTEM_INSTRUCTIONS_HEADER = "System Instructions:"
AVAILABLE_TOOLS_HEADER = "Available Tools (native function calling only):"
MEMORY_BANK_HEADER = "Memory Bank (long-term context snippets):"
WORKING_MEMORY_HEADER = "### Working Memory (private; do not reveal)"
CHAT_HISTORY_HEADER = "Chat History (messages in this room, chronological order):"
CURRENT_MESSAGE_HEADER = "Current User Message:"

# Memory sub-sections with descriptive context
HUMAN_MEMORIES_HEADER = (
    "Human Memories (memories extracted subconsciously; ordered by relevance and recency):"
)
VERBATIM_MEMORIES_HEADER = (
    "Verbatim Memories (chat exchanges from other rooms; ordered by relevance and recency):"
)
THEO_MEMORIES_HEADER = (
    "Theo Memories (self-authored priorities; ordered by relevance, recency, and importance):"
)


def get_memory_header(memory_type: str) -> str:
    """Return the descriptive header for a given memory type.

    Args:
        memory_type: Canonical memory type identifier (human, verbatim, theo)

    Returns:
        Header string for the requested memory type. Falls back to Theo memories header
        when an unknown type is provided to keep formatting stable.
    """

    mapping = {
        "human": HUMAN_MEMORIES_HEADER,
        "verbatim": VERBATIM_MEMORIES_HEADER,
        "theo": THEO_MEMORIES_HEADER,
    }
    return mapping.get(memory_type.lower(), THEO_MEMORIES_HEADER)

