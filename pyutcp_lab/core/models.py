"""Domain models for pyutcp-lab.

These mirror the core UTCP concepts:

* A :class:`Tool` is a single callable capability with a name, a human
  description, free-form tags, and JSON-schema-shaped input/output contracts.
* A :class:`Provider` describes *where* and *how* a set of tools is reached
  (the transport) and is the unit of registration in the repository.
* A :class:`Manual` is the bundle a provider returns at discovery time: the
  provider plus the tools it currently exposes.
* A :class:`ToolCall` is a request to invoke one tool with arguments.

Validation that Pydantic cannot express declaratively (e.g. namespacing rules)
is layered on with ``field_validator`` / ``model_validator`` and raises the
library's own :class:`~pyutcp_lab.core.errors.ValidationError` where it makes
sense for callers to catch it uniformly.
"""

from __future__ import annotations

import re
from enum import Enum
from typing import Any, Optional

from pydantic import BaseModel, ConfigDict, Field, field_validator

# A tool/provider name is a dotted identifier: segments of [a-z0-9_] joined by
# dots, e.g. "search.web" or "math.add". The leading segment is the namespace.
_NAME_RE = re.compile(r"^[a-z0-9_]+(\.[a-z0-9_]+)*$")
_MAX_NAME_LEN = 128


class TransportType(str, Enum):
    """Supported transport kinds. Mirrors the UTCP transport taxonomy."""

    HTTP = "http"
    SSE = "sse"
    CLI = "cli"
    STREAMING_HTTP = "streaming_http"
    GRAPHQL = "graphql"
    WEBSOCKET = "websocket"
    MCP = "mcp"
    TEXT = "text"


def _validate_name(value: str, *, kind: str) -> str:
    if not value:
        raise ValueError(f"{kind} name must not be empty")
    if len(value) > _MAX_NAME_LEN:
        raise ValueError(f"{kind} name exceeds {_MAX_NAME_LEN} characters")
    if not _NAME_RE.match(value):
        raise ValueError(
            f"{kind} name {value!r} is not a valid dotted identifier "
            "(lowercase letters, digits, underscores, dot-separated)"
        )
    return value


class Tool(BaseModel):
    """A single callable capability exposed by a provider."""

    model_config = ConfigDict(frozen=True, extra="forbid")

    name: str
    description: str = ""
    tags: tuple[str, ...] = ()
    inputs: dict[str, Any] = Field(default_factory=dict)
    outputs: dict[str, Any] = Field(default_factory=dict)
    average_response_size: Optional[int] = None

    @field_validator("name")
    @classmethod
    def _check_name(cls, value: str) -> str:
        return _validate_name(value, kind="tool")

    @field_validator("tags")
    @classmethod
    def _normalize_tags(cls, value: tuple[str, ...]) -> tuple[str, ...]:
        # Tags are lowercased and de-duplicated while preserving first-seen order.
        seen: dict[str, None] = {}
        for tag in value:
            cleaned = tag.strip().lower()
            if cleaned:
                seen.setdefault(cleaned, None)
        return tuple(seen)

    @field_validator("average_response_size")
    @classmethod
    def _check_size(cls, value: Optional[int]) -> Optional[int]:
        if value is not None and value < 0:
            raise ValueError("average_response_size must be non-negative")
        return value

    @property
    def namespace(self) -> str:
        """The leading segment of the dotted name (e.g. ``search`` of ``search.web``)."""
        return self.name.split(".", 1)[0]


class Provider(BaseModel):
    """Describes a transport endpoint and the tools reachable through it."""

    model_config = ConfigDict(frozen=True, extra="forbid")

    name: str
    transport: TransportType
    url: Optional[str] = None
    command: Optional[tuple[str, ...]] = None
    headers: dict[str, str] = Field(default_factory=dict)

    @field_validator("name")
    @classmethod
    def _check_name(cls, value: str) -> str:
        return _validate_name(value, kind="provider")


class Manual(BaseModel):
    """A provider plus the tools it currently exposes (the discovery result)."""

    model_config = ConfigDict(frozen=True, extra="forbid")

    provider: Provider
    tools: tuple[Tool, ...] = ()

    @field_validator("tools")
    @classmethod
    def _unique_tool_names(cls, value: tuple[Tool, ...]) -> tuple[Tool, ...]:
        names = [t.name for t in value]
        dupes = {n for n in names if names.count(n) > 1}
        if dupes:
            raise ValueError(
                f"manual contains duplicate tool names: {sorted(dupes)}"
            )
        return value

    def tool_names(self) -> tuple[str, ...]:
        return tuple(t.name for t in self.tools)


class ToolCall(BaseModel):
    """A request to invoke a named tool with arguments."""

    model_config = ConfigDict(extra="forbid")

    tool_name: str
    arguments: dict[str, Any] = Field(default_factory=dict)

    @field_validator("tool_name")
    @classmethod
    def _check_name(cls, value: str) -> str:
        return _validate_name(value, kind="tool")
