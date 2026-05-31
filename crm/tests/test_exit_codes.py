"""Exit-code contract (ADR 0001): 0 success / 1 operational failure / 2 usage error.

These tests pin the signal coding agents loop on. See CONTEXT.md for the terms
(operational failure, usage error, emit envelope).
"""
# pyright: basic
from __future__ import annotations

import json

from click.testing import CliRunner

from crm.cli import CLIContext, cli
from crm.utils.d365_backend import D365Error


def test_in_command_validation_exits_1():
    """Operational failure: in-command validation (--bind-set without --bind-id)."""
    result = CliRunner().invoke(
        cli, ["--json", "action", "invoke", "foo", "--bind-set", "workflows"]
    )
    assert result.exit_code == 1, result.output
    assert json.loads(result.output)["ok"] is False


def test_d365_server_error_exits_1(monkeypatch):
    """Operational failure: a D365Error from the backend → exit 1, status in meta."""
    class StubBackend:
        def get(self, _path, **_kw):
            raise D365Error("Record Not Found", status=404, code="0x80040217")

    monkeypatch.setattr(CLIContext, "backend", lambda self: StubBackend())
    result = CliRunner().invoke(cli, ["--json", "query", "count", "account"])
    assert result.exit_code == 1, result.output
    env = json.loads(result.output)
    assert env["ok"] is False
    assert env["meta"]["status"] == 404


def test_declined_confirmation_exits_1():
    """Operational failure: a declined confirmation prompt → exit 1."""
    result = CliRunner().invoke(
        cli, ["--json", "metadata", "delete-entity", "new_widget"], input="\n"
    )
    assert result.exit_code == 1, result.output
    # output carries the confirm prompt before the envelope, so match the substring
    assert '"error": "aborted by user"' in result.output


def test_success_exits_0(monkeypatch):
    """Success: a command that achieves its effect → exit 0, ok=true."""
    class StubBackend:
        def get(self, _path, **_kw):
            return {"EntityRecordCountCollection": {"Keys": ["account"], "Values": [3]}}

    monkeypatch.setattr(CLIContext, "backend", lambda self: StubBackend())
    result = CliRunner().invoke(cli, ["--json", "query", "count", "account"])
    assert result.exit_code == 0, result.output
    assert json.loads(result.output)["ok"] is True


def test_usage_error_exits_2():
    """Usage error: an unknown flag is rejected by Click → exit 2 (not JSON-wrapped)."""
    result = CliRunner().invoke(cli, ["--json", "query", "count", "--nope"])
    assert result.exit_code == 2
