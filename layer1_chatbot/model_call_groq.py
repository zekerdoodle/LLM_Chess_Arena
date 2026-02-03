"""
Groq Model Call Implementation

Supports Groq Chat Completions API with tool use and Structured Outputs mapping.
"""

from typing import Any, Dict, Optional

from groq import Groq

from .base_model import BaseModelCall, ModelResponseError


class GroqResponseError(ModelResponseError):
    """Custom exception for Groq API errors."""


class GroqModelCall(BaseModelCall):
    """Groq model call implementation."""

    def __init__(self):
        super().__init__("groq")

    def _get_api_key(self, config: Dict[str, Any]) -> Optional[str]:
        from utils.config_loader import get_config_value
        return get_config_value(config, "api_keys.groq")

    def _get_default_model(self) -> str:
        # Select a commonly available Groq model id; can be overridden via config
        return "meta-llama/llama-3.1-8b-instant"

    async def _make_api_call(
        self, prompt: str, api_key: str, model: str, stream: bool, **kwargs
    ) -> Any:
        # Normalize model: if provided model isn't a Groq-hosted model id, use default
        try:
            ml = str(model).lower() if isinstance(model, str) else ""
            groq_keywords = [
                "openai/gpt-oss",
                "gpt-oss-",
                "meta-llama/",
                "whisper-large-v3",
                "whisper-large-v3-turbo",
                "deepseek",
                "qwen/",
                "moonshotai/",
                "playai-tts",
                "compound-beta",
            ]
            if not (ml.startswith("groq:") or any(k in ml for k in groq_keywords)):
                dm = self._get_default_model()
                self.logger.debug(f"[GROQ.MODEL] Overriding incompatible model '{model}' -> '{dm}'")
                model = dm
        except Exception:
            pass

        client = Groq(api_key=api_key)

        # Prepare messages array (OpenAI-compatible format)
        if isinstance(prompt, list):
            messages = []
            for m in prompt:
                role = m.get("role") if isinstance(m, dict) else getattr(m, "role", None)
                content = m.get("content") if isinstance(m, dict) else getattr(m, "content", None)
                messages.append({"role": role or "user", "content": content})
        else:
            messages = [{"role": "user", "content": prompt}]

        # Tools mapping: OpenAI-compatible
        tools_param = None
        if "tools" in kwargs and kwargs["tools"]:
            tools_param = self._convert_tools_to_openai_format(kwargs["tools"])  # reuse OpenAI style
            if tools_param:
                self.logger.debug(f"[GROQ.FUNCTION_CALL] Using {len(tools_param)} tools")

        # Structured outputs mapping
        response_format = None
        text_payload = kwargs.get("text") or {}
        if isinstance(text_payload, dict):
            fmt = text_payload.get("format")
            if isinstance(fmt, dict):
                ftype = (fmt.get("type") or "").lower()
                if ftype == "json_schema":
                    schema = fmt.get("schema") or fmt.get("json_schema", {}).get("schema")
                    name = fmt.get("name") or (fmt.get("json_schema", {}).get("name") if isinstance(fmt.get("json_schema"), dict) else None) or "structured_output"
                    if schema:
                        response_format = {
                            "type": "json_schema",
                            "json_schema": {
                                "name": name,
                                "schema": schema,
                            },
                        }
                elif ftype == "json_object":
                    response_format = {"type": "json_object"}

        # Inject structured tool replay if provided
        try:
            tool_results = kwargs.get("tool_results")
            prev_assistant = kwargs.get("groq_prev_response") or kwargs.get("prev_assistant_response")
            # If we have a previous assistant response with tool_calls, append it as an assistant message
            if prev_assistant is not None:
                try:
                    # Extract tool_calls array from prior response (Groq SDK shape)
                    choice = (prev_assistant.choices or [None])[0]
                    if choice:
                        prev_msg = getattr(choice, "message", None) or {}
                        prev_tool_calls = getattr(prev_msg, "tool_calls", None)
                        if prev_tool_calls:
                            messages.append({
                                "role": "assistant",
                                "content": None,
                                "tool_calls": prev_tool_calls,
                            })
                except Exception:
                    pass
            # Append each tool_result as a tool-role message (OpenAI-compatible)
            if isinstance(tool_results, list) and tool_results:
                for tr in tool_results:
                    tcid = tr.get("tool_call_id")
                    output = tr.get("output", "")
                    if not tcid:
                        continue
                    messages.append({
                        "role": "tool",
                        "tool_call_id": tcid,
                        "content": output,
                    })
        except Exception:
            pass

        # Execute non-streaming chat completion
        try:
            params: Dict[str, Any] = {
                "model": model,
                "messages": messages,
                # Groq supports max_completion_tokens; leave default unless provided
            }
            # Map reasoning to Groq reasoning_effort for supported families.
            # IMPORTANT: Per product requirement, Groq models MUST always use maximum
            # reasoning amount regardless of the dynamic optimizer suggestion.
            # Reference: docs/groq/reasoning.md
            try:
                model_lc = str(model).lower()
            except Exception:
                model_lc = ""
            is_gpt_oss = ("openai/gpt-oss" in model_lc) or model_lc.startswith("gpt-oss-")
            is_qwen32b = model_lc.startswith("qwen/qwen3-32b") or \
                         ("qwen3-32b" in model_lc)
            # Always force maximum supported effort for Groq families
            # GPT-OSS (20B/120B): 'high' is the maximum
            # Qwen 3 32B: supported values are 'none'|'default' – choose 'default' as maximum
            eff = "high" if is_gpt_oss else ("default" if is_qwen32b else None)
            # Apply mapped effort when valid for the model family
            if is_gpt_oss and eff in ("low", "medium", "high"):
                params["reasoning_effort"] = eff
            elif is_qwen32b and eff in ("none", "default"):
                params["reasoning_effort"] = eff
            # If structured outputs requested, drop tools per Groq docs (not supported together)
            if tools_param and not response_format:
                params["tools"] = tools_param
                # Make tool selection policy explicit (OpenAI-compatible)
                params["tool_choice"] = "auto"
                # Reasoning output constraints with tool use:
                # - GPT-OSS: disable include_reasoning to avoid narration
                # - Non-GPT-OSS (e.g., Qwen/DeepSeek): set reasoning_format to 'parsed'
                if is_gpt_oss:
                    try:
                        params["include_reasoning"] = False
                    except Exception:
                        pass
                else:
                    # Ensure compatibility: reasoning_format cannot be 'raw' with tools
                    params["reasoning_format"] = "parsed"
            if response_format:
                params["response_format"] = response_format

            resp = client.chat.completions.create(**params)
            return resp
        except Exception as e:
            # If tool validation failed, retry without tools for graceful degradation
            msg = str(e)
            if "tool call validation failed" in msg or "tool_use_failed" in msg:
                try:
                    # First, attempt a permissive retry WITH tools by allowing
                    # additionalProperties on all tool schemas (best-effort matching)
                    permissive_tools = []
                    try:
                        for t in (tools_param or []):
                            tf = dict(t.get("function", {}))
                            params_obj = dict(tf.get("parameters", {})) if isinstance(tf.get("parameters", {}), dict) else {}
                            if params_obj.get("type") == "object":
                                # Allow extra keys to prevent strict validation failures
                                params_obj["additionalProperties"] = True
                                tf["parameters"] = params_obj
                            permissive_tools.append({"type": "function", "function": tf})
                    except Exception:
                        permissive_tools = tools_param or []

                    params_retry_tools: Dict[str, Any] = {
                        "model": model,
                        "messages": messages,
                        "tools": permissive_tools,
                        "tool_choice": "auto",
                    }
                    if is_gpt_oss:
                        params_retry_tools["include_reasoning"] = False

                    try:
                        resp = client.chat.completions.create(**params_retry_tools)
                        return resp
                    except Exception:
                        # Fall back to retry without tools as last resort
                        params_retry: Dict[str, Any] = {
                            "model": model,
                            "messages": messages,
                        }
                        if response_format:
                            params_retry["response_format"] = response_format
                        resp = client.chat.completions.create(**params_retry)
                        return resp
                except Exception:
                    pass
            # Fallback on model not found: try a small list of widely available Groq models
            if "model_not_found" in msg or "does not exist" in msg:
                fallback_models = [
                    "llama-3.3-70b-versatile",
                    "llama-3.1-70b-versatile",
                    "llama-3.1-8b-instant",
                    "mixtral-8x7b-32768",
                    "gemma2-9b-it",
                ]
                for cand in fallback_models:
                    try:
                        if cand == model:
                            continue
                        self.logger.warning(f"[GROQ.MODEL] Retrying with fallback model '{cand}' due to: {msg[:100]}")
                        params_fb = dict(params)
                        params_fb["model"] = cand
                        resp = client.chat.completions.create(**params_fb)
                        return resp
                    except Exception as fe:
                        # Continue trying next
                        continue
            raise GroqResponseError(str(e)) from e

    def _convert_tools_to_openai_format(self, tools: list) -> list:
        openai_tools = []
        for tool in tools:
            if isinstance(tool, dict):
                parameters = tool.get("parameters", {}).copy()
                # Be permissive for zero-arg tools to avoid model quirks emitting stray keys
                try:
                    if (
                        isinstance(parameters, dict)
                        and parameters.get("type") == "object"
                        and isinstance(parameters.get("properties"), dict)
                        and len(parameters.get("properties")) == 0
                    ):
                        parameters["additionalProperties"] = True
                except Exception:
                    pass
                name = tool.get("name")
                base_tool = {
                    "type": "function",
                    "function": {
                        "name": name,
                        "description": tool.get("description", ""),
                        "parameters": parameters,
                    },
                }
                openai_tools.append(base_tool)
                # Add Groq-specific alias to tolerate models that prefix names with "functions/"
                if name and not str(name).startswith("functions/"):
                    alias_tool = {
                        "type": "function",
                        "function": {
                            "name": f"functions/{name}",
                            "description": tool.get("description", ""),
                            "parameters": parameters,
                        },
                    }
                    openai_tools.append(alias_tool)
        return openai_tools

    def _extract_response_text(self, response: Any) -> str:
        try:
            choice = (response.choices or [None])[0]
            if not choice:
                return ""
            msg = getattr(choice, "message", None) or {}
            content = getattr(msg, "content", None)
            if isinstance(content, str):
                return content
            return str(content or "")
        except Exception:
            return ""

    def _check_response_completion(self, response: Any) -> bool:
        try:
            choice = (response.choices or [None])[0]
            if not choice:
                return True
            msg = getattr(choice, "message", None) or {}
            tcs = getattr(msg, "tool_calls", None)
            return not bool(tcs)
        except Exception:
            return True

    def extract_function_calls(self, response: Any) -> list:
        function_calls = []
        try:
            choice = (response.choices or [None])[0]
            if not choice:
                return []
            msg = getattr(choice, "message", None) or {}
            tcs = getattr(msg, "tool_calls", None) or []
            for tc in tcs:
                f = getattr(tc, "function", None) or {}
                tcid = getattr(tc, "id", None) or getattr(tc, "tool_call_id", None)
                name = getattr(f, "name", "") or f.get("name", "")
                # Normalize Groq names like "functions/add_working_memory" -> "add_working_memory"
                if isinstance(name, str) and name.startswith("functions/"):
                    name = name.split("/", 1)[1]
                function_calls.append({
                    "name": name,
                    "arguments": getattr(f, "arguments", "{}") or f.get("arguments", "{}"),
                    "call_id": tcid,
                })
        except Exception:
            return []
        return function_calls


# Singleton instance helpers
_groq_model = GroqModelCall()


async def call_model(
    prompt: str,
    config: Optional[Dict[str, Any]] = None,
    model: Optional[str] = None,
    stream: bool = False,
    **kwargs,
) -> Any:
    return await _groq_model.call_model(prompt, config, model, stream, **kwargs)


def get_response_text(response: Any) -> str:
    return _groq_model.get_response_text(response)


def get_response_id(response: Any) -> Optional[str]:
    return _groq_model.get_response_id(response)


def is_response_complete(response: Any) -> bool:
    return _groq_model.is_response_complete(response)


def extract_function_calls(response: Any) -> list:
    return _groq_model.extract_function_calls(response)
