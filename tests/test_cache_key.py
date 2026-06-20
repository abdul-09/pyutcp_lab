"""Tests for pyutcp_lab.client.cache_key."""

from __future__ import annotations

from pyutcp_lab.client.cache_key import arguments_fingerprint, cache_key
from pyutcp_lab.core.models import ToolCall


class TestCacheKey:
    def test_includes_tool_name(self) -> None:
        key = cache_key(ToolCall(tool_name="math.add", arguments={"a": 1}))
        assert key.startswith("math.add:")

    def test_argument_order_does_not_matter(self) -> None:
        k1 = cache_key(ToolCall(tool_name="t", arguments={"a": 1, "b": 2}))
        k2 = cache_key(ToolCall(tool_name="t", arguments={"b": 2, "a": 1}))
        assert k1 == k2

    def test_nested_order_does_not_matter(self) -> None:
        k1 = cache_key(ToolCall(tool_name="t", arguments={"o": {"a": 1, "b": 2}}))
        k2 = cache_key(ToolCall(tool_name="t", arguments={"o": {"b": 2, "a": 1}}))
        assert k1 == k2

    def test_different_arguments_differ(self) -> None:
        k1 = cache_key(ToolCall(tool_name="t", arguments={"a": 1}))
        k2 = cache_key(ToolCall(tool_name="t", arguments={"a": 2}))
        assert k1 != k2

    def test_empty_arguments(self) -> None:
        key = cache_key(ToolCall(tool_name="ping"))
        assert key.startswith("ping:")


class TestFingerprint:
    def test_stable(self) -> None:
        assert arguments_fingerprint({"a": 1, "b": 2}) == arguments_fingerprint(
            {"b": 2, "a": 1}
        )

    def test_differs_on_change(self) -> None:
        assert arguments_fingerprint({"a": 1}) != arguments_fingerprint({"a": 2})
