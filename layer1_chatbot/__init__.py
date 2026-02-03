"""
Layer 1 - ChatBot package.

Exports base interfaces without importing provider SDKs at import time to avoid
hard runtime dependencies during test discovery. Provider modules should be
imported lazily by callers (see ModelSelector).
"""

from .base_model import BaseModelCall, ModelResponseError

__all__ = [
    "BaseModelCall",
    "ModelResponseError",
]
