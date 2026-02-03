Structured Outputs

Guarantee model responses strictly conform to your JSON schema for reliable, type-safe data structures.
Introduction

Structured Outputs is a feature that guarantees your model responses strictly conform to your provided JSON Schema or throws an error if the model cannot produce a compliant response. The endpoint ensures reliable data structures without missing fields or invalid values.

This feature operates on a best-effort basis and depends on the model's ability to produce a valid answer that matches your schema. If the model fails to generate a compliant response, the endpoint will return an error rather than an invalid or incomplete result.

Key benefits:

    Guaranteed compliance: Either returns valid JSON Schema-compliant output or throws an error
    Type-safe responses: No need to validate or retry malformed outputs
    Programmatic refusal detection: Detect safety-based model refusals programmatically
    Simplified prompting: No complex prompts needed for consistent formatting


In addition to supporting Structured Outputs in our API, our SDKs also enable you to easily define your schemas with Pydantic and Zod to ensure further type safety. The examples below show how to extract structured information from unstructured text.
Supported models

Structured Outputs is available with the following models:
Model ID	Model
openai/gpt-oss-20b
GPT-OSS 20B
openai/gpt-oss-120b
GPT-OSS 120B
moonshotai/kimi-k2-instruct
Kimi K2 Instruct
meta-llama/llama-4-maverick-17b-128e-instruct
Llama 4 Maverick
meta-llama/llama-4-scout-17b-16e-instruct
Llama 4 Scout

For all other models, you can use JSON Object Mode to get a valid JSON object, though it will not be guaranteed to match your schema.

Note: streaming and tool use are not currently supported with Structured Outputs.
Getting a structured response from unstructured text

import Groq from "groq-sdk";


const groq = new Groq();


const response = await groq.chat.completions.create({

  model: "moonshotai/kimi-k2-instruct",

  messages: [

    { role: "system", content: "Extract product review information from the text." },

    {

      role: "user",

      content: "I bought the UltraSound Headphones last week and I'm really impressed! The noise cancellation is amazing and the battery lasts all day. Sound quality is crisp and clear. I'd give it 4.5 out of 5 stars.",

    },

  ],

  response_format: {

    type: "json_schema",

    json_schema: {

      name: "product_review",

      schema: {

        type: "object",

        properties: {

          product_name: { type: "string" },

          rating: { type: "number" },

          sentiment: { 

            type: "string",

            enum: ["positive", "negative", "neutral"]

          },

          key_features: { 

            type: "array",

            items: { type: "string" }

          }

        },

        required: ["product_name", "rating", "sentiment", "key_features"],

        additionalProperties: false

      }

    }

  }

});


const result = JSON.parse(response.choices[0].message.content || "{}");

console.log(result);

Structured Outputs vs JSON mode

Structured Outputs builds on JSON Object Mode with enhanced capabilities. Both produce valid JSON, but Structured Outputs goes further by guaranteeing your response matches your schema exactly or throws an error if the model cannot produce a compliant response.

Note: Constrained decoding (which always guarantees JSON Schema compliance without errors) is currently only available on a limited-access Llama 3.1 8B model. For all other models, the endpoint uses best-effort validation that may return errors if the model cannot produce a compliant response.

We recommend using Structured Outputs instead of JSON Object Mode whenever possible.
Examples
SQL Query Generation

You can generate structured SQL queries from natural language descriptions, ensuring proper syntax and including metadata about the query structure.

import Groq from "groq-sdk";


const groq = new Groq();


const response = await groq.chat.completions.create({

  model: "moonshotai/kimi-k2-instruct",

  messages: [

    {

      role: "system",

      content: "You are a SQL expert. Generate structured SQL queries from natural language descriptions with proper syntax validation and metadata.",

    },

    { role: "user", content: "Find all customers who made orders over $500 in the last 30 days, show their name, email, and total order amount" },

  ],

  response_format: {

    type: "json_schema",

    json_schema: {

      name: "sql_query_generation",

      schema: {

        type: "object",

        properties: {

          query: { type: "string" },

          query_type: { 

            type: "string", 

            enum: ["SELECT", "INSERT", "UPDATE", "DELETE", "CREATE", "ALTER", "DROP"] 

          },

          tables_used: {

            type: "array",

            items: { type: "string" }

          },

          estimated_complexity: {

            type: "string",

            enum: ["low", "medium", "high"]

          },

          execution_notes: {

            type: "array",

            items: { type: "string" }

          },

          validation_status: {

            type: "object",

            properties: {

              is_valid: { type: "boolean" },

              syntax_errors: {

                type: "array",

                items: { type: "string" }

              }

            },

            required: ["is_valid", "syntax_errors"],

            additionalProperties: false

          }

        },

        required: ["query", "query_type", "tables_used", "estimated_complexity", "execution_notes", "validation_status"],

        additionalProperties: false

      }

    }

  }

});


const result = JSON.parse(response.choices[0].message.content || "{}");

console.log(result);

Schema Validation Libraries

When working with Structured Outputs, you can use popular schema validation libraries like Zod for TypeScript and Pydantic for Python. These libraries provide type safety, runtime validation, and seamless integration with JSON Schema generation.
Support Ticket Classification

This example demonstrates how to classify customer support tickets using structured schemas with both Zod and Pydantic, ensuring consistent categorization and routing.
python

from groq import Groq
from pydantic import BaseModel, Field
from typing import List, Optional, Literal
from enum import Enum
import json

client = Groq()

class SupportCategory(str, Enum):
    API = "api"
    BILLING = "billing"
    ACCOUNT = "account"
    BUG = "bug"
    FEATURE_REQUEST = "feature_request"
    INTEGRATION = "integration"
    SECURITY = "security"
    PERFORMANCE = "performance"

class Priority(str, Enum):
    LOW = "low"
    MEDIUM = "medium"
    HIGH = "high"
    CRITICAL = "critical"

class CustomerTier(str, Enum):
    FREE = "free"
    PAID = "paid"
    ENTERPRISE = "enterprise"
    TRIAL = "trial"

class CustomerInfo(BaseModel):
    name: str
    company: Optional[str] = None
    tier: CustomerTier

class TechnicalDetail(BaseModel):
    component: str
    error_code: Optional[str] = None
    description: str

class SupportTicket(BaseModel):
    category: SupportCategory
    priority: Priority
    urgency_score: float
    customer_info: CustomerInfo
    technical_details: List[TechnicalDetail]
    keywords: List[str]
    requires_escalation: bool
    estimated_resolution_hours: float
    follow_up_date: Optional[str] = Field(None, description="ISO datetime string")
    summary: str

response = client.chat.completions.create(
    model="moonshotai/kimi-k2-instruct",
    messages=[
        {
            "role": "system",
            "content": """You are a customer support ticket classifier for SaaS companies. 
                         Analyze support tickets and categorize them for efficient routing and resolution.
                         Output JSON only using the schema provided.""",
        },
        { 
            "role": "user", 
            "content": """Hello! I love your product and have been using it for 6 months. 
                         I was wondering if you could add a dark mode feature to the dashboard? 
                         Many of our team members work late hours and would really appreciate this. 
                         Also, it would be great to have keyboard shortcuts for common actions. 
                         Not urgent, but would be a nice enhancement! 
                         Best, Mike from StartupXYZ"""
        },
    ],
    response_format={
        "type": "json_schema",
        "json_schema": {
            "name": "support_ticket_classification",
            "schema": SupportTicket.model_json_schema()
        }
    }
)

raw_result = json.loads(response.choices[0].message.content or "{}")
result = SupportTicket.model_validate(raw_result)
print(result.model_dump_json(indent=2))

Implementation Guide
Schema Definition

Design your JSON Schema to constrain model responses. Reference the examples above and see supported schema features for technical limitations.

Schema optimization tips:

    Use descriptive property names and clear descriptions for complex fields
    Create evaluation sets to test schema effectiveness
    Include titles for important structural elements

API Integration

Include the schema in your API request using the response_format parameter:
JSON

response_format: { type: "json_schema", json_schema: { name: "schema_name", schema: … } }


Complete implementation example:

from groq import Groq

import json


client = Groq()


response = client.chat.completions.create(

    model="moonshotai/kimi-k2-instruct",

    messages=[

        {"role": "system", "content": "You are a helpful math tutor. Guide the user through the solution step by step."},

        {"role": "user", "content": "how can I solve 8x + 7 = -23"}

    ],

    response_format={

        "type": "json_schema",

        "json_schema": {

            "name": "math_response",

            "schema": {

                "type": "object",

                "properties": {

                    "steps": {

                        "type": "array",

                        "items": {

                            "type": "object",

                            "properties": {

                                "explanation": {"type": "string"},

                                "output": {"type": "string"}

                            },

                            "required": ["explanation", "output"],

                            "additionalProperties": False

                        }

                    },

                    "final_answer": {"type": "string"}

                },

                "required": ["steps", "final_answer"],

                "additionalProperties": False

            }

        }

    }

)


result = json.loads(response.choices[0].message.content)

print(json.dumps(result, indent=2))

Error Handling

Schema validation failures return HTTP 400 errors with the message Generated JSON does not match the expected schema. Please adjust your prompt.

Resolution strategies:

    Retry requests for transient failures
    Refine prompts for recurring schema mismatches
    Simplify complex schemas if validation consistently fails

Best Practices

User input handling: Include explicit instructions for invalid or incompatible inputs. Models attempt schema adherence even with unrelated data, potentially causing hallucinations. Specify fallback responses (empty fields, error messages) for incompatible inputs.

Output quality: Structured outputs guarantee schema compliance but not semantic accuracy. For persistent errors, refine instructions, add system message examples, or decompose complex tasks. See the prompt engineering guide for optimization techniques.
Schema Requirements

Structured Outputs supports a JSON Schema subset with specific constraints for performance and reliability.
Supported Data Types

    Primitives: String, Number, Boolean, Integer
    Complex: Object, Array, Enum
    Composition: anyOf (union types)

Mandatory Constraints

Required fields: All schema properties must be marked as required. Optional fields are not supported.

JSON

{
  "name": "create_task",
  "description": "Creates a new task in the project management system",
  "strict": true,
  "parameters": {
    "type": "object",
    "properties": {
      "title": {
        "type": "string",
        "description": "The task title or summary"
      },
      "priority": {
        "type": "string",
        "description": "Task priority level",
        "enum": ["low", "medium", "high", "urgent"]
      }
    },
    "additionalProperties": false,
    "required": ["title", "priority"]
  }
}


Closed objects: All objects must set additionalProperties: false to prevent undefined properties. This ensures strict schema adherence.

JSON

{
  "name": "book_appointment",
  "description": "Books a medical appointment",
  "strict": true,
  "schema": {
    "type": "object",
    "properties": {
      "patient_name": {
        "type": "string",
        "description": "Full name of the patient"
      },
      "appointment_type": {
        "type": "string",
        "description": "Type of medical appointment",
        "enum": ["consultation", "checkup", "surgery", "emergency"]
      }
    },
    "additionalProperties": false,
    "required": ["patient_name", "appointment_type"]
  }
}


Union types: Each schema within anyOf must comply with all subset restrictions:

JSON

{
  "type": "object",
  "properties": {
    "payment_method": {
      "anyOf": [
        {
          "type": "object",
          "description": "Credit card payment information",
          "properties": {
            "card_number": {
              "type": "string",
              "description": "The credit card number"
            },
            "expiry_date": {
              "type": "string",
              "description": "Card expiration date in MM/YY format"
            },
            "cvv": {
              "type": "string",
              "description": "Card security code"
            }
          },
          "additionalProperties": false,
          "required": ["card_number", "expiry_date", "cvv"]
        },
        {
          "type": "object",
          "description": "Bank transfer payment information",
          "properties": {
            "account_number": {
              "type": "string",
              "description": "Bank account number"
            },
            "routing_number": {
              "type": "string",
              "description": "Bank routing number"
            },
            "bank_name": {
              "type": "string",
              "description": "Name of the bank"
            }
          },
          "additionalProperties": false,
          "required": ["account_number", "routing_number", "bank_name"]
        }
      ]
    }
  },
  "additionalProperties": false,
  "required": ["payment_method"]
}


Reusable subschemas: Define reusable components with $defs and reference them using $ref:

JSON

{
  "type": "object",
  "properties": {
    "milestones": {
      "type": "array",
      "items": {
        "$ref": "#/$defs/milestone"
      }
    },
    "project_status": {
      "type": "string",
      "enum": ["planning", "in_progress", "completed", "on_hold"]
    }
  },
  "$defs": {
    "milestone": {
      "type": "object",
      "properties": {
        "title": {
          "type": "string",
          "description": "Milestone name"
        },
        "deadline": {
          "type": "string",
          "description": "Due date in ISO format"
        },
        "completed": {
          "type": "boolean"
        }
      },
      "required": ["title", "deadline", "completed"],
      "additionalProperties": false
    }
  },
  "required": ["milestones", "project_status"],
  "additionalProperties": false
}


Root recursion: Use # to reference the root schema:

JSON

{
  "name": "organization_chart",
  "description": "Company organizational structure",
  "strict": true,
  "schema": {
    "type": "object",
    "properties": {
      "employee_id": {
        "type": "string",
        "description": "Unique employee identifier"
      },
      "name": {
        "type": "string",
        "description": "Employee full name"
      },
      "position": {
        "type": "string",
        "description": "Job title or position",
        "enum": ["CEO", "Manager", "Developer", "Designer", "Analyst", "Intern"]
      },
      "direct_reports": {
        "type": "array",
        "description": "Employees reporting to this person",
        "items": {
          "$ref": "#"
        }
      },
      "contact_info": {
        "type": "array",
        "description": "Contact information for the employee",
        "items": {
          "type": "object",
          "properties": {
            "type": {
              "type": "string",
              "description": "Type of contact info",
              "enum": ["email", "phone", "slack"]
            },
            "value": {
              "type": "string",
              "description": "The contact value"
            }
          },
          "additionalProperties": false,
          "required": ["type", "value"]
        }
      }
    },
    "required": [
      "employee_id",
      "name",
      "position",
      "direct_reports",
      "contact_info"
    ],
    "additionalProperties": false
  }
}


Explicit recursion through definition references:

JSON

{
  "type": "object",
  "properties": {
    "file_system": {
      "$ref": "#/$defs/file_node"
    }
  },
  "$defs": {
    "file_node": {
      "type": "object",
      "properties": {
        "name": {
          "type": "string",
          "description": "File or directory name"
        },
        "type": {
          "type": "string",
          "enum": ["file", "directory"]
        },
        "size": {
          "type": "number",
          "description": "Size in bytes (0 for directories)"
        },
        "children": {
          "anyOf": [
            {
              "type": "array",
              "items": {
                "$ref": "#/$defs/file_node"
              }
            },
            {
              "type": "null"
            }
          ]
        }
      },
      "additionalProperties": false,
      "required": ["name", "type", "size", "children"]
    }
  },
  "additionalProperties": false,
  "required": ["file_system"]
}

JSON Object Mode

JSON Object Mode provides basic JSON output validation without schema enforcement. Unlike Structured Outputs with json_schema mode, it guarantees valid JSON syntax but not schema compliance. The endpoint will either return valid JSON or throw an error if the model cannot produce valid JSON syntax. Use Structured Outputs when available for your use case.

Enable JSON Object Mode by setting response_format to { "type": "json_object" }.

Requirements and limitations:

    Include explicit JSON instructions in your prompt (system message or user input)
    Outputs are syntactically valid JSON but may not match your intended schema
    Combine with validation libraries and retry logic for schema compliance

Sentiment Analysis Example

This example shows prompt-guided JSON generation for sentiment analysis, adaptable to classification, extraction, or summarization tasks:

import { Groq } from "groq-sdk";


const groq = new Groq();


async function main() {

  const response = await groq.chat.completions.create({

    model: "openai/gpt-oss-20b",

    messages: [

      {

        role: "system",

        content: `You are a data analysis API that performs sentiment analysis on text.

                Respond only with JSON using this format:

                {

                    "sentiment_analysis": {

                    "sentiment": "positive|negative|neutral",

                    "confidence_score": 0.95,

                    "key_phrases": [

                        {

                        "phrase": "detected key phrase",

                        "sentiment": "positive|negative|neutral"

                        }

                    ],

                    "summary": "One sentence summary of the overall sentiment"

                    }

                }`

      },

      { role: "user", content: "Analyze the sentiment of this customer review: 'I absolutely love this product! The quality exceeded my expectations, though shipping took longer than expected.'" }

    ],

    response_format: { type: "json_object" }

  });


  const result = JSON.parse(response.choices[0].message.content || "{}");

  console.log(result);

}


main();

System prompts structure the output format while maintaining JSON validity. However, keep in mind that the JSON object output may not match your schema.


Response structure:

    sentiment: Classification (positive/negative/neutral)
    confidence_score: Confidence level (0-1 scale)
    key_phrases: Extracted phrases with individual sentiment scores
    summary: Analysis overview and main findings

Was this page helpful?
