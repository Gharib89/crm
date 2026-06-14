# pyright: basic
"""Tests for _journal helper and `crm session audit` command (issue #89)."""
from __future__ import annotations

import json
from typing import Any

import click
import pytest
from click.testing import CliRunner

from crm.cli import cli
from crm.commands._helpers import _journal
from crm.core import audit

pytestmark = pytest.mark.usefixtures("isolated_home")


# ---------------------------------------------------------------------------
# Minimal stub for CLIContext (only the attrs _journal touches)
# ---------------------------------------------------------------------------

class _StubCtx:
    def __init__(self, *, session_name="test-session", profile_name=None,
                 dry_run=False, stage_only=False):
        self.session_name = session_name
        self.profile_name = profile_name
        self.dry_run = dry_run
        self.stage_only = stage_only
        self._backend: Any = None  # set by tests exercising resolved-profile


def _journal_in_ctx(stub_ctx, command, target, result, **kwargs):
    """Call `_journal` inside a synthetic Click context whose `command_path`
    is ``crm <command>`` — so `_journal`'s Click-derived name records as
    `command` (it no longer takes a hand-typed command argument; #264)."""
    names = ["crm", *command.split(" ")]
    cctx = click.Context(click.Command(names[0]), info_name=names[0])
    for name in names[1:]:
        cctx = click.Context(click.Command(name), info_name=name, parent=cctx)
    with cctx:
        _journal(stub_ctx, target, result, **kwargs)


# ---------------------------------------------------------------------------
# _journal helper tests
# ---------------------------------------------------------------------------

class TestJournalHelper:
    def test_writes_a_readable_entry(self):
        ctx = _StubCtx(session_name="sess1")
        _journal_in_ctx(ctx, "entity create", "account", {"id": "aabbccdd-0000-0000-0000-000000000001"})
        rows = audit.read("sess1")
        assert len(rows) == 1
        r = rows[0]
        assert r["command"] == "entity create"
        assert r["target"] == "account"
        assert r["result_id"] == "aabbccdd-0000-0000-0000-000000000001"

    def test_staged_reflects_ctx_stage_only(self):
        ctx = _StubCtx(stage_only=True)
        _journal_in_ctx(ctx, "cmd", "t", {})
        rows = audit.read(ctx.session_name)
        assert rows[0]["staged"] is True

    def test_dry_run_reflects_ctx_dry_run(self):
        ctx = _StubCtx(dry_run=True)
        _journal_in_ctx(ctx, "cmd", "t", {})
        rows = audit.read(ctx.session_name)
        assert rows[0]["dry_run"] is True

    def test_explicit_staged_overrides_ctx_stage_only(self):
        ctx = _StubCtx(stage_only=False)
        _journal_in_ctx(ctx, "cmd", "t", {}, staged=True)
        rows = audit.read(ctx.session_name)
        assert rows[0]["staged"] is True

    def test_explicit_solution_is_recorded(self):
        ctx = _StubCtx()
        _journal_in_ctx(ctx, "cmd", "t", {}, solution="mysolution")
        rows = audit.read(ctx.session_name)
        assert rows[0]["solution"] == "mysolution"

    def test_profile_falls_back_to_profile_name_without_backend(self):
        ctx = _StubCtx(profile_name="explicitprof")
        _journal_in_ctx(ctx, "cmd", "t", {})
        rows = audit.read(ctx.session_name)
        assert rows[0]["profile"] == "explicitprof"

    def test_profile_prefers_resolved_backend_profile_name(self):
        # An env/active-profile run leaves profile_name None but builds a backend;
        # the journal should record the RESOLVED profile name, not None.
        class _Prof:
            name = "resolvedprof"

        class _Backend:
            profile = _Prof()

        ctx = _StubCtx(profile_name=None)
        ctx._backend = _Backend()
        _journal_in_ctx(ctx, "cmd", "t", {})
        rows = audit.read(ctx.session_name)
        assert rows[0]["profile"] == "resolvedprof"

    def test_never_raises_when_audit_record_blows_up(self, monkeypatch):
        monkeypatch.setattr("crm.core.audit.record", lambda **_: (_ for _ in ()).throw(RuntimeError("boom")))
        ctx = _StubCtx()
        # Must not raise
        _journal_in_ctx(ctx, "cmd", "t", {})


# ---------------------------------------------------------------------------
# `crm session audit` command tests
# ---------------------------------------------------------------------------

class TestSessionAuditCommand:
    def _run(self, *args):
        return CliRunner().invoke(cli, list(args))

    def test_empty_journal_human(self):
        result = self._run("session", "audit")
        assert result.exit_code == 0
        assert "No audit entries" in result.output

    def test_empty_journal_json(self):
        result = self._run("--json", "session", "audit")
        assert result.exit_code == 0
        data = json.loads(result.output)
        assert data["ok"] is True
        assert data["data"] == []
        assert data["meta"]["count"] == 0
        assert "session" in data["meta"]

    def test_tail_returns_last_entry(self):
        # Seed two entries for the default session
        audit.record(session="default", profile=None, command="cmd1", target="t1", result={})
        audit.record(session="default", profile=None, command="cmd2", target="t2", result={})
        result = self._run("--json", "session", "audit", "--tail", "1")
        assert result.exit_code == 0
        data = json.loads(result.output)
        assert len(data["data"]) == 1
        assert data["data"][0]["command"] == "cmd2"

    def test_tail_zero_is_a_usage_error(self):
        # --tail must be >= 1 (IntRange); 0 would otherwise return ALL rows.
        result = self._run("session", "audit", "--tail", "0")
        assert result.exit_code == 2
        assert "tail" in result.output.lower()

    def test_session_override_reads_different_session(self):
        # Seed entries in two different sessions
        audit.record(session="alpha", profile=None, command="alpha-cmd", target="t", result={})
        audit.record(session="beta", profile=None, command="beta-cmd", target="t", result={})
        # Global --session sets the active session to "alpha"; subcommand --session
        # overrides to read "beta" instead.
        result = self._run("--json", "--session", "alpha", "session", "audit",
                           "--session", "beta")
        assert result.exit_code == 0, result.output
        data = json.loads(result.output)
        # The --session override on the subcommand selects "beta"
        assert data["meta"]["session"] == "beta"
        assert data["data"][0]["command"] == "beta-cmd"

    def test_human_mode_shows_entries(self):
        audit.record(session="default", profile=None, command="entity create",
                     target="account", result={"id": "aabbccdd-1111-0000-0000-000000000001"})
        result = self._run("session", "audit")
        assert result.exit_code == 0
        assert "entity create" in result.output
        assert "account" in result.output

    def test_human_mode_shows_flags(self):
        audit.record(session="default", profile=None, command="entity create",
                     target="account", result={}, dry_run=True, staged=True)
        result = self._run("session", "audit")
        assert result.exit_code == 0
        assert "dry-run" in result.output
        assert "staged" in result.output
