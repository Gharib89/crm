"""Shell-completion source generation + install marker: ``${CRM_HOME}/completion.json``.

Completion itself is Click's built-in mechanism (``_CRM_COMPLETE=<shell>_source
crm``); this module is a thin layer that (a) renders that source script in-process
and (b) records where ``crm completion install`` wrote it so ``crm self-update``
can regenerate it after an upgrade. Lives in the command layer (not crm/core)
because it owns config-style state writes — same split as ``skill_registry`` (the
sibling it mirrors for marker I/O and the self-update refresh hook).

The marker is a single object (one CLI-managed completion script)::

    {"shell": "zsh", "script_path": "/abs/path/crm.zsh", "installed_version": "3.9.2"}

Read tolerantly (missing/corrupt → ``None``) and written atomically (unique temp
file + os.replace) so a reader never sees a torn file — identical discipline to
``skill_registry``.
"""
# pyright: basic
from __future__ import annotations

import json
import os
import subprocess
import tempfile
from pathlib import Path
from typing import Any

from click.shell_completion import CompletionItem, ShellComplete, split_arg_string

# Click 8.x ships zsh/bash/fish completion classes; "powershell" is added by this
# module via add_completion_class (see PowerShellComplete + the eager registration
# in crm/cli.py). These are the shells we expose.
SUPPORTED_SHELLS = ("zsh", "bash", "fish", "powershell")

# The env var Click derives from prog_name "crm" (``_<PROG>_COMPLETE``). The
# generated script and the ``_CRM_COMPLETE=<shell>_source crm`` invocation both
# key off it, so the cached script matches what a manual setup would produce.
# Pinning ``prog_name="crm"`` at the entry point (crm/cli.py:main) is what keeps
# this stable on Windows, where the binary basename ``crm.exe`` would otherwise
# make Click look for ``_CRM_EXE_COMPLETE`` and break completion.
_COMPLETE_VAR = "_CRM_COMPLETE"

# Shells whose cached-script file extension differs from the shell name. PowerShell
# dot-sources a ``.ps1``; the Unix three use ``crm.<shell>`` (crm.zsh, …).
_SCRIPT_EXT = {"powershell": "ps1"}

# PowerShell completion source. Click ships no PowerShell class, so we register a
# custom one via ``add_completion_class``. The native completer hands the
# scriptblock a raw command-line string + the partial word (unlike the Unix shells'
# pre-split word list + cursor index), so the shim mirrors fish: pass the full line
# as COMP_WORDS and the partial as COMP_CWORD. ``%(...)s`` are filled by
# ShellComplete.source_vars (prog_name, complete_var). ``if/else`` (not the ``?:``
# ternary) keeps the script valid on both Windows PowerShell 5.1 and PowerShell 7+.
_SOURCE_POWERSHELL = '''\
Register-ArgumentCompleter -Native -CommandName %(prog_name)s -ScriptBlock {
    param($wordToComplete, $commandAst, $cursorPosition)
    $env:%(complete_var)s = "powershell_complete"
    $env:COMP_WORDS = $commandAst.ToString()
    $env:COMP_CWORD = $wordToComplete
    %(prog_name)s | ForEach-Object {
        $type, $value, $help = ($_ -split "`t", 3)
        # Only 'plain' items become results; 'file'/'dir' are skipped so PowerShell
        # does native path completion. A positive guard (not `return`) keeps a
        # non-plain item from affecting the items emitted after it.
        if ($type -eq 'plain') {
            if ($help) { $toolTip = $help } else { $toolTip = $value }
            [System.Management.Automation.CompletionResult]::new(
                $value, $value, 'ParameterValue', $toolTip)
        }
    }
    Remove-Item Env:%(complete_var)s, Env:COMP_WORDS, Env:COMP_CWORD
}
'''


class PowerShellComplete(ShellComplete):
    """Click completion for PowerShell (Windows PowerShell 5.1 and PowerShell 7+).

    Registered via ``add_completion_class`` on an always-imported path (crm/cli.py)
    so the completion hot-path has it — the command modules are lazy-loaded, so a
    completion request never imports this module on its own.
    """

    name = "powershell"
    source_template = _SOURCE_POWERSHELL

    def get_completion_args(self) -> tuple[list[str], str]:
        cwords = split_arg_string(os.environ["COMP_WORDS"])
        incomplete = os.environ.get("COMP_CWORD", "")
        args = cwords[1:]
        # PowerShell repeats the partial word in both COMP_WORDS and COMP_CWORD;
        # drop it from the completed args (mirrors fish).
        if incomplete and args and args[-1] == incomplete:
            args.pop()
        return args, incomplete

    def format_completion(self, item: CompletionItem) -> str:
        # Tab-separated triple the scriptblock splits on `` `t ``: type, value, help.
        return f"{item.type}\t{item.value}\t{item.help or ''}"


def _crm_home() -> Path:
    root = Path(os.environ.get("CRM_HOME", str(Path.home() / ".crm"))).expanduser()
    root.mkdir(parents=True, exist_ok=True)
    return root


def marker_path() -> Path:
    return _crm_home() / "completion.json"


def default_script_path(shell: str) -> Path:
    """The cached-script location under ``CRM_HOME`` for a given shell."""
    ext = _SCRIPT_EXT.get(shell, shell)
    return _crm_home() / "completion" / f"crm.{ext}"


def rc_line(shell: str, dest: Path | str) -> str:
    """The line a user adds to their shell startup file to load the script.

    PowerShell dot-sources from ``$PROFILE`` (``. '<path>'``); the Unix shells
    ``source`` it. The PowerShell path is single-quoted so spaces/special chars in
    the destination (common on Windows, e.g. a custom ``--path`` under
    ``C:\\Program Files``) don't break the line; embedded single quotes are doubled
    per PowerShell's single-quote literal rules.
    """
    if shell == "powershell":
        quoted = str(dest).replace("'", "''")
        return f". '{quoted}'"
    return f"source {dest}"


def detect_shell() -> str | None:
    """Best-effort shell autodetect from ``$SHELL``; ``None`` if not one we support."""
    name = Path(os.environ.get("SHELL", "")).name
    return name if name in SUPPORTED_SHELLS else None


def read_marker() -> dict[str, Any] | None:
    """The recorded completion install, or ``None`` if missing/corrupt.

    Tolerant only of *missing* and *corrupt* files (mirrors ``skill_registry``);
    a genuine I/O fault propagates so the caller surfaces a clean error rather
    than silently treating it as "not installed".
    """
    try:
        raw = json.loads(marker_path().read_text(encoding="utf-8"))
    except FileNotFoundError:
        return None
    except (json.JSONDecodeError, ValueError, UnicodeDecodeError):
        return None
    return raw if isinstance(raw, dict) else None


def write_marker(shell: str, script_path: str, installed_version: str) -> None:
    """Atomically record the installed completion script (unique temp + os.replace)."""
    path = marker_path()
    payload = {"shell": shell, "script_path": script_path,
               "installed_version": installed_version}
    fd, tmp_name = tempfile.mkstemp(dir=str(path.parent), prefix=path.name + ".", suffix=".tmp")
    tmp = Path(tmp_name)
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as fh:
            fh.write(json.dumps(payload, indent=2))
        os.replace(tmp, path)
    except BaseException:
        tmp.unlink(missing_ok=True)
        raise


def remove_marker() -> None:
    """Drop the completion marker, if present."""
    marker_path().unlink(missing_ok=True)


def write_script(shell: str, dest: Path) -> str:
    """Render the source script for ``shell`` and write it to ``dest`` (mkdir -p)."""
    dest.parent.mkdir(parents=True, exist_ok=True)
    src = generate_source(shell)
    dest.write_text(src, encoding="utf-8")
    return src


def generate_source(shell: str) -> str:
    """Render the completion *source* script for ``shell`` in-process.

    Equivalent to ``_CRM_COMPLETE=<shell>_source crm`` but without a subprocess,
    so the ``completion show``/``install`` commands (running the current code) emit
    the current template. ``self-update`` of a frozen build uses
    :func:`generate_via_binary` instead (the running process is the *old* code).
    """
    from click.shell_completion import get_completion_class

    from crm.cli import cli

    comp_cls = get_completion_class(shell)
    if comp_cls is None:
        raise ValueError(f"unsupported shell {shell!r}; choose from {SUPPORTED_SHELLS}")
    comp = comp_cls(cli, {}, "crm", _COMPLETE_VAR)
    return comp.source()


def refresh_completion(target_version: str, generate_fn: Any) -> dict[str, Any] | None:
    """Regenerate the recorded completion script to ``target_version``.

    Mirrors ``skill_registry.refresh_skills`` for the single completion marker:
    no marker → ``None`` (nothing to do); already at ``target_version`` →
    ``skipped`` (no rewrite); the cached script gone (user removed it) → the marker
    is ``pruned``; otherwise the script is re-rendered via ``generate_fn(shell)``
    and rewritten. ``generate_fn`` lets the caller pick the renderer — in-process
    (pip) or via the new binary (frozen). A render failure propagates to the
    caller, which wraps it as an ``error`` status (never failing the update).
    """
    marker = read_marker()
    if marker is None:
        return None
    shell = marker.get("shell")
    script_path = marker.get("script_path")
    from_v = marker.get("installed_version")
    if not (isinstance(shell, str) and shell in SUPPORTED_SHELLS and isinstance(script_path, str)):
        return {"shell": shell, "script_path": script_path, "from_version": from_v,
                "to_version": None, "status": "error", "error": "malformed completion marker"}
    if from_v == target_version:
        return {"shell": shell, "script_path": script_path, "from_version": from_v,
                "to_version": from_v, "status": "skipped"}
    dest = Path(script_path)
    if not dest.exists():
        # The user removed the cached script — respect the opt-out, drop the marker.
        remove_marker()
        return {"shell": shell, "script_path": script_path, "from_version": from_v,
                "to_version": None, "status": "pruned"}
    src = generate_fn(shell)
    dest.parent.mkdir(parents=True, exist_ok=True)
    dest.write_text(src, encoding="utf-8")
    write_marker(shell, script_path, target_version)
    return {"shell": shell, "script_path": script_path, "from_version": from_v,
            "to_version": target_version, "status": "refreshed"}


def generate_via_binary(shell: str, binary: str) -> str:
    """Render the source script by invoking ``binary`` with ``_CRM_COMPLETE`` set.

    Used by the frozen ``self-update`` refresh: after the bundle swap the running
    process is the OLD code (and on posix the old package dir is gone), but the
    binary at ``binary`` is the freshly-swapped NEW build, so shelling out to it
    yields the new template. Raises ``CalledProcessError`` on a non-zero exit.
    """
    env = {**os.environ, _COMPLETE_VAR: f"{shell}_source"}
    # Bounded: rendering the script is near-instant, but a wedged binary must not
    # hang `self-update` forever. A timeout raises TimeoutExpired, which the
    # never-raising refresh wrapper turns into an error status.
    out = subprocess.run(
        [binary], env=env, capture_output=True, text=True, check=True, timeout=30,
    )
    # A non-completion invocation (e.g. argv[0] not named `crm`, so Click derives a
    # different complete-var and never enters completion mode) exits 0 with empty
    # stdout. Treat that as a failure so the never-raising refresh records an error
    # and keeps the existing script, rather than overwriting it with a blank file.
    if not out.stdout.strip():
        raise RuntimeError(
            f"{binary!r} produced no completion output for {shell!r} "
            f"(stderr: {out.stderr.strip() or 'none'})"
        )
    return out.stdout
