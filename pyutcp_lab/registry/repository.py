"""In-memory tool repository.

The repository is the authoritative store of *what tools exist and where they
came from*. Providers are registered together with the manual they returned at
discovery time; the repository then exposes the union of all providers' tools
for lookup and listing.

Two indices are kept in lock-step:

* ``_providers`` maps a provider name to its registration (provider + manual).
* ``_tools`` maps a fully-qualified tool name to the owning provider's name.

Every mutation must leave these two consistent: a tool is present in ``_tools``
if and only if its provider is present in ``_providers`` *and* still lists it.
Registration is treated as a whole-provider replacement — re-registering a
provider atomically swaps in its new tool set and retires tools it no longer
exposes — so a provider can never leave stale entries behind.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Iterator, Optional

from ..core.errors import (
    DuplicateProviderError,
    UnknownProviderError,
)
from ..core.models import Manual, Provider, Tool


@dataclass(frozen=True)
class Registration:
    """A provider together with the manual it exposed at registration time."""

    provider: Provider
    manual: Manual


class ToolRepository:
    """Stores providers and the union of their tools, kept consistent."""

    def __init__(self) -> None:
        self._providers: dict[str, Registration] = {}
        # tool name -> provider name that owns it
        self._tool_owner: dict[str, str] = {}
        # tool name -> the Tool object, for fast lookup/listing
        self._tools: dict[str, Tool] = {}

    # -- registration -------------------------------------------------------

    def register(self, manual: Manual, *, replace: bool = False) -> None:
        """Register a provider and all tools in its manual.

        If the provider name already exists and ``replace`` is False, a
        :class:`DuplicateProviderError` is raised. When ``replace`` is True the
        existing registration is swapped out atomically: tools the provider no
        longer exposes are removed, and its new tools are added.
        """
        name = manual.provider.name
        if name in self._providers and not replace:
            raise DuplicateProviderError(
                f"provider {name!r} already registered", provider=name
            )

        # If replacing, first retire the old tool set for this provider so no
        # stale entries survive the swap.
        if name in self._providers:
            self._purge_provider_tools(name)

        self._providers[name] = Registration(provider=manual.provider, manual=manual)
        for tool in manual.tools:
            self._tool_owner[tool.name] = name
            self._tools[tool.name] = tool

    def deregister(self, provider_name: str) -> None:
        """Remove a provider and *all* of its tools.

        Both indices are updated together: after this call neither the provider
        nor any tool it owned remains in the repository.
        """
        if provider_name not in self._providers:
            raise UnknownProviderError(
                f"provider {provider_name!r} is not registered",
                provider=provider_name,
            )
        self._purge_provider_tools(provider_name)
        del self._providers[provider_name]

    def _purge_provider_tools(self, provider_name: str) -> None:
        """Remove every tool owned by ``provider_name`` from both tool indices."""
        owned = [
            tool_name
            for tool_name, owner in self._tool_owner.items()
            if owner == provider_name
        ]
        for tool_name in owned:
            del self._tool_owner[tool_name]
            del self._tools[tool_name]

    # -- lookup -------------------------------------------------------------

    def has_provider(self, provider_name: str) -> bool:
        return provider_name in self._providers

    def get_provider(self, provider_name: str) -> Provider:
        reg = self._providers.get(provider_name)
        if reg is None:
            raise UnknownProviderError(
                f"provider {provider_name!r} is not registered",
                provider=provider_name,
            )
        return reg.provider

    def get_tool(self, tool_name: str) -> Optional[Tool]:
        return self._tools.get(tool_name)

    def owner_of(self, tool_name: str) -> Optional[str]:
        return self._tool_owner.get(tool_name)

    def list_tools(self) -> tuple[Tool, ...]:
        """All tools across all providers, in insertion-stable name order."""
        return tuple(self._tools[name] for name in sorted(self._tools))

    def list_providers(self) -> tuple[str, ...]:
        return tuple(sorted(self._providers))

    def tools_of(self, provider_name: str) -> tuple[Tool, ...]:
        if provider_name not in self._providers:
            raise UnknownProviderError(
                f"provider {provider_name!r} is not registered",
                provider=provider_name,
            )
        names = sorted(
            n for n, owner in self._tool_owner.items() if owner == provider_name
        )
        return tuple(self._tools[n] for n in names)

    def __len__(self) -> int:
        return len(self._tools)

    def __iter__(self) -> Iterator[Tool]:
        return iter(self.list_tools())
