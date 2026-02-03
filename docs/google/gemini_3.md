<br />

Gemini 3 is our most intelligent model family to date, built on a foundation of state-of-the-art reasoning. It is designed to bring any idea to life by mastering agentic workflows, autonomous coding, and complex multimodal tasks. This guide covers key features of the Gemini 3 model family and how to get the most out of it.

High/Dynamic ThinkingLow Thinking

Gemini 3 Pro uses dynamic thinking by default to reason through prompts. For faster, lower-latency responses when complex reasoning isn't required, you can constrain the model's thinking level to`low`.  

### Python

    from google import genai
    from google.genai import types

    client = genai.Client()

    response = client.models.generate_content(
        model="gemini-3-pro-preview",
        contents="Find the race condition in this multi-threaded C++ snippet: [code here]",
    )

    print(response.text)

### JavaScript

    import { GoogleGenAI } from "@google/genai";

    const ai = new GoogleGenAI({});

    async function run() {
      const response = await ai.models.generateContent({
        model: "gemini-3-pro-preview",
        contents="Find the race condition in this multi-threaded C++ snippet: [code here]",
      });

      console.log(response.text);
    }

    run();

### REST

    curl "https://generativelanguage.googleapis.com/v1beta/models/gemini-3-pro-preview:generateContent" \
      -H "x-goog-api-key: $GEMINI_API_KEY" \
      -H 'Content-Type: application/json' \
      -X POST \
      -d '{
        "contents": [{
          "parts": [{"text": "Find the race condition in this multi-threaded C++ snippet: [code here]"}]
        }]
      }'

## Explore

![Gemini 3 Applets Overview](https://ai.google.dev/static/gemini-api/docs/images/gemini-3-applet-overview.png)

Explore our[collection of Gemini 3 apps](https://aistudio.google.com/app/apps?source=showcase&showcaseTag=gemini-3)to see how the model handles advanced reasoning, autonomous coding, and complex multimodal tasks.

## Meet Gemini 3

Gemini 3 Pro is the first model in the new series.`gemini-3-pro-preview`is best for your complex tasks that require broad world knowledge and advanced reasoning across modalities.

|         Model ID         | Context Window (In / Out) | Knowledge Cutoff |            Pricing (Input / Output)\*             |
|--------------------------|---------------------------|------------------|---------------------------------------------------|
| **gemini-3-pro-preview** | 1M / 64k                  | Jan 2025         | $2 / $12 (\<200k tokens) $4 / $18 (\>200k tokens) |

*\* Pricing is per 1 million tokens. Prices listed are for standard text; multimodal input rates may vary.*

For detailed rate limits, batch pricing, and additional information, see the[models page](https://ai.google.dev/gemini-api/docs/models/gemini).

## New API features in Gemini 3

Gemini 3 introduces new parameters designed to give developers more control over latency, cost, and multimodal fidelity.

### Thinking level

The`thinking_level`parameter controls the**maximum** depth of the model's internal reasoning process before it produces a response. Gemini 3 treats these levels as relative allowances for thinking rather than strict token guarantees. If`thinking_level`is not specified, Gemini 3 Pro will default to`high`.

- `low`: Minimizes latency and cost. Best for simple instruction following, chat, or high-throughput applications
- `medium`: (Coming soon), not supported at launch
- `high`(Default): Maximizes reasoning depth. The model may take significantly longer to reach a first token, but the output will be more carefully reasoned.

| **Warning:** You cannot use both`thinking_level`and the legacy`thinking_budget`parameter in the same request. Doing so will return a 400 error.

### Media resolution

Gemini 3 introduces granular control over multimodal vision processing via the`media_resolution`parameter. Higher resolutions improve the model's ability to read fine text or identify small details, but increase token usage and latency. The`media_resolution`parameter determines the**maximum number of tokens allocated per input image or video frame.**

You can now set the resolution to`media_resolution_low`,`media_resolution_medium`, or`media_resolution_high`per individual media part or globally (via`generation_config`). If unspecified, the model uses optimal defaults based on the media type.

**Recommended settings**

|      Media Type       |                 Recommended Setting                 |   Max Tokens    |                                                                                  Usage Guidance                                                                                   |
|-----------------------|-----------------------------------------------------|-----------------|-----------------------------------------------------------------------------------------------------------------------------------------------------------------------------------|
| **Images**            | `media_resolution_high`                             | 1120            | Recommended for most image analysis tasks to ensure maximum quality.                                                                                                              |
| **PDFs**              | `media_resolution_medium`                           | 560             | Optimal for document understanding; quality typically saturates at`medium`. Increasing to`high`rarely improves OCR results for standard documents.                                |
| **Video**(General)    | `media_resolution_low`(or`media_resolution_medium`) | 70 (per frame)  | **Note:** For video,`low`and`medium`settings are treated identically (70 tokens) to optimize context usage. This is sufficient for most action recognition and description tasks. |
| **Video**(Text-heavy) | `media_resolution_high`                             | 280 (per frame) | Required only when the use case involves reading dense text (OCR) or small details within video frames.                                                                           |

**Note:** The`media_resolution`parameter maps to different token counts depending on the input type. While images scale linearly (`media_resolution_low`: 280,`media_resolution_medium`: 560,`media_resolution_high`: 1120), Video is compressed more aggressively. For Video, both`media_resolution_low`and`media_resolution_medium`are capped at 70 tokens per frame, and`media_resolution_high`is capped at 280 tokens. See full details[here](https://ai.google.dev/gemini-api/docs/media-resolution#token-counts)  

### Python

    from google import genai
    from google.genai import types
    import base64

    # The media_resolution parameter is currently only available in the v1alpha API version.
    client = genai.Client(http_options={'api_version': 'v1alpha'})

    response = client.models.generate_content(
        model="gemini-3-pro-preview",
        contents=[
            types.Content(
                parts=[
                    types.Part(text="What is in this image?"),
                    types.Part(
                        inline_data=types.Blob(
                            mime_type="image/jpeg",
                            data=base64.b64decode("..."),
                        ),
                        media_resolution={"level": "media_resolution_high"}
                    )
                ]
            )
        ]
    )

    print(response.text)

### JavaScript

    import { GoogleGenAI } from "@google/genai";

    // The media_resolution parameter is currently only available in the v1alpha API version.
    const ai = new GoogleGenAI({ apiVersion: "v1alpha" });

    async function run() {
      const response = await ai.models.generateContent({
        model: "gemini-3-pro-preview",
        contents: [
          {
            parts: [
              { text: "What is in this image?" },
              {
                inlineData: {
                  mimeType: "image/jpeg",
                  data: "...",
                },
                mediaResolution: {
                  level: "media_resolution_high"
                }
              }
            ]
          }
        ]
      });

      console.log(response.text);
    }

    run();

### REST

    curl "https://generativelanguage.googleapis.com/v1alpha/models/gemini-3-pro-preview:generateContent" \
      -H "x-goog-api-key: $GEMINI_API_KEY" \
      -H 'Content-Type: application/json' \
      -X POST \
      -d '{
        "contents": [{
          "parts": [
            { "text": "What is in this image?" },
            {
              "inlineData": {
                "mimeType": "image/jpeg",
                "data": "..."
              },
              "mediaResolution": {
                "level": "media_resolution_high"
              }
            }
          ]
        }]
      }'

### Temperature

For Gemini 3, we strongly recommend keeping the temperature parameter at its default value of`1.0`.

While previous models often benefited from tuning temperature to control creativity versus determinism, Gemini 3's reasoning capabilities are optimized for the default setting. Changing the temperature (setting it below 1.0) may lead to unexpected behavior, such as looping or degraded performance, particularly in complex mathematical or reasoning tasks.

### Thought signatures

Gemini 3 uses[Thought signatures](https://ai.google.dev/gemini-api/docs/thought-signatures)to maintain reasoning context across API calls. These signatures are encrypted representations of the model's internal thought process. To ensure the model maintains its reasoning capabilities you must return these signatures back to the model in your request exactly as they were received:

- **Function Calling (Strict):**The API enforces strict validation on the "Current Turn". Missing signatures will result in a 400 error.
- **Text/Chat:**Validation is not strictly enforced, but omitting signatures will degrade the model's reasoning and answer quality.

| **Success:** If you use the[official SDKs (Python, Node, Java)](https://ai.google.dev/gemini-api/docs/function-calling?example=meeting#thinking)and standard chat history, Thought Signatures are handled automatically. You do not need to manually manage these fields.

#### Function calling (strict validation)

When Gemini generates a`functionCall`, it relies on the`thoughtSignature`to process the tool's output correctly in the next turn. The "Current Turn" includes all Model (`functionCall`) and User (`functionResponse`) steps that occurred since the last standard**User** `text`message.

- **Single Function Call:** The`functionCall`part contains a signature. You must return it.
- **Parallel Function Calls:** Only the first`functionCall`part in the list will contain the signature. You must return the parts in the exact order received.
- **Multi-Step (Sequential):** If the model calls a tool, receives a result, and calls*another* tool (within the same turn),**both** function calls have signatures. You must return**all**accumulated signatures in the history.

#### Text and streaming

For standard chat or text generation, the presence of a signature is not guaranteed.

- **Non-Streaming** : The final content part of the response may contain a`thoughtSignature`, though it is not always present. If one is returned, you should send it back to maintain best performance.
- **Streaming**: If a signature is generated, it may arrive in a final chunk that contains an empty text part. Ensure your stream parser checks for signatures even if the text field is empty.

#### Code examples

#### Multi-step Function Calling (Sequential)

The user asks a question requiring two separate steps (Check Flight -\> Book Taxi) in one turn.  

**Step 1: Model calls Flight Tool.**   
The model returns a signature`<Sig_A>`  

```java
// Model Response (Turn 1, Step 1)
  {
    "role": "model",
    "parts": [
      {
        "functionCall": { "name": "check_flight", "args": {...} },
        "thoughtSignature": "<Sig_A>" // SAVE THIS
      }
    ]
  }
```

**Step 2: User sends Flight Result**   
We must send back`<Sig_A>`to keep the model's train of thought.  

```java
// User Request (Turn 1, Step 2)
[
  { "role": "user", "parts": [{ "text": "Check flight AA100..." }] },
  { 
    "role": "model", 
    "parts": [
      { 
        "functionCall": { "name": "check_flight", "args": {...} }, 
        "thoughtSignature": "<Sig_A>" // REQUIRED
      } 
    ]
  },
  { "role": "user", "parts": [{ "functionResponse": { "name": "check_flight", "response": {...} } }] }
]
```

**Step 3: Model calls Taxi Tool**   
The model remembers the flight delay via`<Sig_A>`and now decides to book a taxi. It generates a*new* signature`<Sig_B>`.  

```java
// Model Response (Turn 1, Step 3)
{
  "role": "model",
  "parts": [
    {
      "functionCall": { "name": "book_taxi", "args": {...} },
      "thoughtSignature": "<Sig_B>" // SAVE THIS
    }
  ]
}
```

**Step 4: User sends Taxi Result**   
To complete the turn, you must send back the entire chain:`<Sig_A>`AND`<Sig_B>`.  

```java
// User Request (Turn 1, Step 4)
[
  // ... previous history ...
  { 
    "role": "model", 
    "parts": [
       { "functionCall": { "name": "check_flight", ... }, "thoughtSignature": "<Sig_A>" } 
    ]
  },
  { "role": "user", "parts": [{ "functionResponse": {...} }] },
  { 
    "role": "model", 
    "parts": [
       { "functionCall": { "name": "book_taxi", ... }, "thoughtSignature": "<Sig_B>" } 
    ]
  },
  { "role": "user", "parts": [{ "functionResponse": {...} }] }
]
```  

#### Parallel Function Calling

The user asks: "Check the weather in Paris and London." The model returns two function calls in one response.  

```java
// User Request (Sending Parallel Results)
[
  {
    "role": "user",
    "parts": [
      { "text": "Check the weather in Paris and London." }
    ]
  },
  {
    "role": "model",
    "parts": [
      // 1. First Function Call has the signature
      {
        "functionCall": { "name": "check_weather", "args": { "city": "Paris" } },
        "thoughtSignature": "<Signature_A>" 
      },
      // 2. Subsequent parallel calls DO NOT have signatures
      {
        "functionCall": { "name": "check_weather", "args": { "city": "London" } }
      } 
    ]
  },
  {
    "role": "user",
    "parts": [
      // 3. Function Responses are grouped together in the next block
      {
        "functionResponse": { "name": "check_weather", "response": { "temp": "15C" } }
      },
      {
        "functionResponse": { "name": "check_weather", "response": { "temp": "12C" } }
      }
    ]
  }
]
```  

#### Text/In-Context Reasoning (No Validation)

The user asks a question that requires in-context reasoning without external tools. While not strictly validated, including the signature helps the model maintain the reasoning chain for follow-up questions.  

```java
// User Request (Follow-up question)
[
  { 
    "role": "user", 
    "parts": [{ "text": "What are the risks of this investment?" }] 
  },
  { 
    "role": "model", 
    "parts": [
      {
        "text": "I need to calculate the risk step-by-step. First, I'll look at volatility...",
        "thoughtSignature": "<Signature_C>" // Recommended to include
      }
    ]
  },
  { 
    "role": "user", 
    "parts": [{ "text": "Summarize that in one sentence." }] 
  }
]
```

#### Migrating from other models

If you are transferring a conversation trace from another model (e.g., Gemini 2.5) or injecting a custom function call that was not generated by Gemini 3, you will not have a valid signature.

To bypass strict validation in these specific scenarios, populate the field with this specific dummy string:`"thoughtSignature": "context_engineering_is_the_way_to_go"`

### Structured Outputs with tools

Gemini 3 allows you to combine[Structured Outputs](https://ai.google.dev/gemini-api/docs/structured-output)with built-in tools, including[Grounding with Google Search](https://ai.google.dev/gemini-api/docs/google-search),[URL Context](https://ai.google.dev/gemini-api/docs/url-context), and[Code Execution](https://ai.google.dev/gemini-api/docs/code-execution).  

### Python

    from google import genai
    from google.genai import types
    from pydantic import BaseModel, Field
    from typing import List

    class MatchResult(BaseModel):
        winner: str = Field(description="The name of the winner.")
        final_match_score: str = Field(description="The final match score.")
        scorers: List[str] = Field(description="The name of the scorer.")

    client = genai.Client()

    response = client.models.generate_content(
        model="gemini-3-pro-preview",
        contents="Search for all details for the latest Euro.",
        config={
            "tools": [
                {"google_search": {}},
                {"url_context": {}}
            ],
            "response_mime_type": "application/json",
            "response_json_schema": MatchResult.model_json_schema(),
        },  
    )

    result = MatchResult.model_validate_json(response.text)
    print(result)

### JavaScript

    import { GoogleGenAI } from "@google/genai";
    import { z } from "zod";
    import { zodToJsonSchema } from "zod-to-json-schema";

    const ai = new GoogleGenAI({});

    const matchSchema = z.object({
      winner: z.string().describe("The name of the winner."),
      final_match_score: z.string().describe("The final score."),
      scorers: z.array(z.string()).describe("The name of the scorer.")
    });

    async function run() {
      const response = await ai.models.generateContent({
        model: "gemini-3-pro-preview",
        contents: "Search for all details for the latest Euro.",
        config: {
          tools: [
            { googleSearch: {} },
            { urlContext: {} }
          ],
          responseMimeType: "application/json",
          responseJsonSchema: zodToJsonSchema(matchSchema),
        },
      });

      const match = matchSchema.parse(JSON.parse(response.text));
      console.log(match);
    }

    run();

### REST

    curl "https://generativelanguage.googleapis.com/v1beta/models/gemini-3-pro-preview:generateContent" \
      -H "x-goog-api-key: $GEMINI_API_KEY" \
      -H 'Content-Type: application/json' \
      -X POST \
      -d '{
        "contents": [{
          "parts": [{"text": "Search for all details for the latest Euro."}]
        }],
        "tools": [
          {"googleSearch": {}},
          {"urlContext": {}}
        ],
        "generationConfig": {
            "responseMimeType": "application/json",
            "responseJsonSchema": {
                "type": "object",
                "properties": {
                    "winner": {"type": "string", "description": "The name of the winner."},
                    "final_match_score": {"type": "string", "description": "The final score."},
                    "scorers": {
                        "type": "array",
                        "items": {"type": "string"},
                        "description": "The name of the scorer."
                    }
                },
                "required": ["winner", "final_match_score", "scorers"]
            }
        }
      }'

## Migrating from Gemini 2.5

Gemini 3 is our most capable model family to date and offers a stepwise improvement over Gemini 2.5 Pro. When migrating, consider the following:

- **Thinking:** If you were previously using complex prompt engineering (like Chain-of-thought) to force Gemini 2.5 to reason, try Gemini 3 with`thinking_level: "high"`and simplified prompts.
- **Temperature settings:**If your existing code explicitly sets temperature (especially to low values for deterministic outputs), we recommend removing this parameter and using the Gemini 3 default of 1.0 to avoid potential looping issues or performance degradation on complex tasks.
- **PDF \& document understanding:** Default OCR resolution for PDFs has changed. If you relied on specific behavior for dense document parsing, test the new`media_resolution_high`setting to ensure continued accuracy.
- **Token consumption:** Migrating to Gemini 3 Pro defaults may**increase** token usage for PDFs but**decrease**token usage for video. If requests now exceed the context window due to higher default resolutions, we recommend explicitly reducing the media resolution.
- **Image segmentation:** Image segmentation capabilities (returning pixel-level masks for objects) are not supported in Gemini 3 Pro. For workloads requiring native image segmentation, we recommend continuing to utilize Gemini 2.5 Flash with thinking turned off or[Gemini Robotics-ER 1.5](https://ai.google.dev/gemini-api/docs/robotics-overview).

## OpenAI compatibility

For users utilizing the OpenAI compatibility layer, standard parameters are automatically mapped to Gemini equivalents:

- `reasoning_effort`(OAI) maps to`thinking_level`(Gemini). Note that`reasoning_effort`medium maps to`thinking_level`high.

## Prompting best practices

Gemini 3 is a reasoning model, which changes how you should prompt.

- **Precise instructions:**Be concise in your input prompts. Gemini 3 responds best to direct, clear instructions. It may over-analyze verbose or overly complex prompt engineering techniques used for older models.
- **Output verbosity:**By default, Gemini 3 is less verbose and prefers providing direct, efficient answers. If your use case requires a more conversational or "chatty" persona, you must explicitly steer the model in the prompt (e.g., "Explain this as a friendly, talkative assistant").
- **Context management:**When working with large datasets (e.g., entire books, codebases, or long videos), place your specific instructions or questions at the end of the prompt, after the data context. Anchor the model's reasoning to the provided data by starting your question with a phrase like, "Based on the information above...".

Learn more about prompt design strategies in the[prompt engineering guide](https://ai.google.dev/gemini-api/docs/prompting-strategies).

## FAQ

1. **What is the knowledge cutoff for Gemini 3 Pro?** Gemini 3 has a knowledge cutoff of January 2025. For more recent information, use the[Search Grounding](https://ai.google.dev/gemini-api/docs/google-search)tool.

2. **What are the context window limits?**Gemini 3 Pro supports a 1 million token input context window and up to 64k tokens of output.

3. **Is there a free tier for Gemini 3 Pro?** You can try the model for free in Google AI Studio, but currently, there is no free tier available for`gemini-3-pro-preview`in the Gemini API.

4. **Will my old`thinking_budget`code still work?** Yes,`thinking_budget`is still supported for backward compatibility, but we recommend migrating to`thinking_level`for more predictable performance. Do not use both in the same request.

5. **Does Gemini 3 support the Batch API?** Yes, Gemini 3 supports the[Batch API.](https://ai.google.dev/gemini-api/docs/batch-api)

6. **Is Context Caching supported?** Yes,[Context Caching](https://ai.google.dev/gemini-api/docs/caching?lang=python)is supported for Gemini 3. The minimum token count required to initiate caching is 2,048 tokens.

7. **Which tools are supported in Gemini 3?** Gemini 3 supports[Google Search](https://ai.google.dev/gemini-api/docs/google-search),[File Search](https://ai.google.dev/gemini-api/docs/file-search),[Code Execution](https://ai.google.dev/gemini-api/docs/code-execution), and[URL Context](https://ai.google.dev/gemini-api/docs/url-context). It also supports standard[Function Calling](https://ai.google.dev/gemini-api/docs/function-calling?example=meeting)for your own custom tools. Please note that[Google Maps](https://ai.google.dev/gemini-api/docs/maps-grounding)and[Computer Use](https://ai.google.dev/gemini-api/docs/computer-use)are currently not supported.

## Next steps

- Get started with the[Gemini 3 Cookbook](https://colab.research.google.com/github/google-gemini/cookbook/blob/main/quickstarts/Get_started.ipynb#templateParams=%7B%22MODEL_ID%22%3A+%22gemini-3-pro-preview%22%7D)
- Check the dedicated Cookbook guide on[thinking levels](https://colab.research.google.com/github/google-gemini/cookbook/blob/main/quickstarts/Get_started_thinking_REST.ipynb#gemini3)and how to migrate from thinking budget to thinking levels.