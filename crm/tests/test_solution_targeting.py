"""Issue #22: profile-level default solution + publisher prefix targeting.

Covers four acceptance criteria:
  - explicit --solution wins
  - profile default applied when no --solution
  - no resolvable solution -> warning (non-strict) / hard error (strict)
  - publisher prefix supplies schema-name default for create commands

All HTTP is dry-run or mocked; no live D365 server needed.
"""
# pyright: basic
from __future__ import annotations

import json

import pytest
from click.testing import CliRunner

from crm.cli import CLIContext, cli
from crm.commands._helpers import _resolve_solution
from crm.utils.d365_backend import ConnectionProfile


# ── ConnectionProfile round-trip ─────────────────────────────────────────


def test_profile_roundtrips_new_fields():
    p = ConnectionProfile(
        name="p",
        url="https://crm.contoso.local/contoso",
        domain="CONTOSO",
        username="alice",
        default_solution="MySolution",
        publisher_prefix="new",
    )
    d = p.to_dict()
    assert d["default_solution"] == "MySolution"
    assert d["publisher_prefix"] == "new"
    p2 = ConnectionProfile.from_dict(d)
    assert p2.default_solution == "MySolution"
    assert p2.publisher_prefix == "new"


def test_profile_from_dict_defaults_new_fields_when_absent():
    # Back-compat: existing profile JSON without the new keys.
    d = {
        "name": "p",
        "url": "https://crm.contoso.local/contoso",
        "domain": "CONTOSO",
        "username": "alice",
    }
    p = ConnectionProfile.from_dict(d)
    assert p.default_solution is None
    assert p.publisher_prefix is None


# ── _resolve_solution helper ─────────────────────────────────────────────


def _ctx_with_profile(monkeypatch, *, default_solution=None, publisher_prefix=None):
    """Build a CLIContext whose active profile carries the given defaults."""
    profile = ConnectionProfile(
        name="p",
        url="https://crm.contoso.local/contoso",
        domain="",
        username="alice",
        default_solution=default_solution,
        publisher_prefix=publisher_prefix,
    )
    ctx = CLIContext()
    ctx.profile_name = "p"
    from crm.commands import _helpers
    monkeypatch.setattr(_helpers.session_mod, "load_profile", lambda _n: profile)
    return ctx


def test_resolve_explicit_solution_wins(monkeypatch):
    ctx = _ctx_with_profile(monkeypatch, default_solution="ProfileSol")
    solution, warning = _resolve_solution(ctx, "ExplicitSol", require=False)
    assert solution == "ExplicitSol"
    assert warning is None


def test_resolve_profile_default_applied(monkeypatch):
    ctx = _ctx_with_profile(monkeypatch, default_solution="ProfileSol")
    solution, warning = _resolve_solution(ctx, None, require=False)
    assert solution == "ProfileSol"
    assert warning is None


def test_resolve_none_warns(monkeypatch):
    ctx = _ctx_with_profile(monkeypatch, default_solution=None)
    solution, warning = _resolve_solution(ctx, None, require=False)
    assert solution is None
    assert warning is not None
    assert "solution" in warning.lower()


def test_resolve_none_strict_raises(monkeypatch):
    import click

    ctx = _ctx_with_profile(monkeypatch, default_solution=None)
    ctx.json_mode = True
    with pytest.raises(click.exceptions.Exit):
        _resolve_solution(ctx, None, require=True)


# ── CLI integration ──────────────────────────────────────────────────────


def _save_profile(monkeypatch, tmp_path, **kwargs):
    monkeypatch.setenv("CRM_HOME", str(tmp_path))
    monkeypatch.setenv("D365_PASSWORD", "pw")
    from crm.core import session as session_mod

    profile = ConnectionProfile(
        name="p",
        url="https://crm.contoso.local/contoso",
        domain="",
        username="alice",
        **kwargs,
    )
    session_mod.save_profile(profile)
    state = session_mod.load_session("default")
    state["active_profile"] = "p"
    session_mod.save_session(state, "default")


def test_create_entity_uses_publisher_prefix(monkeypatch, tmp_path):
    """No --schema-name: schema name is built from the profile publisher prefix."""
    _save_profile(monkeypatch, tmp_path, publisher_prefix="new", default_solution="MySol")
    captured = {}

    from crm.core import metadata as meta_mod

    def fake_create_entity(_backend, **kwargs):
        captured.update(kwargs)
        return {"schema_name": kwargs["schema_name"], "_dry_run": True}

    monkeypatch.setattr(meta_mod, "create_entity", fake_create_entity)
    result = CliRunner().invoke(
        cli,
        ["--json", "--profile", "p", "metadata", "create-entity",
         "--display", "Project", "--no-publish"],
    )
    assert result.exit_code == 0, result.output
    assert captured["schema_name"] == "new_Project"


def test_create_entity_multiword_display_pascalcases(monkeypatch, tmp_path):
    """No --schema-name + multi-word --display: PascalCased across word boundaries."""
    _save_profile(monkeypatch, tmp_path, publisher_prefix="new", default_solution="MySol")
    captured = {}

    from crm.core import metadata as meta_mod

    def fake_create_entity(_backend, **kwargs):
        captured.update(kwargs)
        return {"schema_name": kwargs["schema_name"], "_dry_run": True}

    monkeypatch.setattr(meta_mod, "create_entity", fake_create_entity)
    result = CliRunner().invoke(
        cli,
        ["--json", "--profile", "p", "metadata", "create-entity",
         "--display", "Project Task", "--no-publish"],
    )
    assert result.exit_code == 0, result.output
    assert captured["schema_name"] == "new_ProjectTask"


def test_create_optionset_multiword_display_pascalcases(monkeypatch, tmp_path):
    """No --name + multi-word --display: PascalCased across word boundaries."""
    _save_profile(monkeypatch, tmp_path, publisher_prefix="new", default_solution="MySol")
    captured = {}

    from crm.core import optionsets as os_mod

    def fake_create_optionset(_backend, **kwargs):
        captured.update(kwargs)
        return {"name": kwargs["name"], "_dry_run": True}

    monkeypatch.setattr(os_mod, "create_optionset", fake_create_optionset)
    result = CliRunner().invoke(
        cli,
        ["--json", "--profile", "p", "metadata", "create-optionset",
         "--display", "Task Priority", "--no-publish"],
    )
    assert result.exit_code == 0, result.output
    assert captured["name"] == "new_TaskPriority"


def test_create_entity_strict_no_solution_errors(monkeypatch, tmp_path):
    """CRM_REQUIRE_SOLUTION=1 + no resolvable solution -> non-zero, ok=false."""
    _save_profile(monkeypatch, tmp_path, publisher_prefix="new")  # no default_solution
    monkeypatch.setenv("CRM_REQUIRE_SOLUTION", "1")

    from crm.core import metadata as meta_mod
    monkeypatch.setattr(
        meta_mod, "create_entity",
        lambda _b, **kw: {"_dry_run": True},
    )
    result = CliRunner().invoke(
        cli,
        ["--json", "--profile", "p", "metadata", "create-entity",
         "--schema-name", "new_Project", "--display", "Project", "--no-publish"],
    )
    assert result.exit_code != 0, result.output
    env = json.loads(result.output)
    assert env["ok"] is False
    assert "solution" in env["error"].lower()


def test_add_attribute_no_solution_warns(monkeypatch, tmp_path):
    """Non-strict + no resolvable solution -> exit 0 but meta.warning present."""
    _save_profile(monkeypatch, tmp_path, publisher_prefix="new")  # no default_solution
    monkeypatch.delenv("CRM_REQUIRE_SOLUTION", raising=False)

    from crm.core import metadata_attrs as ma_mod
    monkeypatch.setattr(
        ma_mod, "add_attribute",
        lambda _b, **kw: {"_dry_run": True},
    )
    result = CliRunner().invoke(
        cli,
        ["--json", "--profile", "p", "metadata", "add-attribute", "new_project",
         "--kind", "string", "--schema-name", "new_Note", "--display", "Note",
         "--no-publish"],
    )
    assert result.exit_code == 0, result.output
    env = json.loads(result.output)
    assert env["ok"] is True
    assert "warning" in env.get("meta", {})
    assert "solution" in env["meta"]["warning"].lower()


def test_connection_status_surfaces_new_fields(monkeypatch, tmp_path):
    _save_profile(monkeypatch, tmp_path, publisher_prefix="new", default_solution="MySol")
    result = CliRunner().invoke(
        cli, ["--json", "--profile", "p", "connection", "status"],
    )
    assert result.exit_code == 0, result.output
    env = json.loads(result.output)
    prof = env["data"]["profile"]
    assert prof["default_solution"] == "MySol"
    assert prof["publisher_prefix"] == "new"


def test_connection_profiles_json_keeps_data_as_name_list(monkeypatch, tmp_path):
    # Back-compat: `--json` consumers iterate `data` as the list of profile
    # names. The richer per-profile detail is surfaced under `meta`, not by
    # changing the shape of `data`.
    _save_profile(monkeypatch, tmp_path, publisher_prefix="new", default_solution="MySol")
    result = CliRunner().invoke(
        cli, ["--json", "--profile", "p", "connection", "profiles"],
    )
    assert result.exit_code == 0, result.output
    env = json.loads(result.output)
    assert env["data"] == ["p"]
    detail = {d["name"]: d for d in env["meta"]["profiles"]}
    assert detail["p"]["default_solution"] == "MySol"
    assert detail["p"]["publisher_prefix"] == "new"
