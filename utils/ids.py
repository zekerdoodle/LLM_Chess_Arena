"""
ULID utilities.

Provides a minimal dependency-free ULID generator suitable for ordering and
uniqueness across processes. ULIDs are 26-char Crockford base32 strings with
48-bit timestamp (ms) + 80-bit randomness.

Note: This is not a cryptographic RNG; it uses os.urandom for the random part.
"""

from __future__ import annotations

import os
import time
from typing import Optional

_CROCKFORD = "0123456789ABCDEFGHJKMNPQRSTVWXYZ"


def _to_base32(data: bytes) -> str:
    """Encode bytes to Crockford base32 without padding."""
    bits = 0
    bit_buffer = 0
    out = []
    for b in data:
        bit_buffer = (bit_buffer << 8) | b
        bits += 8
        while bits >= 5:
            bits -= 5
            out.append(_CROCKFORD[(bit_buffer >> bits) & 0x1F])
    if bits:
        out.append(_CROCKFORD[(bit_buffer << (5 - bits)) & 0x1F])
    return "".join(out)


def ulid(ts_ms: Optional[int] = None) -> str:
    """Generate a 26-char ULID string.

    Args:
        ts_ms: Optional millisecond timestamp; defaults to current time.
    """
    if ts_ms is None:
        ts_ms = int(time.time() * 1000)
    # 48-bit timestamp
    ts = ts_ms & ((1 << 48) - 1)
    ts_bytes = ts.to_bytes(6, 'big')
    # 80-bit randomness
    rnd = os.urandom(10)
    enc = _to_base32(ts_bytes + rnd)
    # Crockford base32 encoding of 16 bytes yields 26 chars
    return enc[:26]


__all__ = ["ulid"]

