"""Offline static validation of a solution package (#141).

`validate_solution(zip_path, backend=None)` reads a Dynamics solution .zip and
reports every discoverable pre-import problem in one pass: missing/unparseable
package files, RootComponents<->customizations parity, unresolved
$webresource: ribbon references, and undeclared global option-set bindings.
With a backend (the --against-org path) it also reports formid/savedqueryid
GUID collisions with the target org. Mirrors the zip/XML handling proven in
solution.py::_sniff_solution_managed.
"""
from __future__ import annotations

import re
import xml.etree.ElementTree as ET
import zipfile
from dataclasses import asdict, dataclass
from pathlib import Path
from typing import Any

from crm.core.solution import SOLUTION_COMPONENT_TYPES as _CT
from crm.utils.d365_backend import D365Backend, D365Error, as_dict

_REQUIRED_MEMBERS = ("solution.xml", "customizations.xml", "[Content_Types].xml")
# Cap decompression so a zip-bomb manifest can't OOM the validator (see the same
# guard on solution._sniff_solution_managed).
_MAX_XML_BYTES = 64 * 1024 * 1024

# customizations.xml container node -> componenttype int (single source of truth
# is SOLUTION_COMPONENT_TYPES). Each container wraps one entry per component.
NODE_COMPONENT_TYPE: dict[str, int] = {
    "Entities": _CT["entity"],
    "optionsets": _CT["optionset"],
    "Roles": _CT["role"],
    "Workflows": _CT["workflow"],
    "WebResources": _CT["webresource"],
    "InteractionCentricDashboards": _CT["systemform"],  # type 60
}


@dataclass(frozen=True)
class Finding:
    severity: str  # "error" | "warning"
    check: str     # "package" | "root-parity" | "webresource-ref" | "optionset-binding" | "guid-collision"
    message: str
    component: str | None = None
    location: str | None = None


def _norm(name: str | None) -> str:
    """Canonicalise a component name/GUID for case- and brace-insensitive matching."""
    return (name or "").strip().strip("{}").lower()


def _load(
    zip_path: str | Path,
) -> tuple[ET.Element | None, ET.Element | None, list[Finding]]:
    """Open the zip, verify required members, parse the two XML manifests.

    Returns (solution_root, customizations_root, findings). On any fatal problem
    (bad zip, missing member, oversized/unparseable manifest) the roots are None
    and `findings` holds error-severity package findings. Raises D365Error only
    on an OS-level read failure (the file exists per click.Path but is unreadable).
    """
    p = Path(zip_path)
    findings: list[Finding] = []
    try:
        with zipfile.ZipFile(p) as zf:
            names = set(zf.namelist())
            missing = [m for m in _REQUIRED_MEMBERS if m not in names]
            if missing:
                return None, None, [
                    Finding("error", "package",
                            f"required member {m!r} missing from the solution zip",
                            component=m)
                    for m in missing
                ]
            for m in ("solution.xml", "customizations.xml"):
                if zf.getinfo(m).file_size > _MAX_XML_BYTES:
                    findings.append(Finding(
                        "error", "package",
                        f"{m} is too large to parse "
                        f"({zf.getinfo(m).file_size} bytes)", location=m))
            if findings:
                return None, None, findings
            sol_raw = zf.read("solution.xml")
            cust_raw = zf.read("customizations.xml")
    except zipfile.BadZipFile:
        return None, None, [Finding("error", "package",
                                    f"{p} is not a valid zip file", location=str(p))]
    except OSError as exc:
        raise D365Error(f"Could not read solution file {p}: {exc}") from exc

    sol_root: ET.Element | None = None
    cust_root: ET.Element | None = None
    try:
        sol_root = ET.fromstring(sol_raw)
    except ET.ParseError as exc:
        findings.append(Finding("error", "package",
                                f"solution.xml is not well-formed: {exc}",
                                location="solution.xml"))
    try:
        cust_root = ET.fromstring(cust_raw)
    except ET.ParseError as exc:
        findings.append(Finding("error", "package",
                                f"customizations.xml is not well-formed: {exc}",
                                location="customizations.xml"))
    return sol_root, cust_root, findings


def validate_solution(
    zip_path: str | Path,
    *,
    backend: D365Backend | None = None,
) -> dict[str, Any]:
    """Statically validate a solution package; return a report envelope.

    {"valid": bool, "findings": [Finding-as-dict, ...], "checks_run": [str, ...]}.
    `valid` is False iff any finding has severity "error". When `backend` is given
    (the --against-org path), also runs online collision/existence checks.
    """
    sol_root, cust_root, findings = _load(zip_path)
    checks_run = ["package"]
    if sol_root is not None and cust_root is not None:
        # Checkers added in later tasks are wired in here.
        pass
    valid = not any(f.severity == "error" for f in findings)
    return {
        "valid": valid,
        "findings": [asdict(f) for f in findings],
        "checks_run": checks_run,
    }
