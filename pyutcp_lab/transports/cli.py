"""CLI (subprocess) transport.

A CLI provider exposes its tools by running a command. Discovery runs the
provider's base command with a discovery subcommand and parses a JSON manual
from stdout; a tool call runs the command with the tool name and a JSON-encoded
argument payload and parses a JSON result from stdout.

The actual process execution is injected as a *runner* callable so the transport
can be tested without spawning real processes. A runner takes the argv list and
the stdin bytes and returns ``(exit_code, stdout, stderr)``.
"""

from __future__ import annotations

import json
from typing import Any, Callable, Iterator, Optional

from ..core.errors import TransportError
from ..core.models import Manual, Provider, Tool, ToolCall, TransportType
from .base import Deadline, Transport

# (argv, stdin) -> (exit_code, stdout, stderr)
Runner = Callable[[list[str], bytes], "tuple[int, bytes, bytes]"]


class CliTransport(Transport):
    """Runs a provider's command-line tool via an injected runner."""

    def __init__(
        self,
        provider: Provider,
        runner: Runner,
        *,
        discovery_arg: str = "--utcp-manual",
    ) -> None:
        if provider.transport is not TransportType.CLI:
            raise TransportError(
                f"CliTransport cannot serve transport {provider.transport.value!r}"
            )
        if not provider.command:
            raise TransportError("CLI provider has no command configured")
        self._provider = provider
        self._base = list(provider.command)
        self._runner = runner
        self._discovery_arg = discovery_arg

    def _run(self, extra: list[str], stdin: bytes) -> bytes:
        argv = self._base + extra
        code, out, err = self._runner(argv, stdin)
        if code != 0:
            message = err.decode("utf-8", "replace").strip() or f"exit {code}"
            raise TransportError(f"CLI command failed: {message}")
        return out

    @staticmethod
    def _decode(raw: bytes) -> Any:
        if not raw.strip():
            return None
        try:
            return json.loads(raw.decode("utf-8"))
        except (json.JSONDecodeError, UnicodeDecodeError) as exc:
            raise TransportError(f"CLI produced invalid JSON: {exc}") from exc

    def discover(self, *, deadline: Optional[Deadline] = None) -> Manual:
        raw = self._run([self._discovery_arg], b"")
        payload = self._decode(raw)
        if not isinstance(payload, dict):
            raise TransportError("CLI manual must be a JSON object")
        tools = tuple(Tool(**t) for t in payload.get("tools", []))
        return Manual(provider=self._provider, tools=tools)

    def call(self, call: ToolCall, *, deadline: Optional[Deadline] = None) -> Any:
        stdin = json.dumps(call.arguments).encode("utf-8")
        raw = self._run([call.tool_name], stdin)
        return self._decode(raw)

    def stream(
        self, call: ToolCall, *, deadline: Optional[Deadline] = None
    ) -> Iterator[Any]:
        # CLI tools return a single result; stream yields it once if non-null.
        result = self.call(call, deadline=deadline)
        if result is not None:
            yield result
