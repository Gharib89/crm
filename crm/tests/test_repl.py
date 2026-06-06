# pyright: basic
"""Regression tests for REPL --session stickiness (issue #128).

The REPL re-invokes `cli.main(args=argv, obj=ctx, ...)` for every typed line.
When the per-line argv omits --session, Click passes the literal default
"default" into the root callback.  Before the fix, this clobbered the session
name that was set at REPL-launch time.
"""
from __future__ import annotations

import os

import pytest

from crm.cli import CLIContext, cli


# ---------------------------------------------------------------------------
# Env-isolation fixture (same pattern as test_session_audit.py)
# ---------------------------------------------------------------------------

@pytest.fixture(autouse=True)
def _isolated_env(tmp_path):
    """Snapshot/restore os.environ; redirect CRM_HOME and disable .env autoload."""
    saved = dict(os.environ)
    os.environ["CRM_HOME"] = str(tmp_path / ".crm")
    os.environ["CRM_DOTENV"] = str(tmp_path / "noop.env")
    try:
        yield tmp_path
    finally:
        os.environ.clear()
        os.environ.update(saved)


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _repl_invoke(argv: list[str], ctx: CLIContext) -> None:
    """Simulate one REPL line: invoke cli.main the same way repl.py does.

    Swallows only Click's controlled exits (Exit / SystemExit) — the normal way
    a command signals its exit code. Any unexpected error (ClickException, or
    anything else) is allowed to propagate so a genuine break in the command
    path or root callback fails the test instead of silently passing.
    """
    import click

    try:
        cli.main(args=argv, obj=ctx, standalone_mode=False, prog_name="crm")
    except SystemExit:
        pass
    except click.exceptions.Exit:
        pass


# ---------------------------------------------------------------------------
# Test cases
# ---------------------------------------------------------------------------

def test_session_sticky_across_bare_repl_lines():
    """REPL lines with no --session must NOT reset the launched session name."""
    ctx = CLIContext()
    ctx.session_name = "work"

    # Simulate a bare REPL line (no --session); subcommand may fail — that's fine.
    # session info is a read-only command that reaches the root callback before
    # trying to do anything backend-related, so it exercises the relevant code path.
    _repl_invoke(["session", "info"], ctx)

    assert ctx.session_name == "work", (
        f"session_name was reset to {ctx.session_name!r}; expected 'work'"
    )


def test_explicit_session_switch_in_repl():
    """An explicit --session on a REPL line SHOULD switch the active session."""
    ctx = CLIContext()
    ctx.session_name = "work"

    _repl_invoke(["--session", "other", "session", "info"], ctx)

    assert ctx.session_name == "other", (
        f"session_name should have switched to 'other', got {ctx.session_name!r}"
    )


def test_non_repl_bare_invocation_defaults():
    """A fresh CLIContext with a bare command (no --session) keeps 'default'."""
    ctx = CLIContext()
    # session_name starts as 'default' per CLIContext.__init__

    _repl_invoke(["session", "info"], ctx)

    assert ctx.session_name == "default", (
        f"expected 'default' for a fresh context, got {ctx.session_name!r}"
    )
