"""
OpenAI Model Call Implementation

This module provides functionality to call OpenAI models using the Responses API
for stateful, multi-turn conversations.
"""

from collections.abc import Mapping, Sequence
from typing import Any, Dict, List, Optional
import json

from openai import OpenAI

try:  # OpenAI >= 1.0.0
    from openai.types.responses import Response
except (ModuleNotFoundError, ImportError):  # Back-compat for legacy SDKs
    try:
        from openai.types import Response  # type: ignore[attr-defined]
    except (ModuleNotFoundError, ImportError):
        Response = Any  # fallback for mocks in tests

from .base_model import BaseModelCall, ModelResponseError
from utils.logger import get_logger
import asyncio
from typing import AsyncGenerator
from utils.prompt_labels import (
    AVAILABLE_TOOLS_HEADER,
    CHAT_HISTORY_HEADER,
    CURRENT_MESSAGE_HEADER,
    MEMORY_BANK_HEADER,
    WORKING_MEMORY_HEADER,
    SYSTEM_INSTRUCTIONS_HEADER,
)
from utils.prompt_printer import extract_prompt_sections


def _coerce_stream_text(value: Any, depth: int = 0, visited: Optional[set[int]] = None) -> str:
    if visited is None:
        visited = set()
    if value is None or depth > 5:
        return ""
    if isinstance(value, str):
        return value
    if isinstance(value, (int, float)):
        return str(value)
    obj_id = id(value)
    if obj_id in visited:
        return ""
    visited.add(obj_id)
    try:
        if isinstance(value, Mapping):
            text_field = value.get("text")
            if isinstance(text_field, str) and text_field:
                return text_field
            content_field = value.get("content")
            if isinstance(content_field, Sequence) and not isinstance(content_field, (str, bytes, bytearray)):
                parts = []
                for item in content_field:
                    part_text = _coerce_stream_text(item, depth + 1, visited)
                    if part_text:
                        parts.append(part_text)
                if parts:
                    return "".join(parts)
            for key in ("delta", "value"):
                nested = value.get(key)
                text = _coerce_stream_text(nested, depth + 1, visited)
                if text:
                    return text
            return ""
        if isinstance(value, Sequence) and not isinstance(value, (str, bytes, bytearray)):
            parts_seq = []
            for item in value:
                part_text = _coerce_stream_text(item, depth + 1, visited)
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
                text = _coerce_stream_text(attr_val, depth + 1, visited)
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
                    text = _coerce_stream_text(dumped, depth + 1, visited)
                    if text:
                        return text
        return ""
    finally:
        visited.discard(obj_id)


class OpenAIResponseError(ModelResponseError):
    """Custom exception for OpenAI API errors."""


class OpenAIModelCall(BaseModelCall):
    """OpenAI model call implementation."""

    def __init__(self):
        """Initialize OpenAI model call."""
        super().__init__("openai")

    def _get_api_key(self, config: Dict[str, Any]) -> Optional[str]:
        """Get OpenAI API key from config."""
        from utils.config_loader import get_config_value

        return get_config_value(config, "api_keys.openai")

    def _get_default_model(self) -> str:
        """Get default OpenAI model."""
        return "gpt-5"

    async def _make_api_call(
        self, prompt: str, api_key: str, model: str, stream: bool, **kwargs
    ):
        """Make OpenAI API call.
        
        Returns:
            - If stream=False: Response object
            - If stream=True: AsyncGenerator yielding event dictionaries
        """
        client = OpenAI(api_key=api_key)

        # Handle conversation format - convert to input format
        if isinstance(prompt, list):
            input_messages = prompt
        else:
            # Parse system instructions from prompt for GPT-5 models
            input_messages = self._parse_prompt_for_system_instructions(prompt, model)

        request_params = {
            "model": model,
            "input": input_messages,
            "stream": stream,
        }

        try:
            model_lc = str(model).lower()
        except Exception:
            model_lc = ""

        # Continuations: support previous_response_id and append function_call_output items
        # per OpenAI Responses API docs (no 'tool_results' param is supported).
        try:
            prev_id = kwargs.get("previous_response_id")
            if prev_id:
                request_params["previous_response_id"] = prev_id
        except Exception:
            pass
        # If caller provided tool_results (provider-agnostic), translate to
        # Responses API input items of type 'function_call_output'.
        try:
            tool_results = kwargs.get("tool_results")
            if isinstance(tool_results, list) and tool_results:
                # Ensure input_messages is a list we can extend
                if not isinstance(input_messages, list):
                    input_messages = [input_messages]
                for tr in tool_results:
                    try:
                        call_id = tr.get("tool_call_id") or tr.get("call_id") or ""
                        output_text = tr.get("output") if isinstance(tr.get("output"), str) else str(tr.get("output"))
                        # Append as function_call_output item
                        input_messages.append({
                            "type": "function_call_output",
                            "call_id": call_id,
                            "output": output_text or "",
                        })
                    except Exception:
                        continue
        except Exception:
            pass

        # Add tools if provided - convert to OpenAI format
        if "tools" in kwargs and kwargs["tools"]:
            openai_tools = self._convert_tools_to_openai_format(
                kwargs["tools"]
            )

            web_search_config: Optional[Dict[str, Any]] = None
            for tool_def in openai_tools:
                if isinstance(tool_def, dict) and tool_def.get("type") in {"web_search_preview", "web_search"}:
                    web_search_config = tool_def
                    break

            if web_search_config is None:
                # GPT-5 chat variants require the legacy `web_search` tool, whereas
                # GPT-5 reasoning models and other modern Responses models accept
                # `web_search_preview` (see docs/openai/web_search.md).
                requires_legacy_web_search = ("gpt-5" in model_lc and "chat" in model_lc)
                web_search_type = "web_search" if requires_legacy_web_search else "web_search_preview"
                web_search_config = {"type": web_search_type}
                openai_tools.append(web_search_config)

            # Optional: Add search context size from config if available
            try:
                from utils.config_loader import get_config_value, load_config
                cfg = kwargs.get("config") or load_config()
                ctx_size = get_config_value(cfg, "web_search_context_size", "medium")
                if ctx_size in ("low", "medium", "high"):
                    web_search_config["search_context_size"] = ctx_size
            except Exception:
                pass
            
            # Optional: Add user location from config if available
            try:
                from utils.config_loader import get_config_value, load_config
                cfg = kwargs.get("config") or load_config()
                loc = get_config_value(cfg, "web_search_user_location") or {}
                if isinstance(loc, dict) and loc.get("country"):
                    web_search_config["user_location"] = {"type": "approximate", **loc}
            except Exception:
                pass
            
            request_params["tools"] = openai_tools
            
            # Tool selection policy: allow caller override; default to "auto"
            try:
                tc_override = kwargs.get("tool_choice")
                if tc_override is not None:
                    request_params["tool_choice"] = tc_override
                else:
                    request_params["tool_choice"] = "auto"
            except Exception:
                request_params["tool_choice"] = "auto"
            self.logger.debug(
                f"[OPENAI.FUNCTION_CALL] Using {len(openai_tools)} tools (ensured native {web_search_config['type']}), tool_choice={request_params.get('tool_choice')}"
            )

        # Add structured outputs and merge optional text settings (verbosity)
        text_payload = None
        if "text_format" in kwargs and kwargs["text_format"]:
            text_payload = {"format": kwargs["text_format"]}
        if "text" in kwargs and isinstance(kwargs["text"], dict):
            if text_payload is None:
                text_payload = {}
            # Do not overwrite format if already set by text_format
            for k, v in kwargs["text"].items():
                if k == "format" and text_payload.get("format") is not None:
                    continue
                text_payload[k] = v

        # Default verbosity per model: GPT-5 reasoning → high, chat-latest → medium
        is_gpt5_family = model_lc.startswith("gpt-5")
        is_chat_latest = (model_lc.startswith("gpt-5-chat-latest") or ("chat-latest" in model_lc))
        # Only GPT-5 reasoning variants (not chat-latest) support reasoning items and encrypted content
        is_gpt5_reasoning = is_gpt5_family and not is_chat_latest
        if is_gpt5_reasoning:
            if text_payload is None:
                text_payload = {"verbosity": "high"}
            else:
                text_payload.setdefault("verbosity", "high")
        elif is_chat_latest:
            if text_payload is None:
                text_payload = {"verbosity": "medium"}
            else:
                try:
                    if text_payload.get("verbosity") not in (None, "medium"):
                        text_payload["verbosity"] = "medium"
                except Exception:
                    text_payload["verbosity"] = "medium"

        if text_payload is not None:
            request_params["text"] = text_payload
            if isinstance(text_payload.get("format"), dict):
                self.logger.debug(
                    f"[OPENAI.STRUCTURED] Using structured outputs: {text_payload['format'].get('name', 'unnamed')}"
                )

        # Per reasoning docs, include encrypted reasoning content only for GPT-5 family
        # Only enable encrypted reasoning content on GPT-5 reasoning models, not chat-latest
        try:
            if is_gpt5_reasoning:
                include_list = request_params.get("include")
                if not isinstance(include_list, list):
                    include_list = []
                if "reasoning.encrypted_content" not in include_list:
                    include_list.append("reasoning.encrypted_content")
                request_params["include"] = include_list
        except Exception:
            pass

        # Add reasoning settings only for GPT-5 family
        if is_gpt5_reasoning:
            if "reasoning" in kwargs and isinstance(kwargs["reasoning"], dict):
                # Shallow copy to avoid mutating caller
                request_params["reasoning"] = dict(kwargs["reasoning"])
            else:
                request_params["reasoning"] = {}

            # Always request summaries so planning iterations surface reasoning text
            try:
                request_params["reasoning"].setdefault("summary", "auto")
                request_params["reasoning"].setdefault("effort", "medium")
                if stream:
                    self.logger.debug("[OPENAI.GPT5.STREAM] Enabled reasoning summaries with summary=auto")
                else:
                    self.logger.debug("[OPENAI.GPT5] Applied reasoning settings with summary=auto")
            except Exception:
                pass

        try:
            stream_or_response = client.responses.create(**request_params)
            
            # If streaming, return async generator
            if stream:
                return self._handle_stream_events(stream_or_response, model)
            
            # Non-streaming: return Response object directly
            return stream_or_response
        except Exception as e:
            # Log concise request summary to aid troubleshooting
            try:
                roles = [m.get("role") for m in (request_params.get("input") or []) if isinstance(m, dict)]
            except Exception:
                roles = []
            tools_count = len(request_params.get("tools") or [])
            self.logger.error(
                f"[OPENAI.ERROR] create failed (model={request_params.get('model')}, stream={bool(request_params.get('stream'))}, roles={roles}, tools={tools_count}): {str(e)[:200]}"
            )
            raise

    def _parse_prompt_for_system_instructions(self, prompt: str, model: str) -> List[Dict[str, str]]:
        """Convert the plain-text master prompt into Responses API messages."""

        sections = extract_prompt_sections(prompt)
        if not sections:
            return [{"role": "user", "content": prompt}]

        messages: List[Dict[str, str]] = []

        # Message 1: Developer/System instructions + tools (AI's role and capabilities)
        developer_parts: List[str] = []
        sys_text = sections.get(SYSTEM_INSTRUCTIONS_HEADER)
        if sys_text:
            developer_parts.append(sys_text)
        tools_text = sections.get(AVAILABLE_TOOLS_HEADER)
        if tools_text:
            developer_parts.append(f"{AVAILABLE_TOOLS_HEADER}\n{tools_text}")

        if developer_parts:
            try:
                model_lc = str(model).lower()
            except Exception:
                model_lc = ""
            developer_role = "developer"
            if model_lc.startswith("text-") or model_lc.startswith("gpt-3.5"):
                developer_role = "system"
            messages.append(
                {
                    "role": developer_role,
                    "content": "\n\n".join(part for part in developer_parts if part).strip(),
                }
            )

        # Message 2: Context (working memory, memory bank, chat history)
        context_parts: List[str] = []
        for header in (WORKING_MEMORY_HEADER, MEMORY_BANK_HEADER, CHAT_HISTORY_HEADER):
            block = sections.get(header)
            if block:
                context_parts.append(f"{header}\n{block}".strip())

        if context_parts:
            messages.append(
                {
                    "role": "user",
                    "content": "\n\n".join(context_parts).strip(),
                }
            )

        # Message 3: Actual user query (the REAL user input)
        current_msg = sections.get(CURRENT_MESSAGE_HEADER)
        if current_msg:
            messages.append({"role": "user", "content": current_msg.strip()})
        elif not messages:
            # Fallback if no sections extracted
            messages.append({"role": "user", "content": prompt})

        return messages

    def get_reasoning_summary(self, response: Response) -> Optional[str]:
        """Best-effort extraction of reasoning summary text from a non-streaming Response."""
        try:
            # Preferred: top-level reasoning.summary
            reasoning = getattr(response, "reasoning", None)
            if reasoning is not None:
                summary = getattr(reasoning, "summary", None)
                if isinstance(summary, str) and summary.strip():
                    return summary
            
            # Some SDKs expose nested response.reasoning.summary
            resp_obj = getattr(response, "response", None)
            if resp_obj is not None:
                r2 = getattr(resp_obj, "reasoning", None)
                if r2 is not None:
                    s2 = getattr(r2, "summary", None)
                    if isinstance(s2, str) and s2.strip():
                        return s2
            
            # Inspect output nodes for a reasoning-like text node
            out = getattr(response, "output", None)
            if out:
                for i, node in enumerate(out):
                    try:
                        node_type = getattr(node, "type", None)
                        if node_type in ("reasoning", "reasoning_summary"):
                            content = getattr(node, "content", None)
                            if content:
                                for c in content:
                                    t = getattr(c, "text", None)
                                    if t and str(t).strip():
                                        return str(t)
                    except Exception:
                        continue
        except Exception:
            pass
        return None

    async def _handle_stream_events(self, stream, model: str) -> AsyncGenerator[Dict[str, Any], None]:
        """Process OpenAI streaming events and yield normalized event dictionaries.
        
        Uses a background thread to read the synchronous stream, avoiding event loop blocking.
        
        Yields event dictionaries with types:
        - reasoning_summary: {type, delta} - incremental reasoning summary text
        - reasoning_summary_clear: {type} - optional signal to reset summaries (provider-driven)
        - output.delta: {type, text} - incremental assistant response text
        - function_call: {type, ...} - function call request
        - done: {type} - stream completion (may include final_response for function calls)
        """
        import threading
        
        accumulated_summary = []  # Track summary segments for logging (shared with thread)
        final_response_obj = None  # Store final response for function call extraction
        current_response_id: Optional[str] = None  # Track latest response_id for downstream consumption
        
        stream_type = type(stream).__name__
        self.logger.debug(f"[OPENAI.STREAM] Processing stream of type: {stream_type}")
        
        # Background thread to read synchronous stream and push events to async queue
        loop = asyncio.get_event_loop()
        q: asyncio.Queue = asyncio.Queue()
        
        def _push(item: Optional[Dict[str, Any]]):
            """Push event from background thread to async queue thread-safely."""
            try:
                payload = item
                if item and isinstance(item, dict) and current_response_id:
                    if item.get("response_id") is None:
                        payload = dict(item)
                        payload["response_id"] = current_response_id
                loop.call_soon_threadsafe(q.put_nowait, payload)
            except Exception:
                pass
        
        def _stream_thread():
            """Background thread that iterates the synchronous stream."""
            nonlocal final_response_obj, accumulated_summary, current_response_id
            # Track active function calls by output_index
            active_function_calls: Dict[int, Dict[str, Any]] = {}
            
            try:
                for event in stream:
                    try:
                        event_type = getattr(event, "type", None)
                        resp_id = getattr(event, "response_id", None)
                        if isinstance(resp_id, str) and resp_id:
                            current_response_id = resp_id
                        
                        # Handle reasoning summary text deltas
                        if event_type == "response.reasoning_summary_text.delta":
                            summary_text = _coerce_stream_text(getattr(event, "delta", None))
                            if summary_text:
                                accumulated_summary.append(summary_text)
                                _push({"type": "reasoning_summary", "delta": summary_text, "response_id": current_response_id})
                        
                        # Handle output text deltas (assistant response)
                        elif event_type == "response.output_text.delta":
                            delta_text = _coerce_stream_text(getattr(event, "delta", None))
                            if delta_text:
                                _push({"type": "output.delta", "text": delta_text, "response_id": current_response_id})
                        
                        # Handle function call item creation
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
                                    self.logger.debug(
                                        f"[OPENAI.STREAM.FUNCTION_CALL] Added function call at index {output_index}: {getattr(item, 'name', 'unknown')}"
                                    )
                        
                        # Handle function call arguments delta
                        elif event_type == "response.function_call_arguments.delta":
                            output_index = getattr(event, "output_index", None)
                            delta = _coerce_stream_text(getattr(event, "delta", None))
                            if output_index is not None and output_index in active_function_calls and delta:
                                active_function_calls[output_index]["arguments"] += delta
                        
                        # Handle function call arguments complete
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
                        
                        # Handle function call item complete
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
                                self.logger.debug(
                                    f"[OPENAI.STREAM.FUNCTION_CALL] Complete function call: {function_call['name']} with {len(function_call['arguments'])} chars of arguments"
                                )
                                _push({
                                    "type": "function_call",
                                    "id": function_call["id"],
                                    "call_id": function_call["call_id"],
                                    "name": function_call["name"],
                                    "arguments": function_call["arguments"] or "{}",
                                    "response_id": current_response_id,
                                })
                        
                        # Handle completion
                        elif event_type == "response.done":
                            # Store final response for fallback function call extraction
                            final_response_obj = getattr(event, "response", None)
                            # Emit any remaining active function calls (fallback for incomplete streams)
                            for output_index, function_call in list(active_function_calls.items()):
                                self.logger.debug(
                                    f"[OPENAI.STREAM.FUNCTION_CALL] Emitting remaining function call at index {output_index}"
                                )
                                _push({
                                    "type": "function_call",
                                    "id": function_call["id"],
                                    "call_id": function_call["call_id"],
                                    "name": function_call["name"],
                                    "arguments": function_call["arguments"] or "{}",
                                })
                            active_function_calls.clear()
                            # Push done event (we'll handle function calls after draining queue)
                            _push({"type": "done", "response_id": current_response_id})
                            break
                        
                        # Handle errors
                        elif event_type == "error":
                            error_msg = str(getattr(event, "error", "Unknown streaming error"))
                            self.logger.error(f"[OPENAI.STREAM.ERROR] {error_msg}")
                            _push({"type": "error", "error": error_msg})
                            break
                    
                    except Exception as event_err:
                        self.logger.warning(
                            f"[OPENAI.STREAM] Event processing error: {event_err}",
                            exc_info=True
                        )
                        continue
            except Exception as stream_err:
                self.logger.error(
                    f"[OPENAI.STREAM] Stream iteration error: {stream_err}",
                    exc_info=True
                )
                _push({"type": "error", "error": str(stream_err)})
            finally:
                # Sentinel to signal stream completion
                _push(None)
        
        # Start background thread
        t = threading.Thread(target=_stream_thread, daemon=True)
        t.start()
        
        # Drain queue and yield events (this allows event loop to process other tasks)
        emitted_function_call_ids: set = set()  # Track emitted function calls to avoid duplicates from fallback
        
        try:
            while True:
                item = await q.get()
                if item is None:
                    # Stream thread finished - log summary stats
                    if accumulated_summary:
                        full_summary = "".join(accumulated_summary)
                        self.logger.debug(
                            f"[OPENAI.STREAM.REASONING] Total summary length: {len(full_summary)} chars"
                        )
                    break
                
                # Track emitted function calls to avoid duplicates from fallback extraction
                if item.get("type") == "function_call":
                    call_id = item.get("call_id") or item.get("id")
                    if call_id:
                        emitted_function_call_ids.add(call_id)
                
                # If this is the done event, extract and yield any missed function calls as fallback
                if item.get("type") == "done":
                    # Extract function calls from final response (fallback for any missed during streaming)
                    if final_response_obj:
                        function_calls = self.extract_function_calls(final_response_obj)
                        if function_calls:
                            for fc in function_calls:
                                call_id = fc.get("call_id") or fc.get("id")
                                # Only emit if we haven't already emitted this function call
                                if call_id and call_id not in emitted_function_call_ids:
                                    self.logger.debug(
                                        f"[OPENAI.STREAM.FUNCTION_CALL] Fallback extraction found missed function call: {fc.get('name')}"
                                    )
                                    emitted_function_call_ids.add(call_id)
                                    yield {"type": "function_call", **fc}
                
                # Yield the event (including done after function calls)
                yield item
        
        except Exception as drain_err:
            self.logger.error(
                f"[OPENAI.STREAM] Queue drainage error: {drain_err}",
                exc_info=True
            )
            yield {"type": "error", "error": str(drain_err)}

    def _convert_tools_to_openai_format(self, tools: list) -> list:
        """Convert tools to OpenAI Responses API format."""
        openai_tools = []

        for tool in tools:
            if isinstance(tool, dict):
                # Convert from standard format to OpenAI format
                parameters = tool.get("parameters", {}).copy()
                
                def enforce_strict_schema(obj: dict):
                    """Recursively enforce OpenAI strict-mode compliance on JSON schema objects.
                    - Set additionalProperties: False for any object schemas
                    - Ensure items' object schemas are strict too
                    - Force presence of 'required' on all object schemas including every key in properties
                    - Convert originally-optional properties to nullable by adding 'null' to their type
                    """
                    if not isinstance(obj, dict):
                        return
                    t = obj.get("type")
                    # If union type, pick first non-null for structural checks
                    effective_type = None
                    if isinstance(t, list):
                        effective_type = next((x for x in t if x != "null"), t[0] if t else None)
                    else:
                        effective_type = t

                    if effective_type == "object":
                        # Strict additionalProperties
                        obj["additionalProperties"] = False
                        props = obj.get("properties") or {}
                        if isinstance(props, dict):
                            # Determine originally-required keys (may be empty/None)
                            original_required = set(obj.get("required") or [])
                            # Make any non-required properties nullable
                            for key, pdef in props.items():
                                try:
                                    if key not in original_required and isinstance(pdef, dict):
                                        ptype = pdef.get("type")
                                        if isinstance(ptype, str):
                                            pdef["type"] = [ptype, "null"]
                                        elif isinstance(ptype, list):
                                            if "null" not in ptype:
                                                pdef["type"] = ptype + ["null"]
                                        # If property uses anyOf/oneOf and doesn't include a null schema,
                                        # we leave it as-is; the branch union already controls nullability.
                                except Exception:
                                    pass
                                # Recurse into property definitions
                                enforce_strict_schema(pdef)
                            # Require all properties by key presence
                            all_keys = list(props.keys())
                            obj["required"] = all_keys
                        # Recurse into nested schemas and any lists (e.g., oneOf/anyOf)
                        for k, v in list(obj.items()):
                            if isinstance(v, dict):
                                enforce_strict_schema(v)
                            elif isinstance(v, list):
                                for it in v:
                                    if isinstance(it, dict):
                                        enforce_strict_schema(it)
                    elif effective_type == "array":
                        items = obj.get("items")
                        if isinstance(items, dict):
                            enforce_strict_schema(items)
                    else:
                        # Primitive or union types: nothing extra, but recurse into children if any
                        for k, v in list(obj.items()):
                            if isinstance(v, dict):
                                enforce_strict_schema(v)
                            elif isinstance(v, list):
                                for it in v:
                                    if isinstance(it, dict):
                                        enforce_strict_schema(it)

                # Ensure additionalProperties is false for strict mode
                parameters["additionalProperties"] = False

                # Recursively enforce strictness across nested schemas
                enforce_strict_schema(parameters)

                # Normalize/strip composition keywords not permitted by OpenAI tool schema
                def _strip_or_flatten_compositions(obj: dict):
                    if not isinstance(obj, dict):
                        return
                    # Flatten simple anyOf(type=...) to a union 'type' list
                    if isinstance(obj.get("anyOf"), list):
                        types_union = []
                        all_simple = True
                        for br in obj["anyOf"]:
                            # Only treat branch as simple if it ONLY has a 'type' key (no 'items', 'properties', etc.)
                            if isinstance(br, dict) and br.get("type") and set(br.keys()) <= {"type"}:
                                bt = br.get("type")
                                if isinstance(bt, list):
                                    for tval in bt:
                                        if tval not in types_union:
                                            types_union.append(tval)
                                else:
                                    if bt not in types_union:
                                        types_union.append(bt)
                            else:
                                all_simple = False
                                break
                        if all_simple and types_union:
                            obj["type"] = types_union if len(types_union) > 1 else types_union[0]
                            obj.pop("anyOf", None)
                    # Remove oneOf entirely; OpenAI tool schema forbids it
                    if "oneOf" in obj:
                        obj.pop("oneOf", None)
                    # Recurse
                    for k, v in list(obj.items()):
                        if isinstance(v, dict):
                            _strip_or_flatten_compositions(v)
                        elif isinstance(v, list):
                            for it in v:
                                if isinstance(it, dict):
                                    _strip_or_flatten_compositions(it)

                _strip_or_flatten_compositions(parameters)
                
                openai_tool = {
                    "type": "function",
                    "name": tool.get("name"),
                    "description": tool.get("description", ""),
                    "parameters": parameters,
                    "strict": True,  # Enable strict mode for better reliability
                }
                openai_tools.append(openai_tool)

        return openai_tools

    def _extract_response_text(self, response: Response) -> str:
        """Extract text from OpenAI response."""
        # Prefer unified output_text when available
        try:
            unified = getattr(response, "output_text", None)
            if isinstance(unified, str) and unified.strip():
                return unified
        except Exception:
            pass

        # Only include assistant message text; ignore reasoning and function_call nodes
        text_content_parts: list[str] = []

        try:
            outputs = getattr(response, "output", None)
            if outputs:
                for output in outputs:
                    otype = getattr(output, "type", None)
                    # Keep assistant message or plain text node (older mocks), or unknown type with text content
                    if otype in ("message", "text") or True:
                        # Role guard (defensive)
                        if otype == "message":
                            role = getattr(output, "role", "assistant")
                            if str(role) != "assistant":
                                continue
                        content_list = getattr(output, "content", None)
                        if not content_list:
                            continue
                        for content in content_list:
                            # Responses API exposes content objects with .text for text chunks
                            if hasattr(content, "text") and isinstance(getattr(content, "text"), str):
                                text = getattr(content, "text")
                                if text:
                                    text_content_parts.append(text)
                            elif isinstance(content, dict) and isinstance(content.get("text"), str):
                                text_content_parts.append(content.get("text") or "")
                            elif isinstance(content, str):
                                text_content_parts.append(content)
            # Join and return if collected
            joined = "".join(text_content_parts).strip()
            if joined:
                return joined
        except Exception:
            pass

        # Fallback: never expose raw object repr; return empty string
        return ""

    def _check_response_completion(self, response: Response) -> bool:
        """Check if OpenAI response is complete (no pending function calls)."""
        if hasattr(response, "output") and response.output:
            for output in response.output:
                # Check for top-level function calls - handle both legacy and GPT-5 formats
                if hasattr(output, "type") and (output.type == "function_call" or "tool_call" in output.type):
                    return False
                # Check for nested function calls in reasoning/text content
                elif hasattr(output, "type") and output.type in ("reasoning", "text") and hasattr(output, "content"):
                    if output.content:
                        for content_item in output.content:
                            if hasattr(content_item, "type") and (content_item.type == "function_call" or "tool_call" in content_item.type):
                                return False
                            elif hasattr(content_item, "content") and content_item.content:
                                for nested_item in content_item.content:
                                    if hasattr(nested_item, "type") and (nested_item.type == "function_call" or "tool_call" in nested_item.type):
                                        return False
        return True

    def extract_function_calls(self, response: Response) -> list:
        """Extract function calls from OpenAI response, including those in reasoning context."""
        function_calls = []

        if hasattr(response, "output") and response.output:
            for output in response.output:
                # Standard function call detection - handle both legacy and GPT-5 formats
                if hasattr(output, "type") and (output.type == "function_call" or "tool_call" in output.type):
                    # GPT-5 Responses API uses 'input' instead of 'arguments'
                    # Prefer plain string/dict values; avoid Mock truthiness
                    arguments_or_input = None
                    if hasattr(output, "arguments"):
                        av = getattr(output, "arguments")
                        if isinstance(av, (str, dict)):
                            arguments_or_input = av
                    if arguments_or_input is None and hasattr(output, "input"):
                        iv = getattr(output, "input")
                        if isinstance(iv, (str, dict)):
                            arguments_or_input = iv
                    if arguments_or_input is None:
                        arguments_or_input = "{}"
                    function_call = {
                        "id": getattr(output, "id", ""),
                        "call_id": getattr(output, "call_id", ""),
                        "name": getattr(output, "name", ""),
                        "arguments": arguments_or_input,
                    }
                    function_calls.append(function_call)
                    self.logger.debug(
                        f"[OPENAI.FUNCTION_CALL] Found function call: {function_call['name']} (type: {output.type})"
                    )
                
                # GPT-5 reasoning context: check if there are nested function calls in reasoning/text nodes
                elif hasattr(output, "type") and output.type in ("reasoning", "text") and hasattr(output, "content"):
                    # Look for function calls nested within reasoning/text content
                    if output.content:
                        for content_item in output.content:
                            if hasattr(content_item, "type") and (content_item.type == "function_call" or "tool_call" in content_item.type):
                                # GPT-5 Responses API uses 'input' instead of 'arguments'
                                arguments_or_input = None
                                if hasattr(content_item, "arguments"):
                                    av = getattr(content_item, "arguments")
                                    if isinstance(av, (str, dict)):
                                        arguments_or_input = av
                                if arguments_or_input is None and hasattr(content_item, "input"):
                                    iv = getattr(content_item, "input")
                                    if isinstance(iv, (str, dict)):
                                        arguments_or_input = iv
                                if arguments_or_input is None:
                                    arguments_or_input = "{}"
                                function_call = {
                                    "id": getattr(content_item, "id", ""),
                                    "call_id": getattr(content_item, "call_id", ""),
                                    "name": getattr(content_item, "name", ""),
                                    "arguments": arguments_or_input,
                                }
                                function_calls.append(function_call)
                                self.logger.debug(
                                    f"[OPENAI.FUNCTION_CALL] Found nested function call in {output.type}: {function_call['name']} (type: {content_item.type})"
                                )
                            # Also check if content_item has nested content (deeper nesting)
                            elif hasattr(content_item, "content") and content_item.content:
                                for nested_item in content_item.content:
                                    if hasattr(nested_item, "type") and (nested_item.type == "function_call" or "tool_call" in nested_item.type):
                                        # GPT-5 Responses API uses 'input' instead of 'arguments'
                                        arguments_or_input = None
                                        if hasattr(nested_item, "arguments"):
                                            av = getattr(nested_item, "arguments")
                                            if isinstance(av, (str, dict)):
                                                arguments_or_input = av
                                        if arguments_or_input is None and hasattr(nested_item, "input"):
                                            iv = getattr(nested_item, "input")
                                            if isinstance(iv, (str, dict)):
                                                arguments_or_input = iv
                                        if arguments_or_input is None:
                                            arguments_or_input = "{}"
                                        function_call = {
                                            "id": getattr(nested_item, "id", ""),
                                            "call_id": getattr(nested_item, "call_id", ""),
                                            "name": getattr(nested_item, "name", ""),
                                            "arguments": arguments_or_input,
                                        }
                                        function_calls.append(function_call)
                                        self.logger.debug(
                                            f"[OPENAI.FUNCTION_CALL] Found deeply nested function call: {function_call['name']} (type: {nested_item.type})"
                                        )

        if function_calls:
            return function_calls

        # Fallback for synthesized streaming responses that stash _function_calls
        fallback_calls = getattr(response, "_function_calls", None)
        if fallback_calls:
            for call in fallback_calls:
                try:
                    if isinstance(call, dict):
                        call_dict = dict(call)
                    else:
                        call_dict = {
                            "id": getattr(call, "id", None),
                            "call_id": getattr(call, "call_id", None),
                            "name": getattr(call, "name", None),
                            "arguments": getattr(call, "arguments", None),
                            "input": getattr(call, "input", None),
                        }
                    raw_arguments = call_dict.get("arguments")
                    if isinstance(raw_arguments, dict):
                        arguments = json.dumps(raw_arguments)
                    elif isinstance(raw_arguments, str) and raw_arguments.strip():
                        arguments = raw_arguments
                    else:
                        alt = call_dict.get("input")
                        if isinstance(alt, dict):
                            arguments = json.dumps(alt)
                        elif isinstance(alt, str) and alt.strip():
                            arguments = alt
                        else:
                            arguments = "{}"
                    function_calls.append(
                        {
                            "id": call_dict.get("id") or "",
                            "call_id": call_dict.get("call_id") or call_dict.get("id") or "",
                            "name": call_dict.get("name") or "",
                            "arguments": arguments,
                        }
                    )
                except Exception:
                    continue

        return function_calls



"""
Streaming note: non-streaming only (handled consistently by orchestrator).
"""



# Create singleton instance
_openai_model = OpenAIModelCall()


async def call_model(
    prompt: str,
    config: Optional[Dict[str, Any]] = None,
    model: Optional[str] = None,
    stream: bool = False,
    **kwargs,
) -> Response:
    """Call an OpenAI model (module-level helper used by ModelSelector)."""
    return await _openai_model.call_model(prompt, config, model, stream, **kwargs)


def get_response_text(response: Response) -> str:
    """Extract text from an OpenAI response (used by ModelSelector)."""
    return _openai_model.get_response_text(response)


def get_reasoning_summary(response: Response) -> Optional[str]:
    """Extract reasoning summary from OpenAI response (non-streaming fallback)."""
    return _openai_model.get_reasoning_summary(response)


def get_response_id(response: Response) -> Optional[str]:
    """Get OpenAI response ID (used by ModelSelector)."""
    return _openai_model.get_response_id(response)


def is_response_complete(response: Response) -> bool:
    """Return True if the OpenAI response is complete (used by ModelSelector)."""
    return _openai_model.is_response_complete(response)


def extract_function_calls(response: Response) -> list:
    """Extract structured function calls from an OpenAI response (used by ModelSelector)."""
    return _openai_model.extract_function_calls(response)
