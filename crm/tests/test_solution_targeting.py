"""Issue #636: explicit --solution is mandatory for customization writes.

The shared `--solution` option's callback (`_require_solution`, #998) raises a
UsageError (exit 2) at parse time when no --solution is given — there is no profile
`default_solution` fallback and no strictness knob any more (supersedes #22 and
the `default_solution` half of ADR 0002). Covers:
  - an explicit --solution (including Default) satisfies the requirement
  - no --solution -> UsageError (exit 2), before any prompt or backend call
  - publisher prefix still supplies the schema-name default for create commands
  - hard metadata delete verbs are EXEMPT (a global delete can't orphan, so the
    MSCRM.SolutionUniqueName header is inert and --solution stays optional)

All HTTP is dry-run or mocked; no live D365 server needed.
"""

# pyright: basic
from __future__ import annotations

import json

import pytest
from click.testing import CliRunner

from crm.cli import cli
from crm.utils.d365_backend import ConnectionProfile

# ── ConnectionProfile round-trip ─────────────────────────────────────────


def test_profile_roundtrips_publisher_prefix():
    p = ConnectionProfile(
        name="p",
        url="https://crm.contoso.local/contoso",
        domain="CONTOSO",
        username="alice",
        publisher_prefix="new",
    )
    d = p.to_dict()
    assert d["publisher_prefix"] == "new"
    assert "default_solution" not in d
    p2 = ConnectionProfile.from_dict(d)
    assert p2.publisher_prefix == "new"
    assert not hasattr(p2, "default_solution")


def test_profile_from_dict_drops_legacy_default_solution():
    # Back-compat: a legacy profile JSON carrying default_solution loads fine and
    # the key is silently dropped (no migration) — it never reaches the model.
    d = {
        "name": "p",
        "url": "https://crm.contoso.local/contoso",
        "domain": "CONTOSO",
        "username": "alice",
        "default_solution": "LegacySol",
        "publisher_prefix": "new",
    }
    p = ConnectionProfile.from_dict(d)
    assert p.publisher_prefix == "new"
    assert not hasattr(p, "default_solution")
    assert "default_solution" not in p.to_dict()


# ── CLI integration ──────────────────────────────────────────────────────


def _save_profile(monkeypatch, tmp_path, **kwargs):
    monkeypatch.setenv("CRM_HOME", str(tmp_path))
    monkeypatch.setenv("CRM_DOTENV", str(tmp_path / "noop.env"))
    from crm.core import session as session_mod

    profile = ConnectionProfile(
        name="p",
        url="https://crm.contoso.local/contoso",
        domain="",
        username="alice",
        **kwargs,
    )
    session_mod.save_profile(profile)
    session_mod.save_profile_secret_plaintext("p", "pw")
    state = session_mod.load_session("default")
    state["active_profile"] = "p"
    session_mod.save_session(state, "default")


def test_create_entity_uses_publisher_prefix(monkeypatch, tmp_path):
    """No --schema-name: schema name is built from the profile publisher prefix."""
    _save_profile(monkeypatch, tmp_path, publisher_prefix="new")
    captured = {}

    from crm.core import metadata as meta_mod

    def fake_create_entity(_backend, **kwargs):
        captured.update(kwargs)
        return {"schema_name": kwargs["schema_name"], "_dry_run": True}

    monkeypatch.setattr(meta_mod, "create_entity", fake_create_entity)
    result = CliRunner().invoke(
        cli,
        [
            "--json",
            "--profile",
            "p",
            "metadata",
            "create-entity",
            "--display",
            "Project",
            "--solution",
            "MySol",
            "--no-publish",
        ],
    )
    assert result.exit_code == 0, result.output
    assert captured["schema_name"] == "new_Project"


def test_create_entity_multiword_display_pascalcases(monkeypatch, tmp_path):
    """No --schema-name + multi-word --display: PascalCased across word boundaries."""
    _save_profile(monkeypatch, tmp_path, publisher_prefix="new")
    captured = {}

    from crm.core import metadata as meta_mod

    def fake_create_entity(_backend, **kwargs):
        captured.update(kwargs)
        return {"schema_name": kwargs["schema_name"], "_dry_run": True}

    monkeypatch.setattr(meta_mod, "create_entity", fake_create_entity)
    result = CliRunner().invoke(
        cli,
        [
            "--json",
            "--profile",
            "p",
            "metadata",
            "create-entity",
            "--display",
            "Project Task",
            "--solution",
            "MySol",
            "--no-publish",
        ],
    )
    assert result.exit_code == 0, result.output
    assert captured["schema_name"] == "new_ProjectTask"


def test_create_optionset_multiword_display_pascalcases(monkeypatch, tmp_path):
    """No --name + multi-word --display: PascalCased across word boundaries."""
    _save_profile(monkeypatch, tmp_path, publisher_prefix="new")
    captured = {}

    from crm.core import optionsets as os_mod

    def fake_create_optionset(_backend, **kwargs):
        captured.update(kwargs)
        return {"name": kwargs["name"], "_dry_run": True}

    monkeypatch.setattr(os_mod, "create_optionset", fake_create_optionset)
    result = CliRunner().invoke(
        cli,
        [
            "--json",
            "--profile",
            "p",
            "metadata",
            "create-optionset",
            "--display",
            "Task Priority",
            "--solution",
            "MySol",
            "--no-publish",
        ],
    )
    assert result.exit_code == 0, result.output
    assert captured["name"] == "new_TaskPriority"


def test_create_entity_no_solution_errors(monkeypatch, tmp_path):
    """No --solution -> exit 2 (UsageError), ok=false, before any backend call."""
    _save_profile(monkeypatch, tmp_path, publisher_prefix="new")

    from crm.core import metadata as meta_mod

    monkeypatch.setattr(
        meta_mod,
        "create_entity",
        lambda _b, **kw: {"_dry_run": True},
    )
    result = CliRunner().invoke(
        cli,
        [
            "--json",
            "--profile",
            "p",
            "metadata",
            "create-entity",
            "--schema-name",
            "new_Project",
            "--display",
            "Project",
            "--no-publish",
        ],
    )
    assert result.exit_code == 2, result.output
    assert "solution" in result.output.lower()


def test_create_entity_no_solution_errors_under_dry_run(monkeypatch, tmp_path):
    """--solution requirement fires even under --dry-run (before any backend call)."""
    _save_profile(monkeypatch, tmp_path, publisher_prefix="new")
    result = CliRunner().invoke(
        cli,
        [
            "--json",
            "--dry-run",
            "--profile",
            "p",
            "metadata",
            "create-entity",
            "--schema-name",
            "new_Project",
            "--display",
            "Project",
            "--no-publish",
        ],
    )
    assert result.exit_code == 2, result.output
    assert "solution" in result.output.lower()


def test_add_attribute_no_solution_errors(monkeypatch, tmp_path):
    """No --solution on add-attribute -> exit 2 (was a warning before #636)."""
    _save_profile(monkeypatch, tmp_path, publisher_prefix="new")

    from crm.core import metadata_attrs as ma_mod

    monkeypatch.setattr(
        ma_mod,
        "add_attribute",
        lambda _b, **kw: {"_dry_run": True},
    )
    result = CliRunner().invoke(
        cli,
        [
            "--json",
            "--profile",
            "p",
            "metadata",
            "add-attribute",
            "new_project",
            "--kind",
            "string",
            "--schema-name",
            "new_Note",
            "--display",
            "Note",
            "--no-publish",
        ],
    )
    assert result.exit_code == 2, result.output
    assert "solution" in result.output.lower()


def test_delete_entity_exempt_no_solution_ok(monkeypatch, tmp_path):
    """Hard metadata delete is EXEMPT (#636): no --solution -> proceeds (exit 0),
    not exit-2. The header is inert on a global delete, so it stays optional.
    """
    _save_profile(monkeypatch, tmp_path, publisher_prefix="new")
    captured = {}

    from crm.core import metadata as meta_mod

    def fake_delete_entity(_backend, logical_name, **kwargs):
        captured["logical_name"] = logical_name
        captured.update(kwargs)
        return {"deleted": logical_name, "_dry_run": True}

    monkeypatch.setattr(meta_mod, "delete_entity", fake_delete_entity)
    result = CliRunner().invoke(
        cli,
        ["--json", "--profile", "p", "metadata", "delete-entity", "new_project", "--yes"],
    )
    assert result.exit_code == 0, result.output
    assert captured["logical_name"] == "new_project"
    assert captured["solution"] is None


def test_delete_entity_solution_forwarded_when_given(monkeypatch, tmp_path):
    """An explicit --solution on a delete is still forwarded (back-compat)."""
    _save_profile(monkeypatch, tmp_path, publisher_prefix="new")
    captured = {}

    from crm.core import metadata as meta_mod

    def fake_delete_entity(_backend, logical_name, **kwargs):
        captured.update(kwargs)
        return {"deleted": logical_name, "_dry_run": True}

    monkeypatch.setattr(meta_mod, "delete_entity", fake_delete_entity)
    result = CliRunner().invoke(
        cli,
        [
            "--json",
            "--profile",
            "p",
            "metadata",
            "delete-entity",
            "new_project",
            "--solution",
            "MySol",
            "--yes",
        ],
    )
    assert result.exit_code == 0, result.output
    assert captured["solution"] == "MySol"


def test_default_solution_satisfies_requirement(monkeypatch, tmp_path):
    """--solution Default is an explicit, deliberate Default-Solution-only write."""
    _save_profile(monkeypatch, tmp_path, publisher_prefix="new")
    captured = {}

    from crm.core import metadata as meta_mod

    def fake_create_entity(_backend, **kwargs):
        captured.update(kwargs)
        return {"schema_name": kwargs["schema_name"], "_dry_run": True}

    monkeypatch.setattr(meta_mod, "create_entity", fake_create_entity)
    result = CliRunner().invoke(
        cli,
        [
            "--json",
            "--profile",
            "p",
            "metadata",
            "create-entity",
            "--schema-name",
            "new_Project",
            "--display",
            "Project",
            "--solution",
            "Default",
            "--no-publish",
        ],
    )
    assert result.exit_code == 0, result.output
    assert captured["solution"] == "Default"


def test_connection_status_has_no_default_solution(monkeypatch, tmp_path):
    _save_profile(monkeypatch, tmp_path, publisher_prefix="new")
    result = CliRunner().invoke(
        cli,
        ["--json", "--profile", "p", "connection", "status"],
    )
    assert result.exit_code == 0, result.output
    env = json.loads(result.output)
    prof = env["data"]["profile"]
    assert prof["publisher_prefix"] == "new"
    assert "default_solution" not in prof


_SOLUTION_REQUIRED = (
    "--solution is required for customization writes — components must "
    "target an explicit unmanaged solution. Pass --solution <unique_name>."
)


@pytest.mark.parametrize(
    "args",
    [
        ["ribbon", "remove", "account", "--button-id", "new.btn"],
        ["security", "create-role", "Agent Read-Only"],
        ["ribbon", "list", "account"],
    ],
    ids=["ribbon-remove", "security-create-role", "ribbon-list"],
)
def test_missing_solution_fails_before_confirmation(monkeypatch, tmp_path, args):
    """A confirm-gated write without --solution fails exit 2, never prompting first."""
    _save_profile(monkeypatch, tmp_path)
    result = CliRunner().invoke(cli, ["--profile", "p", *args])
    assert result.exit_code == 2, result.output
    assert _SOLUTION_REQUIRED in result.output
    assert "Pass --yes" not in result.output


def test_missing_solution_message_is_exact_under_json_dry_run(monkeypatch, tmp_path):
    _save_profile(monkeypatch, tmp_path, publisher_prefix="new")
    args = ["--json", "--dry-run", "--profile", "p", "metadata", "create-entity"]
    result = CliRunner().invoke(cli, [*args, "--display", "Project", "--no-publish"])
    assert result.exit_code == 2, result.output
    assert json.loads(result.output)["error"] == _SOLUTION_REQUIRED
