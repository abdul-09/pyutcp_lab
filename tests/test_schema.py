"""Tests for pyutcp_lab.core.schema."""

from __future__ import annotations

import pytest

from pyutcp_lab.core.schema import (
    ArgumentValidationError,
    SchemaError,
    ensure_valid_arguments,
    validate_arguments,
)


class TestNoSchema:
    def test_none_schema_accepts_anything(self) -> None:
        assert validate_arguments({"x": 1}, None) == []

    def test_empty_schema_accepts_anything(self) -> None:
        assert validate_arguments({"x": 1}, {}) == []

    def test_non_dict_schema_raises(self) -> None:
        with pytest.raises(SchemaError):
            validate_arguments({}, [1, 2])  # type: ignore[arg-type]


class TestTypeChecks:
    def test_string_ok(self) -> None:
        schema = {"type": "object", "properties": {"a": {"type": "string"}}}
        assert validate_arguments({"a": "hi"}, schema) == []

    def test_wrong_type_reported(self) -> None:
        schema = {"type": "object", "properties": {"a": {"type": "string"}}}
        errors = validate_arguments({"a": 5}, schema)
        assert len(errors) == 1
        assert "expected type string" in errors[0]

    def test_bool_is_not_integer(self) -> None:
        schema = {"type": "object", "properties": {"a": {"type": "integer"}}}
        assert validate_arguments({"a": True}, schema) != []

    def test_integer_satisfies_number(self) -> None:
        schema = {"type": "object", "properties": {"a": {"type": "number"}}}
        assert validate_arguments({"a": 3}, schema) == []

    def test_union_type(self) -> None:
        schema = {
            "type": "object",
            "properties": {"a": {"type": ["string", "null"]}},
        }
        assert validate_arguments({"a": None}, schema) == []
        assert validate_arguments({"a": "x"}, schema) == []
        assert validate_arguments({"a": 1}, schema) != []

    def test_unknown_type_raises(self) -> None:
        schema = {"type": "object", "properties": {"a": {"type": "weird"}}}
        with pytest.raises(SchemaError):
            validate_arguments({"a": 1}, schema)


class TestRequiredAndAdditional:
    def test_missing_required(self) -> None:
        schema = {"type": "object", "required": ["city"], "properties": {}}
        errors = validate_arguments({}, schema)
        assert any("missing required" in e for e in errors)

    def test_additional_properties_false(self) -> None:
        schema = {
            "type": "object",
            "properties": {"a": {"type": "string"}},
            "additionalProperties": False,
        }
        errors = validate_arguments({"a": "x", "b": 1}, schema)
        assert any("unexpected field" in e for e in errors)

    def test_additional_properties_allowed_by_default(self) -> None:
        schema = {"type": "object", "properties": {"a": {"type": "string"}}}
        assert validate_arguments({"a": "x", "b": 1}, schema) == []


class TestStringConstraints:
    def test_min_length(self) -> None:
        schema = {"type": "object", "properties": {"a": {"type": "string", "minLength": 2}}}
        assert validate_arguments({"a": "x"}, schema) != []

    def test_max_length(self) -> None:
        schema = {"type": "object", "properties": {"a": {"type": "string", "maxLength": 2}}}
        assert validate_arguments({"a": "xyz"}, schema) != []


class TestNumberConstraints:
    def test_minimum(self) -> None:
        schema = {"type": "object", "properties": {"a": {"type": "integer", "minimum": 1}}}
        assert validate_arguments({"a": 0}, schema) != []

    def test_maximum(self) -> None:
        schema = {"type": "object", "properties": {"a": {"type": "integer", "maximum": 10}}}
        assert validate_arguments({"a": 11}, schema) != []

    def test_in_range(self) -> None:
        schema = {
            "type": "object",
            "properties": {"a": {"type": "integer", "minimum": 1, "maximum": 10}},
        }
        assert validate_arguments({"a": 5}, schema) == []


class TestEnum:
    def test_enum_ok(self) -> None:
        schema = {"type": "object", "properties": {"u": {"enum": ["a", "b"]}}}
        assert validate_arguments({"u": "a"}, schema) == []

    def test_enum_violation(self) -> None:
        schema = {"type": "object", "properties": {"u": {"enum": ["a", "b"]}}}
        assert validate_arguments({"u": "c"}, schema) != []


class TestArray:
    def test_min_items(self) -> None:
        schema = {"type": "object", "properties": {"xs": {"type": "array", "minItems": 2}}}
        assert validate_arguments({"xs": [1]}, schema) != []

    def test_max_items(self) -> None:
        schema = {"type": "object", "properties": {"xs": {"type": "array", "maxItems": 1}}}
        assert validate_arguments({"xs": [1, 2]}, schema) != []

    def test_item_schema_applied(self) -> None:
        schema = {
            "type": "object",
            "properties": {"xs": {"type": "array", "items": {"type": "integer"}}},
        }
        errors = validate_arguments({"xs": [1, "two", 3]}, schema)
        assert any("xs[1]" in e for e in errors)


class TestNested:
    def test_nested_object(self) -> None:
        schema = {
            "type": "object",
            "properties": {
                "loc": {
                    "type": "object",
                    "required": ["lat"],
                    "properties": {"lat": {"type": "number"}},
                }
            },
        }
        assert validate_arguments({"loc": {"lat": 1.5}}, schema) == []
        assert validate_arguments({"loc": {}}, schema) != []


class TestMultipleErrors:
    def test_collects_all_errors(self) -> None:
        schema = {
            "type": "object",
            "required": ["a", "b"],
            "properties": {
                "a": {"type": "string"},
                "b": {"type": "integer"},
            },
        }
        errors = validate_arguments({"a": 1}, schema)
        # 'a' wrong type AND 'b' missing -> at least two errors.
        assert len(errors) >= 2


class TestEnsureValid:
    def test_passes_silently(self) -> None:
        schema = {"type": "object", "properties": {"a": {"type": "string"}}}
        ensure_valid_arguments({"a": "ok"}, schema)  # no raise

    def test_raises_with_errors(self) -> None:
        schema = {"type": "object", "required": ["a"], "properties": {}}
        with pytest.raises(ArgumentValidationError) as exc:
            ensure_valid_arguments({}, schema)
        assert exc.value.errors
