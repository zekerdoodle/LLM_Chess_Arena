Reasoning

grok-4
,
grok-3-mini
, and
grok-3-mini-fast
are reasoning-only models.

presencePenalty
,
frequencyPenalty
and
stop
parameters are not supported by reasoning models. Adding them in the request would result in error.
Key Features

    Think Before Responding: Thinks through problems step-by-step before delivering an answer.
    Math & Quantitative Strength: Excels at numerical challenges and logic puzzles.
    Reasoning Trace: The model's thoughts are available via the
    reasoning_content
    field in the response completion object (see example below).

You can access the model's raw thinking trace via the
message.reasoning_content
of the chat completion response.
grok-4
does not return
reasoning_content
Control how hard the model thinks

reasoning_effort
is not supported by
grok-4
. Specifying
reasoning_effort
parameter will get an error response.

The
reasoning_effort
parameter controls how much time the model spends thinking before responding. It must be set to one of these values:

    low
    : Minimal thinking time, using fewer tokens for quick responses.
    high
    : Maximum thinking time, leveraging more tokens for complex problems.

Choosing the right level depends on your task: use
low
for simple queries that should complete quickly, and
high
for harder problems where response latency is less important.
Usage Example

Here’s a simple example using
grok-4
to multiply 101 by 3. Notice that we can access both the reasoning content and final response.

import os

from xai_sdk import Client
from xai_sdk.chat import system, user

client = Client(
    api_key=os.getenv("XAI_API_KEY"),
    timeout=3600,  # Override default timeout with longer timeout for reasoning models
)

chat = client.chat.create(
    model="grok-4",
    messages=[system("You are a highly intelligent AI assistant.")],
)
chat.append(user("What is 101*3?"))

response = chat.sample()

print("Reasoning Content:")
print(response.reasoning_content)

print("Final Response:")
print(response.content)

print("Number of completion tokens:")
print(response.usage.completion_tokens)

print("Number of reasoning tokens:")
print(response.usage.reasoning_tokens)

Sample Output

output

Reasoning Content:

Final Response:
The result of 101 multiplied by 3 is 303.

Number of completion tokens:
14

Number of reasoning tokens:
310

Notes on Consumption

When you use a reasoning model, the reasoning tokens are also added to your final consumption amount. The reasoning token consumption will likely increase when you use a higher
reasoning_effort
setting.