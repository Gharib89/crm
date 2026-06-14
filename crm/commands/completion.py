"""`crm completion` — print/install shell completion (thin wrapper over Click).

Completion is Click's built-in mechanism (`_CRM_COMPLETE=<shell>_source crm`); this
group just makes it discoverable, caches the source script to a file, and records a
marker so `crm self-update` can refresh it. We never recommend the inline
`eval "$(_CRM_COMPLETE=... crm)"` form — that spawns Python on every shell start.
"""
# pyright: basic
from __future__ import annotations

from pathlib import Path

import click

from crm import __version__
from crm.cli import CLIContext, pass_ctx
from crm.commands import completion_registry as reg

_SHELL_OPT = click.option(
    "--shell", "shell", type=click.Choice(reg.SUPPORTED_SHELLS), default=None,
    help="Target shell. Defaults to autodetecting $SHELL.",
)


def _resolve_shell(shell: str | None) -> str:
    """The chosen or autodetected shell. Raise a UsageError (exit 2 + standard
    envelope, like the `--shell` Choice) when $SHELL can't be mapped — an
    undetectable shell is the same class of input error as an invalid one."""
    resolved = shell or reg.detect_shell()
    if resolved is None:
        raise click.UsageError(
            "could not autodetect shell from $SHELL; pass --shell "
            f"({'|'.join(reg.SUPPORTED_SHELLS)})."
        )
    return resolved


@click.group("completion")
def completion_group():
    """Print or install shell completion for bash, zsh, fish, or PowerShell."""


@completion_group.command("show")
@_SHELL_OPT
@pass_ctx
def completion_show(ctx: CLIContext, shell: str | None):
    """Print the completion source script to stdout (does not write anything)."""
    resolved = _resolve_shell(shell)
    script = reg.generate_source(resolved)
    if ctx.json_mode:
        ctx.emit(True, data={"shell": resolved, "script": script,
                             "script_path": None, "rc_line": None, "installed": False})
    else:
        click.echo(script)


@completion_group.command("install")
@_SHELL_OPT
@click.option("--path", "path", type=click.Path(dir_okay=False), default=None,
              help="Where to write the script. Default: ${CRM_HOME}/completion/crm.<shell>.")
@pass_ctx
def completion_install(ctx: CLIContext, shell: str | None, path: str | None):
    """Cache the completion script under CRM_HOME and print the rc line to source it.

    Idempotent: re-running rewrites the same script + marker. Never edits a shell
    rc file — it only prints the one `source <path>` line for you to add.
    """
    resolved = _resolve_shell(shell)
    dest = Path(path).expanduser().resolve() if path else reg.default_script_path(resolved)
    try:
        reg.write_script(resolved, dest)
        reg.write_marker(resolved, str(dest), __version__)
    except OSError as exc:
        ctx.emit(False, error=f"Failed to install completion to {dest}: {exc}")
        return
    rc_line = reg.rc_line(resolved, dest)
    if ctx.json_mode:
        ctx.emit(
            True,
            data={"shell": resolved, "script_path": str(dest), "rc_line": rc_line, "installed": True},
        )
        return
    # Human mode: the one copy-paste-able rc line is the payload — no key/value dump.
    ctx.skin.status("completion installed", str(dest))
    if resolved == "powershell":
        ctx.skin.hint("Add this line to your PowerShell $PROFILE, then restart your shell:")
    else:
        ctx.skin.hint("Add this line to your shell rc, then restart your shell:")
    click.echo(f"  {rc_line}")
