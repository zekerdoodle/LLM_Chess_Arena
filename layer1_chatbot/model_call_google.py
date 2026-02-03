"""
Google Model Call Implementation

This module provides functionality to call Google's Gemini models with proper
prompt handling, system instruction support, and optimized reasoning capability.
"""

from typing import Any, Dict, Optional, List
from types import SimpleNamespace
import re
import asyncio
import threading
import os
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

try:  # pragma: no cover - google-genai optional in minimal environments
    from google import genai  # type: ignore
    from google.genai import types  # type: ignore
    from google.genai.types import GenerateContentResponse  # type: ignore
except Exception:  # pragma: no cover
    class _MissingGoogleClient:
        def __init__(self, *args, **kwargs):
            raise RuntimeError(
                "google-genai SDK is not installed. Install the 'google-genai' package to enable Google model support."
            )

    def _build_stub(**kwargs):
        return SimpleNamespace(**kwargs)

    genai = SimpleNamespace(Client=_MissingGoogleClient)
    types = SimpleNamespace(
        Tool=lambda **kwargs: _build_stub(**kwargs),
        ToolConfig=lambda **kwargs: _build_stub(**kwargs),
        FunctionCallingConfig=lambda **kwargs: _build_stub(**kwargs),
        GenerateContentConfig=lambda **kwargs: _build_stub(**kwargs),
        ThinkingConfig=lambda **kwargs: _build_stub(**kwargs),
        SafetySetting=lambda **kwargs: _build_stub(**kwargs),
        HarmCategory=SimpleNamespace(
            HARASSMENT="HARASSMENT",
            HATE_SPEECH="HATE_SPEECH",
            SEXUALLY_EXPLICIT="SEXUALLY_EXPLICIT",
            DANGEROUS_CONTENT="DANGEROUS_CONTENT",
            CIVIC_INTEGRITY="CIVIC_INTEGRITY",
        ),
        HarmBlockThreshold=SimpleNamespace(BLOCK_NONE="BLOCK_NONE"),
        Content=lambda **kwargs: _build_stub(**kwargs),
        Part=lambda **kwargs: _build_stub(**kwargs),
        FunctionResponse=lambda **kwargs: _build_stub(**kwargs),
        GoogleSearch=lambda **kwargs: _build_stub(**kwargs),
        FileData=lambda **kwargs: _build_stub(**kwargs),
        Blob=lambda **kwargs: _build_stub(**kwargs),
        FunctionCall=lambda **kwargs: _build_stub(**kwargs),
    )

    class GenerateContentResponse(SimpleNamespace):  # type: ignore
        pass

from .base_model import BaseModelCall, ModelResponseError


class GoogleResponseError(ModelResponseError):
    """Custom exception for Google API errors."""


class GoogleModelCall(BaseModelCall):
    """Google model call implementation."""

    def __init__(self):
        """Initialize Google model call."""
        super().__init__("google")

    def _get_api_key(self, config: Dict[str, Any]) -> Optional[str]:
        """Get Google API key from config."""
        from utils.config_loader import get_config_value

        return get_config_value(config, "api_keys.google")

    def _get_default_model(self) -> str:
        """Get default Google model."""
        # Standardize on latest stable fast model per Google docs
        return "gemini-3-pro-preview"

    async def _make_api_call(
        self, prompt: str, api_key: str, model: str, stream: bool, **kwargs
    ):
        """Make Google API call.
        
        Returns:
            - If stream=False: GenerateContentResponse object
            - If stream=True: AsyncGenerator yielding event dictionaries
        """
        # Normalize model: if the provided model does not look like a Google Gemini model,
        # fall back to this provider's default to avoid 404 NOT_FOUND errors when callers
        # pass a cross-provider primary_model (e.g., 'gpt-5').
        try:
            ml = str(model).lower() if isinstance(model, str) else ""
            if not (ml.startswith("gemini-") or ml.startswith("google")):
                dm = self._get_default_model()
                self.logger.debug(f"[GOOGLE.MODEL] Overriding incompatible model '{model}' -> '{dm}'")
                model = dm
        except Exception:
            pass

        client = genai.Client(api_key=api_key)

        # Lazy import to avoid circulars for config
        try:
            from utils.config_loader import get_config_value, load_config  # type: ignore
            cfg = kwargs.get("_config") or load_config()
        except Exception:
            cfg = None

        # Handle conversation format
        if isinstance(prompt, list):
            prompt_text = "\n".join([
                f"{msg.get('role', 'user')}: {msg.get('content', '')}"
                for msg in prompt
            ])
        else:
            prompt_text = prompt

        # Parse prompt using structured extractor (aligns with OpenAI implementation)
        sections = extract_prompt_sections(prompt_text)
        
        # 1. System Instructions (Developer)
        sys_parts = []
        if SYSTEM_INSTRUCTIONS_HEADER in sections:
            sys_parts.append(sections[SYSTEM_INSTRUCTIONS_HEADER])
        if AVAILABLE_TOOLS_HEADER in sections:
            # Include tool guidance in system instruction for context
            sys_parts.append(f"{AVAILABLE_TOOLS_HEADER}\n{sections[AVAILABLE_TOOLS_HEADER]}")
        
        system_instruction = "\n\n".join(sys_parts).strip()

        # Prefer explicit system_instructions from config if available (override)
        try:
            if cfg is not None:
                cfg_sys = get_config_value(cfg, "system_instructions")  # type: ignore[arg-type]
                if isinstance(cfg_sys, str) and cfg_sys.strip():
                    system_instruction = cfg_sys.strip()
        except Exception:
            pass

        # Generation configuration
        # For Gemini 3, temperature 1.0 is strongly recommended per Google documentation
        # Setting it below 1.0 may cause looping or degraded performance on complex tasks
        is_gemini_3 = "gemini-3" in str(model).lower()
        generation_config: Dict[str, Any] = {
            "temperature": 1.0 if is_gemini_3 else 0.7,
            "top_p": 0.8,
            "top_k": 40,
        }

        sys_instruction_arg: Optional[str] = system_instruction.strip() if isinstance(system_instruction, str) else None

        # Prepare tools list
        tools_objs = []
        tool_config = None

        # 1. Function Tools
        if "tools" in kwargs and kwargs["tools"]:
            google_tools = self._convert_tools_to_google_format(kwargs["tools"])
            if google_tools:
                tools_objs.append(types.Tool(function_declarations=google_tools))
                self.logger.debug(f"[GOOGLE.FUNCTION_CALL] Added {len(google_tools)} function tools")
                
                # Optional function calling mode
                fc_mode = str(kwargs.get("function_calling_mode", "AUTO")).upper()
                try:
                    fn_names = [t.get("name") for t in google_tools if t.get("name")]
                    allowed = kwargs.get("allowed_function_names")
                    if isinstance(allowed, list) and allowed:
                        allowed_names = allowed
                    else:
                        allowed_names = fn_names

                    fcc_kwargs = {"mode": fc_mode}
                    if fc_mode == "ANY" and allowed_names:
                        fcc_kwargs["allowed_function_names"] = allowed_names

                    tool_config = types.ToolConfig(
                        function_calling_config=types.FunctionCallingConfig(**fcc_kwargs)
                    )
                except Exception:
                    tool_config = None

        # 2. Native Google Search (Grounding)
        # Only enable if NO custom function tools are present (API limitation: cannot mix Grounding + Function Calling)
        # This ensures the model doesn't crash when tools are provided.
        if not (kwargs.get("tools") and kwargs["tools"]):
            try:
                search_tool = types.Tool(google_search=types.GoogleSearch())
                tools_objs.append(search_tool)
                self.logger.debug("[GOOGLE.SEARCH] Enabled native Grounding with Google")
            except Exception as e:
                self.logger.warning(f"[GOOGLE.SEARCH] Failed to enable native search: {e}")

        config_kwargs = dict(generation_config)
        if tool_config is not None:
            config_kwargs["tool_config"] = tool_config
        
        # Create base config
        # CRITICAL: Disable AFC (Automatic Function Calling) to prevent the SDK from making
        # additional API calls after our streaming completes. We handle function calls manually
        # in the orchestrator. AFC causes "doubled response" issues where the SDK makes extra
        # calls and returns different text than what we streamed.
        config = types.GenerateContentConfig(
            tools=tools_objs if tools_objs else None,
            automatic_function_calling=types.AutomaticFunctionCallingConfig(disable=True),
            **config_kwargs,
        )

        # Map generic structured output request (text.format)
        try:
            text_payload = kwargs.get("text") or {}
            if isinstance(text_payload, dict):
                fmt = text_payload.get("format")
                if isinstance(fmt, dict):
                    ftype = (fmt.get("type") or "").lower()
                    if ftype == "json_schema":
                        schema = fmt.get("schema")
                        if schema:
                            config.response_mime_type = "application/json"
                            config.response_schema = schema
                    elif ftype == "json_object":
                        config.response_mime_type = "application/json"
        except Exception:
            pass

        # Apply system instruction and thinking configuration
        try:
            if sys_instruction_arg:
                config.system_instruction = sys_instruction_arg
            
            thinking_cfg = kwargs.get("thinking_config")
            is_gemini_3 = "gemini-3" in model.lower()
            
            if isinstance(thinking_cfg, dict):
                # User provided thinking config
                if is_gemini_3:
                    # Gemini 3 uses thinking_level - Force High for now per user request
                    level = "high"
                    
                    try:
                        config.thinking_config = types.ThinkingConfig(
                            thinking_level=level,
                            include_thoughts=True # Always include thoughts for summaries
                        )
                        self.logger.debug(f"[GOOGLE.THINKING] Applied thinking_level={level}, include_thoughts=True")
                    except Exception as e:
                        self.logger.error(f"[GOOGLE.THINKING] Failed to apply thinking_level: {e}")
                else:
                    # Legacy models use thinking_budget
                    tb = int(thinking_cfg.get("thinking_budget")) if thinking_cfg.get("thinking_budget") is not None else None
                    inc = bool(thinking_cfg.get("include_thoughts", False))
                    if tb is not None:
                        try:
                            config.thinking_config = types.ThinkingConfig(
                                thinking_budget=tb,
                                include_thoughts=inc,
                            )
                            self.logger.debug(f"[GOOGLE.THINKING] Applied thinking_budget={tb}, include_thoughts={inc}")
                        except Exception as e:
                            self.logger.error(f"[GOOGLE.THINKING] Failed to apply thinking_budget: {e}")
            else:
                # Default: Enable dynamic thinking
                try:
                    if is_gemini_3:
                        config.thinking_config = types.ThinkingConfig(
                            thinking_level="high",
                            include_thoughts=True
                        )
                        self.logger.debug("[GOOGLE.THINKING] Applied default thinking_level='high', include_thoughts=True")
                    else:
                        config.thinking_config = types.ThinkingConfig(
                            thinking_budget=-1,
                            include_thoughts=True,
                        )
                        self.logger.debug("[GOOGLE.THINKING] Applied default dynamic thinking (-1)")
                except Exception as e:
                    self.logger.error(f"[GOOGLE.THINKING] Failed to apply default thinking config: {e}")

            # Apply safety settings (BLOCK_NONE)
            try:
                categories = [
                    types.HarmCategory.HARASSMENT,
                    types.HarmCategory.HATE_SPEECH,
                    types.HarmCategory.SEXUALLY_EXPLICIT,
                    types.HarmCategory.DANGEROUS_CONTENT,
                    types.HarmCategory.CIVIC_INTEGRITY,
                ]
                minimal = [
                    types.SafetySetting(
                        category=cat,
                        threshold=types.HarmBlockThreshold.BLOCK_NONE,
                    )
                    for cat in categories
                ]
                config.safety_settings = minimal
            except Exception:
                pass

        except Exception as e:
            self.logger.debug(f"[GOOGLE.CONFIG] Error applying config: {e}")

        # Build contents from structured sections
        contents_obj: List[types.Content] = []
        
        # 2. Context Message (Memory + History) -> User role
        context_parts = []
        for header in (WORKING_MEMORY_HEADER, MEMORY_BANK_HEADER, CHAT_HISTORY_HEADER):
            if header in sections:
                block = sections[header]
                if block:
                    context_parts.append(f"{header}\n{block}".strip())
        
        if context_parts:
            context_text = "\n\n".join(context_parts).strip()
            contents_obj.append(types.Content(role="user", parts=[types.Part(text=context_text)]))

        # Prepare attachment parts (audio/video/files) for the current message
        attachment_parts: List[types.Part] = []
        inline_attachments = kwargs.get("inline_data_attachments") or []
        for att in inline_attachments:
            try:
                file_path = att.get("path")
                mime_type = att.get("mime_type", "application/octet-stream")
                use_files_api = False
                
                if file_path and os.path.exists(file_path):
                    try:
                        if mime_type.startswith("audio/") or mime_type.startswith("video/"):
                            use_files_api = True
                        elif os.path.getsize(file_path) > 1 * 1024 * 1024:
                            use_files_api = True
                    except Exception:
                        pass

                if use_files_api and file_path:
                    try:
                        self.logger.info(f"[GOOGLE.FILES_API] Uploading file: {file_path} ({mime_type})")
                        
                        try:
                            upload_config = {"mime_type": mime_type}
                            uploaded_file = client.files.upload(file=file_path, config=upload_config)
                        except TypeError:
                            uploaded_file = client.files.upload(file=file_path)
                        
                        self.logger.info(f"[GOOGLE.FILES_API] Upload complete: {uploaded_file.uri}")

                        try:
                            import time
                            for _ in range(60):
                                if hasattr(uploaded_file, "state"):
                                    state_val = str(uploaded_file.state).upper()
                                    if "ACTIVE" in state_val:
                                        break
                                    if "FAILED" in state_val:
                                        raise GoogleResponseError(f"File processing failed: {state_val}")
                                
                                time.sleep(1)
                                try:
                                    uploaded_file = client.files.get(name=uploaded_file.name)
                                except Exception:
                                    pass
                            else:
                                self.logger.warning("[GOOGLE.FILES_API] File processing timed out (proceeding best-effort)")
                        except Exception as wait_err:
                            self.logger.warning(f"[GOOGLE.FILES_API] Error waiting for file state: {wait_err}")

                        attachment_parts.append(
                            types.Part(
                                file_data=types.FileData(
                                    file_uri=uploaded_file.uri,
                                    mime_type=mime_type,
                                )
                            )
                        )
                        continue
                    except Exception as upload_err:
                        self.logger.error(f"[GOOGLE.FILES_API] Upload failed, falling back to inline: {upload_err}")

                data = att.get("data")
                if isinstance(data, str):
                    import base64
                    data = base64.b64decode(data)
                
                self.logger.debug(f"[GOOGLE.ATTACHMENT] Using inline data for {att.get('mime_type')}")
                attachment_parts.append(
                    types.Part(
                        inline_data=types.Blob(
                            mime_type=att.get("mime_type", "application/octet-stream"),
                            data=data,
                        )
                    )
                )
            except Exception as e:
                self.logger.warning(f"[GOOGLE.ATTACHMENT] Failed to process attachment: {e}")

        # Determine current message text/parts
        current_message_text: Optional[str] = None
        if sections and isinstance(sections.get(CURRENT_MESSAGE_HEADER), str):
            cm_val = sections.get(CURRENT_MESSAGE_HEADER)
            if cm_val and cm_val.strip():
                current_message_text = cm_val.strip()
        elif not sections and prompt_text.strip():
            current_message_text = prompt_text.strip()

        current_message_parts: List[types.Part] = []
        if current_message_text:
            current_message_parts.append(types.Part(text=current_message_text))
        if attachment_parts:
            current_message_parts.extend(attachment_parts)

        added_current_message = False

        # Inject structured multi-turn history (Thought Signature preservation)
        # This replaces the simplistic 'google_prev_response' which only handled the immediate last turn
        google_history = kwargs.get("google_history")
        if google_history and isinstance(google_history, list):
            if current_message_parts:
                contents_obj.append(types.Content(role="user", parts=list(current_message_parts)))
                added_current_message = True
            for turn in google_history:
                try:
                    # Each turn is expected to be a tuple/object with:
                    # (model_response_object, tool_results_list, tool_call_names_list)
                    # OR a dict with keys
                    if isinstance(turn, dict):
                        model_resp = turn.get("model_response")
                        turn_results = turn.get("tool_results")
                        turn_call_names = turn.get("tool_call_names")
                    elif isinstance(turn, (list, tuple)) and len(turn) >= 3:
                        model_resp, turn_results, turn_call_names = turn[0], turn[1], turn[2]
                    else:
                        continue

                    # Reconstruct Model Turn (with signatures)
                    if hasattr(model_resp, "candidates") and model_resp.candidates:
                        cand = model_resp.candidates[0]
                        prev_content = getattr(cand, "content", None)
                        if prev_content and hasattr(prev_content, "parts") and prev_content.parts:
                            sdk_parts = []
                            for p in prev_content.parts:
                                part_kwargs = {}
                                if hasattr(p, "text") and p.text:
                                    part_kwargs["text"] = p.text
                                
                                # Handle function_call
                                if hasattr(p, "function_call"):
                                    fc = p.function_call
                                    fc_name = getattr(fc, "name", None)
                                    fc_args = getattr(fc, "args", None)
                                    if fc_name:
                                        try:
                                            part_kwargs["function_call"] = types.FunctionCall(name=fc_name, args=fc_args)
                                        except Exception:
                                            part_kwargs["function_call"] = {"name": fc_name, "args": fc_args}
                                
                                # Handle thought_signature (CRITICAL for Gemini 3)
                                # Check both snake_case (SDK) and camelCase (API/JSON)
                                ts = getattr(p, "thought_signature", None)
                                if not ts:
                                    ts = getattr(p, "thoughtSignature", None)
                                if ts:
                                    part_kwargs["thought_signature"] = ts
                                
                                if part_kwargs:
                                    sdk_parts.append(types.Part(**part_kwargs))
                            
                            if sdk_parts:
                                contents_obj.append(types.Content(role="model", parts=sdk_parts))

                    # Reconstruct User Turn (Tool Responses)
                    if isinstance(turn_results, list) and turn_results:
                        response_parts = []
                        for idx, tr in enumerate(turn_results):
                            name = None
                            if isinstance(turn_call_names, list) and idx < len(turn_call_names):
                                name = turn_call_names[idx]
                            if not name:
                                name = "tool_result"
                            output = tr.get("output", "")
                            try:
                                fr = types.FunctionResponse(name=name, response={"result": output})
                                response_parts.append(types.Part(function_response=fr))
                            except Exception:
                                response_parts.append(types.Part(text=str(output)))
                        
                        if response_parts:
                            contents_obj.append(types.Content(role="user", parts=response_parts))

                except Exception as e:
                    self.logger.warning(f"[GOOGLE.HISTORY] Error reconstructing history turn: {e}")

        # Fallback for single-step previous response (legacy support)
        elif kwargs.get("google_prev_response"):
            try:
                prev = kwargs.get("google_prev_response")
                if hasattr(prev, "candidates") and prev.candidates:
                    cand = prev.candidates[0]
                    prev_content = getattr(cand, "content", None)
                    if prev_content and hasattr(prev_content, "parts") and prev_content.parts:
                        sdk_parts = []
                        for p in prev_content.parts:
                            part_kwargs = {}
                            if hasattr(p, "text") and p.text:
                                part_kwargs["text"] = p.text
                            if hasattr(p, "function_call"):
                                fc = p.function_call
                                fc_name = getattr(fc, "name", None)
                                fc_args = getattr(fc, "args", None)
                                if fc_name:
                                    try:
                                        part_kwargs["function_call"] = types.FunctionCall(name=fc_name, args=fc_args)
                                    except Exception:
                                        part_kwargs["function_call"] = {"name": fc_name, "args": fc_args}
                            # Handle thought_signature (CRITICAL for Gemini 3)
                            # Check both snake_case (SDK) and camelCase (API/JSON)
                            ts = getattr(p, "thought_signature", None)
                            if not ts:
                                ts = getattr(p, "thoughtSignature", None)
                            if ts:
                                part_kwargs["thought_signature"] = ts
                            if part_kwargs:
                                sdk_parts.append(types.Part(**part_kwargs))
                        if sdk_parts:
                            contents_obj.append(types.Content(role="model", parts=sdk_parts))
                
                tool_results = kwargs.get("tool_results")
                if isinstance(tool_results, list) and tool_results:
                    call_names = kwargs.get("tool_call_names") or []
                    response_parts = []
                    for idx, tr in enumerate(tool_results):
                        name = None
                        if isinstance(call_names, list) and idx < len(call_names):
                            name = call_names[idx]
                        if not name:
                            name = "tool_result"
                        output = tr.get("output", "")
                        try:
                            fr = types.FunctionResponse(name=name, response={"result": output})
                            response_parts.append(types.Part(function_response=fr))
                        except Exception:
                            response_parts.append(types.Part(text=str(output)))
                    if response_parts:
                        contents_obj.append(types.Content(role="user", parts=response_parts))
            except Exception:
                pass

        # 3. Current Message -> User role (only add if not already injected for structured history)
        if not added_current_message:
            if current_message_parts:
                contents_obj.append(types.Content(role="user", parts=list(current_message_parts)))
                added_current_message = True
            elif prompt_text.strip() and not contents_obj:
                contents_obj.append(types.Content(role="user", parts=[types.Part(text=prompt_text.strip())]))
                added_current_message = True

        # Final guard: ensure at least one user part exists
        if not contents_obj:
            contents_obj.append(types.Content(role="user", parts=[types.Part(text="Hello.")]))

        # Debug: log content structure (safely)
        try:
            if self.logger.isEnabledFor(10): # DEBUG
                debug_msg = f"[GOOGLE.CONTENTS] Prepared {len(contents_obj)} turns. "
                if contents_obj:
                    last_parts = contents_obj[-1].parts
                    debug_msg += f"Last turn has {len(last_parts)} parts. "
                    for p in last_parts:
                        if hasattr(p, "file_data") and p.file_data:
                            debug_msg += f"[FileData: {p.file_data.file_uri}] "
                        if hasattr(p, "inline_data") and p.inline_data:
                            debug_msg += f"[InlineData: {p.inline_data.mime_type}] "
                self.logger.debug(debug_msg)
        except Exception:
            pass

        # Execute Call
        if stream:
            try:
                response_stream = client.models.generate_content_stream(
                    model=model,
                    contents=contents_obj,
                    config=config,
                )
                # Pass client to keep it alive during streaming (prevents "client closed" errors)
                return self._handle_stream_events(response_stream, model, client)
            except Exception as e:
                self.logger.error(f"[GOOGLE.STREAM] Failed to start stream: {e}")
                raise

        # Non-streaming
        response = client.models.generate_content(
            model=model,
            contents=contents_obj,
            config=config,
        )

        return response

    async def _handle_stream_events(self, stream, model: str, client=None) -> AsyncGenerator[Dict[str, Any], None]:
        """Process Google streaming events and yield normalized event dictionaries.
        
        Args:
            stream: The streaming response from generate_content_stream
            model: Model name for logging
            client: The genai.Client instance - must be kept alive during streaming
        """
        loop = asyncio.get_event_loop()
        q: asyncio.Queue = asyncio.Queue()
        
        def _push(item):
            try:
                loop.call_soon_threadsafe(q.put_nowait, item)
            except Exception:
                pass
        
        # Heartbeat task to keep connection alive during long thinking pauses
        async def _heartbeat():
            while True:
                await asyncio.sleep(15)
                # Send a ping event to keep the stream active
                _push({"type": "ping"})

        def _stream_thread(client_ref):
            # client_ref is explicitly passed to keep the client alive during streaming
            _ = client_ref  # Prevent "unused variable" warnings; reference keeps client alive
            try:
                for chunk in stream:
                    try:
                        if hasattr(chunk, "candidates") and chunk.candidates:
                            candidate = chunk.candidates[0]
                            content = getattr(candidate, "content", None)
                            if content and hasattr(content, "parts"):
                                for part in content.parts:
                                    # Check for thought_signature (for multi-step tool use)
                                    # The SDK might expose this as 'thought_signature' (snake_case) or 'thoughtSignature'
                                    ts = getattr(part, "thought_signature", None)
                                    if not ts:
                                        ts = getattr(part, "thoughtSignature", None)
                                    
                                    if ts:
                                        _push({"type": "thought_signature", "signature": ts})
                                        # IMPORTANT: Do NOT continue here. The signature is attached to a part that MIGHT also have content 
                                        # (though usually it's attached to function_call or the last text part).
                                        # We need to process the rest of the part (e.g. function_call) to ensure we don't miss it.

                                    # Check for function call
                                    if hasattr(part, "function_call") and part.function_call is not None:
                                        try:
                                            fc = part.function_call
                                            name = getattr(fc, "name", "")
                                            args = getattr(fc, "args", {})
                                            # Convert to dict if possible
                                            if hasattr(args, "items") or isinstance(args, dict):
                                                args = dict(args)
                                            
                                            _push({
                                                "type": "function_call",
                                                "name": name,
                                                "arguments": args,
                                                # Google function calls in stream don't usually have IDs
                                                "id": "",
                                                "call_id": ""
                                            })
                                        except Exception:
                                            pass

                                        # Continue to check for text in the same part (e.g. reasoning or message)


                                    text = getattr(part, "text", "")
                                    
                                    # Check if this is a thought part (SDK dependent)
                                    # Gemini 3 Pro returns thought summaries with thought=True on the part
                                    is_thought = getattr(part, "thought", False)
                                    
                                    if not is_thought:
                                        # Fallback: Try checking dictionary representation if available
                                        try:
                                            if hasattr(part, "to_dict"):
                                                d = part.to_dict()
                                                if d.get("thought"):
                                                    is_thought = True
                                            elif hasattr(part, "model_dump"):
                                                d = part.model_dump()
                                                if d.get("thought"):
                                                    is_thought = True
                                        except Exception:
                                            pass
                                    
                                    if not text:
                                        continue
                                    
                                    # Debug log for thought detection
                                    if is_thought:
                                        self.logger.debug(f"[GOOGLE.STREAM] Thought part detected, len={len(text)}")
                                    
                                    if is_thought:
                                        # Use reasoning_summary for streaming thoughts to align with frontend
                                        _push({"type": "reasoning_summary", "delta": text})
                                    else:
                                        _push({"type": "output.delta", "text": text})
                    except Exception as e:
                        # self.logger.warning(f"[GOOGLE.STREAM] Chunk processing error: {e}")
                        pass
                _push(None)
            except Exception as e:
                _push({"type": "error", "error": str(e)})
                _push(None)

        t = threading.Thread(target=_stream_thread, args=(client,), daemon=True)
        t.start()

        # Start heartbeat
        hb_task = asyncio.create_task(_heartbeat())

        try:
            while True:
                item = await q.get()
                if item is None:
                    break
                yield item
        finally:
            hb_task.cancel()
            try:
                await hb_task
            except asyncio.CancelledError:
                pass
        
        yield {"type": "done"}

    def get_reasoning_summary(self, response: GenerateContentResponse) -> Optional[str]:
        """Extract reasoning summary from Google response.
        
        Gemini 3 Pro returns thought summaries with thought=True on parts.
        """
        try:
            if hasattr(response, "candidates") and response.candidates:
                for candidate in response.candidates:
                    content = getattr(candidate, "content", None)
                    if content and hasattr(content, "parts"):
                        thoughts = []
                        for part in content.parts:
                            # Check if this is a thought part
                            is_thought = getattr(part, "thought", False)
                            if not is_thought:
                                # Fallback: check dictionary representation
                                try:
                                    if hasattr(part, "to_dict"):
                                        d = part.to_dict()
                                        if d.get("thought"):
                                            is_thought = True
                                    elif hasattr(part, "model_dump"):
                                        d = part.model_dump()
                                        if d.get("thought"):
                                            is_thought = True
                                except Exception:
                                    pass
                            
                            if is_thought and hasattr(part, "text") and part.text:
                                thoughts.append(part.text)
                        if thoughts:
                            return "\n".join(thoughts)
        except Exception:
            pass
        return None

    def _clean_schema_for_google(self, obj):
        """Recursively clean schema object for Google Gemini compatibility."""
        if isinstance(obj, dict):
            # Remove additionalProperties at any level
            if "additionalProperties" in obj:
                del obj["additionalProperties"]
            # Some SDK layers may surface snake_case keys in error paths; remove them too
            if "additional_properties" in obj:
                try:
                    del obj["additional_properties"]
                except Exception:
                    pass
            # Remove unsupported composition keywords at this level
            if "oneOf" in obj:
                try:
                    del obj["oneOf"]
                except Exception:
                    pass
            if "anyOf" in obj and isinstance(obj["anyOf"], list):
                # Replace anyOf with first non-null branch for compatibility
                branches = obj.get("anyOf") or []
                chosen = None
                for b in branches:
                    if isinstance(b, dict) and b.get("type") != "null":
                        chosen = b
                        break
                if chosen is None and branches:
                    chosen = branches[0]
                try:
                    obj.pop("anyOf", None)
                    if isinstance(chosen, dict):
                        for k, v in chosen.items():
                            obj[k] = v
                except Exception:
                    pass
                # After flattening anyOf, ensure we re-strip any newly introduced additionalProperties keys
                if "additionalProperties" in obj:
                    obj.pop("additionalProperties", None)
                if "additional_properties" in obj:
                    obj.pop("additional_properties", None)
            
            # Normalize union types by dropping 'null' and preferring the first non-null type
            if "type" in obj and isinstance(obj["type"], list):
                types_list = [t for t in obj["type"] if t != "null"]
                if not types_list:
                    types_list = ["string"]
                obj["type"] = types_list[0]
                # If not an array, remove stray 'items' to satisfy validator
                if obj["type"] != "array" and "items" in obj:
                    del obj["items"]
            # Drop object-only keywords when not an object
            if obj.get("type") and obj.get("type") != "object":
                if "required" in obj:
                    obj.pop("required", None)
                if "properties" in obj:
                    obj.pop("properties", None)
            
            # Recursively clean all values
            for key, value in list(obj.items()):
                if isinstance(value, (dict, list)):
                    self._clean_schema_for_google(value)
            # Final guard: remove any lingering additionalProperties keys introduced during recursion
            if "additionalProperties" in obj:
                obj.pop("additionalProperties", None)
            if "additional_properties" in obj:
                obj.pop("additional_properties", None)
                    
        elif isinstance(obj, list):
            # Recursively clean all items in list
            for item in obj:
                if isinstance(item, (dict, list)):
                    self._clean_schema_for_google(item)

    def _convert_tools_to_google_format(self, tools: list) -> list:
        """Convert tools to Google Gemini format."""
        google_tools = []

        for tool in tools:
            if isinstance(tool, dict):
                # Convert from standard format to Google format
                parameters = tool.get("parameters", {}).copy()

                # Recursively clean the entire parameters object
                self._clean_schema_for_google(parameters)

                # Add optional hints for non-required properties
                if "properties" in parameters:
                    for prop_name, prop_value in list(parameters["properties"].items()):
                        if isinstance(prop_value, dict):
                            # Hint the model to omit rather than use null for optional properties
                            try:
                                required = parameters.get("required", []) or []
                                if prop_name not in required:
                                    desc = prop_value.get("description", "")
                                    hint = " Optional; omit if not used (do not pass null)."
                                    if hint.strip() not in str(desc):
                                        prop_value["description"] = (str(desc) + hint).strip()
                            except Exception:
                                pass

                # Ensure top-level parameters object is valid JSON schema subset
                if not parameters.get("type"):
                    parameters["type"] = "object"

                # Patch: preserve form field schema details that Gemini validator expects
                if tool.get("name") == "define_form":
                    try:
                        form_obj = parameters["properties"]["form"]
                        field_items = form_obj["properties"]["fields"]["items"]
                        field_props = field_items.setdefault("properties", {})
                        if "required" not in field_props:
                            field_props["required"] = {
                                "type": "boolean",
                                "description": "Whether this field must be filled out (default false). Optional; omit if not used (do not pass null).",
                            }
                        # Ensure the field-level required list references existing property keys only
                        req_list = field_items.get("required") or []
                        field_items["required"] = [name for name in req_list if name in field_props]
                    except Exception:
                        pass

                google_tool = {
                    "name": tool.get("name"),
                    "description": tool.get("description", ""),
                    "parameters": parameters,
                }
                google_tools.append(google_tool)

        return google_tools

    def _extract_response_text(self, response: GenerateContentResponse) -> str:
        """Extract text from Google response."""
        try:
            # Check for malformed function call first
            if hasattr(response, "candidates") and response.candidates:
                for candidate in response.candidates:
                    if hasattr(candidate, "finish_reason"):
                        if str(candidate.finish_reason) == "FinishReason.MALFORMED_FUNCTION_CALL":
                            raise GoogleResponseError("MALFORMED_FUNCTION_CALL")
            
            # Extract regular text content from Google response structure
            # Prioritize iterating parts to exclude "thought" parts
            if hasattr(response, "candidates") and response.candidates:
                for candidate in response.candidates:
                    try:
                        content = getattr(candidate, "content", None)
                        if not content:
                            continue
                        parts = getattr(content, "parts", None)
                        if not parts:
                            continue
                        text_parts = []
                        for part in parts:
                            # Skip thought parts
                            if getattr(part, "thought", False):
                                continue
                            
                            if hasattr(part, "text") and part.text:
                                text_parts.append(part.text)
                        if text_parts:
                            return " ".join(text_parts)
                    except Exception:
                        continue

            # Fallback: Direct text attribute (common in mocks or simple responses)
            # Only use this if we couldn't extract from parts (e.g. parts missing)
            if hasattr(response, "text") and isinstance(response.text, str) and response.text:
                return response.text
            
            # Fallback: avoid leaking SDK object reprs into chat history
            # Some SDK versions may stringify GenerateContentResponse objects as a
            # diagnostic (e.g., "sdk_http_response=HttpResponse(...) candidates=None ...").
            # Treat such strings as non-text and return an empty string so callers
            # can retry, stream, or fall back cleanly.
            try:
                s = str(response)
            except Exception:
                s = ""
            if s and (
                "sdk_http_response=HttpResponse" in s
                or "GenerateContentResponseUsageMetadata" in s
                or ("candidates=None" in s and "response_id=" in s)
            ):
                return ""
            return s
        except ValueError as e:
            if "finish_reason" in str(e):
                finish_reason = None
                if hasattr(response, "candidates") and response.candidates:
                    candidate = response.candidates[0]
                    if hasattr(candidate, "finish_reason"):
                        finish_reason = candidate.finish_reason

                if finish_reason == 12:  # BLOCKED_REASON_OTHER or safety block
                    raise GoogleResponseError(
                        "Response blocked by Google safety filters"
                    )
                else:
                    raise GoogleResponseError(
                        f"Response blocked (finish_reason: {finish_reason})"
                    )
            raise GoogleResponseError(
                f"Failed to extract response text: {str(e)}"
            )
        return ""

    def _check_response_completion(
        self, response: GenerateContentResponse
    ) -> bool:
        """Check if Google response is complete."""
        # Check for function calls
        if self.extract_function_calls(response):
            return False

        if hasattr(response, "candidates") and response.candidates:
            for candidate in response.candidates:
                if (
                    hasattr(candidate, "finish_reason")
                    and candidate.finish_reason == "SAFETY"
                ):
                    return False
        return True

    def extract_function_calls(
        self, response: GenerateContentResponse
    ) -> list:
        """Extract function calls from Google response."""
        function_calls = []

        # Check for malformed function call first
        if hasattr(response, "candidates") and response.candidates:
            for candidate in response.candidates:
                if hasattr(candidate, "finish_reason"):
                    if str(candidate.finish_reason) == "FinishReason.MALFORMED_FUNCTION_CALL":
                        raise GoogleResponseError("MALFORMED_FUNCTION_CALL")

        # Preferred: explicit function_calls attribute (SDK may populate this)
        try:
            if hasattr(response, "function_calls") and response.function_calls:
                for fc in response.function_calls:
                    try:
                        name = getattr(fc, "name", None)
                        args = getattr(fc, "args", {})
                        if name:
                            function_calls.append({
                                "name": name,
                                "args": dict(args) if isinstance(args, dict) else args,
                            })
                            self.logger.debug(f"[GOOGLE.FUNCTION_CALL] Found function call (top-level): {name}")
                    except Exception:
                        continue
        except Exception:
            pass

        if hasattr(response, "candidates") and response.candidates:
            for candidate in response.candidates:
                try:
                    content = getattr(candidate, "content", None)
                    if not content:
                        continue
                    parts = getattr(content, "parts", None)
                    if not parts:
                        continue
                    for part in parts:
                        if hasattr(part, "function_call") and part.function_call is not None:
                            # Check if function_call has the required attributes
                            if hasattr(part.function_call, "name") and part.function_call.name is not None:
                                function_call = {
                                    "name": part.function_call.name,
                                    "args": dict(part.function_call.args) if hasattr(part.function_call, "args") else {},
                                }
                                function_calls.append(function_call)
                                self.logger.debug(
                                    f"[GOOGLE.FUNCTION_CALL] Found function call: {function_call['name']}"
                                )
                                # Thought Signature (CRITICAL for Gemini 3)
                                # Check both snake_case (SDK standard) and camelCase (API/JSON standard)
                                sig = getattr(part, "thought_signature", None)
                                if not sig:
                                    sig = getattr(part, "thoughtSignature", None)
                                if sig:
                                    function_call["thought_signature"] = sig
                except Exception:
                    continue

        return function_calls


# Create singleton instance
_google_model = GoogleModelCall()


async def call_model(
    prompt: str,
    config: Optional[Dict[str, Any]] = None,
    model: Optional[str] = None,
    stream: bool = False,
    **kwargs,
) -> GenerateContentResponse:
    """Call a Google model (module-level helper used by ModelSelector)."""
    return await _google_model.call_model(prompt, config, model, stream, **kwargs)


def get_response_text(response: GenerateContentResponse) -> str:
    """Extract text from a Google response (used by ModelSelector)."""
    return _google_model._extract_response_text(response)


def get_reasoning_summary(response: GenerateContentResponse) -> Optional[str]:
    """Extract reasoning summary from a Google response (used by ModelSelector)."""
    return _google_model.get_reasoning_summary(response)


def get_response_id(response: GenerateContentResponse) -> Optional[str]:
    """Get Google response ID (used by ModelSelector)."""
    return _google_model.get_response_id(response)


def is_response_complete(response: GenerateContentResponse) -> bool:
    """Return True if the Google response is complete (used by ModelSelector)."""
    return _google_model.is_response_complete(response)


def extract_function_calls(response: GenerateContentResponse) -> list:
    """Extract structured function calls from a Google response (used by ModelSelector)."""
    return _google_model.extract_function_calls(response)
