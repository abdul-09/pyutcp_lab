"""Search index over tools.

The index is an inverted index: each *token* drawn from a tool's name, tags, and
description maps to the set of tool names whose documents contain it. A search
tokenises the query, looks up the posting set for each token, and ranks matching
tools by how many distinct query tokens they hit (with a small boost for matches
in the name and tags over the description).

Index maintenance has one easily-missed invariant: when a tool is removed, every
posting referencing it must be dropped *and* any token whose posting set becomes
empty must be deleted from the index entirely. Leaving empty posting sets behind
keeps search results correct — an empty set matches nothing — but lets the
index's key set grow without bound across repeated add/remove churn. The index
must therefore prune empty tokens eagerly so its footprint tracks the live tool
set, not the cumulative history of tools it has ever seen.
"""

from __future__ import annotations

import re
from collections import Counter
from dataclasses import dataclass

from ..core.models import Tool

_TOKEN_RE = re.compile(r"[a-z0-9]+")

# Relative weight of where a token was found, used for ranking.
_FIELD_WEIGHTS = {"name": 3, "tag": 2, "description": 1}


def tokenize(text: str) -> list[str]:
    """Split text into lowercase alphanumeric tokens."""
    return _TOKEN_RE.findall(text.lower())


@dataclass(frozen=True)
class SearchResult:
    tool_name: str
    score: int


class SearchIndex:
    """An inverted index over tools supporting ranked keyword search."""

    def __init__(self) -> None:
        # token -> set of tool names containing it
        self._postings: dict[str, set[str]] = {}
        # tool name -> {token: weight} so we can rank and remove precisely
        self._tool_tokens: dict[str, dict[str, int]] = {}

    # -- maintenance --------------------------------------------------------

    def _document_tokens(self, tool: Tool) -> dict[str, int]:
        """Collect token -> best field weight for a tool's searchable text."""
        weights: dict[str, int] = {}
        for token in tokenize(tool.name):
            weights[token] = max(weights.get(token, 0), _FIELD_WEIGHTS["name"])
        for tag in tool.tags:
            for token in tokenize(tag):
                weights[token] = max(weights.get(token, 0), _FIELD_WEIGHTS["tag"])
        for token in tokenize(tool.description):
            weights[token] = max(
                weights.get(token, 0), _FIELD_WEIGHTS["description"]
            )
        return weights

    def add_tool(self, tool: Tool) -> None:
        """Index a tool. Re-adding the same name refreshes its postings."""
        if tool.name in self._tool_tokens:
            self.remove_tool(tool.name)
        tokens = self._document_tokens(tool)
        self._tool_tokens[tool.name] = tokens
        for token in tokens:
            self._postings.setdefault(token, set()).add(tool.name)

    def remove_tool(self, tool_name: str) -> None:
        """Remove a tool from the index, pruning any tokens left empty.

        Every posting set that referenced ``tool_name`` has the name removed,
        and if that leaves the set empty the token key is deleted so the index
        does not accumulate dead tokens.
        """
        tokens = self._tool_tokens.pop(tool_name, None)
        if tokens is None:
            return
        for token in tokens:
            postings = self._postings[token]
            postings.discard(tool_name)
            if not postings:
                del self._postings[token]

    # -- introspection (used by tests and metrics) --------------------------

    @property
    def token_count(self) -> int:
        """Number of distinct tokens currently held. Tracks the live tool set."""
        return len(self._postings)

    @property
    def tool_count(self) -> int:
        return len(self._tool_tokens)

    def postings_for(self, token: str) -> frozenset[str]:
        return frozenset(self._postings.get(token, frozenset()))

    # -- query --------------------------------------------------------------

    def search(self, query: str, *, limit: int = 10) -> tuple[SearchResult, ...]:
        """Return tools matching any query token, ranked by weighted hits.

        A tool's score is the sum, over query tokens it contains, of the field
        weight at which the token appears in that tool. Ties break by tool name
        for deterministic ordering.
        """
        q_tokens = tokenize(query)
        if not q_tokens:
            return ()
        scores: Counter[str] = Counter()
        for token in q_tokens:
            for tool_name in self._postings.get(token, ()):
                scores[tool_name] += self._tool_tokens[tool_name][token]
        ranked = sorted(scores.items(), key=lambda kv: (-kv[1], kv[0]))
        return tuple(
            SearchResult(tool_name=name, score=score)
            for name, score in ranked[:limit]
        )
