"""Confirm / secret-warning / interactive-select UX helpers."""
# pyright: basic
from __future__ import annotations
import os
from typing import TYPE_CHECKING
import click
from crm.commands._tty import _stdin_is_tty
if TYPE_CHECKING:
    from crm.cli import CLIContext


def _plaintext_secret_warning() -> str:
    """Warning shown after writing a profile secret in PLAINTEXT.

    Shared by `profile add` and `profile set-password` so the wording
    stays identical. POSIX notes the 0600 mode; Windows adds that file perms are
    NOT enforced and steers to --store-password (Credential Manager).
    """
    if os.name == "posix":
        return "Stored the secret in PLAINTEXT in the profile file (0600)."
    return (
        "Stored the secret in PLAINTEXT in the profile file. On Windows file "
        "permissions are NOT enforced — prefer --store-password (Credential Manager)."
    )


def _confirm_destructive(
    ctx: "CLIContext", thing: str, name: str, yes: bool, *, message: str | None = None
) -> None:
    """Gate a destructive op behind a confirmation; emit + abort on decline (#264).

    `--yes` skips the prompt. On decline — or a true non-TTY (EOF) stdin, where
    `click.confirm` raises `click.Abort` — this emits the documented
    ``{"ok": false, "error": "aborted by user"}`` envelope via `ctx.emit(False)`
    (which raises `Exit(1)`), so control never returns to the caller and click's
    bare ``Aborted!`` with no JSON is never shown. Returns normally only when the
    user proceeds, so the call site drops its `if not ...:` decline two-liner.

    `message` overrides the default delete wording for non-delete destructive
    ops (e.g. an overwrite-import that names the actual risk) — see #67.
    """
    if yes:
        return
    prompt = message or (
        f"This will permanently delete {thing} {name!r} and all related data. Continue?"
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
        "--yes", is_flag=True, help="Skip interactive confirmation.",
    )(f)


def select_one(title: str, items: list[tuple[str, str]],
               default: str | None = None) -> str | None:
    """Show an inline arrow-key single-select picker; return the chosen value
    (the first element of the chosen tuple) or None if the user cancelled.

    `items` is a list of (value, label) pairs. `default`, if given, is a value
    that should be pre-selected and must match one of the item values. Raises
    ValueError on empty input or a default that isn't among the choices, and
    RuntimeError when stdin is not a TTY (scripts/CI must pass an explicit
    choice instead of relying on the picker)."""
    if not items:
        raise ValueError("select_one: no choices to display")
    if default is not None and default not in {value for value, _ in items}:
        raise ValueError(f"select_one: default {default!r} is not among the choices")
    if not _stdin_is_tty():
        raise RuntimeError(
            "select_one: no interactive terminal — pass an explicit choice instead"
        )
    # Lazy import: questionary (and its prompt_toolkit backend) is heavy; keep
    # it off the `crm --version` fast path (_helpers is imported by cli.py).
    # questionary.select renders inline (↑/↓ + Enter confirms, Esc cancels) —
    # no alternate-screen modal — and .ask() returns None on cancel.
    import questionary
    choices = [questionary.Choice(title=label, value=value) for value, label in items]
    return questionary.select(title, choices=choices, default=default).ask()
