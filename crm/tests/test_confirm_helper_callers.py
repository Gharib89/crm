# pyright: basic
"""Regression sweep for commands that route through `_confirm_destructive`."""

from __future__ import annotations

import ast
import json
from pathlib import Path

from click.testing import CliRunner

from crm.core import session as session_mod
from crm.utils.d365_backend import ConnectionProfile

_GUID = "11111111-1111-1111-1111-111111111111"
_GUID2 = "22222222-2222-2222-2222-222222222222"
_FETCH = "<fetch><entity name='contact'><attribute name='contactid'/></entity></fetch>"
_ROOT = Path(__file__).resolve().parents[1]
_COMMANDS = _ROOT / "commands"
_PROFILE_URL = "https://crm.contoso.local/contoso"


def _seed_profile(tmp_path, monkeypatch, name="t") -> None:
    monkeypatch.setenv("CRM_HOME", str(tmp_path / ".crm"))
    monkeypatch.setenv("CRM_DOTENV", str(tmp_path / "noop.env"))
    session_mod.save_profile(
        ConnectionProfile(name=name, url=_PROFILE_URL, domain="CONTOSO", username="alice")
    )
    session_mod.save_profile_secret_plaintext(name, "pw")


def _helper_callers() -> dict[str, bool]:
    callers: dict[str, bool] = {}
    for path in sorted(_COMMANDS.glob("*.py")):
        tree = ast.parse(path.read_text(encoding="utf-8"))

        class Visitor(ast.NodeVisitor):
            def __init__(self) -> None:
                self.stack: list[str] = []

            def visit_FunctionDef(self, node: ast.FunctionDef) -> None:
                self.stack.append(node.name)
                self.generic_visit(node)
                self.stack.pop()

            def visit_Call(self, node: ast.Call) -> None:
                if isinstance(node.func, ast.Name) and node.func.id == "_confirm_destructive":
                    skip_on_dry_run = True
                    for kw in node.keywords:
                        if kw.arg == "skip_on_dry_run":
                            skip_on_dry_run = bool(
                                isinstance(kw.value, ast.Constant) and kw.value.value
                            )
                    # B023 suppressed: Visitor().visit(tree) runs inside the same
                    # loop iteration that binds `path` — the closure never outlives it.
                    callers[f"{path.stem}:{self.stack[-1]}"] = skip_on_dry_run  # noqa: B023
                self.generic_visit(node)

        Visitor().visit(tree)
    return callers


def _record(called: dict, value, result=None):
    called["hit"] = value
    return {"_dry_run": True} if result is None else result


def _spec_file(tmp_path: Path) -> str:
    path = tmp_path / "spec.yaml"
    path.write_text("solution:\n  unique_name: ContosoCore\n", encoding="utf-8")
    return str(path)


def _zip_file(tmp_path: Path, name: str) -> str:
    path = tmp_path / name
    path.write_bytes(b"zip")
    return str(path)


def _jsonl_file(tmp_path: Path, name: str) -> str:
    path = tmp_path / name
    path.write_text('{"contactid":"11111111-1111-1111-1111-111111111111"}\n', encoding="utf-8")
    return str(path)


def _plan_file(tmp_path: Path) -> str:
    """A prune-intent plan whose pre-flight passes without touching the backend.

    Empty header ``url`` skips the (backend-reading) URL-mismatch check; the org
    matches the stubbed WhoAmI in `_setup_case`; verdicts are clean and payloads
    empty — so `_apply_from_plan` reaches its destructive-confirm gate, which is
    what this sweep exercises.
    """
    path = tmp_path / "sweep.plan.json"
    path.write_text(
        json.dumps(
            {
                "plan_format": 1,
                "header": {
                    "url": "",
                    "organization_id": "sweep-org",
                    "solution": "ContosoCore",
                    "cli_version": "x",
                    "created_at": "2026-01-01T00:00:00+00:00",
                    "intent": {"prune": True, "allow_data_loss": False, "stage_only": False},
                },
                "spec": {"solution": {"unique_name": "ContosoCore"}},
                "payloads": {},
                "verdicts": [{"kind": "entity", "name": "contoso_x", "verdict": "skipped"}],
            }
        ),
        encoding="utf-8",
    )
    return str(path)


def _argv_map(tmp_path: Path, prefix: list[str]) -> dict[str, list[str]]:
    return {
        "app:app_delete": [*prefix, "app", "delete", "cwx_crmworx"],
        "apply:apply_cmd": [*prefix, "apply", "-f", _spec_file(tmp_path), "--prune"],
        "apply:_apply_from_plan": [*prefix, "apply", "--from-plan", _plan_file(tmp_path)],
        "async_ops:async_cancel": [*prefix, "async", "cancel", _GUID],
        "chart:chart_delete": [*prefix, "chart", "delete", _GUID],
        "data:data_delete": [*prefix, "data", "delete", "contacts", "--fetchxml", _FETCH],
        "data:data_import": [
            *prefix,
            "data",
            "import",
            "contacts",
            _jsonl_file(tmp_path, "delete.jsonl"),
            "--mode",
            "delete",
            "--id-column",
            "contactid",
        ],
        "dashboard:dashboard_delete": [*prefix, "dashboard", "delete", _GUID],
        "entity:entity_delete": [*prefix, "--profile", "t", "entity", "delete", "contacts", _GUID],
        "entity:entity_disassociate": [
            *prefix,
            "--profile",
            "t",
            "entity",
            "disassociate",
            "accounts",
            _GUID,
            "primarycontactid",
        ],
        "entity:entity_clear_lookup": [
            *prefix,
            "--profile",
            "t",
            "entity",
            "clear-lookup",
            "accounts",
            _GUID,
            "primarycontactid",
        ],
        "metadata:metadata_delete_entity": [*prefix, "metadata", "delete-entity", "new_widget"],
        "metadata:metadata_delete_attribute": [
            *prefix,
            "metadata",
            "delete-attribute",
            "new_widget",
            "new_field",
        ],
        "metadata:metadata_delete_key": [*prefix, "metadata", "delete-key", "account", "new_Code"],
        "metadata:metadata_delete_relationship": [
            *prefix,
            "metadata",
            "delete-relationship",
            "new_rel",
        ],
        "metadata:metadata_delete_optionset": [
            *prefix,
            "metadata",
            "delete-optionset",
            "new_color",
        ],
        "plugin:unregister_image_cmd": [*prefix, "plugin", "unregister-image", _GUID],
        "plugin:unregister_assembly_cmd": [
            *prefix,
            "plugin",
            "unregister-assembly",
            "Contoso.Plugins",
        ],
        "plugin:unregister_step_cmd": [*prefix, "plugin", "unregister-step", "My Step"],
        "profile:profile_add": [
            *prefix,
            "profile",
            "add",
            "--url",
            _PROFILE_URL,
            "--domain",
            "CONTOSO",
            "--username",
            "alice",
            "--password",
            "pw",
            "--name",
            "t",
        ],
        "profile:profile_rm": [*prefix, "profile", "rm", "t"],
        "report:report_delete": [*prefix, "report", "delete", _GUID],
        "ribbon:ribbon_remove": [
            *prefix,
            "ribbon",
            "remove",
            "account",
            "--button-id",
            "some.button",
            "--solution",
            "CRMWorx",
        ],
        "ribbon:ribbon_hide_button": [
            *prefix,
            "ribbon",
            "hide-button",
            "account",
            "--target-id",
            "some.button",
            "--method",
            "hide-action",
            "--solution",
            "CRMWorx",
        ],
        "security:create_role": [*prefix, "security", "create-role", "R", "--solution", "mysol"],
        "security:set_role_privileges": [
            *prefix,
            "security",
            "set-role-privileges",
            "role-1",
            "--access",
            "read",
            "--entities",
            "account",
            "--depth",
            "global",
        ],
        "security:assign_role": [*prefix, "security", "assign-role", _GUID, "--to-user", _GUID2],
        "security:grant": [
            *prefix,
            "security",
            "grant",
            "accounts",
            _GUID,
            "--to",
            f"user:{_GUID2}",
            "--rights",
            "Read",
        ],
        "security:revoke": [
            *prefix,
            "security",
            "revoke",
            "accounts",
            _GUID,
            "--from",
            f"user:{_GUID2}",
        ],
        "solution:solution_remove_component": [
            *prefix,
            "solution",
            "remove-component",
            "--solution",
            "CRMWorx",
            "--type",
            "61",
            "--id",
            _GUID,
        ],
        "solution:solution_uninstall": [
            *prefix,
            "solution",
            "uninstall",
            "--solution",
            "ContosoCore",
        ],
        "solution:solution_stage_and_upgrade_cmd": [
            *prefix,
            "solution",
            "stage-and-upgrade",
            _zip_file(tmp_path, "upgrade.zip"),
        ],
        "solution:solution_apply_upgrade_cmd": [
            *prefix,
            "solution",
            "apply-upgrade",
            "ContosoCore",
        ],
        "solution:solution_job_cancel": [*prefix, "solution", "job-cancel", _GUID],
        "solution:solution_import_cmd": [
            *prefix,
            "solution",
            "import",
            _zip_file(tmp_path, "import.zip"),
        ],
        "translation:translation_import_cmd": [
            *prefix,
            "translation",
            "import",
            _zip_file(tmp_path, "labels.zip"),
        ],
        "webresource:webresource_delete": [*prefix, "webresource", "delete", "cwx_/foo.js"],
        "workflow:workflow_deactivate": [
            *prefix,
            "--profile",
            "t",
            "workflow",
            "deactivate",
            _GUID,
        ],
        "workflow:workflow_delete": [*prefix, "--profile", "t", "workflow", "delete", _GUID],
    }


def _argv(case_id: str, tmp_path: Path, *, dry_run: bool) -> list[str]:
    prefix = ["--json"] + (["--dry-run"] if dry_run else [])
    return _argv_map(tmp_path, prefix)[case_id]


def _setup_case(case_id: str, monkeypatch, tmp_path: Path, called: dict) -> None:
    monkeypatch.setattr("crm.cli.CLIContext.backend", lambda self: object())
    if case_id.startswith(("entity:", "workflow:")):
        _seed_profile(tmp_path, monkeypatch)
    elif case_id.startswith("profile:"):
        _seed_profile(tmp_path, monkeypatch)

    if case_id == "app:app_delete":
        monkeypatch.setattr(
            "crm.commands.app.app_mod.delete_app", lambda *a, **k: _record(called, "app")
        )
    elif case_id == "apply:apply_cmd":
        monkeypatch.setattr(
            "crm.commands.apply.apply_mod.apply_spec",
            lambda *a, **k: _record(
                called,
                "apply",
                {
                    "ok": True,
                    "applied": [],
                    "updated": [],
                    "skipped": [],
                    "replace_blocked": [],
                    "pruned": [],
                    "planned": [],
                    "failed": [],
                    "staged": False,
                },
            ),
        )
    elif case_id == "apply:_apply_from_plan":
        # Pre-flight reads WhoAmI (stub a matching org) and `run_plan` is the
        # single core call the confirm gate guards (stub it to record the hit).
        monkeypatch.setattr(
            "crm.core.connection.whoami", lambda *a, **k: {"OrganizationId": "sweep-org"}
        )
        monkeypatch.setattr(
            "crm.core.plan.run_plan",
            lambda *a, **k: _record(called, "from-plan", {"status": "valid", "ok": True}),
        )
    elif case_id in {"async_ops:async_cancel", "solution:solution_job_cancel"}:
        monkeypatch.setattr(
            "crm.commands.async_ops.async_ops_mod.cancel_async_operation",
            lambda *a, **k: _record(called, "async"),
        )
        monkeypatch.setattr(
            "crm.commands.solution.async_ops_mod.cancel_async_operation",
            lambda *a, **k: _record(called, "solution-async"),
        )
    elif case_id == "data:data_delete":
        monkeypatch.setattr(
            "crm.commands.data.bulk_delete_mod.bulk_delete", lambda *a, **k: _record(called, "bulk")
        )
    elif case_id == "data:data_import":
        monkeypatch.setattr(
            "crm.commands.data.import_mod.import_records",
            lambda *a, **k: _record(
                called,
                "import",
                {
                    "imported": 1,
                    "failed": 0,
                    "chunks": 1,
                    "entity_set": "contacts",
                    "mode": "delete",
                    "dry_run": False,
                    "format": "jsonl",
                    "failures": [],
                },
            ),
        )
    elif case_id == "dashboard:dashboard_delete":
        monkeypatch.setattr(
            "crm.commands.dashboard.dashboard_mod.delete_dashboard",
            lambda *a, **k: _record(called, "dashboard"),
        )
    elif case_id == "chart:chart_delete":
        monkeypatch.setattr(
            "crm.commands.chart.charts_mod.delete_chart", lambda *a, **k: _record(called, "chart")
        )
    elif case_id == "entity:entity_delete":
        monkeypatch.setattr(
            "crm.commands.entity.entity_mod.delete", lambda *a, **k: _record(called, "delete")
        )
    elif case_id == "entity:entity_disassociate":
        monkeypatch.setattr(
            "crm.commands.entity.entity_mod.disassociate",
            lambda *a, **k: _record(called, "disassociate"),
        )
    elif case_id == "entity:entity_clear_lookup":
        monkeypatch.setattr(
            "crm.commands.entity.entity_mod.clear_lookup", lambda *a, **k: _record(called, "clear")
        )
    elif case_id == "metadata:metadata_delete_entity":
        monkeypatch.setattr(
            "crm.commands.metadata.meta_mod.delete_entity",
            lambda *a, **k: _record(called, "meta-entity"),
        )
    elif case_id == "metadata:metadata_delete_attribute":
        monkeypatch.setattr(
            "crm.commands.metadata.ma_mod.delete_attribute",
            lambda *a, **k: _record(called, "meta-attr"),
        )
    elif case_id == "metadata:metadata_delete_key":
        monkeypatch.setattr(
            "crm.commands.metadata.meta_mod.delete_entity_key",
            lambda *a, **k: _record(called, "meta-key"),
        )
    elif case_id == "metadata:metadata_delete_relationship":
        monkeypatch.setattr(
            "crm.commands.metadata.rel_mod.delete_relationship",
            lambda *a, **k: _record(called, "meta-rel"),
        )
    elif case_id == "metadata:metadata_delete_optionset":
        monkeypatch.setattr(
            "crm.commands.metadata.os_mod.delete_optionset",
            lambda *a, **k: _record(called, "meta-os"),
        )
    elif case_id == "plugin:unregister_image_cmd":
        monkeypatch.setattr(
            "crm.commands.plugin.plugin_mod.unregister_image",
            lambda *a, **k: _record(called, "plugin-image"),
        )
    elif case_id == "plugin:unregister_assembly_cmd":
        monkeypatch.setattr(
            "crm.commands.plugin.plugin_mod.unregister_assembly",
            lambda *a, **k: _record(called, "plugin-asm"),
        )
    elif case_id == "plugin:unregister_step_cmd":
        monkeypatch.setattr(
            "crm.commands.plugin.plugin_mod.unregister_step",
            lambda *a, **k: _record(called, "plugin-step"),
        )
    elif case_id == "report:report_delete":
        monkeypatch.setattr(
            "crm.commands.report.report_mod.delete_report",
            lambda *a, **k: _record(called, "report"),
        )
    elif case_id == "ribbon:ribbon_remove":
        monkeypatch.setattr(
            "crm.commands.ribbon.ribbon_mod.apply_ribbon_change",
            lambda *a, **k: _record(called, "ribbon-remove"),
        )
    elif case_id == "ribbon:ribbon_hide_button":
        monkeypatch.setattr(
            "crm.commands.ribbon.ribbon_mod.retrieve_entity_ribbon",
            lambda *a, **k: {"RibbonDiffXml": "<RibbonDiffXml />"},
        )
        monkeypatch.setattr(
            "crm.commands.ribbon.ribbon_mod.find_composed_element",
            lambda *a, **k: {"Command": "Mscrm.Command"},
        )
        monkeypatch.setattr(
            "crm.commands.ribbon.ribbon_mod.apply_ribbon_change",
            lambda *a, **k: _record(called, "ribbon-hide"),
        )
    elif case_id == "security:create_role":
        monkeypatch.setattr(
            "crm.commands.security.security_mod.create_role",
            lambda *a, **k: _record(called, "create-role"),
        )
    elif case_id == "security:set_role_privileges":
        monkeypatch.setattr(
            "crm.commands.security.security_mod.set_role_privileges",
            lambda *a, **k: _record(called, "set-role"),
        )
    elif case_id == "security:assign_role":
        monkeypatch.setattr(
            "crm.commands.security.security_mod.assign_role_to_user",
            lambda *a, **k: _record(called, "assign"),
        )
    elif case_id == "security:grant":
        monkeypatch.setattr(
            "crm.commands.security.security_mod.grant_access",
            lambda *a, **k: _record(called, "grant"),
        )
    elif case_id == "security:revoke":
        monkeypatch.setattr(
            "crm.commands.security.security_mod.revoke_access",
            lambda *a, **k: _record(called, "revoke"),
        )
    elif case_id == "solution:solution_remove_component":
        monkeypatch.setattr(
            "crm.commands.solution.sol_mod.remove_solution_component",
            lambda *a, **k: _record(called, "remove-component"),
        )
    elif case_id == "solution:solution_uninstall":
        monkeypatch.setattr(
            "crm.commands.solution.sol_mod.uninstall_solution",
            lambda *a, **k: _record(called, "uninstall"),
        )
    elif case_id == "solution:solution_stage_and_upgrade_cmd":
        monkeypatch.setattr(
            "crm.commands.solution.sol_mod.import_solution",
            lambda *a, **k: _record(called, "stage-upgrade"),
        )
    elif case_id == "solution:solution_apply_upgrade_cmd":
        monkeypatch.setattr(
            "crm.commands.solution.sol_mod.delete_and_promote",
            lambda *a, **k: _record(called, "apply-upgrade"),
        )
    elif case_id == "solution:solution_import_cmd":
        monkeypatch.setattr(
            "crm.commands.solution.sol_mod.import_solution",
            lambda *a, **k: _record(called, "import"),
        )
    elif case_id == "translation:translation_import_cmd":
        monkeypatch.setattr(
            "crm.commands.translation.translation_mod.import_translation",
            lambda *a, **k: _record(called, "translation"),
        )
    elif case_id == "webresource:webresource_delete":
        monkeypatch.setattr(
            "crm.commands.webresource.wr_mod.delete_webresource",
            lambda *a, **k: _record(called, "webresource"),
        )
    elif case_id == "workflow:workflow_deactivate":
        monkeypatch.setattr(
            "crm.commands.workflow.workflow_mod.set_workflow_state",
            lambda *a, **k: _record(called, "wf-deactivate"),
        )
    elif case_id == "workflow:workflow_delete":
        monkeypatch.setattr(
            "crm.commands.workflow.workflow_mod.resolve_delete_target",
            lambda *a, **k: {
                "name": "Workflow",
                "workflow_id": _GUID,
                "resolved_from_activation_id": None,
            },
        )
        monkeypatch.setattr(
            "crm.commands.workflow.workflow_mod.delete_workflow",
            lambda *a, **k: _record(called, "wf-delete"),
        )


CALLERS = _helper_callers()


def test_every_discovered_caller_has_sweep_coverage(tmp_path):
    """The argv map (`_argv_map`) is the executable coverage the sweeps below
    run; assert it lines up exactly with the AST-discovered callers so a new
    `_confirm_destructive` call site is caught. A new caller then needs only an
    argv entry here (+ a stub in `_setup_case`) to be covered — there is no
    separate hard-coded caller list to keep in sync.
    """
    covered = set(_argv_map(tmp_path, ["--json"]))
    discovered = set(CALLERS)
    assert discovered - covered == set(), (
        f"new destructive caller(s) missing argv coverage: {discovered - covered}"
    )
    assert covered - discovered == set(), (
        f"stale argv entries with no matching caller: {covered - discovered}"
    )


def test_profile_callers_are_the_only_dry_run_opt_outs():
    assert {k for k, v in CALLERS.items() if not v} == {
        "profile:profile_add",
        "profile:profile_rm",
    }


def _invoke(argv: list[str]):
    from crm.cli import cli

    return CliRunner().invoke(cli, argv, input="")


def test_non_interactive_without_yes_fails_fast_for_all_helper_callers(monkeypatch, tmp_path):
    for case_id in sorted(CALLERS):
        called: dict = {}
        _setup_case(case_id, monkeypatch, tmp_path, called)
        result = _invoke(_argv(case_id, tmp_path, dry_run=False))
        assert result.exit_code == 1, f"{case_id}: {result.output}"
        env = json.loads(result.output)
        assert env["ok"] is False, case_id
        assert "--yes" in env["error"], (case_id, env)
        assert result.output.lstrip().startswith("{"), case_id
        assert "hit" not in called, case_id
        monkeypatch.undo()


def test_dry_run_skips_confirmation_for_dry_run_capable_helper_callers(monkeypatch, tmp_path):
    for case_id, skip_on_dry_run in sorted(CALLERS.items()):
        called: dict = {}
        _setup_case(case_id, monkeypatch, tmp_path, called)
        result = _invoke(_argv(case_id, tmp_path, dry_run=True))
        if not skip_on_dry_run:
            assert result.exit_code == 1, f"{case_id}: {result.output}"
            env = json.loads(result.output)
            assert "--yes" in env["error"], (case_id, env)
            assert "hit" not in called, case_id
        else:
            assert result.exit_code == 0, f"{case_id}: {result.output}"
            env = json.loads(result.output)
            assert env["ok"] is True, (case_id, env)
            assert env["meta"]["dry_run"] is True, (case_id, env)
            assert "hit" in called, case_id
        monkeypatch.undo()
