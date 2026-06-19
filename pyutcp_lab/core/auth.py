"""Authentication for providers.

A provider often needs credentials before its transport can talk to it. This
module models the common schemes and turns each into the HTTP headers (or, for
the CLI, the environment additions) a transport applies to outgoing requests.

Credential values usually should not be written into a provider definition in
plain text. Each auth object therefore stores the raw value (which may be a
``${VAR}`` placeholder) and resolves it lazily through a
:class:`~pyutcp_lab.core.variables.VariableResolver` at the moment headers are
built, so secrets stay in the environment rather than in committed config.
"""

from __future__ import annotations

import base64
from abc import ABC, abstractmethod
from dataclasses import dataclass
from typing import Optional

from .errors import UtcpError
from .variables import VariableResolver

_NULL_RESOLVER = VariableResolver(use_environ=False)


class AuthError(UtcpError):
    """Raised when an auth configuration is invalid or cannot be resolved."""


class Auth(ABC):
    """Base class for provider authentication schemes."""

    @abstractmethod
    def headers(self, resolver: VariableResolver = _NULL_RESOLVER) -> dict[str, str]:
        """Return the headers this scheme contributes to a request."""


@dataclass(frozen=True)
class NoAuth(Auth):
    """No authentication."""

    def headers(self, resolver: VariableResolver = _NULL_RESOLVER) -> dict[str, str]:
        return {}


@dataclass(frozen=True)
class ApiKeyAuth(Auth):
    """An API key placed in a named header (default ``X-API-Key``)."""

    key: str
    header_name: str = "X-API-Key"
    prefix: str = ""

    def headers(self, resolver: VariableResolver = _NULL_RESOLVER) -> dict[str, str]:
        value = resolver.resolve(self.key)
        if not value:
            raise AuthError("API key resolved to an empty value")
        rendered = f"{self.prefix}{value}" if self.prefix else value
        return {self.header_name: rendered}


@dataclass(frozen=True)
class BearerAuth(Auth):
    """An OAuth-style bearer token in the Authorization header."""

    token: str

    def headers(self, resolver: VariableResolver = _NULL_RESOLVER) -> dict[str, str]:
        value = resolver.resolve(self.token)
        if not value:
            raise AuthError("bearer token resolved to an empty value")
        return {"Authorization": f"Bearer {value}"}


@dataclass(frozen=True)
class BasicAuth(Auth):
    """HTTP Basic auth from a username and password."""

    username: str
    password: str

    def headers(self, resolver: VariableResolver = _NULL_RESOLVER) -> dict[str, str]:
        user = resolver.resolve(self.username)
        secret = resolver.resolve(self.password)
        raw = f"{user}:{secret}".encode("utf-8")
        encoded = base64.b64encode(raw).decode("ascii")
        return {"Authorization": f"Basic {encoded}"}


def auth_from_dict(data: Optional[dict[str, str]]) -> Auth:
    """Build an :class:`Auth` from a plain dict (e.g. parsed from config).

    The ``scheme`` field selects the type. Unknown or missing schemes raise
    :class:`AuthError`.
    """
    if not data:
        return NoAuth()
    scheme = data.get("scheme", "none").lower()
    if scheme == "none":
        return NoAuth()
    if scheme == "api_key":
        return ApiKeyAuth(
            key=data["key"],
            header_name=data.get("header_name", "X-API-Key"),
            prefix=data.get("prefix", ""),
        )
    if scheme == "bearer":
        return BearerAuth(token=data["token"])
    if scheme == "basic":
        return BasicAuth(username=data["username"], password=data["password"])
    raise AuthError(f"unknown auth scheme {scheme!r}")
