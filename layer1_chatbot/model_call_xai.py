"""xAI Model Call Implementation.

This module calls xAI's chat completions endpoint while mirroring the
behavioural contract used by the OpenAI provider. The goal is to keep prompt
ordering, tool replay, structured outputs, and error handling aligned so that
switching between providers does not change Theo's semantics.
"""

from __future__ import annotations

import asyncio
import json
from typing import Any, Dict, List, Optional, Sequence

import requests

from .base_model import BaseModelCall, ModelResponseError
from utils.prompt_printer import extract_prompt_sections
from utils.prompt_labels import (
    SYSTEM_INSTRUCTIONS_HEADER,
    AVAILABLE_TOOLS_HEADER,
    MEMORY_BANK_HEADER,
    WORKING_MEMORY_HEADER,
    CHAT_HISTORY_HEADER,
    CURRENT_MESSAGE_HEADER,
)

XAI_API_URL = "https://api.x.ai/v1/chat/completions"


class XAIResponseError(ModelResponseError):
    """Custom exception raised when the xAI API returns an error."""


class XAIResponse:
    """Lightweight wrapper around the xAI chat completion payload."""

    def __init__(self, payload: Dict[str, Any]):
        """Initialise the response wrapper.

        Args:
            payload: Raw JSON dictionary returned by the xAI API.
        """
        from utils.logger import get_logger
        logger = get_logger("xai.response")

        self.payload = payload
        self.id = payload.get("id")
        choices = payload.get("choices") or []
        first_choice = choices[0] if choices else {}
        message = first_choice.get("message") or {}

        self.message = message
        self.role = message.get("role", "assistant")
        self.tool_calls = message.get("tool_calls") or []
        self.usage = payload.get("usage") or {}
        self.content = message.get("content")
        
        # If there are tool calls and content is just raw JSON representation, ignore content
        if self.tool_calls and isinstance(self.content, str):
            try:
                parsed = json.loads(self.content.strip())
                if isinstance(parsed, dict) and "tool_calls" in parsed:
                    logger.debug("[XAI.RESPONSE] Detected raw JSON tool calls in content, ignoring")
                    self.content = None
            except (json.JSONDecodeError, ValueError):
                pass
        
        # Check if native live search was actually used
        num_sources = self.usage.get("num_sources_used", 0)
        if num_sources > 0:
            logger.info(f"[XAI.SEARCH] Native search USED: {num_sources} sources")
        else:
            logger.warning(f"[XAI.SEARCH] Native search NOT used (num_sources_used=0) despite mode=auto")
        
        # Debug logging for content structure (helps diagnose search result parsing)
        if isinstance(self.content, list):
            content_types = [p.get("type") if isinstance(p, dict) else type(p).__name__ for p in self.content]
            logger.debug(f"[XAI.RESPONSE] Content is list with {len(self.content)} parts: types={content_types}")
        elif isinstance(self.content, str):
            logger.debug(f"[XAI.RESPONSE] Content is string: length={len(self.content)}")
        else:
            logger.debug(f"[XAI.RESPONSE] Content type: {type(self.content).__name__}")
        
        # Extract reasoning_content for reasoning models (grok-4, grok-3, etc.)
        # This is critical for conversation continuity per xAI docs
        self.reasoning_content = message.get("reasoning_content")
        self._text = self._coerce_text(self.content)
        
        # Log extracted text length for debugging
        logger.debug(f"[XAI.RESPONSE] Extracted text length: {len(self._text)} chars")

    @staticmethod
    def _coerce_text(content: Any) -> str:
        """Convert arbitrary message content into a usable string.
        
        Handles both plain text and structured content from xAI's live search,
        which may include multiple content blocks with different types.
        """

        if isinstance(content, str):
            return content

        if isinstance(content, list):
            segments: List[str] = []
            for part in content:
                if isinstance(part, dict):
                    part_type = part.get("type", "").lower()
                    
                    # Skip search-related metadata blocks that shouldn't appear in user-facing text
                    if part_type in ("search_call", "search_query", "search_metadata", "citation", "source"):
                        continue
                    
                    # Skip blocks that look like search query echoes or metadata
                    if "text" in part:
                        text_content = str(part["text"]).strip()
                        # Filter out search query echoes that might leak through
                        if (text_content.startswith("today's top news headlines") or
                            text_content.startswith("search query:") or
                            text_content.startswith("query:") or
                            text_content.startswith("searching for:") or
                            len(text_content) < 10 and any(keyword in text_content.lower() for keyword in ["headlines", "news", "search", "query"])):
                            continue
                        segments.append(text_content)
                    # Fallback to "content" field
                    elif "content" in part:
                        content_text = str(part["content"]).strip()
                        # Apply same filtering to content field
                        if not (content_text.startswith("today's top news headlines") or
                                content_text.startswith("search query:") or
                                content_text.startswith("query:") or
                                content_text.startswith("searching for:") or
                                (len(content_text) < 10 and any(keyword in content_text.lower() for keyword in ["headlines", "news", "search", "query"]))):
                            segments.append(content_text)
                    # Fallback to "value" field
                    elif "value" in part:
                        value_text = str(part["value"]).strip()
                        # Apply same filtering to value field
                        if not (value_text.startswith("today's top news headlines") or
                                value_text.startswith("search query:") or
                                value_text.startswith("query:") or
                                value_text.startswith("searching for:") or
                                (len(value_text) < 10 and any(keyword in value_text.lower() for keyword in ["headlines", "news", "search", "query"]))):
                            segments.append(value_text)
                    # For any other dict, try to extract meaningful text
                    elif part_type in ("search_result", "search_call"):
                        # Skip metadata-only blocks, but log them
                        continue
                elif isinstance(part, str):
                    # Apply filtering to string parts as well
                    part_text = part.strip()
                    if not (part_text.startswith("today's top news headlines") or
                            part_text.startswith("search query:") or
                            part_text.startswith("query:") or
                            part_text.startswith("searching for:") or
                            (len(part_text) < 10 and any(keyword in part_text.lower() for keyword in ["headlines", "news", "search", "query"]))):
                        segments.append(part_text)
            return "".join(segments)

        if content is None:
            return ""

        return str(content)

    @property
    def text(self) -> str:
        """Return the assistant-visible text."""

        return self._text


class XAIModelCall(BaseModelCall):
    """xAI model call implementation providing OpenAI-parity semantics."""

    def __init__(self) -> None:
        """Initialise the provider wrapper."""

        super().__init__("xai")

    def _get_api_key(self, config: Dict[str, Any]) -> Optional[str]:
        """Look up the xAI API key from configuration."""

        from utils.config_loader import get_config_value

        return get_config_value(config, "api_keys.xai")

    def _get_default_model(self) -> str:
        """Return the default Grok model."""

        return "grok-4"

    async def _make_api_call(
        self,
        prompt: Any,
        api_key: str,
        model: str,
        stream: bool,
        **kwargs: Any,
    ) -> XAIResponse:
        """Call the xAI chat completions API."""

        if stream:
            raise XAIResponseError(
                "Streamed responses are handled by the streaming orchestrator; "
                "non-streaming call received with stream=True."
            )

        model = self._normalise_model_name(model)
        is_reasoning = self._is_reasoning_model(model)
        timeout = 3600 if is_reasoning else 1800  # 30 min for regular, 1 hour for reasoning

        messages = self._convert_prompt_to_messages(prompt)

        # Per xAI docs: When continuing after tool calls, we need to include:
        # 1. The assistant message with tool_calls (from previous response)
        # 2. The tool result messages (with tool_call_id matching the calls)
        tool_results = kwargs.get("tool_results")
        if isinstance(tool_results, list) and tool_results:
            # First, append the previous assistant message with tool_calls
            prev_response = kwargs.get("xai_prev_response")
            if prev_response:
                try:
                    # Extract the assistant message from the previous response
                    prev_message = {
                        "role": "assistant",
                        "content": prev_response.content or "",
                    }
                    # Include tool_calls if present
                    if hasattr(prev_response, "tool_calls") and prev_response.tool_calls:
                        prev_message["tool_calls"] = prev_response.tool_calls
                    # CRITICAL: Include reasoning_content for reasoning models (grok-4, grok-3)
                    # Without this, the model loses reasoning context and may loop on tools
                    if hasattr(prev_response, "reasoning_content") and prev_response.reasoning_content:
                        prev_message["reasoning_content"] = prev_response.reasoning_content
                        self.logger.debug(f"[XAI.CONTINUATION] Included reasoning_content ({len(str(prev_response.reasoning_content))} chars)")
                    messages.append(prev_message)
                    self.logger.debug(f"[XAI.CONTINUATION] Appended previous assistant message with {len(prev_response.tool_calls)} tool calls")
                except Exception as e:
                    self.logger.warning(f"[XAI.CONTINUATION] Failed to append previous response: {e}")
            
            # Then append the tool results
            self._append_tool_results(messages, tool_results)

        followup = kwargs.get("xai_followup_text")
        if isinstance(followup, str) and followup.strip():
            messages.append({"role": "user", "content": followup.strip()})

        payload: Dict[str, Any] = {
            "model": model,
            "messages": messages,
        }

        previous_id = kwargs.get("previous_response_id")
        if isinstance(previous_id, str) and previous_id.strip():
            payload["previous_response_id"] = previous_id.strip()

        response_format = self._extract_response_format(kwargs)
        if response_format:
            payload["response_format"] = response_format

        tools = kwargs.get("tools")
        if tools:
            converted_tools = self._convert_tools_to_xai_format(tools)
            if converted_tools:
                payload["tools"] = converted_tools
                payload["tool_choice"] = kwargs.get("tool_choice", "auto")

        # Enable native live search for xAI/Grok models by default
        # This works ALONGSIDE tools, not instead of them
        try:
            from utils.config_loader import get_config_value, load_config
            cfg = kwargs.get("config") or load_config()
            
            # Disabled by default for chess arena (uses custom web_search tool instead)
            enable_search = get_config_value(cfg, "xai_enable_live_search", False)  # DEFAULT: FALSE
            
            if enable_search:
                search_params = {"mode": "auto"}  # Let model decide when to search
                
                # CRITICAL: Set date range to ensure CURRENT data (not cached/old results)
                # Default to last 60 days unless config specifies otherwise
                from datetime import datetime, timedelta
                search_days_back = get_config_value(cfg, "xai_search_days_back", 60)
                if isinstance(search_days_back, int) and search_days_back > 0:
                    from_date = (datetime.now() - timedelta(days=search_days_back)).strftime("%Y-%m-%d")
                    search_params["from_date"] = from_date
                    self.logger.info(f"[XAI.SEARCH] Date range: from {from_date} to today (last {search_days_back} days)")
                
                # Optional: Add specific data sources from config if available
                sources = get_config_value(cfg, "xai_search_sources")
                if isinstance(sources, list) and sources:
                    search_params["sources"] = [{"type": src} for src in sources if isinstance(src, str)]
                
                # Optional: Return citations for transparency (default: true)
                return_citations = get_config_value(cfg, "xai_return_citations", True)
                if isinstance(return_citations, bool):
                    search_params["return_citations"] = return_citations
                
                payload["search_parameters"] = search_params
                self.logger.info(f"[XAI.SEARCH] Native live search enabled with mode=auto")
                self.logger.debug(f"[XAI.SEARCH] Parameters: {search_params}")
            else:
                self.logger.info(f"[XAI.SEARCH] Native live search disabled via config")
        except Exception as e:
            self.logger.warning(f"[XAI.SEARCH] Failed to configure live search: {e}")

        reasoning_effort = self._determine_reasoning_effort(model, kwargs)
        if reasoning_effort is not None:
            payload["reasoning_effort"] = reasoning_effort

        request_headers = {
            "Authorization": f"Bearer {api_key}",
            "Content-Type": "application/json",
        }

        response_data = await self._post_async(payload, request_headers, timeout)

        return XAIResponse(response_data)

    async def _post_async(
        self,
        payload: Dict[str, Any],
        headers: Dict[str, str],
        timeout: int,
    ) -> Dict[str, Any]:
        """Perform the HTTP POST on a worker thread to avoid blocking."""

        def _do_request() -> requests.Response:
            return requests.post(
                XAI_API_URL,
                json=payload,
                headers=headers,
                timeout=timeout,
            )

        response = await asyncio.to_thread(_do_request)

        try:
            response.raise_for_status()
        except Exception as exc:
            error_text = self._extract_error_text(response)
            raise XAIResponseError(
                f"xAI API call failed ({response.status_code}): {error_text}"
            ) from exc

        data = response.json()
        if not data or not isinstance(data, dict):
            raise XAIResponseError("xAI API returned an unexpected payload format")

        if not data.get("choices"):
            raise XAIResponseError("xAI API returned no choices")

        return data

    def _convert_prompt_to_messages(self, prompt: Any) -> List[Dict[str, Any]]:
        """Translate Theo's prompt payload into xAI chat messages."""

        messages: List[Dict[str, Any]] = []

        if isinstance(prompt, list):
            for item in prompt:
                msg = self._convert_single_message(item)
                if msg:
                    messages.append(msg)
        elif isinstance(prompt, str):
            sections = extract_prompt_sections(prompt)
            if sections:
                # Message 1: System instructions + tools (AI's role and capabilities)
                system_parts: List[str] = []
                sys_text = sections.get(SYSTEM_INSTRUCTIONS_HEADER)
                if sys_text:
                    system_parts.append(sys_text)
                tools_text = sections.get(AVAILABLE_TOOLS_HEADER)
                if tools_text:
                    system_parts.append(f"{AVAILABLE_TOOLS_HEADER}\n{tools_text}")

                if system_parts:
                    messages.append({"role": "system", "content": "\n\n".join(system_parts).strip()})

                # Message 2: Context (working memory, memory bank, chat history) - label as system for clarity
                context_blocks: List[str] = []
                for header in (WORKING_MEMORY_HEADER, MEMORY_BANK_HEADER, CHAT_HISTORY_HEADER):
                    block = sections.get(header)
                    if block:
                        context_blocks.append(f"{header}\n{block}".strip())

                if context_blocks:
                    messages.append({"role": "system", "content": "\n\n".join(context_blocks).strip()})

                # Message 3: Actual user query (the REAL user input)
                current_msg = sections.get(CURRENT_MESSAGE_HEADER)
                if current_msg:
                    messages.append({"role": "user", "content": current_msg.strip()})

            if not messages:
                messages.append({"role": "user", "content": prompt})
        else:
            messages.append({"role": "user", "content": str(prompt)})

        if not messages:
            messages.append({"role": "user", "content": ""})

        return messages

    def _convert_single_message(self, item: Any) -> Optional[Dict[str, Any]]:
        """Convert a single prompt item into an xAI-compatible message dict."""

        data: Dict[str, Any]
        if item is None:
            return None

        if isinstance(item, dict):
            data = dict(item)
        elif hasattr(item, "model_dump") and callable(getattr(item, "model_dump")):
            data = item.model_dump()  # type: ignore[assignment]
        else:
            role = getattr(item, "role", "user")
            content = getattr(item, "content", "")
            data = {"role": role, "content": content}

        role = str(data.get("role") or data.get("type") or "user").lower()
        content = data.get("content")

        if role == "developer":
            role = "system"

        if isinstance(content, (dict, tuple)):
            # Normalise non-supported sequence types into strings
            content = str(content)

        message: Dict[str, Any] = {"role": role, "content": content}

        if not isinstance(message["content"], (str, list)):
            message["content"] = "" if message["content"] is None else str(message["content"])  # type: ignore[assignment]

        if role == "assistant" and data.get("tool_calls"):
            message["tool_calls"] = data.get("tool_calls")

        if data.get("name"):
            message["name"] = data.get("name")

        if role == "tool":
            tcid = data.get("tool_call_id") or data.get("call_id")
            if tcid:
                message["tool_call_id"] = tcid

        return message

    def _append_tool_results(
        self,
        messages: List[Dict[str, Any]],
        tool_results: Sequence[Dict[str, Any]],
    ) -> None:
        """Append tool result messages, avoiding duplicates."""

        existing_ids = {
            m.get("tool_call_id")
            for m in messages
            if isinstance(m, dict) and m.get("role") == "tool"
        }

        for result in tool_results:
            if not isinstance(result, dict):
                continue

            call_id = result.get("tool_call_id") or result.get("call_id")
            output = result.get("output")

            if isinstance(output, (dict, list)):
                try:
                    output_str = json.dumps(output)
                except Exception:
                    output_str = str(output)
            else:
                output_str = str(output or "")

            tool_message: Dict[str, Any] = {
                "role": "tool",
                "content": output_str,
            }

            if isinstance(call_id, str) and call_id.strip():
                if call_id in existing_ids:
                    continue
                tool_message["tool_call_id"] = call_id
                existing_ids.add(call_id)

            messages.append(tool_message)

    def _convert_tools_to_xai_format(self, tools: Any) -> List[Dict[str, Any]]:
        """Convert provider-agnostic tool schemas into xAI's format."""

        converted: List[Dict[str, Any]] = []
        if not isinstance(tools, list):
            return converted

        for tool in tools:
            if not isinstance(tool, dict):
                continue

            if tool.get("type") == "function" and "function" in tool:
                converted.append(tool)
                continue

            name = tool.get("name")
            if not isinstance(name, str) or not name:
                continue

            function_def = {
                "name": name,
                "description": tool.get("description", ""),
                "parameters": tool.get("parameters", {}),
            }
            converted.append({"type": "function", "function": function_def})

        return converted

    def _extract_response_text(self, response: XAIResponse) -> str:
        """Return assistant text from the response wrapper."""

        return response.text

    def _check_response_completion(self, response: XAIResponse) -> bool:
        """Determine whether the xAI response concluded without tool calls."""

        return not bool(response.tool_calls)

    def extract_function_calls(self, response: XAIResponse) -> List[Dict[str, Any]]:
        """Extract structured function calls from the assistant message."""

        function_calls: List[Dict[str, Any]] = []

        for tool_call in response.tool_calls:
            try:
                if isinstance(tool_call, dict):
                    func = tool_call.get("function", {})
                    name = func.get("name") if isinstance(func, dict) else None
                    arguments = func.get("arguments") if isinstance(func, dict) else None
                    call_id = tool_call.get("id")
                else:
                    func_obj = getattr(tool_call, "function", None)
                    name = getattr(func_obj, "name", None)
                    arguments = getattr(func_obj, "arguments", None)
                    call_id = getattr(tool_call, "id", None)
                if name:
                    function_calls.append(
                        {
                            "name": name,
                            "arguments": arguments or "{}",
                            "call_id": call_id or "",
                        }
                    )
            except Exception as exc:
                self.logger.debug(f"[XAI.FUNCTION_CALL] Skipping malformed tool call: {exc}")

        return function_calls

    def _extract_error_text(self, response: requests.Response) -> str:
        """Extract a human-readable error message from a failed HTTP response."""

        try:
            data = response.json()
            if isinstance(data, dict):
                if "error" in data:
                    err = data["error"]
                    if isinstance(err, dict):
                        return err.get("message") or json.dumps(err)
                    return str(err)
                return json.dumps(data)
        except Exception:
            pass

        return response.text or "Unknown error"

    def _extract_response_format(self, kwargs: Dict[str, Any]) -> Optional[Dict[str, Any]]:
        """Translate Theo's structured output request to xAI format."""

        format_payload: Optional[Dict[str, Any]] = None

        text_payload = kwargs.get("text") if isinstance(kwargs.get("text"), dict) else None
        text_format = kwargs.get("text_format") if isinstance(kwargs.get("text_format"), dict) else None

        if text_payload and not text_format:
            text_format = text_payload.get("format")  # type: ignore[assignment]

        if not isinstance(text_format, dict):
            return None

        format_type = str(text_format.get("type", "")).lower()

        if format_type == "json_schema":
            schema = text_format.get("schema")
            if schema:
                format_payload = {
                    "type": "json_schema",
                    "json_schema": {
                        "name": text_format.get("name", "TheoSchema"),
                        "schema": schema,
                    },
                }
        elif format_type == "json_object":
            format_payload = {"type": "json_object"}

        return format_payload

    def _determine_reasoning_effort(
        self, model: str, kwargs: Dict[str, Any]
    ) -> Optional[str]:
        """Return reasoning effort when supported by the model."""

        reasoning = kwargs.get("reasoning")
        if not isinstance(reasoning, dict):
            return None

        effort = reasoning.get("effort")
        if not isinstance(effort, str):
            return None

        effort_lc = effort.lower()
        if effort_lc not in {"low", "high"}:
            return None

        if model.startswith("grok-4"):
            self.logger.debug(
                "[XAI.REASONING] Ignoring reasoning_effort for grok-4 models (unsupported)"
            )
            return None

        return effort_lc

    @staticmethod
    def _normalise_model_name(model: str) -> str:
        """Fallback to default Grok model when an incompatible name is supplied."""

        try:
            ml = model.lower()
        except Exception:
            ml = ""

        if ml.startswith("grok-") or ml.startswith("xai"):
            return model

        return "grok-4"

    @staticmethod
    def _is_reasoning_model(model: str) -> bool:
        """Return True when the model requires extended timeout.
        
        Grok-4 models (including grok-4-fast) are reasoning models per xAI docs
        and need extended timeouts for complex reasoning tasks.
        """

        ml = model.lower()
        return ml.startswith("grok-4") or ml.startswith("grok-3")


# Singleton instance used by the selector
_xai_model = XAIModelCall()


async def call_model(
    prompt: Any,
    config: Optional[Dict[str, Any]] = None,
    model: Optional[str] = None,
    stream: bool = False,
    **kwargs: Any,
) -> XAIResponse:
    """Convenience wrapper used by the model selector."""

    return await _xai_model.call_model(prompt, config=config, model=model, stream=stream, **kwargs)


def get_response_text(response: XAIResponse) -> str:
    """Return the assistant text from a response wrapper."""

    return _xai_model.get_response_text(response)


def get_response_id(response: XAIResponse) -> Optional[str]:
    """Return the response identifier for conversation continuity."""

    return _xai_model.get_response_id(response)


def is_response_complete(response: XAIResponse) -> bool:
    """Return True when the assistant is ready to reply to the user."""

    return _xai_model.is_response_complete(response)


def extract_function_calls(response: XAIResponse) -> List[Dict[str, Any]]:
    """Expose structured tool calls for orchestration layers."""

    return _xai_model.extract_function_calls(response)
