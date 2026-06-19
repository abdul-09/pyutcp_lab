"""Bounded conversation history.

The history holds a fixed *system prompt* plus an ordered list of conversational
*turns*. To keep a model context within limits, the history enforces a token
budget. That budget governs only the conversational turns, though. The system
prompt is structural: it is always retained in full and is not charged against
the turn budget. Conflating the two makes a long system prompt steal room from
the conversation, truncating turns far earlier than the configured budget
intends.

When appending a turn would push the *turn* token total over the budget, the
oldest turns are dropped (optionally handed to a summarisation callback first)
until the remaining turns fit. Token counting is injected so callers can plug in
a real tokeniser; the default counts whitespace-separated words, which keeps the
accounting deterministic and easy to reason about in tests.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Callable, Optional

TokenCounter = Callable[[str], int]
SummaryHook = Callable[[tuple["Turn", ...]], None]


def _word_count(text: str) -> int:
    return len(text.split())


@dataclass(frozen=True)
class Turn:
    """One conversational turn."""

    role: str
    content: str


class ConversationHistory:
    """Token-budgeted conversation history that always keeps the system prompt."""

    def __init__(
        self,
        *,
        token_budget: int,
        system_prompt: str = "",
        count_tokens: TokenCounter = _word_count,
        on_evict: Optional[SummaryHook] = None,
    ) -> None:
        if token_budget < 1:
            raise ValueError("token_budget must be >= 1")
        self._budget = token_budget
        self._system_prompt = system_prompt
        self._count = count_tokens
        self._on_evict = on_evict
        self._turns: list[Turn] = []

    # -- introspection ------------------------------------------------------

    @property
    def system_prompt(self) -> str:
        return self._system_prompt

    @property
    def turns(self) -> tuple[Turn, ...]:
        return tuple(self._turns)

    def system_tokens(self) -> int:
        return self._count(self._system_prompt)

    def turn_tokens(self) -> int:
        """Total tokens across the retained turns (excludes the system prompt)."""
        return sum(self._count(t.content) for t in self._turns)

    def __len__(self) -> int:
        return len(self._turns)

    # -- mutation -----------------------------------------------------------

    def add(self, role: str, content: str) -> None:
        """Append a turn, then truncate oldest turns to fit the token budget."""
        self._turns.append(Turn(role=role, content=content))
        self._truncate()

    def _truncate(self) -> None:
        """Drop oldest turns until the turn-token total fits the budget.

        Only the turns are measured against the budget; the system prompt is
        retained unconditionally and does not count toward it.
        """
        evicted: list[Turn] = []
        while self._turns and self.turn_tokens() > self._budget:
            evicted.append(self._turns.pop(0))
        if evicted and self._on_evict is not None:
            self._on_evict(tuple(evicted))

    # -- rendering ----------------------------------------------------------

    def render(self) -> list[dict[str, str]]:
        """Return the message list: system prompt first, then retained turns."""
        messages: list[dict[str, str]] = []
        if self._system_prompt:
            messages.append({"role": "system", "content": self._system_prompt})
        messages.extend({"role": t.role, "content": t.content} for t in self._turns)
        return messages
