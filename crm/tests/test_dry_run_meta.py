"""Tests for the canonical meta.dry_run signal (issue #61).

A dry-run invocation in JSON mode must carry meta.dry_run=true in the envelope,
keyed off the invocation-level flag (CLIContext.dry_run) — NOT off sniffing the
data for the _dry_run sentinel — so list-shaped batch previews and poll previews
are covered uniformly and forced-real existence-probe GETs do not false-positive.
Existing meta keys (e.g. staged) are preserved. The signal is scoped to JSON mode;
the in-data _dry_run sentinel is retained for back-compat (ADR 0002 reads it pre-emit).
"""
# pyright: basic
from __future__ import annotations

import json

import requests_mock
from click.testing import CliRunner

from crm.cli import CLIContext, cli


def _seed_profile(tmp_path, monkeypatch):
    """Isolate CRM_HOME and seed an NTLM profile + plaintext secret named 't'."""
    monkeypatch.setenv("CRM_HOME", str(tmp_path / ".crm"))
    monkeypatch.setenv("CRM_DOTENV", str(tmp_path / "noop.env"))
    from crm.core import session as session_mod
    from crm.utils.d365_backend import ConnectionProfile
    session_mod.save_profile(ConnectionProfile(
        name="t", url="https://crm.contoso.local/contoso",
        domain="CONTOSO", username="alice"))
    session_mod.save_profile_secret_plaintext("t", "pw")


def _emit_envelope(ctx, capsys, **kw):
    ctx.emit(True, **kw)
    return json.loads(capsys.readouterr().out)


class TestEmitMetaDryRun:
    """Direct unit tests on the single emit chokepoint."""

    def test_sets_meta_dry_run_in_json_mode(self, capsys):
        ctx = CLIContext()
        ctx.json_mode = True
        ctx.dry_run = True
        env = _emit_envelope(ctx, capsys, data={"x": 1})
        assert env["meta"]["dry_run"] is True

    def test_does_not_clobber_existing_meta(self, capsys):
        ctx = CLIContext()
        ctx.json_mode = True
        ctx.dry_run = True
        env = _emit_envelope(ctx, capsys, data={"x": 1}, meta={"staged": True})
        assert env["meta"]["staged"] is True
        assert env["meta"]["dry_run"] is True

    def test_no_flag_when_not_dry_run(self, capsys):
        ctx = CLIContext()
        ctx.json_mode = True
        ctx.dry_run = False
        env = _emit_envelope(ctx, capsys, data={"x": 1}, meta={"staged": True})
        assert "dry_run" not in env["meta"]

    def test_no_meta_key_when_not_dry_run_and_no_meta(self, capsys):
        ctx = CLIContext()
        ctx.json_mode = True
        ctx.dry_run = False
        env = _emit_envelope(ctx, capsys, data={"x": 1})
        assert "meta" not in env

    def test_does_not_mutate_caller_meta(self, capsys):
        ctx = CLIContext()
        ctx.json_mode = True
        ctx.dry_run = True
        caller_meta = {"staged": True}
        _emit_envelope(ctx, capsys, data={"x": 1}, meta=caller_meta)
        assert "dry_run" not in caller_meta

    def test_scoped_to_json_mode(self, capsys):
        ctx = CLIContext()
        ctx.json_mode = False
        ctx.dry_run = True
        ctx.emit(True, data={"x": 1}, meta={"staged": True})
        assert "dry_run" not in capsys.readouterr().out


class TestDryRunMetaEndToEnd:
    def test_entity_create_dry_run_has_meta_dry_run(self, tmp_path, monkeypatch):
        _seed_profile(tmp_path, monkeypatch)
        result = CliRunner().invoke(
            cli,
            ["--json", "--profile", "t", "--dry-run", "entity", "create", "accounts",
             "--data", '{"name": "Acme"}'],
        )
        assert result.exit_code == 0, result.output
        env = json.loads(result.output)
        assert env["meta"]["dry_run"] is True

    def test_batch_dry_run_has_meta_dry_run_and_preserves_summary(self, tmp_path, monkeypatch):
        _seed_profile(tmp_path, monkeypatch)
        p = tmp_path / "b.json"
        p.write_text('[{"method": "GET", "url": "accounts"}]', encoding="utf-8")
        result = CliRunner().invoke(
            cli,
            ["--json", "--profile", "t", "--dry-run", "batch", str(p)],
        )
        assert result.exit_code == 0, result.output
        env = json.loads(result.output)
        assert env["meta"]["dry_run"] is True
        assert env["meta"]["total"] == 1

    def test_read_verb_dry_run_executes_and_returns_real_data(self, tmp_path, monkeypatch):
        """Reads-execute rule: a read verb under --dry-run runs the real GET and
        returns live data (NOT the request echo); the envelope still flags
        meta.dry_run=true."""
        _seed_profile(tmp_path, monkeypatch)
        with requests_mock.Mocker() as m:
            m.get(requests_mock.ANY, json={"value": [{"name": "Acme Corp"}]})
            result = CliRunner().invoke(
                cli,
                ["--json", "--profile", "t", "--dry-run", "query", "odata", "accounts"],
            )
            assert [r.method for r in m.request_history] == ["GET"]
        assert result.exit_code == 0, result.output
        env = json.loads(result.output)
        assert env["meta"]["dry_run"] is True
        # Live data, not the {_dry_run, method, url, ...} echo.
        assert env["data"] == {"value": [{"name": "Acme Corp"}]}
        assert "_dry_run" not in env["data"]
