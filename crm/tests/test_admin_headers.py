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
    classify_d365_error,
    _resolve_caller_id,
    _resolve_caller_object_id,
    _resolve_bool_env,
)


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

    def test_caller_object_id_from_env_accepts_valid_guid(self, monkeypatch):
        monkeypatch.setenv("CRM_AS_USER_OBJECT_ID", "66666666-7777-8888-9999-aaaaaaaaaaaa")
        assert _resolve_caller_object_id() == "66666666-7777-8888-9999-aaaaaaaaaaaa"

    def test_caller_object_id_from_env_rejects_invalid_guid(self, monkeypatch):
        monkeypatch.setenv("CRM_AS_USER_OBJECT_ID", "not-a-guid")
        with pytest.raises(D365Error, match="CRM_AS_USER_OBJECT_ID"):
            _resolve_caller_object_id()

    def test_caller_object_id_from_env_returns_none_when_unset(self, monkeypatch):
        monkeypatch.delenv("CRM_AS_USER_OBJECT_ID", raising=False)
        assert _resolve_caller_object_id() is None

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
        monkeypatch.setenv("CRM_AS_USER_OBJECT_ID", "11112222-3333-4444-5555-666677778888")
        monkeypatch.setenv("CRM_SUPPRESS_DUP", "1")
        monkeypatch.setenv("CRM_BYPASS_PLUGINS", "true")
        b = D365Backend(profile, password="pw")
        assert b._default_caller_id == "aaaaaaaa-bbbb-cccc-dddd-eeeeeeeeeeee"
        assert b._default_caller_object_id == "11112222-3333-4444-5555-666677778888"
        assert b._default_suppress_dup is True
        assert b._default_bypass_plugins is True

    def test_defaults_absent_when_env_unset(self, monkeypatch, profile):
        for k in ("CRM_AS_USER", "CRM_AS_USER_OBJECT_ID", "CRM_SUPPRESS_DUP", "CRM_BYPASS_PLUGINS"):
            monkeypatch.delenv(k, raising=False)
        b = D365Backend(profile, password="pw")
        assert b._default_caller_id is None
        assert b._default_caller_object_id is None
        assert b._default_suppress_dup is False
        assert b._default_bypass_plugins is False

    def test_invalid_caller_id_env_raises_at_construction(self, monkeypatch, profile):
        monkeypatch.setenv("CRM_AS_USER", "not-a-guid")
        with pytest.raises(D365Error, match="CRM_AS_USER"):
            D365Backend(profile, password="pw")

    def test_invalid_caller_object_id_env_raises_at_construction(self, monkeypatch, profile):
        monkeypatch.delenv("CRM_AS_USER", raising=False)
        monkeypatch.setenv("CRM_AS_USER_OBJECT_ID", "not-a-guid")
        with pytest.raises(D365Error, match="CRM_AS_USER_OBJECT_ID"):
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

    def test_solution_kwarg_sets_header(self, backend, profile):
        with requests_mock.Mocker() as m:
            m.post(f"{profile.api_base}accounts", status_code=204)
            backend.post("accounts", json_body={"name": "a"}, solution="MySol")
            assert m.last_request.headers["MSCRM.SolutionUniqueName"] == "MySol"

    def test_solution_none_omits_header(self, backend, profile):
        with requests_mock.Mocker() as m:
            m.post(f"{profile.api_base}accounts", status_code=204)
            backend.post("accounts", json_body={"name": "a"}, solution=None)
            assert "MSCRM.SolutionUniqueName" not in m.last_request.headers

    def test_solution_kwarg_wins_over_extra_headers(self, backend, profile):
        with requests_mock.Mocker() as m:
            m.post(f"{profile.api_base}accounts", status_code=204)
            backend.post(
                "accounts",
                json_body={"name": "a"},
                solution="MySol",
                extra_headers={"MSCRM.SolutionUniqueName": "Other"},
            )
            assert m.last_request.headers["MSCRM.SolutionUniqueName"] == "MySol"

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

    def test_suppress_dup_false_kwarg_overrides_true_env(self, monkeypatch, profile):
        monkeypatch.setenv("CRM_SUPPRESS_DUP", "1")
        b = D365Backend(profile, password="pw")
        with requests_mock.Mocker() as m:
            m.post(f"{profile.api_base}accounts", status_code=204)
            b.post("accounts", json_body={"name": "a"}, suppress_duplicate_detection=False)
            assert "MSCRM.SuppressDuplicateDetection" not in m.last_request.headers

    def test_bypass_plugins_false_kwarg_overrides_true_env(self, monkeypatch, profile):
        monkeypatch.setenv("CRM_BYPASS_PLUGINS", "1")
        b = D365Backend(profile, password="pw")
        with requests_mock.Mocker() as m:
            m.post(f"{profile.api_base}accounts", status_code=204)
            b.post("accounts", json_body={"name": "a"}, bypass_custom_plugin_execution=False)
            assert "MSCRM.BypassCustomPluginExecution" not in m.last_request.headers

    def test_caller_id_empty_string_disables_env_default(self, monkeypatch, profile):
        monkeypatch.setenv("CRM_AS_USER", "aaaaaaaa-aaaa-aaaa-aaaa-aaaaaaaaaaaa")
        b = D365Backend(profile, password="pw")
        with requests_mock.Mocker() as m:
            m.get(f"{profile.api_base}accounts", json={"value": []})
            b.get("accounts", caller_id="")
            assert "MSCRMCallerID" not in m.last_request.headers

    def test_caller_id_empty_pops_extra_headers_collision(self, monkeypatch, profile):
        monkeypatch.setenv("CRM_AS_USER", "aaaaaaaa-aaaa-aaaa-aaaa-aaaaaaaaaaaa")
        b = D365Backend(profile, password="pw")
        with requests_mock.Mocker() as m:
            m.get(f"{profile.api_base}accounts", json={"value": []})
            b.get("accounts",
                  caller_id="",
                  extra_headers={"MSCRMCallerID": "ffffffff-ffff-ffff-ffff-ffffffffffff"})
            assert "MSCRMCallerID" not in m.last_request.headers

    def test_suppress_false_pops_extra_headers_collision(self, monkeypatch, profile):
        monkeypatch.setenv("CRM_SUPPRESS_DUP", "1")
        b = D365Backend(profile, password="pw")
        with requests_mock.Mocker() as m:
            m.post(f"{profile.api_base}accounts", status_code=204)
            b.post("accounts", json_body={"name": "a"},
                   suppress_duplicate_detection=False,
                   extra_headers={"MSCRM.SuppressDuplicateDetection": "true"})
            assert "MSCRM.SuppressDuplicateDetection" not in m.last_request.headers

    def test_headers_absent_when_neither_kwarg_nor_env(self, monkeypatch, backend, profile):
        for k in ("CRM_AS_USER", "CRM_SUPPRESS_DUP", "CRM_BYPASS_PLUGINS"):
            monkeypatch.delenv(k, raising=False)
        with requests_mock.Mocker() as m:
            m.get(f"{profile.api_base}accounts", json={"value": []})
            backend.get("accounts")
            assert "MSCRMCallerID" not in m.last_request.headers
            assert "MSCRM.SuppressDuplicateDetection" not in m.last_request.headers
            assert "MSCRM.BypassCustomPluginExecution" not in m.last_request.headers


class TestCallerObjectId:
    def test_caller_object_id_kwarg_sets_callerobjectid(self, backend, profile):
        guid = "99999999-8888-7777-6666-555555555555"
        with requests_mock.Mocker() as m:
            m.get(f"{profile.api_base}accounts", json={"value": []})
            backend.get("accounts", caller_object_id=guid)
            assert m.last_request.headers["CallerObjectId"] == guid
            assert "MSCRMCallerID" not in m.last_request.headers

    def test_object_id_env_default_applied_when_kwarg_absent(self, monkeypatch, profile):
        env_guid = "12121212-3434-5656-7878-909090909090"
        monkeypatch.delenv("CRM_AS_USER", raising=False)
        monkeypatch.setenv("CRM_AS_USER_OBJECT_ID", env_guid)
        b = D365Backend(profile, password="pw")
        with requests_mock.Mocker() as m:
            m.get(f"{profile.api_base}accounts", json={"value": []})
            b.get("accounts")
            assert m.last_request.headers["CallerObjectId"] == env_guid
            assert "MSCRMCallerID" not in m.last_request.headers

    def test_object_id_empty_string_disables_env_default(self, monkeypatch, profile):
        monkeypatch.delenv("CRM_AS_USER", raising=False)
        monkeypatch.setenv("CRM_AS_USER_OBJECT_ID", "12121212-3434-5656-7878-909090909090")
        b = D365Backend(profile, password="pw")
        with requests_mock.Mocker() as m:
            m.get(f"{profile.api_base}accounts", json={"value": []})
            b.get("accounts", caller_object_id="")
            assert "CallerObjectId" not in m.last_request.headers

    def test_object_id_invalid_guid_raises_before_http(self, backend):
        with requests_mock.Mocker() as m:
            with pytest.raises(D365Error, match="GUID"):
                backend.get("accounts", caller_object_id="not-a-guid")
            assert m.call_count == 0

    def test_object_id_kwarg_wins_over_extra_headers(self, backend, profile):
        guid = "99999999-8888-7777-6666-555555555555"
        with requests_mock.Mocker() as m:
            m.get(f"{profile.api_base}accounts", json={"value": []})
            backend.get(
                "accounts",
                caller_object_id=guid,
                extra_headers={"CallerObjectId": "ffffffff-ffff-ffff-ffff-ffffffffffff"},
            )
            assert m.last_request.headers["CallerObjectId"] == guid

    def test_object_id_empty_pops_extra_headers_collision(self, monkeypatch, profile):
        monkeypatch.delenv("CRM_AS_USER", raising=False)
        monkeypatch.setenv("CRM_AS_USER_OBJECT_ID", "aaaaaaaa-aaaa-aaaa-aaaa-aaaaaaaaaaaa")
        b = D365Backend(profile, password="pw")
        with requests_mock.Mocker() as m:
            m.get(f"{profile.api_base}accounts", json={"value": []})
            b.get("accounts",
                  caller_object_id="",
                  extra_headers={"CallerObjectId": "ffffffff-ffff-ffff-ffff-ffffffffffff"})
            assert "CallerObjectId" not in m.last_request.headers

    def test_object_id_supersedes_differently_cased_extra_header(self, backend, profile):
        # HTTP header names are case-insensitive: a differently-cased
        # CallerObjectId in extra_headers must not survive the resolved one.
        guid = "99999999-8888-7777-6666-555555555555"
        with requests_mock.Mocker() as m:
            m.get(f"{profile.api_base}accounts", json={"value": []})
            backend.get(
                "accounts",
                caller_object_id=guid,
                extra_headers={"callerobjectid": "ffffffff-ffff-ffff-ffff-ffffffffffff"},
            )
            assert m.last_request.headers["CallerObjectId"] == guid

    def test_systemuserid_drops_differently_cased_object_id_extra_header(self, backend, profile):
        # never-both invariant must hold across header casing: emitting
        # MSCRMCallerID drops any CallerObjectId variant from extra_headers.
        guid = "11111111-2222-3333-4444-555555555555"
        with requests_mock.Mocker() as m:
            m.get(f"{profile.api_base}accounts", json={"value": []})
            backend.get(
                "accounts",
                caller_id=guid,
                extra_headers={"CALLEROBJECTID": "ffffffff-ffff-ffff-ffff-ffffffffffff"},
            )
            assert m.last_request.headers["MSCRMCallerID"] == guid
            assert "CallerObjectId" not in m.last_request.headers

    def test_collision_both_kwargs_raises_before_http(self, backend):
        with requests_mock.Mocker() as m:
            with pytest.raises(D365Error, match="both"):
                backend.get(
                    "accounts",
                    caller_id="11111111-1111-1111-1111-111111111111",
                    caller_object_id="22222222-2222-2222-2222-222222222222",
                )
            assert m.call_count == 0

    def test_collision_both_env_raises_before_http(self, monkeypatch, profile):
        monkeypatch.setenv("CRM_AS_USER", "11111111-1111-1111-1111-111111111111")
        monkeypatch.setenv("CRM_AS_USER_OBJECT_ID", "22222222-2222-2222-2222-222222222222")
        b = D365Backend(profile, password="pw")
        with requests_mock.Mocker() as m:
            with pytest.raises(D365Error, match="both"):
                b.get("accounts")
            assert m.call_count == 0

    def test_collision_env_systemuser_plus_flag_object_id_raises(self, monkeypatch, profile):
        monkeypatch.setenv("CRM_AS_USER", "11111111-1111-1111-1111-111111111111")
        monkeypatch.delenv("CRM_AS_USER_OBJECT_ID", raising=False)
        b = D365Backend(profile, password="pw")
        with requests_mock.Mocker() as m:
            with pytest.raises(D365Error, match="both"):
                b.get("accounts", caller_object_id="22222222-2222-2222-2222-222222222222")
            assert m.call_count == 0

    def test_systemuserid_path_emits_no_callerobjectid(self, backend, profile):
        guid = "11111111-2222-3333-4444-555555555555"
        with requests_mock.Mocker() as m:
            m.get(f"{profile.api_base}accounts", json={"value": []})
            backend.get("accounts", caller_id=guid)
            assert m.last_request.headers["MSCRMCallerID"] == guid
            assert "CallerObjectId" not in m.last_request.headers

    def test_object_id_emitted_under_ntlm_profile(self, profile):
        # profile fixture is auth_scheme="ntlm"; header choice must ignore it.
        assert profile.auth_scheme == "ntlm"
        b = D365Backend(profile, password="pw")
        guid = "22222222-2222-2222-2222-222222222222"
        with requests_mock.Mocker() as m:
            m.get(f"{profile.api_base}accounts", json={"value": []})
            b.get("accounts", caller_object_id=guid)
            assert m.last_request.headers["CallerObjectId"] == guid

    def test_systemuserid_emitted_under_oauth_profile(self, monkeypatch):
        # An oauth profile with the systemuserid input still emits MSCRMCallerID:
        # header choice is independent of auth_scheme. Stub the oauth adapter so
        # construction never reaches msal/AAD.
        monkeypatch.setattr(D365Backend, "_make_oauth_auth", lambda self, secret: (lambda r: r))
        oauth_profile = ConnectionProfile(
            name="cloud", url="https://contoso.crm.dynamics.com", domain="", username="",
            auth_scheme="oauth", tenant_id="tid", client_id="cid", verify_ssl=False,
        )
        b = D365Backend(oauth_profile, password="secret")
        guid = "33333333-3333-3333-3333-333333333333"
        with requests_mock.Mocker() as m:
            m.get(f"{oauth_profile.api_base}accounts", json={"value": []})
            b.get("accounts", caller_id=guid)
            assert m.last_request.headers["MSCRMCallerID"] == guid
            assert "CallerObjectId" not in m.last_request.headers


class TestCallerObjectIdCli:
    """End-to-end: the --as-user-object-id flag reaches the backend as CallerObjectId."""

    @staticmethod
    def _seed(monkeypatch, tmp_path, base):
        monkeypatch.setenv("CRM_HOME", str(tmp_path / ".crm"))
        monkeypatch.setenv("CRM_DOTENV", str(tmp_path / "noop.env"))  # don't autoload repo .env
        for k in ("CRM_AS_USER", "CRM_AS_USER_OBJECT_ID"):
            monkeypatch.delenv(k, raising=False)
        from crm.core import session as session_mod
        session_mod.save_profile(ConnectionProfile(
            name="t", url=base, domain="CONTOSO", username="alice",
            api_version="v9.2"))
        session_mod.save_profile_secret_plaintext("t", "pw")

    def test_cli_as_user_object_id_emits_callerobjectid(self, monkeypatch, tmp_path):
        from click.testing import CliRunner
        from crm.cli import cli

        base = "https://contoso.crm.dynamics.com"
        self._seed(monkeypatch, tmp_path, base)

        guid = "44444444-4444-4444-4444-444444444444"
        rec = "55555555-5555-5555-5555-555555555555"
        with requests_mock.Mocker() as m:
            m.patch(f"{base}/api/data/v9.2/accounts({rec})", status_code=204)
            result = CliRunner().invoke(cli, [
                "--json", "--profile", "t", "entity", "update", "accounts", rec,
                "--data", '{"name": "x"}', "--allow-create",
                "--as-user-object-id", guid,
            ])
            assert result.exit_code == 0, result.output
            assert m.last_request.headers["CallerObjectId"] == guid
            assert "MSCRMCallerID" not in m.last_request.headers

    def test_cli_both_as_user_flags_collide(self, monkeypatch, tmp_path):
        from click.testing import CliRunner
        from crm.cli import cli

        base = "https://contoso.crm.dynamics.com"
        self._seed(monkeypatch, tmp_path, base)

        rec = "55555555-5555-5555-5555-555555555555"
        with requests_mock.Mocker() as m:
            m.patch(f"{base}/api/data/v9.2/accounts({rec})", status_code=204)
            result = CliRunner().invoke(cli, [
                "--json", "--profile", "t", "entity", "update", "accounts", rec,
                "--data", '{"name": "x"}', "--allow-create",
                "--as-user", "11111111-1111-1111-1111-111111111111",
                "--as-user-object-id", "22222222-2222-2222-2222-222222222222",
            ])
            assert result.exit_code != 0
            assert m.call_count == 0
        assert "both" in result.output


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


class TestErrorMapping:
    def test_412_preserves_body_code(self, backend, profile):
        """412 with a D365 code in the body preserves it; PreconditionFailed is
        the fallback only when the body carries no code (#232)."""
        body = {"error": {"code": "0x80048d04", "message": "Concurrency mismatch"}}
        with requests_mock.Mocker() as m:
            m.patch(
                f"{profile.api_base}accounts(00000000-0000-0000-0000-000000000001)",
                status_code=412, json=body,
            )
            with pytest.raises(D365Error) as exc_info:
                backend.patch(
                    "accounts(00000000-0000-0000-0000-000000000001)",
                    json_body={"name": "a"},
                    etag='W/"1"',
                )
            assert exc_info.value.status == 412
            assert exc_info.value.code == "0x80048d04"

    def test_412_no_body_code_falls_back_to_precondition_failed(self, backend, profile):
        """412 with no D365 code in the body falls back to PreconditionFailed."""
        body = {"error": {"code": "", "message": "Concurrency mismatch"}}
        with requests_mock.Mocker() as m:
            m.patch(
                f"{profile.api_base}accounts(00000000-0000-0000-0000-000000000001)",
                status_code=412, json=body,
            )
            with pytest.raises(D365Error) as exc_info:
                backend.patch(
                    "accounts(00000000-0000-0000-0000-000000000001)",
                    json_body={"name": "a"},
                    etag='W/"1"',
                )
            assert exc_info.value.status == 412
            assert exc_info.value.code == "PreconditionFailed"

    def test_403_priv_bypass_keeps_server_code_classifies_forbidden(self, backend, profile):
        """The fragile MissingPrivilege substring synthesis is subsumed by the
        taxonomy (#62): the server's own code is preserved and the 403
        classifies as `forbidden` — no message-substring code rewriting."""
        body = {"error": {
            "code": "0x80040220",
            "message": "User does not have prvBypassCustomPluginExecution privilege",
        }}
        with requests_mock.Mocker() as m:
            m.post(f"{profile.api_base}accounts", status_code=403, json=body)
            with pytest.raises(D365Error) as exc_info:
                backend.post("accounts", json_body={"name": "a"},
                             bypass_custom_plugin_execution=True)
            assert exc_info.value.status == 403
            assert exc_info.value.code == "0x80040220"
            assert classify_d365_error(
                exc_info.value.status, exc_info.value.code, str(exc_info.value)
            ) == ("forbidden", False)

    def test_403_without_priv_keyword_keeps_server_code(self, backend, profile):
        body = {"error": {"code": "0x80040220", "message": "Insufficient privileges"}}
        with requests_mock.Mocker() as m:
            m.post(f"{profile.api_base}accounts", status_code=403, json=body)
            with pytest.raises(D365Error) as exc_info:
                backend.post("accounts", json_body={"name": "a"})
            assert exc_info.value.status == 403
            assert exc_info.value.code == "0x80040220"
