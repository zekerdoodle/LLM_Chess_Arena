"""
Base Model Call Implementation

This module provides a base class for all model call implementations
to eliminate code duplication and standardize the interface.
"""

from abc import ABC, abstractmethod
from typing import Any, Dict, Optional

from utils.config_loader import get_config_value, load_config
from utils.logger import get_logger

logger = get_logger(__name__)


class ModelResponseError(Exception):
    """Base exception for model API errors."""


class BaseModelCall(ABC):
    """Base class for all model call implementations."""

    def __init__(self, provider_name: str):
        """Initialize the base model call."""
        self.provider_name = provider_name
        self.logger = get_logger(f"{__name__}.{provider_name}")

    async def call_model(
        self,
        prompt: str,
        config: Optional[Dict[str, Any]] = None,
        model: Optional[str] = None,
        stream: bool = False,
        **kwargs,
    ) -> Any:
        """
        Call the model with standardized error handling and logging.

        Args:
            prompt: The input prompt
            config: Configuration dictionary
            model: Model to use
            stream: Whether to stream the response
            **kwargs: Additional provider-specific arguments

        Returns:
            Model response object

        Raises:
            ModelResponseError: If the API call fails
        """
        # Load config if not provided
        if config is None:
            config = load_config()

        # Get API key
        api_key = self._get_api_key(config)
        if not api_key:
            raise ModelResponseError(
                f"{self.provider_name} API key not found in configuration"
            )

        # Get model from config if not specified
        if model is None:
            model = get_config_value(
                config, "primary_model", self._get_default_model()
            )

        # Use structured logging for model calls
        from utils.logger import log_content_with_summary
        # Convert list prompts to string for logging
        prompt_for_logging = prompt
        if isinstance(prompt, list):
            # Safely serialize mixed list items (dicts or SDK models)
            lines = []
            for msg in prompt:
                try:
                    # Standard dict case
                    if isinstance(msg, dict):
                        role = msg.get("role", msg.get("type", "item"))
                        content = msg.get("content", msg.get("text", ""))
                        if not isinstance(content, str):
                            content = str(content)
                        lines.append(f"{role}: {content[:500]}")
                        continue

                    # OpenAI/SDK or pydantic-style model
                    if hasattr(msg, "model_dump") and callable(getattr(msg, "model_dump")):
                        md = msg.model_dump()
                        role = md.get("role") or md.get("type", "item")
                        content = md.get("content") or md.get("text") or ""
                        # Normalize content into a short string
                        if isinstance(content, list):
                            parts = []
                            for c in content:
                                if isinstance(c, dict):
                                    if "text" in c:
                                        parts.append(str(c.get("text", "")))
                                    elif "type" in c:
                                        parts.append(f"[{c.get('type')}]")
                                    else:
                                        parts.append(str(c))
                                else:
                                    parts.append(str(c))
                            content = " ".join(parts)
                        elif not isinstance(content, str):
                            content = str(content)
                        lines.append(f"{role}: {content[:500]}")
                        continue

                    # Fallback: best-effort stringification
                    role = getattr(msg, "role", getattr(msg, "type", "item"))
                    content = getattr(msg, "content", getattr(msg, "text", ""))
                    if not isinstance(content, str):
                        content = str(content)
                    lines.append(f"{role}: {content[:500]}")
                except Exception:
                    lines.append(f"[item:{type(msg).__name__}]")

            # Join serialized items for logging
            prompt_for_logging = "\n".join(lines)
        log_content_with_summary(
            self.logger, prompt_for_logging, "prompt", "L1", self.provider_name, 
            context=f"model:{model}", log_level="DEBUG"
        )

        try:
            # Make the actual API call - properly await the async method
            # Pass config in kwargs so providers can access it for native features
            response = await self._make_api_call(
                prompt, api_key, model, stream, config=config, **kwargs
            )

            # Log successful response with structured format
            # Be tolerant of extraction errors (e.g., malformed function calls)
            response_text = ""
            text_extraction_failed = False
            try:
                response_text = self._extract_response_text(response)
            except Exception as te:
                # Do not fail the whole call if text extraction fails; tool orchestration may still proceed
                text_extraction_failed = True
                self.logger.warning(
                    f"L1.{self.provider_name} [model:{model}] - Text extraction failed (response object still valid for tool handling): {te}",
                    exc_info=True
                )
                response_text = "(extraction failed)"
            from utils.logger import log_model_call
            from utils.token_counter import count_tokens
            import time
            # Calculate prompt length correctly using token counting
            prompt_length = count_tokens(prompt_for_logging, model) if prompt_for_logging else 0
            # Note: duration tracking would need to be added to the call_model method
            # Only log response length if extraction succeeded
            response_length = 0 if text_extraction_failed else len(response_text)
            log_model_call(
                self.logger, self.provider_name, model, 
                prompt_length, response_length, layer="L1"
            )

            return response

        except Exception as e:
            error_msg = f"{self.provider_name} API call failed: {str(e)}"
            self.logger.error(error_msg, exc_info=True)
            raise ModelResponseError(error_msg) from e

    def get_response_text(self, response: Any) -> str:
        """Extract text content from response object."""
        try:
            return self._extract_response_text(response)
        except Exception as e:
            self.logger.error(f"Failed to extract text from response: {e}")
            return ""

    def get_response_id(self, response: Any) -> Optional[str]:
        """Get response ID for conversation continuity."""
        try:
            return getattr(response, "id", None)
        except Exception as e:
            self.logger.error(f"Failed to get response ID: {e}")
            return None

    def is_response_complete(self, response: Any) -> bool:
        """Check if response is complete (no pending tool calls)."""
        try:
            return self._check_response_completion(response)
        except Exception as e:
            self.logger.error(f"Failed to check response completion: {e}")
            return True

    @abstractmethod
    def _get_api_key(self, config: Dict[str, Any]) -> Optional[str]:
        """Get API key for this provider from config."""

    @abstractmethod
    def _get_default_model(self) -> str:
        """Get default model name for this provider."""

    @abstractmethod
    async def _make_api_call(
        self, prompt: str, api_key: str, model: str, stream: bool, **kwargs
    ) -> Any:
        """Make the actual API call to the provider."""

    @abstractmethod
    def _extract_response_text(self, response: Any) -> str:
        """Extract text content from provider-specific response object."""

    @abstractmethod
    def _check_response_completion(self, response: Any) -> bool:
        """Check if response is complete (provider-specific implementation)."""
