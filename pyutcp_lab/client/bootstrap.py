"""Building a client from configuration.

:func:`client_from_config` turns a parsed :class:`~pyutcp_lab.core.config.Config`
into a ready :class:`~pyutcp_lab.client.client.UtcpClient`. It needs a
*transport factory*: a callable that, given a provider, returns the transport to
reach it. For each configured provider the bootstrap discovers the provider's
manual and registers it, so the resulting client knows every tool up front.

Auth headers from each provider's config entry are merged onto the provider
before its transport is built, so the transport factory sees a provider whose
headers already carry the resolved credentials.
"""

from __future__ import annotations

from typing import Callable, Optional

from ..core.auth import Auth
from ..core.config import Config, ProviderConfig
from ..core.errors import TransportError
from ..core.models import Provider
from ..core.variables import VariableResolver
from ..registry.cache import ResultCache
from ..registry.repository import ToolRepository
from ..transports.base import Transport
from .client import UtcpClient
from .metrics import MetricsCollector

# Given a fully-prepared provider, return the transport that reaches it.
TransportFactory = Callable[[Provider], Transport]

_NULL_RESOLVER = VariableResolver(use_environ=False)


def _provider_with_auth(
    pc: ProviderConfig, resolver: VariableResolver
) -> Provider:
    """Return the provider with its auth headers merged into its header set."""
    auth_headers = pc.auth.headers(resolver)
    if not auth_headers:
        return pc.provider
    merged = dict(pc.provider.headers)
    merged.update(auth_headers)
    # Provider is frozen, so build a new one with the merged headers.
    return Provider(
        name=pc.provider.name,
        transport=pc.provider.transport,
        url=pc.provider.url,
        command=pc.provider.command,
        headers=merged,
    )


def client_from_config(
    config: Config,
    transport_factory: TransportFactory,
    *,
    resolver: VariableResolver = _NULL_RESOLVER,
    repository: Optional[ToolRepository] = None,
    metrics: Optional[MetricsCollector] = None,
    cache: Optional[ResultCache] = None,
    validate_arguments: bool = False,
    discover: bool = True,
) -> UtcpClient:
    """Build a :class:`UtcpClient` from a config, discovering each provider.

    The transports built for discovery are cached and reused by the client, so a
    provider is reached through the same transport instance for discovery and
    for later calls. When ``discover`` is False the client is returned with no
    tools registered (useful when the caller wants to register manually).
    """
    repo = repository or ToolRepository()
    transports: dict[str, Transport] = {}

    for pc in config.providers:
        prepared = _provider_with_auth(pc, resolver)
        transport = transport_factory(prepared)
        transports[prepared.name] = transport
        if discover:
            manual = transport.discover()
            repo.register(manual, replace=True)

    def resolve_transport(provider_name: str) -> Transport:
        transport = transports.get(provider_name)
        if transport is None:
            raise TransportError(
                f"no transport configured for provider {provider_name!r}"
            )
        return transport

    return UtcpClient(
        resolve_transport=resolve_transport,
        repository=repo,
        metrics=metrics,
        cache=cache,
        validate_arguments=validate_arguments,
    )
