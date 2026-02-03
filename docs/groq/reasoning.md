Reasoning

Reasoning models excel at complex problem-solving tasks that require step-by-step analysis, logical deduction, and structured thinking and solution validation. With Groq inference speed, these types of models can deliver instant reasoning capabilities critical for real-time applications.
Why Speed Matters for Reasoning

Reasoning models are capable of complex decision making with explicit reasoning chains that are part of the token output and used for decision-making, which make low-latency and fast inference essential. Complex problems often require multiple chains of reasoning tokens where each step build on previous results. Low latency compounds benefits across reasoning chains and shaves off minutes of reasoning to a response in seconds.
Supported Models
Model ID	Model
openai/gpt-oss-20b
OpenAI GPT-OSS 20B
openai/gpt-oss-120b
OpenAI GPT-OSS 120B
qwen/qwen3-32b
Qwen 3 32B
deepseek-r1-distill-llama-70b
DeepSeek R1 Distil Llama 70B
Reasoning Format

Groq API supports explicit reasoning formats through the reasoning_format parameter, giving you fine-grained control over how the model's reasoning process is presented. This is particularly valuable for valid JSON outputs, debugging, and understanding the model's decision-making process.

Note: The format defaults to raw or parsed when JSON mode or tool use are enabled as those modes do not support raw. If reasoning is explicitly set to raw with JSON mode or tool use enabled, we will return a 400 error.
Options for Reasoning Format
reasoning_format Options	Description
parsed	Separates reasoning into a dedicated message.reasoning field while keeping the response concise.
raw	Includes reasoning within <think> tags in the main text content.
hidden	Returns only the final answer.
Including Reasoning in the Response

You can also control whether reasoning is included in the response by setting the include_reasoning parameter.
include_reasoning Options	Description
true	Includes the reasoning in a dedicated message.reasoning field. This is the default behavior.
false	Excludes reasoning from the response.

Note: The include_reasoning parameter cannot be used together with reasoning_format. These parameters are mutually exclusive.
Reasoning Effort
Options for Reasoning Effort (Qwen 3 32B)

The reasoning_effort parameter controls the level of effort the model will put into reasoning. This is only supported by Qwen 3 32B.
reasoning_effort Options	Description
none	Disable reasoning. The model will not use any reasoning tokens.
default	Enable reasoning.
Options for Reasoning Effort (GPT-OSS)

The reasoning_effort parameter controls the level of effort the model will put into reasoning. This is only supported by GPT-OSS 20B and GPT-OSS 120B.
reasoning_effort Options	Description
low	Low effort reasoning. The model will use a small number of reasoning tokens.
medium	Medium effort reasoning. The model will use a moderate number of reasoning tokens.
high	High effort reasoning. The model will use a large number of reasoning tokens.
Quick Start

Get started with reasoning models using this basic example that demonstrates how to make a simple API call for complex problem-solving tasks.

import Groq from 'groq-sdk';


const client = new Groq();

const completion = await client.chat.completions.create({

    model: "deepseek-r1-distill-llama-70b",

    messages: [

        {

            role: "user",

            content: "How many r's are in the word strawberry?"

        }

    ],

    temperature: 0.6,

    max_completion_tokens: 1024,

    top_p: 0.95,

    stream: true,

    reasoning_format: "raw"

});


for await (const chunk of completion) {

    process.stdout.write(chunk.choices[0].delta.content || "");

}

Quick Start with Tool Use

This example shows how to combine reasoning models with function calling to create intelligent agents that can perform actions while explaining their thought process.
bash

curl https://api.groq.com//openai/v1/chat/completions -s \
  -H "authorization: bearer $GROQ_API_KEY" \
  -d '{
    "model": "deepseek-r1-distill-llama-70b",
    "messages": [
        {
            "role": "user",
            "content": "What is the weather like in Paris today?"
        }
    ],
    "tools": [
        {
            "type": "function",
            "function": {
                "name": "get_weather",
                "description": "Get current temperature for a given location.",
                "parameters": {
                    "type": "object",
                    "properties": {
                        "location": {
                            "type": "string",
                            "description": "City and country e.g. Bogotá, Colombia"
                        }
                    },
                    "required": [
                        "location"
                    ],
                    "additionalProperties": false
                },
                "strict": true
            }
        }
    ]}'

Recommended Configuration Parameters
Parameter	Default	Range	Description
messages	-	-	Array of message objects. Important: Avoid system prompts - include all instructions in the user message!
temperature	0.6	0.0 - 2.0	Controls randomness in responses. Lower values make responses more deterministic. Recommended range: 0.5-0.7 to prevent repetitions or incoherent outputs
max_completion_tokens	1024	-	Maximum length of model's response. Default may be too low for complex reasoning - consider increasing for detailed step-by-step solutions
top_p	0.95	0.0 - 1.0	Controls diversity of token selection
stream	false	boolean	Enables response streaming. Recommended for interactive reasoning tasks
stop	null	string/array	Custom stop sequences
seed	null	integer	Set for reproducible results. Important for benchmarking - run multiple tests with different seeds
response_format	{type: "text"}	{type: "json_object"} or {type: "text"}	Set to json_object type for structured output.
reasoning_format	raw	"parsed", "raw", "hidden"	Controls how model reasoning is presented in the response. Must be set to either parsed or hidden when using tool calling or JSON mode.
reasoning_effort	default	"none", "default", "low", "medium", "high"	Controls the level of effort the model will put into reasoning. none and default are only supported by Qwen 3 32B. low, medium, and high are only supported by GPT-OSS 20B and GPT-OSS 120B.
Accessing Reasoning Content

Accessing the reasoning content in the response is dependent on the model and the reasoning format you are using. See the examples below for more details and refer to the Reasoning Format section for more information.
Non-GPT-OSS Models

When using raw reasoning format, the reasoning content is accessible in the main text content of assistant responses within <think> tags. This example demonstrates making a request with reasoning_format set to raw to see the model's internal thinking process alongside the final answer.

import { Groq } from 'groq-sdk';


const groq = new Groq();


const chatCompletion = await groq.chat.completions.create({

  "messages": [

    {

      "role": "user",

      "content": "How do airplanes fly? Be concise."

    }

  ],

  "model": "qwen/qwen3-32b",

  "stream": false,

  "reasoning_format": "raw"

});


console.log(chatCompletion.choices[0].message);

GPT-OSS Models

With openai/gpt-oss-20b and openai/gpt-oss-120b, the reasoning_format parameter is not supported. By default, these models will include reasoning content in the reasoning field of the assistant response. You can also control whether reasoning is included in the response by setting the include_reasoning parameter.

import { Groq } from 'groq-sdk';


const groq = new Groq();


const chatCompletion = await groq.chat.completions.create({

  "messages": [

    {

      "role": "user",

      "content": "How do airplanes fly? Be concise."

    }

  ],

  "model": "openai/gpt-oss-20b",

  "stream": false,

  "include_reasoning": false

});


console.log(chatCompletion.choices[0].message);

Optimizing Performance
Temperature and Token Management

The model performs best with temperature settings between 0.5-0.7, with lower values (closer to 0.5) producing more consistent mathematical proofs and higher values allowing for more creative problem-solving approaches. Monitor and adjust your token usage based on the complexity of your reasoning tasks - while the default max_completion_tokens is 1024, complex proofs may require higher limits.
Prompt Engineering

To ensure accurate, step-by-step reasoning while maintaining high performance:

    DeepSeek-R1 works best when all instructions are included directly in user messages rather than system prompts.
    Structure your prompts to request explicit validation steps and intermediate calculations.
    Avoid few-shot prompting and go for zero-shot prompting only.
