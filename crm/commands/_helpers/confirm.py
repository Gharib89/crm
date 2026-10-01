"""Confirm / interactive-select UX helpers."""

# pyright: basic
from __future__ import annotations

from typing import TYPE_CHECKING

import click

from crm.commands._tty import _stdin_is_tty

if TYPE_CHECKING:
    from crm.cli import CLIContext


def _confirm_destructive(
    ctx: CLIContext,
    thing: str,
    name: str,
    yes: bool,
    *,
    message: str | None = None,
    skip_on_dry_run: bool = True,
) -> None:
    """Gate a destructive op behind a confirmation; emit + abort on decline (#264).

    `--yes` skips the prompt. Under a dry-run preview, callers usually skip the
    prompt too: nothing destructive will execute, so the command should reach its
    preview path. Local-only callers with no dry-run preview can opt out via
    `skip_on_dry_run=False`.

    In non-interactive mode (``--json`` or stdin is not a TTY), a destructive
    command must fail fast rather than blocking on stdin: emit a clean error that
    names `--yes` and raise `Exit(1)`. On an interactive decline, emit the
    documented ``{"ok": false, "error": "aborted by user"}`` envelope via
    `ctx.emit(False)` (which raises `Exit(1)`), so control never returns to the
    caller and click's raw ``Aborted!`` message is never shown. Returns
    normally only when the user proceeds, so the call site drops its
    `if not ...:` decline two-liner.

    `message` overrides the default delete wording for non-delete destructive
    ops (e.g. an overwrite-import that names the actual risk) — see #67.
    """
    if yes or (ctx.dry_run and skip_on_dry_run):
        return
    prompt = message or (
        f"This will permanently delete {thing} {name!r} and all related data. Continue?"
    )
    if ctx.json_mode or not _stdin_is_tty():
        text = prompt.strip()
        for tail in (" Continue?", "?"):
            if text.endswith(tail):
                text = text[: -len(tail)].rstrip()
                break
        suffix = "" if text.endswith((".", "!", ":")) else "."
        ctx.emit(
            False,
            error=(
                f"{text}{suffix} Pass --yes to continue "
                "(no interactive prompt under --json or a non-TTY)."
            ),
        )
    try:
        proceed = click.confirm(prompt, default=False)
    except click.Abort:
        proceed = False
    if not proceed:
        ctx.emit(False, error="aborted by user")


def _destructive_option(f):
    """Stack the standard `--yes` confirm-skip flag on a destructive command.

    Pairs with `_confirm_destructive` in the verb body. Intentionally offers
    `--yes` only, with no `-y` short alias: this is the one canonical confirm-skip
    spelling across the CLI. The `profile add` / `profile rm` verbs are the
    deliberate exception — as the most-typed interactive setup verbs they keep a
    `-y` short alias (and bespoke help) via their own inline `@click.option`
    (#294). The split is by design; do not "fix" it by adding `-y` here.
    """
    return click.option(
        "--yes",
        is_flag=True,
        # Explicit: click 8.5 leaves an unset flag default as Sentinel.UNSET
        # until parse; the --yes-off-by-default invariant is pinned by tests.
        default=False,
        help="Skip interactive confirmation.",
    )(f)


def select_one(title: str, items: list[tuple[str, str]], default: str | None = None) -> str | None:
    """Show an inline arrow-key single-select picker; return the chosen value
    (the first element of the chosen tuple) or None if the user cancelled.

    `items` is a list of (value, label) pairs. `default`, if given, is a value
    that should be pre-selected and must match one of the item values. Raises
    ValueError on empty input or a default that isn't among the choices, and
    RuntimeError when stdin is not a TTY (scripts/CI must pass an explicit
    choice instead of relying on the picker).
    """
    if not items:
        raise ValueError("select_one: no choices to display")
    if default is not None and default not in {value for value, _ in items}:
        raise ValueError(f"select_one: default {default!r} is not among the choices")
    if not _stdin_is_tty():
        raise RuntimeError("select_one: no interactive terminal — pass an explicit choice instead")
    # Lazy import: questionary (and its prompt_toolkit backend) is heavy; keep
    # it off the `crm --version` fast path (_helpers is imported by cli.py).
    # questionary.select renders inline (↑/↓ + Enter confirms, Esc cancels) —
    # no alternate-screen modal — and .ask() returns None on cancel.
    import questionary

    choices = [questionary.Choice(title=label, value=value) for value, label in items]
    return questionary.select(title, choices=choices, default=default).ask()
