"""Tests for pyutcp_lab.core.variables."""

from __future__ import annotations

import pytest

from pyutcp_lab.core.errors import (
    CyclicVariableError,
    UndefinedVariableError,
)
from pyutcp_lab.core.variables import VariableResolver, parse_dotenv


class TestParseDotenv:
    def test_basic(self) -> None:
        env = parse_dotenv("A=1\nB=two\n")
        assert env == {"A": "1", "B": "two"}

    def test_comments_and_blanks(self) -> None:
        env = parse_dotenv("# comment\n\nA=1\n   # indented\nB=2\n")
        assert env == {"A": "1", "B": "2"}

    def test_export_prefix(self) -> None:
        assert parse_dotenv("export KEY=val") == {"KEY": "val"}

    def test_quoted_values_preserve_spaces(self) -> None:
        env = parse_dotenv('A="hello world"\nB=\'single\'')
        assert env == {"A": "hello world", "B": "single"}

    def test_inline_comment_stripped_when_unquoted(self) -> None:
        assert parse_dotenv("A=value # trailing") == {"A": "value"}

    def test_inline_hash_kept_when_quoted(self) -> None:
        assert parse_dotenv('A="value # kept"') == {"A": "value # kept"}

    def test_lines_without_equals_ignored(self) -> None:
        assert parse_dotenv("garbage\nA=1") == {"A": "1"}

    def test_empty_key_ignored(self) -> None:
        assert parse_dotenv("=novalue\nA=1") == {"A": "1"}


class TestVariableResolver:
    def test_simple_override(self) -> None:
        r = VariableResolver(overrides={"NAME": "world"}, use_environ=False)
        assert r.resolve("hello ${NAME}") == "hello world"

    def test_layer_precedence_override_beats_dotenv(self) -> None:
        r = VariableResolver(
            overrides={"K": "from_override"},
            dotenv={"K": "from_dotenv"},
            use_environ=False,
        )
        assert r.resolve("${K}") == "from_override"

    def test_dotenv_used_when_override_absent(self) -> None:
        r = VariableResolver(
            overrides={"OTHER": "x"},
            dotenv={"K": "from_dotenv"},
            use_environ=False,
        )
        assert r.resolve("${K}") == "from_dotenv"

    def test_environ_consulted_last(self, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.setenv("ENVONLY", "from_env")
        r = VariableResolver(use_environ=True)
        assert r.resolve("${ENVONLY}") == "from_env"

    def test_environ_disabled(self, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.setenv("ENVONLY", "from_env")
        r = VariableResolver(use_environ=False)
        with pytest.raises(UndefinedVariableError):
            r.resolve("${ENVONLY}")

    def test_undefined_raises_with_name(self) -> None:
        r = VariableResolver(use_environ=False)
        with pytest.raises(UndefinedVariableError) as exc:
            r.resolve("${MISSING}")
        assert exc.value.name == "MISSING"

    def test_recursive_resolution(self) -> None:
        r = VariableResolver(
            overrides={"A": "${B}", "B": "${C}", "C": "deep"},
            use_environ=False,
        )
        assert r.resolve("${A}") == "deep"

    def test_multiple_vars_in_one_template(self) -> None:
        r = VariableResolver(
            overrides={"H": "localhost", "P": "8080"}, use_environ=False
        )
        assert r.resolve("http://${H}:${P}/api") == "http://localhost:8080/api"

    def test_direct_cycle_detected(self) -> None:
        r = VariableResolver(overrides={"A": "${A}"}, use_environ=False)
        with pytest.raises(CyclicVariableError) as exc:
            r.resolve("${A}")
        assert exc.value.chain[0] == "A"

    def test_indirect_cycle_detected(self) -> None:
        r = VariableResolver(
            overrides={"A": "${B}", "B": "${A}"}, use_environ=False
        )
        with pytest.raises(CyclicVariableError) as exc:
            r.resolve("${A}")
        assert "A" in exc.value.chain and "B" in exc.value.chain

    def test_escaped_placeholder_is_literal(self) -> None:
        r = VariableResolver(overrides={"X": "should-not-appear"}, use_environ=False)
        assert r.resolve("price is $${X}") == "price is ${X}"

    def test_escaped_and_real_mixed(self) -> None:
        r = VariableResolver(overrides={"X": "real"}, use_environ=False)
        assert r.resolve("$${X} vs ${X}") == "${X} vs real"

    def test_no_placeholders_passthrough(self) -> None:
        r = VariableResolver(use_environ=False)
        assert r.resolve("plain text") == "plain text"

    def test_resolve_mapping(self) -> None:
        r = VariableResolver(overrides={"TOKEN": "abc"}, use_environ=False)
        out = r.resolve_mapping({"Authorization": "Bearer ${TOKEN}"})
        assert out == {"Authorization": "Bearer abc"}
