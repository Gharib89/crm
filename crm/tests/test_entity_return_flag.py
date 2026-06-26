"""Symmetric echo flags on `entity create`/`update` (#230).

Both verbs accept `--no-return` and `--return-record`; each keeps its own
default (create echoes, update is silent). Passing both is a usage error (exit 2).
The wire effect is the `Prefer: return=representation` header, which we capture
off a stub backend.
"""
# pyright: basic
from __future__ import annotations

import json
from typing import cast

import pytest
from click.testing import CliRunner

from crm.cli import CLIContext, cli
from crm.commands.entity import _resolve_return_record
from crm.utils.d365_backend import ConnectionProfile, D365Backend

# `entity create` resolves the entity's primary id through the read-through name
# cache to inject `_entity_id`; isolate CRM_HOME so it never touches a real cache.
pytestmark = pytest.mark.usefixtures("isolated_home")


class RecordingBackend:
    """Captures the extra_headers handed to post/patch so the Prefer header is asserted."""

    def __init__(self):
        self.headers: dict[str, str] | None = None
        self.profile = ConnectionProfile(
            name="testp", url="https://crm.contoso.local/contoso",
            domain="CONTOSO", username="alice", api_version="v9.2",
        )

    def post(self, _entity_set, *, extra_headers=None, **_kw):
        self.headers = extra_headers
        return {"id": "00000000-0000-0000-0000-000000000001"}

    def patch(self, _path, *, extra_headers=None, **_kw):
        self.headers = extra_headers
        return {}

    def get_collection(self, _path=None, **_kw):
        # No entity definitions → empty name map → create's _entity_id injection
        # is a no-op (the focus here is the Prefer header, not the id key).
        return []

    def url_for(self, path):
        import urllib.parse
        return urllib.parse.urljoin(self.profile.api_base, path.lstrip("/"))


def _prefers_representation(backend: RecordingBackend) -> bool:
    if not backend.headers:
        return False
    directives = [d.strip() for d in backend.headers.get("Prefer", "").split(",")]
    return "return=representation" in directives


# ── create() merges extra_headers with Prefer ──────────────────────────────


def test_create_merges_extra_headers_with_prefer(monkeypatch):
    """A caller's extra_headers ride the create POST alongside Prefer (#483)."""
    from crm.core import entity as entity_mod

    backend = RecordingBackend()
    entity_mod.create(
        cast(D365Backend, backend), "roles", {"name": "x"},
        extra_headers={"MSCRM.SolutionUniqueName": "mysol"},
    )
    assert _prefers_representation(backend)
    assert backend.headers is not None
    assert backend.headers.get("MSCRM.SolutionUniqueName") == "mysol"


# ── resolver unit table ───────────────────────────────────────────────────


@pytest.mark.parametrize(
    "no_return,return_record,default,expected",
    [
        # default echoes; explicit flags short-circuit before `default` is read,
        # so each short-circuit needs only one row (default value irrelevant there).
        (False, False, True, True),    # neither flag → default (echo on)
        (False, False, False, False),  # neither flag → default (silent)
        (True, False, True, False),    # no_return wins → False
        (False, True, True, True),     # return_record wins → True
    ],
)
def test_resolver_table(no_return, return_record, default, expected):
    assert _resolve_return_record(no_return, return_record, default=default) is expected


# ── CLI: new symmetric forms accepted ──────────────────────────────────────


def _invoke(monkeypatch, args):
    backend = RecordingBackend()
    monkeypatch.setattr(CLIContext, "backend", lambda self: backend)
    result = CliRunner().invoke(cli, args)
    return result, backend


def test_update_no_return_succeeds_no_echo(monkeypatch):
    result, backend = _invoke(
        monkeypatch,
        ["--json", "entity", "update", "accounts", "00000000-0000-0000-0000-000000000009", "--data", '{"statuscode":1}', "--no-return"],
    )
    assert result.exit_code == 0, result.output
    assert json.loads(result.output)["ok"] is True
    assert not _prefers_representation(backend)


def test_create_return_record_succeeds_echoes(monkeypatch):
    result, backend = _invoke(
        monkeypatch,
        ["--json", "entity", "create", "accounts", "--data", '{"name":"x"}', "--return-record"],
    )
    assert result.exit_code == 0, result.output
    assert _prefers_representation(backend)


# ── CLI: existing forms unchanged ──────────────────────────────────────────


def test_create_no_return_unchanged(monkeypatch):
    _, backend = _invoke(
        monkeypatch,
        ["--json", "entity", "create", "accounts", "--data", '{"name":"x"}', "--no-return"],
    )
    assert not _prefers_representation(backend)


def test_create_default_echoes(monkeypatch):
    _, backend = _invoke(
        monkeypatch, ["--json", "entity", "create", "accounts", "--data", '{"name":"x"}']
    )
    assert _prefers_representation(backend)


def test_update_return_record_unchanged(monkeypatch):
    _, backend = _invoke(
        monkeypatch,
        ["--json", "entity", "update", "accounts", "00000000-0000-0000-0000-000000000009", "--data", '{"x":1}', "--return-record"],
    )
    assert _prefers_representation(backend)


def test_update_default_silent(monkeypatch):
    _, backend = _invoke(
        monkeypatch, ["--json", "entity", "update", "accounts", "00000000-0000-0000-0000-000000000009", "--data", '{"x":1}']
    )
    assert not _prefers_representation(backend)


# ── CLI: both flags → exit 2 ───────────────────────────────────────────────


def test_create_both_flags_exit_2():
    result = CliRunner().invoke(
        cli,
        ["--json", "entity", "create", "accounts", "--data", "{}", "--no-return", "--return-record"],
    )
    assert result.exit_code == 2, result.output
    assert json.loads(result.output)["ok"] is False


def test_update_both_flags_exit_2():
    result = CliRunner().invoke(
        cli,
        ["--json", "entity", "update", "accounts", "00000000-0000-0000-0000-000000000009", "--data", "{}", "--no-return", "--return-record"],
    )
    assert result.exit_code == 2, result.output
    assert json.loads(result.output)["ok"] is False
