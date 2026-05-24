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


class TestBackendDefaults:
    def test_defaults_resolved_from_env(self, monkeypatch, profile):
        monkeypatch.setenv("CRM_AS_USER", "aaaaaaaa-bbbb-cccc-dddd-eeeeeeeeeeee")
        monkeypatch.setenv("CRM_SUPPRESS_DUP", "1")
        monkeypatch.setenv("CRM_BYPASS_PLUGINS", "true")
        b = D365Backend(profile, password="pw")
        assert b._default_caller_id == "aaaaaaaa-bbbb-cccc-dddd-eeeeeeeeeeee"
        assert b._default_suppress_dup is True
        assert b._default_bypass_plugins is True

    def test_defaults_absent_when_env_unset(self, monkeypatch, profile):
        for k in ("CRM_AS_USER", "CRM_SUPPRESS_DUP", "CRM_BYPASS_PLUGINS"):
            monkeypatch.delenv(k, raising=False)
        b = D365Backend(profile, password="pw")
        assert b._default_caller_id is None
        assert b._default_suppress_dup is False
        assert b._default_bypass_plugins is False

    def test_invalid_caller_id_env_raises_at_construction(self, monkeypatch, profile):
        monkeypatch.setenv("CRM_AS_USER", "not-a-guid")
        with pytest.raises(D365Error, match="CRM_AS_USER"):
            D365Backend(profile, password="pw")


class TestHeaderInjection:
    def _mock_ok(self, m, method, path, profile):
        url = f"{profile.api_base}{path}"
        m.request(method, url, json={"value": []}, status_code=200,
                  headers={"Content-Type": "application/json"})
        return url

    def test_caller_id_kwarg_sets_mscrmcallerid(self, backend, profile):
        guid = "11111111-2222-3333-4444-555555555555"
        with requests_mock.Mocker() as m:
            url = self._mock_ok(m, "GET", "accounts", profile)
            backend.get("accounts", caller_id=guid)
            assert m.last_request.headers["MSCRMCallerID"] == guid

    def test_caller_id_invalid_guid_raises_before_http(self, backend):
        with requests_mock.Mocker() as m:
            with pytest.raises(D365Error, match="GUID"):
                backend.get("accounts", caller_id="not-a-guid")
            assert m.call_count == 0

    def test_caller_id_kwarg_overrides_env_default(self, monkeypatch, profile):
        monkeypatch.setenv("CRM_AS_USER", "aaaaaaaa-aaaa-aaaa-aaaa-aaaaaaaaaaaa")
        b = D365Backend(profile, password="pw")
        guid = "bbbbbbbb-bbbb-bbbb-bbbb-bbbbbbbbbbbb"
        with requests_mock.Mocker() as m:
            m.get(f"{profile.api_base}accounts", json={"value": []})
            b.get("accounts", caller_id=guid)
            assert m.last_request.headers["MSCRMCallerID"] == guid

    def test_env_default_applied_when_kwarg_absent(self, monkeypatch, profile):
        env_guid = "aaaaaaaa-aaaa-aaaa-aaaa-aaaaaaaaaaaa"
        monkeypatch.setenv("CRM_AS_USER", env_guid)
        b = D365Backend(profile, password="pw")
        with requests_mock.Mocker() as m:
            m.get(f"{profile.api_base}accounts", json={"value": []})
            b.get("accounts")
            assert m.last_request.headers["MSCRMCallerID"] == env_guid

    def test_suppress_dup_detection_kwarg_sets_header(self, backend, profile):
        with requests_mock.Mocker() as m:
            m.post(f"{profile.api_base}accounts", status_code=204,
                   headers={"OData-EntityId": f"{profile.api_base}accounts(00000000-0000-0000-0000-000000000001)"})
            backend.post("accounts", json_body={"name": "a"},
                         suppress_duplicate_detection=True)
            assert m.last_request.headers["MSCRM.SuppressDuplicateDetection"] == "true"

    def test_bypass_plugins_kwarg_sets_header(self, backend, profile):
        with requests_mock.Mocker() as m:
            m.post(f"{profile.api_base}accounts", status_code=204,
                   headers={"OData-EntityId": f"{profile.api_base}accounts(00000000-0000-0000-0000-000000000001)"})
            backend.post("accounts", json_body={"name": "a"},
                         bypass_custom_plugin_execution=True)
            assert m.last_request.headers["MSCRM.BypassCustomPluginExecution"] == "true"

    def test_typed_kwargs_win_over_extra_headers(self, backend, profile):
        guid = "11111111-2222-3333-4444-555555555555"
        with requests_mock.Mocker() as m:
            m.get(f"{profile.api_base}accounts", json={"value": []})
            backend.get(
                "accounts",
                caller_id=guid,
                extra_headers={"MSCRMCallerID": "ffffffff-ffff-ffff-ffff-ffffffffffff"},
            )
            assert m.last_request.headers["MSCRMCallerID"] == guid

    def test_headers_absent_when_neither_kwarg_nor_env(self, monkeypatch, backend, profile):
        for k in ("CRM_AS_USER", "CRM_SUPPRESS_DUP", "CRM_BYPASS_PLUGINS"):
            monkeypatch.delenv(k, raising=False)
        with requests_mock.Mocker() as m:
            m.get(f"{profile.api_base}accounts", json={"value": []})
            backend.get("accounts")
            assert "MSCRMCallerID" not in m.last_request.headers
            assert "MSCRM.SuppressDuplicateDetection" not in m.last_request.headers
            assert "MSCRM.BypassCustomPluginExecution" not in m.last_request.headers


class TestEtag:
    def test_etag_value_sets_if_match(self, backend, profile):
        with requests_mock.Mocker() as m:
            m.patch(f"{profile.api_base}accounts(00000000-0000-0000-0000-000000000001)",
                    status_code=204)
            backend.patch(
                "accounts(00000000-0000-0000-0000-000000000001)",
                json_body={"name": "a"},
                etag='W/"123"',
            )
            assert m.last_request.headers["If-Match"] == 'W/"123"'

    def test_etag_star_sets_if_match_star(self, backend, profile):
        with requests_mock.Mocker() as m:
            m.patch(f"{profile.api_base}accounts(00000000-0000-0000-0000-000000000001)",
                    status_code=204)
            backend.patch(
                "accounts(00000000-0000-0000-0000-000000000001)",
                json_body={"name": "a"},
                etag="*",
            )
            assert m.last_request.headers["If-Match"] == "*"

    def test_etag_on_delete(self, backend, profile):
        with requests_mock.Mocker() as m:
            m.delete(f"{profile.api_base}accounts(00000000-0000-0000-0000-000000000001)",
                     status_code=204)
            backend.delete(
                "accounts(00000000-0000-0000-0000-000000000001)",
                etag='W/"7"',
            )
            assert m.last_request.headers["If-Match"] == 'W/"7"'

    def test_etag_on_get_raises(self, backend):
        with requests_mock.Mocker() as m:
            with pytest.raises(D365Error, match="etag not valid on GET"):
                backend.get("accounts", etag='W/"1"')
            assert m.call_count == 0

    def test_etag_on_post_raises(self, backend):
        with requests_mock.Mocker() as m:
            with pytest.raises(D365Error, match="etag not valid on POST"):
                backend.post("accounts", json_body={}, etag='W/"1"')
            assert m.call_count == 0

    def test_etag_empty_raises(self, backend):
        with requests_mock.Mocker() as m:
            with pytest.raises(D365Error, match="non-empty"):
                backend.patch("accounts(x)", json_body={}, etag="")
            assert m.call_count == 0
