"""
Layer 4: Tool Orchestrator - Tool Processing Loop

This module handles the complex tool orchestration loop where Theo "thinks" and
executes multiple tool calls sequentially or in parallel until a final response is ready.
"""

import asyncio
import base64
import json
import logging
import time
import uuid as _uuid
from collections.abc import Mapping, Sequence
from typing import Any, Dict, List, Optional, Callable, Awaitable, Tuple, Union
from types import SimpleNamespace

# Web-only operation; Discord-specific integrations were removed
try:  # pragma: no cover - optional dependency in tests
    from layer1_chatbot.model_call_google import GoogleResponseError
except ImportError:  # Fallback to keep unit tests running without google-genai
    class GoogleResponseError(Exception):
        """Fallback Google error used when optional dependencies are absent."""

from utils.logger import get_logger, log_tool_result
from utils.token_counter import count_tokens
from utils.tool_executor import execute_tool_call
from utils.tool_schemas import generate_tool_schemas
from utils.dynamic_optimizer import get_reasoning_kwargs
from utils.circuit_breaker import get_circuit_breaker
from utils.prompt_labels import CURRENT_MESSAGE_HEADER, WORKING_MEMORY_HEADER
import os
from layer5_features.interruptions import start_processing, end_processing
from pathlib import Path




# Lazy imports inside functions to avoid heavy deps at import time

logger = get_logger(__name__)

# Tools exempt from hard-disable deduplication (cheap and repeat-prone)
NON_DISABLE_TOOLS: set[str] = {
    "url_retrieval",
    "page_parser",
    "web_search",
    "generate_image",  # Allow repeated image variations without suppression
}

# Tools that intentionally allow identical requests within a turn (e.g., variations)
MULTI_RUN_TOOLS: set[str] = {"generate_image"}

# Apply tighter duplicate suppression for tools that previously caused loops
STRICT_DUPLICATE_THRESHOLDS: dict[str, int] = {
    "get_connection_status": 2,
    "connect_bank_account": 2,
}


def _apply_reasoning_and_tool_overrides(
    provider: str,
    model_kwargs: Optional[Dict[str, Any]],
    tool_schemas: List[Dict[str, Any]],
    optimization_result: Optional[Dict[str, Any]],
) -> Tuple[Dict[str, Any], List[Dict[str, Any]]]:
    """Apply provider-specific overrides for reasoning effort and tool access.

    For xAI (Grok) models we ignore the dynamic optimizer's reasoning_effort and
    always instruct the caller to use maximum reasoning while keeping tools
    active. All reasoning_effort levels now support tools (including minimal,
    since GPT-5-Chat now supports tool use).
    """

    adjusted_kwargs: Dict[str, Any] = dict(model_kwargs or {})
    adjusted_tools = tool_schemas

    effort = None
    if isinstance(optimization_result, dict):
        effort = str(optimization_result.get("reasoning_effort") or "").lower()

    if provider == "xai":
        reasoning_block = adjusted_kwargs.get("reasoning")
        if isinstance(reasoning_block, dict):
            reasoning_block["effort"] = "high"
        else:
            adjusted_kwargs["reasoning"] = {"effort": "high"}
        return adjusted_kwargs, adjusted_tools

    # All reasoning efforts now support tools (GPT-5-Chat supports tools as of Oct 2024)
    # No need to disable tools for minimal reasoning

    return adjusted_kwargs, adjusted_tools


def _should_skip_signature(tool_name: str, signature: tuple[str, str], disabled: set[tuple[str, str]]) -> bool:
    """Return True if this exact tool+args signature should be skipped.

    Research tools are exempt from hard-disable to prevent stalls.
    """
    if tool_name in NON_DISABLE_TOOLS:
        return False
    return signature in disabled


def _maybe_disable_signature(tool_name: str, signature: tuple[str, str], disabled: set[tuple[str, str]]) -> None:
    """Disable a signature unless exempt. No-op for NON_DISABLE_TOOLS."""
    if tool_name in NON_DISABLE_TOOLS:
        return
    disabled.add(signature)


class ToolOrchestrationError(Exception):
    """Custom exception for tool orchestration errors."""
    pass


class ToolExecutionError(Exception):
    """Custom exception for individual tool execution errors."""
    def __init__(self, tool_name: str, error: str, context: str = ""):
        self.tool_name = tool_name
        self.error = error
        self.context = context
        super().__init__(f"Tool '{tool_name}' failed: {error}")


async def _await_protected(coro: Awaitable[Any]) -> Any:
    """Run an awaitable under asyncio.shield so it finishes even if cancelled."""

    task = asyncio.create_task(coro)
    try:
        return await asyncio.shield(task)
    except asyncio.CancelledError as exc:
        # If we get cancelled, try to wait for the task to complete naturally
        # but don't re-raise the CancelledError if the task completes successfully
        try:
            if not task.done():
                # Task is still running, wait for it to complete
                return await task
            else:
                # Task is already done, get its result
                return task.result()
        except asyncio.CancelledError:
            # Task was also cancelled, re-raise the original cancellation
            raise exc
        except Exception as task_exc:
            # Task completed with an error, surface that error instead
            raise task_exc


def _resolve_room_from_message(message: Any) -> str:
    room = getattr(message, "room_id", None)
    if isinstance(room, str) and room.strip():
        return room.strip()
    try:
        channel = getattr(message, "channel", None)
        room = getattr(channel, "id", None)
        if isinstance(room, str) and room.strip():
            return room.strip()
    except Exception:
        pass
    return "web"


def _persist_inline_image_fallback(
    room: str,
    markdown: str,
    rel_url: str,
    upload_path: Optional[str],
    metadata_lines: List[str],
    run_id: Optional[str],
) -> None:
    """Best-effort persistence when async helpers are cancelled mid-flight."""

    try:
        from utils.vault_paths import get_vault_root
    except Exception:
        def get_vault_root():  # type: ignore
            return Path("vault")

    chats_dir = Path(get_vault_root()) / "chats"
    chats_dir.mkdir(parents=True, exist_ok=True)
    chat_path = chats_dir / f"{room}.json"

    try:
        if chat_path.exists():
            with open(chat_path, "r", encoding="utf-8") as fh:
                msgs = json.load(fh)
        else:
            msgs = []
    except Exception:
        msgs = []

    timestamp = time.time()
    message_id = _uuid.uuid4().hex
    size = 0
    filename = rel_url.strip().split("/")[-1] if rel_url else "generated.png"
    try:
        if upload_path and os.path.exists(upload_path):
            size = os.path.getsize(upload_path)
    except Exception:
        size = 0

    entry = {
        "role": "assistant",
        "content": markdown.strip() or "(generated image)",
        "message_id": message_id,
        "timestamp": timestamp,
        "reactions": {},
        "attachments": [
            {
                "filename": filename,
                "url": rel_url,
                "size": int(size) if size else 0,
                "content_type": "image/png",
            }
        ],
    }
    if run_id:
        entry["run_id"] = str(run_id)
    msgs.append(entry)

    if metadata_lines:
        msgs.append(
            {
                "role": "system",
                "content": "\n".join(metadata_lines),
                "message_id": f"{message_id}-meta",
                "timestamp": timestamp,
                "hidden": True,
                "reactions": {},
                **({"run_id": str(run_id)} if run_id else {}),
            }
        )

    try:
        with open(chat_path, "w", encoding="utf-8") as fh:
            json.dump(msgs, fh, indent=2, ensure_ascii=False)
    except Exception:
        pass

    try:
        from utils.active_room import set_active_room

        set_active_room(room)
    except Exception:
        pass


async def _auto_send_image_to_room(
    tool_data: Dict[str, Any],
    result_payload: Dict[str, Any],
    message: Any,
    emit_cb: Callable[[Dict[str, Any]], Awaitable[None]],
) -> None:
    """Handle generate_image auto-send with cancellation resilience."""

    room = _resolve_room_from_message(message)
    run_id = getattr(message, "id", None)
    image_b64 = tool_data.get("image_data")
    image_bytes: Optional[bytes] = None
    if isinstance(image_b64, str) and image_b64.strip():
        try:
            image_bytes = base64.b64decode(image_b64, validate=False)
        except Exception:
            image_bytes = None
    if image_bytes is None and isinstance(tool_data.get("image_path"), str):
        src_path = tool_data.get("image_path")
        if src_path and os.path.exists(src_path):
            try:
                with open(src_path, "rb") as fh:
                    image_bytes = fh.read()
            except Exception:
                image_bytes = None
    if not image_bytes:
        logger.debug("L4.tools [web] - No image bytes available for auto-send; skipping")
        return

    try:
        from utils.attachments import save_bytes_attachment
        from utils.vault_paths import get_uploads_root
    except Exception as exc:
        logger.error(f"L4.tools [web] - Missing attachment utilities for image auto-send: {exc}")
        return

    attachment = save_bytes_attachment(room, filename="generated.png", data=image_bytes, mime_type="image/png")
    rel_url = attachment.get("url")
    uploads_root = Path(get_uploads_root(room))
    upload_path = uploads_root / Path(rel_url or "").name

    if not isinstance(result_payload, dict):
        result_payload = {}

    safe_alt = "Generated image"

    caption_source = ""
    output_text = result_payload.get("output") if isinstance(result_payload, dict) else None
    if isinstance(output_text, str) and output_text.strip():
        caption_source = output_text.strip()
    elif isinstance(tool_data.get("status"), str) and tool_data["status"].strip():
        caption_source = tool_data["status"].strip()
    if not caption_source:
        caption_source = "Image generation successful."
    if len(caption_source) > 600:
        caption_source = caption_source[:597].rstrip() + "…"

    if rel_url:
        markdown = ""
    else:
        markdown = "(generated image)"

    tool_data.setdefault("status", caption_source)

    metadata_lines: List[str] = ["[image_generated]"]
    if rel_url:
        metadata_lines.append(f"url: {rel_url}")
    for key in ("width", "height", "steps", "cfg_scale", "seed", "generation_time"):
        value = tool_data.get(key)
        if value is None:
            value = result_payload.get(key)
        if value is None:
            continue
        if key == "generation_time":
            try:
                metadata_lines.append(f"generation_time_s: {float(value):.2f}")
            except Exception:
                metadata_lines.append(f"generation_time_s: {value}")
        elif key == "cfg_scale":
            metadata_lines.append(f"cfg_scale: {value}")
        elif key == "seed":
            metadata_lines.append(f"seed: {value}")
        elif key in {"width", "height"}:
            continue

    if tool_data.get("width") or result_payload.get("width"):
        w = tool_data.get("width") or result_payload.get("width")
        h = tool_data.get("height") or result_payload.get("height")
        if w and h:
            metadata_lines.append(f"dimensions: {w}x{h}")

    cancelled = False

    # Append hidden metadata best-effort
    try:
        from layer4_tools.web_tools import append_hidden_message

        await _await_protected(
            append_hidden_message(
                "\n".join(metadata_lines),
                channel_id=room,
                role="system",
                run_id=str(run_id) if run_id else None,
            )
        )
    except asyncio.CancelledError:
        cancelled = True
    except Exception as exc:
        logger.debug(f"L4.tools [web] - Failed to append hidden metadata: {exc}")

    # Persist assistant message; fall back to synchronous path if cancellation prevents completion
    persisted = False
    try:
        from layer4_tools.web_tools import manually_send_message

        await _await_protected(
            manually_send_message(
                markdown,
                channel_id=room,
                run_id=str(run_id) if run_id else None,
                display_inline=True,
                prebuilt_attachments=[attachment] if attachment else None,
            )
        )
        persisted = True
    except asyncio.CancelledError:
        cancelled = True
    except Exception as exc:
        logger.debug(f"L4.tools [web] - manually_send_message failed for auto image: {exc}")

    if not persisted:
        _persist_inline_image_fallback(room, markdown, rel_url or "", str(upload_path), metadata_lines, str(run_id) if run_id else None)

    tool_data["image_url"] = rel_url
    try:
        tool_data["upload_path"] = str(upload_path)
    except Exception:
        pass

    if cancelled:
        raise asyncio.CancelledError


def _queue_pending_image_autosend(
    pending: List[Tuple[Dict[str, Any], Dict[str, Any]]],
    tool_data: Optional[Dict[str, Any]],
    result_payload: Dict[str, Any],
) -> bool:
    """Queue a send_image action for later flushing, avoiding duplicates."""

    if not isinstance(tool_data, dict):
        return False
    if tool_data.get("action") != "send_image":
        return False

    for existing_data, _ in pending:
        if existing_data is tool_data:
            return True

    pending.append((tool_data, result_payload))
    return True


async def _flush_pending_image_autosends(
    pending: List[Tuple[Dict[str, Any], Dict[str, Any]]],
    message: Any,
    emit_cb: Callable[[Dict[str, Any]], Awaitable[None]],
) -> int:
    """Send pending auto-send image actions sequentially.

    Returns:
        Number of images successfully delivered.
    """

    sent = 0
    while pending:
        tool_data, result_payload = pending.pop(0)
        try:
            await _auto_send_image_to_room(tool_data, result_payload, message, emit_cb)
            sent += 1
        except asyncio.CancelledError:
            pending.insert(0, (tool_data, result_payload))
            raise
        except Exception as auto_err:
            logger.debug(f"L4.tools [web] - Deferred send_image failed: {auto_err}")
    return sent





def _extract_image_summary_record(
    tool_data: Optional[Dict[str, Any]],
    result_payload: Dict[str, Any],
) -> Optional[Dict[str, Any]]:
    """Collect metadata for a generated image so we can summarize later."""

    if not isinstance(tool_data, dict):
        return None

    def _first_non_none(*values):
        for value in values:
            if value is not None:
                return value
        return None

    record: Dict[str, Any] = {}
    for key in ("prompt", "negative_prompt", "image_url", "image_path", "image_name"):
        if key in tool_data and tool_data.get(key) not in (None, ""):
            record[key] = tool_data.get(key)
    if isinstance(result_payload, dict):
        for key in ("image_url", "image_path", "image_name"):
            if key not in record and result_payload.get(key) not in (None, ""):
                record[key] = result_payload.get(key)

    for key in ("width", "height", "steps", "seed"):
        value = _first_non_none(
            tool_data.get(key),
            result_payload.get(key) if isinstance(result_payload, dict) else None,
        )
        if value is not None:
            try:
                record[key] = int(value)
            except Exception:
                record[key] = value

    cfg_value = _first_non_none(
        tool_data.get("cfg_scale"),
        result_payload.get("cfg_scale") if isinstance(result_payload, dict) else None,
    )
    if cfg_value is not None:
        try:
            record["cfg_scale"] = float(cfg_value)
        except Exception:
            record["cfg_scale"] = cfg_value

    if not record:
        return None
    return record


def _truncate_for_metadata(value: Optional[str], limit: int = 160) -> str:
    if not value:
        return ""
    value = str(value).strip()
    if len(value) <= limit:
        return value
    return value[: max(1, limit - 1)] + "…"


def _build_image_metadata_message(records: List[Dict[str, Any]]) -> Optional[str]:
    if not records:
        return None
    lines = ["System: Image batch metadata:"]
    for idx, rec in enumerate(records, 1):
        width = rec.get("width")
        height = rec.get("height")
        size = f"{width}x{height}" if width and height else "unknown"
        steps = rec.get("steps")
        cfg = rec.get("cfg_scale")
        seed = rec.get("seed")
        seed_text = str(seed) if seed not in (None, "", -1) else "random"
        headline = (
            f"{idx}. Seed {seed_text} · {size} · "
            f"{steps if steps is not None else '?'} steps · CFG {cfg if cfg is not None else '?'}"
        )
        lines.append(headline)
        prompt_snippet = _truncate_for_metadata(rec.get("prompt"))
        if prompt_snippet:
            lines.append(f"   Prompt: {prompt_snippet}")
        neg_snippet = _truncate_for_metadata(rec.get("negative_prompt"), 140)
        if neg_snippet:
            lines.append(f"   Negative: {neg_snippet}")
    lines.append("Summarize these settings for the user and invite any follow-up tweaks.")
    return "\n".join(lines)


def _format_image_summary(records: List[Dict[str, Any]]) -> str:
    if not records:
        return "I generated the requested images. Let me know if you'd like any refinements."
    lines: List[str] = []
    lines.append(f"Here’s the batch: {len(records)} image(s) rendered with InvokeAI.")
    for idx, rec in enumerate(records, 1):
        width = rec.get("width")
        height = rec.get("height")
        size = f"{width}x{height}" if width and height else "unknown size"
        steps = rec.get("steps")
        cfg = rec.get("cfg_scale")
        seed = rec.get("seed")
        seed_text = str(seed) if seed not in (None, "", -1) else "random"
        prompt_snippet = _truncate_for_metadata(rec.get("prompt"))
        neg_snippet = _truncate_for_metadata(rec.get("negative_prompt"), 140)
        detail = (
            f"{idx}. Seed {seed_text} · {size} · "
            f"{steps if steps is not None else '?'} steps · CFG {cfg if cfg is not None else '?'}"
        )
        lines.append(detail)
        if prompt_snippet:
            lines.append(f"   Prompt: {prompt_snippet}")
        if neg_snippet:
            lines.append(f"   Negative: {neg_snippet}")
    lines.append(
        "Everything’s attached above—happy to iterate on poses, lighting, outfits, or anything else you’d like to explore."
    )
    return "\n".join(lines)


async def _stream_model_with_circuit_breaker(
    model_selector,
    attempt_prompt: Any,
    model: str,
    config: Dict[str, Any],
    tool_schemas: List[Dict[str, Any]] | None,
    model_kwargs: Dict[str, Any] | None,
    on_event: Optional[Callable[[Dict[str, Any]], Awaitable[None]]] = None,
) -> str:
    """Stream final assistant output without tools, emitting output.delta events.

    Notes:
    - Does not disrupt tool calls because it is used only for the final, no‑tool turn.
    - Provider-aware streaming for OpenAI (Responses API) and Groq (Chat Completions).
    - Falls back to a single non-streaming call if streaming is not supported or fails.
    """
    full_text: List[str] = []
    provider = None
    try:
        provider = model_selector.get_provider_from_model(model)
    except Exception:
        provider = "openai"

    async def emit_payload(payload: Union[str, Dict[str, Any]]):
        if isinstance(payload, str):
            if not payload:
                return
            full_text.append(payload)
            if on_event:
                try:
                    await on_event({"type": "output.delta", "text": payload})
                except Exception:
                    pass
            return

        if not isinstance(payload, dict):
            return

        payload_type = payload.get("type")
        # Log reasoning_summary events when received from queue
        if payload_type == "reasoning_summary":
            try:
                delta_len = len(str(payload.get('delta', '')))
                logger.debug(f"L4.orchestrator [emit_payload] - Received reasoning_summary delta: {delta_len} chars, forwarding to on_event")
            except Exception:
                pass
        if payload_type == "output.delta":
            text = str(payload.get("text") or "")
            if text:
                full_text.append(text)
        if on_event:
            try:
                await on_event(payload)
            except Exception:
                pass

    try:
        # Prefer OpenAI Responses API streaming
        if provider == "openai":
            # If minimal effort + GPT-5 family, route to gpt-5-chat-latest
            # (GPT-5-Chat now supports tools, so we route even with tools present)
            try:
                ml = str(model or "").lower()
                minimal_effort = False
                if isinstance(model_kwargs, dict):
                    r = model_kwargs.get("reasoning") or {}
                    if isinstance(r, dict):
                        minimal_effort = str(r.get("effort") or "").lower() == "minimal"
                if ml.startswith("gpt-5") and minimal_effort:
                    if ml.startswith("gpt-5.1"):
                        model = "gpt-5.1-chat-latest"
                    else:
                        model = "gpt-5-chat-latest"
                    logger.debug(f"L4.stream - Routing to {model} for minimal effort turn")
                    if isinstance(model_kwargs, dict) and model_kwargs.get("reasoning"):
                        # Drop reasoning payload to avoid requesting summaries on chat-only models
                        model_kwargs = dict(model_kwargs)
                        model_kwargs.pop("reasoning", None)
            except Exception:
                pass
            # Use OpenAI Responses API streaming (per docs/openai_streaming.md on Responses)
            try:
                from openai import OpenAI  # type: ignore
            except Exception as _e:
                raise RuntimeError(f"OpenAI SDK unavailable: {_e}")
            # Pull API key from config
            try:
                from utils.config_loader import get_config_value as _get
                api_key = _get(config, "api_keys.openai")
            except Exception:
                api_key = None
            if not api_key:
                raise RuntimeError("OpenAI API key missing")
            client = OpenAI(api_key=api_key)
            # Build input array for Responses API
            if isinstance(attempt_prompt, list):
                input_items = attempt_prompt
            else:
                input_items = [{"role": "user", "content": str(attempt_prompt)}]

            # Background thread to read events and push deltas to the async queue
            import threading as _threading
            loop = asyncio.get_event_loop()
            q: asyncio.Queue = asyncio.Queue()

            def _push(item: Optional[Union[str, Dict[str, Any]]]):
                try:
                    loop.call_soon_threadsafe(q.put_nowait, item)
                except Exception:
                    pass

            def _openai_stream_thread():
                visited_ids: set[int] = set()

                def _extract_openai_text(value: Any, depth: int = 0) -> str:
                    if value is None or depth > 5:
                        return ""
                    try:
                        if isinstance(value, str):
                            return value
                        if isinstance(value, (int, float)):
                            return str(value)
                    except Exception:
                        pass
                    obj_id = id(value)
                    if obj_id in visited_ids:
                        return ""
                    visited_ids.add(obj_id)
                    try:
                        if isinstance(value, Mapping):
                            text_field = value.get("text")
                            if isinstance(text_field, str) and text_field:
                                return text_field
                            content_field = value.get("content")
                            if isinstance(content_field, Sequence) and not isinstance(content_field, (str, bytes, bytearray)):
                                parts: List[str] = []
                                for item in content_field:
                                    part_text = _extract_openai_text(item, depth + 1)
                                    if part_text:
                                        parts.append(part_text)
                                if parts:
                                    return "".join(parts)
                            for key in ("delta", "value"):
                                nested = value.get(key)
                                text = _extract_openai_text(nested, depth + 1)
                                if text:
                                    return text
                            return ""
                        if isinstance(value, Sequence) and not isinstance(value, (str, bytes, bytearray)):
                            parts_seq: List[str] = []
                            for item in value:
                                part_text = _extract_openai_text(item, depth + 1)
                                if part_text:
                                    parts_seq.append(part_text)
                            if parts_seq:
                                return "".join(parts_seq)
                            return ""
                        for attr in ("text", "content", "delta", "value"):
                            if hasattr(value, attr):
                                try:
                                    attr_val = getattr(value, attr)
                                except Exception:
                                    continue
                                text = _extract_openai_text(attr_val, depth + 1)
                                if text:
                                    return text
                        for attr in ("model_dump", "dict"):
                            method = getattr(value, attr, None)
                            if callable(method):
                                try:
                                    dumped = method()
                                except TypeError:
                                    try:
                                        dumped = method(exclude_none=True)
                                    except Exception:
                                        dumped = None
                                except Exception:
                                    dumped = None
                                if dumped is not None:
                                    text = _extract_openai_text(dumped, depth + 1)
                                    if text:
                                        return text
                    finally:
                        visited_ids.discard(obj_id)
                    return ""

                params: Dict[str, Any] = {
                    "model": model,
                    "input": input_items,
                    "stream": True,
                }
                # Promote supported custom kwargs (reasoning, text, token limits, temperature, etc.)
                if isinstance(model_kwargs, dict):
                    try:
                        reasoning_cfg = model_kwargs.get("reasoning")
                        if isinstance(reasoning_cfg, dict):
                            params["reasoning"] = dict(reasoning_cfg)
                    except Exception:
                        pass
                    try:
                        text_cfg = model_kwargs.get("text")
                        if isinstance(text_cfg, dict):
                            params["text"] = dict(text_cfg)
                    except Exception:
                        pass
                    for key in (
                        "max_output_tokens",
                        "max_completion_tokens",
                        "max_tokens",
                        "temperature",
                        "top_p",
                        "presence_penalty",
                        "frequency_penalty",
                    ):
                        try:
                            val = model_kwargs.get(key)
                        except Exception:
                            val = None
                        if val is not None:
                            params[key] = val
                # Only add reasoning summary if reasoning was explicitly provided (for reasoning models)
                # Don't add reasoning for chat-latest models which don't support it
                if isinstance(params.get("reasoning"), dict):
                    params["reasoning"].setdefault("summary", "auto")
                try:
                    with client.responses.stream(**params) as stream:
                        pending_summary_clear = False
                        # Track active function calls by output_index
                        active_function_calls: Dict[int, Dict[str, Any]] = {}
                        
                        for event in stream:
                            try:
                                event_type = getattr(event, "type", "")
                                if event_type == "response.reasoning_summary_text.delta":
                                    delta_obj = getattr(event, "delta", None)
                                    summary_text = _extract_openai_text(delta_obj)
                                    if summary_text:
                                        pending_summary_clear = True
                                        # Log reasoning_summary from thread
                                        try:
                                            logger.debug(f"L4.orchestrator [thread] - Pushing reasoning_summary delta: {len(summary_text)} chars")
                                        except Exception:
                                            pass
                                        _push({"type": "reasoning_summary", "delta": summary_text})
                                elif event_type == "response.output_text.delta":
                                    delta_obj = getattr(event, "delta", None)
                                    text_fragment = _extract_openai_text(delta_obj)
                                    if text_fragment:
                                        if pending_summary_clear:
                                            pending_summary_clear = False
                                            _push({"type": "reasoning_summary_clear"})
                                        _push({"type": "output.delta", "text": text_fragment})
                                elif event_type == "response.output_item.added":
                                    item = getattr(event, "item", None)
                                    output_index = getattr(event, "output_index", None)
                                    if item and output_index is not None:
                                        item_type = getattr(item, "type", None)
                                        if item_type == "function_call":
                                            # Initialize function call accumulator
                                            active_function_calls[output_index] = {
                                                "id": getattr(item, "id", ""),
                                                "call_id": getattr(item, "call_id", ""),
                                                "name": getattr(item, "name", ""),
                                                "arguments": "",  # Will accumulate from deltas
                                            }
                                            try:
                                                logger.debug(f"L4.orchestrator [thread] - Added function call at index {output_index}: {getattr(item, 'name', 'unknown')}")
                                            except Exception:
                                                pass
                                elif event_type == "response.function_call_arguments.delta":
                                    output_index = getattr(event, "output_index", None)
                                    delta_obj = getattr(event, "delta", None)
                                    delta_text = _extract_openai_text(delta_obj)
                                    if output_index is not None and output_index in active_function_calls and delta_text:
                                        active_function_calls[output_index]["arguments"] += delta_text
                                elif event_type == "response.function_call_arguments.done":
                                    output_index = getattr(event, "output_index", None)
                                    item = getattr(event, "item", None)
                                    if output_index is not None and output_index in active_function_calls:
                                        # Update with final arguments from done event if available
                                        if item:
                                            final_args = getattr(item, "arguments", None)
                                            if isinstance(final_args, str):
                                                active_function_calls[output_index]["arguments"] = final_args
                                            elif isinstance(final_args, dict):
                                                import json
                                                active_function_calls[output_index]["arguments"] = json.dumps(final_args)
                                elif event_type == "response.output_item.done":
                                    output_index = getattr(event, "output_index", None)
                                    item = getattr(event, "item", None)
                                    if output_index is not None and output_index in active_function_calls:
                                        # Finalize function call with any updates from item
                                        if item:
                                            final_args = getattr(item, "arguments", None)
                                            if isinstance(final_args, str) and final_args:
                                                active_function_calls[output_index]["arguments"] = final_args
                                            elif isinstance(final_args, dict):
                                                import json
                                                active_function_calls[output_index]["arguments"] = json.dumps(final_args)
                                        
                                        # Emit complete function call event
                                        function_call = active_function_calls.pop(output_index)
                                        try:
                                            logger.debug(f"L4.orchestrator [thread] - Complete function call: {function_call['name']} with {len(function_call['arguments'])} chars of arguments")
                                        except Exception:
                                            pass
                                        _push({
                                            "type": "function_call",
                                            "id": function_call["id"],
                                            "call_id": function_call["call_id"],
                                            "name": function_call["name"],
                                            "arguments": function_call["arguments"] or "{}",
                                        })
                                elif event_type == "response.error":
                                    err = getattr(event, "error", None) or getattr(event, "message", "") or "Unknown streaming error"
                                    _push({"type": "error", "error": str(err)})
                                    break
                                elif event_type == "response.done":
                                    # Emit any remaining active function calls (fallback)
                                    for output_index, function_call in list(active_function_calls.items()):
                                        try:
                                            logger.debug(f"L4.orchestrator [thread] - Emitting remaining function call at index {output_index}")
                                        except Exception:
                                            pass
                                        _push({
                                            "type": "function_call",
                                            "id": function_call["id"],
                                            "call_id": function_call["call_id"],
                                            "name": function_call["name"],
                                            "arguments": function_call["arguments"] or "{}",
                                        })
                                    active_function_calls.clear()
                                    # No-op; sentinel pushed after loop
                                    break
                            except Exception:
                                continue
                except Exception:
                    # Signal end on any error; caller will fallback if nothing was emitted
                    pass
                finally:
                    _push(None)

            t = _threading.Thread(target=_openai_stream_thread, daemon=True)
            t.start()

            # Drain queue and emit deltas
            while True:
                item = await q.get()
                if item is None:
                    break
                await emit_payload(item)
            return "".join(full_text)

        # Groq streaming (Chat Completions API)
        if provider == "groq":
            try:
                from groq import Groq  # type: ignore
            except Exception as _e:
                raise RuntimeError(f"Groq SDK unavailable: {_e}")
            try:
                from utils.config_loader import get_config_value as _get
                api_key = _get(config, "api_keys.groq")
            except Exception:
                api_key = None
            if not api_key:
                raise RuntimeError("Groq API key missing")
            client = Groq(api_key=api_key)
            # Build messages array
            if isinstance(attempt_prompt, list):
                messages = attempt_prompt
            else:
                messages = [{"role": "user", "content": str(attempt_prompt)}]
            import threading as _threading
            loop = asyncio.get_event_loop()
            q: asyncio.Queue = asyncio.Queue()

            def _push(item: Optional[str]):
                try:
                    loop.call_soon_threadsafe(q.put_nowait, item)
                except Exception:
                    pass

            def _groq_stream_thread():
                params: Dict[str, Any] = {
                    "model": model,
                    "messages": messages,
                    "stream": True,
                }
                # Enforce maximum reasoning for all Groq models regardless of optimizer hints
                try:
                    ml = str(model).lower()
                except Exception:
                    ml = ""
                is_gpt_oss = ("openai/gpt-oss" in ml) or ml.startswith("gpt-oss-")
                is_qwen32b = ml.startswith("qwen/qwen3-32b") or ("qwen3-32b" in ml)
                if is_gpt_oss:
                    params["reasoning_effort"] = "high"  # Max for GPT-OSS per docs/groq/reasoning.md
                elif is_qwen32b:
                    params["reasoning_effort"] = "default"  # Max supported for Qwen 3 32B
                try:
                    stream = client.chat.completions.create(**params)
                    for chunk in stream:
                        try:
                            choice = (getattr(chunk, "choices", []) or [None])[0]
                            if not choice:
                                continue
                            delta = getattr(choice, "delta", None)
                            if not delta:
                                continue
                            frag = getattr(delta, "content", None)
                            if isinstance(frag, str) and frag:
                                _push(frag)
                        except Exception:
                            continue
                except Exception:
                    pass
                finally:
                    _push(None)

            t = _threading.Thread(target=_groq_stream_thread, daemon=True)
            t.start()

            while True:
                item = await q.get()
                if item is None:
                    break
                await emit_payload(item)
            return "".join(full_text)

        # Anthropic Messages streaming
        if provider == "anthropic":
            try:
                import anthropic  # type: ignore
            except Exception as _e:
                raise RuntimeError(f"Anthropic SDK unavailable: {_e}")
            try:
                from utils.config_loader import get_config_value as _get
                api_key = _get(config, "api_keys.anthropic")
            except Exception:
                api_key = None
            if not api_key:
                raise RuntimeError("Anthropic API key missing")
            client = anthropic.Anthropic(api_key=api_key)

            # Build messages array
            if isinstance(attempt_prompt, list):
                messages = attempt_prompt
            else:
                messages = [{"role": "user", "content": str(attempt_prompt)}]

            import threading as _threading
            loop = asyncio.get_event_loop()
            q: asyncio.Queue = asyncio.Queue()

            def _push(item: Optional[str]):
                try:
                    loop.call_soon_threadsafe(q.put_nowait, item)
                except Exception:
                    pass

            def _anthropic_stream_thread():
                try:
                    with client.messages.stream(
                        model=model,
                        max_tokens=1024,
                        messages=messages,
                    ) as stream:
                        for text in stream.text_stream:
                            try:
                                if isinstance(text, str) and text:
                                    _push(text)
                            except Exception:
                                continue
                except Exception:
                    pass
                finally:
                    _push(None)

            t = _threading.Thread(target=_anthropic_stream_thread, daemon=True)
            t.start()
            while True:
                item = await q.get()
                if item is None:
                    break
                await emit_payload(item)
            return "".join(full_text)

        # Google Generative AI streaming
        if provider == "google":
            try:
                from google import genai  # type: ignore
                from google.genai import types as gtypes  # type: ignore
            except Exception as _e:
                raise RuntimeError(f"Google genai SDK unavailable: {_e}")
            # No API key fetch needed; Client picks up env if set, else requires config
            client = genai.Client()
            # Convert attempt_prompt to contents list
            if isinstance(attempt_prompt, list):
                # Flatten simple message contents to strings
                contents = []
                for m in attempt_prompt:
                    try:
                        c = m.get("content") if isinstance(m, dict) else getattr(m, "content", None)
                        if isinstance(c, str):
                            contents.append(c)
                    except Exception:
                        continue
                if not contents:
                    contents = [str(attempt_prompt)]
            else:
                contents = [str(attempt_prompt)]

            import threading as _threading
            loop = asyncio.get_event_loop()
            q: asyncio.Queue = asyncio.Queue()

            def _push(item: Optional[Union[str, Dict[str, Any]]]):
                try:
                    loop.call_soon_threadsafe(q.put_nowait, item)
                except Exception:
                    pass

            def _looks_like_sdk_repr(txt: str) -> bool:
                t = str(txt)
                return (
                    "sdk_http_response=HttpResponse" in t
                    or "GenerateContentResponseUsageMetadata" in t
                    or ("candidates=None" in t and "response_id=" in t)
                )

            def _google_stream_thread():
                try:
                    # Enforce BLOCK_NONE safety across all categories for streaming
                    try:
                        cats = [
                            gtypes.HarmCategory.HARASSMENT,
                            gtypes.HarmCategory.HATE_SPEECH,
                            gtypes.HarmCategory.SEXUALLY_EXPLICIT,
                            gtypes.HarmCategory.DANGEROUS_CONTENT,
                            gtypes.HarmCategory.CIVIC_INTEGRITY,
                        ]
                        safety = [
                            gtypes.SafetySetting(category=c, threshold=gtypes.HarmBlockThreshold.BLOCK_NONE)
                            for c in cats
                        ]
                        # CRITICAL: Disable AFC (Automatic Function Calling) to prevent doubled responses
                        # AFC causes the SDK to make additional API calls after our streaming completes,
                        # resulting in different text than what was streamed to the user.
                        gcfg = gtypes.GenerateContentConfig(
                            safety_settings=safety,
                            automatic_function_calling=gtypes.AutomaticFunctionCallingConfig(disable=True),
                        )
                    except Exception:
                        gcfg = gtypes.GenerateContentConfig(
                            automatic_function_calling=gtypes.AutomaticFunctionCallingConfig(disable=True),
                        )
                    
                    # Apply thinking config - Gemini 3 uses thinking_level, legacy uses thinking_budget
                    is_gemini_3 = "gemini-3" in model.lower()
                    
                    if is_gemini_3:
                        # Gemini 3: Always use thinking_level=high with include_thoughts=True
                        try:
                            gcfg.thinking_config = gtypes.ThinkingConfig(
                                thinking_level="high",
                                include_thoughts=True,
                            )
                            logger.debug("L4.orchestrator [google-stream] - Applied Gemini 3 thinking_level=high")
                        except Exception:
                            pass
                    elif isinstance(model_kwargs, dict):
                        tcfg = model_kwargs.get("thinking_config")
                        if isinstance(tcfg, dict):
                            try:
                                tb = int(tcfg.get("thinking_budget")) if tcfg.get("thinking_budget") is not None else None
                                inc = bool(tcfg.get("include_thoughts", False))
                                if tb is not None:
                                    gcfg.thinking_config = gtypes.ThinkingConfig(
                                        thinking_budget=tb,
                                        include_thoughts=inc,
                                    )
                            except Exception:
                                pass
                        # Or map generic reasoning override if present
                        elif model_kwargs.get("reasoning") or model_kwargs.get("thinking_budget"):
                             try:
                                 # Default to dynamic thinking if we have a reasoning signal
                                 gcfg.thinking_config = gtypes.ThinkingConfig(
                                     thinking_budget=-1,
                                     include_thoughts=True,
                                 )
                             except Exception:
                                 pass

                    # Handle google_prev_response for multi-step context injection
                    final_contents = []
                    prev_resp = model_kwargs.get("google_prev_response") if isinstance(model_kwargs, dict) else None
                    
                    # Prepare attachment parts
                    attachment_parts = []
                    inline_attachments = model_kwargs.get("inline_data_attachments") or []
                    if isinstance(model_kwargs, dict):
                         inline_attachments = model_kwargs.get("inline_data_attachments") or []
                    for att in inline_attachments:
                        try:
                            # Ensure data is bytes
                            data = att.get("data")
                            if isinstance(data, str):
                                import base64
                                data = base64.b64decode(data)
                            attachment_parts.append(gtypes.Part(
                                inline_data=gtypes.Blob(
                                    mime_type=att.get("mime_type", "application/octet-stream"),
                                    data=data
                                )
                            ))
                        except Exception as e:
                             logger.warning(f"L4.orchestrator [google-stream] - Failed to process attachment: {e}")

                    # Convert user prompt to explicit Content object
                    user_text = contents[0] if contents else ""
                    parts = [gtypes.Part(text=user_text)] + attachment_parts
                    final_contents.append(gtypes.Content(role="user", parts=parts))

                    if prev_resp:
                        try:
                            # Extract candidates from previous response (supports both SDK objects and SynthesizedResponse)
                            cands = getattr(prev_resp, "candidates", [])
                            if cands:
                                cand = cands[0]
                                content = getattr(cand, "content", None)
                                parts = getattr(content, "parts", [])
                                if parts:
                                    sdk_parts = []
                                    for p in parts:
                                        part_args = {}
                                        # Text
                                        txt = getattr(p, "text", None)
                                        if txt:
                                            part_args["text"] = txt
                                        
                                        # Function Call
                                        # SynthesizedResponse uses SimpleNamespace with name/args; SDK uses FunctionCall
                                        fc = getattr(p, "function_call", None)
                                        if fc:
                                            fc_name = getattr(fc, "name", None)
                                            fc_args = getattr(fc, "args", None)
                                            if fc_name:
                                                # Try to construct SDK FunctionCall
                                                try:
                                                    part_args["function_call"] = gtypes.FunctionCall(name=fc_name, args=fc_args)
                                                except Exception:
                                                    # Fallback for older SDKs or different shapes
                                                    part_args["function_call"] = {"name": fc_name, "args": fc_args}

                                        # Thought Signature (CRITICAL for Gemini 3)
                                        # SynthesizedResponse attaches this to the part namespace
                                        sig = getattr(p, "thought_signature", None)
                                        if sig:
                                            part_args["thought_signature"] = sig
                                        
                                        if part_args:
                                            sdk_parts.append(gtypes.Part(**part_args))
                                    
                                    if sdk_parts:
                                        final_contents.append(gtypes.Content(role="model", parts=sdk_parts))
                        except Exception as hist_err:
                            logger.warning(f"L4.orchestrator [google-stream] - Failed to inject history: {hist_err}")

                    stream = client.models.generate_content_stream(
                        model=model,
                        contents=final_contents,
                        config=gcfg,
                    )
                    for chunk in stream:
                        try:
                            # Try to extract parts directly from candidates -> content.parts
                            cands = getattr(chunk, "candidates", None) or []
                            has_content = False
                            for cand in cands:
                                content = getattr(cand, "content", None)
                                parts = getattr(content, "parts", None) or []
                                for part in parts:
                                    ptxt = getattr(part, "text", None)
                                    if isinstance(ptxt, str) and ptxt:
                                        has_content = True
                                        # Check for thought/reasoning
                                        is_thought = getattr(part, "thought", False)
                                        if is_thought:
                                            _push({"type": "reasoning_summary", "delta": ptxt})
                                        else:
                                            # Plain text (output delta)
                                            _push(ptxt)
                            
                            # Fallback to chunk.text if no parts iterated (rare but possible in some SDK versions)
                            if not has_content:
                                txt = getattr(chunk, "text", None)
                                if isinstance(txt, str) and txt and not _looks_like_sdk_repr(txt):
                                    _push(txt)

                        except Exception:
                            continue
                except Exception:
                    pass
                finally:
                    _push(None)

            t = _threading.Thread(target=_google_stream_thread, daemon=True)
            t.start()
            while True:
                item = await q.get()
                if item is None:
                    break
                await emit_payload(item)
            joined = "".join(full_text)
            # If nothing valid streamed or only SDK-repr-like content slipped through,
            # fall back to a single non-streaming call and extract text robustly.
            if (not joined) or _looks_like_sdk_repr(joined):
                try:
                    gcfg = None
                    try:
                        cats = [
                            gtypes.HarmCategory.HARASSMENT,
                            gtypes.HarmCategory.HATE_SPEECH,
                            gtypes.HarmCategory.SEXUALLY_EXPLICIT,
                            gtypes.HarmCategory.DANGEROUS_CONTENT,
                            gtypes.HarmCategory.CIVIC_INTEGRITY,
                        ]
                        safety = [
                            gtypes.SafetySetting(category=c, threshold=gtypes.HarmBlockThreshold.BLOCK_NONE)
                            for c in cats
                        ]
                        gcfg = gtypes.GenerateContentConfig(
                            safety_settings=safety,
                            automatic_function_calling=gtypes.AutomaticFunctionCallingConfig(disable=True),
                        )
                    except Exception:
                        gcfg = gtypes.GenerateContentConfig(
                            automatic_function_calling=gtypes.AutomaticFunctionCallingConfig(disable=True),
                        )
                    resp = client.models.generate_content(model=model, contents=contents, config=gcfg)
                    try:
                        # Reuse provider extractor to avoid duplicate parsing logic
                        from layer1_chatbot.model_call_google import _google_model as _gmodel  # type: ignore
                        extracted = _gmodel._extract_response_text(resp)
                    except Exception:
                        extracted = None
                    if extracted and not _looks_like_sdk_repr(extracted):
                        return extracted
                except Exception:
                    pass
            return joined

        # xAI streaming (prefer native SDK; fallback to OpenAI-compatible base_url)
        if provider == "xai":
            api_key = None
            try:
                from utils.config_loader import get_config_value as _get
                api_key = _get(config, "api_keys.xai")
            except Exception:
                api_key = None
            if not api_key:
                raise RuntimeError("xAI API key missing")
            # Try xai-sdk first
            try:
                from xai_sdk import Client as _XAIClient  # type: ignore
                from xai_sdk.chat import user as _x_user, system as _x_system  # type: ignore
                client = _XAIClient(api_key=api_key, timeout=3600)
                chat = client.chat.create(model=model)
                # Map attempt_prompt → chat messages
                if isinstance(attempt_prompt, list):
                    for m in attempt_prompt:
                        try:
                            r = m.get("role") if isinstance(m, dict) else getattr(m, "role", "user")
                            c = m.get("content") if isinstance(m, dict) else getattr(m, "content", "")
                            if r == "system":
                                chat.append(_x_system(str(c)))
                            else:
                                chat.append(_x_user(str(c)))
                        except Exception:
                            continue
                else:
                    chat.append(_x_user(str(attempt_prompt)))

                import threading as _threading
                loop = asyncio.get_event_loop()
                q: asyncio.Queue = asyncio.Queue()

                def _push(item: Optional[str]):
                    try:
                        loop.call_soon_threadsafe(q.put_nowait, item)
                    except Exception:
                        pass

                def _xai_stream_thread():
                    try:
                        for response, chunk in chat.stream():
                            try:
                                frag = getattr(chunk, "content", None)
                                if isinstance(frag, str) and frag:
                                    _push(frag)
                            except Exception:
                                continue
                    except Exception:
                        pass
                    finally:
                        _push(None)

                t = _threading.Thread(target=_xai_stream_thread, daemon=True)
                t.start()
                while True:
                    item = await q.get()
                    if item is None:
                        break
                    await emit_payload(item)
                return "".join(full_text)
            except Exception:
                # Fallback: use OpenAI client with xAI base_url
                try:
                    from openai import OpenAI as _OA  # type: ignore
                    client = _OA(api_key=api_key, base_url="https://api.x.ai/v1")
                    # Prepare messages
                    if isinstance(attempt_prompt, list):
                        messages = attempt_prompt
                    else:
                        messages = [{"role": "user", "content": str(attempt_prompt)}]
                    import threading as _threading
                    loop = asyncio.get_event_loop()
                    q: asyncio.Queue = asyncio.Queue()
                    def _push(item: Optional[str]):
                        try:
                            loop.call_soon_threadsafe(q.put_nowait, item)
                        except Exception:
                            pass
                    def _xai_openai_stream_thread():
                        try:
                            stream = client.chat.completions.create(
                                model=model,
                                messages=messages,
                                stream=True,
                            )
                            for chunk in stream:
                                try:
                                    choice = (getattr(chunk, "choices", []) or [None])[0]
                                    if not choice:
                                        continue
                                    delta = getattr(choice, "delta", None)
                                    if not delta:
                                        continue
                                    frag = getattr(delta, "content", None)
                                    if isinstance(frag, str) and frag:
                                        _push(frag)
                                except Exception:
                                    continue
                        except Exception:
                            pass
                        finally:
                            _push(None)
                    t = _threading.Thread(target=_xai_openai_stream_thread, daemon=True)
                    t.start()
                    while True:
                        item = await q.get()
                        if item is None:
                            break
                        await emit_payload(item)
                    return "".join(full_text)
                except Exception:
                    # fallthrough to non-stream fallback below
                    pass

        # Providers without streaming path or after failures — fall back to a single non-streaming call
        fb_resp, fb_model = await _call_model_with_circuit_breaker(
            model_selector,
            attempt_prompt,
            model,
            tool_schemas or [],
            config,
            model_kwargs,
            rebuild_prompt_func=None,
        )
        try:
            text = model_selector.extract_response_text(
                fb_resp, model_selector.get_provider_from_model(fb_model), fb_model
            ) or ""
        except Exception:
            text = ""
        if text:
            await emit_payload(text)
        return "".join(full_text)

    except Exception as e:
        logger.debug(f"L4.orchestrator [stream-final] - Unexpected error: {e}")
        # Provide empty; caller will fallback to non-stream response
        return ""


async def process_tool_orchestration(
    content: str,
    message: Any,
    model_selector,
    prompt_builder,
    config: Dict[str, Any],
    event_cb: Optional[Callable[[Dict[str, Any]], Awaitable[None]]] = None,
) -> Optional[str]:
    """Process a user message through the tool orchestration loop."""
    
    # Enhanced error handling with context
    stage = "init"
    run_id = getattr(message, "id", None) or getattr(message, "run_id", None) or "unknown"
    try:
        # Web mode
        
        async def _emit(evt: Dict[str, Any]):
            if not event_cb:
                return
            try:
                await event_cb(evt)
            except Exception as ee:
                logger.debug(f"L4.orchestrator [event] - emit failed: {ee}")
        
        # Clear memory optimization cache for new user message
        stage = "clear_memory_cache"
        prompt_builder.clear_memory_cache()
        logger.debug("L4.tools [orchestrator] - Cleared prompt builder memory cache for new message")

        # Build conversation history with memory integration
        conversation_history = []
        channel = getattr(message, "channel", None)
        conversation_id = str(getattr(channel, "id", "") or run_id)
        topic_id = getattr(channel, "topic_id", None)
        tool_context: Dict[str, Any] = {"conversation_id": conversation_id}
        if topic_id is not None:
            tool_context["topic_id"] = str(topic_id)
        tool_context["run_id"] = run_id  # NEW: Pass run_id for task-aware tools
        tool_context["room_id"] = conversation_id  # Explicitly set room_id for tools like send_file

        # Start processing early so UI reflects activity immediately
        stage = "start_processing"
        start_processing("tool_orchestration")
        # Immediately show "thinking"; remove optimizer-specific placeholders
        stage = "emit_initial_thinking_status"
        await _emit({"type": "status", "value": "thinking"})
        stage = "build_initial_prompt"
        initial_prompt = await prompt_builder.build_conversation_prompt(
            content, message, conversation_history
        )

        # Auto-image analysis disabled by default per spec alignment
        
        # Determine model/provider early to control live summary emissions
        model = config.get("primary_model", "gpt-5")
        provider = model_selector.get_provider_from_model(model)
        try:
            model_lc = str(model).lower()
        except Exception:
            model_lc = ""
        is_openai = provider == "openai"
        is_gpt5 = model_lc.startswith("gpt-5")
        
        # Keep thinking status active during planning without emitting placeholder deltas
        stage = "emit_planning_status"
        await _emit({"type": "status", "value": "thinking"})

        # Get tool schemas for model (provider-specific filtering for native web search)
        stage = "generate_tool_schemas"
        tool_schemas = generate_tool_schemas(provider=provider)

        # Get model response with tool schemas (model/provider already determined)

        logger.info(
            f"L4.tools [orchestrator] - Processing with {model} ({provider})"
        )

        max_iterations = 1000  # Upper bound; per-turn tool-only guard below controls early exits
        # Safety cap: maximum consecutive iterations where the model only returns tool calls
        # Precedence: config['max_tool_only_iterations'] > THEO_MAX_TOOL_ITERS env > default 1000
        try:
            cfg_value = config.get("max_tool_only_iterations")
            env_value = os.getenv("THEO_MAX_TOOL_ITERS")
            # Safer default to avoid infinite tool-looping on UI actions like define_form/show_form
            max_tool_only_iters = int(cfg_value or env_value or 1000)
        except Exception:
            max_tool_only_iters = 1000
        consecutive_tool_only_iters = 0
        # Track whether an image was just auto-sent this iteration to avoid premature turn end
        image_autosent_recently = False
        image_summary_records: List[Dict[str, Any]] = []
        image_summary_metadata_added = False
        skip_final_streaming = False
        last_reasoning_summary: Optional[str] = None
        
        # Loop detection: Track tool calls to prevent infinite loops
        tool_call_history = []  # List of (tool_name, file_path, iteration, arguments) tuples
        # Track pagination progress for tools like read_part_of_file
        pagination_state = {}  # key=(tool_name,file_path) -> {last_start:int,last_end:int,stalled:int,ts:float}
        max_same_tool_file_calls = 3  # Max repeated calls to same tool on same file without progress
        # Stricter de-duplication: disable exact tool+args signatures after N repeats
        disabled_signatures: set[tuple[str, str]] = set()
        duplicate_signature_threshold = 5
        # Helper functions are module-level; use local disabled set when calling
        
        # Track successfully executed tools for error context
        executed_tools: List[str] = []
        
        # Unified prompting for all providers (no provider-specific streaming/structured branches)
        # Track and tame repeated form operations per turn
        define_form_calls_total: int = 0
        forms_shown_total: int = 0
        # Limit programmatic form submissions per form per turn to avoid spam during smoke tests
        # key = (form_id, room_id or '') -> count
        form_save_counts: dict[tuple[str, str], int] = {}
        # Track last-seen argument signature for each tool so we can detect meaningful changes
        last_tool_signatures: dict[str, str] = {}

        async def _apply_guard_skip(
            tool_call: Dict[str, Any],
            reason: str,
            *,
            reasoning_text: Optional[str] = None,
        ) -> None:
            """Mark a tool call as guard-skipped while surfacing a clear hint to Theo."""

            tool_call["__guard_skip"] = True
            tool_call["__guard_reason"] = reason
            if reasoning_text:
                try:
                    await _emit({"type": "reasoning.delta", "text": reasoning_text})
                except Exception:
                    pass
            # Always explain the skip inside Theo's running conversation state
            try:
                conversation_history.append(f"System: {reason}")
            except Exception:
                pass

        # Define a prompt rebuilder for true fallback attempts (no translation across providers)
        async def _rebuild_prompt_for_model(target_model: str, include_tool_results: bool = True) -> Any:
            """Rebuild prompt for fallback model with fresh context.
            
            Args:
                target_model: The fallback model to rebuild for
                include_tool_results: If True, include tool execution results in the prompt
            
            When include_tool_results=True, the fallback prompt includes:
            - Tool execution results (so fallback knows what was already done)
            - System messages (for context and guidance)
            But excludes:
            - Failed model responses (which may have caused the fallback)
            - User messages that are already in the base prompt
            """
            try:
                # Clear caches and rebuild an entirely fresh prompt
                try:
                    prompt_builder.clear_memory_cache()
                except Exception:
                    pass
                
                # Filter conversation history to include only tool results and system messages
                filtered_history = []
                if include_tool_results and conversation_history:
                    for entry in conversation_history:
                        # Include tool results and system messages
                        if entry.startswith("Tool:") or entry.startswith("System:"):
                            filtered_history.append(entry)
                        # Exclude model responses that may have parsing issues
                        # and user messages (already in base prompt)
                
                fresh_prompt = await prompt_builder.build_conversation_prompt(
                    content, message, filtered_history
                )
                return fresh_prompt
            except Exception as e:
                logger.warning(f"L4.orchestrator [rebuild] - Failed to rebuild prompt for {target_model}: {e}")
                return current_prompt

        # Build reasoning/text kwargs once from optimizer result (cached in prompt_builder)
        optimization_result = None
        model_kwargs = {}
        try:
            optimization_result = getattr(prompt_builder, "_last_optimization", None)
            model_kwargs = get_reasoning_kwargs(optimization_result)
        except Exception as e:
            logger.debug(f"L4.orchestrator [reasoning] - Could not derive reasoning kwargs: {e}")

        model_kwargs, tool_schemas = _apply_reasoning_and_tool_overrides(
            provider, model_kwargs, tool_schemas, optimization_result
        )

        # Inject audio attachments into model_kwargs if present (for Google Gemini)
        try:
            raw_attachments = getattr(message, "attachments", []) or []
            audio_blobs = []
            for att in raw_attachments:
                ctype = getattr(att, "content_type", "") or ""
                fname = getattr(att, "filename", "") or ""
                path = getattr(att, "path", "")
                url = getattr(att, "url", "")
                
                # If path is missing but URL exists (uploaded via API), resolve it
                if not path and url and isinstance(url, str) and url.startswith("/uploads/"):
                     print(f"DEBUG: Attempting to resolve URL {url}")
                     try:
                         from utils.vault_paths import get_uploads_root
                         parts = url.strip("/").split("/")
                         if len(parts) >= 3 and parts[0] == "uploads":
                             # /uploads/{room}/{filename}
                             room_id = parts[1]
                             filename = parts[2]
                             uploads_root = get_uploads_root(room_id)
                             resolved = os.path.join(uploads_root, filename)
                             print(f"DEBUG: Resolved path: {resolved}, exists: {os.path.exists(resolved)}")
                             if os.path.exists(resolved):
                                 path = resolved
                     except Exception as e:
                         print(f"DEBUG: Resolution error: {e}")
                         logger.debug(f"L4.orchestrator - Failed to resolve audio path from URL {url}: {e}")

                # Check for audio types
                is_audio = (ctype and ctype.startswith("audio/")) or any(fname.lower().endswith(ext) for ext in [".mp3", ".wav", ".ogg", ".m4a", ".flac", ".aac"])
                
                if is_audio and path and os.path.exists(path):
                    try:
                        with open(path, "rb") as f:
                            blob = f.read()
                        # Use filename extension to infer mime if generic
                        if not ctype or ctype == "application/octet-stream":
                             import mimetypes
                             ctype, _ = mimetypes.guess_type(path)
                             if not ctype:
                                 ctype = "audio/mp3" # fallback
                        
                        audio_blobs.append({
                            "mime_type": ctype,
                            "data": blob,
                            "path": path
                        })
                        logger.info(f"L4.orchestrator - Loaded audio attachment: {fname} ({len(blob)} bytes)")
                    except Exception as e:
                        logger.warning(f"L4.orchestrator - Failed to read audio attachment {fname}: {e}")
            
            if audio_blobs:
                model_kwargs["inline_data_attachments"] = audio_blobs
                # Explicitly inform the model about the audio to prevent unnecessary tool use
                hint = (
                    "\n\n[System Notification] An audio file has been successfully uploaded and attached to this context. "
                    "You have direct sensory access to this audio data via your multimodal capabilities. "
                    "Please listen to it and analyze it directly. Do NOT claim you cannot hear it. "
                    "Do NOT try to use python tools to open it."
                )
                if isinstance(initial_prompt, str):
                    initial_prompt += hint
                elif isinstance(initial_prompt, list):
                    initial_prompt.append({"role": "system", "content": hint.strip()})
        except Exception as e:
             logger.warning(f"L4.orchestrator - Error processing audio attachments: {e}")

        # Removed prior behavior that forced a continuation round after image sends.

        # Track response IDs for continuation calls (required for reasoning models per OpenAI docs)
        last_response_id: Optional[str] = None
        
        # Track objects for Google context preservation
        last_response_object: Any = None
        last_tool_results: List[Dict[str, Any]] = []
        last_tool_call_names: List[str] = []
        last_turn_items_count: int = 0
        google_turn_history: List[Dict[str, Any]] = []  # List of {model_response, tool_results, tool_call_names}

        for iteration in range(max_iterations):
            logger.info(
                f"L4.orchestrator [iter:{iteration + 1}] - Starting iteration {iteration + 1}/{max_iterations}"
            )
            stage = f"iteration_{iteration + 1}_prepare_prompt"

            # Prepare current prompt (string) for this iteration to support streaming role parsing
            if iteration == 0:
                current_prompt = initial_prompt
            else:
                stage = f"iteration_{iteration + 1}_rebuild_prompt"
                current_prompt = await prompt_builder.build_conversation_prompt(
                    content, message, conversation_history
                )

            # Get model response with tool schemas (with retry and fallback logic)
            response = None
            response_text = None
            tool_calls = None
            malformed_retries = 0
            max_malformed_retries = 2
            was_streamed = False  # Track if response was streamed (to avoid duplicate emissions)

            # Track the model/provider used by the successful attempt
            successful_model = None
            successful_provider = None

            while malformed_retries <= max_malformed_retries:
                try:
                    # Try primary model (or fallback if we've exhausted retries)
                    current_model = model
                    if malformed_retries == max_malformed_retries:
                        # Last attempt: use fallback model if available
                        fallback_model = config.get("fallback_model")
                        if fallback_model and fallback_model != model:
                            current_model = fallback_model
                            logger.warning(
                                f"L4.tools [orchestrator] - Using fallback model {current_model} due to malformed function call errors"
                            )
                        else:
                            logger.warning(
                                f"L4.tools [orchestrator] - No fallback model available, continuing with {model}"
                            )
                    
                    # If switching to a different model/provider (fallback), rebuild prompt from scratch per spec
                    current_provider = model_selector.get_provider_from_model(current_model)
                    attempt_prompt = current_prompt
                    if current_model != model:
                        try:
                            # Clear caches and rebuild as a fresh turn for the fallback attempt
                            prompt_builder.clear_memory_cache()
                        except Exception:
                            pass
                        # Rebuild the base prompt from the beginning (no in-iteration conversation_history)
                        attempt_prompt = await prompt_builder.build_conversation_prompt(
                            content, message, []
                        )
                    logger.info(f"L4.orchestrator [iter:{iteration}] - Calling model {current_model} (provider: {current_provider})")

                    # For continuation calls, pass previous_response_id (required for reasoning models)
                    iteration_kwargs = dict(model_kwargs)  # Copy to avoid mutating base kwargs
                    if last_response_id and current_provider == "openai":
                        iteration_kwargs["previous_response_id"] = last_response_id
                        logger.debug(f"L4.orchestrator [iter:{iteration}] - Passing previous_response_id={last_response_id}")
                    
                    # For Google models, pass previous response object to preserve thought signatures
                    # and pass tool results explicitly to maintain structured history
                    if iteration > 0 and current_provider == "google":
                         if google_turn_history:
                             iteration_kwargs["google_history"] = google_turn_history
                             # Rebuild prompt excluding all previous turns (model responses + tool results)
                             # because we are passing them as structured objects in google_history.
                             # We need to calculate how many items to exclude from conversation_history.
                             # Each turn adds at least 2 items (Response + Tool Output) to conversation_history.
                             # However, prompt_builder.build_conversation_prompt is what we call.
                             # If we pass google_history, we should probably PASS an empty conversation_history list
                             # to build_conversation_prompt, effectively relying on google_history for the chat turns?
                             # Or slice the history.
                             
                             # Current logic logic for Google slicing was:
                             # if last_turn_items_count > 0:
                             #    sliced_history = conversation_history[:-last_turn_items_count]
                             
                             # With full history, we might need to slice EVERYTHING except system messages?
                             # Actually, prompt_builder merges conversation_history string.
                             # If we use google_history, model_call_google builds the 'contents' object from scratch 
                             # using system instruction + google_history.
                             # So we should pass a CLEANED prompt to avoid duplicating text history + struct history.
                             
                             # STRATEGY: If google_history is used, we pass only the INITIAL prompt (message) 
                             # and assume model_call_google reconstructs the conversation.
                             # But we might have tool results from *previous* iterations that need to be in history?
                             # google_turn_history contains ALL previous turns in this run.
                             
                             # So if we pass google_history, we should essentially effectively reset the text prompt 
                             # to just the initial user message, OR carefully slice.
                             # BUT wait: build_conversation_prompt adds "Current Conversation:\n..." at the end.
                             # If we suppress that, we are good.
                             
                             # Let's try: If google_history is present, we pass an empty list for conversation_history
                             # to build_conversation_prompt? No, that might break other things.
                             # Actually, model_call_google handles:
                             # 1. System Instructions
                             # 2. google_history (turns)
                             # 3. Current Message (from prompt text)
                             
                             # So prompt text should NOT contain the history.
                             # attempt_prompt right now contains the full text history.
                             
                             # If we are sending google_history, we should force attempt_prompt to be just the CURRENT 
                             # iteration's input? 
                             # In a tool loop, the "current input" is implicitly the result of the previous tool, 
                             # but structurally, the "User" turn *IS* the tool result.
                             # Wait, in Gemini, the structure is:
                             # [User: Msg] -> [Model: Call] -> [User: Result] -> [Model: Call] ...
                             
                             # If we have google_history, it contains all previous turns.
                             # The "Current Message" part of model_call_google is intended for the *next* user message.
                             # But in a tool loop, we are often responding to tool outputs.
                             # Tool outputs are typically the *last* thing in google_history (the User turn).
                             # So we might not need a "Current Message" at all if we are just feeding back tool results?
                             
                             # Actually, logic in model_call_google:
                             # - It builds history from google_history.
                             # - Then it appends "Current Message" from sections.
                             
                             # In a Loop:
                             # Iter 0: User Msg -> Model (Call)
                             # Iter 1: google_history=[(Resp, Result)]. 
                             #         We want Model to see [User Msg, Model Call, Tool Result].
                             #         And generate next response.
                             #         So we DON'T want to append "User Msg" again.
                             #         But model_call_google appends "context_text" (system/memory) + "current_msg".
                             
                             # If we pass google_history, we should probably ensure attempt_prompt ONLY contains 
                             # context/memory/system stuff, and NO "Current Message" if it's already covered by history?
                             # BUT the *initial* user message is in google_history only if we put it there?
                             # No, google_turn_history stores *turns* (Response + Result).
                             # It does NOT store the *initial* user message.
                             
                             # So:
                             # Iter 1: 
                             # History: [(ModelResp0, ToolResult0)]
                             # We need: [UserMsg0] -> [ModelResp0] -> [ToolResult0] -> [Model?]
                             # The UserMsg0 is "current_message" in the prompt.
                             # model_call_google puts "current_message" at the END.
                             # That would result in: [ModelResp0] -> [ToolResult0] -> [UserMsg0]. WRONG order.
                             
                             # Correct Gemini structure:
                             # Content(role='user', parts=[UserMsg0])
                             # Content(role='model', parts=[Call0])
                             # Content(role='user', parts=[Result0])
                             
                             # model_call_google logic needs to insert "context_text" (which includes UserMsg0) 
                             # BEFORE google_history?
                             # Currently model_call_google does:
                             # 1. contents_obj.append(context_text)  <-- This usually has the User Msg if prompt_builder put it there
                             # 2. Inject google_history
                             # 3. Append "Current Message" (if found in sections)
                             
                             # prompt_builder.build_conversation_prompt puts the original message in "current_message" section.
                             # So:
                             # context_parts includes WORKING_MEMORY, MEMORY_BANK, CHAT_HISTORY.
                             # It does NOT include CURRENT_MESSAGE_HEADER by default in that loop?
                             # Let's check model_call_google.
                             # "for header in (WORKING_MEMORY_HEADER, MEMORY_BANK_HEADER, CHAT_HISTORY_HEADER): ..."
                             # It does NOT include CURRENT_MESSAGE_HEADER in context_parts.
                             
                             # "3. Current Message -> User role"
                             # It extracts CURRENT_MESSAGE_HEADER.
                             
                             # So:
                             # context_parts (Memory) -> User
                             # google_history -> (Model, User)...
                             # current_msg -> User
                             
                             # This order is: Memory -> History -> CurrentMsg.
                             # This implies CurrentMsg is NEW.
                             # But in a tool loop, the "CurrentMsg" (Initial User Request) happened BEFORE the tool calls.
                             
                             # So for Iteration > 0, we essentially want:
                             # Memory -> InitialMsg -> History -> [Next Model Gen]
                             
                             # If we use google_history, we need to ensure InitialMsg comes *before* it.
                             # We can achieve this by treating InitialMsg as the first item in google_history? No, that's complex.
                             
                             # Or we rely on the fact that "Memory" block usually contains the User Msg?
                             # No, prompt_builder puts it in "current_message".
                             
                             # Solution:
                             # We need to be careful.
                             # If we pass google_history, we are saying "Here are the turns that happened".
                             # The initial message started the chain.
                             # If we put the initial message in "Current Message", it goes to the END.
                             
                             # Ideally, for Gemini multi-turn, we construct:
                             # User: [Initial Prompt + Memory]
                             # Model: [Call]
                             # User: [Result]
                             
                             # If we use google_history for the [Model: Call] -> [User: Result] chain...
                             # We need the [Initial Prompt] to be the *first* User message.
                             # model_call_google creates `contents_obj`.
                             # It appends `context_parts` as the first User message.
                             # If we can shove the Initial Prompt into `context_parts` or `system_instruction`, we are good.
                             # Or we can create a fake "Turn 0" that is just [User: InitialPrompt]? No, turns are (Model, User).
                             
                             # Actually, `prompt_builder`'s `build_conversation_prompt` puts original content in "current_message".
                             # This is extracted by `extract_prompt_sections` in `model_call_google`.
                             
                             # If we are in a tool loop (iter > 0), we should probably NOT pass "current_message" as a separate section at the end.
                             # Instead, we should make sure the Initial Prompt is part of the "context" at the start.
                             
                             # Hack/Fix:
                             # In Iter > 0 for Google:
                             # 1. Pass `google_history`.
                             # 2. Modify `attempt_prompt` so that "Current Message" is moved to "System" or "Memory" section?
                             # Or just prepend it to the first user message?
                             
                             # Let's look at `model_call_google.py` again.
                             # `context_text = "\n\n".join(context_parts).strip()`
                             # `contents_obj.append(types.Content(role="user", parts=[types.Part(text=context_text)]))`
                             
                             # This `context_text` is the FIRST content object.
                             # Then `google_history` turns are appended.
                             # Then `current_msg` is appended.
                             
                             # If `current_msg` is the original user prompt, it shouldn't be at the end.
                             # It should be in `context_text`.
                             
                             # So, in `tool_orchestrator`, if we are using `google_history`, we should:
                             # - Ask `prompt_builder` to put the message in a way that it ends up in context?
                             # - OR, we manually prepend the message to the "context" part of the string?
                             
                             # Simplest approach:
                             # If `google_history` is active (Iter > 0), we can pass `text={"format": ...}` or modify the prompt string?
                             # `extract_prompt_sections` uses headers.
                             # We can string-replace "### Current Message" with "### Context" or something?
                             # Or just "### Task"?
                             
                             # Better: Just rely on the fact that the initial message is static.
                             # If we move it to the "Memory Bank" section or "Working Memory" section in the prompt string, it joins `context_parts`.
                             
                             # So, inside `tool_orchestrator`, for Google Iter > 0:
                             # `attempt_prompt = attempt_prompt.replace("### Current Message", "### Original Request")`
                             # `model_call_google` doesn't recognize "Original Request", so it won't extract it as `current_msg`.
                             # Wait, `extract_prompt_sections` puts unknown sections where?
                             # It usually drops them or puts them in preamble?
                             # No, `extract_prompt_sections` returns a dict of known sections.
                             # Everything else is lost?
                             
                             # Let's check `utils.prompt_printer.extract_prompt_sections`.
                             # It parses specific headers.
                             
                             # If I change the header to `### Chat History`, it will go into `context_parts`.
                             # But `### Chat History` is already used.
                             # `### Memory Bank` is used.
                             
                             # Maybe I should just prepend the content to the `### System Instructions`?
                             # No, that's system role.
                             
                             # Okay, let's modify `model_call_google.py` one more time?
                             # No, I want to stick to the plan if possible.
                             
                             # Alternative:
                             # In `model_call_google.py`:
                             # If `google_history` is present, we treat `current_msg` (if present) as part of the *first* user message (context), 
                             # NOT as a final user message.
                             
                             # Let's check `model_call_google.py` logic again.
                             # It appends `context_text` as a User message.
                             # Then history.
                             # Then `current_msg` as User message.
                             
                             # If I can merge `current_msg` into `context_text`, I win.
                             # I can do this in `tool_orchestrator` by manipulating the prompt string.
                             # `attempt_prompt = attempt_prompt.replace("### Current Message", "### Working Memory")` 
                             # (Working Memory is in `context_parts`).
                             # That seems safest. Append it to Working Memory.
                             
                             attempt_prompt = attempt_prompt.replace(CURRENT_MESSAGE_HEADER, f"{WORKING_MEMORY_HEADER}\n(Original Request)")
                             
                             # Wait, duplicate headers? `extract_prompt_sections` might overwrite.
                             # It uses regex to split.
                             
                             # Let's just rely on the fact that `prompt_builder` separates them.
                             # If I replace `### Current Message` with something that isn't a header, where does it go?
                             # `extract_prompt_sections` splits by headers. Text *before* any header is preamble.
                             # Text *under* a header belongs to that header.
                             
                             # If I remove `### Current Message` header but keep the text, it will belong to the *previous* header.
                             # In `build_conversation_prompt`, the order is:
                             # System, Memory, Current Message, Channel, Attachments...
                             
                             # `sections` dict key order matters in `build_prompt`?
                             # `utils.prompt_printer.build_prompt` iterates specific order?
                             # Let's assume standard order.
                             
                             # Actually, simpler fix in `tool_orchestrator`:
                             # If Iter > 0 and Google:
                             # 1. Pass `google_history`.
                             # 2. Pass `conversation_history=[]` to `prompt_builder` (so we don't get text duplicates of history).
                             # 3. BUT `prompt_builder` still adds "Current Message".
                             # 4. We need to suppress "Current Message" from being a *separate final turn*.
                             
                             # Actually, `model_call_google` logic I just wrote:
                             # It puts `context_parts` (User) -> `google_history` (Model, User)... -> `current_msg` (User).
                             
                             # If `current_msg` is "Check weather...", and history has "Call Weather", "Weather Result".
                             # Result:
                             # User: [Memory]
                             # Model: Call Weather
                             # User: Weather Result
                             # User: Check weather...  <-- THIS IS BAD.
                             
                             # We want "Check weather..." to be in the FIRST User block.
                             
                             # So, in `model_call_google.py`, I should have handled this.
                             # "If google_history is present, maybe prepend current_msg to context_text instead of appending at end?"
                             
                             # Too late to edit `model_call_google.py` without another tool call.
                             # I should fix it there. It's a logic bug in my implementation of Step 1.
                             
                             # I will create a new TODO to fix `model_call_google.py` logic regarding `current_msg` placement when `google_history` is used.
                             # This is better than hacking string replacements.
                             
                             pass

                    elif last_response_object and iteration > 0 and current_provider == "openai":
                         # Legacy support for OpenAI continuation
                         pass
                         
                    # Update for Google History logic:
                    # If we are in Google mode, we want to rely on structured history.
                    # We should strip the text-based history from the prompt to avoid duplication/confusion.
                    if iteration > 0 and current_provider == "google":
                        # Rebuild prompt with EMPTY text history, because google_history handles it.
                        # But we keep the original message/context.
                        attempt_prompt = await prompt_builder.build_conversation_prompt(
                            content, message, [] 
                        )
                        # Note: We will need to fix the 'current_message' placement in model_call_google.py
                        # or else it will appear at the end.
                        
                        # Temporary hack until I fix model_call_google.py:
                        # Force 'current_message' to be empty in the prompt sections, and manually inject it into Memory Bank?
                        # No, let's just fix the model call file next.


                    # Immediately keep UI in thinking state without emitting placeholder deltas
                    # Unified non-streaming call for all providers
                    response, actual_model = await _call_model_with_circuit_breaker(
                        model_selector,
                        attempt_prompt,
                        current_model,
                        tool_schemas,
                        config,
                        iteration_kwargs,
                        rebuild_prompt_func=_rebuild_prompt_for_model,
                        event_cb=_emit,
                    )

                    # Check if response was streamed (SynthesizedResponse indicates streaming was used)
                    if response and response.__class__.__name__ == "SynthesizedResponse":
                        was_streamed = True
                        logger.debug(f"L4.orchestrator [iter:{iteration}] - Response was streamed (events already emitted)")

                    # Extract response ID for continuation calls (required for reasoning models)
                    if response and hasattr(response, 'id'):
                        current_response_id = getattr(response, 'id', None)
                        if current_response_id:
                            last_response_id = current_response_id
                            logger.debug(f"L4.orchestrator [iter:{iteration}] - Captured response_id={last_response_id}")

                    # Use actual_model instead of current_model for extraction to handle circuit breaker fallback
                    actual_provider = model_selector.get_provider_from_model(actual_model)
                    if actual_model != current_model:
                        logger.info(f"L4.orchestrator [iter:{iteration}] - Circuit breaker used fallback model {actual_model}")

                    # Extract response text and tool calls with enhanced error handling
                    try:
                        response_text = model_selector.extract_response_text(
                            response, actual_provider, actual_model
                        )
                        logger.debug(f"L4.orchestrator [iter:{iteration}] - Extracted response text: {len(response_text or '')} chars")
                        
                        # Validate xAI tool format - catch hallucinated XML tool calls
                        if actual_provider == "xai" and response_text:
                            if "<function_calls>" in response_text and "<invoke name=" in response_text:
                                logger.warning(f"L4.orchestrator [iter:{iteration}] - xAI model hallucinated Anthropic XML tool format! "
                                             f"This suggests prompt leakage. Response preview: {response_text[:200]}...")
                                # Strip the hallucinated tool calls to prevent execution
                                import re
                                response_text = re.sub(r'<function_calls>.*?</function_calls>', '', response_text, flags=re.DOTALL).strip()
                                logger.info(f"L4.orchestrator [iter:{iteration}] - Stripped hallucinated tool calls from xAI response")
                                
                    except Exception as e:
                        logger.error(f"L4.orchestrator [iter:{iteration}] - Failed to extract response text: {e}")
                        response_text = None
                    
                    try:
                        if logger.isEnabledFor(logging.DEBUG):
                            if hasattr(response, "output"):
                                outputs = list(getattr(response, "output") or [])
                                logger.debug(
                                    "L4.orchestrator [debug] - Response has output items=%d",
                                    len(outputs),
                                )
                                for idx, output_item in enumerate(outputs):
                                    item_type = getattr(output_item, "type", "no-type")
                                    logger.debug(
                                        "L4.orchestrator [debug] - Output[%d] type=%s",
                                        idx,
                                        item_type,
                                    )
                                    attr_names: List[str] = []
                                    for attr_name in dir(output_item):
                                        if attr_name.startswith("_"):
                                            continue
                                        if attr_name in ("model_fields", "model_computed_fields"):
                                            continue
                                        try:
                                            value = getattr(output_item, attr_name)
                                        except Exception:
                                            continue
                                        if callable(value):
                                            continue
                                        attr_names.append(attr_name)
                                    if attr_names:
                                        logger.debug(
                                            "L4.orchestrator [debug] - Output[%d] attrs=%s",
                                            idx,
                                            ", ".join(attr_names[:8]),
                                        )
                                    content_items = getattr(output_item, "content", None)
                                    if content_items:
                                        for cidx, content_item in enumerate(content_items):
                                            logger.debug(
                                                "L4.orchestrator [debug] - Content[%d] type=%s",
                                                cidx,
                                                getattr(content_item, "type", "no-type"),
                                            )
                            else:
                                logger.debug("L4.orchestrator [debug] - Response has no output attribute")
                                attr_list = [
                                    attr for attr in dir(response) if not attr.startswith("_")
                                ]
                                if attr_list:
                                    logger.debug(
                                        "L4.orchestrator [debug] - Response attrs=%s",
                                        ", ".join(attr_list[:10]),
                                    )

                        tool_calls = model_selector.extract_function_calls(
                            response, actual_provider
                        )
                        logger.debug(
                            "L4.orchestrator [iter:%s] - Extracted %d function calls",
                            iteration,
                            len(tool_calls or []),
                        )

                        if logger.isEnabledFor(logging.DEBUG):
                            for idx, call in enumerate(tool_calls or []):
                                call_name = call.get("name", "unknown")
                                call_args = call.get("arguments", {})
                                logger.debug(
                                    "L4.orchestrator [iter:%s] - Function call %d (%s) arg_keys=%s",
                                    iteration,
                                    idx + 1,
                                    call_name,
                                    sorted(call_args.keys()) if isinstance(call_args, dict) else type(call_args).__name__,
                                )

                    except GoogleResponseError as e:
                        # Bubble up Google malformed function call to trigger retry logic
                        raise
                    except Exception as e:
                        logger.error(f"L4.orchestrator [iter:{iteration}] - Failed to extract function calls: {e}")
                        tool_calls = []
                    
                    # Removed post-call reasoning escalation; reasoning/verbosity are set once per turn
                    # based on the dynamic optimizer result and remain constant for the session.

                    successful_model = actual_model
                    successful_provider = actual_provider

                    # Success - break out of retry loop
                    break
                    
                except GoogleResponseError as e:
                    if "MALFORMED_FUNCTION_CALL" in str(e):
                        malformed_retries += 1
                        logger.warning(
                            f"L4.tools [orchestrator] - Malformed function call detected (attempt {malformed_retries}/{max_malformed_retries + 1})"
                        )
                        if malformed_retries <= max_malformed_retries:
                            continue  # Retry
                        else:
                            # All retries exhausted
                            logger.error(
                                f"L4.tools [orchestrator] - All retries exhausted for malformed function call"
                            )
                            raise ToolOrchestrationError(
                                "Model repeatedly generated malformed function calls. Please try rephrasing your request."
                            ) from e
                    else:
                        # Different Google error, don't retry
                        raise ToolOrchestrationError(f"Google API error: {e}") from e
                        
                except Exception as e:
                    logger.error(
                        f"L4.tools [orchestrator] - Response parsing failed: {e}",
                        exc_info=True,
                    )
                    # Attempt a single fallback on parse/extraction errors by rebuilding prompt
                    try:
                        fallback_model = config.get("fallback_model")
                        if isinstance(fallback_model, str) and fallback_model and fallback_model != current_model:
                            logger.info(
                                f"L4.tools [orchestrator] - Trying fallback model due to parse failure: {fallback_model}"
                            )
                            try:
                                fb_prompt = await _rebuild_prompt_for_model(fallback_model)
                            except Exception:
                                fb_prompt = initial_prompt if iteration == 0 else current_prompt
                            fb_resp, fb_actual_model = await _call_model_with_circuit_breaker(
                                model_selector,
                                fb_prompt,
                                fallback_model,
                                tool_schemas,
                                config,
                                model_kwargs,
                                rebuild_prompt_func=_rebuild_prompt_for_model,
                            )
                            # Try to extract from fallback response; let exceptions bubble to outer handler
                            fb_actual_provider = model_selector.get_provider_from_model(fb_actual_model)
                            response_text = model_selector.extract_response_text(
                                fb_resp, fb_actual_provider, fb_actual_model
                            )
                            tool_calls = model_selector.extract_function_calls(
                                fb_resp, fb_actual_provider
                            )
                            successful_model = fb_actual_model
                            successful_provider = fb_actual_provider
                            # Success with fallback: break out of retry loop
                            break
                        # No fallback configured or same as current; re-raise
                        raise
                    except Exception as fb_err:
                        logger.error(
                            f"L4.tools [orchestrator] - Fallback after parse failure also failed: {fb_err}",
                            exc_info=True,
                        )
                        raise ToolOrchestrationError(f"Response parsing failed: {e}") from e

            # Add model response to conversation (for logging/UI)
            response_history_index: Optional[int] = None
            history_len_before_turn = len(conversation_history)
            if response_text:
                conversation_history.append(f"Theo: {response_text}")
                response_history_index = len(conversation_history) - 1

            # Update tracking variables for next iteration
            last_response_object = response
            last_tool_results = []
            last_tool_call_names = []
            
            # Collect tool results for next iteration
            # We need to extract the 'output' from the conversation history or executed_tools?
            # executed_tools is just a list of names.
            # We need the actual results.
            # The results are added to conversation_history as strings: "Tool: ... returned: ..."
            # But we need the structured output for model_call_google.
            
            # We can't easily get structured output from conversation_history strings.
            # We should track them during execution.
            # Let's modify the execution loop to collect them.
            
            # Since we can't modify the execution loop in this ReplaceFileContent call easily (it's huge),
            # we will rely on the fact that we can't easily get structured results here without more changes.
            # However, we can try to reconstruct a minimal result or modify the execution loop in a separate step.
            
            # Actually, let's just track the count for now to enable slicing.
            # And we need to capture tool results.
            # Let's initialize a list before the execution loop.
            pass

            if consecutive_tool_only_iters >= max_tool_only_iters:
                logger.error(
                    f"L4.tools [loop_guard] - Reached max tool-only iterations ({max_tool_only_iters}); stopping to prevent infinite loop"
                )
                end_processing()
                return (
                    "🔄 LOOP GUARD: The model kept requesting tools without producing an answer. "
                    "I stopped to prevent an infinite loop. Please refine the request or run the needed action explicitly."
                )

            # Emit reasoning summaries and tool preambles for all models
            # SKIP for streamed responses (events already emitted during streaming)
            try:
                if event_cb and not was_streamed:
                    # For OpenAI GPT-5, try to get reasoning summary first
                    reasoning_text = None
                    if provider == "openai" and str(model).lower().startswith("gpt-5"):
                        from layer1_chatbot.model_call_openai import get_reasoning_summary
                        reasoning_text = get_reasoning_summary(response)
                        if logger.isEnabledFor(logging.DEBUG):
                            logger.debug(
                                "L4.orchestrator [iter:%s] - Reasoning summary captured (chars=%d)",
                                iteration,
                                len(reasoning_text or ""),
                            )
                    
                    # Fall back to tool preamble extraction from response text
                    if not reasoning_text and isinstance(response_text, str) and response_text.strip():
                        rt = response_text.strip().replace("\n", " ")
                        import re
                        
                        # Look for tool preamble patterns (before tool calls)
                        preamble_patterns = [
                            r"^(I'll|I will|Let me|I need to|I should|I'm going to|I plan to)\s+.{10,200}",
                            r"^(First|Next|Now|Then),?\s+.{10,200}",
                            r"^(To .{5,50}),?\s+I.{10,200}",
                        ]
                        
                        for pattern in preamble_patterns:
                            match = re.match(pattern, rt, re.IGNORECASE)
                            if match:
                                reasoning_text = match.group(0)
                                break
                        
                        # Fallback: first sentence if it looks like a plan/explanation
                        if not reasoning_text:
                            parts = re.split(r"(?<=[\.!?])\s+", rt)
                            first_sentence = parts[0] if parts else rt[:360]
                            if len(first_sentence) > 20 and any(word in first_sentence.lower() 
                                for word in ["let", "will", "going", "need", "should", "plan", "first"]):
                                reasoning_text = first_sentence[:360]
                    
                    # Emit reasoning if found (suppress for OpenAI GPT-5 live stream)
                    if reasoning_text:
                        cleaned_summary = reasoning_text.strip()
                        if len(cleaned_summary) > 2:
                            if provider == "openai" and str(model).lower().startswith("gpt-5"):
                                if cleaned_summary != last_reasoning_summary:
                                    try:
                                        if last_reasoning_summary:
                                            await _emit({"type": "reasoning_summary_clear"})
                                        await _emit({"type": "reasoning_summary", "delta": cleaned_summary})
                                        last_reasoning_summary = cleaned_summary
                                    except Exception as emit_err:
                                        logger.debug(
                                            "L4.orchestrator [iter:%s] - Failed to emit reasoning_summary: %s",
                                            iteration,
                                            emit_err,
                                        )
                            else:
                                prefix = "Planning: " if tool_calls else "Reasoning: "
                                final_text = f"{prefix}{cleaned_summary}"
                                if logger.isEnabledFor(logging.DEBUG):
                                    logger.debug(
                                        "L4.orchestrator [iter:%s] - Emitting reasoning delta (chars=%d)",
                                        iteration,
                                        len(final_text or ""),
                                    )
                                await _emit({"type": "reasoning.delta", "text": final_text})
            except Exception as e:
                logger.debug(f"L4.orchestrator [iter:{iteration}] - Failed to emit reasoning: {e}")

            # Safety check: Prevent infinite loops with too many iterations
            # Note: This check is placed BEFORE tool execution to ensure it can trigger
            if iteration >= 999:
                logger.error(f"L4.orchestrator [iter:{iteration}] - Maximum iterations exceeded, terminating to prevent infinite loop")
                error_response = f"I've reached the maximum number of processing iterations ({iteration + 1}). This might indicate a problem with tool execution or an infinite loop. Please try rephrasing your request or check if the tools are working correctly."
                conversation_history.append(f"System: {error_response}")
                end_processing()
                return error_response
            
            # If no tool calls, finish the turn unless we just auto-sent an image
            if not tool_calls:
                logger.info(
                    f"L4.orchestrator [iter:{iteration}] - No tool calls, returning response"
                )
                if (not response_text or not str(response_text).strip()) and image_summary_records:
                    response_text = _format_image_summary(image_summary_records)
                    skip_final_streaming = True
                    if response_history_index is not None:
                        conversation_history[response_history_index] = f"Theo: {response_text}"
                    else:
                        conversation_history.append(f"Theo: {response_text}")
                        response_history_index = len(conversation_history) - 1
                # If an image was auto-sent in the immediately preceding iteration, do not end turn here.
                # Allow the loop to continue so the model can proceed with the rest of the plan.
                if image_autosent_recently:
                    logger.info("L4.orchestrator - Skipping terminal turn due to recent image auto-send")
                    image_autosent_recently = False
                else:
                    end_processing()
                    # If streaming is enabled, perform a provider-native streaming call for final answer
                    # without tools, and stream tokens directly to UI. This preserves tool use semantics.
                    try:
                        stream_cfg = (config.get("streaming") or {}) if isinstance(config, dict) else {}
                    except Exception:
                        stream_cfg = {}
                    streaming_enabled = bool(stream_cfg.get("enabled", True))
                    if skip_final_streaming:
                        streaming_enabled = False
                    # Skip final streaming if main loop already streamed - prevents duplicate API calls
                    # and "message sent twice" issues when full prompt is re-sent as user content
                    if was_streamed:
                        streaming_enabled = False
                        logger.debug("L4.orchestrator - Skipping final streaming (main loop already streamed)")
                    # Avoid streaming in tests to keep runs deterministic
                    try:
                        import os as _os
                        if _os.environ.get("PYTEST_CURRENT_TEST") or _os.environ.get("THEO_TEST_MODE") == "1":
                            streaming_enabled = False
                    except Exception:
                        pass
                    if streaming_enabled:
                        try:
                            await _emit({"type": "status", "value": "streaming"})
                        except Exception:
                            pass
                        try:
                            # Inject previous response for Gemini multi-step context
                            final_model_kwargs = dict(model_kwargs)
                            if response and provider == "google":
                                final_model_kwargs["google_prev_response"] = response

                            # Enforce no-tools on the final streamed call to avoid disruption
                            streamed_text = await _stream_model_with_circuit_breaker(
                                model_selector,
                                # Rebuild a fresh prompt for the selected model (no conversation_history section)
                                # to match provider expectations
                                attempt_prompt=current_prompt,
                                model=(successful_model or model),
                                config=config,
                                # Explicitly disable tools for streaming finalization
                                tool_schemas=[],
                                model_kwargs=final_model_kwargs,
                                on_event=_emit,
                            )
                            # Provide a sane fallback when stream yields nothing
                            if not streamed_text:
                                streamed_text = response_text or ""
                            return streamed_text or "I encountered an issue generating a response. Please try again."
                        except Exception as se:
                            logger.debug(f"L4.orchestrator [stream-final] - Streaming fallback: {se}")
                            # fall through to non-streaming result below
                    # Non-streaming path (or when streaming disabled)
                    await _emit({"type": "status", "value": "responding"})
                    # Attempt a single fallback if needed
                    if not response_text:
                        try:
                            fallback_model = config.get("fallback_model")
                            if isinstance(fallback_model, str) and fallback_model and fallback_model != (successful_model or model):
                                try:
                                    fb_prompt = await _rebuild_prompt_for_model(fallback_model)
                                except Exception:
                                    fb_prompt = initial_prompt if iteration == 0 else current_prompt
                                fb_resp, fb2_model = await _call_model_with_circuit_breaker(
                                    model_selector,
                                    fb_prompt,
                                    fallback_model,
                                    tool_schemas,
                                    config,
                                    model_kwargs,
                                    rebuild_prompt_func=_rebuild_prompt_for_model,
                                )
                                try:
                                    response_text = model_selector.extract_response_text(
                                        fb_resp, model_selector.get_provider_from_model(fb2_model), fb2_model
                                    ) or ""
                                except Exception:
                                    response_text = ""
                        except Exception:
                            pass
                    
                    # Include tool execution context in error message if tools were executed
                    if not response_text:
                        error_msg = "I encountered an issue generating a response."
                        if executed_tools:
                            unique_tools = list(dict.fromkeys(executed_tools))  # Deduplicate while preserving order
                            tool_summary = ", ".join(unique_tools[:5])  # Limit to first 5 tools
                            if len(unique_tools) > 5:
                                tool_summary += f", and {len(unique_tools) - 5} more"
                            error_msg += f" However, I successfully executed: {tool_summary}."
                        error_msg += " Please try again or rephrase your request."
                        return error_msg
                    
                    return response_text

            # Preserve original tool_calls for providers (e.g., OpenAI) that require
            # function_call_output for every function call in the prior response.
            # This snapshot allows us to include placeholder outputs for any calls
            # we choose to skip via de-duplication or safety guards.
            try:
                original_tool_calls = list(tool_calls or [])
            except Exception:
                original_tool_calls = tool_calls

            # Deduplicate tool calls within this iteration to avoid executing duplicates
            try:
                deduped_tool_calls = []
                seen_call_keys: set[tuple[str, str]] = set()
                for tc in (tool_calls or []):
                    if not isinstance(tc, dict):
                        continue
                    tn = tc.get("name", "unknown")
                    if tn in MULTI_RUN_TOOLS:
                        deduped_tool_calls.append(tc)
                        continue
                    args = tc.get("arguments", {})
                    try:
                        ak = json.dumps(args, sort_keys=True)
                    except Exception:
                        ak = str(args)
                    key = (tn, ak)
                    if key in seen_call_keys:
                        continue
                    seen_call_keys.add(key)
                    deduped_tool_calls.append(tc)
                tool_calls = deduped_tool_calls
            except Exception as e:
                logger.debug(f"L4.orchestrator [iter:{iteration}] - Tool-call deduplication failed: {e}")

            

            # Loop detection: Check for repeated tool calls on same files
            # Also apply stricter de-duplication for identical tool+args signatures
            filtered_tool_calls = []
            # Per-iteration limit for define_form calls to reduce UI churn
            define_form_calls_this_iter: int = 0
            for tool_call in tool_calls:
                tool_name = tool_call.get("name", "unknown")
                file_path = None
                args = tool_call.get("arguments", {})
                if isinstance(args, dict):
                    file_path = args.get("file_path") or args.get("path") or args.get("filename")

                try:
                    if tool_name == "save_form_submission" and isinstance(args, dict):
                        reduced = {
                            "form_id": args.get("form_id"),
                            "room_id": args.get("room_id"),
                        }
                        args_key = json.dumps(reduced, sort_keys=True)
                    else:
                        args_key = json.dumps(args, sort_keys=True)
                except Exception:
                    args_key = str(args)
                signature = (tool_name, args_key)
                previous_signature = last_tool_signatures.get(tool_name)
                args_changed = previous_signature != args_key
                skip_reason: Optional[str] = None
                skip_reason_delta: Optional[str] = None
                
                # If args changed, clear any disabled signatures for this tool to allow fresh attempts
                if args_changed and tool_name not in NON_DISABLE_TOOLS:
                    # Remove all disabled signatures for this tool
                    to_remove = [sig for sig in disabled_signatures if sig[0] == tool_name]
                    for sig in to_remove:
                        disabled_signatures.discard(sig)
                    if to_remove:
                        logger.debug(
                            f"L4.tools [deduplication] - Cleared {len(to_remove)} disabled signature(s) for tool '{tool_name}' due to arg change"
                        )

                if _should_skip_signature(tool_name, signature, disabled_signatures) and not args_changed:
                    logger.info(
                        f"L4.tools [deduplication] - Skipping disabled signature for tool '{tool_name}'"
                    )
                    skip_reason = (
                        f"Tool '{tool_name}' call skipped because the same request already ran earlier this turn."
                    )

                if skip_reason is None and file_path:
                    if tool_name == "read_part_of_file" and isinstance(args, dict) and not args.get("search"):
                        try:
                            start_ln = int(args.get("start_line", 1) or 1)
                            end_ln = args.get("end_line")
                            end_ln = int(end_ln) if isinstance(end_ln, int) or (isinstance(end_ln, str) and end_ln.isdigit()) else None
                        except Exception:
                            start_ln = 1
                            end_ln = None

                        key = (tool_name, file_path)
                        state = pagination_state.get(
                            key, {"last_start": 0, "last_end": 0, "stalled": 0, "ts": time.time()}
                        )

                        last_start = int(state.get("last_start") or 0)
                        last_end = int(state.get("last_end") or 0)
                        advancing = start_ln >= last_end if last_end else start_ln > last_start

                        state["stalled"] = 0 if advancing else int(state.get("stalled", 0)) + 1
                        state["last_start"] = start_ln
                        if end_ln:
                            state["last_end"] = end_ln
                        state["ts"] = time.time()
                        pagination_state[key] = state

                        if state["stalled"] >= max_same_tool_file_calls:
                            logger.error(
                                f"L4.tools [loop_detection] - LOOP DETECTED: Paginated reads stalled for '{file_path}' ({state['stalled']} non-advancing calls)"
                            )
                            error_response = (
                                f"🔄 LOOP DETECTED: I've attempted to read parts of '{file_path}' "
                                f"{state['stalled']} times without advancing. This suggests I'm stuck re-reading the same range. "
                                f"I'll stop here to prevent an infinite loop."
                            )
                            conversation_history.append(f"System: {error_response}")
                            end_processing()
                            return error_response
                        if advancing:
                            logger.debug(
                                f"L4.tools [loop_detection] - Pagination advancing for {file_path}: {start_ln}-{end_ln or '?'}; not a loop"
                            )
                    else:
                        recent_calls = [
                            (t, f, i, a)
                            for t, f, i, a in tool_call_history[-10:]
                            if t == tool_name and f == file_path and i >= iteration - 5
                        ]
                        same_arg_calls = []
                        try:
                            if tool_name in ("update_file",):
                                def key_args(a: dict):
                                    if not isinstance(a, dict):
                                        return None
                                    return (
                                        a.get("old_text"),
                                        a.get("new_text"),
                                        a.get("start_line"),
                                        a.get("end_line"),
                                        a.get("line_number"),
                                        a.get("case_sensitive"),
                                    )

                                current_key = key_args(args)
                                same_arg_calls = [c for c in recent_calls if key_args(c[3]) == current_key]
                            else:
                                same_arg_calls = recent_calls
                        except Exception:
                            same_arg_calls = recent_calls

                        unique_iters = len({i for _, _, i, _ in same_arg_calls})
                        if unique_iters >= max_same_tool_file_calls:
                            logger.error(
                                f"L4.tools [loop_detection] - LOOP DETECTED: Tool '{tool_name}' called {unique_iters} times on '{file_path}' with same arguments in recent iterations"
                            )
                            error_response = (
                                f"🔄 LOOP DETECTED: I've attempted to use the '{tool_name}' tool on '{file_path}' "
                                f"{unique_iters} times without making progress. This suggests the tool isn't working "
                                f"as expected or the modifications aren't taking effect. I'll stop here to prevent an "
                                f"infinite loop. Please check the file manually or try a different approach."
                            )
                            conversation_history.append(f"System: {error_response}")
                            end_processing()
                            return error_response

                tool_call_history.append((tool_name, file_path, iteration, args))

                try:
                    recent_sig_calls = [
                        1
                        for t, _f, _i, a in tool_call_history[-20:]
                        if t == tool_name
                        and (
                            (json.dumps(a, sort_keys=True) if isinstance(a, dict) else str(a))
                            == args_key
                        )
                    ]
                    threshold = STRICT_DUPLICATE_THRESHOLDS.get(
                        tool_name, duplicate_signature_threshold
                    )

                    if (
                        skip_reason is None
                        and tool_name == "bash"
                        and len(recent_sig_calls) >= 2
                        and not args_changed
                    ):
                        _maybe_disable_signature(tool_name, signature, disabled_signatures)
                        logger.warning(
                            "L4.tools [deduplication] - Disabled bash for repeated identical calls (>= 2)"
                        )
                        skip_reason_delta = "Skipping repeated bash call…"
                        skip_reason = (
                            "Repeated bash call detected without progress; skipping to avoid a loop."
                        )
                    if (
                        skip_reason is None
                        and tool_name not in MULTI_RUN_TOOLS
                        and len(recent_sig_calls) >= threshold
                        and not args_changed
                    ):
                        _maybe_disable_signature(tool_name, signature, disabled_signatures)
                        logger.warning(
                            f"L4.tools [deduplication] - Disabled tool '{tool_name}' for repeated identical calls (>= {threshold})"
                        )
                        if tool_name == "get_connection_status":
                            skip_reason_delta = (
                                "You've already checked Plaid status — move on to linking or ask the user."
                            )
                            skip_reason = (
                                "Repeated get_connection_status call detected. Generate a new Plaid link with connect_bank_account() or ask the user how to proceed instead of re-running the status check."
                            )
                        else:
                            skip_reason_delta = f"Skipping repeated identical tool call: {tool_name}…"
                            skip_reason = (
                                f"Tool '{tool_name}' produced the same request too many times in a row; skipping this repeat to prevent a loop."
                            )
                except Exception:
                    pass

                if skip_reason is None and tool_name == "define_form":
                    if (define_form_calls_this_iter >= 1 or define_form_calls_total >= 4) and not args_changed:
                        skip_reason_delta = "Skipping extra define_form calls to avoid loops…"
                        skip_reason = (
                            "Repeated define_form with unchanged schema skipped to prevent a loop. Adjust the fields or proceed to show_form."
                        )
                    else:
                        define_form_calls_this_iter += 1
                        define_form_calls_total += 1

                if skip_reason is None and tool_name == "save_form_submission":
                    try:
                        form_id = None
                        room_for_key = None
                        if isinstance(args, dict):
                            form_id = str(args.get("form_id") or "")
                            room_for_key = args.get("room_id")
                        if not room_for_key:
                            try:
                                room_for_key = str(
                                    getattr(getattr(message, "channel", None), "id", None) or ""
                                )
                            except Exception:
                                room_for_key = ""
                        key = (form_id or "", str(room_for_key or ""))
                        count = form_save_counts.get(key, 0)
                        if count >= 1:
                            skip_reason_delta = (
                                f"Skipping duplicate save_form_submission for form '{form_id}' this turn…"
                            )
                            skip_reason = f"save_form_submission for form '{form_id}' already ran earlier this turn."
                        else:
                            form_save_counts[key] = count + 1
                    except Exception:
                        pass

                last_tool_signatures[tool_name] = args_key

                if skip_reason is not None:
                    await _apply_guard_skip(
                        tool_call,
                        skip_reason,
                        reasoning_text=skip_reason_delta or skip_reason,
                    )
                else:
                    tool_call.pop("__guard_skip", None)
                    tool_call.pop("__guard_reason", None)

                filtered_tool_calls.append(tool_call)

            # Replace tool_calls with filtered list after de-dup loop
            tool_calls = filtered_tool_calls

            # Execute tools with provider-aware parallelism (Groq: no parallel tool calls)
            try:
                logger.debug(f"L4.orchestrator [iter:{iteration}] - Starting execution of {len(tool_calls)} tools")
                
                # Validate tool calls before execution
                valid_tool_calls = []
                for i, tool_call in enumerate(tool_calls):
                    if not isinstance(tool_call, dict):
                        logger.error(f"L4.orchestrator [iter:{iteration}] - Invalid tool call {i+1}: not a dictionary - {type(tool_call)}")
                        continue
                    
                    tool_name = tool_call.get("name")
                    if not tool_name:
                        logger.error(f"L4.orchestrator [iter:{iteration}] - Invalid tool call {i+1}: missing 'name' field")
                        continue
                    
                    # Coerce JSON-string arguments into a dict for robustness
                    try:
                        args_val = tool_call.get("arguments", {})
                        if isinstance(args_val, str):
                            import json as _json
                            try:
                                args_val = _json.loads(args_val) or {}
                                tool_call["arguments"] = args_val
                            except Exception:
                                # If it isn't valid JSON, drop arguments to empty dict
                                tool_call["arguments"] = {}
                        if not isinstance(tool_call.get("arguments", {}), dict):
                            logger.error(f"L4.orchestrator [iter:{iteration}] - Invalid tool call {i+1}: 'arguments' not a dictionary after coercion")
                            continue
                    except Exception:
                        logger.error(f"L4.orchestrator [iter:{iteration}] - Failed to coerce tool arguments for call {i+1}")
                        continue
                    
                    valid_tool_calls.append(tool_call)
                    logger.debug(f"L4.orchestrator [iter:{iteration}] - Valid tool call {i+1}: {tool_name}")
                
                # Forms are now explicitly managed - no auto-transformation
                # Users must explicitly call show_form to display a form

                # If provider is Groq, only execute ONE tool per iteration (no parallel tool calls)
                try:
                    if (successful_provider or provider) == "groq" and len(valid_tool_calls) > 1:
                        logger.info(
                            f"L4.orchestrator [iter:{iteration}] - Groq provider detected; executing only one tool this turn (of {len(valid_tool_calls)})"
                        )
                        # Prioritize by the existing sort order, keep only the first
                        valid_tool_calls = [valid_tool_calls[0]]
                except Exception:
                    # If detection fails, proceed without restriction
                    pass

                if not valid_tool_calls:
                    logger.warning(f"L4.orchestrator [iter:{iteration}] - No valid tool calls to execute")
                    parallel_results = []
                else:
                    # Ensure dependency-safe ordering: run define_form before show_form/save_form_submission
                    try:
                        def _priority(tc: Dict[str, Any]) -> int:
                            name = (tc or {}).get("name", "")
                            if name == "define_form":
                                return 0
                            if name in ("show_form", "save_form_submission"):
                                return 1
                            return 2
                        valid_tool_calls.sort(key=_priority)
                    except Exception:
                        pass
                    # Assign deterministic UI IDs per tool call so the web UI can
                    # stream icons accurately for concurrent calls (even with same name)
                    try:
                        _now_ms = int(time.time() * 1000)
                        for i, t in enumerate(valid_tool_calls):
                            try:
                                # Prefer provider call_id when present; else generate
                                ui_id = t.get("call_id") or t.get("_ui_id")
                                if not ui_id:
                                    ui_id = f"tc-{iteration}-{i+1}-{_now_ms}-{_uuid.uuid4().hex[:8]}"
                                t["_ui_id"] = ui_id
                            except Exception:
                                # Best-effort; fallback silent
                                pass
                    except Exception:
                        pass
                    # Emit start events for tools
                    for t in valid_tool_calls:
                        try:
                            payload = {
                                "type": "tool.start",
                                "name": t.get("name", "unknown"),
                                "arguments": t.get("arguments", {}),
                                # Stable per-call identifier for the UI
                                "id": t.get("_ui_id") or t.get("call_id"),
                            }
                            guard_reason = t.get("__guard_reason") if t.get("__guard_skip") else None
                            if guard_reason:
                                payload["guard_reason"] = guard_reason
                            logger.info(
                                "L4.orchestrator [run:%s iter:%s] tool.start name=%s id=%s",
                                run_id,
                                iteration,
                                payload.get("name"),
                                payload.get("id"),
                            )
                            await _emit(payload)
                            # Also surface a short preamble: only show tool usage label
                            try:
                                tn = t.get("name", "unknown")
                                if guard_reason:
                                    await _emit({"type": "reasoning.delta", "text": guard_reason})
                                else:
                                    await _emit({"type": "reasoning.delta", "text": f"Running {tn}…"})
                            except Exception:
                                pass
                        except Exception:
                            pass
                    
                    reordered_tool_calls = list(valid_tool_calls)

                    # Execute tools: parallel for most providers; sequential for Groq
                    if (successful_provider or provider) == "groq":
                        parallel_results = []
                        for i, tool_call in enumerate(reordered_tool_calls):
                            try:
                                res = await execute_single_tool(i + 1, tool_call, tool_context)
                            except Exception as e:
                                res = e
                            parallel_results.append(res)
                    else:
                        tasks = [
                            execute_single_tool(i + 1, tool_call, tool_context)
                            for i, tool_call in enumerate(reordered_tool_calls)
                        ]
                        logger.debug(f"L4.orchestrator [iter:{iteration}] - Executing {len(tasks)} validated tool calls in parallel")
                        parallel_results = await asyncio.gather(
                            *tasks, return_exceptions=True
                        )

                # Process results and handle web interactions
                tool_results = []
                handled_tool_actions: set[int] = set()
                pending_image_autosends: List[Tuple[Dict[str, Any], Dict[str, Any]]] = []
                auto_followup_messages: List[str] = []
                logger.debug(f"L4.orchestrator [iter:{iteration}] - Processing {len(parallel_results)} tool results")
                
                for i, result_data in enumerate(parallel_results):
                    try:
                        if isinstance(result_data, Exception):
                            # Get tool name safely
                            tool_name = "unknown"
                            if i < len(reordered_tool_calls):
                                tool_name = reordered_tool_calls[i].get("name", "unknown")

                            error_msg = str(result_data)
                            logger.error(
                                f"L4.orchestrator [iter:{iteration}] - Tool '{tool_name}' execution exception: {error_msg}",
                                exc_info=True,
                            )
                            tool_results.append(
                                {
                                    "success": False, 
                                    "error": f"Tool '{tool_name}' failed: {error_msg}",
                                    "tool_name": tool_name
                                }
                            )
                            # Collect for Google context
                            last_tool_results.append({"output": f"Error: {error_msg}"})
                            last_tool_call_names.append(tool_name)
                            resolved_id = (reordered_tool_calls[i].get("_ui_id") if i < len(reordered_tool_calls) else None)
                            logger.info(
                                "L4.orchestrator [run:%s iter:%s] tool.end name=%s id=%s success=%s",
                                run_id,
                                iteration,
                                tool_name,
                                resolved_id,
                                False,
                            )
                            await _emit({
                                "type": "tool.end",
                                "name": tool_name,
                                "id": resolved_id,
                                "success": False,
                                "error": error_msg,
                            })
                            # Surface failure to conversation so the model can recover next iteration
                            try:
                                conversation_history.append(f"System: Tool '{tool_name}' failed: {error_msg}")
                            except Exception:
                                pass
                            continue

                        # result_data should be a tuple (result, tool_call) when successful
                        if isinstance(result_data, tuple) and len(result_data) == 2:
                            result, tool_call = result_data
                            
                            if not isinstance(result, dict):
                                logger.error(f"L4.orchestrator [iter:{iteration}] - Tool result {i+1} is not a dictionary: {type(result)}")
                                tool_results.append({
                                    "success": False,
                                    "error": f"Tool returned invalid result type: {type(result)}",
                                    "tool_name": tool_call.get("name", "unknown") if isinstance(tool_call, dict) else "unknown"
                                })
                                # Collect for Google context
                                last_tool_results.append({"output": f"Error: Tool returned invalid result type: {type(result)}"})
                                last_tool_call_names.append(tool_call.get("name", "unknown") if isinstance(tool_call, dict) else "unknown")
                                continue
                            
                            tool_results.append(result)
                            
                            # Collect structured result for Google context preservation
                            last_tool_results.append({"output": result.get("output", "")})
                            last_tool_call_names.append(tool_call.get("name", "unknown") if isinstance(tool_call, dict) else "unknown")
                            
                            current_index = len(tool_results) - 1
                            try:
                                tool_data = result.get("tool_data") if isinstance(result, dict) else None
                            except Exception:
                                tool_data = None
                            if _queue_pending_image_autosend(pending_image_autosends, tool_data, result):
                                handled_tool_actions.add(current_index)
                                image_autosent_recently = True
                                summary_record = _extract_image_summary_record(tool_data, result)
                                if summary_record:
                                    image_summary_records.append(summary_record)
                            logger.debug(f"L4.orchestrator [iter:{iteration}] - Tool result {i+1}: success={result.get('success', False)}")
                            # Emit log and end events
                            try:
                                preview = (result.get("output") or "")
                                if isinstance(preview, str):
                                    preview = preview[:200]
                                # Send a log preview for UI feedback
                                await _emit({
                                    "type": "tool.log",
                                    "name": (reordered_tool_calls[i].get("name") if i < len(reordered_tool_calls) else "unknown"),
                                    "id": (reordered_tool_calls[i].get("_ui_id") if i < len(reordered_tool_calls) else None),
                                    "message": preview or "(no output)",
                                })
                                resolved_name = (reordered_tool_calls[i].get("name") if i < len(reordered_tool_calls) else "unknown")
                                resolved_id = (reordered_tool_calls[i].get("_ui_id") if i < len(reordered_tool_calls) else None)
                                logger.info(
                                    "L4.orchestrator [run:%s iter:%s] tool.end name=%s id=%s success=%s",
                                    run_id,
                                    iteration,
                                    resolved_name,
                                    resolved_id,
                                    bool(result.get("success")),
                                )
                                await _emit({
                                    "type": "tool.end",
                                    "name": resolved_name,
                                    "id": resolved_id,
                                    "success": bool(result.get("success")),
                                    "preview": preview,
                                })
                                # If the tool failed, surface a concise message into the conversation
                                if not bool(result.get("success")):
                                    try:
                                        err = result.get("error") or "Tool reported failure"
                                        tn = (reordered_tool_calls[i].get("name") if i < len(reordered_tool_calls) else "unknown")
                                        conversation_history.append(f"System: Tool '{tn}' failed: {err}")
                                    except Exception:
                                        pass
                            except Exception:
                                pass
                        else:
                            logger.error(f"L4.orchestrator [iter:{iteration}] - Unexpected result format from tool {i+1}: {type(result_data)}")
                            tool_results.append({
                                "success": False,
                                "error": f"Tool returned unexpected result format: {type(result_data)}",
                                "tool_name": "unknown"
                            })
                            resolved_id = (reordered_tool_calls[i].get("_ui_id") if i < len(reordered_tool_calls) else None)
                            logger.info(
                                "L4.orchestrator [run:%s iter:%s] tool.end name=%s id=%s success=%s",
                                run_id,
                                iteration,
                                "unknown",
                                resolved_id,
                                False,
                            )
                            await _emit({
                                "type": "tool.end",
                                "name": "unknown",
                                "id": resolved_id,
                                "success": False,
                                "error": "unexpected result format",
                            })
                            try:
                                conversation_history.append("System: Tool returned unexpected result format")
                            except Exception:
                                pass
                            
                    except Exception as process_error:
                        logger.error(f"L4.orchestrator [iter:{iteration}] - Error processing result {i}: {process_error}", exc_info=True)
                        tool_results.append({
                            "success": False,
                            "error": f"Error processing tool result: {process_error}",
                            "tool_name": "unknown"
                        })
                        # Collect for Google context
                        last_tool_results.append({"output": f"Error: {process_error}"})
                        last_tool_call_names.append("unknown")



                # Capture structured turn for Google history
                if response and tool_results:
                    turn_record = {
                        "model_response": response,
                        "tool_results": tool_results,
                        "tool_call_names": [tc.get("name") for tc in reordered_tool_calls]
                    }
                    google_turn_history.append(turn_record)
                    logger.debug(f"L4.orchestrator [iter:{iteration}] - Appended structured turn to google_turn_history (total: {len(google_turn_history)})")

                if auto_followup_messages:
                    for note in auto_followup_messages:
                        conversation_history.append(note)
                        try:
                            await _emit({"type": "reasoning.delta", "text": note})
                        except Exception:
                            pass

                # Handle web interactions from tool results (e.g., manual message emit, show_form)
                from utils.forms_store import enqueue_ui_event as _enqueue_form_evt  # local import to avoid heavy import at module import time
                for idx, result in enumerate(tool_results):
                    if idx in handled_tool_actions:
                        continue
                    if result.get("success") and result.get("tool_data"):
                        td = result["tool_data"]
                        try:
                            action = td.get("action") if isinstance(td, dict) else None
                            # Forms are now explicitly managed - no auto-showing
                            # Users must explicitly call show_form to display a form
                            if action == "define_form":
                                # Proactively hint the next step to the model and user
                                form_id = td.get("form_id")
                                if form_id:
                                    try:
                                        hint = f"Form '{form_id}' defined. Next: show_form(form_id='{form_id}')."
                                        await _emit({"type": "reasoning.delta", "text": hint})
                                        conversation_history.append(f"System: {hint}")
                                    except Exception:
                                        pass
                                # Continue without emitting UI events
                                continue
                            if action == "web.message" and isinstance(td.get("text"), str):
                                # Emit the message live and add to conversation history
                                await _emit({"type": "output.delta", "text": td.get("text")})
                                conversation_history.append(f"Theo: {td.get('text')}")
                            elif action == "link_token_created":
                                # Emit Plaid Link event to trigger automatic popup
                                payload = {"action": "link_token_created", "link_token": td.get("link_token")}
                                # Attach run id and room for context
                                try:
                                    rid = getattr(message, 'id', None)
                                    if rid:
                                        payload["run_id"] = str(rid)
                                except Exception:
                                    pass
                                try:
                                    room = getattr(message, 'room', None) or getattr(message, 'room_id', None)
                                    if room:
                                        payload["room_id"] = str(room)
                                except Exception:
                                    pass
                                logger.info(f"L4.tools [plaid] - Emitting plaid.link event with link_token")
                                await _emit({"type": "plaid.link", **payload})
                            elif action == "show_form":
                                # Emit a UI event for the active run and enqueue for global forms SSE
                                payload = {
                                    "action": "show_form",
                                    "form": td.get("form"),
                                    "prefill": td.get("prefill"),
                                }
                                room_hint = td.get("room")
                                if not room_hint:
                                    try:
                                        room_hint = getattr(message, "room", None) or getattr(message, "room_id", None)
                                    except Exception:
                                        room_hint = None
                                if room_hint:
                                    room_str = str(room_hint)
                                    payload["room"] = room_str
                                    payload["room_id"] = room_str
                                # Attach the current run id so UI can include it on submit
                                try:
                                    rid = getattr(message, 'id', None)
                                    if rid:
                                        payload["run_id"] = str(rid)
                                except Exception:
                                    pass
                                # Deduplicate: if the last UI event for this room is an identical show_form, skip re-emitting
                                try:
                                    from utils.forms_store import get_last_ui_event as _get_last_ui_evt
                                    room_guess = payload.get("room")
                                    if not room_guess:
                                        try:
                                            room_guess = getattr(getattr(message, 'channel', None), 'id', None)
                                        except Exception:
                                            room_guess = None
                                    last_evt = _get_last_ui_evt(room_guess) if room_guess else _get_last_ui_evt(None)
                                    if last_evt and isinstance(last_evt, dict):
                                        if (str(last_evt.get('action') or '') == 'show_form'):
                                            last_f = ((last_evt.get('form') or {}).get('form_id')) or last_evt.get('form_id')
                                            new_f = ((payload.get('form') or {}).get('form_id')) or payload.get('form_id')
                                            last_r = (last_evt.get('room') or None)
                                            new_r = (payload.get('room') or None)
                                            if str(last_f) == str(new_f) and str(last_r or '') == str(new_r or ''):
                                                logger.info("L4.tools [forms] - Duplicate show_form suppressed for active form")
                                                # Do not emit again to run or SSE
                                                continue
                                except Exception:
                                    pass
                                # Emit to run stream (per-run)
                                await _emit({"type": "forms.show", **payload})
                                # Also enqueue to global UI events so web clients listening to /api/forms/stream receive it
                                try:
                                    _enqueue_form_evt(payload)
                                except Exception as ee:
                                    logger.debug(f"L4.tools [forms] - enqueue_ui_event failed: {ee}")
                                forms_shown_total += 1
                                logger.info(f"L4.tools [forms] - Form {td.get('form', {}).get('form_id', 'unknown')} shown (non-terminal)")
                            elif action == "send_image":
                                if _queue_pending_image_autosend(pending_image_autosends, td, result):
                                    handled_tool_actions.add(idx)
                                    image_autosent_recently = True
                                    summary_record = _extract_image_summary_record(td, result)
                                    if summary_record:
                                        image_summary_records.append(summary_record)
                        except Exception as web_error:
                            logger.debug(f"L4.tools [web] - Web interaction skip/fail: {web_error}")

                sent_image_count = 0
                try:
                    sent_image_count = await _flush_pending_image_autosends(pending_image_autosends, message, _emit)
                except asyncio.CancelledError:
                    raise
                except Exception as flush_err:
                    logger.debug(f"L4.tools [web] - Deferred send_image flush failed: {flush_err}")

                if sent_image_count > 0:
                    image_autosent_recently = True
                    if not image_summary_metadata_added:
                        metadata_message = _build_image_metadata_message(image_summary_records)
                        if metadata_message:
                            conversation_history.append(metadata_message)
                            image_summary_metadata_added = True
                    try:
                        plural = "s" if sent_image_count != 1 else ""
                        conversation_history.append(
                            f"System: Delivered {sent_image_count} generated image{plural}. Provide a final summary for the user before closing the turn. Include seeds, resolution, steps, CFG, and invite further tweaks."
                        )
                    except Exception:
                        pass

                # Removed pending form notifications injection per simplified forms flow

                # Add tool results to conversation with enhanced error handling
                for result in tool_results:
                    if result.get("success"):
                        output = result.get("output", "Success")
                        # Cap extremely long tool outputs in conversational history to avoid loops
                        try:
                            max_chars = int(os.getenv("THEO_TOOL_OUTPUT_MAX", "4000") or 4000)
                        except Exception:
                            max_chars = 4000
                        if isinstance(output, str) and len(output) > max_chars:
                            display_output = output[: max_chars - 12] + "...[truncated]"
                        else:
                            display_output = output
                        tool_name_logged = result.get("tool_name", "")
                        
                        # Track successfully executed tools for error context
                        if tool_name_logged:
                            executed_tools.append(tool_name_logged)
                        if not isinstance(display_output, str):
                            display_output = str(display_output)
                        if not display_output:
                            continue
                        new_entry = f"Tool Result: {display_output}"

                        # Check for duplicate outputs in conversation history
                        # BUT: Don't deduplicate if the output contains warnings or errors
                        # (these should always be visible to help the LLM learn from failures)
                        duplicate_found = False
                        has_warning_or_error = any(
                            marker in str(display_output) 
                            for marker in ["⚠️", "WARNING", "Warning", "ERROR", "Error", "❌", "FAILED", "Failed"]
                        )
                        
                        # Debug logging
                        if tool_name_logged == "get_financial_accounts":
                            logger.info(
                                f"L4.tools [dedup-check] - Tool: {tool_name_logged}, "
                                f"Has warning/error: {has_warning_or_error}, "
                                f"Output preview: {str(display_output)[:200]}"
                            )
                        
                        if not has_warning_or_error:
                            for j, existing_entry in enumerate(conversation_history):
                                if existing_entry == new_entry:
                                    # Mark duplicate found but don't show ugly message in chat
                                    # The duplicate is simply not added again
                                    duplicate_found = True
                                    logger.debug(
                                        f"L4.tools [deduplication] - Skipped duplicate tool output (already at position {j})"
                                    )
                                    break

                        # Add the current result only if not a duplicate
                        if not duplicate_found:
                            conversation_history.append(new_entry)
                        # Persist tool result to room chat history as hidden (with dedup per specs)
                        try:
                            from layer4_tools.web_tools import append_tool_result_with_dedup
                            rid = getattr(message, 'id', None)
                            room = getattr(getattr(message, 'channel', None), 'id', None) or "web"
                            append_tool_result_with_dedup(new_entry, room, run_id=str(rid) if rid else None)
                        except Exception as _pe:
                            logger.debug(f"L4.tools [persist] - Tool result persistence failed: {_pe}")
                        if duplicate_found:
                            logger.info(
                                f"L4.tools [deduplication] - Tool output deduplicated (kept most recent)"
                            )
                        
                        # Surface structured follow-up suggestions from tool metadata
                        tool_data = result.get("tool_data", {}) if isinstance(result, dict) else {}
                        if isinstance(tool_data, dict) and tool_data:
                            next_actions = tool_data.get("next_actions")
                            if isinstance(next_actions, (list, tuple)) and next_actions:
                                for suggestion in list(next_actions)[:3]:
                                    suggestion_text = str(suggestion).strip()
                                    if not suggestion_text:
                                        continue
                                    hint_entry = (
                                        "System: Next step suggestion – "
                                        f"{suggestion_text}"
                                    )
                                    conversation_history.append(hint_entry)
                                    try:
                                        await _emit({"type": "reasoning.delta", "text": suggestion_text})
                                    except Exception:
                                        pass

                        # Check if this is a terminal tool that should end the conversation
                        tool_data = tool_data or {}
                        # Terminal condition: prefer generic flag; keep provider flags for back-compat
                        if tool_data.get("terminal") or tool_data.get("finalize_reply") or tool_data.get("discord_response"):
                            logger.info(
                                f"L4.orchestrator [iter:{iteration}] - Terminal tool '{result.get('tool_name', 'unknown')}' executed, ending conversation"
                            )
                            end_processing()
                            return output
                            
                    else:
                        error_msg = result.get("error", "Unknown error")
                        tool_name = result.get("tool_name", "unknown")
                        conversation_history.append(f"Tool Error: {error_msg}")
                        # Persist tool error as well (hidden)
                        try:
                            from layer4_tools.web_tools import append_tool_result_with_dedup
                            rid = getattr(message, 'id', None)
                            room = getattr(getattr(message, 'channel', None), 'id', None) or "web"
                            append_tool_result_with_dedup(f"Tool Error: {error_msg}", room, run_id=str(rid) if rid else None)
                        except Exception:
                            pass
                        logger.warning(
                            f"L4.tools [orchestrator] - Tool '{tool_name}' execution error: {error_msg}"
                        )

                # For OpenAI structured flow, function_call_output items are appended once below

            except asyncio.CancelledError:
                logger.warning("L4.tools [orchestrator] - Tool execution was cancelled.")
                conversation_history.append("Tool Error: Tool execution was cancelled.")
                raise
            except Exception as e:
                logger.error(
                    f"L4.tools [orchestrator] - Tool execution phase failed: {e}",
                    exc_info=True,
                )
                # Add error to conversation and continue
                conversation_history.append(f"Tool Error: Execution phase failed: {str(e)}")

            # Structured tool replay per provider docs (/docs/{provider})
            try:
                if successful_provider in ("openai", "anthropic", "groq", "xai"):
                    try:
                        from layer1_chatbot.model_call_openai import get_response_id as _get_oa_resp_id
                    except Exception:
                        _get_oa_resp_id = None  # type: ignore
                    # Build generic tool_results payload aligned across providers
                    provider_tool_results = []
                    tool_call_names: list[str] = []
                    try:
                        if tool_results:
                            for i, res in enumerate(tool_results):
                                call = reordered_tool_calls[i] if i < len(reordered_tool_calls) else {}
                                call_id = (call or {}).get("call_id") if isinstance(call, dict) else None
                                name = (call or {}).get("name") if isinstance(call, dict) else None
                                out_text = res.get("output") if isinstance(res, dict) else None
                                if not isinstance(out_text, str) or not out_text:
                                    td = res.get("tool_data") if isinstance(res, dict) else None
                                    try:
                                        out_text = json.dumps(td if td is not None else {"success": res.get("success", False)})
                                    except Exception:
                                        out_text = str(td) if td is not None else ""
                                # Some providers require call_id (OpenAI/Anthropic/Groq), others prefer name mapping (Google)
                                entry = {"output": out_text}
                                if call_id:
                                    entry["tool_call_id"] = call_id
                                provider_tool_results.append(entry)
                                if name:
                                    tool_call_names.append(name)
                        else:
                            # No execution (e.g., skipped by dedup). Create placeholder outputs for all prior function calls.
                            expected_ids = []
                            for oc in (original_tool_calls or []):
                                try:
                                    ocid = (oc or {}).get("call_id")
                                    if isinstance(ocid, str) and ocid.strip():
                                        expected_ids.append(ocid)
                                except Exception:
                                    continue
                            if expected_ids:
                                logger.info(
                                    f"L4.orchestrator [{successful_provider}] - Synthesizing {len(expected_ids)} placeholder tool_outputs for skipped calls"
                                )
                                for cid in expected_ids:
                                    provider_tool_results.append({
                                        "tool_call_id": cid,
                                        "output": "(skipped by orchestrator: duplicate or unsupported tool call)",
                                    })
                    except Exception as _bte:
                        logger.debug(f"L4.orchestrator [structured] - Failed to build tool_results: {_bte}")

                    prov = successful_provider
                    # Dispatch per provider
                    if prov == "openai":
                        prev_id = None
                        try:
                            if _get_oa_resp_id is not None:
                                prev_id = _get_oa_resp_id(response)
                            else:
                                prev_id = getattr(response, "id", None)
                        except Exception:
                            prev_id = getattr(response, "id", None)
                        if prev_id and provider_tool_results:
                            logger.debug("L4.orchestrator [openai] - Sending tool_results continuation to Responses API")
                            cont_kwargs = dict(model_kwargs or {})
                            cont_kwargs["previous_response_id"] = prev_id
                            cont_kwargs["tool_results"] = provider_tool_results
                            # Rebuild a fresh prompt so the continuation immediately sees the updated
                            try:
                                continuation_prompt = await prompt_builder.build_conversation_prompt(
                                    content, message, conversation_history
                                )
                            except Exception:
                                continuation_prompt = current_prompt
                            continuation, _ = await _call_model_with_circuit_breaker(
                                model_selector,
                                continuation_prompt,
                                successful_model or model,
                                tool_schemas,
                                config,
                                cont_kwargs,
                                rebuild_prompt_func=_rebuild_prompt_for_model,
                            )
                        else:
                            continuation = None
                    elif prov == "anthropic":
                        cont_kwargs = dict(model_kwargs or {})
                        cont_kwargs["anthropic_tool_results"] = provider_tool_results
                        try:
                            cont_kwargs["anthropic_prev_content"] = getattr(response, "content", None)
                        except Exception:
                            pass
                        # Rebuild prompt for immediate context update
                        try:
                            continuation_prompt = await prompt_builder.build_conversation_prompt(
                                content, message, conversation_history
                            )
                        except Exception:
                            continuation_prompt = current_prompt
                        continuation, _ = await _call_model_with_circuit_breaker(
                            model_selector,
                            continuation_prompt,
                            successful_model or model,
                            tool_schemas,
                            config,
                            cont_kwargs,
                            rebuild_prompt_func=_rebuild_prompt_for_model,
                        )
                    elif prov == "groq":
                        cont_kwargs = dict(model_kwargs or {})
                        cont_kwargs["tool_results"] = provider_tool_results
                        cont_kwargs["groq_prev_response"] = response
                        try:
                            continuation_prompt = await prompt_builder.build_conversation_prompt(
                                content, message, conversation_history
                            )
                        except Exception:
                            continuation_prompt = current_prompt
                        continuation, _ = await _call_model_with_circuit_breaker(
                            model_selector,
                            continuation_prompt,
                            successful_model or model,
                            tool_schemas,
                            config,
                            cont_kwargs,
                            rebuild_prompt_func=_rebuild_prompt_for_model,
                        )
                    elif prov == "google":
                        cont_kwargs = dict(model_kwargs or {})
                        cont_kwargs["tool_results"] = provider_tool_results
                        if tool_call_names:
                            cont_kwargs["tool_call_names"] = tool_call_names
                        try:
                            continuation_prompt = await prompt_builder.build_conversation_prompt(
                                content, message, conversation_history
                            )
                        except Exception:
                            continuation_prompt = current_prompt
                        continuation, _ = await _call_model_with_circuit_breaker(
                            model_selector,
                            continuation_prompt,
                            successful_model or model,
                            tool_schemas,
                            config,
                            cont_kwargs,
                            rebuild_prompt_func=_rebuild_prompt_for_model,
                        )
                    elif prov == "xai":
                        cont_kwargs = dict(model_kwargs or {})
                        cont_kwargs["tool_results"] = provider_tool_results
                        # xai_prev_response is used to extract assistant message with tool_calls and reasoning_content
                        cont_kwargs["xai_prev_response"] = response
                        try:
                            continuation_prompt = await prompt_builder.build_conversation_prompt(
                                content, message, conversation_history
                            )
                        except Exception:
                            continuation_prompt = current_prompt
                        logger.info(f"L4.orchestrator [xai] - Calling continuation with {len(provider_tool_results)} tool results")
                        continuation, _ = await _call_model_with_circuit_breaker(
                            model_selector,
                            continuation_prompt,
                            successful_model or model,
                            tool_schemas,
                            config,
                            cont_kwargs,
                            rebuild_prompt_func=_rebuild_prompt_for_model,
                        )
                        logger.info(f"L4.orchestrator [xai] - Continuation call completed")
                    else:
                        continuation = None

                    if continuation is not None:
                        # Try to extract any new function calls or final text
                        try:
                            response_text = model_selector.extract_response_text(
                                continuation,
                                model_selector.get_provider_from_model(successful_model or model),
                                successful_model or model,
                            )
                        except Exception:
                            response_text = None
                        try:
                            tool_calls = model_selector.extract_function_calls(
                                continuation,
                                model_selector.get_provider_from_model(successful_model or model),
                            )
                        except Exception:
                            tool_calls = []

                        # If the continuation produced a direct answer, return it now
                        if not tool_calls:
                            end_processing()
                            await _emit({"type": "status", "value": "responding"})
                            return response_text or ""
                        else:
                            # Otherwise, proceed to next iteration with the new calls
                            conversation_history.append("System: Continued after tool_results; proceeding with additional calls.")
                            consecutive_tool_only_iters += 1
                            continue
            except Exception as _se:
                logger.warning(f"L4.orchestrator [structured] - Structured replay failed for {successful_provider or provider}: {_se}", exc_info=True)
                # Surface the error to conversation so user knows what happened
                conversation_history.append(f"System: Tool continuation failed: {str(_se)[:200]}")

            # Persist tool outputs to chat history and local conversation history (spec alignment)
            try:
                from utils.logger import ContentFilter
                chan_id = getattr(getattr(message, 'channel', None), 'id', None)
                for res in tool_results:
                    name = res.get("tool_name", "unknown")
                    ok = res.get("success", False)
                    text = res.get("output") if ok else res.get("error", "(error)")
                    status = "SUCCESS" if ok else "FAILED"
                    summary = ContentFilter.truncate_content(str(text or ""), max_chars=600, preserve_structure=True)
                    logline = f"TOOL_RESULT [{name}] {status}\n{summary}"
                    conversation_history.append(f"System: {logline}")
                    
                    # CRITICAL FIX: Add tool results to conversation in a way Theo can see
                    if ok and text:
                        # Add successful tool results as assistant messages so Theo can see them
                        conversation_history.append(f"Theo: {text}")
                    elif not ok:
                        # Add failed tool results as system messages
                        error_msg = res.get("error", "Unknown error")
                        conversation_history.append(f"System: Tool {name} failed: {error_msg}")
            except Exception as persist_err:
                logger.debug(f"L4.orchestrator [history] - Skipped persisting tool results: {persist_err}")

            # End tool execution processing state
            end_processing()
            
            # Update item count for Google context slicing in next iteration
            last_turn_items_count = len(conversation_history) - history_len_before_turn

        # If we reach here, we hit max iterations
        logger.warning(
            f"L4.orchestrator [final] - Max iterations ({max_iterations}) reached, returning last tool result"
        )

        # Return the last successful tool result instead of raw function call
        if conversation_history:
            for entry in reversed(conversation_history):
                if entry.startswith("Tool Result: "):
                    # Remove "Tool Result: " prefix
                    tool_result = entry[13:]
                    logger.info(
                        f"L4.tools [orchestrator] - Returning last tool result: {tool_result[:100]}..."
                    )
                    return tool_result

            # If no tool results found, try to return last non-function-call response
            for entry in reversed(conversation_history):
                if (
                    entry.startswith("Theo: ")
                    and "[Function calls:" not in entry
                ):
                    return entry[6:]  # Remove "Theo: " prefix

        return "I encountered an issue processing your request with tools. Please try again."

    except asyncio.CancelledError as e:
        try:
            # Provide more context about what was happening when cancelled
            iteration_info = ""
            if "iteration_" in stage:
                iteration_num = stage.split("_")[1] if "_" in stage else "unknown"
                iteration_info = f" (iteration {iteration_num})"
            
            logger.warning(
                "L4.orchestrator - Cancelled during stage '%s'%s. Content preview: %s",
                stage,
                iteration_info,
                content[:80] if isinstance(content, str) else "<non-str>",
            )
            logger.warning("L4.orchestrator - Cancellation exception detail: %r", e)
            
            # If cancelled during prompt rebuilding, this might indicate a timeout issue
            if "rebuild_prompt" in stage:
                logger.warning(
                    "L4.orchestrator - Task cancelled during prompt rebuilding - this may indicate a timeout or resource issue"
                )
                # For prompt rebuilding cancellations, we should be more graceful
                # and potentially return a partial result instead of failing completely
                if conversation_history:
                    for entry in reversed(conversation_history):
                        if entry.startswith("Tool Result: "):
                            tool_result = entry[13:]
                            logger.info(
                                f"L4.orchestrator - Returning partial tool result due to cancellation: {tool_result[:100]}..."
                            )
                            return tool_result
        except Exception:
            pass
        end_processing()
        raise

    except ToolOrchestrationError as e:
        logger.error(
            f"L4.tools [orchestrator] - Tool orchestration error: {e}",
            exc_info=True,
        )
        end_processing()
        return f"I encountered an error while processing your request: {str(e)}. Please try again."

    except Exception as e:
        model = config.get("primary_model", "gpt-5")
        logger.error(
            f"L4.orchestrator [fatal] - Unexpected error in tool processing with {model}: {e}",
            exc_info=True,
        )
        end_processing()
        raise ToolOrchestrationError(f"An unexpected error occurred during tool orchestration: {e}") from e


async def _consume_stream(
    stream_generator: Any,
    event_cb: Optional[Callable[[Dict[str, Any]], Awaitable[None]]] = None,
) -> Any:
    """Consume a streaming response generator and emit events.
    
    Args:
        stream_generator: AsyncGenerator yielding event dictionaries
        event_cb: Optional callback to emit events to
    
    Returns:
        Synthesized response object with accumulated text and function calls
    """
    accumulated_text = []
    function_calls = []
    reasoning_items = []  # Store reasoning items for continuation calls
    thought_signatures = [] # Store thought signatures for Google multi-step
    event_count = 0
    reasoning_count = 0
    response_id: Optional[str] = None
    
    try:
        async for event in stream_generator:
            event_count += 1
            event_type = event.get("type")
            if not response_id:
                rid = event.get("response_id")
                if isinstance(rid, str) and rid:
                    response_id = rid
            
            # Forward reasoning summary events and accumulate for continuation
            if event_type == "reasoning_summary":
                reasoning_count += 1
                if reasoning_count <= 5:  # Log first few
                    logger.debug(f"L4.stream - Received reasoning_summary event #{reasoning_count}")

                # Store reasoning delta for continuation calls (OpenAI requirement)
                delta_text = event.get("delta", "")
                if delta_text:
                    # Build reasoning item structure compatible with OpenAI's format
                    reasoning_items.append({
                        "type": "reasoning",
                        "summary_text": delta_text,
                    })

                if event_cb:
                    try:
                        # Forward as reasoning_summary with delta field (frontend expects this format)
                        emit_event = {
                            "type": "reasoning_summary",
                            "delta": delta_text,
                        }
                        await event_cb(emit_event)
                    except Exception as emit_err:
                        logger.debug(f"L4.stream - Failed to emit reasoning_summary: {emit_err}")
            
            # Collect thought signatures (Google Gemini)
            elif event_type == "thought_signature":
                sig = event.get("signature")
                if sig:
                    thought_signatures.append(sig)

            # Forward reasoning summary clear events
            elif event_type == "reasoning_summary_clear" and event_cb:
                try:
                    await event_cb(event)
                except Exception as emit_err:
                    logger.debug(f"L4.stream - Failed to emit reasoning_summary_clear: {emit_err}")
            
            # Accumulate output text
            elif event_type == "output.delta":
                text = event.get("text", "")
                if text:
                    accumulated_text.append(text)
                # Forward output deltas for real-time streaming
                if event_cb:
                    try:
                        await event_cb(event)
                    except Exception as emit_err:
                        logger.debug(f"L4.stream - Failed to emit output.delta: {emit_err}")
            
            # Collect function calls
            elif event_type == "function_call":
                function_calls.append(event)
            
            # Handle completion
            elif event_type == "done":
                if not response_id:
                    rid = event.get("response_id")
                    if isinstance(rid, str) and rid:
                        response_id = rid
                break
            
            # Handle errors
            elif event_type == "error":
                error_msg = event.get("error", "Unknown streaming error")
                logger.error(f"L4.stream - Stream error: {error_msg}")
                raise ToolOrchestrationError(f"Streaming error: {error_msg}")
    
    except Exception as e:
        logger.error(f"L4.stream - Error consuming stream: {e}", exc_info=True)
        raise
    
    # Log summary of stream consumption
    logger.info(
        f"L4.stream - Consumed {event_count} total events, {reasoning_count} reasoning summaries ({len(reasoning_items)} items), "
        f"{len(thought_signatures)} signatures, {len(accumulated_text)} output chunks"
    )

    def _normalize_function_call(call: Dict[str, Any]) -> Dict[str, Any]:
        """Coerce streamed function call events into a consistent OpenAI-like shape."""
        if not isinstance(call, dict):
            call = {}
        raw_arguments = call.get("arguments")
        arguments_str = "{}"
        if isinstance(raw_arguments, str):
            arguments_str = raw_arguments or "{}"
        elif isinstance(raw_arguments, dict):
            try:
                arguments_str = json.dumps(raw_arguments)
            except Exception:
                arguments_str = json.dumps(raw_arguments, default=str)
        elif raw_arguments is not None:
            try:
                arguments_str = json.dumps(raw_arguments, default=str)
            except Exception:
                arguments_str = "{}"

        call_id = str(call.get("call_id") or call.get("id") or "")
        normalized: Dict[str, Any] = {
            "type": "function_call",
            "id": str(call.get("id") or call_id or ""),
            "call_id": call_id,
            "name": str(call.get("name") or ""),
            "arguments": arguments_str,
        }
        # Provide alternate access patterns used by some extractors
        normalized["input"] = normalized["arguments"]
        return normalized

    normalized_calls = [_normalize_function_call(call) for call in function_calls]

    # Synthesize a response object for the orchestrator
    class SynthesizedResponse:
        def __init__(
            self,
            text: str,
            calls: list[Dict[str, Any]],
            resp_id: Optional[str],
            reasoning: list[Dict[str, Any]] = None,
            thought_signatures: list[Any] = None
        ):
            self.output_text = text or ""
            # Property alias for Google extractor compatibility
            self.text = self.output_text
            self._function_calls = list(calls)
            self.id = resp_id or ""
            self.thought_signatures = thought_signatures or []

            # Build output array: reasoning items first, then function calls
            # Per OpenAI docs: reasoning items must be passed back with tool results
            self.output = []
            if reasoning:
                # Add reasoning items as SimpleNamespace objects
                for reason_item in reasoning:
                    self.output.append(SimpleNamespace(**reason_item))
            # Add function call items
            for call in calls:
                self.output.append(SimpleNamespace(**call))
            tool_calls_payload = [
                SimpleNamespace(
                    id=(call.get("call_id") or call.get("id") or ""),
                    type="function",
                    function=SimpleNamespace(
                        name=call.get("name") or "",
                        arguments=call.get("arguments") or "{}",
                    ),
                )
                for call in calls
            ]
            # Create minimal structure for existing extraction logic (OpenAI style)
            self.choices = [
                SimpleNamespace(
                    message=SimpleNamespace(
                        content=self.output_text,
                        tool_calls=tool_calls_payload or None,
                    )
                )
            ]
            # Create minimal structure for Google extraction logic
            # GoogleModelCall checks candidates[0].content.parts...
            
            parts_list = []
            if self.output_text:
                parts_list.append(SimpleNamespace(text=self.output_text))
            
            # Add function calls to parts list for Google history injection
            # Attach thought_signatures to these parts if available
            for idx, call in enumerate(calls):
                # Construct function_call object for the part
                # model_call_google._handle_stream_events expects part.function_call.name/.args
                fc_namespace = SimpleNamespace(
                    name=call.get("name"),
                    args=json.loads(call.get("arguments", "{}"))
                )
                
                part_namespace = SimpleNamespace(function_call=fc_namespace)
                
                # Attach thought signature if available for this call
                # Assuming 1:1 mapping; if signatures < calls, we attach what we have
                if idx < len(self.thought_signatures):
                    part_namespace.thought_signature = self.thought_signatures[idx]
                
                parts_list.append(part_namespace)

            self.candidates = [
                SimpleNamespace(
                    content=SimpleNamespace(
                        parts=parts_list
                    ),
                    finish_reason="STOP"
                )
            ]

        def __str__(self):
            return self.output_text

        def __repr__(self):
            return f"<SynthesizedResponse text={len(self.output_text)} chars calls={len(self._function_calls)} sigs={len(self.thought_signatures)}>"

    final_text = "".join(accumulated_text)
    logger.debug(
        f"L4.stream - Stream complete: {len(final_text)} chars, {len(normalized_calls)} function calls, "
        f"{len(reasoning_items)} reasoning items"
    )

    return SynthesizedResponse(final_text, normalized_calls, response_id, reasoning_items, thought_signatures)


async def _call_model_with_circuit_breaker(
    model_selector,
    prompt: Any,
    model: str,
    tool_schemas: List[Dict],
    config: Dict,
    model_kwargs: Dict[str, Any] | None = None,
    rebuild_prompt_func: Optional[Callable[[str], Awaitable[Any]]] = None,
    event_cb: Optional[Callable[[Dict[str, Any]], Awaitable[None]]] = None,
):
    """Call model with circuit breaker protection and fallback logic.

    If the primary model fails and a fallback model is configured, this function will try the fallback.
    When trying the fallback, if rebuild_prompt_func is provided, it will be awaited to reconstruct
    a brand new prompt payload tailored to the fallback model/provider (no translation of the original).
    
    Supports streaming for OpenAI GPT-5 and Gemini models with reasoning summaries.
    
    Args:
        model_selector: ModelSelector instance
        prompt: Prompt to send to the model
        model: Model name
        tool_schemas: Tool schemas
        config: Configuration dict
        model_kwargs: Model-specific kwargs
        rebuild_prompt_func: Function to rebuild prompt for fallback
        event_cb: Event callback for streaming events
    
    Returns:
        tuple: (response, actual_model_used) where actual_model_used is the name of the model that generated the response
    """
    
    # Determine if we should stream (OpenAI GPT-5 or Gemini models with reasoning)
    provider = model_selector.get_provider_from_model(model)
    try:
        model_lc = str(model).lower()
    except Exception:
        model_lc = ""
    
    # Enable streaming for:
    # 1. OpenAI GPT-5 models
    # 2. Gemini models (provider="google" or "gemini-" in name)
    should_stream = (
        (provider == "openai" and model_lc.startswith("gpt-5")) or
        (provider == "google") or 
        (model_lc.startswith("gemini-"))
    )
    
    # Get circuit breaker for primary model
    # Allow dev override for circuit breaker tuning
    cb_failures = int(os.getenv("THEO_CB_MODEL_FAILS", "3") or 3)
    cb_reset = int(os.getenv("THEO_CB_MODEL_RESET", "300") or 300)
    primary_breaker = get_circuit_breaker(f"model_{model}", failure_threshold=cb_failures, reset_timeout=cb_reset)
    
    # Try primary model if circuit is closed
    if not primary_breaker.is_open():
        try:
            # Pass unified kwargs; providers will ignore unknown keys
            kwargs: Dict[str, Any] = dict(model_kwargs or {})
            
            # Enable streaming for OpenAI GPT-5 or Gemini
            if should_stream:
                kwargs["stream"] = True
                logger.info(f"L4.orchestrator [stream] - Enabling streaming for {model} (provider: {provider})")
            
            response = await model_selector.call_model(
                prompt,
                model=model,
                tools=tool_schemas,
                disable_internal_fallback=True,
                **kwargs,
            )
            
            # If streaming, consume the generator and emit events
            if should_stream:
                response_type = type(response).__name__
                has_anext = hasattr(response, "__anext__")
                logger.info(f"L4.orchestrator [stream] - Response type: {response_type}, is async generator: {has_anext}")
                
                if has_anext:
                    logger.info(f"L4.orchestrator [stream] - Consuming stream with event_cb={'present' if event_cb else 'missing'}")
                    response = await _consume_stream(response, event_cb)
                else:
                    logger.warning(f"L4.orchestrator [stream] - Expected async generator but got {response_type}")
            
            primary_breaker.record_success()
            return (response, model)
        except Exception as e:
            primary_breaker.record_failure()
            logger.error(f"L4.tools [circuit_breaker] - Primary model {model} failed: {e}")
            
            # Don't try fallback immediately if circuit just opened
            if primary_breaker.is_open():
                logger.warning(f"L4.tools [circuit_breaker] - Circuit opened for {model}")
    
    # Try fallback model
    fallback_model = config.get("fallback_model")
    if fallback_model and fallback_model != model:
        fallback_breaker = get_circuit_breaker(f"model_{fallback_model}", failure_threshold=cb_failures, reset_timeout=cb_reset)
        
        if not fallback_breaker.is_open():
            logger.info(f"L4.tools [circuit_breaker] - Trying fallback model: {fallback_model}")
            try:
                # Rebuild prompt fresh for the fallback model if a rebuilder is provided
                fb_prompt = prompt
                if rebuild_prompt_func is not None:
                    try:
                        fb_prompt = await rebuild_prompt_func(fallback_model)
                    except Exception as rb_err:
                        logger.warning(f"L4.tools [circuit_breaker] - Fallback prompt rebuild failed, using existing prompt: {rb_err}")
                # For fallback, pass same unified kwargs
                fb_kwargs: Dict[str, Any] = dict(model_kwargs or {})
                response = await model_selector.call_model(
                    fb_prompt,
                    model=fallback_model,
                    tools=tool_schemas,
                    disable_internal_fallback=True,
                    **fb_kwargs,
                )
                fallback_breaker.record_success()
                return (response, fallback_model)
            except Exception as fallback_error:
                fallback_breaker.record_failure()
                logger.error(f"L4.tools [circuit_breaker] - Fallback model {fallback_model} failed: {fallback_error}")
    
    # Both models failed or circuits are open - provide graceful degradation
    logger.error("L4.tools [circuit_breaker] - All model circuits open or failed, providing fallback response")
    
    # Create a minimal response that allows the system to continue
    class FallbackResponse:
        def __init__(self):
            self.choices = [type('obj', (object,), {
                'message': type('obj', (object,), {
                    'content': "I'm experiencing technical difficulties with my AI models. Please try again in a few minutes.",
                    'tool_calls': None
                })()
            })()]
    
    return (FallbackResponse(), model)  # Return original model name for synthetic fallback


async def execute_single_tool(
    tool_index: int, tool_call: Dict[str, Any], context: Optional[Dict[str, Any]] = None
) -> tuple[Dict[str, Any], Dict[str, Any]]:
    """Execute a single tool call with enhanced error handling."""
    
    tool_name = tool_call.get("name", "unknown")
    arguments = tool_call.get("arguments", {})

    if tool_call.get("__guard_skip"):
        reason = tool_call.get("__guard_reason") or (
            f"Tool '{tool_name}' execution skipped by safety guard."
        )
        logger.debug(
            "L4.tools [guard] - Returning synthetic result for %s: %s",
            tool_name,
            reason,
        )
        return {
            "success": False,
            "error": reason,
            "tool_name": tool_name,
            "guarded": True,
            "output": reason,
        }, tool_call

    # Get circuit breaker for this tool
    # Dev-tunable breaker for tools
    tool_failures = int(os.getenv("THEO_CB_TOOL_FAILS", "8") or 8)
    tool_reset = int(os.getenv("THEO_CB_TOOL_RESET", "600") or 600)
    tool_breaker = get_circuit_breaker(f"tool_{tool_name}", failure_threshold=tool_failures, reset_timeout=tool_reset)
    
    # Check if tool circuit is open
    if tool_breaker.is_open():
        logger.warning(f"L4.tools [circuit_breaker] - Tool '{tool_name}' circuit is open, skipping execution")
        return {
            "success": False,
            "error": f"Tool '{tool_name}' is temporarily unavailable (circuit breaker active)",
            "tool_name": tool_name,
            "circuit_breaker_active": True
        }, tool_call
    
    try:
        logger.debug(
            f"L4.tools [execution] - Executing tool {tool_index}: {tool_name}"
        )
        
        # Execute the tool
        result = await execute_tool_call(tool_call, None, context=context)
        
        # Record success for circuit breaker
        if result.get('success', False):
            tool_breaker.record_success()
        else:
            tool_breaker.record_failure()
        
        logger.debug(
            f"L4.tools [execution] - Tool {tool_index} completed: {result.get('success', False)}"
        )
        
        return result, tool_call
        
    except Exception as e:
        # Record failure for circuit breaker
        tool_breaker.record_failure()
        
        logger.error(
            f"L4.tools [execution] - Tool '{tool_name}' execution failed: {e}",
            exc_info=True,
        )
        
        # Return graceful error instead of raising exception
        return {
            "success": False,
            "error": f"Tool '{tool_name}' execution failed: {str(e)}",
            "tool_name": tool_name,
            "temporary_failure": True
        }, tool_call


# Streaming removed: all providers use unified non-streaming calls


# Export for tool registry
__all__ = ["process_tool_orchestration", "ToolOrchestrationError", "ToolExecutionError"]
