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
_WEBRESOURCE_REF = re.compile(r"\$webresource:([^\"'\s)<>]+)")

# (customizations element tag, id-field candidates, org entity set, org id attr)
_COLLISION_SOURCES: tuple[tuple[str, tuple[str, ...], str, str], ...] = (
    ("systemform", ("formid", "FormId", "id"), "systemforms", "formid"),
    ("savedquery", ("savedqueryid", "SavedQueryId", "id"), "savedqueries", "savedqueryid"),
)

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


def _entry_name(entry: ET.Element) -> str | None:
    """Best-effort identifying name of a customizations component entry."""
    for attr in ("schemaName", "Name", "name"):
        v = entry.get(attr)
        if v:
            return v
    for child in ("Name", "UniqueName", "FormId", "LocalizedName"):
        t = entry.findtext(child)
        if t:
            return t
    return None


def _customization_components(cust_root: ET.Element) -> set[tuple[int, str]]:
    """(componenttype, normalised name) for every component declared in a known node."""
    found: set[tuple[int, str]] = set()
    for node_tag, ctype in NODE_COMPONENT_TYPE.items():
        for container in cust_root.iter(node_tag):
            for entry in list(container):
                name = _entry_name(entry)
                if name:
                    found.add((ctype, _norm(name)))
    return found


def _root_components(sol_root: ET.Element) -> set[tuple[int, str]]:
    """(type, normalised schemaName-or-id) for every <RootComponent> in solution.xml."""
    found: set[tuple[int, str]] = set()
    for rc in sol_root.iter("RootComponent"):
        type_attr = rc.get("type")
        if type_attr is None or not type_attr.strip().lstrip("-").isdigit():
            continue
        name = rc.get("schemaName") or rc.get("id")
        if name:
            found.add((int(type_attr), _norm(name)))
    return found


def _check_root_parity(sol_root: ET.Element, cust_root: ET.Element) -> list[Finding]:
    cust = _customization_components(cust_root)
    root = _root_components(sol_root)
    findings: list[Finding] = []
    for ctype, name in sorted(cust - root):
        findings.append(Finding(
            "error", "root-parity",
            f"component {name!r} of type {ctype} is present in customizations.xml "
            f"but not declared in <RootComponents>",
            component=name, location="customizations.xml"))
    for ctype, name in sorted(root - cust):
        findings.append(Finding(
            "error", "root-parity",
            f"RootComponent {name!r} of type {ctype} is declared in solution.xml "
            f"but has no definition in customizations.xml",
            component=name, location="solution.xml/<RootComponents>"))
    return findings


def _webresource_exists_in_org(backend: D365Backend, name: str) -> bool:
    lit = name.replace("'", "''")
    resp = as_dict(backend.get(
        "webresourceset",
        params={"$select": "webresourceid", "$filter": f"name eq '{lit}'", "$top": "1"}))
    return bool(resp.get("value"))


def _check_webresource_refs(
    cust_root: ET.Element, backend: D365Backend | None
) -> list[Finding]:
    refs: set[str] = set()
    for ribbon in cust_root.iter("RibbonDiffXml"):
        refs.update(_WEBRESOURCE_REF.findall(ET.tostring(ribbon, encoding="unicode")))
    if not refs:
        return []
    pkg: set[str] = set()
    for container in cust_root.iter("WebResources"):
        for entry in list(container):
            n = _entry_name(entry)
            if n:
                pkg.add(_norm(n))
    findings: list[Finding] = []
    for ref in sorted(refs):
        if _norm(ref) in pkg:
            continue
        if backend is not None and _webresource_exists_in_org(backend, ref):
            continue
        where = "package or org" if backend is not None else "package"
        findings.append(Finding(
            "error", "webresource-ref",
            f"ribbon references web resource {ref!r} which is not present in the {where}",
            component=ref, location="customizations.xml/RibbonDiffXml"))
    return findings


def _optionset_exists_in_org(backend: D365Backend, name: str) -> bool:
    lit = name.replace("'", "''")
    resp = as_dict(backend.get(
        "GlobalOptionSetDefinitions",
        params={"$select": "Name", "$filter": f"Name eq '{lit}'", "$top": "1"}))
    return bool(resp.get("value"))


def _check_optionset_bindings(
    cust_root: ET.Element, backend: D365Backend | None
) -> list[Finding]:
    declared: set[str] = set()
    for container in cust_root.iter("optionsets"):
        for entry in list(container):
            n = _entry_name(entry)
            if n:
                declared.add(_norm(n))
    findings: list[Finding] = []
    seen: set[str] = set()
    for os_el in cust_root.iter("OptionSet"):
        flag = (os_el.findtext("IsGlobal") or os_el.get("IsGlobal") or "").strip().lower()
        if flag not in ("1", "true"):
            continue
        name = os_el.get("Name") or os_el.findtext("Name")
        if not name or _norm(name) in seen:
            continue
        seen.add(_norm(name))
        if _norm(name) in declared:
            continue
        if backend is not None and _optionset_exists_in_org(backend, name):
            continue
        where = "package or org" if backend is not None else "package"
        findings.append(Finding(
            "error", "optionset-binding",
            f"attribute binds global option set {name!r} which is not declared in the {where}",
            component=name, location="customizations.xml"))
    return findings


def _extract_guid(el: ET.Element, candidates: tuple[str, ...]) -> str | None:
    for c in candidates:
        v = el.get(c) or el.findtext(c)
        if v:
            return v
    return None


def _check_org_collisions(cust_root: ET.Element, backend: D365Backend) -> list[Finding]:
    """Report formid/savedqueryid GUIDs that already exist in the target org.

    A cloned form/view that kept its source GUID collides on import (the root
    cause of the "label ... already exists" class). OData v4 GUID filters take
    the bare GUID (no quotes), matching solution.solution_components.
    """
    findings: list[Finding] = []
    for elem_tag, id_fields, entity_set, id_attr in _COLLISION_SOURCES:
        guids: set[str] = set()
        for el in cust_root.iter(elem_tag):
            gid = _extract_guid(el, id_fields)
            if gid:
                guids.add(_norm(gid))
        for gid in sorted(guids):
            resp = as_dict(backend.get(
                entity_set,
                params={"$select": id_attr, "$filter": f"{id_attr} eq {gid}", "$top": "1"}))
            if resp.get("value"):
                findings.append(Finding(
                    "error", "guid-collision",
                    f"{elem_tag} id {gid} already exists in the target org "
                    f"(import will fail with a duplicate-id/label error)",
                    component=gid, location="customizations.xml"))
    return findings


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
        checks_run.append("root-parity")
        findings += _check_root_parity(sol_root, cust_root)
        checks_run.append("webresource-ref")
        findings += _check_webresource_refs(cust_root, backend)
        checks_run.append("optionset-binding")
        findings += _check_optionset_bindings(cust_root, backend)
        if backend is not None:
            checks_run.append("guid-collision")
            findings += _check_org_collisions(cust_root, backend)
    valid = not any(f.severity == "error" for f in findings)
    return {
        "valid": valid,
        "findings": [asdict(f) for f in findings],
        "checks_run": checks_run,
    }
