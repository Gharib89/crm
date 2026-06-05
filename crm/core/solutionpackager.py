"""Offline bridge to the CoreTools SolutionPackager.exe (extract / pack).

These are OFFLINE local-file transforms over an already-exported solution zip:
they shell out to SolutionPackager.exe and never touch the Web API, a backend,
a connection, or a profile. `git diff` on the extracted tree IS the diff — this
module deliberately does not reimplement packing or add an XML-diff engine.
"""

from __future__ import annotations

import os
import shutil
import subprocess
from pathlib import Path
from typing import Any

from crm.utils.d365_backend import D365Error

# We never bundle or auto-download SolutionPackager; point the user at the NuGet.
_NUGET_HINT = (
    "Install it from the Microsoft.CrmSdk.CoreTools NuGet package, then pass "
    "--solutionpackager-path or set CRM_SOLUTIONPACKAGER."
)

# SolutionPackager is chatty (one line per component); keep only the tail so the
# emitted envelope stays small but still shows the final summary / any error.
_TAIL_LINES = 20


def _tail(text: str, lines: int = _TAIL_LINES) -> str:
    """Last `lines` lines of `text`, trailing newline stripped."""
    return "\n".join(text.splitlines()[-lines:])


# Canonical SolutionPackager /packagetype values, keyed by lower-case input.
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


def _resolve_solution_packager(path_override: str | None) -> str:
    """Resolve the SolutionPackager executable: flag → CRM_SOLUTIONPACKAGER env
    → PATH lookup. A supplied (flag/env) path that is not a file, or a total
    absence on PATH, raises a D365Error naming the CoreTools NuGet."""
    candidate = path_override or os.environ.get("CRM_SOLUTIONPACKAGER")
    if candidate:
        # Expand ~ and $VARS so a profile/.env value like "~/CoreTools/SolutionPackager.exe"
        # resolves (the shell only expands these for values typed at the prompt).
        candidate = os.path.expanduser(os.path.expandvars(candidate))
        if not Path(candidate).is_file():
            raise D365Error(f"SolutionPackager not found at {candidate!r}. {_NUGET_HINT}")
        return candidate
    found = shutil.which("SolutionPackager.exe") or shutil.which("SolutionPackager")
    if found:
        return found
    raise D365Error(f"SolutionPackager executable not found on PATH. {_NUGET_HINT}")


def _run_solution_packager(
    action: str,
    *,
    zipfile: str | Path,
    folder: str | Path,
    package_type: str,
    solutionpackager_path: str | None,
    timeout: int | None,
) -> dict[str, Any]:
    """Shell out to SolutionPackager for `action` (Extract|Pack) and return the
    `{action, exit_code, folder, zipfile, stdout_tail}` envelope."""
    if timeout is not None and timeout <= 0:
        raise D365Error(
            f"timeout must be a positive number of seconds; got {timeout}."
        )
    pkg = _normalize_package_type(package_type)
    exe = _resolve_solution_packager(solutionpackager_path)
    argv = [
        exe,
        f"/action:{action}",
        f"/zipfile:{zipfile}",
        f"/folder:{folder}",
        f"/packagetype:{pkg}",
    ]
    try:
        proc = subprocess.run(
            argv, check=False, capture_output=True, text=True, timeout=timeout,
        )
    except subprocess.TimeoutExpired as exc:
        raise D365Error(
            f"SolutionPackager {action} timed out after {timeout}s; "
            "raise --timeout or check the solution size."
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
    solutionpackager_path: str | None = None,
    timeout: int | None = None,
) -> dict[str, Any]:
    """Extract an exported solution zip into a source-controllable folder tree."""
    return _run_solution_packager(
        "Extract",
        zipfile=zipfile, folder=folder, package_type=package_type,
        solutionpackager_path=solutionpackager_path, timeout=timeout,
    )


def pack_solution(
    *,
    zipfile: str | Path,
    folder: str | Path,
    package_type: str = "Unmanaged",
    solutionpackager_path: str | None = None,
    timeout: int | None = None,
) -> dict[str, Any]:
    """Pack a source folder tree back into an importable solution zip."""
    return _run_solution_packager(
        "Pack",
        zipfile=zipfile, folder=folder, package_type=package_type,
        solutionpackager_path=solutionpackager_path, timeout=timeout,
    )
