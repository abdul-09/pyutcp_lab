"""Tests for pyutcp_lab.core.models."""

from __future__ import annotations

import pytest
from pydantic import ValidationError as PydanticValidationError

from pyutcp_lab.core.models import (
    Manual,
    Provider,
    Tool,
    ToolCall,
    TransportType,
)


class TestTool:
    def test_minimal_tool(self) -> None:
        tool = Tool(name="search.web")
        assert tool.name == "search.web"
        assert tool.description == ""
        assert tool.tags == ()
        assert tool.namespace == "search"

    def test_namespace_of_unqualified_name(self) -> None:
        assert Tool(name="ping").namespace == "ping"

    @pytest.mark.parametrize(
        "bad",
        ["", "Search.Web", "search web", "search..web", ".search", "search.", "a-b"],
    )
    def test_invalid_names_rejected(self, bad: str) -> None:
        with pytest.raises(PydanticValidationError):
            Tool(name=bad)

    def test_long_name_rejected(self) -> None:
        with pytest.raises(PydanticValidationError):
            Tool(name="a" * 129)

    def test_tags_normalized_and_deduped(self) -> None:
        tool = Tool(name="t", tags=("  Web ", "web", "API", "api", ""))
        assert tool.tags == ("web", "api")

    def test_negative_response_size_rejected(self) -> None:
        with pytest.raises(PydanticValidationError):
            Tool(name="t", average_response_size=-1)

    def test_valid_response_size_accepted(self) -> None:
        tool = Tool(name="t", average_response_size=2048)
        assert tool.average_response_size == 2048

    def test_tool_is_frozen(self) -> None:
        tool = Tool(name="t")
        with pytest.raises(PydanticValidationError):
            tool.name = "other"  # type: ignore[misc]

    def test_extra_fields_forbidden(self) -> None:
        with pytest.raises(PydanticValidationError):
            Tool(name="t", surprise=1)  # type: ignore[call-arg]


class TestProvider:
    def test_http_provider(self) -> None:
        p = Provider(name="api", transport=TransportType.HTTP, url="http://x")
        assert p.transport is TransportType.HTTP
        assert p.url == "http://x"

    def test_transport_from_string(self) -> None:
        p = Provider(name="api", transport="cli", command=("echo", "hi"))
        assert p.transport is TransportType.CLI

    def test_invalid_transport_rejected(self) -> None:
        with pytest.raises(PydanticValidationError):
            Provider(name="api", transport="carrier-pigeon")


class TestManual:
    def test_tool_names(self) -> None:
        m = Manual(
            provider=Provider(name="p", transport=TransportType.HTTP),
            tools=(Tool(name="a"), Tool(name="b")),
        )
        assert m.tool_names() == ("a", "b")

    def test_duplicate_tool_names_rejected(self) -> None:
        with pytest.raises(PydanticValidationError):
            Manual(
                provider=Provider(name="p", transport=TransportType.HTTP),
                tools=(Tool(name="a"), Tool(name="a")),
            )

    def test_empty_manual_allowed(self) -> None:
        m = Manual(provider=Provider(name="p", transport=TransportType.TEXT))
        assert m.tool_names() == ()


class TestToolCall:
    def test_defaults(self) -> None:
        call = ToolCall(tool_name="math.add")
        assert call.arguments == {}

    def test_with_arguments(self) -> None:
        call = ToolCall(tool_name="math.add", arguments={"a": 1, "b": 2})
        assert call.arguments["a"] == 1

    def test_bad_name_rejected(self) -> None:
        with pytest.raises(PydanticValidationError):
            ToolCall(tool_name="Bad Name")
