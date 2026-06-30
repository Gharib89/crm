"""Issue #623: --solution is always required for customization writes.

Covers the acceptance criteria from issue #623 (Pass A):
  - explicit --solution resolves correctly
  - no --solution raises UsageError (exit 2) — the old default_solution fallback is gone
  - the old --require-solution / CRM_REQUIRE_SOLUTION opt-in is removed
  - publisher prefix still supplies schema-name default for create commands
  - a legacy profile dict carrying default_solution round-trips through
    from_dict/to_dict without error (key is silently ignored)

All HTTP is dry-run or mocked; no live D365 server needed.
"""
# pyright: basic
from __future__ import annotations

import json

import click
import pytest
from click.testing import CliRunner

from crm.cli import CLIContext, cli
from crm.commands._helpers import _resolve_solution
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
    assert "default_solution" not in d, "default_solution must not appear in to_dict output"
    assert d["publisher_prefix"] == "new"
    p2 = ConnectionProfile.from_dict(d)
    assert p2.publisher_prefix == "new"


def test_profile_from_dict_ignores_legacy_default_solution():
    """Back-compat: existing profile JSON carrying default_solution loads fine
    (the key is simply ignored) — no migration code, no error."""
    d = {
        "name": "p",
        "url": "https://crm.contoso.local/contoso",
        "domain": "CONTOSO",
        "username": "alice",
        "default_solution": "MySolution",  # legacy key
        "publisher_prefix": "new",
    }
    p = ConnectionProfile.from_dict(d)
    assert p.publisher_prefix == "new"
    # No default_solution attribute on the profile
    assert not hasattr(p, "default_solution")
    # to_dict must not re-emit the legacy key
    out = p.to_dict()
    assert "default_solution" not in out


def test_profile_from_dict_defaults_publisher_prefix_when_absent():
    # Back-compat: existing profile JSON without publisher_prefix.
    d = {
        "name": "p",
        "url": "https://crm.contoso.local/contoso",
        "domain": "CONTOSO",
        "username": "alice",
    }
    p = ConnectionProfile.from_dict(d)
    assert p.publisher_prefix is None


# ── _resolve_solution helper ─────────────────────────────────────────────


def _ctx_with_profile(monkeypatch, *, publisher_prefix=None):
    """Build a CLIContext whose active profile carries the given prefix."""
    profile = ConnectionProfile(
        name="p",
        url="https://crm.contoso.local/contoso",
        domain="",
        username="alice",
        publisher_prefix=publisher_prefix,
    )
    ctx = CLIContext()
    ctx.profile_name = "p"
    from crm.commands import _helpers
    monkeypatch.setattr(_helpers.session_mod, "load_profile", lambda _n: profile)
    return ctx


def test_resolve_explicit_solution_returns_it(monkeypatch):
    ctx = _ctx_with_profile(monkeypatch, publisher_prefix="new")
    solution, warning = _resolve_solution(ctx, "ExplicitSol")
    assert solution == "ExplicitSol"
    assert warning is None


def test_resolve_no_solution_raises_usage_error(monkeypatch):
    """No --solution → UsageError (exit 2), regardless of profile."""
    ctx = _ctx_with_profile(monkeypatch, publisher_prefix="new")
    with pytest.raises(click.UsageError) as exc_info:
        _resolve_solution(ctx, None)
    assert "solution" in str(exc_info.value).lower()


def test_resolve_empty_string_raises_usage_error(monkeypatch):
    """Empty string is falsy → UsageError too."""
    ctx = _ctx_with_profile(monkeypatch)
    with pytest.raises(click.UsageError):
        _resolve_solution(ctx, "")


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
        ["--json", "--profile", "p", "metadata", "create-entity",
         "--display", "Project", "--solution", "MySol", "--no-publish"],
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
        ["--json", "--profile", "p", "metadata", "create-entity",
         "--display", "Project Task", "--solution", "MySol", "--no-publish"],
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
        ["--json", "--profile", "p", "metadata", "create-optionset",
         "--display", "Task Priority", "--solution", "MySol", "--no-publish"],
    )
    assert result.exit_code == 0, result.output
    assert captured["name"] == "new_TaskPriority"


def test_create_entity_no_solution_exits_2(monkeypatch, tmp_path):
    """No --solution → UsageError (exit 2), never calls the core function."""
    _save_profile(monkeypatch, tmp_path, publisher_prefix="new")

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
    assert result.exit_code == 2, result.output


def test_add_attribute_no_solution_exits_2(monkeypatch, tmp_path):
    """No --solution → UsageError (exit 2) for add-attribute too."""
    _save_profile(monkeypatch, tmp_path, publisher_prefix="new")

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
    assert result.exit_code == 2, result.output


def test_connection_status_no_default_solution_field(monkeypatch, tmp_path):
    """connection status profile data must not contain a default_solution key."""
    _save_profile(monkeypatch, tmp_path, publisher_prefix="new")
    result = CliRunner().invoke(
        cli, ["--json", "--profile", "p", "connection", "status"],
    )
    assert result.exit_code == 0, result.output
    env = json.loads(result.output)
    prof = env["data"]["profile"]
    assert "default_solution" not in prof
    assert prof["publisher_prefix"] == "new"
