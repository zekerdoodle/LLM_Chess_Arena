"""
Local Model Call Implementation for Ollama

This module provides local model inference capabilities through Ollama,
allowing Theo to use models running on the local machine or network.
"""

import asyncio
import json
from typing import Any, Dict, Optional

import aiohttp

from layer1_chatbot.base_model import BaseModelCall, ModelResponseError
from utils.config_loader import get_config_value
from utils.logger import get_logger

logger = get_logger(__name__)


class LocalResponse:
    """Local model response object that mimics cloud provider responses."""
    
    def __init__(self, content: str, model: str, response_data: Dict[str, Any]):
        self.content = content
        self.text = content  # Alternative access pattern
        self.model = model
        self.response_data = response_data
        self.id = f"local-{hash(content) % 1000000}"  # Simple ID generation
    
    def __str__(self) -> str:
        return self.content


class LocalModelCall(BaseModelCall):
    """Local model call implementation using Ollama."""
    
    def __init__(self):
        super().__init__("local")
        
    def _get_api_key(self, config: Dict[str, Any]) -> Optional[str]:
        """Local models don't need API keys."""
        return "local-key"  # Return dummy key to satisfy base class
    
    def _get_default_model(self) -> str:
        """Get default local model."""
        return "gemma3:12b"
    
    async def _make_api_call(
        self, prompt: str, api_key: str, model: str, stream: bool = False, **kwargs
    ) -> LocalResponse:
        """Make API call to local Ollama instance."""
        # Get Ollama host from config
        ollama_host = get_config_value(
            kwargs.get('config', {}), 
            'ollama_host', 
            'http://localhost:11434'
        )
        
        # Handle conversation format
        if isinstance(prompt, list):
            messages = prompt
            # Convert to string for generate API if needed
            prompt_text = "\n".join([
                f"{msg.get('role', 'user')}: {msg.get('content', '')}" 
                for msg in prompt
            ])
        else:
            messages = [{"role": "user", "content": prompt}]
            prompt_text = prompt
        
        # Check if tools are provided (for function calling)
        tools = kwargs.get("tools", [])
        use_chat_api = bool(tools)  # Use chat API if tools are provided
        
        if use_chat_api:
            # Use Ollama's chat API with tool support
            request_data = {
                "model": model,
                "messages": messages,
                "stream": False,
                "keep_alive": "1h",  # Keep model loaded for 1 hour
                "options": {
                    "temperature": kwargs.get("temperature", 0.7),
                    "top_p": kwargs.get("top_p", 0.9),
                    "num_predict": kwargs.get("max_tokens", 2048),
                    "num_ctx": 131072,  # Full 128k context window
                }
            }
            
            # Add tools if provided
            if tools:
                request_data["tools"] = tools
                
            api_endpoint = f"{ollama_host}/api/chat"
        else:
            # Use traditional generate API for regular text generation
            request_data = {
                "model": model,
                "prompt": prompt_text,
                "stream": False,
                "keep_alive": "1h",  # Keep model loaded for 1 hour
                "options": {
                    "temperature": kwargs.get("temperature", 0.7),
                    "top_p": kwargs.get("top_p", 0.9),
                    "num_predict": kwargs.get("max_tokens", 2048),
                    "num_ctx": 131072,  # Full 128k context window
                }
            }
            api_endpoint = f"{ollama_host}/api/generate"
        
        # Handle structured outputs (OpenAI format)
        text_format = kwargs.get("text_format")
        if text_format and isinstance(text_format, dict):
            if text_format.get("type") == "json_schema":
                # Add JSON mode instruction to prompt
                schema_name = text_format.get("name", "response")
                prompt_with_json = f"{prompt_text}\n\nPlease respond with valid JSON only, following this schema name: {schema_name}"
                request_data["prompt"] = prompt_with_json
                request_data["options"]["temperature"] = 0.1  # Lower temp for structured output
        
        try:
            timeout = aiohttp.ClientTimeout(total=1800)  # 30 minute timeout for slow local inference
            async with aiohttp.ClientSession(timeout=timeout) as session:
                
                self.logger.debug(f"Calling local model at {api_endpoint} with model {model}")
                
                async with session.post(api_endpoint, json=request_data) as response:
                    if response.status != 200:
                        error_text = await response.text()
                        raise ModelResponseError(
                            f"Local model API returned status {response.status}: {error_text}"
                        )
                    
                    response_data = await response.json()
                    
                    if use_chat_api:
                        # Extract content from chat API response
                        message = response_data.get("message", {})
                        content = message.get("content", "")
                        tool_calls = message.get("tool_calls", [])
                        
                        # Store tool calls in response data for extraction
                        response_data["tool_calls"] = tool_calls
                    else:
                        # Extract content from generate API response
                        content = response_data.get("response", "")
                    
                    if not content and not response_data.get("tool_calls"):
                        raise ModelResponseError("Empty response from local model")
                    
                    # Create response object
                    local_response = LocalResponse(content or "", model, response_data)
                    
                    self.logger.debug(f"Local model response: {len(content)} characters, {len(response_data.get('tool_calls', []))} tool calls")
                    return local_response
                    
        except asyncio.TimeoutError:
            raise ModelResponseError("Local model request timed out")
        except aiohttp.ClientError as e:
            raise ModelResponseError(f"Local model connection error: {str(e)}")
        except json.JSONDecodeError as e:
            raise ModelResponseError(f"Invalid JSON response from local model: {str(e)}")
        except Exception as e:
            raise ModelResponseError(f"Unexpected error calling local model: {str(e)}")
    
    def _extract_response_text(self, response: LocalResponse) -> str:
        """Extract text content from local response."""
        return response.content
    
    def _check_response_completion(self, response: LocalResponse) -> bool:
        """Check if local response is complete."""
        # Check if there are tool calls that need to be processed
        if response.response_data.get("tool_calls"):
            return False  # More processing needed for tool calls
        return True
    
    def extract_function_calls(self, response) -> list:
        """Extract function calls from local model response."""
        function_calls = []
        
        # Handle both LocalResponse and fallback response objects
        if hasattr(response, 'response_data'):
            tool_calls = response.response_data.get("tool_calls", [])
        else:
            # Fallback case - no tool calls from non-local response
            return []
        
        for tool_call in tool_calls:
            try:
                # Ollama tool call format
                if isinstance(tool_call, dict) and "function" in tool_call:
                    function_info = tool_call["function"]
                    function_calls.append({
                        "name": function_info.get("name", ""),
                        "arguments": function_info.get("arguments", {})
                    })
                # Direct format (fallback)
                elif isinstance(tool_call, dict) and "name" in tool_call:
                    function_calls.append({
                        "name": tool_call.get("name", ""),
                        "arguments": tool_call.get("arguments", {})
                    })
            except Exception as e:
                self.logger.error(f"Error parsing tool call: {e}")
                continue
        
        return function_calls


# Create instance for import
local_model_call = LocalModelCall()


def extract_function_calls(response) -> list:
    """Extract structured function calls from a local response (used by ModelSelector)."""
    return local_model_call.extract_function_calls(response)


async def call_model(
    prompt: str,
    config: Optional[Dict[str, Any]] = None,
    model: Optional[str] = None,
    stream: bool = False,
    **kwargs
) -> LocalResponse:
    """
    Call local model through Ollama.
    
    Args:
        prompt: The input prompt
        config: Configuration dictionary
        model: Model to use (defaults to gemma3:12b)
        stream: Whether to stream response (not implemented)
        **kwargs: Additional arguments
        
    Returns:
        LocalResponse object with generated text
        
    Raises:
        ModelResponseError: If the model call fails
    """
    return await local_model_call.call_model(
        prompt=prompt,
        config=config,
        model=model or "gemma3:12b",
        stream=stream,
        **kwargs
    )
