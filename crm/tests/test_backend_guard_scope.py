"""#698: backend construction lives INSIDE the guarded error scope.

Several commands built the D365 backend *outside* the `d365_errors` guard, so a
bad profile / rejected credential — which surfaces as a `D365Error` at
construction — escaped as a raw Python traceback instead of the house one-line
error / JSON envelope. These tests pin that every swept command now translates a
construction-time failure into a clean envelope with a non-zero exit, and that
`solution validate` stays fully offline (no backend constructed) without
`--against-org`.
"""
# pyright: basic
from __future__ import annotations

import json
import zipfile

import pytest
from click.testing import CliRunner

from crm.cli import CLIContext, cli
from crm.commands import scaffold as scaffold_cmd
from crm.utils.d365_backend import ConnectionProfile, D365Error

pytestmark = pytest.mark.usefixtures("isolated_home")


def _good_zip(path):
    """A minimal, structurally valid solution zip (mirrors test_solution_validate)."""
    sol = (
        '<?xml version="1.0"?>\n'
        "<ImportExportXml><SolutionManifest><UniqueName>cwx_test</UniqueName>"
        "<Managed>0</Managed><RootComponents></RootComponents>"
        "</SolutionManifest></ImportExportXml>"
    )
    cust = (
        '<?xml version="1.0"?>\n'
        "<ImportExportXml><Entities></Entities><optionsets></optionsets>"
        "<InteractionCentricDashboards></InteractionCentricDashboards>"
        "<WebResources></WebResources></ImportExportXml>"
    )
    with zipfile.ZipFile(path, "w") as zf:
        zf.writestr("solution.xml", sol)
        zf.writestr("customizations.xml", cust)
        zf.writestr("[Content_Types].xml", "<Types/>")
    return path


@pytest.fixture
def broken_backend(monkeypatch):
    """Make `ctx.backend()` fail exactly as a bad profile / rejected secret does:
    raise a `D365Error` at construction. A correctly-scoped command translates it
    into the failure envelope; an unguarded one lets it escape as a traceback."""

    def _raise(_self):
        raise D365Error("bad profile: rejected secret", status=401, code="0x0")

    monkeypatch.setattr(CLIContext, "backend", _raise)
    # `scaffold table` gates on an active profile carrying a publisher_prefix
    # *before* it touches the backend; give it one so control still reaches the
    # (now guarded) construction rather than short-circuiting on the precondition.
    monkeypatch.setattr(
        scaffold_cmd, "_active_profile",
        lambda _ctx: ConnectionProfile(
            name="testp", url="https://crm.contoso.local/contoso",
            domain="CONTOSO", username="alice", api_version="v9.2",
            verify_ssl=False, publisher_prefix="new",
        ),
    )


def _solution_validate_argv(tmp_path):
    return ["--json", "solution", "validate",
            str(_good_zip(tmp_path / "sol.zip")), "--against-org"]


def _ribbon_export_argv(_tmp_path):
    # dry-run path: the previously unguarded `ctx.backend().get(...)` construction.
    return ["--json", "--dry-run", "ribbon", "export", "account"]


def _action_function_argv(_tmp_path):
    return ["--json", "action", "function", "WhoAmI"]


def _scaffold_table_argv(_tmp_path):
    return ["--json", "scaffold", "table", "Widget",
            "--column", "Code:string", "--solution", "Dev"]


@pytest.mark.parametrize(
    "argv_factory",
    [
        pytest.param(_solution_validate_argv, id="solution-validate-against-org"),
        pytest.param(_ribbon_export_argv, id="ribbon-export-dry-run"),
        pytest.param(_action_function_argv, id="action-function"),
        pytest.param(_scaffold_table_argv, id="scaffold-table"),
    ],
)
def test_broken_profile_renders_clean_envelope(broken_backend, tmp_path, argv_factory):
    """A construction-time D365Error → clean JSON envelope + exit 1, never a traceback."""
    result = CliRunner().invoke(cli, argv_factory(tmp_path))
    assert result.exit_code == 1, result.output
    assert result.exception is None or isinstance(
        result.exception, SystemExit
    ), f"backend error escaped the guard: {result.exception!r}"
    env = json.loads(result.output)  # a real envelope, not a raw traceback
    assert env["ok"] is False
    assert env["meta"]["status"] == 401
    assert "Traceback" not in result.output


def test_solution_validate_offline_never_constructs_backend(tmp_path, monkeypatch):
    """Without --against-org, `solution validate` stays fully offline: the backend
    is never constructed (the sentinel raise never fires), so a valid zip validates
    to a clean success envelope with no profile/connection required."""

    def _boom(_self):
        raise D365Error("backend must not be constructed offline", status=401)

    monkeypatch.setattr(CLIContext, "backend", _boom)
    zip_path = _good_zip(tmp_path / "good.zip")
    result = CliRunner().invoke(cli, ["--json", "solution", "validate", str(zip_path)])
    assert result.exit_code == 0, result.output
    env = json.loads(result.output)
    assert env["ok"] is True
