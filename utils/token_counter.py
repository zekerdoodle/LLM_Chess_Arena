"""
Token Counter Module for LLM Chess Arena

Simple token counting with tiktoken fallback.
"""

import logging
from typing import Optional

logger = logging.getLogger(__name__)

# Try to import tiktoken
try:
    import tiktoken
    _HAS_TIKTOKEN = True
except ImportError:
    tiktoken = None  # type: ignore
    _HAS_TIKTOKEN = False


def count_tokens(text: str, model: str = "gpt-4o") -> int:
    """
    Count tokens in text using tiktoken for the specified model.

    Args:
        text: Text to count tokens for
        model: Model name to determine encoding

    Returns:
        Number of tokens in the text
    """
    if not _HAS_TIKTOKEN or tiktoken is None:
        # Fallback: estimate ~4 characters per token
        return len(text) // 4

    try:
        # Use cl100k_base encoding (works for most modern models)
        encoding = tiktoken.get_encoding("cl100k_base")
        return len(encoding.encode(text))
    except Exception as e:
        logger.warning(f"Token counting failed: {e}, using fallback")
        return len(text) // 4
