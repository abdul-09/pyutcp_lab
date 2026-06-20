"""Tests for pyutcp_lab.client.bootstrap.client_from_config."""

from __future__ import annotations

import json

import pytest

from pyutcp_lab.core.config import load_config
from pyutcp_lab.core.errors import TransportError
from pyutcp_lab.core.models import Manual, Provider, Tool, ToolCall
from pyutcp_lab.core.variables import VariableResolver
from pyutcp_lab.client.bootstrap import client_from_config


class DiscoverableTransport:
    """A fake transport that returns a fixed manual and echoes calls."""

    def __init__(self, provider: Provider, tools: tuple[str, ...]) -> None:
        self.provider = provider
        self._tools = tools
        self.seen_headers = dict(provider.headers)

    def discover(self, *, deadline=None) -> Manual:
        return Manual(
            provider=self.provider,
            tools=tuple(Tool(name=t) for t in self._tools),
        )

    def call(self, call: ToolCall, *, deadline=None) -> object:
        return {"tool": call.tool_name, "by": self.provider.name}


def _config(*provider_entries: dict) -> object:
    return load_config(
        json.dumps({"providers": list(provider_entries)}), use_environ=False
    )


class TestClientFromConfig:
    def test_discovers_and_registers(self) -> None:
        config = _config(
            {"name": "weather", "transport": "http", "url": "http://x"}
        )

        def factory(provider: Provider) -> DiscoverableTransport:
            return DiscoverableTransport(provider, ("weather.now", "weather.week"))

        client = client_from_config(config, factory)
        assert set(client.repository.list_providers()) == {"weather"}
        assert client.repository.get_tool("weather.now") is not None
        result = client.call(ToolCall(tool_name="weather.now"))
        assert result == {"tool": "weather.now", "by": "weather"}

    def test_multiple_providers(self) -> None:
        config = _config(
            {"name": "a", "transport": "http", "url": "http://a"},
            {"name": "b", "transport": "http", "url": "http://b"},
        )

        def factory(provider: Provider) -> DiscoverableTransport:
            return DiscoverableTransport(provider, (f"{provider.name}.tool",))

        client = client_from_config(config, factory)
        assert set(client.repository.list_providers()) == {"a", "b"}

    def test_auth_headers_merged_into_provider(self) -> None:
        config = load_config(
            json.dumps(
                {
                    "providers": [
                        {
                            "name": "secure",
                            "transport": "http",
                            "url": "http://x",
                            "auth": {"scheme": "bearer", "token": "${TK}"},
                            "headers": {"X-Base": "1"},
                        }
                    ]
                }
            ),
            overrides={"TK": "tok"},
            use_environ=False,
        )
        captured: dict[str, DiscoverableTransport] = {}

        def factory(provider: Provider) -> DiscoverableTransport:
            t = DiscoverableTransport(provider, ("secure.ping",))
            captured["t"] = t
            return t

        resolver = VariableResolver(overrides={"TK": "tok"}, use_environ=False)
        client_from_config(config, factory, resolver=resolver)
        headers = captured["t"].seen_headers
        assert headers["X-Base"] == "1"
        assert headers["Authorization"] == "Bearer tok"

    def test_no_discover_leaves_repo_empty(self) -> None:
        config = _config({"name": "p", "transport": "http", "url": "http://x"})
        client = client_from_config(
            config,
            lambda p: DiscoverableTransport(p, ("p.a",)),
            discover=False,
        )
        assert client.repository.list_providers() == ()

    def test_call_for_unconfigured_provider_raises(self) -> None:
        config = _config({"name": "p", "transport": "http", "url": "http://x"})
        client = client_from_config(
            config, lambda p: DiscoverableTransport(p, ("p.a",))
        )
        # Manually register a tool owned by a provider with no transport.
        client.repository.register(
            Manual(
                provider=Provider(name="ghost", transport="http", url="http://g"),
                tools=(Tool(name="ghost.x"),),
            )
        )
        with pytest.raises(TransportError):
            client.call(ToolCall(tool_name="ghost.x"))
