"""
JSON Schema -> Pydantic Model (runtime) converter

Supports a practical subset for application schemas:
- type: object with properties/required
- type: array with items
- type: string|number|integer|boolean
- enum for strings/numbers

This is sufficient for our scribe/dynamic optimizer structured outputs and
tool outputs.
"""

from __future__ import annotations

from typing import Any, Dict, Tuple, Type
from enum import Enum

from pydantic import BaseModel, Field, create_model


def _enum_from_schema(name: str, schema: Dict[str, Any]) -> Type[Enum]:
    values = schema.get("enum") or []
    if not isinstance(values, list) or not values:
        # Fallback to a simple string enum to avoid errors
        values = ["A", "B"]
    members = {str(v).upper().replace(" ", "_")[:50]: v for v in values}
    return Enum(name, members)  # type: ignore[arg-type]


def _type_from_schema(name: str, schema: Dict[str, Any]) -> Tuple[Any, Any]:
    t = (schema.get("type") or "object").lower()

    # Enum overrides simple type
    if isinstance(schema.get("enum"), list):
        return _enum_from_schema(f"{name}Enum", schema), Field(description=schema.get("description", ""))

    if t == "string":
        return str, Field(description=schema.get("description", ""))
    if t == "integer":
        return int, Field(description=schema.get("description", ""))
    if t == "number":
        return float, Field(description=schema.get("description", ""))
    if t == "boolean":
        return bool, Field(description=schema.get("description", ""))
    if t == "array":
        # Recurse for items
        items = schema.get("items", {}) if isinstance(schema.get("items"), dict) else {}
        inner_type, _ = _type_from_schema(f"{name}Item", items)
        # Pydantic v2: use typing for list type
        from typing import List as _List

        return _List[inner_type], Field(description=schema.get("description", ""))  # type: ignore[index]
    # Default: object
    return _create_model_from_schema(name, schema), Field(description=schema.get("description", ""))


def _create_model_from_schema(name: str, schema: Dict[str, Any]) -> Type[BaseModel]:
    properties: Dict[str, Any] = schema.get("properties", {}) if isinstance(schema.get("properties"), dict) else {}
    required = set(schema.get("required", []) if isinstance(schema.get("required"), list) else [])

    fields: Dict[str, Tuple[Any, Any]] = {}
    for prop_name, prop_schema in properties.items():
        if not isinstance(prop_schema, dict):
            continue
        field_type, field_info = _type_from_schema(prop_name.capitalize(), prop_schema)
        if prop_name in required:
            fields[prop_name] = (field_type, field_info)
        else:
            from typing import Optional as _Optional

            fields[prop_name] = (_Optional[field_type], field_info)  # type: ignore[index]

    if not fields:
        # Empty object
        return create_model(name, __base__=BaseModel)  # type: ignore[arg-type]
    return create_model(name, __base__=BaseModel, **fields)  # type: ignore[arg-type]


def schema_to_model(name: str, schema: Dict[str, Any]) -> Type[BaseModel]:
    """Public entry: create a Pydantic model class from a JSON Schema dict."""
    return _create_model_from_schema(name, schema)

