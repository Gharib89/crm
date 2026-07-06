"""Confirm / secret-warning / interactive-select UX helpers."""
# pyright: basic
from __future__ import annotations
import os
import sys
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
    ctx: "CLIContext", thing: str, name: str, yes: bool, *,
    message: str | None = None, skip_on_dry_run: bool = True
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
    caller and click's bare ``Aborted!`` with no JSON is never shown. Returns
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
    # CliRunner drives human-path prompt tests with a non-TTY click.testing
    # stdin wrapper; treat that harness stream as prompt-capable so the
    # interactive decline path stays covered while real non-TTY callers still
    # fail fast.
    can_prompt = _stdin_is_tty() or type(sys.stdin).__module__ == "click.testing"
    if ctx.json_mode or not can_prompt:
        text = prompt.strip()
        if text.endswith(" Continue?"):
            text = text[:-10].rstrip()
        elif text.endswith("?"):
            text = text[:-1].rstrip()
        suffix = "" if text.endswith((".", "!", ":")) else "."
        ctx.emit(
            False,
            error=(f"{text}{suffix} Pass --yes to continue "
                   "(no interactive prompt under --json or a non-TTY)."),
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


def prompt_secret(prompt: str) -> str | None:
    """Prompt for a secret on a TTY, echoing ``*`` per keystroke; return the
    entered value or None (empty entry or Esc/Ctrl-C cancel).

    Uses ``questionary.password()`` rather than a fully-hidden prompt so new
    users get visual feedback that their typing registered. Deliberate tradeoff
    (#655): asterisks reveal the secret's length — the industry norm (ssh/gh/aws)
    is no echo at all — chosen here for feedback, not as a security control.

    This only runs on a TTY: like `select_one`, it refuses non-TTY stdin itself
    with a clear ``RuntimeError`` so a caller that forgets to gate fails loudly
    instead of hitting a raw prompt_toolkit error. Off a TTY the CLI does not
    prompt at all — the secret must come from ``--password`` / ``--client-secret``
    or a stored secret — so there is no hidden-prompt fallback here. questionary
    is imported lazily (like `select_one`) to stay off the `crm --version` fast
    path."""
    if not _stdin_is_tty():
        raise RuntimeError(
            "prompt_secret: no interactive terminal — pass the secret explicitly instead"
        )
    import questionary
    return questionary.password(prompt).ask() or None
