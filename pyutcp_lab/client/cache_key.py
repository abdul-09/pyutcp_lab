"""Cache keys for tool calls.

A cached tool result is keyed by the tool name plus its arguments. The key must
be stable across calls that are semantically identical, so argument ordering and
nested-dict ordering cannot matter: ``{"a": 1, "b": 2}`` and ``{"b": 2, "a": 1}``
have to produce the same key. The arguments are therefore serialised with sorted
keys before hashing.
"""

from __future__ import annotations

import hashlib
import json
from typing import Any

from ..core.models import ToolCall


def cache_key(call: ToolCall) -> str:
    """Return a stable key for a tool call.

    The key combines the tool name with a canonical JSON encoding of the
    arguments (keys sorted at every level), so two calls with equal arguments in
    a different order map to the same key.
    """
    canonical = json.dumps(call.arguments, sort_keys=True, separators=(",", ":"))
    digest = hashlib.sha256(canonical.encode("utf-8")).hexdigest()[:16]
    return f"{call.tool_name}:{digest}"


def arguments_fingerprint(arguments: dict[str, Any]) -> str:
    """A short, stable fingerprint of an argument mapping (for logging/debug)."""
    canonical = json.dumps(arguments, sort_keys=True, separators=(",", ":"))
    return hashlib.sha256(canonical.encode("utf-8")).hexdigest()[:16]
