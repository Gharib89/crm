"""Offline bridge to the Power Platform CLI `pac solution unpack` / `pac solution pack`.

These are OFFLINE local-file transforms over an already-exported solution zip:
they shell out to `pac` and never touch the Web API, a backend, a connection, or
a profile. `git diff` on the extracted tree IS the diff — this module
deliberately does not reimplement packing or add an XML-diff engine.

`pac` (cross-platform .NET tool) supersedes the legacy, Windows-only
SolutionPackager.exe, which Microsoft no longer recommends; `pac solution
unpack`/`pack` are the documented replacements and run on Linux/macOS/Windows.
"""

from __future__ import annotations

import os
import shutil
import subprocess
from pathlib import Path
from typing import Any

from crm.utils.d365_backend import D365Error

# We never bundle or auto-download pac; point the user at the install path.
_PAC_HINT = (
    "Install the Power Platform CLI (pac) — e.g. `dotnet tool install --global "
    "Microsoft.PowerApps.CLI.Tool` (requires .NET SDK) — then pass --pac-path "
    "or set CRM_PAC."
)

# pac is chatty (one line per component); keep only the tail so the emitted
# envelope stays small but still shows the final summary / any error.
_TAIL_LINES = 20

# Map the conceptual action (kept stable in the envelope + CLI verbs) to the pac
# subcommand: `extract` → `pac solution unpack`, `pack` → `pac solution pack`.
_SUBCOMMAND = {"Extract": "unpack", "Pack": "pack"}


def pac_subcommand(action: str | None) -> str:
    """The real `pac solution` subcommand for an envelope `action` (Extract →
    unpack, Pack → pack); an unknown action is returned unchanged. Lets callers
    show the runnable command without duplicating the mapping."""
    return _SUBCOMMAND.get(action or "", action or "")


def _tail(text: str, lines: int = _TAIL_LINES) -> str:
    """Last `lines` lines of `text`, trailing newline stripped."""
    return "\n".join(text.splitlines()[-lines:])


# Canonical pac --packagetype values, keyed by lower-case input.
_PACKAGE_TYPES = {"unmanaged": "Unmanaged", "managed": "Managed", "both": "Both"}


def _normalize_package_type(value: str) -> str:
    """Normalise a package type to its canonical casing; raise on an unknown one."""
    try:
        return _PACKAGE_TYPES[value.strip().lower()]
    except KeyError:
        known = ", ".join(_PACKAGE_TYPES.values())
        raise D365Error(
            f"unknown package type {value!r}; choose one of: {known}."
        ) from None


def _resolve_pac(path_override: str | None) -> str:
    """Resolve the `pac` executable: flag → CRM_PAC env → PATH lookup. A supplied
    (flag/env) path that is not a file, or a total absence on PATH, raises a
    D365Error naming the Power Platform CLI install path.

    `CRM_SOLUTIONPACKAGER` is honored as a deprecated back-compat alias for
    `CRM_PAC` (point it at `pac`, not SolutionPackager.exe) so callers that set
    the old env var keep resolving an executable after the #500 migration."""
    candidate = (
        path_override
        or os.environ.get("CRM_PAC")
        or os.environ.get("CRM_SOLUTIONPACKAGER")
    )
    if candidate:
        # Expand ~ and $VARS so a flag/env value like "~/.dotnet/tools/pac"
        # resolves (the shell only expands these for values typed at the prompt).
        candidate = os.path.expanduser(os.path.expandvars(candidate))
        if not Path(candidate).is_file():
            raise D365Error(f"pac not found at {candidate!r}. {_PAC_HINT}")
        return candidate
    found = shutil.which("pac")
    if found:
        return found
    raise D365Error(f"pac executable not found on PATH. {_PAC_HINT}")


def _run_pac(
    action: str,
    *,
    zipfile: str | Path,
    folder: str | Path,
    package_type: str,
    pac_path: str | None,
    timeout: int | None,
) -> dict[str, Any]:
    """Shell out to `pac solution <unpack|pack>` for `action` (Extract|Pack) and
    return the `{action, exit_code, folder, zipfile, stdout_tail}` envelope."""
    if timeout is not None and timeout <= 0:
        raise D365Error(
            f"timeout must be a positive number of seconds; got {timeout}."
        )
    pkg = _normalize_package_type(package_type)
    exe = _resolve_pac(pac_path)
    argv = [
        exe,
        "solution",
        _SUBCOMMAND[action],
        "--zipfile", str(zipfile),
        "--folder", str(folder),
        "--packagetype", pkg,
    ]
    try:
        proc = subprocess.run(
            argv, check=False, capture_output=True, text=True, timeout=timeout,
        )
    except subprocess.TimeoutExpired as exc:
        raise D365Error(
            f"pac solution {_SUBCOMMAND[action]} timed out after {timeout}s; "
            "increase --timeout or check the solution size."
        ) from exc
    except OSError as exc:
        # The path exists (is_file passed) but could not be executed — e.g. it is
        # not the real binary (wrong format) or lacks execute permission.
        raise D365Error(
            f"Could not run pac at {exe!r}: {exc}. {_PAC_HINT}"
        ) from exc
    return {
        "action": action,
        "exit_code": proc.returncode,
        "folder": str(folder),
        "zipfile": str(zipfile),
        "stdout_tail": _tail(proc.stdout),
    }


def extract_solution(
    *,
    zipfile: str | Path,
    folder: str | Path,
    package_type: str = "Unmanaged",
    pac_path: str | None = None,
    timeout: int | None = None,
) -> dict[str, Any]:
    """Extract an exported solution zip into a source-controllable folder tree."""
    return _run_pac(
        "Extract",
        zipfile=zipfile, folder=folder, package_type=package_type,
        pac_path=pac_path, timeout=timeout,
    )


def pack_solution(
    *,
    zipfile: str | Path,
    folder: str | Path,
    package_type: str = "Unmanaged",
    pac_path: str | None = None,
    timeout: int | None = None,
) -> dict[str, Any]:
    """Pack a source folder tree back into an importable solution zip."""
    return _run_pac(
        "Pack",
        zipfile=zipfile, folder=folder, package_type=package_type,
        pac_path=pac_path, timeout=timeout,
    )
