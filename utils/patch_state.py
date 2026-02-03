"""
Patch state persistence utilities.

Stores ephemeral patch options in the vault so they survive code rollbacks
but are cleared after use on startup.

Primary use: allow the apply_patch tool to decide whether the post‑reboot
continuation run should be silent or visible, and optionally hint a target
room/run for wake‑up routing.
"""

from __future__ import annotations

from pathlib import Path
from typing import Any, Dict, Optional
import json

from utils.vault_paths import get_vault_root
from utils.logger import get_logger


logger = get_logger(__name__)


def _opts_path() -> Path:
    try:
        root = get_vault_root()
    except Exception:
        # Fallback to default path if resolver fails for any reason
        root = Path("vault")
    try:
        root.mkdir(parents=True, exist_ok=True)
    except Exception:
        pass
    return root / "patch_options.json"


def set_patch_options(*, silent: Optional[bool] = None, room_id: Optional[str] = None, run_id: Optional[str] = None) -> bool:
    """
    Persist patch options to the vault.

    Args:
        silent: If True, the continuation run should not surface a visible assistant reply.
                If False, the continuation should be visible. If None, defaults will be used downstream.
        room_id: Optional room id hint for wake‑up routing.
        run_id: Optional run id to associate with the wake‑up.

    Returns:
        True if options were written successfully.
    """
    try:
        path = _opts_path()
        data: Dict[str, Any] = {}
        if path.exists():
            try:
                data = json.loads(path.read_text(encoding="utf-8")) or {}
            except Exception:
                data = {}
        if silent is not None:
            data["silent"] = bool(silent)
        if isinstance(room_id, str) and room_id:
            data["room_id"] = room_id
        if isinstance(run_id, str) and run_id:
            data["run_id"] = run_id
        path.write_text(json.dumps(data, indent=2, ensure_ascii=False), encoding="utf-8")
        logger.debug("patch_state: options persisted (%s)", ", ".join(sorted(data.keys())))
        return True
    except Exception as e:
        try:
            logger.debug(f"patch_state: failed to persist options: {e}")
        except Exception:
            pass
        return False


def get_patch_options(clear: bool = True) -> Dict[str, Any]:
    """
    Load patch options from the vault. Optionally clear after read.

    Args:
        clear: When True, remove the file after reading to avoid reuse.

    Returns:
        Dict with optional keys: silent (bool), room_id (str), run_id (str)
    """
    out: Dict[str, Any] = {}
    try:
        path = _opts_path()
        if path.exists():
            try:
                out = json.loads(path.read_text(encoding="utf-8")) or {}
            except Exception:
                out = {}
            if clear:
                try:
                    path.unlink()
                except Exception:
                    pass
    except Exception as e:
        try:
            logger.debug(f"patch_state: failed to load options: {e}")
        except Exception:
            pass
    return out


__all__ = ["set_patch_options", "get_patch_options"]

