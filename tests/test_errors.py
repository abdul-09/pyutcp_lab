"""Tests for pyutcp_lab.core.errors — verifying the hierarchy and payloads."""

from __future__ import annotations

from pyutcp_lab.core.errors import (
    CyclicVariableError,
    DuplicateProviderError,
    ProviderError,
    TimeoutError,
    TransportError,
    UndefinedVariableError,
    UnknownProviderError,
    UtcpError,
    VariableError,
)


def test_all_errors_derive_from_base() -> None:
    for exc in (
        VariableError,
        UndefinedVariableError,
        CyclicVariableError,
        ProviderError,
        DuplicateProviderError,
        UnknownProviderError,
        TransportError,
        TimeoutError,
    ):
        assert issubclass(exc, UtcpError)


def test_variable_subclasses() -> None:
    assert issubclass(UndefinedVariableError, VariableError)
    assert issubclass(CyclicVariableError, VariableError)


def test_provider_subclasses() -> None:
    assert issubclass(DuplicateProviderError, ProviderError)
    assert issubclass(UnknownProviderError, ProviderError)


def test_timeout_is_transport_error() -> None:
    assert issubclass(TimeoutError, TransportError)


def test_undefined_variable_payload() -> None:
    err = UndefinedVariableError("TOKEN")
    assert err.name == "TOKEN"
    assert "TOKEN" in str(err)


def test_cyclic_variable_payload() -> None:
    err = CyclicVariableError(["A", "B", "A"])
    assert err.chain == ["A", "B", "A"]
    assert "A -> B -> A" in str(err)


def test_provider_error_carries_name() -> None:
    err = DuplicateProviderError("already registered", provider="api")
    assert err.provider == "api"
