Introduction to Tool Use

Tool use is a powerful feature that allows Large Language Models (LLMs) to interact with external resources, such as APIs, databases, and the web, to gather dynamic data they wouldn't otherwise have access to in their pre-trained (or static) state and perform actions beyond simple text generation.

Tool use bridges the gap between the data that the LLMs were trained on with dynamic data and real-world actions, which opens up a wide array of realtime use cases for us to build powerful applications with, especially with Groq's insanely fast inference speed. 🚀
Supported Models
Model ID	Tool Use Support?	Parallel Tool Use Support?	JSON Mode Support?
moonshotai/kimi-k2-instruct
Yes	Yes	Yes
openai/gpt-oss-20b
Yes	No	Yes
openai/gpt-oss-120b
Yes	No	Yes
meta-llama/llama-4-scout-17b-16e-instruct
Yes	Yes	Yes
meta-llama/llama-4-maverick-17b-128e-instruct
Yes	Yes	Yes
deepseek-r1-distill-llama-70b
Yes	Yes	Yes
llama-3.3-70b-versatile
Yes	Yes	Yes
llama-3.1-8b-instant
Yes	Yes	Yes
Agentic Tooling

In addition to the models that support custom tools above, Groq also offers agentic tool systems. These are AI systems with tools like web search and code execution built directly into the system. You don't need to specify any tools yourself - the system will automatically use its built-in tools as needed.

Learn More About Agentic Tooling
Discover how to build powerful applications with real-time web search and code execution
How Tool Use Works

Groq API tool use structure is compatible with OpenAI's tool use structure, which allows for easy integration. See the following cURL example of a tool use request:

bash

curl https://api.groq.com/openai/v1/chat/completions \
-H "Content-Type: application/json" \
-H "Authorization: Bearer $GROQ_API_KEY" \
-d '{
  "model": "llama-3.3-70b-versatile",
  "messages": [
    {
      "role": "user",
      "content": "What'\''s the weather like in Boston today?"
    }
  ],
  "tools": [
    {
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
    }
  ],
  "tool_choice": "auto"
}'


To integrate tools with Groq API, follow these steps:

    Provide tools (or predefined functions) to the LLM for performing actions and accessing external data in real-time in addition to your user prompt within your Groq API request
    Define how the tools should be used to teach the LLM how to use them effectively (e.g. by defining input and output formats)
    Let the LLM autonomously decide whether or not the provided tools are needed for a user query by evaluating the user query, determining whether the tools can enhance its response, and utilizing the tools accordingly
    Extract tool input, execute the tool code, and return results
    Let the LLM use the tool result to formulate a response to the original prompt

This process allows the LLM to perform tasks such as real-time data retrieval, complex calculations, and external API interaction, all while maintaining a natural conversation with our end user.
Tool Use with Groq

Groq API endpoints support tool use to almost instantly deliver structured JSON output that can be used to directly invoke functions from desired external resources.
Tools Specifications

Tool use is part of the Groq API chat completion request payload. Groq API tool calls are structured to be OpenAI-compatible.
Tool Call Structure

The following is an example tool call structure:
JSON

{
  "model": "llama-3.3-70b-versatile",
  "messages": [
    {
      "role": "system",
      "content": "You are a weather assistant. Use the get_weather function to retrieve weather information for a given location."
    },
    {
      "role": "user",
      "content": "What's the weather like in New York today?"
    }
  ],
  "tools": [
    {
      "type": "function",
      "function": {
        "name": "get_weather",
        "description": "Get the current weather for a location",
        "parameters": {
          "type": "object",
          "properties": {
            "location": {
              "type": "string",
              "description": "The city and state, e.g. San Francisco, CA"
            },
            "unit": {
              "type": "string",
              "enum": ["celsius", "fahrenheit"],
              "description": "The unit of temperature to use. Defaults to fahrenheit."
            }
          },
          "required": ["location"]
        }
      }
    }
  ],
  "tool_choice": "auto",
  "max_completion_tokens": 4096
}'

Tool Call Response

The following is an example tool call response based on the above:
JSON

"model": "llama-3.3-70b-versatile",
"choices": [{
    "index": 0,
    "message": {
        "role": "assistant",
        "tool_calls": [{
            "id": "call_d5wg",
            "type": "function",
            "function": {
                "name": "get_weather",
                "arguments": "{\"location\": \"New York, NY\"}"
            }
        }]
    },
    "logprobs": null,
    "finish_reason": "tool_calls"
}],


When a model decides to use a tool, it returns a response with a tool_calls object containing:

    id: a unique identifier for the tool call
    type: the type of tool call, i.e. function
    name: the name of the tool being used
    parameters: an object containing the input being passed to the tool

Setting Up Tools

To get started, let's go through an example of tool use with Groq API that you can use as a base to build more tools on your own.

Step 1: Create Tool

Let's install Groq SDK, set up our Groq client, and create a function called calculate to evaluate a mathematical expression that we will represent as a tool.

Note: In this example, we're defining a function as our tool, but your tool can be any function or an external resource (e.g. dabatase, web search engine, external API).
shell

pip install groq


Python

from groq import Groq

import json


# Initialize the Groq client

client = Groq()

# Specify the model to be used (we recommend Llama 3.3 70B)

MODEL = 'llama-3.3-70b-versatile'


def calculate(expression):

    """Evaluate a mathematical expression"""

    try:

        # Attempt to evaluate the math expression

        result = eval(expression)

        return json.dumps({"result": result})

    except:

        # Return an error message if the math expression is invalid

        return json.dumps({"error": "Invalid expression"})

Step 2: Pass Tool Definition and Messages to Model

Next, we'll define our calculate tool within an array of available tools and call our Groq API chat completion. You can read more about tool schema and supported required and optional fields above in Tool Specifications.

By defining our tool, we'll inform our model about what our tool does and have the model decide whether or not to use the tool. We should be as descriptive and specific as possible for our model to be able to make the correct tool use decisions.

In addition to our tools array, we will provide our messages array (e.g. containing system prompt, assistant prompt, and/or user prompt).
Step 3: Receive and Handle Tool Results

After executing our chat completion, we'll extract our model's response and check for tool calls.

If the model decides that no tools should be used and does not generate a tool or function call, then the response will be a normal chat completion (i.e. response_message = response.choices[0].message) with a direct model reply to the user query.

If the model decides that tools should be used and generates a tool or function call, we will:

    Define available tool or function
    Add the model's response to the conversation by appending our message
    Process the tool call and add the tool response to our message
    Make a second Groq API call with the updated conversation
    Return the final response


Python

# imports calculate function from step 1

def run_conversation(user_prompt):

    # Initialize the conversation with system and user messages

    messages=[

        {

            "role": "system",

            "content": "You are a calculator assistant. Use the calculate function to perform mathematical operations and provide the results."

        },

        {

            "role": "user",

            "content": user_prompt,

        }

    ]

    # Define the available tools (i.e. functions) for our model to use

    tools = [

        {

            "type": "function",

            "function": {

                "name": "calculate",

                "description": "Evaluate a mathematical expression",

                "parameters": {

                    "type": "object",

                    "properties": {

                        "expression": {

                            "type": "string",

                            "description": "The mathematical expression to evaluate",

                        }

                    },

                    "required": ["expression"],

                },

            },

        }

    ]

    # Make the initial API call to Groq

    response = client.chat.completions.create(

        model=MODEL, # LLM to use

        messages=messages, # Conversation history

        stream=False,

        tools=tools, # Available tools (i.e. functions) for our LLM to use

        tool_choice="auto", # Let our LLM decide when to use tools

        max_completion_tokens=4096 # Maximum number of tokens to allow in our response

    )

    # Extract the response and any tool call responses

    response_message = response.choices[0].message

    tool_calls = response_message.tool_calls

    if tool_calls:

        # Define the available tools that can be called by the LLM

        available_functions = {

            "calculate": calculate,

        }

        # Add the LLM's response to the conversation

        messages.append(response_message)


        # Process each tool call

        for tool_call in tool_calls:

            function_name = tool_call.function.name

            function_to_call = available_functions[function_name]

            function_args = json.loads(tool_call.function.arguments)

            # Call the tool and get the response

            function_response = function_to_call(

                expression=function_args.get("expression")

            )

            # Add the tool response to the conversation

            messages.append(

                {

                    "tool_call_id": tool_call.id, 

                    "role": "tool", # Indicates this message is from tool use

                    "name": function_name,

                    "content": function_response,

                }

            )

        # Make a second API call with the updated conversation

        second_response = client.chat.completions.create(

            model=MODEL,

            messages=messages

        )

        # Return the final response

        return second_response.choices[0].message.content

# Example usage

user_prompt = "What is 25 * 4 + 10?"

print(run_conversation(user_prompt))

Parallel Tool Use

We learned about tool use and built single-turn tool use examples above. Now let's take tool use a step further and imagine a workflow where multiple tools can be called simultaneously, enabling more efficient and effective responses.

This concept is known as parallel tool use and is key for building agentic workflows that can deal with complex queries, which is a great example of where inference speed becomes increasingly important (and thankfully we can access fast inference speed with Groq API).

Here's an example of parallel tool use with a tool for getting the temperature and the tool for getting the weather condition to show parallel tool use with Groq API in action:


Python

import json

from groq import Groq

import os


# Initialize Groq client

client = Groq()

model = "llama-3.3-70b-versatile"


# Define weather tools

def get_temperature(location: str):

    # This is a mock tool/function. In a real scenario, you would call a weather API.

    temperatures = {"New York": "22°C", "London": "18°C", "Tokyo": "26°C", "Sydney": "20°C"}

    return temperatures.get(location, "Temperature data not available")


def get_weather_condition(location: str):

    # This is a mock tool/function. In a real scenario, you would call a weather API.

    conditions = {"New York": "Sunny", "London": "Rainy", "Tokyo": "Cloudy", "Sydney": "Clear"}

    return conditions.get(location, "Weather condition data not available")


# Define system messages and tools

messages = [

    {"role": "system", "content": "You are a helpful weather assistant."},

    {"role": "user", "content": "What's the weather and temperature like in New York and London? Respond with one sentence for each city. Use tools to get the information."},

]


tools = [

    {

        "type": "function",

        "function": {

            "name": "get_temperature",

            "description": "Get the temperature for a given location",

            "parameters": {

                "type": "object",

                "properties": {

                    "location": {

                        "type": "string",

                        "description": "The name of the city",

                    }

                },

                "required": ["location"],

            },

        },

    },

    {

        "type": "function",

        "function": {

            "name": "get_weather_condition",

            "description": "Get the weather condition for a given location",

            "parameters": {

                "type": "object",

                "properties": {

                    "location": {

                        "type": "string",

                        "description": "The name of the city",

                    }

                },

                "required": ["location"],

            },

        },

    }

]


# Make the initial request

response = client.chat.completions.create(

    model=model, messages=messages, tools=tools, tool_choice="auto", max_completion_tokens=4096, temperature=0.5

)


response_message = response.choices[0].message

tool_calls = response_message.tool_calls


# Process tool calls

messages.append(response_message)


available_functions = {

    "get_temperature": get_temperature,

    "get_weather_condition": get_weather_condition,

}


for tool_call in tool_calls:

    function_name = tool_call.function.name

    function_to_call = available_functions[function_name]

    function_args = json.loads(tool_call.function.arguments)

    function_response = function_to_call(**function_args)


    messages.append(

        {

            "role": "tool",

            "content": str(function_response),

            "tool_call_id": tool_call.id,

        }

    )


# Make the final request with tool call results

final_response = client.chat.completions.create(

    model=model, messages=messages, tools=tools, tool_choice="auto", max_completion_tokens=4096

)


print(final_response.choices[0].message.content)


Error Handling

Groq API tool use is designed to verify whether a model generates a valid tool call object. When a model fails to generate a valid tool call object, Groq API will return a 400 error with an explanation in the "failed_generation" field of the JSON body that is returned.
Next Steps

For more information and examples of working with multiple tools in parallel using Groq API and Instructor, see our Groq API Cookbook tutorial here.

Tool Use with Structured Outputs (Python)

Groq API offers best-effort matching for parameters, which means the model could occasionally miss parameters or misinterpret types for more complex tool calls. We recommend the Instuctor library to simplify the process of working with structured data and to ensure that the model's output adheres to a predefined schema.

Here's an example of how to implement tool use using the Instructor library with Groq API:

shell

pip install instructor pydantic


Python

import instructor

from pydantic import BaseModel, Field

from groq import Groq


# Define the tool schema

tool_schema = {

    "name": "get_weather_info",

    "description": "Get the weather information for any location.",

    "parameters": {

        "type": "object",

        "properties": {

            "location": {

                "type": "string",

                "description": "The location for which we want to get the weather information (e.g., New York)"

            }

        },

        "required": ["location"]

    }

}


# Define the Pydantic model for the tool call

class ToolCall(BaseModel):

    input_text: str = Field(description="The user's input text")

    tool_name: str = Field(description="The name of the tool to call")

    tool_parameters: str = Field(description="JSON string of tool parameters")


class ResponseModel(BaseModel):

    tool_calls: list[ToolCall]


# Patch Groq() with instructor

client = instructor.from_groq(Groq(), mode=instructor.Mode.JSON)


def run_conversation(user_prompt):

    # Prepare the messages

    messages = [

        {

            "role": "system",

            "content": f"You are an assistant that can use tools. You have access to the following tool: {tool_schema}"

        },

        {

            "role": "user",

            "content": user_prompt,

        }

    ]


    # Make the Groq API call

    response = client.chat.completions.create(

        model="llama-3.3-70b-versatile",

        response_model=ResponseModel,

        messages=messages,

        temperature=0.5,

        max_completion_tokens=1000,

    )


    return response.tool_calls


# Example usage

user_prompt = "What's the weather like in San Francisco?"

tool_calls = run_conversation(user_prompt)


for call in tool_calls:

    print(f"Input: {call.input_text}")

    print(f"Tool: {call.tool_name}")

    print(f"Parameters: {call.tool_parameters}")

    print()

Benefits of Using Structured Outputs

    Type Safety: Pydantic models ensure that output adheres to the expected structure, reducing the risk of errors.
    Automatic Validation: Instructor automatically validates the model's output against the defined schema.

Next Steps

For more information and examples of working with structured outputs using Groq API and Instructor, see our Groq API Cookbook tutorial here.
Streaming Tool Use

The Groq API also offers streaming tool use, where you can stream tool use results to the client as they are generated.
python

from groq import Groq
import json

client = Groq()

async def main():
    stream = await client.chat.completions.create(
        messages=[
            {
                "role": "system",
                "content": "You are a helpful assistant.",
            },
            {
                "role": "user",
                # We first ask it to write a Poem, to show the case where there's text output before function calls, since that is also supported
                "content": "What is the weather in San Francisco and in Tokyo? First write a short poem.",
            },
        ],
        tools=[
            {
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
            }
        ],
        model="llama-3.3-70b-versatile",
        temperature=0.5,
        stream=True
    )

    async for chunk in stream:
        print(json.dumps(chunk.model_dump()) + "\n")

if __name__ == "__main__":
    import asyncio
    asyncio.run(main())

Best Practices

    Provide detailed tool descriptions for optimal performance.
    We recommend tool use with the Instructor library for structured outputs.
    Implement a routing system when using fine-tuned models in your workflow.
    Handle tool execution errors by returning error messages with "is_error": true.

Was this page helpful?
