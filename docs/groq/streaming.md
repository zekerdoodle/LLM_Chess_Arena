# Streaming Responses

## Overview
Groq's OpenAI-compatible API streams chat completion chunks with ultra-low latency, making it ideal for real-time assistants and high-frequency agents. All Groq-hosted models that emit text support streaming via Server-Sent Events.

### Official Resources
- [API reference](https://console.groq.com/docs/api-reference)
- [Groq playground](https://console.groq.com/playground)
- [API keys](https://console.groq.com/keys)

## Quick Start

```bash
curl https://api.groq.com/openai/v1/chat/completions \
  -H "Content-Type: application/json" \
  -H "Authorization: Bearer $GROQ_API_KEY" \
  -d '{
    "model": "llama-3.3-70b-versatile",
    "messages": [{"role": "user", "content": "Explain streaming"}],
    "stream": true
  }'
```

## Python helper

```python
import json
import requests


def stream_groq_response(messages, api_key: str, model: str = "llama-3.3-70b-versatile"):
    url = "https://api.groq.com/openai/v1/chat/completions"
    headers = {
        "Authorization": f"Bearer {api_key}",
        "Content-Type": "application/json",
    }
    data = {
        "model": model,
        "messages": messages,
        "stream": True,
        "temperature": 0.7,
        "max_tokens": 1024,
    }

    with requests.post(url, headers=headers, json=data, stream=True) as response:
        response.raise_for_status()
        for line in response.iter_lines():
            if not line:
                continue
            payload = line.decode("utf-8")
            if not payload.startswith("data: "):
                continue
            body = payload[6:]
            if body.strip() == "[DONE]":
                break

            chunk = json.loads(body)
            delta = chunk["choices"][0]["delta"].get("content")
            if delta:
                yield delta
```

## JavaScript helper

```javascript
export async function streamGroqResponse(messages, apiKey, model = "llama-3.3-70b-versatile") {
  const response = await fetch("https://api.groq.com/openai/v1/chat/completions", {
    method: "POST",
    headers: {
      Authorization: `Bearer ${apiKey}`,
      "Content-Type": "application/json",
    },
    body: JSON.stringify({
      model,
      messages,
      stream: true,
      temperature: 0.7,
      max_tokens: 1024,
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

## Key Parameters
- `model` – e.g. `llama-3.3-70b-versatile`, `llama-3.1-8b-instant`
- `messages` – standard OpenAI-style message list
- `stream` – set to `true` for streaming chunks
- `max_tokens` – optional completion cap
- `temperature`, `top_p`, `frequency_penalty`, `presence_penalty`, `stop` – optional sampling controls
- `stream_options.include_usage` – include usage counters in streaming payloads

## Chunk Structure

```json
{
  "id": "chatcmpl-f51b2cd2-bef7-417e-964e-a08f0b513c22",
  "object": "chat.completion.chunk",
  "created": 1730241104,
  "model": "llama-3.3-70b-versatile",
  "choices": [
    {
      "index": 0,
      "delta": {
        "content": "Hello",
        "role": "assistant"
      },
      "finish_reason": null
    }
  ],
  "usage": {
    "prompt_tokens": 18,
    "completion_tokens": 1,
    "total_tokens": 19
  },
  "system_fingerprint": "fp_179b0f92c9"
}
```

## Operational Tips
- Raise HTTP timeouts for reasoning models that stream extended thinking tokens.
- Inspect `usage` deltas if you enable `stream_options.include_usage` to track billing.
- Backpressure readers that update UI elements to avoid locking the fetch reader.
- Handle `[DONE]` sentinel to close downstream processors cleanly.

*Last reviewed: September 15, 2025*
