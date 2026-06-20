"""Loading providers from a configuration file.

Real deployments rarely register providers in code. They point the client at a
config file that lists every provider, its transport, how to reach it, and how to
authenticate. This module parses such a file (JSON always, YAML when PyYAML is
installed), runs ``${VAR}`` substitution over every string so secrets and
environment-specific values stay out of the committed file, and turns the result
into :class:`~pyutcp_lab.core.models.Provider` and
:class:`~pyutcp_lab.core.auth.Auth` objects.

A config document looks like::

    {
        "variables": {"REGION": "eu"},
        "providers": [
            {
                "name": "weather",
                "transport": "http",
                "url": "https://${REGION}.weather.example/api",
                "auth": {"scheme": "bearer", "token": "${WEATHER_TOKEN}"},
                "headers": {"X-Region": "${REGION}"}
            }
        ]
    }

Inline ``variables`` are one source of substitution values; a ``.env`` file and
the process environment are also consulted, in that order of precedence.
"""

from __future__ import annotations

import json
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Optional

from .auth import Auth, auth_from_dict
from .errors import UtcpError
from .models import Provider, TransportType
from .variables import VariableResolver, parse_dotenv


class ConfigError(UtcpError):
    """Raised when a configuration file is malformed or cannot be loaded."""


@dataclass
class ProviderConfig:
    """A provider plus the auth resolved from its config entry."""

    provider: Provider
    auth: Auth


@dataclass
class Config:
    """A parsed configuration: the providers it defines and their auth."""

    providers: list[ProviderConfig] = field(default_factory=list)

    def provider_names(self) -> tuple[str, ...]:
        return tuple(pc.provider.name for pc in self.providers)

    def get(self, name: str) -> ProviderConfig:
        for pc in self.providers:
            if pc.provider.name == name:
                return pc
        raise ConfigError(f"no provider named {name!r} in config")


def _parse_text(text: str, *, fmt: str) -> Any:
    if fmt == "json":
        try:
            return json.loads(text)
        except json.JSONDecodeError as exc:
            raise ConfigError(f"invalid JSON config: {exc}") from exc
    if fmt == "yaml":
        try:
            import yaml  # imported lazily so PyYAML stays optional
        except ImportError as exc:  # pragma: no cover - depends on environment
            raise ConfigError(
                "YAML config requires PyYAML; install it or use JSON"
            ) from exc
        try:
            return yaml.safe_load(text)
        except yaml.YAMLError as exc:
            raise ConfigError(f"invalid YAML config: {exc}") from exc
    raise ConfigError(f"unsupported config format {fmt!r}")


def _format_for_path(path: Path) -> str:
    suffix = path.suffix.lower()
    if suffix in (".yaml", ".yml"):
        return "yaml"
    if suffix == ".json":
        return "json"
    raise ConfigError(f"cannot infer config format from {path.name!r}")


def _build_provider(entry: dict[str, Any], resolver: VariableResolver) -> ProviderConfig:
    if "name" not in entry:
        raise ConfigError("provider entry is missing 'name'")
    if "transport" not in entry:
        raise ConfigError(f"provider {entry['name']!r} is missing 'transport'")

    name = resolver.resolve(str(entry["name"]))
    transport_raw = resolver.resolve(str(entry["transport"]))
    try:
        transport = TransportType(transport_raw)
    except ValueError as exc:
        raise ConfigError(
            f"provider {name!r} has unknown transport {transport_raw!r}"
        ) from exc

    url = entry.get("url")
    if url is not None:
        url = resolver.resolve(str(url))

    command = entry.get("command")
    if command is not None:
        if not isinstance(command, list):
            raise ConfigError(f"provider {name!r} 'command' must be a list")
        command = tuple(resolver.resolve(str(part)) for part in command)

    headers_raw = entry.get("headers", {})
    if not isinstance(headers_raw, dict):
        raise ConfigError(f"provider {name!r} 'headers' must be a mapping")
    headers = {k: resolver.resolve(str(v)) for k, v in headers_raw.items()}

    provider = Provider(
        name=name,
        transport=transport,
        url=url,
        command=command,
        headers=headers,
    )

    # Auth credential values are resolved lazily by the Auth object itself, so
    # the raw (possibly ${VAR}) values are passed through unchanged here.
    auth = auth_from_dict(entry.get("auth"))
    return ProviderConfig(provider=provider, auth=auth)


def load_config(
    text: str,
    *,
    fmt: str = "json",
    overrides: Optional[dict[str, str]] = None,
    dotenv: Optional[dict[str, str]] = None,
    use_environ: bool = True,
) -> Config:
    """Parse a config document from text.

    ``fmt`` is ``"json"`` or ``"yaml"``. Substitution values come from, in
    precedence order: ``overrides``, the config's own ``variables`` block, the
    supplied ``dotenv`` mapping, and the process environment.
    """
    document = _parse_text(text, fmt=fmt)
    if not isinstance(document, dict):
        raise ConfigError("config root must be a mapping")

    inline_vars = document.get("variables", {})
    if not isinstance(inline_vars, dict):
        raise ConfigError("'variables' must be a mapping")

    # Merge override sources: explicit overrides win, then inline variables.
    merged_overrides: dict[str, str] = {}
    merged_overrides.update({k: str(v) for k, v in inline_vars.items()})
    if overrides:
        merged_overrides.update(overrides)

    resolver = VariableResolver(
        overrides=merged_overrides, dotenv=dotenv, use_environ=use_environ
    )

    providers_raw = document.get("providers", [])
    if not isinstance(providers_raw, list):
        raise ConfigError("'providers' must be a list")

    providers = [_build_provider(entry, resolver) for entry in providers_raw]
    return Config(providers=providers)


def load_config_file(
    path: str | Path,
    *,
    overrides: Optional[dict[str, str]] = None,
    env_file: Optional[str | Path] = None,
    use_environ: bool = True,
) -> Config:
    """Load and parse a config file, inferring JSON/YAML from its extension.

    If ``env_file`` is given, it is parsed as a ``.env`` file and supplies one
    layer of substitution values.
    """
    config_path = Path(path)
    if not config_path.is_file():
        raise ConfigError(f"config file not found: {config_path}")
    fmt = _format_for_path(config_path)
    text = config_path.read_text(encoding="utf-8")

    dotenv: Optional[dict[str, str]] = None
    if env_file is not None:
        env_path = Path(env_file)
        if not env_path.is_file():
            raise ConfigError(f".env file not found: {env_path}")
        dotenv = parse_dotenv(env_path.read_text(encoding="utf-8"))

    return load_config(
        text,
        fmt=fmt,
        overrides=overrides,
        dotenv=dotenv,
        use_environ=use_environ,
    )
