# OpenAI API Streaming Documentation

## Overview

OpenAI provides comprehensive streaming support across their API endpoints, enabling real-time token generation for chat completions, assistants, and other text generation tasks.

## Official Documentation
- **Main API Documentation**: https://platform.openai.com/docs
- **Chat Completions**: https://platform.openai.com/docs/guides/text-generation
- **API Reference**: https://platform.openai.com/docs/api-reference
- **Playground**: https://platform.openai.com/playground

## Key Features

- **Chat Completions Streaming**: Real-time message generation
- **Assistant API Streaming**: Streaming for assistant responses
- **Function Calling**: Tool use with streaming responses
- **Structured Outputs**: JSON schema with streaming
- **Vision Support**: Streaming with image inputs
- **Audio Support**: Real-time audio processing

## Streaming Configuration

### Basic Chat Completion Streaming

```bash
curl https://api.openai.com/v1/chat/completions \
  -H "Content-Type: application/json" \
  -H "Authorization: Bearer $OPENAI_API_KEY" \
  -d '{
    "model": "gpt-4o",
    "messages": [{"role": "user", "content": "Explain machine learning"}],
    "stream": true
  }'
```

### Python SDK Implementation

```python
import openai
from openai import OpenAI

client = OpenAI(api_key="your-api-key")

def stream_chat_completion(messages, model="gpt-4o"):
    stream = client.chat.completions.create(
        model=model,
        messages=messages,
        stream=True,
        temperature=0.7,
        max_tokens=1000
    )
    
    for chunk in stream:
        if chunk.choices[0].delta.content is not None:
            print(chunk.choices[0].delta.content, end="", flush=True)
    
    print()  # New line after streaming completes

# Usage
messages = [
    {"role": "system", "content": "You are a helpful assistant."},
    {"role": "user", "content": "Write a short story about AI"}
]

stream_chat_completion(messages)
```

### JavaScript Implementation

```javascript
import OpenAI from 'openai';

const openai = new OpenAI({
    apiKey: process.env.OPENAI_API_KEY,
});

async function streamChatCompletion(messages, model = "gpt-4o") {
    const stream = await openai.chat.completions.create({
        model: model,
        messages: messages,
        stream: true,
        temperature: 0.7,
        max_tokens: 1000
    });

    for await (const chunk of stream) {
        const content = chunk.choices[0]?.delta?.content || '';
        if (content) {
            process.stdout.write(content);
        }
    }
    console.log(); // New line
}

// Usage
const messages = [
    { role: "system", content: "You are a helpful assistant." },
    { role: "user", content: "Explain quantum computing" }
];

streamChatCompletion(messages);
```

### Raw HTTP Streaming

```python
import requests
import json

def stream_openai_raw(messages, model="gpt-4o"):
    url = "https://api.openai.com/v1/chat/completions"
    headers = {
        "Authorization": f"Bearer {api_key}",
        "Content-Type": "application/json"
    }
    
    data = {
        "model": model,
        "messages": messages,
        "stream": True,
        "temperature": 0.7
    }
    
    response = requests.post(url, headers=headers, json=data, stream=True)
    response.raise_for_status()
    
    for line in response.iter_lines():
        if line:
            line = line.decode('utf-8')
            if line.startswith('data: '):
                json_str = line[6:]  # Remove 'data: ' prefix
                if json_str.strip() == '[DONE]':
                    break
                try:
                    chunk = json.loads(json_str)
                    content = chunk['choices'][0]['delta'].get('content', '')
                    if content:
                        print(content, end='', flush=True)
                except json.JSONDecodeError:
                    continue
```

## Supported Models

### Text Models (All support streaming)
- **GPT-4o**: Most capable multimodal model
- **GPT-4o mini**: Fast and cost-effective
- **GPT-4 Turbo**: Previous generation flagship
- **GPT-3.5 Turbo**: Fast and efficient
- **o1-preview**: Advanced reasoning (limited streaming)
- **o1-mini**: Faster reasoning model

### Multimodal Support
- **Vision**: GPT-4o, GPT-4 Turbo with vision
- **Audio**: Real-time audio API (separate endpoint)
- **Images**: DALL-E integration (non-streaming)

## Key Parameters

### Required Parameters
- `model`: Model identifier
- `messages`: Array of message objects
- `stream`: Set to `true` for streaming

### Optional Parameters
- `max_tokens`: Maximum tokens to generate
- `temperature`: Randomness (0-2, default: 1)
- `top_p`: Nucleus sampling (0-1, default: 1)
- `frequency_penalty`: Penalize frequent tokens (-2 to 2)
- `presence_penalty`: Penalize new topics (-2 to 2)
- `stop`: Custom stop sequences
- `seed`: For reproducible outputs
- `response_format`: Structure output format

### Streaming-Specific Parameters
- `stream_options`: Additional streaming options
  - `include_usage`: Include token usage in final chunk

## Response Format

### Streaming Chunk Structure
```json
{
  "id": "chatcmpl-123",
  "object": "chat.completion.chunk",
  "created": 1677652288,
  "model": "gpt-4o",
  "system_fingerprint": "fp_44709d6fcb",
  "choices": [{
    "index": 0,
    "delta": {
      "role": "assistant",
      "content": "Hello"
    },
    "logprobs": null,
    "finish_reason": null
  }]
}
```

### Final Chunk with Usage
```json
{
  "id": "chatcmpl-123",
  "object": "chat.completion.chunk", 
  "created": 1677652288,
  "model": "gpt-4o",
  "system_fingerprint": "fp_44709d6fcb",
  "choices": [{
    "index": 0,
    "delta": {},
    "logprobs": null,
    "finish_reason": "stop"
  }],
  "usage": {
    "prompt_tokens": 9,
    "completion_tokens": 12,
    "total_tokens": 21
  }
}
```

## Advanced Features

### Function Calling with Streaming

```python
tools = [{
    "type": "function",
    "function": {
        "name": "get_current_weather",
        "description": "Get the current weather in a given location",
        "parameters": {
            "type": "object",
            "properties": {
                "location": {
                    "type": "string",
                    "description": "The city and state, e.g. San Francisco, CA"
                },
                "unit": {
                    "type": "string", 
                    "enum": ["celsius", "fahrenheit"]
                }
            },
            "required": ["location"]
        }
    }
}]

stream = client.chat.completions.create(
    model="gpt-4o",
    messages=[{"role": "user", "content": "What's the weather in Boston?"}],
    tools=tools,
    tool_choice="auto",
    stream=True
)

for chunk in stream:
    if chunk.choices[0].delta.tool_calls:
        for tool_call in chunk.choices[0].delta.tool_calls:
            print(f"Tool call: {tool_call}")
    elif chunk.choices[0].delta.content:
        print(chunk.choices[0].delta.content, end="", flush=True)
```

### Structured Outputs with Streaming

```python
from pydantic import BaseModel
from typing import List

class Step(BaseModel):
    explanation: str
    output: str

class MathReasoning(BaseModel):
    steps: List[Step]
    final_answer: str

stream = client.beta.chat.completions.parse(
    model="gpt-4o",
    messages=[
        {"role": "user", "content": "Solve 2x + 3 = 7"}
    ],
    response_format=MathReasoning,
    stream=True
)

for chunk in stream:
    if chunk.choices[0].delta.parsed:
        print(chunk.choices[0].delta.parsed)
```

### Vision with Streaming

```python
messages = [
    {
        "role": "user",
        "content": [
            {"type": "text", "text": "What's in this image?"},
            {
                "type": "image_url",
                "image_url": {
                    "url": "https://example.com/image.jpg"
                }
            }
        ]
    }
]

stream = client.chat.completions.create(
    model="gpt-4o",
    messages=messages,
    max_tokens=300,
    stream=True
)

for chunk in stream:
    if chunk.choices[0].delta.content:
        print(chunk.choices[0].delta.content, end="", flush=True)
```

## Assistant API Streaming

### Streaming Assistant Responses

```python
# Create a thread and run with streaming
thread = client.beta.threads.create()

client.beta.threads.messages.create(
    thread_id=thread.id,
    role="user",
    content="Explain the theory of relativity"
)

with client.beta.threads.runs.stream(
    thread_id=thread.id,
    assistant_id=assistant_id
) as stream:
    for event in stream:
        if event.event == 'thread.message.delta':
            for content in event.data.delta.content:
                if content.type == 'text':
                    print(content.text.value, end="", flush=True)
```

## Rate Limits and Pricing

### Current Pricing (as of August 2025)
- **GPT-4o**: $5.00/1M input, $15.00/1M output tokens
- **GPT-4o mini**: $0.15/1M input, $0.60/1M output tokens
- **GPT-4 Turbo**: $10.00/1M input, $30.00/1M output tokens

### Rate Limits
- **Requests per minute**: Varies by tier (500-10,000+)
- **Tokens per minute**: Varies by tier and model
- **Concurrent requests**: Limited based on tier

## Error Handling

### Common Error Response
```json
{
  "error": {
    "message": "Rate limit reached",
    "type": "rate_limit_error",
    "param": null,
    "code": "rate_limit_exceeded"
  }
}
```

### Error Types
- `rate_limit_error`: Rate limiting
- `invalid_request_error`: Bad parameters
- `authentication_error`: Invalid API key
- `permission_error`: Insufficient permissions
- `not_found_error`: Resource not found
- `unprocessable_entity_error`: Request couldn't be processed
- `internal_server_error`: Server error

## Complete Implementation Example

```python
import openai
import asyncio
import json
from typing import AsyncIterator, Iterator, Dict, Any
from openai import OpenAI, AsyncOpenAI

class OpenAIStreamer:
    def __init__(self, api_key: str):
        self.client = OpenAI(api_key=api_key)
        self.async_client = AsyncOpenAI(api_key=api_key)
    
    def stream_chat(
        self, 
        messages: list,
        model: str = "gpt-4o",
        **kwargs
    ) -> Iterator[str]:
        """Synchronous streaming"""
        try:
            stream = self.client.chat.completions.create(
                model=model,
                messages=messages,
                stream=True,
                **kwargs
            )
            
            for chunk in stream:
                content = chunk.choices[0].delta.content
                if content:
                    yield content
                    
        except Exception as e:
            raise Exception(f"OpenAI streaming error: {e}")
    
    async def stream_chat_async(
        self,
        messages: list,
        model: str = "gpt-4o", 
        **kwargs
    ) -> AsyncIterator[str]:
        """Asynchronous streaming"""
        try:
            stream = await self.async_client.chat.completions.create(
                model=model,
                messages=messages,
                stream=True,
                **kwargs
            )
            
            async for chunk in stream:
                content = chunk.choices[0].delta.content
                if content:
                    yield content
                    
        except Exception as e:
            raise Exception(f"OpenAI async streaming error: {e}")
    
    def stream_with_tools(
        self,
        messages: list,
        tools: list,
        model: str = "gpt-4o"
    ):
        """Stream with function calling"""
        stream = self.client.chat.completions.create(
            model=model,
            messages=messages,
            tools=tools,
            tool_choice="auto",
            stream=True
        )
        
        for chunk in stream:
            choice = chunk.choices[0]
            
            if choice.delta.content:
                yield {"type": "content", "data": choice.delta.content}
            
            if choice.delta.tool_calls:
                for tool_call in choice.delta.tool_calls:
                    yield {"type": "tool_call", "data": tool_call}
            
            if choice.finish_reason:
                yield {"type": "finish", "reason": choice.finish_reason}

# Usage example
async def main():
    streamer = OpenAIStreamer("your-api-key")
    
    messages = [
        {"role": "system", "content": "You are a helpful assistant."},
        {"role": "user", "content": "Write a Python function to sort a list"}
    ]
    
    # Synchronous streaming
    print("Sync streaming:")
    for chunk in streamer.stream_chat(messages, temperature=0.7):
        print(chunk, end="", flush=True)
    
    print("\n\nAsync streaming:")
    # Asynchronous streaming
    async for chunk in streamer.stream_chat_async(messages, temperature=0.7):
        print(chunk, end="", flush=True)
    
    print("\n\nWith tools:")
    # Function calling example
    tools = [{
        "type": "function",
        "function": {
            "name": "execute_python",
            "description": "Execute Python code",
            "parameters": {
                "type": "object",
                "properties": {
                    "code": {"type": "string"}
                },
                "required": ["code"]
            }
        }
    }]
    
    for event in streamer.stream_with_tools(messages, tools):
        if event["type"] == "content":
            print(event["data"], end="", flush=True)
        elif event["type"] == "tool_call":
            print(f"\nTool call: {event['data']}")
        elif event["type"] == "finish":
            print(f"\nFinished: {event['reason']}")

if __name__ == "__main__":
    asyncio.run(main())
```

## Best Practices

1. **Connection Management**
   - Use appropriate timeouts
   - Handle network interruptions gracefully
   - Implement retry logic with exponential backoff

2. **Token Management**
   - Monitor token usage for cost control
   - Set appropriate max_tokens limits
   - Use efficient prompting techniques

3. **Error Handling**
   - Handle all error types appropriately
   - Implement graceful degradation
   - Log errors for debugging

4. **Performance Optimization**
   - Use async for multiple concurrent streams
   - Choose appropriate models for tasks
   - Cache responses when possible

## Troubleshooting

### Common Issues

1. **Rate Limiting**
   - Monitor request patterns
   - Implement proper queuing
   - Use exponential backoff

2. **Incomplete Streams**
   - Check for proper error handling
   - Verify network connectivity
   - Monitor finish_reason values

3. **Token Counting**
   - Use `stream_options: {"include_usage": true}`
   - Track cumulative usage
   - Account for prompt tokens

### Debug Tips

1. Enable request/response logging
2. Test with playground first
3. Monitor API status page
4. Use smaller models for testing

---

**Resources:**
- [OpenAI Platform](https://platform.openai.com/)
- [API Documentation](https://platform.openai.com/docs)
- [Community Forum](https://community.openai.com/)
- [Status Page](https://status.openai.com/)
- [Python SDK](https://github.com/openai/openai-python)

*Last updated: August 29, 2025*
