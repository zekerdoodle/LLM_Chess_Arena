"""
Utilities for safe assembly of streaming text.

This module provides helpers to merge token/delta fragments from providers
that occasionally repeat parts of the previously emitted text. We prefer to
fix duplication at the source (provider SDKs), but a defensive de-dup here
keeps both the live stream and the persisted final output clean.

All helpers are pure functions to simplify testing.
"""

from __future__ import annotations

from typing import Tuple


def _find_near_tail_overlap(prev_tail: str, new_text: str, *, min_overlap: int = 16, tail_window: int = 400, gap_allowance: int = 16) -> int:
    """Return the length of the longest prefix of ``new_text`` that appears near the end
    of ``prev_tail`` (within ``gap_allowance`` chars of the end).

    This handles provider streams that resend a whole trailing sentence or line,
    where the overlap may end just before a few new characters (e.g., 'you w').

    Parameters:
    - prev_tail: the previous accumulated text tail (already trimmed to a window).
    - new_text: the incoming delta text from the stream.
    - min_overlap: minimum number of characters to consider a valid overlap.
    - tail_window: ignored here (trim before calling); kept for symmetry/documentation.
    - gap_allowance: how far from the very end the matched prefix may end to still
      count as overlap (guards against minor boundary artifacts).

    Returns the number of leading characters in ``new_text`` considered overlap.
    """
    if not prev_tail or not new_text:
        return 0
    # Fast bound for search
    limit = min(len(new_text), len(prev_tail), tail_window)
    if limit < min_overlap:
        return 0
    # Search longest-first to keep the most precise match
    for k in range(limit, min_overlap - 1, -1):
        frag = new_text[:k]
        idx = prev_tail.rfind(frag)
        if idx == -1:
            continue
        # The match should end close to the tail end (within gap_allowance)
        if (idx + k) >= (len(prev_tail) - gap_allowance):
            return k
    return 0


def dedupe_stream_delta(accumulated: str, delta: str, *, min_overlap: int = 16, tail_window: int = 400, gap_allowance: int = 16) -> Tuple[str, bool]:
    """Return a de-duplicated delta and whether anything was removed.

    - Looks for a significant prefix of ``delta`` that already appears near the end
      of ``accumulated`` and removes it.
    - Also handles the common suffix/prefix boundary overlap (classic case).

    Example:
    >>> prev = "... is working as expected.\n\nWant me to archive ... in case you w"
    >>> nxt =  "receive → echo) is working as expected.\n\nWant me to archive ... later?"
    >>> dedupe_stream_delta(prev, nxt)[0].startswith('later?')
    False

    Returns
    -------
    (new_delta, changed)
      new_delta: safe-to-append delta
      changed: True iff duplication was removed
    """
    if not delta:
        return delta, False
    acc = accumulated or ""
    # 1) Classic suffix/prefix overlap removal
    max_check = min(len(acc), len(delta), tail_window)
    for k in range(max_check, 0, -1):
        if acc.endswith(delta[:k]):
            return delta[k:], True
    # 2) Near-tail repeated prefix removal
    tail = acc[-tail_window:]
    k = _find_near_tail_overlap(tail, delta, min_overlap=min_overlap, tail_window=tail_window, gap_allowance=gap_allowance)
    if k > 0:
        return delta[k:], True
    return delta, False

