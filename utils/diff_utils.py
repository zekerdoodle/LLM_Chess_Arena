"""
Diff Utilities

Provides a minimal, dependency-free `create_diff` helper used by self-patching
and testing. This replaces the prior implementation that lived in
layer4_tools.file_tools, allowing us to retire that monolithic module.
"""

from pathlib import Path
from typing import Dict, Tuple
import difflib

from utils.logger import get_logger

logger = get_logger(__name__)


def create_diff(file_path: str, proposed_changes: str) -> Tuple[str, Dict[str, object]]:
    """Generate a unified diff between current file contents and proposed changes.

    Args:
        file_path: Path to the file to compare.
        proposed_changes: The new content to diff against the current contents.

    Returns:
        Tuple of (diff_text, tool_output) where tool_output contains metadata.

    Examples:
        >>> diff, meta = create_diff("vault/example.txt", "new text")
        >>> isinstance(diff, str)
        True
    """
    try:
        logger.info(f"Utils [create_diff] - Generating diff for: {file_path}")

        # Read current file content; treat missing file as empty
        current_text = ""
        try:
            p = Path(file_path)
            if p.exists() and p.is_file():
                current_text = p.read_text(encoding="utf-8", errors="ignore")
        except Exception:
            # Fail open to empty if unreadable
            current_text = ""

        current_lines = current_text.splitlines(keepends=True)
        proposed_lines = proposed_changes.splitlines(keepends=True)

        diff_lines = list(
            difflib.unified_diff(
                current_lines,
                proposed_lines,
                fromfile=f"{file_path} (current)",
                tofile=f"{file_path} (proposed)",
                lineterm="",
            )
        )

        if not diff_lines:
            # Maintain a useful output even when contents are identical by emitting
            # headers and the current/proposed content for reviewer context.
            header = f"--- {file_path} (current)\n+++ {file_path} (proposed)\n"
            diff_text = header + "".join(proposed_lines)
            logger.info("Utils [create_diff] - No differences found (emitting headers + content)")
        else:
            diff_text = "".join(diff_lines)

        stats = {
            "current_lines": len(current_lines),
            "proposed_lines": len(proposed_lines),
            "diff_lines": len(diff_lines),
            "lines_added": sum(1 for l in diff_lines if l.startswith("+") and not l.startswith("+++")),
            "lines_removed": sum(1 for l in diff_lines if l.startswith("-") and not l.startswith("---")),
            "lines_modified": len([l for l in diff_lines if l.startswith("@")]),
        }

        return diff_text, {
            "action": "create_diff",
            "success": True,
            "file_path": file_path,
            "has_changes": bool(diff_lines),
            "statistics": stats,
            "diff": diff_text,
        }
    except Exception as e:
        logger.error(f"Utils [create_diff] - Error: {e}", exc_info=True)
        return f"Error creating diff: {str(e)}", {
            "action": "create_diff",
            "success": False,
            "error": str(e),
        }


__all__ = ["create_diff"]
