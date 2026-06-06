"""Typed error hierarchy for pyutcp-lab.

All exceptions raised by the library derive from :class:`UtcpError`, so callers
can catch the whole family with a single ``except``. Subclasses carry structured
context (the offending variable name, the provider, etc.) rather than only a
message string, which keeps error handling at call sites precise.
"""

from __future__ import annotations

from typing import Optional


class UtcpError(Exception):
    """Base class for every error raised by pyutcp-lab."""


class ValidationError(UtcpError):
    """Raised when a model fails domain validation beyond Pydantic's checks."""


class VariableError(UtcpError):
    """Base class for variable-substitution failures."""


class UndefinedVariableError(VariableError):
    """A referenced ``${VAR}`` has no value in any source."""

    def __init__(self, name: str) -> None:
        self.name = name
        super().__init__(f"undefined variable: {name!r}")


class CyclicVariableError(VariableError):
    """A variable reference chain forms a cycle (``A -> B -> A``)."""

    def __init__(self, chain: list[str]) -> None:
        self.chain = list(chain)
        rendered = " -> ".join(chain)
        super().__init__(f"cyclic variable reference: {rendered}")


class ProviderError(UtcpError):
    """Base class for provider-related failures."""

    def __init__(self, message: str, provider: Optional[str] = None) -> None:
        self.provider = provider
        super().__init__(message)


class DuplicateProviderError(ProviderError):
    """A provider name was registered twice without replacement."""


class UnknownProviderError(ProviderError):
    """An operation referenced a provider that is not registered."""


class TransportError(UtcpError):
    """Base class for transport-layer failures (network, subprocess, etc.)."""


class TimeoutError(TransportError):  # noqa: A001 - intentional domain name
    """A call exceeded its allotted time budget."""
