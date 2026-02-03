"""
Forms Tools for Theo (Layer 4) - Simplified

Simple CRUD for forms: define, show (stops turn), save, list.
No notifications, no prompt injection, no complex orchestration.
"""

from __future__ import annotations

import time
from typing import Any, Dict, List, Optional, Tuple

from utils.logger import get_logger
from utils.forms_store import define_form as _define_form_store
from utils.forms_store import get_form as _get_form_store
from utils.forms_store import append_submission as _append_submission
from utils.forms_store import list_submissions as _list_submissions

logger = get_logger(__name__)


def define_form(form: Dict[str, Any]) -> Tuple[str, Dict[str, Any]]:
    """Register or update a form definition in the vault registry.

    Args:
        form: Dict with keys form_id, title, fields, version?, description?
    """
    ok, msg = _define_form_store(form)
    if ok:
        logger.info(f"L4.forms [tool:define_form] - Defined form: {form.get('form_id')}")
        return "Form registered.", {"success": True, "action": "define_form", "form_id": form.get("form_id")}
    else:
        logger.error(f"L4.forms [tool:define_form] - Failed: {msg}")
        return f"ERROR: {msg}", {"success": False, "error": msg}


def _pairs_to_dict(pairs: Optional[List[Dict[str, Any]]]) -> Dict[str, Any]:
    """Convert array of {id, value} pairs to a dict; tolerate already-dicts."""
    if pairs is None:
        return {}
    if isinstance(pairs, dict):
        return pairs
    out: Dict[str, Any] = {}
    try:
        for item in pairs or []:
            if isinstance(item, dict) and "id" in item:
                out[str(item.get("id"))] = item.get("value")
    except Exception:
        pass
    return out


def show_form(form_id: str, prefill: Optional[Any] = None, room_id: Optional[str] = None) -> Tuple[str, Dict[str, Any]]:
    """Request the UI to render a form immediately (non-terminal).

    Returns an action payload that the orchestrator will emit to the client.
    Theo's turn continues; the form displays inline in the web UI while the
    assistant completes its response. When the user submits, a user message is
    generated with machine-readable form answers.
    """
    form = _get_form_store(form_id)
    if not form:
        msg = f"Form '{form_id}' not found"
        logger.error(f"L4.forms [tool:show_form] - {msg}")
        # Help the caller recover by listing available forms
        try:
            from utils.forms_store import get_form_registry as _get_registry
            reg = _get_registry() or {}
            available = sorted(list(reg.keys()))
        except Exception:
            available = []
        return (
            f"ERROR: {msg}. Available forms: {', '.join(available) if available else '(none)'}",
            {
                "success": False,
                "error": msg,
                "available_forms": available,
                "action": "show_form_failed",
                "requested_form_id": form_id,
            },
        )
    
    payload = {
        "action": "show_form",
        "form": form,
        "prefill": _pairs_to_dict(prefill),
        "room": room_id,
        # No terminal flag — form display is non-blocking
    }
    logger.info(f"L4.forms [tool:show_form] - Emitting form {form_id} (non-terminal)")
    return "Opening a form for you to fill out.", payload


def save_form_submission(form_id: str, answers: Any, room_id: Optional[str] = None, mock_mode: bool = False) -> Tuple[str, Dict[str, Any]]:
    """Persist a form submission to the JSONL file.
    
    Args:
        form_id: The ID of the form being submitted
        answers: The form answers (dict or list of {id, value} pairs)
        room_id: Optional room identifier
        mock_mode: If True, allows programmatic form submission for testing without user interaction
    """
    form = _get_form_store(form_id)
    if not form:
        msg = f"Form '{form_id}' not found"
        logger.error(f"L4.forms [tool:save_form_submission] - {msg}")
        return f"ERROR: {msg}", {"success": False, "error": msg}
    
    rec = {
        "ts": time.time(),
        "form_id": form_id,
        "version": form.get("version", 1),
        "answers": _pairs_to_dict(answers),
        "room_id": room_id,
    }
    
    ok, msg = _append_submission(rec)
    if ok:
        mode_suffix = " (mock mode)" if mock_mode else ""
        logger.info(f"L4.forms [tool:save_form_submission] - Saved submission for {form_id}{mode_suffix}")
        return f"Thanks — recorded your response.{mode_suffix}", {"success": True, "action": "form_saved", "form_id": form_id, "mock_mode": mock_mode}
    else:
        logger.error(f"L4.forms [tool:save_form_submission] - Failed: {msg}")
        return f"ERROR: {msg}", {"success": False, "error": msg}


def list_form_submissions(form_id: Optional[str] = None, limit: int = 20, after_ts: Optional[float] = None, room_id: Optional[str] = None) -> Tuple[str, Dict[str, Any]]:
    """List recent submissions for Theo's review."""
    items = _list_submissions(form_id=form_id, limit=limit, after_ts=after_ts, room_id=room_id)
    if not items:
        return "No submissions found.", {"success": True, "count": 0, "submissions": []}
    
    lines = []
    for it in items:
        ts = it.get("ts")
        answers = it.get("answers", {})
        lines.append(f"- {form_id or it.get('form_id')} @ {ts}: {list(answers.keys())}")
    
    return "\n".join(lines), {"success": True, "count": len(items), "submissions": items}


# Removed: see_form_updates (use list_form_submissions)


__all__ = [
    "define_form",
    "show_form", 
    "save_form_submission",
    "list_form_submissions",
]
