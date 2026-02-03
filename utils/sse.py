"""
SSE Utilities

Helpers to build consistent Server-Sent Events streams for endpoints that
poll filesystem state and emit updates. Consolidates repeated logic across
rooms/tasks/forms/system streams.
"""

from __future__ import annotations

from typing import Callable, AsyncGenerator, Optional
import asyncio
import json
import os
import time


def is_test_mode() -> bool:
    try:
        return (
            bool(os.environ.get("PYTEST_CURRENT_TEST"))
            or os.environ.get("THEO_TEST_MODE") == "1"
            or os.environ.get("THEO_DISABLE_BACKGROUND") == "1"
        )
    except Exception:
        return False


def sse_headers() -> dict:
    return {
        "Cache-Control": "no-cache, no-transform",
        "X-Accel-Buffering": "no",
        "Connection": "keep-alive",
    }


async def stream_generic(
    *,
    event_name: str,
    read_payload: Callable[[], dict],
    marker: Callable[[], Optional[float]],
    ping_name: str = "ping",
    ping_interval: float = 20.0,
    poll_normal: float = 0.8,
    poll_fast: float = 0.2,
) -> AsyncGenerator[bytes, None]:
    """Generic SSE stream based on a changing marker and a payload reader.

    - Sends one initial event on connect (event_name)
    - Polls `marker()` and emits when the value changes
    - Periodically emits a ping event
    - In test mode, stops after ~6 seconds
    """
    last_marker = None
    last_ping = time.time()
    deadline = time.time() + 6.0 if is_test_mode() else None

    # Initial payload
    try:
        init = json.dumps(read_payload())
    except Exception:
        init = json.dumps({})
    yield f"event: {event_name}\ndata: {init}\n\n".encode()

    try:
        while True:
            try:
                mk = marker()
            except Exception:
                mk = None
            # Emit when marker changes, even if it becomes None (e.g., file deleted)
            if mk != last_marker:
                try:
                    payload = json.dumps(read_payload())
                except Exception:
                    payload = json.dumps({})
                yield f"event: {event_name}\ndata: {payload}\n\n".encode()
                last_marker = mk

            # periodic ping
            now = time.time()
            if (now - last_ping) > ping_interval:
                try:
                    ping = json.dumps({"ts": int(now)})
                    yield f"event: {ping_name}\ndata: {ping}\n\n".encode()
                except Exception:
                    pass
                last_ping = now

            await asyncio.sleep(poll_fast if deadline else poll_normal)
            if deadline and time.time() > deadline:
                break
    except asyncio.CancelledError:
        return


def dir_latest_mtime(path: str, *, recursive: bool = False) -> Optional[float]:
    """Return latest mtime within a directory tree (or directory itself)."""
    try:
        if not os.path.isdir(path):
            return None
        latest = os.path.getmtime(path)
        if recursive:
            for root, _, files in os.walk(path):
                for fn in files:
                    try:
                        mt = os.path.getmtime(os.path.join(root, fn))
                        if mt > latest:
                            latest = mt
                    except Exception:
                        continue
        else:
            for fn in os.listdir(path):
                try:
                    mt = os.path.getmtime(os.path.join(path, fn))
                    if mt > latest:
                        latest = mt
                except Exception:
                    continue
        return latest
    except Exception:
        return None


def file_mtime(path: str) -> Optional[float]:
    try:
        return os.path.getmtime(path) if os.path.exists(path) else None
    except Exception:
        return None


__all__ = [
    "is_test_mode",
    "sse_headers",
    "stream_generic",
    "dir_latest_mtime",
    "file_mtime",
]
