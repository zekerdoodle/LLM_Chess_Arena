"""
Image Analysis Tools for Layer 4

This file provides the analyze_image tool that enables Theo to understand visual content.
It calls OpenAI's vision-capable models (configured via image_analysis_model in config.yaml)
to analyze images and generate detailed text descriptions.

Key features:
- Automatic analysis when users upload images (triggered via prompt instructions)
- Manual analysis via tool call for generated or vault images
- Saves descriptions as {image_name}_description.md for indexing and future reference
- Returns analysis to Theo only; never sends messages to user (they can already see the image!)
- Sandboxed to vault directory for security

Integration points:
- Registered in utils/tool_executor.py (TOOL_REGISTRY)
- Schema defined in utils/tool_schemas.py
- Auto-trigger on upload in server/routers/assets.py
- Config validation in utils/config_loader.py ensures OpenAI model is used
"""

import base64
import mimetypes
from pathlib import Path
from typing import Any, Dict, Optional, Tuple
import time

from openai import OpenAI
import requests
from io import BytesIO
from PIL import Image

from utils.logger import get_logger
from utils.config_loader import load_config

logger = get_logger(__name__)


def _load_openai_client() -> Tuple[OpenAI, Dict[str, Any]]:
    cfg = load_config()
    api_keys = cfg.get("api_keys", {})
    key = api_keys.get("openai")
    if not key:
        raise ValueError("OpenAI API key not found in configuration")
    client = OpenAI(api_key=key)
    return client, cfg


def _path_to_data_url(path: Path) -> str:
    try:
        mime, _ = mimetypes.guess_type(str(path))
        if not mime:
            # Default to PNG if unknown
            mime = "image/png"
        with open(path, "rb") as f:
            b64 = base64.b64encode(f.read()).decode("utf-8")
        return f"data:{mime};base64,{b64}"
    except Exception as e:
        raise RuntimeError(f"Failed to read image: {e}")


def _save_markdown_beside_image(image_path: Path, prompt: str, description: str) -> Optional[str]:
    try:
        ts = time.strftime("%Y-%m-%d %H:%M:%S")
        # Strip extension and append _description.md per specs
        base_name = image_path.stem  # filename without extension
        md_name = f"{base_name}_description.md"
        md_path = image_path.parent / md_name
        content = (
            f"# Image Analysis\n\n"
            f"- File: {image_path.name}\n"
            f"- Analyzed: {ts}\n"
            f"- Prompt: {prompt}\n\n"
            f"## Description\n\n{description}\n"
        )
        md_path.write_text(content, encoding="utf-8")
        return str(md_path)
    except Exception as e:
        logger.warning(f"L4.image_analysis - Failed to save markdown: {e}")
        return None


def _resolve_image_path(image: str, vault_root: Path) -> Optional[Path]:
    """Resolve an image path to an absolute path within the vault.
    
    Handles various input formats:
    - Absolute paths: /home/debian/Projects/Theo/vault/uploads/...
    - Vault-relative: vault/uploads/... or uploads/...
    - URL-style: /uploads/room_id/file.png
    - Filename only: searches within vault
    
    Args:
        image: Image path string in any supported format
        vault_root: Resolved vault root directory path
        
    Returns:
        Resolved Path object if found within vault, None otherwise
    """
    image_str = str(image).strip()
    
    # Handle URL-style paths from attachments (e.g., "/uploads/room_id/file.png")
    if image_str.startswith("/uploads/"):
        parts = image_str.strip("/").split("/")
        if len(parts) >= 2:
            # Reconstruct: vault_root / "uploads" / room_id / filename
            path = vault_root / "/".join(parts)
        else:
            path = Path(image_str)
    # Handle generated images path format
    elif image_str.startswith("/vault/generated_images/"):
        # Strip leading slash and join with vault root
        path = vault_root / image_str.lstrip("/").replace("/vault/", "", 1)
    else:
        path = Path(image_str)
    
    # Convert relative paths to absolute (relative to vault)
    if not path.is_absolute():
        path = vault_root / path
    
    try:
        resolved = path.resolve()
    except Exception:
        resolved = path
    
    # If the candidate is not within vault, try to locate by filename inside the vault
    if not str(resolved).startswith(str(vault_root)):
        # Best-effort filename search within vault
        filename = path.name
        
        # Try common locations first (faster than recursive search)
        common_dirs = ["uploads", "generated_images", ""]
        for subdir in common_dirs:
            search_path = vault_root / subdir if subdir else vault_root
            candidates = list(search_path.glob(filename))
            if candidates:
                # Prefer the most recently modified
                try:
                    resolved = max(candidates, key=lambda p: p.stat().st_mtime)
                    logger.debug(f"L4.image_analysis - Found image by filename: {resolved}")
                    break
                except Exception:
                    resolved = candidates[0]
                    break
        
        # Fall back to recursive search if not found
        if not str(resolved).startswith(str(vault_root)):
            try:
                candidates = list(vault_root.rglob(filename))
                if candidates:
                    try:
                        resolved = max(candidates, key=lambda p: p.stat().st_mtime)
                        logger.debug(f"L4.image_analysis - Found image by recursive search: {resolved}")
                    except Exception:
                        resolved = candidates[0]
            except Exception:
                pass
    
    # Final validation: must be within vault and must exist
    if not str(resolved).startswith(str(vault_root)):
        return None
    if not resolved.exists():
        return None
    
    return resolved


def analyze_image(
    image: str,
    prompt: Optional[str] = None,
    detail: str = "auto",
    save_markdown: bool = True,
    dont_send: bool = False,
) -> Tuple[str, Dict[str, Any]]:
    """
    Analyze an image with OpenAI's vision-capable model and return a detailed description.

    Args:
        image: Local file path or fully-qualified URL or data URL
               Supports various formats:
               - Absolute: /home/debian/Projects/Theo/vault/uploads/room/file.png
               - Vault-relative: vault/uploads/room/file.png or uploads/room/file.png
               - URL-style: /uploads/room/file.png
               - Filename only: file.png (searches vault)
        prompt: Optional guidance/question (defaults to detailed captioning for memory/search)
        detail: one of 'low'|'high'|'auto' (model decides)
        save_markdown: Save a sidecar .md next to the image for indexing
        dont_send: Deprecated - no longer used (we never send messages to user)

    Returns:
        Tuple of (result_text, tool_output)
        - result_text: Full detailed analysis for Theo's internal reasoning
        - tool_output: Contains success status, analysis_text, and metadata
    """
    start = time.time()
    if not image or not str(image).strip():
        return ("Error: 'image' is required", {"success": False, "error": "missing image"})

    user_prompt = prompt or (
        "Provide a concise but detailed description of this image, including key objects, text, and context."
        " Include bullet points for notable details."
    )

    try:
        client, cfg = _load_openai_client()
        model = cfg.get("image_analysis_model", "gpt-4.1-mini")

        # Build image input (URL or data URL)
        image_url: str
        resolved: Optional[Path] = None  # Track resolved filesystem path for markdown saving
        if image.startswith("data:"):
            image_url = image
        elif image.startswith("http://") or image.startswith("https://"):
            # Restrict analysis to local vault images or data URLs only
            return (
                "Error: External image URLs are not allowed. Please upload the image or use a generated image path.",
                {"success": False, "error": "external_url_disallowed"}
            )
        else:
            # Resolve relative to vault root (not project root) to honor sandboxing
            proj_root = Path(__file__).parent.parent
            try:
                from utils.vault_paths import get_vault_root
                vault_root = Path(get_vault_root()).resolve()
            except Exception:
                # Fall back to the canonical project-local vault directory
                vault_root = (proj_root / "vault").resolve()

            # Use the enhanced path resolver
            resolved = _resolve_image_path(image, vault_root)
            
            if resolved is None:
                # Generate helpful error message listing potential locations
                try:
                    recent_images = []
                    for subdir in ["uploads", "generated_images"]:
                        try:
                            search_dir = vault_root / subdir
                            if search_dir.exists():
                                for img_path in sorted(search_dir.rglob("*.png"), key=lambda p: p.stat().st_mtime, reverse=True)[:3]:
                                    rel_path = img_path.relative_to(vault_root)
                                    recent_images.append(str(rel_path))
                        except Exception:
                            pass
                    
                    error_msg = f"Error: Image not found in vault: {image}\n\nTip: Use paths like:\n"
                    if recent_images:
                        error_msg += "\n".join(f"  - {p}" for p in recent_images[:5])
                    else:
                        error_msg += "  - uploads/room_id/filename.png\n  - generated_images/filename.png"
                    
                    return (
                        error_msg,
                        {"success": False, "error": "file not found", "image": image, "recent_images": recent_images[:5]}
                    )
                except Exception:
                    return (
                        f"Error: Image not found in vault: {image}",
                        {"success": False, "error": "file not found", "image": image}
                    )
            
            image_url = _path_to_data_url(resolved)

        # Compose multimodal input for Responses API
        content = [
            {"type": "input_text", "text": user_prompt},
            {"type": "input_image", "image_url": image_url, "detail": detail},
        ]

        logger.info(f"L4.image_analysis [tool:analyze_image] - Calling {model} (detail={detail})")

        resp = client.responses.create(
            model=model,
            input=[{"role": "user", "content": content}],
        )

        text = getattr(resp, "output_text", None) or str(resp)
        elapsed = round(time.time() - start, 2)

        # Optionally save markdown sidecar for indexing
        analysis_md = None
        if save_markdown and resolved is not None:
            try:
                # Use the already-resolved path (computed above during image loading)
                # which is guaranteed to be a valid filesystem path within the vault
                analysis_md = _save_markdown_beside_image(resolved, user_prompt, text)
            except Exception as e:
                logger.warning(f"L4.image_analysis - Failed to save markdown: {e}")

        # Prepare result - full analysis for Theo's internal use
        result_text = (
            f"🖼️ Image Analysis Completed\n\n"
            f"Model: {model} | Detail: {detail} | Time: {elapsed}s\n\n"
            f"{text}"
        )

        tool_output: Dict[str, Any] = {
            "success": True,
            "model": model,
            "detail": detail,
            "analysis_text": text,
            "analysis_markdown": analysis_md,
            "elapsed": elapsed,
        }

        # Note: We never send a message to the user - they can already see the image!
        # The detailed analysis in result_text is for Theo's internal use only.
        # Theo's response to the user's message is confirmation enough.

        return result_text, tool_output

    except Exception as e:
        logger.error(f"L4.image_analysis [tool:analyze_image] - Error: {e}", exc_info=True)
        return (
            f"Error analyzing image: {e}",
            {"success": False, "error": str(e)}
        )


__all__ = ["analyze_image"]
