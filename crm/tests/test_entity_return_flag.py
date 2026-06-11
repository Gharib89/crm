"""Symmetric echo flags on `entity create`/`update` (#230).

Both verbs accept `--no-return` and `--return-record`; each keeps its own
default (create echoes, update is silent). Passing both is a usage error (exit 2).
The wire effect is the `Prefer: return=representation` header, which we capture
off a stub backend.
"""
# pyright: basic
from __future__ import annotations

import json

import pytest
from click.testing import CliRunner

from crm.cli import CLIContext, cli
from crm.commands.entity import _resolve_return_record


class RecordingBackend:
    """Captures the extra_headers handed to post/patch so the Prefer header is asserted."""

    def __init__(self):
        self.headers: dict[str, str] | None = None

    def post(self, _entity_set, *, extra_headers=None, **_kw):
        self.headers = extra_headers
        return {"id": "00000000-0000-0000-0000-000000000001"}

    def patch(self, _path, *, extra_headers=None, **_kw):
        self.headers = extra_headers
        return {}


def _prefers_representation(backend: RecordingBackend) -> bool:
    if not backend.headers:
        return False
    directives = [d.strip() for d in backend.headers.get("Prefer", "").split(",")]
    return "return=representation" in directives


# ── resolver unit table ───────────────────────────────────────────────────


@pytest.mark.parametrize(
    "no_return,return_record,default,expected",
    [
        # create default = echo on
        (False, False, True, True),
        (True, False, True, False),
        (False, True, True, True),
        # update default = silent
        (False, False, False, False),
        (False, True, False, True),
        (True, False, False, False),
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
