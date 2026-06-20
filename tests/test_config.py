"""Tests for pyutcp_lab.core.config."""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from pyutcp_lab.core.auth import BearerAuth, NoAuth
from pyutcp_lab.core.config import (
    ConfigError,
    load_config,
    load_config_file,
)
from pyutcp_lab.core.models import TransportType


def _doc(**kw) -> str:
    return json.dumps(kw)


class TestLoadConfig:
    def test_minimal_provider(self) -> None:
        config = load_config(
            _doc(providers=[{"name": "p", "transport": "http", "url": "http://x"}]),
            use_environ=False,
        )
        assert config.provider_names() == ("p",)
        pc = config.get("p")
        assert pc.provider.transport is TransportType.HTTP
        assert isinstance(pc.auth, NoAuth)

    def test_inline_variables_substituted(self) -> None:
        config = load_config(
            _doc(
                variables={"REGION": "eu"},
                providers=[
                    {"name": "p", "transport": "http", "url": "http://${REGION}.x"}
                ],
            ),
            use_environ=False,
        )
        assert config.get("p").provider.url == "http://eu.x"

    def test_overrides_beat_inline_variables(self) -> None:
        config = load_config(
            _doc(
                variables={"R": "inline"},
                providers=[{"name": "p", "transport": "http", "url": "${R}"}],
            ),
            overrides={"R": "override"},
            use_environ=False,
        )
        assert config.get("p").provider.url == "override"

    def test_auth_parsed(self) -> None:
        config = load_config(
            _doc(
                providers=[
                    {
                        "name": "p",
                        "transport": "http",
                        "url": "http://x",
                        "auth": {"scheme": "bearer", "token": "${TK}"},
                    }
                ],
            ),
            overrides={"TK": "secret"},
            use_environ=False,
        )
        pc = config.get("p")
        assert isinstance(pc.auth, BearerAuth)
        # The token stays as the raw placeholder until headers() resolves it.
        from pyutcp_lab.core.variables import VariableResolver

        resolver = VariableResolver(overrides={"TK": "secret"}, use_environ=False)
        assert pc.auth.headers(resolver) == {"Authorization": "Bearer secret"}

    def test_cli_command_substituted(self) -> None:
        config = load_config(
            _doc(
                variables={"BIN": "mytool"},
                providers=[
                    {"name": "c", "transport": "cli", "command": ["${BIN}", "--json"]}
                ],
            ),
            use_environ=False,
        )
        assert config.get("c").provider.command == ("mytool", "--json")

    def test_headers_substituted(self) -> None:
        config = load_config(
            _doc(
                variables={"V": "1"},
                providers=[
                    {
                        "name": "p",
                        "transport": "http",
                        "url": "http://x",
                        "headers": {"X-Version": "${V}"},
                    }
                ],
            ),
            use_environ=False,
        )
        assert config.get("p").provider.headers == {"X-Version": "1"}


class TestConfigErrors:
    def test_non_object_root(self) -> None:
        with pytest.raises(ConfigError):
            load_config("[1, 2]", use_environ=False)

    def test_bad_json(self) -> None:
        with pytest.raises(ConfigError):
            load_config("{not json", use_environ=False)

    def test_variables_not_mapping(self) -> None:
        with pytest.raises(ConfigError):
            load_config(_doc(variables=[1, 2]), use_environ=False)

    def test_providers_not_list(self) -> None:
        with pytest.raises(ConfigError):
            load_config(_doc(providers={}), use_environ=False)

    def test_missing_name(self) -> None:
        with pytest.raises(ConfigError):
            load_config(_doc(providers=[{"transport": "http"}]), use_environ=False)

    def test_missing_transport(self) -> None:
        with pytest.raises(ConfigError):
            load_config(_doc(providers=[{"name": "p"}]), use_environ=False)

    def test_unknown_transport(self) -> None:
        with pytest.raises(ConfigError):
            load_config(
                _doc(providers=[{"name": "p", "transport": "smoke"}]),
                use_environ=False,
            )

    def test_command_not_list(self) -> None:
        with pytest.raises(ConfigError):
            load_config(
                _doc(providers=[{"name": "c", "transport": "cli", "command": "x"}]),
                use_environ=False,
            )

    def test_headers_not_mapping(self) -> None:
        with pytest.raises(ConfigError):
            load_config(
                _doc(
                    providers=[
                        {"name": "p", "transport": "http", "headers": [1]}
                    ]
                ),
                use_environ=False,
            )

    def test_unknown_format(self) -> None:
        with pytest.raises(ConfigError):
            load_config("{}", fmt="toml", use_environ=False)

    def test_get_unknown_provider(self) -> None:
        config = load_config(_doc(providers=[]), use_environ=False)
        with pytest.raises(ConfigError):
            config.get("nope")


class TestLoadConfigFile:
    def test_loads_json_file(self, tmp_path: Path) -> None:
        path = tmp_path / "c.json"
        path.write_text(
            _doc(providers=[{"name": "p", "transport": "http", "url": "http://x"}])
        )
        config = load_config_file(path, use_environ=False)
        assert config.provider_names() == ("p",)

    def test_missing_file(self, tmp_path: Path) -> None:
        with pytest.raises(ConfigError):
            load_config_file(tmp_path / "nope.json")

    def test_unknown_extension(self, tmp_path: Path) -> None:
        path = tmp_path / "c.txt"
        path.write_text("{}")
        with pytest.raises(ConfigError):
            load_config_file(path)

    def test_env_file_used(self, tmp_path: Path) -> None:
        cfg = tmp_path / "c.json"
        cfg.write_text(
            _doc(providers=[{"name": "p", "transport": "http", "url": "${HOST}"}])
        )
        env = tmp_path / ".env"
        env.write_text("HOST=http://from-env\n")
        config = load_config_file(cfg, env_file=env, use_environ=False)
        assert config.get("p").provider.url == "http://from-env"

    def test_missing_env_file(self, tmp_path: Path) -> None:
        cfg = tmp_path / "c.json"
        cfg.write_text(_doc(providers=[]))
        with pytest.raises(ConfigError):
            load_config_file(cfg, env_file=tmp_path / "nope.env")

    def test_loads_yaml_file(self, tmp_path: Path) -> None:
        pytest.importorskip("yaml")
        path = tmp_path / "c.yaml"
        path.write_text(
            "providers:\n"
            "  - name: p\n"
            "    transport: http\n"
            "    url: http://yaml\n"
        )
        config = load_config_file(path, use_environ=False)
        assert config.get("p").provider.url == "http://yaml"

    def test_malformed_yaml_raises(self, tmp_path: Path) -> None:
        pytest.importorskip("yaml")
        path = tmp_path / "bad.yaml"
        path.write_text("providers: [unclosed\n  - bad: : :\n")
        with pytest.raises(ConfigError):
            load_config_file(path, use_environ=False)
