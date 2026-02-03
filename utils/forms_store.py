"""
Forms storage utilities: form registry and submissions (JSONL) with atomic ops.

Used by tools and server endpoints.
"""

from __future__ import annotations

import json
import os
import time
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple

from utils.logger import get_logger
from utils.vault_paths import get_vault_root
from utils.atomic_file_ops import load_json, save_json

logger = get_logger(__name__)


try:
    VAULT_FORMS_DIR = Path(get_vault_root()) / "forms"
except Exception:
    VAULT_FORMS_DIR = Path("vault/forms")
REGISTRY_FILE = VAULT_FORMS_DIR / "forms_registry.json"
SUBMISSIONS_FILE = VAULT_FORMS_DIR / "submissions.jsonl"
UI_EVENTS_FILE = VAULT_FORMS_DIR / "ui_events.jsonl"
DECLINES_FILE = VAULT_FORMS_DIR / "declines.jsonl"


def _ensure_dirs() -> None:
    try:
        VAULT_FORMS_DIR.mkdir(parents=True, exist_ok=True)
    except Exception as e:
        logger.error(f"forms_store: failed to ensure forms dir: {e}")


def get_form_registry() -> Dict[str, Any]:
    _ensure_dirs()
    data = load_json(REGISTRY_FILE, {})
    if not isinstance(data, dict):
        return {}
    return data


def save_form_registry(data: Dict[str, Any]) -> bool:
    _ensure_dirs()
    return save_json(REGISTRY_FILE, data)


def define_form(form_def: Dict[str, Any]) -> Tuple[bool, str]:
    """Register or update a form definition.

    Requires a "form_id" string. Accepts a "version" (int or str).
    """
    try:
        form_id = str(form_def.get("form_id", "")).strip()
        if not form_id:
            return False, "form_id is required"
        registry = get_form_registry()
        existing = registry.get(form_id) or {}
        # Replace entire def; keep a compact structure
        registry[form_id] = {
            "form_id": form_id,
            "version": form_def.get("version", 1),
            "title": form_def.get("title", form_id),
            "description": form_def.get("description", ""),
            "fields": form_def.get("fields", []),
        }
        if save_form_registry(registry):
            return True, "Form defined"
        return False, "Failed to save registry"
    except Exception as e:
        logger.error(f"forms_store: define_form error: {e}")
        return False, str(e)


def get_form(form_id: str) -> Optional[Dict[str, Any]]:
    try:
        registry = get_form_registry()
        form = registry.get(str(form_id))
        return form if isinstance(form, dict) else None
    except Exception as e:
        logger.error(f"forms_store: get_form error: {e}")
        return None


def append_submission(record: Dict[str, Any]) -> Tuple[bool, str]:
    """Append a single JSON object as a line into submissions.jsonl."""
    try:
        _ensure_dirs()
        # Lightweight duplicate suppression: if an identical submission for the same
        # form/room was saved very recently, skip appending.
        try:
            form_id = str(record.get("form_id"))
            room_id = record.get("room_id")
            answers = record.get("answers") or {}
            recent = list_submissions(form_id=form_id, limit=1, after_ts=None, room_id=room_id)
            if recent:
                last = recent[0]
                # Same answers and within 2 seconds window
                same_answers = (last.get("answers") or {}) == answers
                try:
                    dt = abs(float(record.get("ts", 0.0)) - float(last.get("ts", 0.0)))
                except Exception:
                    dt = 0.0
                if same_answers and dt <= 2.0:
                    return True, "Duplicate submission suppressed"
        except Exception:
            pass

        # Rate limiting: suppress bursts of programmatic submissions to the same form/room
        # within a short window. Legit users won't submit >3 times in 10s, but smoke tests
        # or loops might. We count last ~64KB tail to avoid reading the whole file.
        try:
            now_ts = float(record.get("ts") or time.time())
            window_s = 10.0
            max_within_window = 3
            fid = str(record.get("form_id") or "")
            rid = str(record.get("room_id") or "")
            if SUBMISSIONS_FILE.exists():
                with open(SUBMISSIONS_FILE, "rb") as rf:
                    rf.seek(0, 2)
                    size = rf.tell()
                    take = min(65536, size)
                    rf.seek(size - take)
                    tail = rf.read().decode("utf-8", errors="ignore")
                lines = [ln.strip() for ln in tail.splitlines() if ln.strip()]
                count = 0
                for ln in reversed(lines):
                    try:
                        obj = json.loads(ln)
                    except Exception:
                        continue
                    if str(obj.get("form_id") or "") != fid:
                        continue
                    if str(obj.get("room_id") or "") != rid:
                        continue
                    try:
                        ts = float(obj.get("ts", 0.0))
                    except Exception:
                        ts = 0.0
                    if (now_ts - ts) <= window_s:
                        count += 1
                        if count >= max_within_window:
                            return True, "Submission rate limited (suppressed duplicate burst)"
                    else:
                        # Older than window; stop scanning
                        break
        except Exception:
            # Never block on rate limiter errors
            pass
        with open(SUBMISSIONS_FILE, "a", encoding="utf-8") as f:
            f.write(json.dumps(record, ensure_ascii=False) + "\n")
        return True, "Submission saved"
    except Exception as e:
        logger.error(f"forms_store: append_submission error: {e}")
        return False, str(e)


def append_decline(record: Dict[str, Any]) -> Tuple[bool, str]:
    """Persist a form decline event so Theo can audit opt-outs."""
    try:
        _ensure_dirs()
        payload: Dict[str, Any] = {}
        try:
            form_id = str(record.get("form_id", "")).strip()
        except Exception:
            form_id = ""
        if not form_id:
            return False, "form_id is required"
        payload["form_id"] = form_id

        try:
            room_id = record.get("room_id")
            if room_id is not None:
                payload["room_id"] = str(room_id)
        except Exception:
            pass

        try:
            ts = float(record.get("ts") or time.time())
        except Exception:
            ts = time.time()
        payload["ts"] = ts

        reason = record.get("reason")
        if isinstance(reason, str) and reason.strip():
            payload["reason"] = reason.strip()[:800]

        source_run_id = record.get("source_run_id")
        if isinstance(source_run_id, str) and source_run_id.strip():
            payload["source_run_id"] = source_run_id.strip()

        declined_by = record.get("declined_by")
        if isinstance(declined_by, str) and declined_by.strip():
            payload["declined_by"] = declined_by.strip()

        with open(DECLINES_FILE, "a", encoding="utf-8") as f:
            f.write(json.dumps(payload, ensure_ascii=False) + "\n")
        return True, "Decline saved"
    except Exception as e:
        logger.error(f"forms_store: append_decline error: {e}")
        return False, str(e)


def list_submissions(form_id: Optional[str] = None, limit: int = 50, after_ts: Optional[float] = None, room_id: Optional[str] = None) -> List[Dict[str, Any]]:
    results: List[Dict[str, Any]] = []
    try:
        if not SUBMISSIONS_FILE.exists():
            return []
        with open(SUBMISSIONS_FILE, "r", encoding="utf-8") as f:
            for line in f:
                line = line.strip()
                if not line:
                    continue
                try:
                    obj = json.loads(line)
                    if form_id and obj.get("form_id") != form_id:
                        continue
                    if room_id and obj.get("room_id") != room_id:
                        continue
                    if after_ts is not None:
                        try:
                            if float(obj.get("ts", 0.0)) <= float(after_ts):
                                continue
                        except Exception:
                            pass
                    results.append(obj)
                except Exception:
                    continue
        # Return most recent first
        results.sort(key=lambda x: x.get("ts", 0.0), reverse=True)
        return results[: max(1, int(limit))]
    except Exception as e:
        logger.error(f"forms_store: list_submissions error: {e}")
        return []


def enqueue_ui_event(event: Dict[str, Any]) -> Tuple[bool, str]:
    """Append a UI event (like show_form) to the UI events JSONL for SSE delivery."""
    try:
        _ensure_dirs()
        event = dict(event)
        event.setdefault("ts", time.time())

        # Lightweight de-duplication: if the last event is identical in action
        # and target (same form_id/room/run_id), skip writing a duplicate.
        try:
            last: Dict[str, Any] | None = None
            if UI_EVENTS_FILE.exists():
                with open(UI_EVENTS_FILE, "rb") as rf:
                    try:
                        rf.seek(0, 2)
                        size = rf.tell()
                        take = min(8192, size)
                        rf.seek(size - take)
                        tail = rf.read().decode("utf-8", errors="ignore")
                        lines = [ln.strip() for ln in tail.splitlines() if ln.strip()]
                        if lines:
                            last = json.loads(lines[-1]) if lines[-1] else None
                    except Exception:
                        last = None
            if last and isinstance(last, dict):
                a1 = str((event.get("action") or "")).strip()
                a2 = str((last.get("action") or "")).strip()
                if a1 == a2:
                    # Compare keys for known actions
                    if a1 == "show_form":
                        f1 = ((event.get("form") or {}).get("form_id")) or event.get("form_id")
                        f2 = ((last.get("form") or {}).get("form_id")) or last.get("form_id")
                        r1 = (event.get("room") or None)
                        r2 = (last.get("room") or None)
                        if (str(f1) == str(f2)) and (str(r1 or "") == str(r2 or "")):
                            return True, "Duplicate show_form suppressed"
                    elif a1 == "dismiss_form":
                        f1 = event.get("form_id")
                        f2 = last.get("form_id")
                        r1 = (event.get("room") or None)
                        r2 = (last.get("room") or None)
                        if (str(f1 or "") == str(f2 or "")) and (str(r1 or "") == str(r2 or "")):
                            return True, "Duplicate dismiss_form suppressed"
                    elif a1 == "start_run":
                        u1 = event.get("run_id")
                        u2 = last.get("run_id")
                        if u1 and (str(u1) == str(u2)):
                            return True, "Duplicate start_run suppressed"
        except Exception:
            pass

        with open(UI_EVENTS_FILE, "a", encoding="utf-8") as f:
            f.write(json.dumps(event, ensure_ascii=False) + "\n")
        return True, "UI event queued"
    except Exception as e:
        logger.error(f"forms_store: enqueue_ui_event error: {e}")
        return False, str(e)


def get_last_ui_event(room_id: Optional[str] = None) -> Optional[Dict[str, Any]]:
    """Return the most recent UI event, optionally filtering by room.

    If room_id is provided, returns the most recent event where event['room'] matches.
    """
    try:
        _ensure_dirs()
        if not UI_EVENTS_FILE.exists():
            return None
        with open(UI_EVENTS_FILE, "rb") as rf:
            rf.seek(0, 2)
            size = rf.tell()
            take = min(65536, size)
            rf.seek(size - take)
            tail = rf.read().decode("utf-8", errors="ignore")
            lines = [ln.strip() for ln in tail.splitlines() if ln.strip()]
            if not lines:
                return None
            # Scan from the end for a matching room if requested
            if room_id is not None:
                r = str(room_id)
                for ln in reversed(lines):
                    try:
                        obj = json.loads(ln)
                        if (obj.get("room") or None) == r:
                            return obj
                    except Exception:
                        continue
                return None
            else:
                try:
                    return json.loads(lines[-1])
                except Exception:
                    return None
    except Exception as e:
        logger.debug(f"forms_store: get_last_ui_event error: {e}")
        return None


def wait_for_submission(
    form_id: str,
    room_id: Optional[str] = None,
    after_ts: Optional[float] = None,
    timeout_s: float = 8.0,
    poll_interval_s: float = 0.2,
) -> Optional[Dict[str, Any]]:
    """Poll submissions.jsonl for a matching submission.

    Args:
        form_id: Target form id to wait for
        room_id: Optional room filter
        after_ts: Only consider submissions strictly after this timestamp
        timeout_s: Max seconds to wait
        poll_interval_s: Sleep between polls

    Returns:
        The first matching submission dict, or None if timed out.
    """
    try:
        _ensure_dirs()
        import time as _t
        deadline = _t.time() + max(0.0, float(timeout_s or 0.0))
        # Normalize filters
        fid = str(form_id)
        rid = str(room_id) if room_id else None
        thr = float(after_ts) if after_ts is not None else None
        # Simple in-place scanner that reads the file each poll (sizes expected small)
        while _t.time() <= deadline:
            try:
                if SUBMISSIONS_FILE.exists():
                    with open(SUBMISSIONS_FILE, "r", encoding="utf-8") as f:
                        for line in f:
                            line = line.strip()
                            if not line:
                                continue
                            try:
                                obj = json.loads(line)
                            except Exception:
                                continue
                            if obj.get("form_id") != fid:
                                continue
                            if rid is not None and obj.get("room_id") != rid:
                                continue
                            try:
                                ts_ok = True if thr is None else (float(obj.get("ts", 0.0)) > thr)
                            except Exception:
                                ts_ok = True
                            if ts_ok:
                                return obj
            except Exception:
                pass
            _t.sleep(max(0.05, float(poll_interval_s or 0.1)))
    except Exception as e:
        logger.debug(f"forms_store: wait_for_submission error: {e}")
    return None

