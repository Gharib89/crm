"""Integration tests for audit journal wiring (issue #89).

Verifies that mutating verbs append exactly one journal line, read verbs
append nothing, and result_id is derived correctly.
"""
# pyright: basic
from __future__ import annotations

from click.testing import CliRunner

from crm.cli import cli
from crm.core import audit
from crm.utils.d365_backend import ConnectionProfile


def _save_profile(monkeypatch, tmp_path, **kwargs):
    """Set up a minimal CRM_HOME with a saved profile and active-profile state.

    Mirrors the pattern from test_solution_targeting.py.
    """
    monkeypatch.setenv("CRM_HOME", str(tmp_path))
    # Point dotenv at a non-existent file so resolve_credentials can't auto-load
    # a developer's real ./.env and make these tests non-hermetic (#56 class).
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


# ── Test 1: dry-run mutation → exactly one journal line ───────────────────

def test_entity_create_dry_run_journals_one_line(monkeypatch, tmp_path):
    """entity create --dry-run → one journal entry with correct fields."""
    _save_profile(monkeypatch, tmp_path)

    from crm.core import entity as entity_mod

    def fake_create(_backend, entity_set, payload, **kwargs):
        return {"id": "aaaabbbb-0000-0000-0000-000000000001", "_dry_run": True}

    monkeypatch.setattr(entity_mod, "create", fake_create)

    result = CliRunner().invoke(
        cli,
        ["--json", "--profile", "p", "--dry-run",
         "entity", "create", "accounts", "--data", '{"name": "Contoso"}'],
    )
    assert result.exit_code == 0, result.output

    rows = audit.read("default")
    assert len(rows) == 1, f"expected 1 journal row, got {len(rows)}: {rows}"
    row = rows[0]
    assert row["command"] == "entity create"
    assert row["target"] == "accounts"
    assert row["dry_run"] is True
    assert row["ok"] is True


# ── Test 2: non-dry-run mutation with id → result_id populated ────────────

def test_entity_create_result_id_captured(monkeypatch, tmp_path):
    """entity create (non-dry-run, faked) → result_id equals the returned id."""
    _save_profile(monkeypatch, tmp_path)

    from crm.core import entity as entity_mod

    fake_id = "12345678-abcd-0000-0000-000000000099"

    def fake_create(_backend, entity_set, payload, **kwargs):
        return {"id": fake_id, "name": "Contoso"}

    monkeypatch.setattr(entity_mod, "create", fake_create)

    result = CliRunner().invoke(
        cli,
        ["--json", "--profile", "p",
         "entity", "create", "contacts", "--data", '{"firstname": "Alice"}'],
    )
    assert result.exit_code == 0, result.output

    rows = audit.read("default")
    assert len(rows) == 1, f"expected 1 journal row, got {len(rows)}: {rows}"
    row = rows[0]
    assert row["result_id"] == fake_id
    assert row["dry_run"] is False
    assert row["target"] == "contacts"


# ── Test 3: read verb → no journal line ───────────────────────────────────

def test_entity_get_does_not_journal(monkeypatch, tmp_path):
    """entity get is a read verb → audit journal stays empty."""
    _save_profile(monkeypatch, tmp_path)

    from crm.core import entity as entity_mod

    def fake_retrieve(_backend, entity_set, record_id, **kwargs):
        return {"accountid": record_id, "name": "Contoso"}

    monkeypatch.setattr(entity_mod, "retrieve", fake_retrieve)

    result = CliRunner().invoke(
        cli,
        ["--json", "--profile", "p",
         "entity", "get", "accounts",
         "aaaabbbb-0000-0000-0000-000000000001"],
    )
    assert result.exit_code == 0, result.output

    rows = audit.read("default")
    assert rows == [], f"expected empty journal for read verb, got: {rows}"


# ── Test 4: breadth — metadata create-entity dry-run ────────────────────

def test_metadata_create_entity_dry_run_journals(monkeypatch, tmp_path):
    """metadata create-entity --dry-run → one journal line with correct command."""
    _save_profile(monkeypatch, tmp_path, publisher_prefix="new")

    from crm.core import metadata as meta_mod

    def fake_create_entity(_backend, **kwargs):
        return {"schema_name": kwargs["schema_name"], "_dry_run": True}

    monkeypatch.setattr(meta_mod, "create_entity", fake_create_entity)

    result = CliRunner().invoke(
        cli,
        ["--json", "--profile", "p", "--dry-run",
         "metadata", "create-entity",
         "--display", "Project", "--solution", "MySol", "--no-publish"],
    )
    assert result.exit_code == 0, result.output

    rows = audit.read("default")
    assert len(rows) == 1, f"expected 1 journal row, got {len(rows)}: {rows}"
    row = rows[0]
    assert row["command"] == "metadata create-entity"
    assert row["target"] == "new_Project"
    assert row["solution"] == "MySol"
    assert row["dry_run"] is True
    assert row["ok"] is True
