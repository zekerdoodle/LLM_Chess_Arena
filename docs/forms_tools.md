# Form Tools: Define, Show, Save, List

This document describes Theo's form tools with clear, provider-safe schemas and examples. These are the same tools exposed to models via native function-calling.

Overview
- Purpose: Create lightweight forms for structured user input in the web app.
- Tools: `define_form`, `show_form`, `save_form_submission`, `list_form_submissions`.
- Flow: define_form → show_form (turn ends) → user submits → list_form_submissions to review.

Key Rules
- Only define a given form once per turn; then call `show_form` to display it.
- For choice fields (`select`, `multiselect`, `radio`), you must provide `options`.
- `prefill` and `answers` accept either an object mapping or an array of `{id, value}` pairs.

Schemas (JSON Schema subset)
1) define_form
{
  "form": {
    "form_id": "string",
    "title": "string",
    "description": "string|null",
    "version": "integer|null",
    "fields": [
      {
        "id": "string",
        "label": "string",
        "type": "text|textarea|number|email|url|select|multiselect|checkbox|radio|date",
        "required": "boolean",
        "options": ["string"] | null,
        "placeholder": "string|null",
        "help": "string|null"
      }
    ]
  }
}

2) show_form
{
  "form_id": "string",
  "prefill": {"field_id": value} | [{"id": "field_id", "value": value}],
  "room_id": "string|null"
}

3) save_form_submission
{
  "form_id": "string",
  "answers": {"field_id": value} | [{"id": "field_id", "value": value}],
  "room_id": "string|null"
}

4) list_form_submissions
{
  "form_id": "string|null",
  "limit": "integer",
  "after_ts": "number|null",
  "room_id": "string|null"
}

Examples
- Define a reusable form
{
  "form": {
    "form_id": "feedback_v1",
    "title": "Feedback",
    "version": 1,
    "fields": [
      {"id": "name", "label": "Your name", "type": "text"},
      {"id": "rating", "label": "Rating", "type": "radio", "required": true, "options": ["1","2","3","4","5"]},
      {"id": "comments", "label": "Comments", "type": "textarea"}
    ]
  }
}

- Show it with prefill
{"form_id": "feedback_v1", "prefill": {"name": "Ada"}}

- Save a submission
{"form_id": "feedback_v1", "answers": {"name": "Ada", "rating": "5", "comments": "Great!"}, "room_id": "web"}

- List recent submissions
{"form_id": "feedback_v1", "limit": 10}

Provider Notes
- OpenAI/GPT-5: Uses Responses API tool calling. Schemas here use `anyOf` for union types to avoid ambiguous `type: [object,array]` constructs.
- Anthropic: Tools are not used for “structured outputs”; form tools are exposed via tool_use with the same shapes.
- Google, xAI, Groq: Mapped from the same base schema; see respective docs under `docs/{provider}` for exact request shapes.

