# Streaming Responses

## Overview
xAI's Grok API streams text and reasoning tokens across every text-capable model. Responses are delivered over Server-Sent Events and expose the same chunk format as the OpenAI Chat Completions API, making it simple to integrate with existing tooling while still unlocking Grok-specific features like extended thinking.

### Official Resources
- [xAI documentation](https://docs.x.ai/)
- [Streaming response guide](https://docs.x.ai/docs/guides/streaming-response)
- [xAI console](https://accounts.x.ai/sign-in)
- [Model catalog](https://docs.x.ai/docs/models)

## Quick Start

```bash
curl https://api.x.ai/v1/chat/completions \
  -H "Content-Type: application/json" \
  -H "Authorization: Bearer $XAI_API_KEY" \
  -d '{
    "model": "grok-4",
    "messages": [{"role": "user", "content": "Explain quantum computing"}],
    "stream": true
  }'
```

## xAI SDK helper

```python
import os
from xai_sdk import Client
from xai_sdk.chat import system, user


client = Client(
    api_key=os.getenv("XAI_API_KEY"),
    timeout=3600,  # extend timeout for reasoning-heavy runs
)


chat = client.chat.create(model="grok-4")
chat.append(system("You are a helpful AI assistant."))
chat.append(user("Explain the importance of streaming in AI applications"))

for response, chunk in chat.stream():
    print(chunk.content, end="", flush=True)

print(f"\nFull response: {response.content}")
```

## OpenAI SDK compatibility

```python
import openai


client = openai.OpenAI(
    api_key="your-xai-api-key",
    base_url="https://api.x.ai/v1",
)


stream = client.chat.completions.create(
    model="grok-4",
    messages=[{"role": "user", "content": "Write a poem about AI"}],
    stream=True,
    temperature=0.8,
)

for chunk in stream:
    delta = chunk.choices[0].delta.content
    if delta is not None:
        print(delta, end="", flush=True)
```

## JavaScript helper

```javascript
export async function streamGrokResponse(messages, xaiApiKey, model = "grok-4") {
  const response = await fetch("https://api.x.ai/v1/chat/completions", {
    method: "POST",
    headers: {
      Authorization: `Bearer ${xaiApiKey}`,
      "Content-Type": "application/json",
    },
    body: JSON.stringify({
      model,
      messages,
      stream: true,
      temperature: 0.7,
    }),
  });

  const reader = response.body.getReader();
  const decoder = new TextDecoder();
  let buffer = "";

  while (true) {
    const { done, value } = await reader.read();
    if (done) break;

    buffer += decoder.decode(value, { stream: true });
    const lines = buffer.split("\n");
    buffer = lines.pop() ?? "";

    for (const line of lines) {
      if (!line.startsWith("data: ")) continue;
      const body = line.slice(6);
      if (body.trim() === "[DONE]") return;

      const data = JSON.parse(body);
      const content = data.choices[0]?.delta?.content;
      if (content) {
        process.stdout.write(content);
      }
    }
  }

  process.stdout.write("\n");
}
```

## Event sample

```json
{
  "id": "<completion_id>",
  "object": "chat.completion.chunk",
  "created": 1730241104,
  "model": "grok-4",
  "choices": [
    {
      "index": 0,
      "delta": {
        "content": "Ah",
        "role": "assistant"
      }
    }
  ],
  "usage": {
    "prompt_tokens": 41,
    "completion_tokens": 1,
    "total_tokens": 42,
    "prompt_tokens_details": {
      "text_tokens": 41,
      "audio_tokens": 0,
      "image_tokens": 0,
      "cached_tokens": 0
    }
  },
  "system_fingerprint": "fp_xxxxxxxxxx"
}
```

## Operational Guidance
- Always set `stream: true`; it is disabled by default for compatibility with OpenAI tooling.
- Increase request timeouts (e.g., 1 hour) for reasoning models that stream extended thinking output.
- When using tool calls, the function payload is delivered in a single chunk rather than partial deltas.
- Implement `[DONE]` handling and close the response stream to release sockets cleanly.

## Supported Models
- **grok-4** – flagship reasoning model with rich thinking traces
- **grok-4-0709** – checkpoint variant with tuned thinking cadence
- **grok-code-fast-1** – high-throughput code generation model ($0.20 / 1M input, $1.50 / 1M output)

*Last reviewed: September 15, 2025*
