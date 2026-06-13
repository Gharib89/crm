"""Unit tests for the managed-solution lifecycle verbs (#196).

Covers crm.core.solution.clone_as_patch / delete_and_promote / uninstall_solution
and the holding-import flag on import_solution, plus command wiring. The Web API
contracts are verified against the op-9-1 / Dataverse references:

- CloneAsPatch (unbound action): body {ParentSolutionUniqueName, DisplayName,
  VersionNumber}; CloneAsPatchResponse.SolutionId.
- DeleteAndPromote (unbound action): body {UniqueName}; response SolutionId.
- holding import: ImportSolution(Async) HoldingSolution:true.
- uninstall: DELETE /solutions(<id>).

All HTTP is mocked via requests_mock; no live D365 server.
"""
# pyright: basic
from __future__ import annotations

import json

import pytest
import requests_mock
from click.testing import CliRunner

from crm.cli import cli
from crm.core import solution as sol_mod
from crm.utils.d365_backend import D365Error


_PATCH_ID = "33333333-3333-3333-3333-333333333333"
_SOL_ID = "22222222-2222-2222-2222-222222222222"


def _posts(m):
    return [r for r in m.request_history if r.method == "POST"]


# ── clone_as_patch ──────────────────────────────────────────────────────────


class TestCloneAsPatch:
    def test_posts_expected_body_and_parses_id(self, backend):
        with requests_mock.Mocker() as m:
            m.get(backend.url_for("solutions"),
                  json={"value": [{"solutionid": _SOL_ID, "uniquename": "CRMWorx",
                                   "friendlyname": "CRM Worx", "version": "1.0.0.0",
                                   "ismanaged": False}]})
            m.post(backend.url_for("CloneAsPatch"), json={"SolutionId": _PATCH_ID})
            out = sol_mod.clone_as_patch(
                backend, parent_solution="CRMWorx", version="1.0.0.1")
        assert out["cloned"] is True
        assert out["patch_solutionid"] == _PATCH_ID
        body = _posts(m)[-1].json()
        assert body["ParentSolutionUniqueName"] == "CRMWorx"
        assert body["VersionNumber"] == "1.0.0.1"

    def test_auto_bumps_revision_when_version_omitted(self, backend):
        with requests_mock.Mocker() as m:
            m.get(backend.url_for("solutions"),
                  json={"value": [{"solutionid": _SOL_ID, "uniquename": "CRMWorx",
                                   "friendlyname": "CRM Worx", "version": "2.3.4.5",
                                   "ismanaged": False}]})
            m.post(backend.url_for("CloneAsPatch"), json={"SolutionId": _PATCH_ID})
            out = sol_mod.clone_as_patch(backend, parent_solution="CRMWorx")
        # revision (4th part) bumped; major.minor preserved
        assert out["version"] == "2.3.4.6"
        assert _posts(m)[-1].json()["VersionNumber"] == "2.3.4.6"

    def test_display_name_defaults_to_parent_friendlyname(self, backend):
        with requests_mock.Mocker() as m:
            m.get(backend.url_for("solutions"),
                  json={"value": [{"solutionid": _SOL_ID, "uniquename": "CRMWorx",
                                   "friendlyname": "CRM Worx", "version": "1.0.0.0",
                                   "ismanaged": False}]})
            m.post(backend.url_for("CloneAsPatch"), json={"SolutionId": _PATCH_ID})
            sol_mod.clone_as_patch(backend, parent_solution="CRMWorx", version="1.0.0.1")
        assert _posts(m)[-1].json()["DisplayName"] == "CRM Worx"

    def test_dry_run_previews_no_post(self, dry_backend):
        with requests_mock.Mocker() as m:
            m.get(dry_backend.url_for("solutions"),
                  json={"value": [{"solutionid": _SOL_ID, "uniquename": "CRMWorx",
                                   "friendlyname": "CRM Worx", "version": "1.0.0.0",
                                   "ismanaged": False}]})
            out = sol_mod.clone_as_patch(dry_backend, parent_solution="CRMWorx")
        assert out["_dry_run"] is True
        assert "cloned" not in out
        assert _posts(m) == []

    def test_parent_not_found_raises_no_post(self, backend):
        with requests_mock.Mocker() as m:
            m.get(backend.url_for("solutions"), json={"value": []})
            with pytest.raises(D365Error, match="[Nn]ot found"):
                sol_mod.clone_as_patch(backend, parent_solution="ghost")
            assert _posts(m) == []


# ── delete_and_promote ──────────────────────────────────────────────────────


class TestDeleteAndPromote:
    def test_posts_unique_name_and_parses_id(self, backend):
        with requests_mock.Mocker() as m:
            m.post(backend.url_for("DeleteAndPromote"), json={"SolutionId": _SOL_ID})
            out = sol_mod.delete_and_promote(backend, "CRMWorx")
        assert out["promoted"] is True
        assert out["solution"] == "CRMWorx"
        assert out["solutionid"] == _SOL_ID
        assert _posts(m)[-1].json() == {"UniqueName": "CRMWorx"}

    def test_dry_run_previews_no_post(self, dry_backend):
        with requests_mock.Mocker() as m:
            out = sol_mod.delete_and_promote(dry_backend, "CRMWorx")
        assert out["_dry_run"] is True
        assert "promoted" not in out
        assert _posts(m) == []


# ── import_solution holding flag ─────────────────────────────────────────────


class TestHoldingImport:
    def test_holding_solution_flag_in_body(self, dry_backend, tmp_path):
        zip_path = tmp_path / "in.zip"
        zip_path.write_bytes(b"PK\x03\x04stub")
        out = sol_mod.import_solution(dry_backend, zip_path, holding_solution=True)
        assert out["_dry_run"] is True
        assert out["body"]["HoldingSolution"] is True

    def test_holding_solution_default_false(self, dry_backend, tmp_path):
        zip_path = tmp_path / "in.zip"
        zip_path.write_bytes(b"PK\x03\x04stub")
        out = sol_mod.import_solution(dry_backend, zip_path)
        assert out["body"]["HoldingSolution"] is False


# ── uninstall_solution ──────────────────────────────────────────────────────


def _dels(m):
    return [r for r in m.request_history if r.method == "DELETE"]


class TestUninstall:
    def _mock_info(self, m, backend, managed=True):
        m.get(backend.url_for("solutions"),
              json={"value": [{"solutionid": _SOL_ID, "uniquename": "ManagedApp",
                               "ismanaged": managed}]})

    def test_no_blockers_deletes(self, backend):
        with requests_mock.Mocker() as m:
            # ANY first (RetrieveDependenciesForUninstall), specific solutions GET
            # last so last-registered-wins routes solution_info correctly.
            m.get(requests_mock.ANY, json={"value": []})
            self._mock_info(m, backend)
            m.delete(backend.url_for(f"solutions({_SOL_ID})"), status_code=204)
            out = sol_mod.uninstall_solution(backend, "ManagedApp")
        assert out["uninstalled"] is True
        assert out["solution"] == "ManagedApp"
        assert out["solutionid"] == _SOL_ID
        assert len(_dels(m)) == 1

    def test_blockers_refuse_no_delete(self, backend):
        with requests_mock.Mocker() as m:
            m.get(requests_mock.ANY,
                  json={"value": [{"dependentcomponenttype": 1,
                                   "dependentcomponentobjectid": _PATCH_ID}]})
            self._mock_info(m, backend)
            m.delete(backend.url_for(f"solutions({_SOL_ID})"), status_code=204)
            with pytest.raises(D365Error, match="blocker"):
                sol_mod.uninstall_solution(backend, "ManagedApp")
            assert _dels(m) == []

    def test_force_deletes_despite_blockers(self, backend):
        with requests_mock.Mocker() as m:
            m.get(requests_mock.ANY,
                  json={"value": [{"dependentcomponenttype": 1,
                                   "dependentcomponentobjectid": _PATCH_ID}]})
            self._mock_info(m, backend)
            m.delete(backend.url_for(f"solutions({_SOL_ID})"), status_code=204)
            out = sol_mod.uninstall_solution(backend, "ManagedApp", force=True)
        assert out["uninstalled"] is True
        assert len(_dels(m)) == 1

    def test_dry_run_previews_no_delete(self, dry_backend):
        with requests_mock.Mocker() as m:
            m.get(requests_mock.ANY, json={"value": []})
            self._mock_info(m, dry_backend)
            out = sol_mod.uninstall_solution(dry_backend, "ManagedApp")
        assert out["_dry_run"] is True
        assert out["solutionid"] == _SOL_ID
        assert _dels(m) == []

    def test_not_found_raises_no_delete(self, backend):
        with requests_mock.Mocker() as m:
            m.get(backend.url_for("solutions"), json={"value": []})
            with pytest.raises(D365Error, match="[Nn]ot found"):
                sol_mod.uninstall_solution(backend, "ghost")
            assert _dels(m) == []


# ── command wiring + exit codes ──────────────────────────────────────────────


class TestCloneAsPatchCommand:
    def test_wires_core_and_emits_json(self, monkeypatch):
        captured = {}

        def fake(backend, **kw):
            captured.update(kw)
            return {"cloned": True, "patch_solutionid": _PATCH_ID, "version": "1.0.0.1"}

        monkeypatch.setattr("crm.core.solution.clone_as_patch", fake)
        monkeypatch.setattr("crm.cli.CLIContext.backend", lambda self: object())
        result = CliRunner().invoke(cli, [
            "--json", "solution", "clone-as-patch", "--solution", "CRMWorx",
        ])
        assert result.exit_code == 0, result.output
        assert captured["parent_solution"] == "CRMWorx"
        assert json.loads(result.output)["data"]["patch_solutionid"] == _PATCH_ID

    def test_core_error_exit_1(self, monkeypatch):
        def boom(backend, **kw):
            raise D365Error("bad version")

        monkeypatch.setattr("crm.core.solution.clone_as_patch", boom)
        monkeypatch.setattr("crm.cli.CLIContext.backend", lambda self: object())
        result = CliRunner().invoke(cli, [
            "--json", "solution", "clone-as-patch", "--solution", "CRMWorx",
        ])
        assert result.exit_code == 1, result.output
        assert json.loads(result.output)["ok"] is False


class TestUninstallCommand:
    def test_wires_force_and_yes(self, monkeypatch):
        captured = {}

        def fake(backend, unique_name, *, force):
            captured.update(unique_name=unique_name, force=force)
            return {"uninstalled": True, "solution": unique_name, "solutionid": _SOL_ID}

        monkeypatch.setattr("crm.core.solution.uninstall_solution", fake)
        monkeypatch.setattr("crm.cli.CLIContext.backend", lambda self: object())
        result = CliRunner().invoke(cli, [
            "--json", "solution", "uninstall", "--solution", "ManagedApp",
            "--force", "--yes",
        ])
        assert result.exit_code == 0, result.output
        assert captured == {"unique_name": "ManagedApp", "force": True}

    def test_abort_without_yes_on_non_tty(self, monkeypatch):
        # No --yes + EOF stdin → _confirm_destructive returns False → exit 1.
        called = {"core": False}
        monkeypatch.setattr("crm.core.solution.uninstall_solution",
                            lambda *a, **k: called.update(core=True))
        monkeypatch.setattr("crm.cli.CLIContext.backend", lambda self: object())
        result = CliRunner().invoke(cli, [
            "--json", "solution", "uninstall", "--solution", "ManagedApp",
        ])
        assert result.exit_code == 1, result.output
        assert called["core"] is False
        assert "aborted by user" in result.output


class TestStageAndUpgradeCommand:
    def test_stages_holding_import(self, monkeypatch, tmp_path):
        zip_path = tmp_path / "up.zip"
        zip_path.write_bytes(b"PK\x03\x04stub")
        captured = {}

        def fake_import(backend, path, **kw):
            captured.update(kw)
            return {"status": "succeeded", "import_job_id": "x"}

        promoted = {"called": False}
        monkeypatch.setattr("crm.core.solution.import_solution", fake_import)
        monkeypatch.setattr("crm.core.solution.delete_and_promote",
                            lambda *a, **k: promoted.update(called=True))
        monkeypatch.setattr("crm.cli.CLIContext.backend", lambda self: object())
        result = CliRunner().invoke(cli, [
            "--json", "solution", "stage-and-upgrade", str(zip_path), "--yes",
        ])
        assert result.exit_code == 0, result.output
        assert captured["holding_solution"] is True
        assert promoted["called"] is False  # no --promote → stage only

    def test_promote_requires_solution_exit_2(self, monkeypatch, tmp_path):
        zip_path = tmp_path / "up.zip"
        zip_path.write_bytes(b"PK\x03\x04stub")
        monkeypatch.setattr("crm.cli.CLIContext.backend", lambda self: object())
        result = CliRunner().invoke(cli, [
            "--json", "solution", "stage-and-upgrade", str(zip_path),
            "--promote", "--yes",
        ])
        assert result.exit_code == 2, result.output

    def test_promote_orchestrates_delete_and_promote(self, monkeypatch, tmp_path):
        zip_path = tmp_path / "up.zip"
        zip_path.write_bytes(b"PK\x03\x04stub")
        monkeypatch.setattr("crm.core.solution.import_solution",
                            lambda backend, path, **kw: {"status": "succeeded"})
        monkeypatch.setattr("crm.core.solution.delete_and_promote",
                            lambda backend, name: {"promoted": True, "solution": name})
        monkeypatch.setattr("crm.cli.CLIContext.backend", lambda self: object())
        result = CliRunner().invoke(cli, [
            "--json", "solution", "stage-and-upgrade", str(zip_path),
            "--promote", "--solution", "ManagedApp", "--yes",
        ])
        assert result.exit_code == 0, result.output
        env = json.loads(result.output)
        assert env["data"]["promote"]["promoted"] is True
