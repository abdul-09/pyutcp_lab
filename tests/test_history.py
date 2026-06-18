"""Tests for pyutcp_lab.agent.history.ConversationHistory."""

from __future__ import annotations

import pytest

from pyutcp_lab.agent.history import ConversationHistory, Turn


def history(**kw: object) -> ConversationHistory:
    kw.setdefault("token_budget", 10)
    return ConversationHistory(**kw)  # type: ignore[arg-type]


class TestConstruction:
    def test_rejects_zero_budget(self) -> None:
        with pytest.raises(ValueError):
            ConversationHistory(token_budget=0)


class TestBasic:
    def test_add_and_render(self) -> None:
        h = history(system_prompt="sys")
        assert h.system_prompt == "sys"
        h.add("user", "hello there")
        msgs = h.render()
        assert msgs[0] == {"role": "system", "content": "sys"}
        assert msgs[1] == {"role": "user", "content": "hello there"}

    def test_no_system_prompt_omitted(self) -> None:
        h = history()
        h.add("user", "hi")
        assert all(m["role"] != "system" for m in h.render())

    def test_turn_tokens_excludes_system(self) -> None:
        h = history(system_prompt="one two three four five")
        h.add("user", "a b")
        assert h.turn_tokens() == 2
        assert h.system_tokens() == 5

    def test_len_counts_turns(self) -> None:
        h = history(token_budget=100)
        h.add("user", "a")
        h.add("assistant", "b")
        assert len(h) == 2


class TestTruncation:
    def test_drops_oldest_when_over_budget(self) -> None:
        h = history(token_budget=3)
        h.add("user", "one two")  # 2 tokens
        h.add("assistant", "three four")  # +2 -> 4 > 3, drop oldest
        assert [t.content for t in h.turns] == ["three four"]

    def test_keeps_all_when_within_budget(self) -> None:
        h = history(token_budget=10)
        h.add("user", "a b")
        h.add("assistant", "c d")
        assert len(h) == 2

    def test_eviction_hook_receives_dropped_turns(self) -> None:
        evicted: list[Turn] = []
        h = history(token_budget=2, on_evict=lambda turns: evicted.extend(turns))
        h.add("user", "one two")  # fits (2)
        h.add("assistant", "three")  # 3 > 2, drop "one two"
        assert [t.content for t in evicted] == ["one two"]

    def test_single_oversized_turn_is_dropped(self) -> None:
        # A lone turn bigger than the whole budget cannot be retained.
        h = history(token_budget=2)
        h.add("user", "a b c d e")
        assert len(h) == 0


class TestSystemPromptNotCharged:
    """The memory-accounting invariant the M10 task is built around.

    The token budget bounds the conversational turns only. The system prompt is
    always retained and must not be charged against the budget — otherwise a
    long system prompt shrinks the effective turn allowance and the history
    drops turns that should have fit.
    """

    def test_large_system_prompt_does_not_evict_fitting_turns(self) -> None:
        big_system = " ".join(f"w{i}" for i in range(50))  # 50 tokens
        h = ConversationHistory(token_budget=6, system_prompt=big_system)
        h.add("user", "a b")  # 2
        h.add("assistant", "c d")  # +2 = 4
        h.add("user", "e f")  # +2 = 6, exactly the turn budget
        # All three turns fit the turn budget of 6; the 50-token system prompt
        # must not have caused any eviction.
        assert len(h) == 3
        assert h.turn_tokens() == 6

    def test_turn_budget_independent_of_system_size(self) -> None:
        small = ConversationHistory(token_budget=4, system_prompt="x")
        large = ConversationHistory(
            token_budget=4, system_prompt=" ".join(["w"] * 100)
        )
        for h in (small, large):
            h.add("user", "a b")
            h.add("assistant", "c d")
        # Identical turn retention regardless of system-prompt size.
        assert [t.content for t in small.turns] == [t.content for t in large.turns]

    def test_system_prompt_always_rendered_even_if_huge(self) -> None:
        big_system = " ".join(["w"] * 100)
        h = ConversationHistory(token_budget=2, system_prompt=big_system)
        h.add("user", "a b")
        rendered = h.render()
        assert rendered[0]["role"] == "system"
        assert rendered[0]["content"] == big_system
