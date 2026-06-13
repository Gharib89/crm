"""Unit tests for crm.core.solution create_publisher / create_solution.

The publisher + solution Web API contract (entity sets `publishers` / `solutions`,
`customizationprefix` 2-8 alnum not 'mscrm', `customizationoptionvalueprefix`
10000-99999, solution `publisherid@odata.bind`) is verified against the on-prem
9.1 docs. All HTTP is mocked via requests_mock; no live D365 server.
"""
# pyright: basic
from __future__ import annotations

import json

import pytest
import requests_mock
from click.testing import CliRunner

from crm.cli import cli
from crm.core import solution as sol_mod
from crm.utils.d365_backend import ConnectionProfile, D365Error


_PUB_ID = "11111111-1111-1111-1111-111111111111"
_SOL_ID = "22222222-2222-2222-2222-222222222222"


def _posts(m):
    return [r for r in m.request_history if r.method == "POST"]


def _gets(m):
    return [r for r in m.request_history if r.method == "GET"]


# ── create_publisher ──────────────────────────────────────────────────────


class TestCreatePublisher:
    def test_posts_expected_body_and_parses_id(self, backend):
        pub_url = backend.url_for(f"publishers({_PUB_ID})")
        with requests_mock.Mocker() as m:
            m.get(backend.url_for("publishers"), json={"value": []})  # existence guard
            m.post(backend.url_for("publishers"), status_code=204,
                   headers={"OData-EntityId": pub_url})
            out = sol_mod.create_publisher(
                backend, name="crmworx", prefix="cwx", option_value_prefix=30000)
        assert out["created"] is True
        assert out["publisherid"] == _PUB_ID
        body = _posts(m)[0].json()
        assert body["uniquename"] == "crmworx"
        assert body["customizationprefix"] == "cwx"
        assert body["customizationoptionvalueprefix"] == 30000
        # --display omitted → friendlyname defaults to the unique name
        assert body["friendlyname"] == "crmworx"

    def test_friendly_name_override(self, backend):
        with requests_mock.Mocker() as m:
            m.get(backend.url_for("publishers"), json={"value": []})
            m.post(backend.url_for("publishers"), status_code=204,
                   headers={"OData-EntityId": backend.url_for(f"publishers({_PUB_ID})")})
            sol_mod.create_publisher(
                backend, name="crmworx", friendly_name="CRMWorx Publisher",
                prefix="cwx", option_value_prefix=30000)
        assert _posts(m)[0].json()["friendlyname"] == "CRMWorx Publisher"

    @pytest.mark.parametrize("bad_prefix", [
        "c",          # too short (<2)
        "toolongpfx",  # too long (>8)
        "cw-x",       # non-alphanumeric
        "1cwx",       # must start with a letter
        "mscrmx",     # reserved 'mscrm' prefix
    ])
    def test_rejects_invalid_prefix_before_post(self, backend, bad_prefix):
        with requests_mock.Mocker() as m:
            with pytest.raises(D365Error):
                sol_mod.create_publisher(
                    backend, name="crmworx", prefix=bad_prefix, option_value_prefix=30000)
            assert _posts(m) == []
            assert _gets(m) == []  # validation precedes any HTTP

    @pytest.mark.parametrize("bad_ovp", [9999, 100000])
    def test_rejects_option_value_prefix_out_of_range(self, backend, bad_ovp):
        with requests_mock.Mocker() as m:
            with pytest.raises(D365Error, match="10000"):
                sol_mod.create_publisher(
                    backend, name="crmworx", prefix="cwx", option_value_prefix=bad_ovp)
            assert _posts(m) == []

    def test_duplicate_uniquename_error_no_post(self, backend):
        with requests_mock.Mocker() as m:
            m.get(backend.url_for("publishers"),
                  json={"value": [{"publisherid": _PUB_ID, "uniquename": "crmworx"}]})
            with pytest.raises(D365Error, match="exists"):
                sol_mod.create_publisher(
                    backend, name="crmworx", prefix="cwx", option_value_prefix=30000)
            assert _posts(m) == []

    def test_duplicate_uniquename_skip_no_post(self, backend):
        with requests_mock.Mocker() as m:
            m.get(backend.url_for("publishers"),
                  json={"value": [{"publisherid": _PUB_ID, "uniquename": "crmworx"}]})
            out = sol_mod.create_publisher(
                backend, name="crmworx", prefix="cwx", option_value_prefix=30000,
                if_exists="skip")
        assert out["skipped"] is True
        assert out["exists"] is True
        assert out["publisherid"] == _PUB_ID
        assert _posts(m) == []

    def test_dry_run_previews_no_post(self, dry_backend):
        with requests_mock.Mocker() as m:
            m.get(dry_backend.url_for("publishers"), json={"value": []})
            out = sol_mod.create_publisher(
                dry_backend, name="crmworx", prefix="cwx", option_value_prefix=30000)
        assert out["_dry_run"] is True
        assert out["_exists"] is False
        assert any(r.method == "GET" for r in m.request_history)
        assert _posts(m) == []


# ── create_solution ────────────────────────────────────────────────────────


class TestCreateSolution:
    def _mock_resolve_publisher(self, m, backend, rows):
        m.get(backend.url_for("publishers"), json={"value": rows})

    def test_resolves_publisher_and_binds(self, backend):
        sol_url = backend.url_for(f"solutions({_SOL_ID})")
        with requests_mock.Mocker() as m:
            m.get(backend.url_for("solutions"), json={"value": []})  # existence
            self._mock_resolve_publisher(m, backend, [{"publisherid": _PUB_ID}])
            m.post(backend.url_for("solutions"), status_code=204,
                   headers={"OData-EntityId": sol_url})
            out = sol_mod.create_solution(
                backend, name="CRMWorx", publisher_unique_name="crmworx")
        assert out["created"] is True
        assert out["solutionid"] == _SOL_ID
        assert out["publisherid"] == _PUB_ID
        body = _posts(m)[0].json()
        assert body["uniquename"] == "CRMWorx"
        assert body["friendlyname"] == "CRMWorx"   # default from name
        assert body["version"] == "1.0.0.0"        # default version
        assert body["publisherid@odata.bind"] == f"/publishers({_PUB_ID})"

    def test_display_and_version_overrides(self, backend):
        with requests_mock.Mocker() as m:
            m.get(backend.url_for("solutions"), json={"value": []})
            self._mock_resolve_publisher(m, backend, [{"publisherid": _PUB_ID}])
            m.post(backend.url_for("solutions"), status_code=204,
                   headers={"OData-EntityId": backend.url_for(f"solutions({_SOL_ID})")})
            sol_mod.create_solution(
                backend, name="CRMWorx", friendly_name="CRM Worx",
                version="2.1.0.0", publisher_unique_name="crmworx")
        body = _posts(m)[0].json()
        assert body["friendlyname"] == "CRM Worx"
        assert body["version"] == "2.1.0.0"

    def test_publisher_id_binds_without_resolution_get(self, backend):
        with requests_mock.Mocker() as m:
            m.get(backend.url_for("solutions"), json={"value": []})
            m.post(backend.url_for("solutions"), status_code=204,
                   headers={"OData-EntityId": backend.url_for(f"solutions({_SOL_ID})")})
            out = sol_mod.create_solution(
                backend, name="CRMWorx", publisher_id=_PUB_ID)
        assert out["created"] is True
        body = _posts(m)[0].json()
        assert body["publisherid@odata.bind"] == f"/publishers({_PUB_ID})"
        # publisher id supplied directly → no lookup against /publishers
        assert not any("publishers" in r.url for r in _gets(m))

    def test_publisher_not_found_raises_no_orphan(self, backend):
        with requests_mock.Mocker() as m:
            m.get(backend.url_for("solutions"), json={"value": []})
            self._mock_resolve_publisher(m, backend, [])  # publisher missing
            with pytest.raises(D365Error, match="[Pp]ublisher not found"):
                sol_mod.create_solution(
                    backend, name="CRMWorx", publisher_unique_name="ghost")
            assert _posts(m) == []  # no orphan solution created

    def test_neither_publisher_flag_raises(self, backend):
        with requests_mock.Mocker() as m:
            m.get(backend.url_for("solutions"), json={"value": []})
            with pytest.raises(D365Error, match="[Pp]ublisher"):
                sol_mod.create_solution(backend, name="CRMWorx")
            assert _posts(m) == []

    def test_duplicate_uniquename_error_no_post(self, backend):
        with requests_mock.Mocker() as m:
            m.get(backend.url_for("solutions"),
                  json={"value": [{"solutionid": _SOL_ID, "uniquename": "CRMWorx"}]})
            with pytest.raises(D365Error, match="exists"):
                sol_mod.create_solution(
                    backend, name="CRMWorx", publisher_unique_name="crmworx")
            assert _posts(m) == []

    def test_duplicate_uniquename_skip_no_post(self, backend):
        with requests_mock.Mocker() as m:
            m.get(backend.url_for("solutions"),
                  json={"value": [{"solutionid": _SOL_ID, "uniquename": "CRMWorx"}]})
            out = sol_mod.create_solution(
                backend, name="CRMWorx", publisher_unique_name="crmworx",
                if_exists="skip")
        assert out["skipped"] is True
        assert out["solutionid"] == _SOL_ID
        assert _posts(m) == []

    def test_dry_run_previews_no_post(self, dry_backend):
        with requests_mock.Mocker() as m:
            m.get(dry_backend.url_for("solutions"), json={"value": []})
            m.get(dry_backend.url_for("publishers"), json={"value": [{"publisherid": _PUB_ID}]})
            out = sol_mod.create_solution(
                dry_backend, name="CRMWorx", publisher_unique_name="crmworx")
        assert out["_dry_run"] is True
        assert out["_exists"] is False
        # dry-run preview still carries the resolved publisher bind in the body
        assert out["body"]["publisherid@odata.bind"] == f"/publishers({_PUB_ID})"
        assert _posts(m) == []


# ── command wiring + exit codes + profile auto-wire ─────────────────────────


def _named_profile() -> ConnectionProfile:
    return ConnectionProfile(
        name="crmworx", url="https://crm.contoso.local/contoso",
        domain="CONTOSO", username="alice",
    )


class TestSolutionCreateCommands:
    def test_create_publisher_wires_core_and_reports_env_skip(self, monkeypatch):
        captured = {}

        def fake(backend, **kw):
            captured.update(kw)
            return {"created": True, "publisherid": _PUB_ID, "uniquename": kw["name"]}

        monkeypatch.setattr("crm.core.solution.create_publisher", fake)
        monkeypatch.setattr("crm.cli.CLIContext.backend", lambda self: object())
        monkeypatch.setattr("crm.commands.solution._active_profile", lambda ctx: None)
        result = CliRunner().invoke(cli, [
            "--json", "solution", "create-publisher",
            "--name", "crmworx", "--prefix", "cwx", "--option-value-prefix", "30000",
        ])
        assert result.exit_code == 0, result.output
        assert captured["name"] == "crmworx"
        assert captured["prefix"] == "cwx"
        assert captured["option_value_prefix"] == 30000
        env = json.loads(result.output)
        assert env["data"]["profile_update"] == "skipped: no named profile"

    def test_create_publisher_missing_ovp_exit_2(self, monkeypatch):
        monkeypatch.setattr("crm.cli.CLIContext.backend", lambda self: object())
        result = CliRunner().invoke(cli, [
            "--json", "solution", "create-publisher", "--name", "crmworx", "--prefix", "cwx",
        ])
        assert result.exit_code == 2

    def test_create_publisher_bad_if_exists_exit_2(self, monkeypatch):
        monkeypatch.setattr("crm.cli.CLIContext.backend", lambda self: object())
        result = CliRunner().invoke(cli, [
            "--json", "solution", "create-publisher", "--name", "crmworx",
            "--prefix", "cwx", "--option-value-prefix", "30000", "--if-exists", "bogus",
        ])
        assert result.exit_code == 2

    def test_create_publisher_core_error_exit_1(self, monkeypatch):
        def boom(backend, **kw):
            raise D365Error("customizationprefix invalid")

        monkeypatch.setattr("crm.core.solution.create_publisher", boom)
        monkeypatch.setattr("crm.cli.CLIContext.backend", lambda self: object())
        monkeypatch.setattr("crm.commands.solution._active_profile", lambda ctx: None)
        result = CliRunner().invoke(cli, [
            "--json", "solution", "create-publisher",
            "--name", "crmworx", "--prefix", "cwx", "--option-value-prefix", "30000",
        ])
        assert result.exit_code == 1, result.output
        assert json.loads(result.output)["ok"] is False

    def test_create_publisher_autowires_named_profile(self, monkeypatch):
        prof = _named_profile()
        saved = {}
        monkeypatch.setattr("crm.core.solution.create_publisher",
                            lambda backend, **kw: {"created": True, "publisherid": _PUB_ID})
        monkeypatch.setattr("crm.cli.CLIContext.backend", lambda self: object())
        monkeypatch.setattr("crm.commands.solution._active_profile", lambda ctx: prof)
        monkeypatch.setattr(
            "crm.commands.solution.session_mod.save_profile",
            lambda p: saved.update({"name": p.name, "prefix": p.publisher_prefix}))
        result = CliRunner().invoke(cli, [
            "--json", "solution", "create-publisher",
            "--name", "crmworx", "--prefix", "cwx", "--option-value-prefix", "30000",
        ])
        assert result.exit_code == 0, result.output
        assert saved == {"name": "crmworx", "prefix": "cwx"}
        env = json.loads(result.output)
        assert env["data"]["profile_updated"]["publisher_prefix"] == "cwx"

    def test_create_publisher_no_set_default_skips_wire(self, monkeypatch):
        prof = _named_profile()
        saved = {"called": False}
        monkeypatch.setattr("crm.core.solution.create_publisher",
                            lambda backend, **kw: {"created": True, "publisherid": _PUB_ID})
        monkeypatch.setattr("crm.cli.CLIContext.backend", lambda self: object())
        monkeypatch.setattr("crm.commands.solution._active_profile", lambda ctx: prof)
        monkeypatch.setattr("crm.commands.solution.session_mod.save_profile",
                            lambda p: saved.update({"called": True}))
        result = CliRunner().invoke(cli, [
            "--json", "solution", "create-publisher",
            "--name", "crmworx", "--prefix", "cwx", "--option-value-prefix", "30000",
            "--no-set-default",
        ])
        assert result.exit_code == 0, result.output
        assert saved["called"] is False

    def test_create_publisher_dry_run_never_writes_profile(self, monkeypatch):
        prof = _named_profile()
        saved = {"called": False}
        monkeypatch.setattr("crm.core.solution.create_publisher",
                            lambda backend, **kw: {"_dry_run": True, "_exists": False})
        monkeypatch.setattr("crm.cli.CLIContext.backend", lambda self: object())
        monkeypatch.setattr("crm.commands.solution._active_profile", lambda ctx: prof)
        monkeypatch.setattr("crm.commands.solution.session_mod.save_profile",
                            lambda p: saved.update({"called": True}))
        result = CliRunner().invoke(cli, [
            "--json", "--dry-run", "solution", "create-publisher",
            "--name", "crmworx", "--prefix", "cwx", "--option-value-prefix", "30000",
        ])
        assert result.exit_code == 0, result.output
        assert saved["called"] is False

    def test_create_solution_wires_core_and_autowires_default(self, monkeypatch):
        prof = _named_profile()
        captured = {}
        saved = {}

        def fake(backend, **kw):
            captured.update(kw)
            return {"created": True, "solutionid": _SOL_ID, "uniquename": kw["name"]}

        monkeypatch.setattr("crm.core.solution.create_solution", fake)
        monkeypatch.setattr("crm.cli.CLIContext.backend", lambda self: object())
        monkeypatch.setattr("crm.commands.solution._active_profile", lambda ctx: prof)
        monkeypatch.setattr(
            "crm.commands.solution.session_mod.save_profile",
            lambda p: saved.update({"name": p.name, "default_solution": p.default_solution}))
        result = CliRunner().invoke(cli, [
            "--json", "solution", "create",
            "--name", "CRMWorx", "--publisher", "crmworx",
        ])
        assert result.exit_code == 0, result.output
        assert captured["publisher_unique_name"] == "crmworx"
        assert captured["publisher_id"] is None
        assert saved == {"name": "crmworx", "default_solution": "CRMWorx"}
        env = json.loads(result.output)
        assert env["data"]["profile_updated"]["default_solution"] == "CRMWorx"

    def test_create_solution_both_publisher_flags_exit_1(self, monkeypatch):
        monkeypatch.setattr("crm.cli.CLIContext.backend", lambda self: object())
        result = CliRunner().invoke(cli, [
            "--json", "solution", "create", "--name", "CRMWorx",
            "--publisher", "crmworx", "--publisher-id", _PUB_ID,
        ])
        assert result.exit_code == 1, result.output
        assert json.loads(result.output)["ok"] is False

    def test_create_solution_neither_publisher_flag_exit_1(self, monkeypatch):
        monkeypatch.setattr("crm.cli.CLIContext.backend", lambda self: object())
        result = CliRunner().invoke(cli, [
            "--json", "solution", "create", "--name", "CRMWorx",
        ])
        assert result.exit_code == 1, result.output
        assert json.loads(result.output)["ok"] is False
