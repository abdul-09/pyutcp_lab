"""Tests for pyutcp_lab.transports.graphql."""

from __future__ import annotations

import json

import pytest

from pyutcp_lab.core.errors import TransportError
from pyutcp_lab.core.models import Provider, ToolCall, TransportType
from pyutcp_lab.transports.graphql import GraphQLTransport, build_query
from tests.fakes import FakeClock, FakeConnection


def gql_provider() -> Provider:
    return Provider(name="g", transport=TransportType.GRAPHQL, url="http://x")


class TestBuildQuery:
    def test_no_arguments(self) -> None:
        body = build_query("ping", {})
        assert "ping" in body["query"]
        assert body["variables"] == {}

    def test_with_arguments(self) -> None:
        body = build_query("search", {"q": "cats", "limit": 5})
        assert "$q" in body["query"]
        assert "$limit" in body["query"]
        assert body["variables"] == {"q": "cats", "limit": 5}


class TestGraphQLTransport:
    def test_rejects_wrong_transport(self) -> None:
        http = Provider(name="h", transport=TransportType.HTTP, url="http://x")
        with pytest.raises(TransportError):
            GraphQLTransport(http, connect=lambda p: None)  # type: ignore[arg-type,return-value]

    def test_call_unwraps_root_field(self) -> None:
        resp = json.dumps({"data": {"search": [1, 2, 3]}}).encode()
        conn = FakeConnection([resp], FakeClock())
        t = GraphQLTransport(gql_provider(), connect=lambda p: conn)
        result = t.call(ToolCall(tool_name="search", arguments={"q": "x"}))
        assert result == [1, 2, 3]

    def test_errors_raise(self) -> None:
        resp = json.dumps({"errors": [{"message": "boom"}]}).encode()
        conn = FakeConnection([resp], FakeClock())
        t = GraphQLTransport(gql_provider(), connect=lambda p: conn)
        with pytest.raises(TransportError, match="boom"):
            t.call(ToolCall(tool_name="x"))

    def test_bad_json_raises(self) -> None:
        conn = FakeConnection([b"not json"], FakeClock())
        t = GraphQLTransport(gql_provider(), connect=lambda p: conn)
        with pytest.raises(TransportError):
            t.call(ToolCall(tool_name="x"))

    def test_non_object_response_raises(self) -> None:
        conn = FakeConnection([b"[1,2,3]"], FakeClock())
        t = GraphQLTransport(gql_provider(), connect=lambda p: conn)
        with pytest.raises(TransportError):
            t.call(ToolCall(tool_name="x"))

    def test_discover_parses_manual(self) -> None:
        resp = json.dumps(
            {"data": {"utcp": {"tools": [{"name": "g.a"}, {"name": "g.b"}]}}}
        ).encode()
        conn = FakeConnection([resp], FakeClock())
        t = GraphQLTransport(gql_provider(), connect=lambda p: conn)
        assert t.discover().tool_names() == ("g.a", "g.b")

    def test_discover_bad_manual_raises(self) -> None:
        resp = json.dumps({"data": {"utcp": [1, 2]}}).encode()
        conn = FakeConnection([resp], FakeClock())
        t = GraphQLTransport(gql_provider(), connect=lambda p: conn)
        with pytest.raises(TransportError):
            t.discover()

    def test_stream_yields_result(self) -> None:
        resp = json.dumps({"data": {"gen": {"v": 1}}}).encode()
        conn = FakeConnection([resp], FakeClock())
        t = GraphQLTransport(gql_provider(), connect=lambda p: conn)
        assert list(t.stream(ToolCall(tool_name="gen"))) == [{"v": 1}]

    def test_stream_null_yields_nothing(self) -> None:
        resp = json.dumps({"data": {"gen": None}}).encode()
        conn = FakeConnection([resp], FakeClock())
        t = GraphQLTransport(gql_provider(), connect=lambda p: conn)
        assert list(t.stream(ToolCall(tool_name="gen"))) == []

    def test_call_with_deadline(self) -> None:
        from pyutcp_lab.transports.base import Deadline

        resp = json.dumps({"data": {"search": [1]}}).encode()
        clock = FakeClock(start=0.0)
        conn = FakeConnection([resp], clock)
        t = GraphQLTransport(gql_provider(), connect=lambda p: conn)
        d = Deadline.after(30.0, clock=clock.time)
        assert t.call(ToolCall(tool_name="search"), deadline=d) == [1]

    def test_call_scalar_data_returned_as_is(self) -> None:
        # When data is not an object, it is returned unwrapped.
        resp = json.dumps({"data": 42}).encode()
        conn = FakeConnection([resp], FakeClock())
        t = GraphQLTransport(gql_provider(), connect=lambda p: conn)
        assert t.call(ToolCall(tool_name="answer")) == 42
