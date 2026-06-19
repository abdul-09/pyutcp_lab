"""Tests for pyutcp_lab.core.auth."""

from __future__ import annotations

import base64

import pytest

from pyutcp_lab.core.auth import (
    ApiKeyAuth,
    AuthError,
    BasicAuth,
    BearerAuth,
    NoAuth,
    auth_from_dict,
)
from pyutcp_lab.core.variables import VariableResolver


class TestNoAuth:
    def test_no_headers(self) -> None:
        assert NoAuth().headers() == {}


class TestApiKey:
    def test_default_header(self) -> None:
        auth = ApiKeyAuth(key="secret123")
        assert auth.headers() == {"X-API-Key": "secret123"}

    def test_custom_header_and_prefix(self) -> None:
        auth = ApiKeyAuth(key="abc", header_name="X-Token", prefix="tok_")
        assert auth.headers() == {"X-Token": "tok_abc"}

    def test_resolves_variable(self) -> None:
        resolver = VariableResolver(overrides={"API_KEY": "from_env"}, use_environ=False)
        auth = ApiKeyAuth(key="${API_KEY}")
        assert auth.headers(resolver) == {"X-API-Key": "from_env"}

    def test_empty_value_raises(self) -> None:
        resolver = VariableResolver(overrides={"K": ""}, use_environ=False)
        with pytest.raises(AuthError):
            ApiKeyAuth(key="${K}").headers(resolver)


class TestBearer:
    def test_bearer_header(self) -> None:
        assert BearerAuth(token="t0ken").headers() == {
            "Authorization": "Bearer t0ken"
        }

    def test_resolves_variable(self) -> None:
        resolver = VariableResolver(overrides={"TK": "xyz"}, use_environ=False)
        assert BearerAuth(token="${TK}").headers(resolver) == {
            "Authorization": "Bearer xyz"
        }

    def test_empty_token_raises(self) -> None:
        resolver = VariableResolver(overrides={"TK": ""}, use_environ=False)
        with pytest.raises(AuthError):
            BearerAuth(token="${TK}").headers(resolver)


class TestBasic:
    def test_basic_header(self) -> None:
        headers = BasicAuth(username="user", password="pass").headers()
        expected = base64.b64encode(b"user:pass").decode()
        assert headers == {"Authorization": f"Basic {expected}"}

    def test_resolves_variables(self) -> None:
        resolver = VariableResolver(
            overrides={"U": "alice", "P": "secret"}, use_environ=False
        )
        headers = BasicAuth(username="${U}", password="${P}").headers(resolver)
        expected = base64.b64encode(b"alice:secret").decode()
        assert headers["Authorization"] == f"Basic {expected}"


class TestAuthFromDict:
    def test_none_is_noauth(self) -> None:
        assert isinstance(auth_from_dict(None), NoAuth)

    def test_explicit_none(self) -> None:
        assert isinstance(auth_from_dict({"scheme": "none"}), NoAuth)

    def test_api_key(self) -> None:
        auth = auth_from_dict({"scheme": "api_key", "key": "k", "header_name": "H"})
        assert isinstance(auth, ApiKeyAuth)
        assert auth.headers() == {"H": "k"}

    def test_bearer(self) -> None:
        auth = auth_from_dict({"scheme": "bearer", "token": "t"})
        assert isinstance(auth, BearerAuth)

    def test_basic(self) -> None:
        auth = auth_from_dict({"scheme": "basic", "username": "u", "password": "p"})
        assert isinstance(auth, BasicAuth)

    def test_unknown_scheme_raises(self) -> None:
        with pytest.raises(AuthError):
            auth_from_dict({"scheme": "magic"})

    def test_case_insensitive_scheme(self) -> None:
        assert isinstance(auth_from_dict({"scheme": "BEARER", "token": "t"}), BearerAuth)
