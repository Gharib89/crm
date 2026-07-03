"""`crm examples [GROUP]` — a curated gallery of runnable example invocations (#658).

Thin Click wrapper over ``crm.core.examples``. On a human TTY it drives a
group→example picker and prints the chosen command for copy/edit (no auto-run in
v1). Under ``--json`` or off a TTY (agents, CI, pipes) it emits a plain listing —
the standard JSON envelope (ADR 0008) or a human table — so it never blocks on a
prompt.
"""
# pyright: basic
from __future__ import annotations

import click

from crm.cli import CLIContext, pass_ctx
from crm.commands._helpers.confirm import select_one
from crm.commands._tty import _stdin_is_tty, _stdout_is_tty
from crm.core import examples as reg


def _example_label(ex: reg.Example) -> str:
    """Picker label for one example: its description, tagged with the workflow
    sequence it belongs to (if any) so related steps read as a group."""
    if ex.workflow:
        return f"[{ex.workflow}] {ex.description}"
    return ex.description


@click.command("examples")
@click.argument("group", required=False)
@pass_ctx
def examples_cmd(ctx: CLIContext, group: str | None):
    """Browse a curated gallery of runnable example commands.

    With no GROUP on a terminal, pick a group then an example; the chosen command
    is printed for you to copy and edit. With GROUP, jump straight to that group's
    examples. Under --json or when not on a terminal, prints the full listing.
    """
    if group is not None and group not in reg.EXAMPLES:
        ctx.emit(
            False,
            error=f"No examples for group {group!r}; choose from: {', '.join(reg.groups())}.",
        )
        return

    # Interactive picker only when both stdin and stdout are real terminals.
    # Everything else (agents, CI, --json, or a piped/redirected stdout such as
    # `crm examples | head`) gets a non-blocking listing — a prompt whose UI
    # would be swallowed by the pipe must never block.
    if not ctx.json_mode and _stdin_is_tty() and _stdout_is_tty():
        _run_picker(ctx, group)
        return

    pairs = reg.listing(group)
    if ctx.json_mode:
        ctx.emit(True, data=[
            {"group": g, "command": ex.command, "description": ex.description}
            for g, ex in pairs
        ])
        return
    ctx.emit(True, table={
        "headers": ["group", "command", "description"],
        "rows": [[g, ex.command, ex.description] for g, ex in pairs],
    })


def _run_picker(ctx: CLIContext, group: str | None) -> None:
    """Human TTY flow: pick a group (if not given) then an example, and print the
    chosen command. Cancelling either picker is a clean no-selection failure."""
    if group is None:
        chosen = select_one(
            "Pick an example group",
            [(g, f"{g}  ({len(reg.examples_for(g))} examples)") for g in reg.groups()],
        )
        if not chosen:
            ctx.emit(False, error="no group selected")
            return
        group = chosen

    examples = reg.examples_for(group)
    command = select_one(
        f"Pick a {group} example",
        [(ex.command, _example_label(ex)) for ex in examples],
    )
    if not command:
        ctx.emit(False, error="no example selected")
        return

    # Print the command on its own line for easy copy/edit; the hint explains it
    # was not run (auto-run is deliberately out of scope for v1).
    ctx.skin.hint("copy, edit, and run:")
    click.echo(command)
