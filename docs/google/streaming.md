# Streaming Responses

## Overview
Google's Gemini platform streams token output for both the direct Gemini API and the Vertex AI OpenAI-compatible interface. Streaming works across text, vision, and multimodal tasks so UI surfaces can render partial answers immediately while models continue reasoning.

### Official Resources
- [Gemini API documentation](https://ai.google.dev/docs)
- [Vertex AI documentation](https://cloud.google.com/vertex-ai/docs)
- [Chrome AI streaming guide](https://developer.chrome.com/docs/ai/streaming)
- [AI Studio](https://aistudio.google.com/)

### Key Capabilities
- **Universal streaming** – available for Gemini 1.5 and 2.x families
- **Multimodal coverage** – text, image understanding, and video frames
- **High token budgets** – up to 65K streamed output tokens, depending on the model
- **OpenAI compatibility** – Vertex AI exposes a Chat Completions-compatible API
- **Server-Sent Events** – standard SSE transport for predictable client integration

## Streaming Endpoints

### Gemini API quick start

```bash
curl "https://generativelanguage.googleapis.com/v1beta/models/gemini-1.5-pro:generateContent?alt=sse&stream=true" \
  -H "Content-Type: application/json" \
  -H "x-goog-api-key: $GEMINI_API_KEY" \
  -d '{
    "contents": [{
      "parts": [{
        "text": "Explain quantum computing"
      }]
    }]
  }'
```

### Python SDK helper

```python
import google.generativeai as genai


def stream_gemini_response(prompt: str, model_name: str = "gemini-1.5-pro") -> None:
    genai.configure(api_key="your-api-key")

    model = genai.GenerativeModel(model_name)
    response = model.generate_content(
        prompt,
        stream=True,
        generation_config=genai.types.GenerationConfig(
            temperature=0.7,
            max_output_tokens=1024,
        ),
    )

    for chunk in response:
        if chunk.text:
            print(chunk.text, end="", flush=True)

    print()


stream_gemini_response("Write a short story about AI")
```

### Vertex AI with the OpenAI SDK

```python
from google.auth import default
import google.auth.transport.requests
import openai


credentials, project_id = default(scopes=["https://www.googleapis.com/auth/cloud-platform"])
credentials.refresh(google.auth.transport.requests.Request())

client = openai.OpenAI(
    base_url=f"https://us-central1-aiplatform.googleapis.com/v1/projects/{project_id}/locations/us-central1/endpoints/openapi",
    api_key=credentials.token,
)


def stream_vertex_response(messages, model="google/gemini-2.0-flash-001"):
    stream = client.chat.completions.create(
        model=model,
        messages=messages,
        stream=True,
        temperature=0.7,
    )

    for chunk in stream:
        if chunk.choices[0].delta.content:
            print(chunk.choices[0].delta.content, end="", flush=True)

    print()


messages = [{"role": "user", "content": "Explain machine learning"}]
stream_vertex_response(messages)
```

### JavaScript helper

```javascript
import { GoogleGenerativeAI } from "@google/generative-ai";

const genAI = new GoogleGenerativeAI(process.env.GEMINI_API_KEY);

export async function streamGeminiResponse(prompt, modelName = "gemini-1.5-pro") {
  const model = genAI.getGenerativeModel({ model: modelName });
  const result = await model.generateContentStream(prompt);

  for await (const chunk of result.stream) {
    const chunkText = chunk.text();
    if (chunkText) {
      process.stdout.write(chunkText);
    }
  }

  process.stdout.write("\n");
}
```

### Raw HTTP streaming

```python
import json
import requests


def stream_gemini_raw(prompt: str, api_key: str) -> None:
    url = "https://generativelanguage.googleapis.com/v1beta/models/gemini-1.5-pro:generateContent"
    headers = {
        "Content-Type": "application/json",
        "x-goog-api-key": api_key,
    }
    data = {
        "contents": [{"parts": [{"text": prompt}]}],
        "generationConfig": {
            "temperature": 0.7,
            "maxOutputTokens": 1024,
        },
    }

    with requests.post(url, headers=headers, json=data, stream=True) as response:
        response.raise_for_status()
        for line in response.iter_lines():
            if not line:
                continue
            payload = line.decode("utf-8")
            if payload.startswith("data: "):
                body = payload[6:]
                if body.strip() == "[DONE]":
                    break
                chunk = json.loads(body)
                for candidate in chunk.get("candidates", []):
                    for part in candidate.get("content", {}).get("parts", []):
                        text = part.get("text")
                        if text:
                            print(text, end="", flush=True)

    print()
```

## Practical Guidance
- Enforce generous HTTP timeouts for long reasoning runs, especially when streaming via Vertex AI.
- Handle partial multimodal payloads: image annotations might arrive after text chunks.
- Track usage metadata from `usageMetadata` events to monitor token consumption in real time.
- Implement exponential backoff when retrying after quota or network errors.

## Supported Models
- **gemini-1.5-pro** – general purpose, multimodal
- **gemini-1.5-flash** – fast, cost-optimized with streaming support
- **gemini-2.0-flash-001** – next-generation flash model surfaced through Vertex AI Chat Completions
- **gemini-2.0-pro-exp** – experimental reasoning-heavy surface (where available)

*Last reviewed: September 15, 2025*
