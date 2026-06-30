"""Tests for connection identity in the emit envelope (issue #624).

Every `--json` SUCCESS envelope from a command that resolved a backend carries
`meta.profile` (the resolved profile name) and `meta.url` (the resolved Web API
base), so an agent can tell which org/profile served a result from the output
alone. Injected once at the single emit chokepoint. Error envelopes keep their
reserved meta shape; local/meta verbs that never connect stay clean; human
output is unaffected (the injection is JSON-only).
"""
# pyright: basic
from __future__ import annotations

import json

import click
import requests_mock
from click.testing import CliRunner

from crm.cli import CLIContext, cli
from crm.utils.d365_backend import ConnectionProfile, D365Backend

_API_BASE = "https://crm.contoso.local/contoso/api/data/v9.2/"


def _seed_profile(tmp_path, monkeypatch):
    """Isolate CRM_HOME and seed an NTLM profile + plaintext secret named 't'."""
    monkeypatch.setenv("CRM_HOME", str(tmp_path / ".crm"))
    monkeypatch.setenv("CRM_DOTENV", str(tmp_path / "noop.env"))
    from crm.core import session as session_mod
    session_mod.save_profile(ConnectionProfile(
        name="t", url="https://crm.contoso.local/contoso",
        domain="CONTOSO", username="alice"))
    session_mod.save_profile_secret_plaintext("t", "pw")


def _ctx_with_backend() -> CLIContext:
    ctx = CLIContext()
    ctx.json_mode = True
    profile = ConnectionProfile(
        name="t", url="https://crm.contoso.local/contoso",
        domain="CONTOSO", username="alice")
    ctx._backend = D365Backend(profile, "pw")
    ctx.connection_resolved = True
    return ctx


def _emit(ctx, capsys, ok=True, **kw):
    # emit(ok=False) prints the envelope then raises click Exit; swallow it so
    # the printed envelope can still be inspected.
    try:
        ctx.emit(ok, **kw)
    except click.exceptions.Exit:
        pass
    return json.loads(capsys.readouterr().out)


class TestEmitConnectionIdentity:
    def test_success_envelope_carries_profile_and_url(self, capsys):
        env = _emit(_ctx_with_backend(), capsys, data={"x": 1})
        assert env["meta"]["profile"] == "t"
        assert env["meta"]["url"] == _API_BASE

    def test_merges_with_existing_meta_without_clobbering(self, capsys):
        env = _emit(_ctx_with_backend(), capsys, data={"x": 1}, meta={"count": 3})
        assert env["meta"]["count"] == 3
        assert env["meta"]["profile"] == "t"
        assert env["meta"]["url"] == _API_BASE

    def test_error_envelope_is_not_stamped(self, capsys):
        # Error envelopes keep their reserved meta shape (#624 out of scope).
        env = _emit(_ctx_with_backend(), capsys, ok=False,
                    error="boom", meta={"status": 500})
        assert "profile" not in env.get("meta", {})
        assert "url" not in env.get("meta", {})

    def test_local_verb_without_backend_is_not_stamped(self, capsys):
        ctx = CLIContext()
        ctx.json_mode = True  # never resolved a backend
        env = _emit(ctx, capsys, data={"x": 1})
        assert "meta" not in env

    def test_invalidated_backend_is_not_stamped(self, capsys):
        # `profile add` resolves then invalidates the backend before emit; a
        # cleared _backend must not be stamped even with the flag still set.
        ctx = _ctx_with_backend()
        ctx._backend = None
        env = _emit(ctx, capsys, data={"x": 1}, meta={"profile": "as-set"})
        assert env["meta"] == {"profile": "as-set"}
        assert "url" not in env["meta"]

    def test_human_output_is_unaffected(self, capsys):
        ctx = _ctx_with_backend()
        ctx.json_mode = False
        ctx.emit(True, data={"x": 1})
        out = capsys.readouterr().out
        assert _API_BASE not in out
        assert "profile" not in out.lower()


class TestEndToEndCoverage:
    """Acceptance: meta on a representative read AND mutate; override reflected;
    a verb that never connects stays clean."""

    def test_read_verb_envelope_carries_profile_and_url(self, tmp_path, monkeypatch):
        _seed_profile(tmp_path, monkeypatch)
        with requests_mock.Mocker() as m:
            m.get(requests_mock.ANY, json={"value": [{"name": "Acme"}]})
            result = CliRunner().invoke(
                cli, ["--json", "--profile", "t", "query", "odata", "accounts"])
        assert result.exit_code == 0, result.output
        meta = json.loads(result.output)["meta"]
        assert meta["profile"] == "t"
        assert meta["url"] == _API_BASE

    def test_mutate_verb_envelope_carries_profile_and_url(self, tmp_path, monkeypatch):
        _seed_profile(tmp_path, monkeypatch)
        guid = "11111111-1111-1111-1111-111111111111"
        with requests_mock.Mocker() as m:
            m.post(f"{_API_BASE}accounts", status_code=204,
                   headers={"OData-EntityId": f"{_API_BASE}accounts({guid})"})
            result = CliRunner().invoke(
                cli, ["--json", "--profile", "t", "entity", "create", "accounts",
                      "--data", '{"name": "Acme"}', "--no-return"])
        assert result.exit_code == 0, result.output
        meta = json.loads(result.output)["meta"]
        assert meta["profile"] == "t"
        assert meta["url"] == _API_BASE

    def test_profile_override_is_reflected(self, tmp_path, monkeypatch):
        # Active profile is 't'; a per-run --profile override must win in meta.
        _seed_profile(tmp_path, monkeypatch)
        from crm.core import session as session_mod
        session_mod.save_profile(ConnectionProfile(
            name="prod", url="https://crm.contoso.local/prod",
            domain="CONTOSO", username="bob"))
        session_mod.save_profile_secret_plaintext("prod", "pw")
        state = session_mod.load_session("default")
        state["active_profile"] = "t"
        session_mod.save_session(state, "default")
        with requests_mock.Mocker() as m:
            m.get(requests_mock.ANY, json={"value": []})
            result = CliRunner().invoke(
                cli, ["--json", "--profile", "prod", "query", "odata", "accounts"])
        assert result.exit_code == 0, result.output
        meta = json.loads(result.output)["meta"]
        assert meta["profile"] == "prod"
        assert meta["url"] == "https://crm.contoso.local/prod/api/data/v9.2/"

    def test_verb_that_never_connects_has_no_url(self, tmp_path, monkeypatch):
        # `connection status` is local — it must not gain a spurious meta.url,
        # proving the stamp is per-verb (connection opened) not per-group.
        monkeypatch.setenv("CRM_HOME", str(tmp_path / ".crm"))
        result = CliRunner().invoke(cli, ["--json", "connection", "status"])
        assert result.exit_code == 0, result.output
        env = json.loads(result.output)
        assert "url" not in env.get("meta", {})
        assert "profile" not in env.get("meta", {})


class TestWhoamiEnriched:
    def test_whoami_data_carries_profile_url_org_name_and_guids(
        self, tmp_path, monkeypatch
    ):
        _seed_profile(tmp_path, monkeypatch)
        with requests_mock.Mocker() as m:
            m.get(f"{_API_BASE}WhoAmI", json={
                "@odata.context": f"{_API_BASE}$metadata#Microsoft.Dynamics.CRM.WhoAmIResponse",
                "UserId": "11111111-1111-1111-1111-111111111111",
                "BusinessUnitId": "22222222-2222-2222-2222-222222222222",
                "OrganizationId": "33333333-3333-3333-3333-333333333333",
            })
            m.get(
                f"{_API_BASE}organizations(33333333-3333-3333-3333-333333333333)",
                json={"name": "Contoso Dev"},
            )
            result = CliRunner().invoke(
                cli, ["--json", "--profile", "t", "connection", "whoami"])
        assert result.exit_code == 0, result.output
        data = json.loads(result.output)["data"]
        assert data["UserId"] == "11111111-1111-1111-1111-111111111111"
        assert data["BusinessUnitId"] == "22222222-2222-2222-2222-222222222222"
        assert data["OrganizationId"] == "33333333-3333-3333-3333-333333333333"
        assert data["profile"] == "t"
        assert data["url"] == _API_BASE
        assert data["org_name"] == "Contoso Dev"

    def test_whoami_org_name_best_effort_on_lookup_failure(
        self, tmp_path, monkeypatch
    ):
        # A failed org-name read leaves org_name null, never masks the probe.
        _seed_profile(tmp_path, monkeypatch)
        with requests_mock.Mocker() as m:
            m.get(f"{_API_BASE}WhoAmI", json={
                "UserId": "11111111-1111-1111-1111-111111111111",
                "BusinessUnitId": "22222222-2222-2222-2222-222222222222",
                "OrganizationId": "33333333-3333-3333-3333-333333333333",
            })
            m.get(
                f"{_API_BASE}organizations(33333333-3333-3333-3333-333333333333)",
                status_code=403, json={"error": {"message": "denied"}},
            )
            result = CliRunner().invoke(
                cli, ["--json", "--profile", "t", "connection", "whoami"])
        assert result.exit_code == 0, result.output
        data = json.loads(result.output)["data"]
        assert data["org_name"] is None
        assert data["profile"] == "t"
