"""
Tool Schema Generation Utilities

This module generates provider‑friendly function schemas and concise guidance
for Theo's native, structured function calling. Descriptions use a consistent,
natural, second‑person imperative style (e.g., "Use this to …", "Create …").
Parameter/property descriptions are short and specific, avoiding robotic
phrases like "You can …".
"""

from typing import Any, Dict, List, Optional, Tuple
from pathlib import Path
import json

from utils.vault_paths import get_vault_root
from utils.prompt_labels import AVAILABLE_TOOLS_HEADER

# In-process cache: schemas are static across the process lifetime
_SCHEMAS_CACHE: List[Dict[str, Any]] | None = None


# ------------------------------
# Description normalization
# ------------------------------

_TOOL_IMPERATIVE_STARTS: Tuple[str, ...] = (
    # Common, clear imperative leads we accept as-is
    "Create ",
    "Generate ",
    "Fetch ",
    "Display ",
    "Show ",
    "Send ",
    "Save ",
    "Mark ",
    "Add ",
    "Update ",
    "Delete ",
    "List ",
    "Preview ",
    "Apply ",
    "Run ",
    "Execute ",
    "Parse ",
    "Read ",
)


def _to_second_person(description: str) -> str:
    """Normalize a tool description to a natural second‑person/imperative style.

    Guidelines:
    - Prefer imperative phrasing the model can follow: "Use this to …", "Create …".
    - Avoid robotic prefixes like "You can …".
    - Keep existing clear imperatives (Create/Generate/…); just trim whitespace.
    - Rewrite weak leads ("Allows", "This tool", "Use a …") into "Use this to …".

    Examples:
        >>> _to_second_person("Send a message to the room.")
        'Send a message to the room.'
        >>> _to_second_person("Use a free search engine to find URLs.")
        'Use this to find URLs.'
        >>> _to_second_person("This tool allows you to preview diffs")
        'Use this to preview diffs.'
    """
    try:
        if not isinstance(description, str):
            return description
        desc = " ".join(description.strip().split())  # collapse whitespace
        if not desc:
            return desc

        # If already imperative with an accepted verb, keep as-is
        for lead in _TOOL_IMPERATIVE_STARTS:
            if desc.startswith(lead):
                return _ensure_period(desc)

        low = desc.lower()
        # Remove unhelpful second-person starts
        for bad in ("you can ", "you should ", "you may ", "you must "):
            if low.startswith(bad):
                desc = desc[len(bad):].lstrip().capitalize()
                break

        # Rewrite common weak leads to "Use this to …"
        wl = desc.lower()
        if wl.startswith("use a ") or wl.startswith("use an ") or wl.startswith("use the ") or wl == "use":
            # e.g., "Use a free search engine to find URLs" → "Use this to find URLs"
            after = desc.split(" to ", 1)[-1] if " to " in wl else desc[4:].lstrip()
            return _ensure_period(f"Use this to {after}")
        if wl.startswith("use "):
            after = desc[4:].lstrip()
            return _ensure_period(f"Use this to {after}")
        if wl.startswith("this tool ") or wl.startswith("this tool can ") or wl.startswith("this tool allows"):
            # Normalize to "Use this to …"
            # Try to keep the final action after "to"
            if " to " in wl:
                after = desc.split(" to ", 1)[1]
                return _ensure_period(f"Use this to {after}")
            # Fallback to a generic improvement
            return _ensure_period("Use this to perform the described action")
        if wl.startswith("allows ") or wl.startswith("allow ") or wl.startswith("enables ") or wl.startswith("enable ") or wl.startswith("lets ") or wl.startswith("let "):
            # "Allows searching" → "Use this to search"
            after = desc.split(" to ", 1)[-1] if " to " in wl else desc.split(" ", 1)[-1]
            # Remove possible leading "you to"
            after = after.lstrip()
            after_l = after.lower()
            if after_l.startswith("you to "):
                after = after[7:]
            return _ensure_period(f"Use this to {after}")

        # If it starts with a verb already (imperative without subject), keep it
        # e.g., "Send a message…", "Preview diffs…" → leave
        if _looks_imperative(desc):
            return _ensure_period(desc)

        # Default: make it an imperative via "Use this to …"
        return _ensure_period(f"Use this to {desc[0].lower()}{desc[1:]}")
    except Exception:
        return description


def _looks_imperative(text: str) -> bool:
    if not text:
        return False
    # Basic heuristic: starts with a capitalized verb-like word (no subject)
    first = text.split(" ", 1)[0]
    # Too short or ends with punctuation? still fine
    # Accept if it starts with a known imperative verb stem
    stems = [s.strip() for s in _TOOL_IMPERATIVE_STARTS]
    return any(first.startswith(stem.strip()) for stem in stems)


def _ensure_period(text: str) -> str:
    # Add trailing period if the sentence does not end with ., !, or ?
    if not text:
        return text
    if text[-1] in ".!?":
        return text
    return text + "."


def _to_second_person_param(desc: str) -> str:
    """Normalize parameter/property descriptions to concise, imperative phrasing.

    Rules:
    - Avoid "You can …"; prefer direct imperatives or short noun phrases.
    - Keep clear starts like "Path to …", "ID of …", "Maximum number …".
    - If description begins with a weak second‑person lead, strip it.
    - If it begins with verbs like use/set/provide/pass/send/save/list/create/update/delete,
      capitalize and keep (without adding "You").
    """
    try:
        if not isinstance(desc, str):
            return desc
        s = " ".join(desc.strip().split())
        if not s:
            return s
        low = s.lower()
        # Strip robotic second-person opens
        for bad in ("you can ", "you should ", "you may ", "you must ", "you "):
            if low.startswith(bad):
                s = s[len(bad):].lstrip().capitalize()
                low = s.lower()
                break
        verbs = ("use ", "set ", "provide ", "pass ", "send ", "save ", "list ", "create ", "update ", "delete ")
        for v in verbs:
            if low.startswith(v):
                return s[0].upper() + s[1:]
        return s
    except Exception:
        return desc


def _normalize_param_descriptions(obj: Any) -> Any:
    """Recursively normalize all 'description' fields in a JSON schema object.

    Returns the same object with in-place updates where applicable.
    """
    try:
        if isinstance(obj, dict):
            # Normalize description at this level
            if "description" in obj and isinstance(obj["description"], str):
                obj["description"] = _to_second_person_param(obj["description"])
            # Recurse into common schema containers
            for key in ("properties", "patternProperties"):
                if key in obj and isinstance(obj[key], dict):
                    for _, v in obj[key].items():
                        _normalize_param_descriptions(v)
            # items can be dict or list
            if "items" in obj:
                items = obj["items"]
                if isinstance(items, list):
                    for v in items:
                        _normalize_param_descriptions(v)
                else:
                    _normalize_param_descriptions(items)
            # anyOf, oneOf, allOf
            for key in ("anyOf", "oneOf", "allOf"):
                if key in obj and isinstance(obj[key], list):
                    for v in obj[key]:
                        _normalize_param_descriptions(v)
            # additionalProperties can be dict
            if isinstance(obj.get("additionalProperties"), dict):
                _normalize_param_descriptions(obj["additionalProperties"])
        elif isinstance(obj, list):
            for v in obj:
                _normalize_param_descriptions(v)
    except Exception:
        # Best-effort; ignore normalization errors
        return obj
    return obj


def generate_tool_schemas(provider: Optional[str] = None) -> List[Dict[str, Any]]:
    """Generate OpenAI-compatible function schemas from TOOL_REGISTRY.

    Args:
        provider: Optional provider name ('openai', 'xai', etc.) to filter tool schemas.
                 When 'openai' or 'xai', the web_search tool is hidden since those providers
                 have native web search capabilities.

    Returns:
        List of tool schemas for native function calling
    """
    # Don't cache when provider filtering is active
    global _SCHEMAS_CACHE
    if _SCHEMAS_CACHE is not None and provider is None:
        return _SCHEMAS_CACHE

    schemas: List[Dict[str, Any]] = []

    # Working Memory Tools
    schemas.extend([
        {
            "name": "add_working_memory",
            "description": "Capture a short private note in working memory so it persists for the next few exchanges. Supports pinning (keeps item at top, immune to expiration) and deadlines (time-based priorities).",
            "x_prompt_summary": "Add a concise working memory note (expires automatically unless pinned or has deadline).",
            "parameters": {
                "type": "object",
                "properties": {
                    "content": {
                        "type": "string",
                        "description": "Working memory note (keep it brief and actionable)",
                    },
                    "tag": {
                        "type": ["string", "null"],
                        "description": "Optional tag such as goal, plan, fact, decision, blocker, todo, hypothesis, note",
                    },
                    "ttl_exchanges": {
                        "type": ["integer", "null"],
                        "description": "How many exchanges the note should persist (1-10). Ignored if deadline_at is set until deadline passes.",
                        "minimum": 1,
                        "maximum": 10,
                    },
                    "pinned": {
                        "type": ["boolean", "null"],
                        "description": "Pin this item to top (max 3 pinned items). Pinned items never expire and float to top.",
                    },
                    "pin_rank": {
                        "type": ["integer", "null"],
                        "description": "Priority rank for pinned items (1-3, higher is more important). Only applies if pinned=true.",
                        "minimum": 1,
                        "maximum": 3,
                    },
                    "deadline_at": {
                        "type": ["string", "null"],
                        "description": "ISO 8601 deadline timestamp (e.g., '2025-10-12T19:00:00-05:00' or '2025-10-12T19:00:00' for America/Chicago default). Items with deadlines ignore TTL until deadline passes.",
                    },
                    "remind_before": {
                        "type": ["string", "null"],
                        "description": "Duration before deadline to mark as 'due soon' (e.g., '2h', '24h', '15m')",
                    },
                    "deadline_type": {
                        "type": ["string", "null"],
                        "description": "Type of deadline: 'soft' (just highlights) or 'hard' (can trigger auto-actions). Default: 'soft'",
                        "enum": ["soft", "hard"],
                    },
                    "snooze_until": {
                        "type": ["string", "null"],
                        "description": "ISO 8601 timestamp to defer this item until. Used for quick deferrals.",
                    },
                },
                "required": ["content"],
                "additionalProperties": False,
            },
        },
        {
            "name": "remove_working_memory",
            "description": "Delete a working memory note by the number shown in the prompt injection list.",
            "x_prompt_summary": "Remove a working memory entry by its displayed number.",
            "parameters": {
                "type": "object",
                "properties": {
                    "index": {
                        "type": "integer",
                        "description": "Displayed working memory number to remove",
                        "minimum": 1,
                    },
                    "wm_version": {
                        "type": ["string", "null"],
                        "description": "Optional working memory version from the most recent tool response",
                    },
                },
                "required": ["index"],
                "additionalProperties": False,
            },
        },
        {
            "name": "update_working_memory",
            "description": "Rewrite or append to an existing working memory note, optionally adjusting its TTL, tag, pinning, or deadline.",
            "x_prompt_summary": "Update a working memory note by number (replace text, append text, retag, refresh TTL, pin/unpin, or set deadline).",
            "parameters": {
                "type": "object",
                "properties": {
                    "index": {
                        "type": "integer",
                        "description": "Displayed working memory number to update",
                        "minimum": 1,
                    },
                    "content": {
                        "type": ["string", "null"],
                        "description": "Full replacement text for the note",
                    },
                    "append": {
                        "type": ["string", "null"],
                        "description": "Short suffix to append to the existing note",
                    },
                    "ttl_exchanges": {
                        "type": ["integer", "null"],
                        "description": "New TTL (1-10 exchanges). Ignored if deadline_at is set until deadline passes.",
                        "minimum": 1,
                        "maximum": 10,
                    },
                    "tag": {
                        "type": ["string", "null"],
                        "description": "Optional tag such as goal, plan, fact, decision, blocker, todo, hypothesis, note",
                    },
                    "pinned": {
                        "type": ["boolean", "null"],
                        "description": "Pin/unpin this item (max 3 pinned items total). Pinned items never expire and float to top.",
                    },
                    "pin_rank": {
                        "type": ["integer", "null"],
                        "description": "Priority rank for pinned items (1-3, higher is more important). Only applies if pinned=true.",
                        "minimum": 1,
                        "maximum": 3,
                    },
                    "deadline_at": {
                        "type": ["string", "null"],
                        "description": "ISO 8601 deadline timestamp (e.g., '2025-10-12T19:00:00-05:00' or '2025-10-12T19:00:00' for America/Chicago default). Items with deadlines ignore TTL until deadline passes.",
                    },
                    "remind_before": {
                        "type": ["string", "null"],
                        "description": "Duration before deadline to mark as 'due soon' (e.g., '2h', '24h', '15m')",
                    },
                    "deadline_type": {
                        "type": ["string", "null"],
                        "description": "Type of deadline: 'soft' (just highlights) or 'hard' (can trigger auto-actions). Default: 'soft'",
                        "enum": ["soft", "hard"],
                    },
                    "snooze_until": {
                        "type": ["string", "null"],
                        "description": "ISO 8601 timestamp to defer this item until. Used for quick deferrals.",
                    },
                    "wm_version": {
                        "type": ["string", "null"],
                        "description": "Optional working memory version from the most recent tool response",
                    },
                },
                "required": ["index"],
                "additionalProperties": False,
            },
        },
        {
            "name": "snapshot_working_memory",
            "description": "Promote selected working memory notes into Theo memories with an explicit importance score.",
            "x_prompt_summary": "Snapshot working memory into Theo memories (choose scope, importance, optional title).",
            "parameters": {
                "type": "object",
                "properties": {
                    "importance": {
                        "type": "integer",
                        "description": "Importance score for the resulting Theo memory (0-100)",
                        "minimum": 0,
                        "maximum": 100,
                    },
                    "scope": {
                        "type": ["string", "null"],
                        "description": "Which notes to snapshot: visible (default), all, tag, or indices",
                        "enum": ["visible", "all", "tag", "indices"],
                        "default": "visible",
                    },
                    "tag": {
                        "type": ["string", "null"],
                        "description": "Tag filter when scope is 'tag'",
                    },
                    "indices": {
                        "type": ["array", "null"],
                        "description": "Specific working memory numbers to snapshot when scope is 'indices'",
                        "items": {"type": "integer", "minimum": 1},
                    },
                    "title": {
                        "type": ["string", "null"],
                        "description": "Optional heading for the Theo memory entry",
                    },
                },
                "required": ["importance"],
                "additionalProperties": False,
            },
        },
    ])


    # Financial Tools (Plaid Integration)
    schemas.append(
        {
            "name": "connect_bank_account",
            "description": "Initiate bank account connection via Plaid. Call ONCE (without parameters) to generate a secure link - the user will be prompted to select and login to their bank at the end of the turn. DO NOT call repeatedly or 'check status' - one call starts the connection flow. After user completes login in their browser, the system automatically handles the rest.",
            "x_prompt_summary": "Start bank connection flow (user prompted to login at end of turn).",
            "parameters": {
                "type": "object",
                "properties": {
                    "public_token": {
                        "type": ["string", "null"],
                        "description": "Public token from Plaid Link after user connects bank (optional on first call)",
                    },
                },
                "additionalProperties": False,
            },
        }
    )

    schemas.append(
        {
            "name": "get_financial_accounts",
            "description": "Get a COMPLETE list of ALL connected financial accounts with current balances, account types, and details. Returns ALL accounts in a single call - do not call repeatedly to check for more accounts. This is a read-only query that provides a comprehensive snapshot.",
            "x_prompt_summary": "List all connected bank accounts with balances (returns complete list).",
            "parameters": {
                "type": "object",
                "properties": {},
                "additionalProperties": False,
            },
        }
    )

    schemas.append(
        {
            "name": "get_transactions",
            "description": (
                "Get transaction history for connected accounts with merchant names, amounts, categories, and dates. "
                "Transactions are sorted by date (most recent first). Tip: specify the exact account mask (e.g., ...2046) "
                "when requesting data so the correct account is targeted."
            ),
            "x_prompt_summary": "Fetch transaction history (specify account mask when possible).",
            "parameters": {
                "type": "object",
                "properties": {
                    "start_date": {
                        "type": ["string", "null"],
                        "description": "Start date in YYYY-MM-DD format (defaults to 30 days ago)",
                    },
                    "end_date": {
                        "type": ["string", "null"],
                        "description": "End date in YYYY-MM-DD format (defaults to today)",
                    },
                    "account_id": {
                        "type": ["string", "null"],
                        "description": "Optional specific account ID to filter transactions",
                    },
                    "limit": {
                        "type": "integer",
                        "description": "Maximum number of transactions to return (default 50, max 100)",
                        "default": 50,
                    },
                },
                "additionalProperties": False,
            },
        }
    )

    schemas.append(
        {
            "name": "get_spending_analysis",
            "description": "Analyze spending breakdown by category with totals, percentages, and visual representation. Shows top spending categories and transaction counts.",
            "x_prompt_summary": "Analyze spending by category for a date range.",
            "parameters": {
                "type": "object",
                "properties": {
                    "start_date": {
                        "type": ["string", "null"],
                        "description": "Start date in YYYY-MM-DD format (defaults to 30 days ago)",
                    },
                    "end_date": {
                        "type": ["string", "null"],
                        "description": "End date in YYYY-MM-DD format (defaults to today)",
                    },
                },
                "additionalProperties": False,
            },
        }
    )

    schemas.append(
        {
            "name": "disconnect_bank_account",
            "description": "Disconnect a bank account and revoke Plaid access. Permanently removes the connection and all locally cached data for the specified item.",
            "x_prompt_summary": "Remove a bank connection and revoke access.",
            "parameters": {
                "type": "object",
                "properties": {
                    "item_id": {
                        "type": "string",
                        "description": "Plaid item ID to disconnect (from get_connection_status)",
                    },
                },
                "required": ["item_id"],
                "additionalProperties": False,
            },
        }
    )

    schemas.append(
        {
            "name": "get_connection_status",
            "description": "Get COMPLETE current Plaid connection status including number of connected items, accounts, and environment details. Returns comprehensive status in a single call - subsequent calls will show the same data unless user connects/disconnects accounts.",
            "x_prompt_summary": "Check Plaid connection status (returns complete status).",
            "parameters": {
                "type": "object",
                "properties": {},
                "additionalProperties": False,
            },
        }
    )

    # Forms Tools
    schemas.append(
        {
            "name": "define_form",
            "description": "Register or update a reusable structured form that Theo can present to the user. Use for recurring data collection like goals check-ins.",
            "parameters": {
                "type": "object",
                "properties": {
                    "form": {
                        "type": "object",
                        "description": "Form definition with required and optional fields",
                        "properties": {
                            "form_id": {
                                "type": "string",
                                "description": "Unique identifier for the form (e.g., 'weekly_goals', 'feedback_form')"
                            },
                            "title": {
                                "type": "string",
                                "description": "Human-readable title displayed to users"
                            },
                            "description": {
                                "type": "string",
                                "description": "Optional description explaining the form's purpose"
                            },
                            "version": {
                                "type": "integer",
                                "description": "Version number for form updates (default: 1)"
                            },
                            "fields": {
                                "type": "array",
                                "description": "Array of form field definitions",
                                "items": {
                                    "type": "object",
                                    "properties": {
                                        "id": {
                                            "type": "string",
                                            "description": "Unique field identifier (e.g., 'name', 'rating', 'comments')"
                                        },
                                        "label": {
                                            "type": "string",
                                            "description": "Human-readable label displayed to users"
                                        },
                                        "type": {
                                            "type": "string",
                                            "enum": [
                                                "text",
                                                "textarea",
                                                "number",
                                                "email",
                                                "url",
                                                "select",
                                                "multiselect",
                                                "checkbox",
                                                "radio",
                                                "date"
                                            ],
                                            "description": "Field type"
                                        },
                                        "required": {
                                            "type": "boolean",
                                            "description": "Whether this field must be filled out (default: false)"
                                        },
                                        "options": {
                                            "description": "For select/radio/multiselect: array of option strings or key/value objects",
                                            "anyOf": [
                                                {
                                                    "type": "array",
                                                    "items": {"type": "string"},
                                                },
                                                {
                                                    "type": "array",
                                                    "items": {
                                                        "type": "object",
                                                        "properties": {
                                                            "id": {"type": "string"},
                                                            "value": {"type": ["string", "number", "boolean"]},
                                                        },
                                                        "required": ["id", "value"],
                                                        "additionalProperties": False,
                                                    },
                                                },
                                            ],
                                        },
                                        "placeholder": {
                                            "type": "string",
                                            "description": "Example text shown in the input field"
                                        },
                                        "help": {
                                            "type": "string",
                                            "description": "Additional help text displayed below the field"
                                        }
                                    },
                                    "required": ["id", "label", "type"],
                                    "additionalProperties": False
                                }
                            }
                        },
                        "required": ["form_id", "title", "fields"],
                        "additionalProperties": False
                    }
                },
                "required": ["form"],
                "additionalProperties": False,
            },
        }
    )

    schemas.append(
        {
            "name": "show_form",
            "description": "Display a previously defined form to the user inline in the UI. Non-blocking: Theo may continue the turn; when the user submits, a user message with answers wakes Theo.",
            "parameters": {
                "type": "object",
                "properties": {
                    "form_id": {
                        "type": "string",
                        "description": "ID of the form to display (must be previously defined with define_form)"
                    },
                    "prefill": {
                        "description": "Optional values to pre-fill in the form, provided as an array of {id,value} pairs",
                        "type": "array",
                        "items": {
                            "type": "object",
                            "properties": {
                                "id": {"type": "string"},
                                "value": {"type": ["string", "number", "boolean", "null"]},
                            },
                            "required": ["id", "value"],
                            "additionalProperties": False,
                        },
                    },
                    "room_id": {
                        "type": ["string", "null"],
                        "description": "Optional room identifier for multi-room deployments"
                    },
                },
                "required": ["form_id"],
                "additionalProperties": False,
            },
        }
    )

    schemas.append(
        {
            "name": "save_form_submission",
            "description": "Save user-submitted answers for a form into long-term storage for Theo's review.",
            "parameters": {
                "type": "object",
                "properties": {
                    "form_id": {
                        "type": "string",
                        "description": "ID of the form being submitted"
                    },
                    "answers": {
                        "description": "User's form responses as an array of {id,value} pairs",
                        "type": "array",
                        "items": {
                            "type": "object",
                            "properties": {
                                "id": {"type": "string"},
                                "value": {"type": ["string", "number", "boolean", "null"]},
                            },
                            "required": ["id", "value"],
                            "additionalProperties": False,
                        },
                    },
                    "room_id": {
                        "type": ["string", "null"],
                        "description": "Optional room identifier for multi-room deployments"
                    },
                    "mock_mode": {
                        "type": "boolean",
                        "description": "If true, allows programmatic form submission for testing without user interaction. Default: false",
                        "default": False
                    }
                },
                "required": ["form_id", "answers"],
                "additionalProperties": False,
            },
        }
    )

    schemas.append(
        {
            "name": "list_form_submissions",
            "description": "List recent form submissions for Theo's review and analysis.",
            "parameters": {
                "type": "object",
                "properties": {
                    "form_id": {
                        "type": "string",
                        "description": "Optional: filter submissions to a specific form"
                    },
                    "limit": {
                        "type": "integer",
                        "description": "Maximum number of submissions to return (default: 20)"
                    },
                    "after_ts": {
                        "type": "number",
                        "description": "Optional: only show submissions after this timestamp (Unix seconds)"
                    },
                    "room_id": {
                        "type": "string",
                        "description": "Optional: filter submissions to a specific room"
                    },
                },
                "required": [],
                "additionalProperties": False,
            },
        }
    )

    # see_form_updates removed (redundant)

    # File tools removed — use 'bash' for controlled shell access to read/list files.

    # Deep Research Tools
    # Helper: attach visible policy summary for bash tool (nudges model away from forbidden ops)
    def _sh_policy_for_schema() -> Dict[str, Any]:
        try:
            vr = get_vault_root()
            raw_path = vr / "policy.json"
            if raw_path.exists():
                with raw_path.open("r", encoding="utf-8") as f:
                    data = json.load(f)
                # Only expose known keys to keep schema concise
                return {
                    "kind": data.get("kind", "blacklist"),
                    "blocked_binaries": data.get("blocked_binaries", []),
                    "blocked_command_patterns": data.get("blocked_command_patterns", []),
                    "blocked_read_paths_glob": data.get("blocked_read_paths_glob", []),
                    "blocked_write_paths_glob": data.get("blocked_write_paths_glob", []),
                    "maintenance_override_file": data.get("maintenance_override_file", None),
                }
        except Exception:
            pass
        # Fallback minimal summary
        return {
            "kind": "blacklist",
            "blocked_binaries": ["sudo", "su", "pkexec", "doas", "shutdown", "reboot", "halt", "poweroff"],
            "blocked_command_patterns": [
                r"^\\s*rm\\s+-rf\\s+/(\\s|$)",
                r"^\\s*(mkfs\\.|mkswap)\\b",
                r"^\\s*dd\\b.*\\bof=/dev/(sd|nvme|mmcblk)\\w+",
            ],
            "blocked_read_paths_glob": [],
            "blocked_write_paths_glob": ["/boot/**", "/dev/**", "/proc/**", "/sys/**", "/run/**"],
            "maintenance_override_file": "/vault/.unlock_core",
        }

    schemas.append(
        {
            "name": "url_retrieval",
            "description": "Find URLs using a search engine. SLOWER and less convenient than native web search - only use when you need exact URLs to verify sources or fetch verbatim content with page_parser. For general information gathering, prefer native web search (automatic for OpenAI/xAI).",
            "parameters": {
                "type": "object",
                "properties": {
                    "query": {"type": "string", "description": "Search query"},
                    "max_results": {"type": "integer", "default": 10},
                    "site": {
                        "description": "Domain or list of domains to prefer (adds site: filters)",
                        "anyOf": [
                            {"type": "string"},
                            {"type": "array", "items": {"type": "string"}}
                        ]
                    },
                    "time_range": {
                        "type": ["string", "null"],
                        "description": "Optional time range hint (e.g., 'd','w','m','y')",
                    },
                    "include_snippets": {"type": "boolean", "default": True},
                },
                "required": ["query"],
                "additionalProperties": False,
            },
        }
    )

    schemas.append(
        {
            "name": "page_parser",
            "description": "Fetch and parse full page content to Markdown. SLOWER than native web search - only use when you need exact figures, verbatim quotes, or complete article text that native search doesn't provide. For general information, use native web search instead.",
            "parameters": {
                "type": "object",
                "properties": {
                    "url": {"type": "string", "description": "URL to fetch and parse"},
                    "urls": {
                        "type": "array",
                        "description": "Multiple URLs to fetch and parse (processed sequentially)",
                        "items": {"type": "string"},
                        "minItems": 1,
                    },
                    "save": {"type": "boolean", "default": False},
                    "max_tokens": {"type": "integer", "default": 8192},
                    "format": {"type": "string", "enum": ["markdown"], "default": "markdown"},
                    "use_cache": {"type": "boolean", "default": True},
                    "force": {"type": "boolean", "default": False},
                },
                "required": [],
                # Provider-safe enforcement that one of 'url' or 'urls' must be present.
                # Use full object branches so providers (OpenAI/Google) validate correctly.
                "oneOf": [
                    {
                        "type": "object",
                        "properties": {
                            "url": {"type": "string", "description": "URL to fetch and parse"},
                            "urls": {
                                "type": "array",
                                "description": "Multiple URLs to fetch and parse (processed sequentially)",
                                "items": {"type": "string"},
                                "minItems": 1,
                            },
                            "save": {"type": "boolean", "default": False},
                            "max_tokens": {"type": "integer", "default": 8192},
                            "format": {"type": "string", "enum": ["markdown"], "default": "markdown"},
                            "use_cache": {"type": "boolean", "default": True},
                            "force": {"type": "boolean", "default": False},
                        },
                        "required": ["url"],
                        "additionalProperties": False,
                    },
                    {
                        "type": "object",
                        "properties": {
                            "url": {"type": "string", "description": "URL to fetch and parse"},
                            "urls": {
                                "type": "array",
                                "description": "Multiple URLs to fetch and parse (processed sequentially)",
                                "items": {"type": "string"},
                                "minItems": 1,
                            },
                            "save": {"type": "boolean", "default": False},
                            "max_tokens": {"type": "integer", "default": 8192},
                            "format": {"type": "string", "enum": ["markdown"], "default": "markdown"},
                            "use_cache": {"type": "boolean", "default": True},
                            "force": {"type": "boolean", "default": False},
                        },
                        "required": ["urls"],
                        "additionalProperties": False,
                    },
                ],
                "additionalProperties": False,
            },
        }
    )

    # read_part_of_file, search_in_file, smart_files_search removed — use 'sh' with grep/rg/head/tail.


    # Shell tool (preferred file/discovery ops) — Bash
    schemas.append(
        {
            "name": "bash",
            "description": (
                "Run non-interactive Bash commands from the vault workspace (CWD). See Code Ops Safety below. Security policy blocks dangerous patterns and truncates large outputs, saving full logs under vault/sh_logs."
            ),
            "x_prompt_summary": "Run Bash one-liners from the vault; project root is read-only and risky commands are blocked.",
            "parameters": {
                "type": "object",
                "properties": {
                    "command": {
                        "type": "string",
                        "description": "Command to run. Executes with CWD in the vault. See Code Ops Safety below for edit workflow. Convenience: '/clones/...' and '../clones/...' are normalized to THEO_CLONES_ROOT.",
                    },
                    "timeout": {
                        "type": ["integer", "null"],
                        "description": "Timeout in milliseconds (optional; clamped by server policy)",
                    },
                    "stdin": {
                        "type": ["string", "null"],
                        "description": "Standard input to pass to the process (optional)",
                    },
                    "env": {
                        "type": ["array", "null"],
                        "description": "Environment variables as list of 'KEY=VALUE' strings (optional; filtered allowlist: THEO_*, PYTHON*, PATH, LC_ALL, LANG). Predefined: THEO_VAULT_ROOT, THEO_CLONES_ROOT, THEO_PROJECT_ROOT.",
                        "items": {"type": "string"},
                    },
                },
                "required": ["command"],
                "additionalProperties": False,
            },
            # Non-standard extension for model visibility
            "x-policy": _sh_policy_for_schema(),
        }
    )

    # Removed granular edit helpers: edit_file_line, replace_in_file, insert_at_line (use update_file)

    # Web room tools (only manually_send_message is exposed)

    schemas.append(
        {
            "name": "list_rooms",
            "description": "List all available rooms (chats) with detailed information including title, message history preview (first and last 3 exchanges), and activity timestamps. Use this to understand available rooms before sending messages or files.",
            "parameters": {
                "type": "object",
                "properties": {
                    "room_id": {
                        "type": "string",
                        "description": "Optional specific room ID to get details for. If omitted, lists all rooms.",
                    },
                },
                "required": [],
                "additionalProperties": False,
            },
        }
    )

    schemas.append(
        {
            "name": "manually_send_message",
            "description": "Send a user-facing message mid-turn or on a schedule without ending the conversation turn. Intended for progress updates, reminders, or notifications. DO NOT use this for your final response; your normal text output is automatically sent to the user.",
            "x_prompt_summary": "Send a message mid-turn (updates/reminders). Do NOT use for final response; normal text is auto-sent.",
            "parameters": {
                "type": "object",
                "properties": {
                    "text": {
                        "type": "string",
                        "description": "Message text to send",
                    },
                    "channel_id": {
                        "type": "string",
                        "description": "Room ID (optional; defaults to the active/current room if not provided). For tasks, automatically uses the task's dedicated room if omitted.",
                    },
                    "delivery": {
                        "type": "string",
                        "enum": ["default", "inbox"],
                        "description": "Delivery mode: default (room) or inbox feed",
                    },
                    "schedule": {
                        "type": ["object", "null"],
                        "description": "Optional schedule for future delivery (requires start_time)",
                        "properties": {
                            "start_time": {"type": "string", "description": "When to send (friendly formats accepted)"},
                            "recurrence": {"type": ["string", "null"], "description": "Cron expression for recurrence (optional)"}
                        },
                        "required": ["start_time"],
                        "additionalProperties": False
                    },
                    "embed": {
                        "type": "boolean",
                        "description": "Send as an embed (default: false)",
                    },
                    "run_id": {
                        "type": "string",
                        "description": "Run identifier to associate with this message (optional)",
                    },
                    "file_path": {
                        "type": "string",
                        "description": "Vault-relative or absolute path within the vault to send as an attachment",
                    },
                    "file_paths": {
                        "type": "array",
                        "items": {"type": "string"},
                        "description": "Multiple vault paths to send as attachments",
                    },
                    "file_data_base64": {
                        "type": "string",
                        "description": "Base64-encoded file bytes to save and send as an attachment",
                    },
                    "filename": {
                        "type": "string",
                        "description": "Filename to store when sending base64 content",
                    },
                    "mime_type": {
                        "type": "string",
                        "description": "MIME type hint for attachment (optional)",
                    },
                    "display_inline": {
                        "type": "boolean",
                        "description": "If true and attachment is Markdown/image, include inline content",
                    },
                },
                "required": ["text"],
                "additionalProperties": False,
            },
        }
    )

    schemas.append(
        {
            "name": "send_file",
            "description": "Send a single existing file from the vault to the user as a downloadable attachment. IMPORTANT: This tool is ONLY for sending files that already exist - it does NOT create, write, or modify files. To create a file first, use the 'bash' tool (e.g., 'cat > file.html' or 'echo > file.txt'), then use send_file to deliver it. Each send creates a unique attachment (if you call this multiple times with the same file, numbered copies like file_1.html, file_2.html will be created to prevent overwrites). Call this tool once per file you want to send.",
            "parameters": {
                "type": "object",
                "properties": {
                    "path": {
                        "type": "string",
                        "description": "Vault-relative or absolute path (within the vault) to the existing file to send.",
                    },
                    "room_id": {
                        "type": "string",
                        "description": "Room ID to target (defaults to the current conversation room).",
                    },
                },
                "required": ["path"],
                "additionalProperties": False,
            },
        }
    )

    # Image generation (InvokeAI)
    schemas.append(
        {
            "name": "generate_image",
            "description": (
                "Render a Juggernaut XIII: Ragnarok image via InvokeAI. Provide a richly detailed positive prompt plus a decisive negative prompt, overriding resolution, steps, or CFG only when the human requests it. Base defaults follow the config (portrait 832x1216, about 35 steps, CFG 3-6) and summarize key settings and the seed when you deliver the outputs."
            ),
            "x_prompt_summary": "Render an image with InvokeAI using the given prompt and optional workflow overrides.",
            "parameters": {
                "type": "object",
                "properties": {
                    "prompt": {
                        "type": "string",
                        "description": "Describe the subject and style clearly (e.g., lens, film stock, lighting, composition)."
                    },
                    "auto_send": {
                        "type": ["boolean", "null"],
                        "default": True,
                        "description": "Auto-send the generated image to the user when true (preferred)."
                    },
                    "dont_send": {
                        "type": ["boolean", "null"],
                        "default": False,
                        "description": "Deprecated: Do not auto-send the image when true. Prefer 'auto_send'."
                    },
                    "timeout_seconds": {
                        "type": ["integer", "null"],
                        "description": "Set a per-call timeout in seconds (default 180).",
                        "default": 180,
                        "minimum": 1
                    },
                    "negative_prompt": {
                        "type": ["string", "null"],
                        "description": "Block artifacts and disallowed content (backend defaults to the configured Juggernaut negative prompt: 'bad hands, extra fingers, missing fingers, mutated hands, deformed, bad anatomy, bad eyes, fake eyes, lowres, worst quality, blurry, watermark, text, logo, cgi, 3d render, airbrushed, disfigured, extra limbs'). Add extra blockers relevant to the request.",
                        "default": None
                    },
                    "steps": {
                        "type": ["integer", "null"],
                        "description": "Sampling steps (leave blank to use the automation config).",
                        "default": None,
                        "minimum": 1
                    },
                    "cfg_scale": {
                        "type": ["number", "null"],
                        "description": "Prompt adherence.",
                        "default": 4.5,
                        "minimum": 0
                    },
                    "width": {
                        "type": ["integer", "null"],
                        "description": "Image width in pixels (default 832). Use multiples of 64.",
                        "default": 832,
                        "minimum": 64
                    },
                    "height": {
                        "type": ["integer", "null"],
                        "description": "Image height in pixels (default 1216). Use multiples of 64.",
                        "default": 1216,
                        "minimum": 64
                    },
                    "seed": {
                        "type": ["integer", "null"],
                        "description": "Seed (use -1 for random).",
                        "default": 322073574
                    }
                },
                "required": ["prompt"],
                "additionalProperties": False,
            },
        }
    )

    # Image analysis (OpenAI vision)
    schemas.append(
        {
            "name": "analyze_image",
            "description": "Analyze an image using OpenAI's vision model and return a detailed text description. CRITICAL: When you see 'Image: filename - URL: /uploads/...' in the attachments section, you MUST call this tool immediately with that URL to see what's in the image. Without calling this tool, you cannot see image content. You can also use it to analyze generated images or any vault image. The description is saved as {image_name}_description.md alongside the image.",
            "x_prompt_summary": "Analyze an image file and return a detailed text description using vision AI. Must be called to see image attachments.",
            "parameters": {
                "type": "object",
                "properties": {
                    "image": {
                        "type": "string",
                        "description": "The URL or path from the attachment info. When you see 'URL: /uploads/room_id/file.png' in the attachments, use that exact URL string. Also accepts vault-relative paths and absolute vault paths. Supports PNG, JPEG, WEBP, and non-animated GIF."
                    },
                    "prompt": {
                        "type": ["string", "null"],
                        "description": "Optional custom analysis prompt. Defaults to a detailed descriptive analysis. The system automatically appends 'respond only with a textual description in markdown format' to all prompts.",
                        "default": None
                    },
                    "detail": {
                        "type": ["string", "null"],
                        "enum": ["low", "high", "auto"],
                        "description": "Image processing detail level. 'low' is faster/cheaper (512px), 'high' provides better understanding, 'auto' lets the model decide.",
                        "default": "auto"
                    },
                    "save_markdown": {
                        "type": ["boolean", "null"],
                        "description": "Save the analysis as {image_name}_description.md alongside the image for indexing and future reference.",
                        "default": True
                    },
                    "dont_send": {
                        "type": ["boolean", "null"],
                        "description": "When false (default), the analysis result is auto-sent to the chat. Set true to suppress auto-send and only return as tool output.",
                        "default": False
                    }
                },
                "required": ["image"],
                "additionalProperties": False,
            },
        }
    )

    # Coding Tools
    schemas.append(
        {
            "name": "read_code_structure",
            "description": "Read a Python file’s structure quickly using AST for safe inspection (prefer clone paths).",
            "parameters": {
                "type": "object",
                "properties": {
                    "file_path": {
                        "type": "string",
                        "description": "Path to the Python file (e.g., 'clones/clone_YYYYMMDD_HHMMSS/path/to/file.py'). If you pass '/clones/...' or a project_root absolute that doesn't exist, it is remapped to THEO_CLONES_ROOT.",
                    },
                },
                "required": ["file_path"],
                "additionalProperties": False,
            },
        }
    )



    schemas.append(
        {
            "name": "list_clones",
            "description": "List all available code clones with metadata (file count, size, timestamps). Use this to discover which clones exist before referencing them in other tools.",
            "parameters": {
                "type": "object",
                "properties": {},
                "required": [],
                "additionalProperties": False,
            },
        }
    )

    schemas.append(
        {
            "name": "run_quick_lint",
            "description": "Run a quick Flake8 check for style and syntax errors. Clone only: lint files inside a clone (clones/...). Do not lint the live project root.",
            "parameters": {
                "type": "object",
                "properties": {
                    "file_path": {
                        "type": "string",
                        "description": "Path to the file to lint. Prefer clone-relative paths like 'clones/clone_YYYYMMDD_HHMMSS/path/to/file.py'.",
                    },
                },
                "required": ["file_path"],
                "additionalProperties": False,
            },
        }
    )

    schemas.append(
        {
            "name": "execute_tests",
            "description": "Execute unit/functional tests with pytest. Clone only: run tests from within a clone directory (e.g., test_pattern='clones/clone_.../tests/'). Avoid running against the live project root.",
            "parameters": {
                "type": "object",
                "properties": {
                    "test_pattern": {
                        "type": "string",
                        "description": "Test file pattern or specific test to run (default: 'tests/')",
                    },
                    "verbose": {
                        "type": "boolean",
                        "description": "Whether to run in verbose mode (default: false)",
                    },
                    "allow_project_root": {
                        "type": "boolean",
                        "default": False,
                        "description": "Override safety and allow running tests from the live project root (not recommended).",
                    },
                },
                "required": [],
                "additionalProperties": False,
            },
        }
    )

    schemas.append(
        {
            "name": "create_diff",
            "description": "Generate a unified diff to preview changes before applying them. Clone only: generate diffs for files inside a clone (clones/...). Never diff and patch the live project root directly.",
            "parameters": {
                "type": "object",
                "properties": {
                    "file_path": {
                        "type": "string",
                        "description": "Path to the file to diff. Prefer clone-relative paths like 'clones/clone_YYYYMMDD_HHMMSS/path/to/file.py'.",
                    },
                    "proposed_changes": {
                        "type": "string",
                        "description": "Proposed changes to compare against current file",
                    },
                },
                "required": ["file_path", "proposed_changes"],
                "additionalProperties": False,
            },
        }
    )

    # Project tools removed (use tasks/notifications instead)

    schemas.append(
        {
            "name": "create_task",
            "description": "Create a scheduled follow-up task that Theo will run later. Tasks enable self-prompting, background work, temporal reminders, and task chains.",
            "x_prompt_summary": "Schedule a task with name, details, start time, and optional recurrence/silent flags. Tasks can run in any room - control output destination with room_id parameter.",
            "parameters": {
                "type": "object",
                "properties": {
                    "name": {
                        "type": "string",
                        "description": "Name of the task",
                    },
                    "details": {
                        "type": "string",
                        "description": "What should be done",
                    },
                    "start_time": {
                        "type": "string",
                        "description": "When to start. Supported formats: ISO 8601 ('2025-10-24T14:30:00'), dates ('2025-10-24'), times ('14:30' or '2:30pm'), relative times ('30 seconds', '5 minutes', '2 hours', '1 day'), or 'now'. For recurring tasks, use full words (e.g., '30 minutes' not '30m'). Do not use cron expressions here - use 'recurrence' parameter instead.",
                    },
                    "recurrence": {
                        "type": ["string", "null"],
                        "description": "Optional 5-field cron expression for recurring tasks (minute hour day month weekday). Examples: '0 9 * * *' (daily 9am), '0 9 * * 1-5' (weekdays 9am), '30 14 * * 0' (Sundays 2:30pm), '0 */6 * * *' (every 6 hours). Leave null for one-time tasks. Must be exactly 5 fields - no seconds field.",
                    },
                    "silent": {
                        "type": "boolean",
                        "description": "Controls inbox visibility. false (default): task output appears in Tasks Inbox AND task room. true: task output appears only in task room (hidden from inbox).",
                    },
                    "room_id": {
                        "type": ["string", "null"],
                        "description": "Controls where task outputs appear. null (default): From standard room → duplicates room (new isolated task room with conversation context). From task room → outputs to same room (chains related tasks). 'same': Outputs to current room (standard or task). 'new_room': Creates new empty task room. '<room_id>': Outputs to specific room by ID.",
                    },
                },
                "required": ["name", "details", "start_time"],
                "additionalProperties": False,
            },
        }
    )

    schemas.append(
        {
            "name": "update_task",
            "description": "Update task fields or status. Tasks that fail repeatedly (5 times) are automatically marked 'needs_attention' - fix them and set status back to 'active' to retry.",
            "parameters": {
                "type": "object",
                "properties": {
                    "task_id": {
                        "type": "string",
                        "description": "ID of the task to update",
                    },
                    "name": {
                        "type": "string",
                        "description": "New name for the task",
                    },
                    "details": {
                        "type": "string",
                        "description": "New details for the task",
                    },
                    "status": {
                        "type": "string",
                        "enum": ["active", "completed", "archived", "needs_attention"],
                        "description": "New status. 'needs_attention' is auto-set when tasks fail 5+ times. Change to 'active' after fixing to retry.",
                    },
                    "room_id": {
                        "type": ["string", "null"],
                        "description": "Change where future task executions output. Use room ID, 'same', or 'new_room'.",
                    },
                    "delivery_mode": {
                        "type": ["string", "null"],
                        "enum": ["room_only", "room_and_inbox"],
                        "description": "Override delivery mode. 'room_only': task room only. 'room_and_inbox': also appears in Tasks Inbox. Normally controlled by 'silent' flag.",
                    },
                },
                "required": ["task_id"],
                "additionalProperties": False,
            },
        }
    )

    schemas.append(
        {
            "name": "list_task",
            "description": "List tasks, optionally filtered, and include recent completions",
            "parameters": {
                "type": "object",
                "properties": {
                    "task_id": {
                        "type": "string",
                        "description": "Specific task ID to get info for (optional)",
                    },
                    "status": {
                        "type": "string",
                        "enum": ["active", "completed", "archived", "all"],
                        "description": "Optional status filter",
                    },
                    "include_recent_completed": {
                        "type": "boolean",
                        "description": "When true, include the most recently completed tasks in the response",
                        "default": False,
                    },
                    "recent_completed_limit": {
                        "type": "integer",
                        "minimum": 1,
                        "maximum": 50,
                        "description": "Maximum number of completed tasks to include when include_recent_completed is true",
                    },
                },
                "required": [],
                "additionalProperties": False,
            },
        }
    )


    # Analysis tool removed per spec (use 'sh')

    # Web Search Tools (parallel-capable, Perplexity-powered)
    # Hide web_search for providers with native web search (OpenAI, xAI)
    # Google uses 'web_search' tool as a proxy to native grounding when other tools are present.
    if provider not in ("openai", "xai"):
        schemas.append(
            {
                "name": "web_search",
                "description": "Search the web using Perplexity's Search API. Accepts a single 'query' or multiple 'queries' (parallel, max 5). Supports filtering by recency, domain, language, and country. For exact citations or verbatim text, use url_retrieval then page_parser.",
                "parameters": {
                    "type": "object",
                    "properties": {
                        "query": {
                            "type": "string",
                            "description": "Single search query",
                        },
                        "queries": {
                            "type": "array",
                            "items": {"type": "string"},
                            "description": "Multiple search queries for batch search (max 5)",
                        },
                        "max_results": {
                            "type": "integer",
                            "minimum": 1,
                            "maximum": 20,
                            "description": "Number of results per query (default 10)",
                        },
                        "recency": {
                            "type": "string",
                            "enum": ["day", "week", "month", "year"],
                            "description": "Filter results by time period",
                        },
                        "country": {
                            "type": "string",
                            "description": "ISO 2-letter country code for regional filtering (e.g., 'US', 'GB', 'DE')",
                        },
                        "domains": {
                            "type": "array",
                            "items": {"type": "string"},
                            "description": "Domains to include or exclude (prefix with '-' to exclude, e.g., '-reddit.com'). Max 20.",
                        },
                        "languages": {
                            "type": "array",
                            "items": {"type": "string"},
                            "description": "ISO 639-1 language codes (e.g., ['en', 'fr']). Max 10.",
                        },
                    },
                    "required": [],
                    "additionalProperties": False,
                },
            }
        )

    # Code Agent Tools
    schemas.append(
        {
            "name": "code_agent",
            "description": "Run the Codex CLI autonomously inside a clone. Only operates within 'clones/...' directories. Returns stdout/stderr as tool output. IMPORTANT: Always check 'Files Modified' count in output and use preview_patch to verify changes before applying. Code agents can report success without making actual file modifications.",
            "parameters": {
                "type": "object",
                "properties": {
                    "prompt": {
                        "type": "string",
                        "description": "Prompt to pass to the Codex CLI",
                    },
                    "clone_path": {
                        "type": "string",
                        "description": "Clone path like 'clones/clone_YYYYMMDD_HHMMSS' where Codex will operate (required)",
                    },
                    "timeout_seconds": {
                        "type": "integer",
                        "description": "Max seconds to allow Codex to run (default 900)",
                        "default": 900,
                    },
                },
                "required": ["prompt", "clone_path"],
                "additionalProperties": False,
            },
        }
    )

    # Self-patching Tools (create_backup removed; handled automatically)

    schemas.append(
        {
            "name": "create_clone",
            "description": "Create a clone of the current codebase for safe modifications",
            "parameters": {
                "type": "object",
                "properties": {},
                "required": [],
                # Allow extra keys for LLM quirks; executor ignores them
                "additionalProperties": True,
            },
        }
    )

    schemas.append(
        {
            "name": "apply_patch",
            "description": "Apply changes from clone to main codebase and restart",
            "parameters": {
                "type": "object",
                "properties": {
                    "clone_path": {
                        "type": "string",
                        "description": "Path to the clone directory to apply patches from (e.g., 'clones/clone_20250802_182933')",
                    },
                    "silent_continuation": {
                        "type": "boolean",
                        "description": "If true, suppress the post‑reboot continuation assistant reply (still append a hidden wake‑up status). Defaults to false (visible continuation).",
                    },
                    "target_room": {
                        "type": "string",
                        "description": "Optional room id hint for where to resume after reboot. Defaults to the most recently active non‑default room.",
                    },
                },
                "required": ["clone_path"],
                "additionalProperties": False,
            },
        }
    )

    schemas.append(
        {
            "name": "preview_patch",
            "description": "Preview diffs between a clone and the running codebase without applying",
            "parameters": {
                "type": "object",
                "properties": {
                    "clone_path": {
                        "type": "string",
                        "description": "Path to the clone directory (e.g., 'clones/clone_YYYYMMDD_HHMMSS')",
                    },
                    "max_diffs": {
                        "type": "integer",
                        "description": "Maximum number of file diffs to include (default 200)",
                    },
                    "include_deletions": {
                        "type": "boolean",
                        "description": "Include files that exist in live but not in clone (default true)",
                    },
                    "extensions": {
                        "type": "array",
                        "items": {"type": "string"},
                        "description": "Optional list of file extensions to consider",
                    },
                },
                "required": ["clone_path"],
                "additionalProperties": False,
            },
        }
    )


    # Info Tools (memory only)

    schemas.append(
        {
            "name": "add_memory",
            "description": "Add a Theo memory with importance ranking. Theo memories are your *chosen* memories. To be used for preferences, learned rules, and other important context. Importance 100 means it will always be retrieved.",
            "parameters": {
                "type": "object",
                "properties": {
                    "memory_string": {
                        "type": "string",
                        "description": "The memory content to store",
                    },
                    "importance": {
                        "type": "integer",
                        "minimum": 0,
                        "maximum": 100,
                        "description": "Importance ranking from 0-100",
                    },
                },
                "required": ["memory_string", "importance"],
                "additionalProperties": False,
            },
        }
    )

    schemas.append(
        {
            "name": "update_memory",
            "description": "Update an existing Theo memory. Provide memory_string to update content, importance to update ranking, or both. At least one field must be updated.",
            "parameters": {
                "type": "object",
                "properties": {
                    "memory_id": {
                        "type": "string",
                        "description": "ID of the memory to update",
                    },
                    "memory_string": {
                        "type": "string",
                        "description": "New memory content (optional, only if updating content)",
                    },
                    "importance": {
                        "type": "integer",
                        "minimum": 0,
                        "maximum": 100,
                        "description": "New importance ranking from 0-100 (optional, only if updating importance)",
                    },
                },
                "required": ["memory_id"],
                "additionalProperties": False,
            },
        }
    )

    schemas.append(
        {
            "name": "delete_memory",
            "description": "Delete a Theo memory",
            "parameters": {
                "type": "object",
                "properties": {
                    "memory_id": {
                        "type": "string",
                        "description": "ID of the memory to delete",
                    },
                },
                "required": ["memory_id"],
                "additionalProperties": False,
            },
        }
    )

    # Enforce second-person descriptions on all tools
    for s in schemas:
        try:
            if isinstance(s, dict) and "description" in s:
                s["description"] = _to_second_person(s.get("description", ""))
            # Also normalize nested parameter/property descriptions
            params = s.get("parameters")
            if isinstance(params, dict):
                _normalize_param_descriptions(params)
        except Exception:
            # Non-fatal; leave description as-is if normalization fails
            pass

    # Only cache if no provider filtering was applied
    if provider is None:
        _SCHEMAS_CACHE = schemas
    return schemas


def generate_tools_info(provider: Optional[str] = None) -> str:
    """Generate concise guidance for tool usage (schemas provide full details).
    
    Args:
        provider: Optional provider name to filter tool guidance (e.g., 'xai', 'openai')
    """
    lines = []
    # General guidance
    lines.append("Tool Usage Guidance:")
    lines.append("")
    lines.append("Code Ops Safety:")
    lines.append("- NEVER modify the live running codebase directly. All self-updates use: create_clone → edit/test in clone → preview_patch → apply_patch.")
    lines.append("- Bash tool: CWD is vault. Project root is write-protected (reads allowed). Global FS accessible.")
    lines.append("- Code agent: Must target clone_path only, never live project root.")
    lines.append("")
    # Web tools guidance (provider-aware via schema filtering)
    if provider in ("xai", "openai"):
        lines.append("- Web research: Native web search is FAST and automatic—use it for broad discovery. Reach for url_retrieval + page_parser only when you need exact figures or cite a specific page (slower but precise).")
    else:
        lines.append("- Web research: Use web_search for broad discovery. Switch to url_retrieval + page_parser only when you need exact figures or a precise citation (slower but precise).")
    # Analysis guidance removed; use 'sh' when code execution is necessary.
    lines.append("- Full, strict tool schemas are available via function-calling; follow parameter requirements exactly.")
    # Forms guidance (explicit, concise, actionable)
    lines.append("Form Tools:")
    lines.append("- Workflow: define_form → show_form (inline, non-blocking) → user submits (triggers a user message) → list_form_submissions to review → save_form_submission if needed programmatically.")
    lines.append("- Only one active form at a time. Do NOT call show_form again while a form is already visible — wait for user submission or dismissal.")
    lines.append("- Avoid redefining the same form repeatedly; define once, then show or reuse.")
    # (kept) Anti-looping guidance plus no re-showing when active
    lines.append("- Prefill/answers formats: EITHER an object {field_id: value} OR an array of {id, value} pairs.")
    lines.append("- Field types: text | textarea | number | email | url | select | multiselect | checkbox | radio | date. For select/multiselect/radio, include options.")
    lines.append("- Example define_form: {form: {form_id:'feedback_v1', title:'Feedback', version:1, fields:[{id:'name',label:'Your name',type:'text'},{id:'rating',label:'Rating',type:'radio',required:true,options:['1','2','3','4','5']}]} }")
    lines.append("- Example show_form: {form_id:'feedback_v1', prefill:{name:'Ada'}}")
    lines.append("")
    lines.append("Tasks & Rooms:")
    lines.append("- Leave room_id blank when creating tasks—the system automatically duplicates standard rooms (preserving context) or reuses task rooms (for chaining).")
    lines.append("- Only set room_id when user explicitly requests specific room handling ('new_room' for empty room, 'same' to reuse current, or specific room ID).")
    lines.append("- Set silent=true to keep runs hidden (delivery defaults to room_only); leave false to surface deliverables in the Tasks Inbox via room_and_inbox.")
    lines.append("- Override visibility with delivery_mode: room_only keeps the transcript private, room_and_inbox also posts to the inbox feed.")
    lines.append("- Task rooms stay out of the main sidebar—the user reaches them through the Tasks modal and inbox feed.")
    lines.append("- Auto titles use the first assistant output; start with a concise summary so the room renames cleanly.")
    lines.append("")
    lines.append("Code Agent:")
    lines.append("- Good uses: auto-refactors, adding tests, multi-file edits, scaffolding modules. Provide explicit instructions with acceptance criteria.")
    lines.append("- After running: Verify changes by reading modified files and executing tests. Use tool output (stdout/stderr) as a report, not proof of success.")
    return "\n".join(lines)


def generate_tools_docstrings(include_parameters: bool = False) -> str:
    """Generate a stable, per-tool docstrings block for inclusion in prompts.

    The block lists every available tool with its human-readable description.
    Optionally, include a compact summary of top-level parameters. This is kept
    terse to avoid excessive token usage while still being informative.

    Args:
        include_parameters: When True, append a short parameter list for each tool.

    Returns:
        A deterministic string that can be embedded in prompts before dynamic sections.

    Example:
        Available Tools (Docstrings):
        - add_working_memory — Capture a brief private note that persists for a few exchanges.
        - sh — Run non‑interactive shell commands in the vault (CWD = vault).
    """
    try:
        schemas = generate_tool_schemas()
    except Exception:
        return ""

    lines: List[str] = []
    lines.append("Available Tools (Docstrings):")

    for tool in schemas:
        name = tool.get("name", "unknown_tool")
        summary = tool.get("x_prompt_summary")
        if not summary:
            desc = str(tool.get("description", ""))
            cleaned = " ".join(desc.strip().split())
            # Use the first sentence (or entire cleaned text if no period)
            period_index = cleaned.find(".")
            summary = cleaned if period_index == -1 else cleaned[: period_index + 1]
        desc_one_line = " ".join(str(summary).strip().split())
        lines.append(f"- {name} — {desc_one_line}")

        if include_parameters:
            try:
                params = tool.get("parameters") or {}
                if isinstance(params, dict) and params.get("type") == "object":
                    props = list((params.get("properties") or {}).items())
                    if props:
                        # Keep at most the first 6 parameters to control length
                        preview = []
                        for i, (pname, pobj) in enumerate(props):
                            if i >= 6:
                                preview.append("…")
                                break
                            ptype = pobj.get("type", "any") if isinstance(pobj, dict) else "any"
                            pdesc = pobj.get("description", "") if isinstance(pobj, dict) else ""
                            pdesc = " ".join(str(pdesc).strip().split())
                            preview.append(f"    • {pname}: {ptype} — {pdesc}")
                        lines.extend(preview)
            except Exception:
                # Ignore parameter rendering errors for robustness
                pass

    return "\n".join(lines)
