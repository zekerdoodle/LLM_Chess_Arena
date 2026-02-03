"""
Attachment helpers for normalizing, saving, and sanitizing attachment records used in
chat history, uploads, and tool/web message outputs.

Centralizes:
- Unique filename generation
- Saving uploads under vault/uploads/{room}/
"""

from __future__ import annotations

import os
from typing import Any, Dict, List, Optional, Tuple

try:  # FastAPI optional import for type hints only
    from fastapi import UploadFile  # type: ignore
except Exception:  # pragma: no cover
    UploadFile = Any  # type: ignore

from utils.vault_paths import get_uploads_root


def normalize_attachments(items: Any) -> List[Dict[str, Any]]:
    """Normalize a list of attachment-like objects/dicts to a standard shape.

    Output keys:
    - filename: str
    - url: str
    - content_type: str
    - size: int
    """
    out: List[Dict[str, Any]] = []
    try:
        for a in (items or []):
            try:
                if isinstance(a, dict):
                    filename = str(a.get("filename", ""))
                    url = str(a.get("url", ""))
                    ctype = str(a.get("content_type", ""))
                    try:
                        size = int(a.get("size", 0) or 0)
                    except Exception:
                        size = 0
                else:
                    # Object with attributes
                    filename = str(getattr(a, "filename", ""))
                    url = str(getattr(a, "url", ""))
                    ctype = str(getattr(a, "content_type", ""))
                    try:
                        size = int(getattr(a, "size", 0) or 0)
                    except Exception:
                        size = 0
                out.append({
                    "filename": filename,
                    "url": url,
                    "content_type": ctype,
                    "size": size,
                })
            except Exception:
                continue
    except Exception:
        return []
    return out


def _ensure_uploads_dir(room_id: str) -> str:
    try:
        path = get_uploads_root(room_id)
        return str(path)
    except Exception:
        root = os.path.join("vault", "uploads", str(room_id))
        os.makedirs(root, exist_ok=True)
        return root


def ensure_unique_filename(save_dir: str, base: str) -> Tuple[str, str]:
    """Return (dest_path, unique_name) ensuring no clobber by suffixing _N when needed."""
    unique = base
    i = 1
    dest = os.path.join(save_dir, unique)
    while os.path.exists(dest):
        name, ext = os.path.splitext(base)
        unique = f"{name}_{i}{ext}"
        dest = os.path.join(save_dir, unique)
        i += 1
    return dest, unique


async def save_uploaded_files(room_id: str, uploads: List[UploadFile]) -> List[Dict[str, Any]]:
    """Save a list of UploadFile objects to vault/uploads/{room} and return attachment descriptors.

    Ensures unique names.
    """
    save_dir = _ensure_uploads_dir(room_id)
    out: List[Dict[str, Any]] = []
    for f in uploads or []:
        try:
            ct = getattr(f, "content_type", "") or ""
            base = os.path.basename(getattr(f, "filename", ""))
            dest, unique = ensure_unique_filename(save_dir, base)
            try:
                data = await f.read()  # type: ignore[attr-defined]
            except Exception:
                data = b""
            with open(dest, "wb") as wf:
                wf.write(data)
            out.append({
                "filename": base,
                "url": f"/uploads/{room_id}/{unique}",
                "size": len(data),
                "content_type": ct,
            })
        except Exception:
            continue
    return out


def save_bytes_attachment(room_id: str, filename: str, data: bytes, mime_type: Optional[str] = None) -> Dict[str, Any]:
    """Save raw bytes as an attachment into uploads/{room} with a unique name.

    Returns a normalized attachment descriptor.
    """
    save_dir = _ensure_uploads_dir(room_id)
    base = os.path.basename(filename or "file.bin")
    dest, unique = ensure_unique_filename(save_dir, base)
    with open(dest, "wb") as wf:
        wf.write(data or b"")
    return {
        "filename": base,
        "url": f"/uploads/{room_id}/{unique}",
        "size": len(data or b""),
        "content_type": mime_type or "",
    }


__all__ = [
    "normalize_attachments",
    "ensure_unique_filename",
    "save_uploaded_files",
    "save_bytes_attachment",
]
