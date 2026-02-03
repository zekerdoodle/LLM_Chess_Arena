#!/usr/bin/env python3
"""
Model Selector - Layer 1 ChatBot Component

This module handles model provider selection and routing for the Theo Web App.
It provides a unified interface for calling different AI model providers with
automatic fallback capabilities.
"""

from typing import Any, Dict, Optional

from utils.config_loader import get_config_value, load_config
from utils.logger import get_logger

logger = get_logger(__name__)


class ModelSelector:
    """Handles model provider selection and routing."""

    def __init__(self, config: Optional[Dict[str, Any]] = None):
        """Initialize the model selector with configuration.
        
        Args:
            config: Configuration dictionary. If None, loads from config file.
        """
        self.config = config or load_config()
        # Lazy import providers to avoid hard dependencies during import time
        self.providers = {}
        try:
            from layer1_chatbot.model_call_openai import call_model as openai_call_model
            self.providers["openai"] = openai_call_model
        except Exception:
            self.providers["openai"] = None  # type: ignore
        try:
            from layer1_chatbot.model_call_groq import call_model as groq_call_model
            self.providers["groq"] = groq_call_model
        except Exception:
            self.providers["groq"] = None  # type: ignore
        try:
            from layer1_chatbot.model_call_anthropic import call_model as anthropic_call_model
            self.providers["anthropic"] = anthropic_call_model
        except Exception:
            self.providers["anthropic"] = None  # type: ignore
        try:
            from layer1_chatbot.model_call_google import call_model as google_call_model
            self.providers["google"] = google_call_model
        except Exception:
            self.providers["google"] = None  # type: ignore
        try:
            from layer1_chatbot.model_call_xai import call_model as xai_call_model
            self.providers["xai"] = xai_call_model
        except Exception:
            self.providers["xai"] = None  # type: ignore
        try:
            from layer1_chatbot.model_call_local import call_model as local_call_model
            self.providers["local"] = local_call_model
        except Exception:
            self.providers["local"] = None  # type: ignore

    def get_provider_from_model(self, model: str) -> str:
        """
        Determine the provider from model name.

        Args:
            model: Model name (e.g., 'gpt-4o-mini', 'claude-3-5-sonnet')

        Returns:
            Provider name (e.g., 'openai', 'anthropic')
        """
        model_lower = model.lower()

        # Groq-hosted models
        # Recognize common Groq Cloud model name patterns (without requiring a 'groq:' prefix)
        # Examples: 'openai/gpt-oss-20b', 'llama-3.1-8b-instant', 'meta-llama/llama-guard-4-12b', 'whisper-large-v3'
        # Note: deepseek and qwen models without vendor prefix route to local, not Groq
        groq_keywords = [
            "openai/gpt-oss",      # fully qualified OSS models hosted by Groq
            "gpt-oss-",            # vendorless alias sometimes used for Groq OSS models
            "meta-llama/",         # vendor/model pattern used by Groq
            "whisper-large-v3",    # Groq-hosted audio models
            "whisper-large-v3-turbo",
            "groq/deepseek",       # Groq-hosted deepseek (vendor-qualified)
            "groq/qwen",           # Groq-hosted qwen (vendor-qualified)
            "moonshotai/",
            "playai-tts",
            "compound-beta",
        ]
        if (
            model_lower.startswith("groq:") or
            model_lower.startswith("openai/") or
            model_lower.startswith("gpt-oss-") or
            any(k in model_lower for k in groq_keywords)
        ):
            return "groq"

        # OpenAI models
        # Map GPT family and O-series (o3, o4) to OpenAI
        if (
            # 'openai/' vendor prefix above is routed to Groq; do not match here
            any(prefix in model_lower for prefix in ["gpt-"]) or
            model_lower.startswith("o3") or model_lower.startswith("o4")
        ):
            return "openai"

        # Anthropic models
        if any(prefix in model_lower for prefix in ["claude-", "anthropic"]):
            return "anthropic"

        # Google models
        if any(prefix in model_lower for prefix in ["gemini-", "google"]):
            return "google"

        # xAI models (Grok) - language and image generation
        if any(prefix in model_lower for prefix in ["grok-", "xai:", "xai/"]):
            return "xai"

        # Local models (Ollama) - keep broad patterns, but check after Groq mapping
        if any(prefix in model_lower for prefix in [
            "gemma3:", "gemma3-tools", "petros/", "petrosstav/", "llama:",
            "mistral:", "qwen:", "local-", "mistral-small:", "deepseek:"
        ]) or model_lower.startswith("llama-"):
            return "local"

        # Default fallback
        return "openai"

    async def call_model(
        self,
        prompt: str,
        model: Optional[str] = None,
        disable_internal_fallback: bool = False,
        **kwargs,
    ) -> Any:
        """
        Call the appropriate model based on configuration or model name.
        Implements automatic fallback on provider failures.

        Args:
            prompt: The input prompt
            model: Specific model to use (optional)
            **kwargs: Additional arguments for the model call

        Returns:
            Model response object
        """
        # Get model from config if not specified
        if model is None:
            model = get_config_value(
                self.config, "primary_model", "gpt-5"
            )

        # Store original model before any transformations for fallback comparison
        original_model = model
        
        # If optimizer selected minimal reasoning, route GPT-5 primary traffic to
        # gpt-5-chat-latest for better casual chat. GPT-5-Chat now supports tools,
        # so we route even when tools are present.
        try:
            pm = str(get_config_value(self.config, "primary_model", "gpt-5") or "").lower()
            requested = str(model or "").lower()
            effort = None
            if isinstance(kwargs.get("reasoning"), dict):
                effort = str(kwargs["reasoning"].get("effort") or "").lower()
            is_minimal = effort == "minimal"
            # Only retarget when:
            # - GPT-5 family is in use and matches the configured primary model
            # - Minimal reasoning is selected by the optimizer
            if (requested.startswith("gpt-5") and pm.startswith("gpt-5") and requested == pm
                    and is_minimal):
                if requested.startswith("gpt-5.1"):
                    chat_model = "gpt-5.1-chat-latest"
                else:
                    chat_model = "gpt-5-chat-latest"
                logger.debug(f"L1.selector - Routing minimal-effort call to {chat_model}")
                model = chat_model
                # GPT-5-Chat does not accept reasoning summaries; drop explicit payloads
                kwargs.pop("reasoning", None)
        except (KeyError, AttributeError, TypeError) as _route_err:
            # Expected errors from missing keys/attributes - not a problem
            logger.debug(f"L1.selector - Minimal chat routing skipped (expected): {_route_err}")
        except Exception as _route_err:
            # Unexpected error - log as warning with original model info
            logger.warning(
                f"L1.selector - Unexpected error in model routing, using original model '{model}': {_route_err}",
                exc_info=True
            )

        # Try primary model first
        try:
            provider = self.get_provider_from_model(model)
            logger.debug(f"Using provider: {provider} with model: {model}")

            call_function = self.providers.get(provider)
            if not call_function:
                raise ValueError(f"Unknown provider: {provider}")

            return await call_function(
                prompt, config=self.config, model=model, **kwargs
            )

        except Exception as e:
            # Check if we should try fallback
            if disable_internal_fallback:
                # Defer fallback handling to caller (e.g., orchestrator) so it can rebuild prompts per provider
                raise e

            fallback_model = get_config_value(self.config, "fallback_model")
            
            # Prevent infinite loop: don't fallback if fallback is same as original primary
            # Use original_model (before transformation) for comparison to avoid false negatives
            if fallback_model and original_model and fallback_model == original_model:
                logger.warning(
                    f"L1.main [model:{model}] - Fallback model is same as primary (original: {original_model}), skipping fallback"
                )
                raise e
            
            if fallback_model and original_model and fallback_model != original_model:
                logger.warning(
                    f"L1.main [model:{model}] - Primary model failed: {str(e)}"
                )
                logger.info(
                    f"L1.main [model:{fallback_model}] - Attempting fallback model"
                )

                try:
                    fallback_provider = self.get_provider_from_model(
                        fallback_model
                    )
                    fallback_function = self.providers.get(fallback_provider)
                    if fallback_function:
                        # Disable further internal fallbacks to prevent infinite recursion
                        return await fallback_function(
                            prompt,
                            config=self.config,
                            model=fallback_model,
                            disable_internal_fallback=True,
                            **kwargs,
                        )
                except Exception as fallback_error:
                    logger.error(
                        f"L1.main [model:{fallback_model}] - Fallback model also failed: {str(fallback_error)}"
                    )

            # Re-raise original error if no fallback or fallback failed
            raise e

    def extract_function_calls(self, response: Any, provider: str) -> list:
        """
        Extract function calls from model response using provider-specific logic.
        
        Args:
            response: Raw model response from provider
            provider: Provider name (e.g., 'openai', 'anthropic')
            
        Returns:
            List of function call dictionaries in unified format
        """
        try:
            # Import provider-specific extraction functions
            if provider == "openai":
                from layer1_chatbot.model_call_openai import extract_function_calls
                native_function_calls = extract_function_calls(response)
            elif provider == "groq":
                from layer1_chatbot.model_call_groq import extract_function_calls
                native_function_calls = extract_function_calls(response)
            elif provider == "anthropic":
                from layer1_chatbot.model_call_anthropic import extract_function_calls
                native_function_calls = extract_function_calls(response)
            elif provider == "google":
                from layer1_chatbot.model_call_google import extract_function_calls
                native_function_calls = extract_function_calls(response)
            elif provider == "xai":
                from layer1_chatbot.model_call_xai import extract_function_calls
                native_function_calls = extract_function_calls(response)
            elif provider == "local":
                from layer1_chatbot.model_call_local import extract_function_calls
                native_function_calls = extract_function_calls(response)
            else:
                logger.warning(f"Unknown provider: {provider}, no function calls extracted")
                return []

            # Convert to unified format
            import json
            unified_function_calls = []
            
            for func_call in native_function_calls:
                try:
                    # Handle all provider formats:
                    # OpenAI/xAI: "arguments", Anthropic: "input", Google: "args"
                    arguments_raw = (
                        func_call.get("arguments") or 
                        func_call.get("input") or 
                        func_call.get("args", "{}")
                    )
                    if isinstance(arguments_raw, str):
                        arguments = json.loads(arguments_raw)
                    else:
                        arguments = arguments_raw
                except BaseException:
                    # Fallback to raw values
                    arguments = (
                        func_call.get("arguments", {}) or 
                        func_call.get("input", {}) or 
                        func_call.get("args", {})
                    )

                # Normalize id → call_id for providers that use different fields (e.g., Anthropic)
                call_id = func_call.get("call_id") or func_call.get("id")
                unified_function_calls.append({
                    "name": func_call.get("name"),
                    "arguments": arguments,
                    "call_id": call_id,
                })

            logger.debug(f"L1.{provider} - Extracted {len(unified_function_calls)} function calls")
            return unified_function_calls

        except Exception as e:
            # Special-case Google MALFORMED_FUNCTION_CALL so the orchestrator can retry/fallback
            if provider == "google":
                try:
                    from layer1_chatbot.model_call_google import GoogleResponseError  # lazy import
                except Exception:
                    GoogleResponseError = None  # type: ignore
                # If we can match the exact type, or detect by name/message, bubble up
                if (GoogleResponseError and isinstance(e, GoogleResponseError)) or \
                   getattr(e, "__class__", type(e)).__name__ == "GoogleResponseError" or \
                   ("MALFORMED_FUNCTION_CALL" in str(e)):
                    raise e

            logger.error(f"L1.{provider} - Error extracting function calls: {e}", exc_info=True)
            return []

    def extract_response_text(self, response: Any, provider: str, model: str) -> Optional[str]:
        """
        Extract text from model response using provider-specific logic.
        
        Args:
            response: Raw model response from provider
            provider: Provider name (e.g., 'openai', 'anthropic')
            model: Model name for logging context
            
        Returns:
            Extracted response text or None
        """
        try:
            # Import provider-specific extraction functions
            if provider == "openai":
                from layer1_chatbot.model_call_openai import get_response_text
                response_text = get_response_text(response)
            elif provider == "groq":
                from layer1_chatbot.model_call_groq import get_response_text
                response_text = get_response_text(response)
            elif provider == "anthropic":
                from layer1_chatbot.model_call_anthropic import get_response_text
                response_text = get_response_text(response)
            elif provider == "google":
                from layer1_chatbot.model_call_google import get_response_text
                response_text = get_response_text(response)
            elif provider == "xai":
                from layer1_chatbot.model_call_xai import get_response_text
                response_text = get_response_text(response)
            elif provider == "local":
                # Local responses are already LocalResponse objects with .content
                response_text = getattr(response, 'content', str(response))
            else:
                # Fallback to string conversion
                logger.warning(
                    f"L1.{provider} [model:{model}] - Response text extraction failed, using fallback method"
                )
                response_text = str(response)

            logger.debug(f"L1.{provider} [model:{model}] - Extracted response text")
            return response_text

        except Exception as e:
            logger.error(
                f"L1.{provider} [model:{model}] - Error extracting response text: {e}",
                exc_info=True,
            )
            return None 
