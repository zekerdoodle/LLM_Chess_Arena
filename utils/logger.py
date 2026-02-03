# Fixed: remove accidental/bad import introduced by a failed patch
"""
Enhanced Logger Module (Context + Sanitization)

Centralized logging with:
- Context injection (request_id, run_id, room_id) via contextvars
- Sensitive data sanitization (API keys, tokens, passwords)
- Smart content filtering/truncation for large payloads
- Structured tool/model call helpers

Integrates with logging.dictConfig via config/logging.yaml by referencing filters:

filters:
  context:
    (): utils.logger.LogContextFilter
  sanitize:
    (): utils.logger.SanitizingFilter

Attach both filters to console and file handlers for consistent behavior.
"""

import contextvars
import errno
import hashlib
import json
import logging
import logging.handlers
import re
import sys
import time
from uuid import uuid4
from pathlib import Path
from typing import Any, Dict, Optional, TextIO, Union


class ContentFilter:
    """Smart content filtering for log messages."""
    
    @staticmethod
    def sanitize_sensitive_data(content: str) -> str:
        """Remove or mask sensitive information from log content."""
        # Mask API keys
        content = re.sub(r'(sk-|Bearer\s+)[a-zA-Z0-9\-_]{10,}', r'\1***', content)
        content = re.sub(r'(token["\s:=]+)[a-zA-Z0-9\-_]{10,}', r'\1***', content, re.IGNORECASE)
        content = re.sub(r'(key["\s:=]+)[a-zA-Z0-9\-_]{10,}', r'\1***', content, re.IGNORECASE)
        content = re.sub(r'(password["\s:=]+)[^\s"\']{4,}', r'\1***', content, re.IGNORECASE)
        # Strip embedded image payloads (data URIs) to avoid log bloat
        content = re.sub(
            r'data:image/[a-z0-9.+-]+;base64,[a-z0-9+/=\r\n]+',
            '[image data omitted]',
            content,
            flags=re.IGNORECASE,
        )
        return content
    
    @staticmethod
    def truncate_content(content: str, max_chars: int = 300, preserve_structure: bool = True) -> str:
        """Intelligently truncate content while preserving readability."""
        if len(content) <= max_chars:
            return content
        
        if preserve_structure:
            # Try to preserve structure for JSON, code, etc.
            if content.strip().startswith('{') and '}' in content:
                # JSON-like structure
                try:
                    lines = content.split('\n')
                    if len(lines) > 3:
                        return f"{lines[0]}\n  ... ({len(lines)-2} lines) ...\n{lines[-1]}"
                except:
                    pass
            
            # For other structured content, try to keep beginning and end
            mid_point = max_chars // 2 - 20
            return f"{content[:mid_point]}... [truncated {len(content)-max_chars} chars] ...{content[-(max_chars-mid_point-50):]}"
        
        return content[:max_chars] + f"... [truncated {len(content)-max_chars} chars]"
    
    @staticmethod
    def summarize_tool_args(args: Dict[str, Any]) -> str:
        """Create concise summary of tool arguments instead of full dump."""
        if not args:
            return "{}"
        
        summary_parts = []
        for key, value in args.items():
            if isinstance(value, str):
                if len(value) > 50:
                    char_count = len(value)
                    # Try to identify content type
                    if value.strip().startswith('{'):
                        summary_parts.append(f"{key}: json({char_count} chars)")
                    elif '\n' in value:
                        line_count = value.count('\n') + 1
                        summary_parts.append(f"{key}: text({line_count} lines, {char_count} chars)")
                    else:
                        summary_parts.append(f"{key}: str({char_count} chars)")
                else:
                    summary_parts.append(f"{key}: '{value}'")
            elif isinstance(value, (list, tuple)):
                summary_parts.append(f"{key}: {type(value).__name__}({len(value)} items)")
            elif isinstance(value, dict):
                summary_parts.append(f"{key}: dict({len(value)} keys)")
            else:
                summary_parts.append(f"{key}: {value}")
        
        return "{" + ", ".join(summary_parts) + "}"
    
    @staticmethod
    def get_content_hash(content: str) -> str:
        """Generate short hash for content deduplication."""
        return hashlib.md5(content.encode()).hexdigest()[:8]
    
    @staticmethod
    def format_structured_log(layer: str, module: str, context: str, message: str, 
                            extra_data: Optional[Dict[str, Any]] = None) -> str:
        """Format log message according to specs: [LAYER.MODULE] [CONTEXT] - MESSAGE"""
        formatted_context = f"[{context}]" if context else ""
        base_msg = f"[{layer}.{module}] {formatted_context} - {message}"
        
        if extra_data:
            # Add structured data in a concise way
            data_parts = []
            for key, value in extra_data.items():
                if isinstance(value, (int, float)):
                    data_parts.append(f"{key}:{value}")
                elif isinstance(value, str) and len(value) < 20:
                    data_parts.append(f"{key}:{value}")
                else:
                    data_parts.append(f"{key}:")
            
            if data_parts:
                base_msg += f" ({', '.join(data_parts)})"
        
        return base_msg


# -------------------- Contextvars support --------------------
_request_id_var: contextvars.ContextVar[str] = contextvars.ContextVar("request_id", default="-")
_run_id_var: contextvars.ContextVar[str] = contextvars.ContextVar("run_id", default="-")
_room_id_var: contextvars.ContextVar[str] = contextvars.ContextVar("room_id", default="-")


def new_request_id() -> str:
    """Generate a new request id (12-hex)."""
    return uuid4().hex[:12]


def set_log_context(*, request_id: Optional[str] = None, run_id: Optional[str] = None, room_id: Optional[str] = None) -> None:
    """Set per-task log context values. Use None to leave a value unchanged."""
    try:
        if request_id is not None:
            _request_id_var.set(str(request_id) if request_id else "-")
        if run_id is not None:
            _run_id_var.set(str(run_id) if run_id else "-")
        if room_id is not None:
            _room_id_var.set(str(room_id) if room_id else "-")
    except Exception:
        pass


def clear_log_context() -> None:
    """Reset context variables to defaults."""
    try:
        _request_id_var.set("-")
        _run_id_var.set("-")
        _room_id_var.set("-")
    except Exception:
        pass


def get_current_room_id(default: Optional[str] = None) -> Optional[str]:
    """
    Return the current room id from the logging context if available.

    This provides a lightweight way for tools (e.g., task creation) to infer
    the originating room without depending on server-specific classes.

    Args:
        default: Value to return if no room id is set.

    Returns:
        The room id string or the provided default if not set.

    Example:
        >>> set_log_context(room_id="r1")
        >>> get_current_room_id()
        'r1'
    """
    try:
        rid = _room_id_var.get()
        if isinstance(rid, str) and rid and rid != "-":
            return rid
        return default
    except Exception:
        return default


class LogContextFilter(logging.Filter):
    """Inject request_id, run_id, room_id into every LogRecord."""

    def filter(self, record: logging.LogRecord) -> bool:  # noqa: A003
        try:
            if not hasattr(record, "request_id"):
                record.request_id = _request_id_var.get() or "-"
            if not hasattr(record, "run_id"):
                record.run_id = _run_id_var.get() or "-"
            if not hasattr(record, "room_id"):
                record.room_id = _room_id_var.get() or "-"
        except Exception:
            record.request_id = getattr(record, "request_id", "-")
            record.run_id = getattr(record, "run_id", "-")
            record.room_id = getattr(record, "room_id", "-")
        return True


class SanitizingFilter(logging.Filter):
    """Sanitize sensitive data from log messages."""

    def filter(self, record: logging.LogRecord) -> bool:  # noqa: A003
        try:
            msg = record.getMessage()
            if isinstance(msg, str) and msg:
                sanitized = ContentFilter.sanitize_sensitive_data(msg)
                if sanitized != msg:
                    record.msg = sanitized
                    record.args = ()
        except Exception:
            pass
        return True


class SafeRotatingFileHandler(logging.handlers.RotatingFileHandler):
    """Rotating handler that disables itself gracefully when disk space is exhausted."""

    def __init__(
        self,
        filename: str,
        mode: str = "a",
        maxBytes: int = 0,
        backupCount: int = 0,
        encoding: Optional[str] = None,
        delay: bool = False,
        errors: Optional[str] = None,
        fallback_stream: Optional[TextIO] = None,
    ) -> None:
        super().__init__(
            filename,
            mode=mode,
            maxBytes=maxBytes,
            backupCount=backupCount,
            encoding=encoding,
            delay=delay,
            errors=errors,
        )
        self._fallback_stream: TextIO = fallback_stream or sys.stderr
        self.disabled_due_to_disk_error: bool = False
        self.last_disk_error: Optional[OSError] = None

    def emit(self, record) -> None:  # type: ignore[override]
        if self.disabled_due_to_disk_error:
            return
        try:
            super().emit(record)
        except OSError as exc:  # pragma: no cover - exercised via dedicated tests
            if _is_disk_full_error(exc):
                self._handle_disk_full(exc)
                return
            raise

    def _handle_disk_full(self, error: OSError) -> None:
        self.disabled_due_to_disk_error = True
        self.last_disk_error = error
        try:
            logging.getLogger().removeHandler(self)
        except Exception:
            pass
        try:
            super().close()
        except Exception:
            pass
        timestamp = time.strftime("%Y-%m-%d %H:%M:%S")
        warning = (
            f"{timestamp} [WARNING] Theo file logging disabled: disk full ({error})\n"
            "Logging will continue to console only until disk space is restored.\n"
        )
        try:
            self._fallback_stream.write(warning)
        except Exception:
            try:
                sys.stderr.write(warning)
            except Exception:
                pass


def _is_disk_full_error(error: OSError) -> bool:
    try:
        if error.errno == errno.ENOSPC:
            return True
    except Exception:
        pass
    message = str(error)
    return "No space left on device" in message or "ENOSPC" in message


def setup_logger(
    name: str = "theo",
    level: str = "INFO",
    log_file: str = "logs/theo.log",
    max_size: str = "10MB",
    backup_count: int = 5,
) -> logging.Logger:
    """
    Set up the logging configuration.
    
    Args:
        name: Logger name
        level: Logging level (DEBUG, INFO, WARNING, ERROR, CRITICAL)
        log_file: Log file path
        max_size: Maximum log file size (e.g., '10MB', '100KB')
        backup_count: Number of rotated log files to keep
    
    Returns:
        Configured logger instance
    """
    # Convert level string to logging level
    log_level = getattr(logging, level.upper(), logging.INFO)

    # Parse max_size string into bytes
    def parse_size(size_str: str) -> int:
        size_str = size_str.strip().upper()
        if size_str.endswith("KB"):
            return int(size_str[:-2]) * 1024
        elif size_str.endswith("MB"):
            return int(size_str[:-2]) * 1024 * 1024
        elif size_str.endswith("GB"):
            return int(size_str[:-2]) * 1024 * 1024 * 1024
        else:
            return int(size_str)

    max_bytes = parse_size(max_size)

    # Configure the root logger to affect all loggers
    root_logger = logging.getLogger()
    root_logger.setLevel(log_level)

    # Clear existing handlers to avoid duplicates
    root_logger.handlers.clear()

    # Create enhanced formatters
    detailed_formatter = logging.Formatter(
        "%(asctime)s [%(levelname)s] %(message)s",
        datefmt="%Y-%m-%d %H:%M:%S"
    )
    console_formatter = logging.Formatter(
        "%(asctime)s [%(levelname)s] %(message)s",
        datefmt="%H:%M:%S"
    )

    # Console handler (concise format)
    console_handler = logging.StreamHandler(sys.stdout)
    console_handler.setLevel(log_level)
    console_handler.setFormatter(console_formatter)
    console_handler.addFilter(LogContextFilter())
    console_handler.addFilter(SanitizingFilter())
    root_logger.addHandler(console_handler)

    # File handler with rotation (detailed format)
    if log_file:
        log_path = Path(log_file)
        log_path.parent.mkdir(parents=True, exist_ok=True)

        file_handler = SafeRotatingFileHandler(
            log_file,
            maxBytes=max_bytes,
            backupCount=backup_count,
            encoding="utf-8",
        )
        file_handler.setLevel(log_level)
        file_handler.setFormatter(detailed_formatter)
        file_handler.addFilter(LogContextFilter())
        file_handler.addFilter(SanitizingFilter())
        root_logger.addHandler(file_handler)

    # Also create/configure the named logger and return it
    logger = logging.getLogger(name)
    logger.setLevel(log_level)

    return logger


def get_logger(name: str = "theo") -> logging.Logger:
    """
    Get a logger instance, creating it if it doesn't exist.

    Args:
        name: Logger name

    Returns:
        Logger instance
    """
    return logging.getLogger(name)


# Enhanced logging functions with smart filtering

# Tools that should always surface at INFO level because they impact repo state.
_HIGH_VISIBILITY_TOOLS = {
    "apply_patch",
    "create_backup",
    "create_clone",
    "snapshot_working_memory",
}


def log_tool_execution(logger: logging.Logger, tool_name: str, args: Dict[str, Any], 
                      layer: str = "L4", module: str = "tools") -> None:
    """
    Log tool execution - optimized for Claude's debugging needs.
    """
    context = f"tool:{tool_name}"
    
    # Surface high-visibility tools at INFO without dumping full payloads; others drop to DEBUG.
    if tool_name in _HIGH_VISIBILITY_TOOLS:
        message = f"Tool {tool_name} invoked"
        level = logging.INFO
    else:
        args_summary = ContentFilter.summarize_tool_args(args)
        message = f"Tool {tool_name} called {args_summary}"
        level = logging.DEBUG

    formatted_msg = ContentFilter.format_structured_log(layer, module, context, message)
    logger.log(level, formatted_msg)


def log_tool_result(logger: logging.Logger, tool_name: str, success: bool, result: str,
                   layer: str = "L4", module: str = "tools") -> None:
    """
    Log tool results - critical for visibility.
    """
    context = f"tool:{tool_name}"
    
    if success:
        if tool_name in _HIGH_VISIBILITY_TOOLS:
            message = f"Tool {tool_name} completed"
            level = logging.INFO
        else:
            message = f"Tool {tool_name} succeeded"
            level = logging.DEBUG
    else:
        truncated_error = ContentFilter.truncate_content(str(result), max_chars=160, preserve_structure=False)
        message = f"Tool {tool_name} failed: {truncated_error}"
        level = logging.WARNING

    formatted_msg = ContentFilter.format_structured_log(layer, module, context, message)
    logger.log(level, formatted_msg)


def log_model_call(logger: logging.Logger, provider: str, model: str, prompt_chars: int,
                  response_chars: int = None, duration: float = None, 
                  layer: str = "L1") -> None:
    """
    Log model API calls with performance metrics.
    """
    context = f"model:{model}"
    
    extra_data = {"prompt_chars": prompt_chars}
    if response_chars is not None:
        extra_data["response_chars"] = response_chars
    if duration is not None:
        extra_data["duration"] = f"{duration:.1f}s"
    
    message = f"API call to {provider}"
    formatted_msg = ContentFilter.format_structured_log(layer, provider, context, message, extra_data)
    logger.info(formatted_msg)


def log_memory_operation(logger: logging.Logger, operation: str, memory_type: str,
                        count: int = None, layer: str = "L3") -> None:
    """
    Log memory operations (retrieval, storage, etc.).
    """
    context = f"mem:{memory_type}"
    message = f"{operation.capitalize()} operation"
    
    extra_data = {}
    if count is not None:
        extra_data["count"] = count
    
    formatted_msg = ContentFilter.format_structured_log(layer, "memory", context, message, extra_data)
    logger.info(formatted_msg)


def log_content_with_summary(logger: logging.Logger, content: str, content_type: str,
                           layer: str, module: str, context: str = "",
                           log_level: str = "DEBUG") -> None:
    """
    Log content with intelligent summarization instead of full dump.
    """
    sanitized = ContentFilter.sanitize_sensitive_data(content)
    
    char_count = len(content)
    line_count = content.count('\n') + 1 if '\n' in content else 1
    content_hash = ContentFilter.get_content_hash(content)
    
    message = f"{content_type} processed"
    extra_data = {
        "chars": char_count,
        "lines": line_count,
        "hash": content_hash
    }
    
    formatted_msg = ContentFilter.format_structured_log(layer, module, context, message, extra_data)
    
    if log_level.upper() == "DEBUG":
        truncated = ContentFilter.truncate_content(sanitized, max_chars=200)
        formatted_msg += f" | Preview: {truncated}"
    
    getattr(logger, log_level.lower())(formatted_msg)


def log_performance_metric(logger: logging.Logger, metric_name: str, value: Union[int, float],
                          unit: str = "", layer: str = "PERF", module: str = "metrics") -> None:
    """
    Log performance metrics in a structured way.
    """
    message = f"{metric_name}: {value}{unit}"
    formatted_msg = ContentFilter.format_structured_log(layer, module, "performance", message)
    logger.info(formatted_msg)


def log_function_entry(
    logger: logging.Logger, func_name: str, **kwargs
) -> None:
    """
    Log function entry with parameters (intelligently truncated).
    """
    if not kwargs:
        logger.debug(f"→ {func_name}()")
        return
    
    args_summary = ContentFilter.summarize_tool_args(kwargs)
    logger.debug(f"→ {func_name}({args_summary})")


def log_function_exit(
    logger: logging.Logger, func_name: str, result: Optional[Any] = None
) -> None:
    """
    Log function exit with optional result.
    """
    if result is not None:
        if isinstance(result, str):
            result_summary = ContentFilter.truncate_content(result, max_chars=80, preserve_structure=False)
        else:
            result_summary = str(result)[:80] + "..." if len(str(result)) > 80 else str(result)
        logger.debug(f"← {func_name} → {result_summary}")
    else:
        logger.debug(f"← {func_name}")


def log_error(
    logger: logging.Logger, message: str, exc_info: Optional[Exception] = None,
    layer: str = "", module: str = "", context: str = ""
) -> None:
    """
    Log an error with optional exception information and structured format.
    """
    if layer and module:
        formatted_msg = ContentFilter.format_structured_log(layer, module, context, message)
    else:
        formatted_msg = message
    
    if exc_info:
        logger.error(f"{formatted_msg}: {exc_info}", exc_info=True)
    else:
        logger.error(formatted_msg)


# Initialize default logger

# 🥚 Easter egg: Theo was here – harmless self-patch on 2025-09-05

# 🥚 Easter egg: Theo was here — harmless self-patch on 2025-09-05
