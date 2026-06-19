"""Validation of tool-call arguments against a tool's declared input schema.

A :class:`~pyutcp_lab.core.models.Tool` carries an ``inputs`` mapping that
describes the arguments it accepts. The shape follows a small, practical subset
of JSON Schema: enough to catch the mistakes that actually happen at call time
(missing required fields, wrong types, out-of-range numbers, bad enum values)
without pulling in a full schema engine.

A schema is a dict like::

    {
        "type": "object",
        "required": ["city"],
        "properties": {
            "city": {"type": "string", "minLength": 1},
            "days": {"type": "integer", "minimum": 1, "maximum": 14},
            "units": {"type": "string", "enum": ["metric", "imperial"]},
        },
        "additionalProperties": False,
    }

Validation collects every problem it finds rather than stopping at the first, so
a caller gets the full picture in one pass.
"""

from __future__ import annotations

from typing import Any, Optional

from .errors import UtcpError

# JSON type name -> the Python types that satisfy it. bool is excluded from
# number/integer on purpose: in JSON, true is not 1.
_TYPE_MAP: dict[str, tuple[type, ...]] = {
    "string": (str,),
    "integer": (int,),
    "number": (int, float),
    "boolean": (bool,),
    "array": (list,),
    "object": (dict,),
    "null": (type(None),),
}


class SchemaError(UtcpError):
    """Raised when a schema itself is malformed (not when data fails it)."""


class ArgumentValidationError(UtcpError):
    """Raised when arguments do not satisfy a tool's input schema."""

    def __init__(self, errors: list[str]) -> None:
        self.errors = list(errors)
        joined = "; ".join(errors)
        super().__init__(f"argument validation failed: {joined}")


def _matches_type(value: Any, type_name: str) -> bool:
    expected = _TYPE_MAP.get(type_name)
    if expected is None:
        raise SchemaError(f"unknown schema type {type_name!r}")
    if type_name in ("integer", "number") and isinstance(value, bool):
        return False
    return isinstance(value, expected)


def _validate_value(value: Any, schema: dict[str, Any], path: str) -> list[str]:
    """Validate one value against one (sub)schema, returning error strings."""
    errors: list[str] = []

    declared_type = schema.get("type")
    if declared_type is not None:
        type_names = (
            declared_type if isinstance(declared_type, list) else [declared_type]
        )
        if not any(_matches_type(value, t) for t in type_names):
            errors.append(
                f"{path}: expected type {'/'.join(type_names)}, "
                f"got {type(value).__name__}"
            )
            # If the base type is wrong, range/length checks are meaningless.
            return errors

    if "enum" in schema and value not in schema["enum"]:
        errors.append(f"{path}: {value!r} is not one of {schema['enum']!r}")

    if isinstance(value, str):
        errors.extend(_check_string(value, schema, path))
    if isinstance(value, (int, float)) and not isinstance(value, bool):
        errors.extend(_check_number(value, schema, path))
    if isinstance(value, list):
        errors.extend(_check_array(value, schema, path))
    if isinstance(value, dict):
        errors.extend(_check_object(value, schema, path))

    return errors


def _check_string(value: str, schema: dict[str, Any], path: str) -> list[str]:
    errors: list[str] = []
    min_len = schema.get("minLength")
    max_len = schema.get("maxLength")
    if min_len is not None and len(value) < min_len:
        errors.append(f"{path}: string shorter than minLength {min_len}")
    if max_len is not None and len(value) > max_len:
        errors.append(f"{path}: string longer than maxLength {max_len}")
    return errors


def _check_number(value: float, schema: dict[str, Any], path: str) -> list[str]:
    errors: list[str] = []
    minimum = schema.get("minimum")
    maximum = schema.get("maximum")
    if minimum is not None and value < minimum:
        errors.append(f"{path}: {value} is below minimum {minimum}")
    if maximum is not None and value > maximum:
        errors.append(f"{path}: {value} is above maximum {maximum}")
    return errors


def _check_array(value: list[Any], schema: dict[str, Any], path: str) -> list[str]:
    errors: list[str] = []
    min_items = schema.get("minItems")
    max_items = schema.get("maxItems")
    if min_items is not None and len(value) < min_items:
        errors.append(f"{path}: array has fewer than minItems {min_items}")
    if max_items is not None and len(value) > max_items:
        errors.append(f"{path}: array has more than maxItems {max_items}")
    item_schema = schema.get("items")
    if isinstance(item_schema, dict):
        for i, item in enumerate(value):
            errors.extend(_validate_value(item, item_schema, f"{path}[{i}]"))
    return errors


def _check_object(value: dict[str, Any], schema: dict[str, Any], path: str) -> list[str]:
    errors: list[str] = []
    properties = schema.get("properties", {})
    required = schema.get("required", [])

    for field_name in required:
        if field_name not in value:
            errors.append(f"{path}: missing required field {field_name!r}")

    additional = schema.get("additionalProperties", True)
    if additional is False:
        for key in value:
            if key not in properties:
                errors.append(f"{path}: unexpected field {key!r}")

    for key, sub_value in value.items():
        sub_schema = properties.get(key)
        if isinstance(sub_schema, dict):
            child_path = key if path == "$" else f"{path}.{key}"
            errors.extend(_validate_value(sub_value, sub_schema, child_path))

    return errors


def validate_arguments(
    arguments: dict[str, Any], schema: Optional[dict[str, Any]]
) -> list[str]:
    """Validate a tool's arguments against its input schema.

    Returns a list of human-readable error strings; an empty list means the
    arguments are valid. A ``None`` or empty schema accepts anything.
    """
    if not schema:
        return []
    if not isinstance(schema, dict):
        raise SchemaError("schema must be a dict")
    return _validate_value(arguments, schema, "$")


def ensure_valid_arguments(
    arguments: dict[str, Any], schema: Optional[dict[str, Any]]
) -> None:
    """Validate arguments and raise :class:`ArgumentValidationError` on failure."""
    errors = validate_arguments(arguments, schema)
    if errors:
        raise ArgumentValidationError(errors)
