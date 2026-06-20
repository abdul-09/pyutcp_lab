"""Tests for the pyutcp_lab command-line interface."""

from __future__ import annotations

import io
import json
from pathlib import Path

import pytest

from pyutcp_lab.__main__ import main


def _write_config(tmp_path: Path, **kw) -> Path:
    path = tmp_path / "c.json"
    path.write_text(json.dumps(kw))
    return path


def _run(argv: list[str]) -> tuple[int, str, str]:
    out, err = io.StringIO(), io.StringIO()
    code = main(argv, out=out, err=err)
    return code, out.getvalue(), err.getvalue()


class TestValidate:
    def test_reports_provider_count(self, tmp_path: Path) -> None:
        cfg = _write_config(
            tmp_path,
            providers=[
                {"name": "a", "transport": "http", "url": "http://a"},
                {"name": "b", "transport": "http", "url": "http://b"},
            ],
        )
        code, out, _ = _run(["validate", str(cfg)])
        assert code == 0
        assert "2 providers" in out

    def test_singular_provider(self, tmp_path: Path) -> None:
        cfg = _write_config(
            tmp_path, providers=[{"name": "a", "transport": "http", "url": "http://a"}]
        )
        code, out, _ = _run(["validate", str(cfg)])
        assert "1 provider" in out and "providers" not in out

    def test_bad_config_exit_code(self, tmp_path: Path) -> None:
        path = tmp_path / "bad.json"
        path.write_text("{not json")
        code, _, err = _run(["validate", str(path)])
        assert code == 2
        assert "error" in err


class TestList:
    def test_lists_providers(self, tmp_path: Path) -> None:
        cfg = _write_config(
            tmp_path,
            providers=[
                {"name": "weather", "transport": "http", "url": "http://x"},
                {"name": "search", "transport": "sse", "url": "http://y"},
            ],
        )
        code, out, _ = _run(["list", str(cfg)])
        assert code == 0
        assert "weather\thttp" in out
        assert "search\tsse" in out


class TestInspect:
    def test_inspect_http(self, tmp_path: Path) -> None:
        cfg = _write_config(
            tmp_path,
            providers=[{"name": "w", "transport": "http", "url": "http://x"}],
        )
        code, out, _ = _run(["inspect", str(cfg), "w"])
        assert code == 0
        assert "name:      w" in out
        assert "transport: http" in out
        assert "url:       http://x" in out

    def test_inspect_cli_command(self, tmp_path: Path) -> None:
        cfg = _write_config(
            tmp_path,
            providers=[{"name": "c", "transport": "cli", "command": ["t", "-j"]}],
        )
        code, out, _ = _run(["inspect", str(cfg), "c"])
        assert "command:   t -j" in out

    def test_inspect_shows_auth(self, tmp_path: Path) -> None:
        cfg = _write_config(
            tmp_path,
            providers=[
                {
                    "name": "s",
                    "transport": "http",
                    "url": "http://x",
                    "auth": {"scheme": "api_key", "key": "k", "header_name": "X-Key"},
                }
            ],
        )
        code, out, _ = _run(["inspect", str(cfg), "s"])
        assert "ApiKeyAuth" in out
        assert "X-Key" in out

    def test_inspect_no_auth(self, tmp_path: Path) -> None:
        cfg = _write_config(
            tmp_path, providers=[{"name": "p", "transport": "http", "url": "http://x"}]
        )
        code, out, _ = _run(["inspect", str(cfg), "p"])
        assert "NoAuth" in out

    def test_inspect_unknown_provider(self, tmp_path: Path) -> None:
        cfg = _write_config(
            tmp_path, providers=[{"name": "p", "transport": "http", "url": "http://x"}]
        )
        code, _, err = _run(["inspect", str(cfg), "missing"])
        assert code == 2
        assert "error" in err


class TestArgparse:
    def test_no_command_errors(self) -> None:
        with pytest.raises(SystemExit):
            _run([])
