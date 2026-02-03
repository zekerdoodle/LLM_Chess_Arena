"""Presence-aware notification helpers."""

from __future__ import annotations

import time
from typing import Optional

from utils.logger import get_logger

logger = get_logger(__name__)


def emit_presence_notification(target_context: str, preview: str) -> None:
    """Emit unread indicators and push notifications when user is away."""
    try:
        from utils.active_room import get_active_payload

        payload = get_active_payload()
        active_context = payload.get("context")
        if not active_context:
            room = payload.get("room")
            if room:
                active_context = f"room:{room}"
        updated_at = payload.get("updated_at")
    except Exception as exc:
        logger.debug(f"presence.notify - active payload unavailable: {exc}")
        active_context = None
        updated_at = None

    if active_context == target_context:
        return

    recently_active = False
    try:
        if isinstance(updated_at, (int, float)):
            recently_active = (time.time() - float(updated_at)) <= 120
    except Exception:
        recently_active = False

    user_has_focus = False
    if active_context and active_context != "idle":
        user_has_focus = True
    elif not active_context and recently_active:
        # No explicit context but recent activity on a room
        user_has_focus = True

    try:
        from utils.forms_store import enqueue_ui_event

        enqueue_ui_event(
            {
                "action": "notification.badge",
                "target": target_context,
                "preview": preview[:240],
            }
        )
    except Exception as exc:
        logger.debug(f"presence.notify - UI badge emit skipped: {exc}")

    if user_has_focus:
        return

    try:
        from utils import webpush as webpush_util

        if hasattr(webpush_util, "send_to_all"):
            body = preview.strip()
            if len(body) > 240:
                body = body[:240] + "…"
            url = "./"
            if target_context.startswith("room:"):
                url = f"/?room={target_context.split(':', 1)[1]}"
            elif target_context == "tasks_inbox":
                url = "/?tasks=inbox"
            webpush_util.send_to_all(
                {
                    "title": "Theo",
                    "body": body or "New update",
                    "url": url,
                }
            )
    except Exception as exc:
        logger.debug(f"presence.notify - Push skipped: {exc}")


__all__ = ["emit_presence_notification"]
