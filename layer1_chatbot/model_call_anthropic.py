"""
Anthropic Model Call Implementation

This module provides functionality to call Anthropic's Claude models
for conversational AI interactions.
"""

from typing import Any, Dict, Optional

import anthropic
from anthropic.types import Message

from .base_model import BaseModelCall, ModelResponseError


class AnthropicResponseError(ModelResponseError):
    """Custom exception for Anthropic API errors."""


class AnthropicModelCall(BaseModelCall):
    """Anthropic model call implementation."""

    def __init__(self):
        """Initialize Anthropic model call."""
        super().__init__("anthropic")

    def _get_api_key(self, config: Dict[str, Any]) -> Optional[str]:
        """Get Anthropic API key from config."""
        from utils.config_loader import get_config_value

        return get_config_value(config, "api_keys.anthropic")

    def _get_default_model(self) -> str:
        """Get default Anthropic model."""
        return "claude-3-5-sonnet-20241022"

    async def _make_api_call(
        self, prompt: str, api_key: str, model: str, stream: bool, **kwargs
    ) -> Message:
        """Make Anthropic API call."""
        # Normalize model: if provided model isn't an Anthropic/Claude model, use default
        try:
            ml = str(model).lower() if isinstance(model, str) else ""
            if not (ml.startswith("claude") or ml.startswith("anthropic")):
                dm = self._get_default_model()
                self.logger.debug(f"[ANTHROPIC.MODEL] Overriding incompatible model '{model}' -> '{dm}'")
                model = dm
        except Exception:
            pass

        client = anthropic.Anthropic(api_key=api_key)

        # Handle conversation format; Anthropic only supports 'user' and 'assistant'
        if isinstance(prompt, list):
            messages = []
            for m in prompt:
                role = m.get("role") if isinstance(m, dict) else getattr(m, "role", None)
                content = m.get("content") if isinstance(m, dict) else getattr(m, "content", None)
                if role == "developer":
                    role = "user"
                messages.append({"role": role or "user", "content": content})
        else:
            messages = [{"role": "user", "content": prompt}]
        
        # Ensure messages content is in Anthropic's expected shape (list of content blocks)
        normalized = []
        for m in messages:
            role = m.get("role", "user")
            content = m.get("content")
            if isinstance(content, str):
                content = [{"type": "text", "text": content}]
            elif isinstance(content, list):
                # Assume already in content-block style
                pass
            else:
                content = [{"type": "text", "text": str(content)}]
            normalized.append({"role": role, "content": content})

        # Optional system prompt/blocks
        system_param = None
        if "system" in kwargs and kwargs["system"]:
            sysval = kwargs["system"]
            if isinstance(sysval, str):
                system_param = [{"type": "text", "text": sysval}]
            elif isinstance(sysval, list):
                system_param = sysval
        # Determine max_tokens (allow override)
        max_tokens = int(kwargs.get("max_tokens", 4096) or 4096)
        request_params = {
            "model": model,
            "max_tokens": max_tokens,
            "messages": normalized,
        }
        if system_param is not None:
            request_params["system"] = system_param

        # Add tools if provided
        if "tools" in kwargs and kwargs["tools"]:
            anthropic_tools = self._convert_tools_to_anthropic_format(
                kwargs["tools"]
            )
            request_params["tools"] = anthropic_tools
            # Optional tool_choice control (auto/any/tool/none or explicit dict)
            tc = kwargs.get("tool_choice")
            if tc is not None:
                request_params["tool_choice"] = tc
            # Optional disabling parallel tool use
            if "disable_parallel_tool_use" in kwargs:
                request_params["disable_parallel_tool_use"] = bool(kwargs.get("disable_parallel_tool_use"))
            self.logger.debug(
                f"[ANTHROPIC.FUNCTION_CALL] Using {len(anthropic_tools)} tools"
            )

        # Extended thinking mapping (Anthropic)
        try:
            model_lc = str(model).lower() if isinstance(model, str) else ""
        except Exception:
            model_lc = ""
        supports_thinking = any(model_lc.startswith(pfx) for pfx in ("claude-3-7-sonnet", "claude-sonnet-4", "claude-opus-4"))
        thinking_cfg = kwargs.get("thinking")
        if supports_thinking:
            if thinking_cfg and isinstance(thinking_cfg, dict):
                if thinking_cfg.get("type") == "enabled":
                    # Respect caller-provided thinking config as-is
                    request_params["thinking"] = thinking_cfg
            else:
                # Derive from reasoning.effort when provided
                effort = None
                if isinstance(kwargs.get("reasoning"), dict):
                    effort = (kwargs["reasoning"].get("effort") or "").lower()
                if effort in ("medium", "high"):
                    # Do not explicitly cap thinking tokens; enable extended thinking without a budget
                    request_params["thinking"] = {"type": "enabled"}
        # tool_choice constraints with thinking: only auto/none supported
        if request_params.get("thinking") and isinstance(request_params.get("tool_choice"), dict):
            tc_type = request_params["tool_choice"].get("type")
            if tc_type in ("any", "tool"):
                request_params.pop("tool_choice", None)
        elif request_params.get("thinking") and request_params.get("tool_choice") in ("any",):
            request_params.pop("tool_choice", None)
        # Do not adjust max_tokens for thinking; avoid implicit limits
        # Inject structured tool replay if provided
        try:
            anth_tool_results = kwargs.get("anthropic_tool_results") or kwargs.get("tool_results")
            prev_content = kwargs.get("anthropic_prev_content")
            follow_text = kwargs.get("anthropic_followup_text")
            if anth_tool_results:
                # Append the assistant's prior tool_use content then a single user message with all tool_results
                if prev_content:
                    try:
                        # Ensure prev_content is a list of content blocks
                        if isinstance(prev_content, list):
                            normalized.append({"role": "assistant", "content": prev_content})
                        else:
                            # Best-effort coercion
                            normalized.append({"role": "assistant", "content": [{"type": "text", "text": str(prev_content)}]})
                    except Exception:
                        pass
                # Build tool_result content array
                tr_blocks = []
                for tr in anth_tool_results:
                    tool_use_id = tr.get("tool_call_id") or tr.get("tool_use_id")
                    output = tr.get("output", "")
                    if not tool_use_id:
                        continue
                    # Anthropic expects a single content string or array; keep simple
                    tr_blocks.append({
                        "type": "tool_result",
                        "tool_use_id": tool_use_id,
                        "content": output,
                    })
                if tr_blocks:
                    normalized.append({"role": "user", "content": tr_blocks})
                if follow_text:
                    try:
                        normalized.append({"role": "user", "content": [{"type": "text", "text": str(follow_text)}]})
                    except Exception:
                        pass
        except Exception:
            pass

        # Token-efficient tools (beta) optional for Claude 3.7 Sonnet
        use_token_efficient = bool(kwargs.get("enable_token_efficient_tools")) and model_lc.startswith("claude-3-7-sonnet")
        # Streaming removed: always use non-streaming messages API
        if use_token_efficient:
            response = client.beta.messages.create(**request_params, betas=["token-efficient-tools-2025-02-19"])
        else:
            response = client.messages.create(**request_params)

        return response

    def _convert_tools_to_anthropic_format(self, tools: list) -> list:
        """Convert tools to Anthropic format."""
        anthropic_tools = []

        for tool in tools:
            if isinstance(tool, dict):
                # Convert from standard format to Anthropic format
                anthropic_tool = {
                    "name": tool.get("name"),
                    "description": tool.get("description", ""),
                    "input_schema": tool.get("parameters", {}),
                }
                anthropic_tools.append(anthropic_tool)

        return anthropic_tools

    def _extract_response_text(self, response: Message) -> str:
        """Extract text from Anthropic response."""
        text_content = ""

        if hasattr(response, "content") and response.content:
            for content in response.content:
                if hasattr(content, "type"):
                    if content.type == "tool_use":
                        # Format tool use for display
                        tool_name = getattr(content, "name", "unknown_tool")
                        tool_input = getattr(content, "input", {})
                        text_content += f"{tool_name}({tool_input})"
                    elif hasattr(content, "text") and content.text:
                        text_content += content.text

        return text_content

    def _check_response_completion(self, response: Message) -> bool:
        """Check if Anthropic response is complete."""
        if hasattr(response, "content") and response.content:
            for content in response.content:
                if hasattr(content, "type") and content.type == "tool_use":
                    return False
        return True

    def extract_function_calls(self, response: Message) -> list:
        """Extract function calls from Anthropic response."""
        function_calls = []

        if hasattr(response, "content") and response.content:
            for content in response.content:
                if hasattr(content, "type") and content.type == "tool_use":
                    function_call = {
                        "id": getattr(content, "id", ""),
                        "name": getattr(content, "name", ""),
                        "input": getattr(content, "input", {}),
                    }
                    function_calls.append(function_call)
                    self.logger.debug(
                        f"[ANTHROPIC.FUNCTION_CALL] Found function call: {function_call['name']}"
                    )

        return function_calls


# Create singleton instance
_anthropic_model = AnthropicModelCall()


async def call_model(
    prompt: str,
    config: Optional[Dict[str, Any]] = None,
    model: Optional[str] = None,
    stream: bool = False,
    **kwargs,
) -> Message:
    """Call an Anthropic model (module-level helper used by ModelSelector)."""
    return await _anthropic_model.call_model(prompt, config, model, stream, **kwargs)


def get_response_text(response: Message) -> str:
    """Extract text from an Anthropic response (used by ModelSelector)."""
    return _anthropic_model.get_response_text(response)


def get_response_id(response: Message) -> Optional[str]:
    """Get Anthropic response ID (used by ModelSelector)."""
    return _anthropic_model.get_response_id(response)


def is_response_complete(response: Message) -> bool:
    """Return True if the Anthropic response is complete (used by ModelSelector)."""
    return _anthropic_model.is_response_complete(response)


def extract_function_calls(response: Message) -> list:
    """Extract structured function calls from an Anthropic response (used by ModelSelector)."""
    return _anthropic_model.extract_function_calls(response)
