"""Unit tests for crm.core.solution.update_solution (solution set-version).

update_solution resolves the solutionid via solution_info, refuses managed
solutions and patches client-side (the server rejects a patch version bump with
CannotUpdateSolutionPatch), builds a payload of only the supplied fields, and
delegates to the shared entity.update record-update path (If-Match:* + dry-run
reused, no new HTTP). All HTTP is mocked via requests_mock; no live D365 server.
"""
# pyright: basic
from __future__ import annotations

import json

import pytest
import requests_mock
from click.testing import CliRunner

from crm.cli import cli
from crm.core import solution as sol_mod
from crm.utils.d365_backend import ConnectionProfile, D365Backend, D365Error


@pytest.fixture
def profile() -> ConnectionProfile:
    return ConnectionProfile(
        name="testp", url="https://crm.contoso.local/contoso",
        domain="CONTOSO", username="alice", api_version="v9.2", verify_ssl=False,
    )


@pytest.fixture
def backend(profile):
    return D365Backend(profile, password="pw", dry_run=False)


@pytest.fixture
def dry_backend(profile):
    return D365Backend(profile, password="pw", dry_run=True)


_SOL_ID = "22222222-2222-2222-2222-222222222222"


def _patches(m):
    return [r for r in m.request_history if r.method == "PATCH"]


def _unmanaged_row(**extra):
    row = {"solutionid": _SOL_ID, "uniquename": "CRMWorx",
           "ismanaged": False, "_parentsolutionid_value": None}
    row.update(extra)
    return row


class TestUpdateSolution:
    def test_version_bump_patches_solution(self, backend):
        with requests_mock.Mocker() as m:
            m.get(backend.url_for("solutions"), json={"value": [_unmanaged_row()]})
            m.patch(backend.url_for(f"solutions({_SOL_ID})"), status_code=204)
            out = sol_mod.update_solution(backend, "CRMWorx", version="2.0.0.0")
        assert out["updated"] is True
        assert out["solutionid"] == _SOL_ID
        body = _patches(m)[0].json()
        assert body == {"version": "2.0.0.0"}

    def test_all_none_raises_pre_http(self, backend):
        with requests_mock.Mocker() as m:
            with pytest.raises(D365Error, match="nothing to update"):
                sol_mod.update_solution(backend, "CRMWorx")
            assert m.request_history == []  # validation precedes any HTTP

    def test_unique_name_single_quote_escaped_in_filter(self, backend):
        from urllib.parse import unquote
        with requests_mock.Mocker() as m:
            m.get(backend.url_for("solutions"), json={"value": [_unmanaged_row()]})
            m.patch(backend.url_for(f"solutions({_SOL_ID})"), status_code=204)
            sol_mod.update_solution(backend, "O'Brien", version="2.0.0.0")
        get_url = unquote([r for r in m.request_history if r.method == "GET"][0].url)
        # OData literal escaping: ' -> '' guards against $filter injection
        assert "'O''Brien'" in get_url

    def test_friendly_name_and_description_only(self, backend):
        with requests_mock.Mocker() as m:
            m.get(backend.url_for("solutions"), json={"value": [_unmanaged_row()]})
            m.patch(backend.url_for(f"solutions({_SOL_ID})"), status_code=204)
            out = sol_mod.update_solution(
                backend, "CRMWorx", friendly_name="CRM Worx", description="prod build")
        assert out["updated"] is True
        # only the supplied fields land in the payload; version is absent
        assert _patches(m)[0].json() == {
            "friendlyname": "CRM Worx", "description": "prod build"}

    def test_patch_target_fails_fast_no_patch(self, backend):
        patch_row = _unmanaged_row(
            _parentsolutionid_value="99999999-9999-9999-9999-999999999999")
        with requests_mock.Mocker() as m:
            m.get(backend.url_for("solutions"), json={"value": [patch_row]})
            m.patch(backend.url_for(f"solutions({_SOL_ID})"), status_code=204)
            with pytest.raises(D365Error, match="patch"):
                sol_mod.update_solution(backend, "CRMWorx", version="2.0.0.0")
            assert _patches(m) == []  # never reaches the PATCH

    def test_managed_solution_fails_fast_no_patch(self, backend):
        managed_row = _unmanaged_row(ismanaged=True)
        with requests_mock.Mocker() as m:
            m.get(backend.url_for("solutions"), json={"value": [managed_row]})
            m.patch(backend.url_for(f"solutions({_SOL_ID})"), status_code=204)
            with pytest.raises(D365Error, match="managed"):
                sol_mod.update_solution(backend, "CRMWorx", version="2.0.0.0")
            assert _patches(m) == []

    def test_dry_run_previews_no_patch(self, dry_backend):
        with requests_mock.Mocker() as m:
            # solution_info is a forced-real read even under --dry-run (mirrors create)
            m.get(dry_backend.url_for("solutions"), json={"value": [_unmanaged_row()]})
            m.patch(dry_backend.url_for(f"solutions({_SOL_ID})"), status_code=204)
            out = sol_mod.update_solution(dry_backend, "CRMWorx", version="2.0.0.0")
        assert out["_dry_run"] is True
        assert out["body"] == {"version": "2.0.0.0"}
        assert out["solutionid"] == _SOL_ID
        assert _patches(m) == []  # no real PATCH under dry-run
        assert any(r.method == "GET" for r in m.request_history)  # forced-real resolve

    @pytest.mark.parametrize("bad_version", [
        "2.0",          # too few parts
        "1.0.0",        # 3-part (Dataverse version is 4-part)
        "1.0.0.0.0",    # too many parts
        "1.0.0.x",      # non-numeric segment
        "v2.0.0.0",     # leading non-numeric
        "1.0.0.",       # trailing dot
    ])
    def test_invalid_version_raises_pre_http(self, backend, bad_version):
        with requests_mock.Mocker() as m:
            with pytest.raises(D365Error, match="4-part dotted"):
                sol_mod.update_solution(backend, "CRMWorx", version=bad_version)
            assert m.request_history == []  # validation precedes any HTTP


# ── command wiring + exit codes ─────────────────────────────────────────────


class TestSolutionSetVersionCommand:
    def test_wires_core_with_all_fields(self, monkeypatch):
        captured = {}

        def fake(backend, unique_name, **kw):
            captured["unique_name"] = unique_name
            captured.update(kw)
            return {"updated": True, "solutionid": _SOL_ID, "uniquename": unique_name}

        monkeypatch.setattr("crm.core.solution.update_solution", fake)
        monkeypatch.setattr("crm.cli.CLIContext.backend", lambda self: object())
        result = CliRunner().invoke(cli, [
            "--json", "solution", "set-version", "CRMWorx",
            "--version", "2.0.0.0",
            "--friendly-name", "CRM Worx", "--description", "prod build",
        ])
        assert result.exit_code == 0, result.output
        assert captured == {
            "unique_name": "CRMWorx", "version": "2.0.0.0",
            "friendly_name": "CRM Worx", "description": "prod build"}
        assert json.loads(result.output)["data"]["updated"] is True

    def test_version_only_passes_none_for_omitted(self, monkeypatch):
        captured = {}
        monkeypatch.setattr(
            "crm.core.solution.update_solution",
            lambda backend, unique_name, **kw: captured.update(kw) or {"updated": True})
        monkeypatch.setattr("crm.cli.CLIContext.backend", lambda self: object())
        result = CliRunner().invoke(cli, [
            "--json", "solution", "set-version", "CRMWorx", "--version", "2.0.0.0"])
        assert result.exit_code == 0, result.output
        assert captured == {"version": "2.0.0.0",
                            "friendly_name": None, "description": None}

    def test_core_error_exit_1(self, monkeypatch):
        def boom(backend, unique_name, **kw):
            raise D365Error("nothing to update: pass version, friendly_name, or description.")

        monkeypatch.setattr("crm.core.solution.update_solution", boom)
        monkeypatch.setattr("crm.cli.CLIContext.backend", lambda self: object())
        result = CliRunner().invoke(cli, ["--json", "solution", "set-version", "CRMWorx"])
        assert result.exit_code == 1, result.output
        assert json.loads(result.output)["ok"] is False
