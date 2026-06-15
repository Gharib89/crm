# pyright: basic
"""Regression tests for REPL --session stickiness (issue #128).

The REPL re-invokes `cli.main(args=argv, obj=ctx, ...)` for every typed line.
When the per-line argv omits --session, Click passes the literal default
"default" into the root callback.  Before the fix, this clobbered the session
name that was set at REPL-launch time.
"""
from __future__ import annotations

import pytest

from crm.cli import CLIContext, cli
from crm.commands.repl import _strip_repl_prefix

pytestmark = pytest.mark.usefixtures("isolated_home")


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


# ---------------------------------------------------------------------------
# Leading `crm` prefix strip (issue #300)
# ---------------------------------------------------------------------------

def test_strip_leading_crm_prefix():
    """A single leading `crm` token is dropped so the shell reflex works."""
    assert _strip_repl_prefix(["crm", "connection", "whoami"]) == ["connection", "whoami"]


def test_bare_crm_is_noop():
    """A bare `crm` line strips to empty and signals a no-op (None), not [] —
    dispatching [] would relaunch the REPL via invoke_without_command."""
    assert _strip_repl_prefix(["crm"]) is None


def test_prefixless_line_unchanged():
    """The accepted prefix-less form passes through untouched."""
    assert _strip_repl_prefix(["connection", "whoami"]) == ["connection", "whoami"]


def test_only_exact_crm_token_stripped():
    """A token that merely starts with `crm` (e.g. `crmfoo`) is not stripped."""
    assert _strip_repl_prefix(["crmfoo", "x"]) == ["crmfoo", "x"]


def test_strips_at_most_one_crm():
    """Only the first `crm` is stripped; a second is left as an argument."""
    assert _strip_repl_prefix(["crm", "crm", "whoami"]) == ["crm", "whoami"]
