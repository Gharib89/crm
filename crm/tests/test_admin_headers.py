"""Unit tests for Spec C admin headers + ETag.

All HTTP is mocked via `requests_mock`. No live D365 server needed.
"""
# pyright: basic

from __future__ import annotations

import pytest
import requests_mock

from crm.utils.d365_backend import (
    ConnectionProfile,
    D365Backend,
    D365Error,
    _resolve_caller_id,
    _resolve_bool_env,
)


@pytest.fixture
def profile() -> ConnectionProfile:
    return ConnectionProfile(
        name="testp",
        url="https://crm.contoso.local/contoso",
        domain="CONTOSO",
        username="alice",
        api_version="v9.2",
        verify_ssl=False,
    )


@pytest.fixture
def backend(profile) -> D365Backend:
    return D365Backend(profile, password="pw", dry_run=False)


class TestEnvResolution:
    def test_caller_id_from_env_accepts_valid_guid(self, monkeypatch):
        monkeypatch.setenv("CRM_AS_USER", "11111111-2222-3333-4444-555555555555")
        assert _resolve_caller_id() == "11111111-2222-3333-4444-555555555555"

    def test_caller_id_from_env_rejects_invalid_guid(self, monkeypatch):
        monkeypatch.setenv("CRM_AS_USER", "not-a-guid")
        with pytest.raises(D365Error, match="CRM_AS_USER"):
            _resolve_caller_id()

    def test_caller_id_from_env_returns_none_when_unset(self, monkeypatch):
        monkeypatch.delenv("CRM_AS_USER", raising=False)
        assert _resolve_caller_id() is None

    def test_bool_env_true_variants(self, monkeypatch):
        for v in ("1", "true", "True", "yes", "on"):
            monkeypatch.setenv("CRM_SUPPRESS_DUP", v)
            assert _resolve_bool_env("CRM_SUPPRESS_DUP") is True

    def test_bool_env_false_variants(self, monkeypatch):
        for v in ("0", "false", "no", "off", ""):
            monkeypatch.setenv("CRM_SUPPRESS_DUP", v)
            assert _resolve_bool_env("CRM_SUPPRESS_DUP") is False

    def test_bool_env_missing_returns_false(self, monkeypatch):
        monkeypatch.delenv("CRM_SUPPRESS_DUP", raising=False)
        assert _resolve_bool_env("CRM_SUPPRESS_DUP") is False
