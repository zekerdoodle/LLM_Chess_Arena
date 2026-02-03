"""
Prompt Printer Module for LLM Chess Arena

Provides utilities for parsing prompts into sections for different model providers.
"""

from typing import Dict, Optional

from utils.prompt_labels import (
    AVAILABLE_TOOLS_HEADER,
    CHAT_HISTORY_HEADER,
    CURRENT_MESSAGE_HEADER,
    MEMORY_BANK_HEADER,
    SYSTEM_INSTRUCTIONS_HEADER,
    WORKING_MEMORY_HEADER,
)


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

    Providers that expect role-based messages can use this helper to remap
    a plain-text prompt into structured messages.
    """
    sections: Dict[str, list] = {}
    current_header: Optional[str] = None

    for line in (prompt_text or "").splitlines():
        stripped = line.strip()
        if stripped in _PRIMARY_SECTION_HEADERS:
            current_header = stripped
            sections[current_header] = []
            continue

        if current_header is None:
            continue

        sections[current_header].append(line)

    normalized: Dict[str, str] = {}
    for header, lines in sections.items():
        content = "\n".join(lines).strip()
        if content:
            normalized[header] = content

    return normalized
