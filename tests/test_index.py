"""Tests for pyutcp_lab.registry.index.SearchIndex."""

from __future__ import annotations

import pytest

from pyutcp_lab.core.models import Tool
from pyutcp_lab.registry.index import SearchIndex, tokenize


def tool(name: str, *, tags: tuple[str, ...] = (), description: str = "") -> Tool:
    return Tool(name=name, tags=tags, description=description)


@pytest.fixture
def index() -> SearchIndex:
    return SearchIndex()


class TestTokenize:
    def test_splits_on_non_alphanumeric(self) -> None:
        assert tokenize("search.web_v2") == ["search", "web", "v2"]

    def test_lowercases(self) -> None:
        assert tokenize("HTTP GraphQL") == ["http", "graphql"]

    def test_empty(self) -> None:
        assert tokenize("") == []


class TestAddAndSearch:
    def test_search_by_name_token(self, index: SearchIndex) -> None:
        index.add_tool(tool("search.web"))
        results = index.search("web")
        assert [r.tool_name for r in results] == ["search.web"]

    def test_search_by_tag(self, index: SearchIndex) -> None:
        index.add_tool(tool("t.one", tags=("weather",)))
        assert [r.tool_name for r in index.search("weather")] == ["t.one"]

    def test_search_by_description(self, index: SearchIndex) -> None:
        index.add_tool(tool("t.one", description="fetches current temperature"))
        assert [r.tool_name for r in index.search("temperature")] == ["t.one"]

    def test_name_outranks_description(self, index: SearchIndex) -> None:
        index.add_tool(tool("weather.now"))  # 'weather' in name (weight 3)
        index.add_tool(tool("t.other", description="weather"))  # weight 1
        results = index.search("weather")
        assert results[0].tool_name == "weather.now"
        assert results[0].score > results[1].score

    def test_multi_token_query_accumulates_score(self, index: SearchIndex) -> None:
        index.add_tool(tool("a.one", description="alpha beta"))
        index.add_tool(tool("a.two", description="alpha"))
        results = index.search("alpha beta")
        assert results[0].tool_name == "a.one"  # matched both tokens

    def test_empty_query_returns_nothing(self, index: SearchIndex) -> None:
        index.add_tool(tool("t.one"))
        assert index.search("") == ()

    def test_no_match_returns_nothing(self, index: SearchIndex) -> None:
        index.add_tool(tool("t.one"))
        assert index.search("absent") == ()

    def test_limit_caps_results(self, index: SearchIndex) -> None:
        for i in range(5):
            index.add_tool(tool(f"t.n{i}", description="common"))
        assert len(index.search("common", limit=3)) == 3

    def test_tie_breaks_by_name(self, index: SearchIndex) -> None:
        index.add_tool(tool("b.tool", description="x"))
        index.add_tool(tool("a.tool", description="x"))
        names = [r.tool_name for r in index.search("x")]
        assert names == ["a.tool", "b.tool"]

    def test_readd_refreshes_postings(self, index: SearchIndex) -> None:
        index.add_tool(tool("t.one", description="old"))
        index.add_tool(tool("t.one", description="new"))
        assert index.search("old") == ()
        assert [r.tool_name for r in index.search("new")] == ["t.one"]
        assert index.tool_count == 1


class TestRemove:
    def test_remove_drops_from_results(self, index: SearchIndex) -> None:
        index.add_tool(tool("t.one", description="findme"))
        index.remove_tool("t.one")
        assert index.search("findme") == ()
        assert index.tool_count == 0

    def test_remove_unknown_is_noop(self, index: SearchIndex) -> None:
        index.remove_tool("ghost")  # must not raise
        assert index.tool_count == 0

    def test_shared_token_survives_partial_removal(self, index: SearchIndex) -> None:
        index.add_tool(tool("a.one", description="shared"))
        index.add_tool(tool("b.two", description="shared"))
        index.remove_tool("a.one")
        # 'shared' token still maps to b.two.
        assert [r.tool_name for r in index.search("shared")] == ["b.two"]
        assert "b.two" in index.postings_for("shared")


class TestTokenLeak:
    """Token count should track live tools, not everything ever indexed.

    The number of distinct tokens the index holds should follow the live tool
    set. Adding and removing tools that bring their own unique tokens shouldn't
    make the index grow without bound, which means a posting set that goes empty
    after a removal needs its token dropped too.
    """

    def test_token_count_returns_to_zero(self, index: SearchIndex) -> None:
        index.add_tool(tool("t.one", description="unique_alpha"))
        assert index.token_count > 0
        index.remove_tool("t.one")
        assert index.token_count == 0

    def test_no_growth_across_churn(self, index: SearchIndex) -> None:
        # Each cycle introduces a brand-new unique token, then removes it.
        # A leaking index would accumulate one dead token per cycle.
        for i in range(200):
            name = f"churn.tool{i}"
            index.add_tool(tool(name, description=f"token_{i}"))
            index.remove_tool(name)
        assert index.tool_count == 0
        assert index.token_count == 0

    def test_steady_state_token_count(self, index: SearchIndex) -> None:
        # Keep one stable tool; churn others around it. Token count must reflect
        # only the live set (the stable tool's tokens), never the churn history.
        index.add_tool(tool("stable.tool", description="permanent_token"))
        baseline = index.token_count
        for i in range(100):
            name = f"temp.tool{i}"
            index.add_tool(tool(name, description=f"ephemeral_{i}"))
            index.remove_tool(name)
        assert index.token_count == baseline
