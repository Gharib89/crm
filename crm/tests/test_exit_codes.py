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
    """Usage error under --json: unknown flag → exit 2 AND a parseable envelope."""
    result = CliRunner().invoke(cli, ["--json", "query", "count", "--nope"])
    assert result.exit_code == 2, result.output
    assert json.loads(result.output)["ok"] is False


def test_missing_required_option_json_envelope():
    """Missing required option under --json → exit 2 with parseable {ok: false}."""
    # `entity create <set>` requires payload; omit it and pass --data="" is not it —
    # instead drive a command with a genuinely missing required option. Here we use
    # the in-command UsageError site below; for a Click-level missing argument we
    # invoke `entity get` with no arguments at all.
    result = CliRunner().invoke(cli, ["--json", "entity", "get"])
    assert result.exit_code == 2, result.output
    env = json.loads(result.output)
    assert env["ok"] is False
    assert isinstance(env["error"], str) and env["error"]


def test_in_command_usage_error_json_envelope():
    """In-command UsageError (_load_payload) under --json → exit 2, parseable envelope."""
    result = CliRunner().invoke(cli, ["--json", "entity", "create", "accounts"])
    assert result.exit_code == 2, result.output
    env = json.loads(result.output)
    assert env["ok"] is False
    assert "--data" in env["error"]


def test_root_level_bad_parameter_json_envelope():
    """Root-callback BadParameter (--log-level) under --json → exit 2, parseable envelope."""
    result = CliRunner().invoke(
        cli, ["--json", "--log-level", "bogus", "query", "count", "account"]
    )
    assert result.exit_code == 2, result.output
    env = json.loads(result.output)
    assert env["ok"] is False
    assert isinstance(env["error"], str) and env["error"]


def test_usage_error_human_path_not_json():
    """No --json: usage error stays raw Click text on stderr, exit 2 (no regression)."""
    result = CliRunner().invoke(cli, ["query", "count", "--nope"])
    assert result.exit_code == 2, result.output
    assert "Error" in result.output
    try:
        json.loads(result.output)
        is_json = True
    except (ValueError, json.JSONDecodeError):
        is_json = False
    assert not is_json, "human path must not emit a JSON envelope"


def test_usage_error_json_exits_2_not_1():
    """The JSON-wrapped usage error stays exit 2, distinct from an operational
    failure (exit 1) — contrast both to prove the distinction is real."""
    usage = CliRunner().invoke(cli, ["--json", "query", "count", "--nope"])
    operational = CliRunner().invoke(
        cli, ["--json", "action", "invoke", "foo", "--bind-set", "workflows"]
    )
    assert usage.exit_code == 2, usage.output
    assert operational.exit_code == 1, operational.output


def test_repl_path_emits_json_envelope(capsys):
    """REPL --json line: a usage error emits the envelope to stdout, not skin.error.
    The signal is the '--json' token in argv, not a pre-set ctx flag — so we leave
    ctx.json_mode at its default to prove argv alone drives the envelope."""
    import click

    ctx = CLIContext()
    errors: list[str] = []
    ctx.skin.error = lambda msg: errors.append(msg)  # type: ignore[assignment]
    try:
        cli.main(
            args=["--json", "query", "count", "--nope"], obj=ctx,
            standalone_mode=False, prog_name="crm",
        )
    except (SystemExit, click.exceptions.Exit, click.ClickException):
        pass
    out = capsys.readouterr().out
    env = json.loads(out)
    assert env["ok"] is False
    assert errors == [], "skin.error must not be used in json mode"


def _repl_run(ctx, argv, errors):
    """Mirror repl.py's invocation + catch: run one line through the real wrapper,
    routing any leftover ClickException to skin.error like the loop does."""
    import click

    try:
        cli.main(args=argv, obj=ctx, standalone_mode=False, prog_name="crm")
    except (SystemExit, click.exceptions.Exit):
        pass
    except click.ClickException as exc:
        ctx.skin.error(exc.format_message())


def test_repl_path_human_uses_skin_error():
    """REPL without --json: usage error routes to skin.error text path, no envelope."""
    ctx = CLIContext()
    errors: list[str] = []
    ctx.skin.error = lambda msg: errors.append(msg)  # type: ignore[assignment]
    _repl_run(ctx, ["query", "count", "--nope"], errors)
    assert errors, "skin.error should carry the usage error text in human mode"


def test_repl_human_line_survives_prior_json_line(capsys):
    """Regression guard for the stale-json_mode bug: a --json line followed by a
    no-'--json' usage-error line on the SAME CLIContext must keep the human path —
    skin.error is called and NO JSON envelope is written to stdout for line 2."""
    ctx = CLIContext()
    errors: list[str] = []
    ctx.skin.error = lambda msg: errors.append(msg)  # type: ignore[assignment]

    # Line 1: a --json invocation. This sets ctx.json_mode=True via the root callback.
    _repl_run(ctx, ["--json", "--dry-run", "query", "count", "account"], errors)
    capsys.readouterr()  # discard line-1 output
    errors.clear()

    # Line 2: a HUMAN-mode usage error (no --json). The stale ctx.json_mode must NOT
    # cause a JSON envelope; argv has no '--json', so it stays human.
    _repl_run(ctx, ["query", "count", "--nope"], errors)
    out = capsys.readouterr().out
    assert errors, "human line after a json line must route to skin.error"
    try:
        json.loads(out)
        is_json = True
    except (ValueError, json.JSONDecodeError):
        is_json = False
    assert not is_json, "stale json_mode must not emit an envelope on a human line"
