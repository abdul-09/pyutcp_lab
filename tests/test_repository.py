"""Tests for pyutcp_lab.registry.repository.ToolRepository."""

from __future__ import annotations

import pytest

from pyutcp_lab.core.errors import DuplicateProviderError, UnknownProviderError
from pyutcp_lab.core.models import Manual, Provider, Tool, TransportType
from pyutcp_lab.registry.repository import ToolRepository


def make_manual(provider_name: str, *tool_names: str) -> Manual:
    return Manual(
        provider=Provider(name=provider_name, transport=TransportType.HTTP, url="http://x"),
        tools=tuple(Tool(name=t) for t in tool_names),
    )


@pytest.fixture
def repo() -> ToolRepository:
    return ToolRepository()


class TestRegister:
    def test_register_adds_provider_and_tools(self, repo: ToolRepository) -> None:
        repo.register(make_manual("p", "p.a", "p.b"))
        assert repo.has_provider("p")
        assert repo.get_tool("p.a") is not None
        assert len(repo) == 2

    def test_duplicate_without_replace_raises(self, repo: ToolRepository) -> None:
        repo.register(make_manual("p", "p.a"))
        with pytest.raises(DuplicateProviderError):
            repo.register(make_manual("p", "p.a"))

    def test_replace_swaps_tool_set(self, repo: ToolRepository) -> None:
        repo.register(make_manual("p", "p.a", "p.b"))
        repo.register(make_manual("p", "p.b", "p.c"), replace=True)
        # p.a was dropped; p.b kept; p.c added.
        assert repo.get_tool("p.a") is None
        assert repo.get_tool("p.b") is not None
        assert repo.get_tool("p.c") is not None
        assert len(repo) == 2

    def test_replace_leaves_no_stale_owner(self, repo: ToolRepository) -> None:
        repo.register(make_manual("p", "p.a"))
        repo.register(make_manual("p", "p.z"), replace=True)
        assert repo.owner_of("p.a") is None
        assert repo.owner_of("p.z") == "p"


class TestDeregister:
    def test_deregister_removes_provider(self, repo: ToolRepository) -> None:
        repo.register(make_manual("p", "p.a"))
        repo.deregister("p")
        assert not repo.has_provider("p")

    def test_unknown_provider_raises(self, repo: ToolRepository) -> None:
        with pytest.raises(UnknownProviderError):
            repo.deregister("nope")

    def test_deregister_removes_all_its_tools(self, repo: ToolRepository) -> None:
        """Deregistering a provider has to take its tools with it.

        When a provider goes away, every tool it owned has to leave both the
        provider index and the tool index. A tool can't outlive its provider,
        or a later lookup returns something that can't actually be called.
        """
        repo.register(make_manual("p", "p.a", "p.b"))
        repo.register(make_manual("q", "q.a"))
        repo.deregister("p")

        # p's tools are gone from every view.
        assert repo.get_tool("p.a") is None
        assert repo.get_tool("p.b") is None
        assert repo.owner_of("p.a") is None
        assert "p.a" not in {t.name for t in repo.list_tools()}
        assert len(repo) == 1  # only q.a remains

        # q is untouched.
        assert repo.get_tool("q.a") is not None

    def test_deregister_then_reregister_clean(self, repo: ToolRepository) -> None:
        repo.register(make_manual("p", "p.a"))
        repo.deregister("p")
        repo.register(make_manual("p", "p.a"))  # no DuplicateProviderError
        assert repo.get_tool("p.a") is not None
        assert len(repo) == 1


class TestLookup:
    def test_get_provider(self, repo: ToolRepository) -> None:
        repo.register(make_manual("p", "p.a"))
        assert repo.get_provider("p").name == "p"

    def test_get_provider_unknown_raises(self, repo: ToolRepository) -> None:
        with pytest.raises(UnknownProviderError):
            repo.get_provider("nope")

    def test_list_tools_sorted(self, repo: ToolRepository) -> None:
        repo.register(make_manual("p", "p.b", "p.a"))
        assert [t.name for t in repo.list_tools()] == ["p.a", "p.b"]

    def test_list_providers_sorted(self, repo: ToolRepository) -> None:
        repo.register(make_manual("z", "z.a"))
        repo.register(make_manual("a", "a.a"))
        assert repo.list_providers() == ("a", "z")

    def test_tools_of_provider(self, repo: ToolRepository) -> None:
        repo.register(make_manual("p", "p.b", "p.a"))
        repo.register(make_manual("q", "q.a"))
        assert [t.name for t in repo.tools_of("p")] == ["p.a", "p.b"]

    def test_tools_of_unknown_raises(self, repo: ToolRepository) -> None:
        with pytest.raises(UnknownProviderError):
            repo.tools_of("nope")

    def test_iteration(self, repo: ToolRepository) -> None:
        repo.register(make_manual("p", "p.a", "p.b"))
        assert {t.name for t in repo} == {"p.a", "p.b"}

    def test_owner_of_unknown_tool(self, repo: ToolRepository) -> None:
        assert repo.owner_of("ghost") is None
