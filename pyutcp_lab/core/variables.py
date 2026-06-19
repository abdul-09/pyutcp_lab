"""Variable substitution for provider/tool definitions.

Provider definitions frequently contain ``${VAR}`` placeholders that must be
resolved against a layered set of sources before the provider is usable:

1. an explicit mapping passed by the caller,
2. variables loaded from a ``.env`` file,
3. the process environment.

Earlier sources win over later ones. Substitution is recursive: a variable's
value may itself reference other variables. Recursion is bounded by cycle
detection. A reference chain that returns to a name already being resolved
raises :class:`~pyutcp_lab.core.errors.CyclicVariableError` rather than
recursing until the interpreter's stack limit.
"""

from __future__ import annotations

import os
import re
from typing import Mapping, Optional

from .errors import CyclicVariableError, UndefinedVariableError

# Matches ${NAME}; NAME is letters, digits, and underscores.
_VAR_RE = re.compile(r"\$\{([A-Za-z_][A-Za-z0-9_]*)\}")

# An escaped placeholder $${NAME} is emitted literally as ${NAME}.
_ESCAPED_RE = re.compile(r"\$\$\{([A-Za-z_][A-Za-z0-9_]*)\}")
_ESCAPE_SENTINEL = "\x00ESC\x00"


def parse_dotenv(text: str) -> dict[str, str]:
    """Parse the contents of a ``.env`` file into a mapping.

    Supports ``KEY=value`` lines, ``#`` comments, blank lines, optional
    ``export`` prefixes, and single/double quoted values. Inline comments after
    an unquoted value are stripped.
    """
    result: dict[str, str] = {}
    for raw in text.splitlines():
        line = raw.strip()
        if not line or line.startswith("#"):
            continue
        if line.startswith("export "):
            line = line[len("export ") :].lstrip()
        if "=" not in line:
            continue
        key, _, value = line.partition("=")
        key = key.strip()
        value = value.strip()
        if not key:
            continue
        if len(value) >= 2 and value[0] == value[-1] and value[0] in "\"'":
            value = value[1:-1]
        else:
            # Strip an inline comment from an unquoted value.
            hash_idx = value.find(" #")
            if hash_idx != -1:
                value = value[:hash_idx].rstrip()
        result[key] = value
    return result


class VariableResolver:
    """Resolves ``${VAR}`` references against layered sources.

    Sources are consulted in the order they were supplied; the first that
    defines a name wins. The process environment is consulted last unless
    ``use_environ`` is ``False``.
    """

    def __init__(
        self,
        overrides: Optional[Mapping[str, str]] = None,
        dotenv: Optional[Mapping[str, str]] = None,
        *,
        use_environ: bool = True,
    ) -> None:
        self._layers: list[Mapping[str, str]] = []
        if overrides:
            self._layers.append(dict(overrides))
        if dotenv:
            self._layers.append(dict(dotenv))
        self._use_environ = use_environ

    def _lookup(self, name: str) -> Optional[str]:
        for layer in self._layers:
            if name in layer:
                return layer[name]
        if self._use_environ and name in os.environ:
            return os.environ[name]
        return None

    def resolve(self, template: str) -> str:
        """Return ``template`` with every ``${VAR}`` replaced by its value.

        Resolution is recursive and cycle-guarded. ``$${VAR}`` is treated as an
        escaped literal and rendered as ``${VAR}``.
        """
        return self._resolve(template, _resolving=())

    def _resolve(self, template: str, _resolving: tuple[str, ...]) -> str:
        # Protect escaped placeholders before substitution, restore them after.
        protected = _ESCAPED_RE.sub(
            lambda m: f"{_ESCAPE_SENTINEL}{m.group(1)}{_ESCAPE_SENTINEL}",
            template,
        )

        def replace(match: re.Match[str]) -> str:
            name = match.group(1)
            if name in _resolving:
                raise CyclicVariableError([*_resolving, name])
            value = self._lookup(name)
            if value is None:
                raise UndefinedVariableError(name)
            # Recurse so values may themselves reference other variables.
            return self._resolve(value, _resolving=(*_resolving, name))

        substituted = _VAR_RE.sub(replace, protected)
        return self._restore_escapes(substituted)

    @staticmethod
    def _restore_escapes(text: str) -> str:
        # Turn the sentinel-wrapped names back into literal ${NAME}.
        return re.sub(
            rf"{_ESCAPE_SENTINEL}([A-Za-z_][A-Za-z0-9_]*){_ESCAPE_SENTINEL}",
            r"${\1}",
            text,
        )

    def resolve_mapping(self, mapping: Mapping[str, str]) -> dict[str, str]:
        """Resolve every value in a string-to-string mapping."""
        return {k: self.resolve(v) for k, v in mapping.items()}
