"""
Simple Logger Module for LLM Chess Arena
"""

import logging
import re
from typing import Any, Dict, Optional

# Configure basic logging format
logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s - %(name)s - %(levelname)s - %(message)s'
)


def get_logger(name: str) -> logging.Logger:
    """Get a logger instance with the given name."""
    return logging.getLogger(name)


def _sanitize(content: str) -> str:
    """Remove or mask sensitive information from log content."""
    content = re.sub(r'(sk-|Bearer\s+)[a-zA-Z0-9\-_]{10,}', r'\1***', content)
    content = re.sub(r'(token["\s:=]+)[a-zA-Z0-9\-_]{10,}', r'\1***', content, re.IGNORECASE)
    content = re.sub(r'(key["\s:=]+)[a-zA-Z0-9\-_]{10,}', r'\1***', content, re.IGNORECASE)
    return content


def log_content_with_summary(
    logger: logging.Logger,
    content: str,
    max_length: int = 500,
    level: int = logging.DEBUG
) -> None:
    """Log content with truncation for long messages."""
    if len(content) <= max_length:
        logger.log(level, _sanitize(content))
    else:
        truncated = content[:max_length] + f"... [truncated {len(content) - max_length} chars]"
        logger.log(level, _sanitize(truncated))


def log_model_call(
    logger: logging.Logger,
    model: str,
    messages: list,
    response: Optional[Any] = None,
    error: Optional[Exception] = None,
    duration_ms: Optional[float] = None
) -> None:
    """Log a model API call."""
    msg_count = len(messages) if messages else 0

    if error:
        logger.error(f"Model call failed: {model} ({msg_count} messages) - {error}")
    else:
        duration_str = f" in {duration_ms:.0f}ms" if duration_ms else ""
        logger.info(f"Model call: {model} ({msg_count} messages){duration_str}")
