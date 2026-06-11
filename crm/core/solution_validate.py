"""Offline static validation of a solution package (#141).

`validate_solution(zip_path, backend=None)` reads a Dynamics solution .zip and
reports every discoverable pre-import problem in one pass: missing/unparseable
package files, RootComponents<->customizations parity, unresolved
$webresource: ribbon references, and undeclared global option-set bindings.
With a backend (the --against-org path) it also reports formid/savedqueryid
GUID collisions with the target org, plus BPF process-stage GUID collisions read
from the Workflows/*.xaml members. Mirrors the zip/XML handling proven in
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

# BPF process-stage GUIDs live in the Workflows/*.xaml members, NOT in the two
# top-level manifests. A cloned solution whose XAML kept its source StageId /
# NextStageId values passes the form/view collision check yet fails import with
# "Cannot insert duplicate key" on CreateProcessStage (#163). Both keys denote a
# `processstage` record (EntitySetName=processstages, PK=processstageid). Scoped
# regex over the XAML text: <x:String x:Key="StageId">{guid}</x:String> (and the
# NextStageId variant). The `[^>]*` after the x:Key attribute tolerates further
# attributes on the same element (e.g. xml:space="preserve" emitted before or
# after x:Key on a real export) before the closing `>`. Namespaced ElementTree
# key-attr matching is fiddly and the structure is fixed per the issue, so a
# regex is the simplest robust pull.
#
# DEFERRED — not probed, pending $metadata confirmation of the entity set:
#   * ProcessStepId (<x:String x:Key="ProcessStepId">...): there is NO standalone
#     `processstep` Web API entity (the MS Learn page 404s), so we cannot emit a
#     query against an unconfirmed set without risking a spurious HTTP error.
#   * LabelId="{guid}" on mcwo:StepLabel elements: localized-label rows, not a
#     clean queryable collision set. Neither is implemented here.
_XAML_STAGE_GUID = re.compile(
    r'x:Key="(?:Stage|NextStage)Id"[^>]*>\s*([^<]+?)\s*</', re.IGNORECASE)
_WORKFLOWS_MEMBER = re.compile(r"(?:^|/)Workflows/[^/]+\.xaml$", re.IGNORECASE)
# A captured value is probed only if it is a bare GUID (post-_norm, brace-stripped
# and lowercased) — keeps malformed/unexpected text out of the OData $filter.
_GUID_RE = re.compile(
    r"^[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}$")

# (customizations element tag, id-field candidates, org entity set, org id attr)
_COLLISION_SOURCES: tuple[tuple[str, tuple[str, ...], str, str], ...] = (
    ("systemform", ("formid", "FormId", "id"), "systemforms", "formid"),
    ("savedquery", ("savedqueryid", "SavedQueryId", "id"), "savedqueries", "savedqueryid"),
)

# customizations.xml container node -> componenttype int (single source of truth
# is SOLUTION_COMPONENT_TYPES). Each container wraps one entry per component.
# Restricted to the node types whose customizations entry is keyed the SAME way
# the matching <RootComponent> is (schemaName/name, or the form GUID) so parity
# matching is reliable. Roles/Workflows are deliberately excluded: their
# <RootComponent> is keyed by GUID `id` while their customizations entry exposes
# a Name, so including them would emit spurious both-direction parity findings on
# a real export. Re-add once that GUID-vs-Name keying is verified (see #141 plan).
NODE_COMPONENT_TYPE: dict[str, int] = {
    "Entities": _CT["entity"],
    "optionsets": _CT["optionset"],
    "WebResources": _CT["webresource"],
    "InteractionCentricDashboards": _CT["systemform"],  # type 60
}

# Component types we scan in customizations.xml. The reverse parity direction
# (a <RootComponent> with no backing definition) is checked ONLY for these
# types, so the many component types a real solution legitimately roots without
# a scanned customizations node (workflows, plug-in assemblies, SDK steps, …)
# never produce false "declared but no definition" findings.
_SCANNED_TYPES: frozenset[int] = frozenset(NODE_COMPONENT_TYPE.values())


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
        ctype = int(type_attr)
        if ctype not in _SCANNED_TYPES:
            continue  # only parity-check types we also scan in customizations.xml
        name = rc.get("schemaName") or rc.get("id")
        if name:
            found.add((ctype, _norm(name)))
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


def _force_get(backend: D365Backend, path: str, params: dict[str, str]) -> dict[str, Any]:
    """Read-only org probe: always a real GET, even under --dry-run.

    The org checks are idempotent reads whose accuracy depends on the live
    answer; a preview/empty dry-run response would surface false findings.
    Mirrors metadata.target_exists / plugin._force_read_rows.
    """
    return as_dict(backend.get(path, params=params))


def _webresource_exists_in_org(backend: D365Backend, name: str) -> bool:
    lit = name.replace("'", "''")
    resp = _force_get(
        backend, "webresourceset",
        {"$select": "webresourceid", "$filter": f"name eq '{lit}'", "$top": "1"})
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
    resp = _force_get(
        backend, "GlobalOptionSetDefinitions",
        {"$select": "Name", "$filter": f"Name eq '{lit}'", "$top": "1"})
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
            resp = _force_get(
                backend, entity_set,
                {"$select": id_attr, "$filter": f"{id_attr} eq {gid}", "$top": "1"})
            if resp.get("value"):
                findings.append(Finding(
                    "error", "guid-collision",
                    f"{elem_tag} id {gid} already exists in the target org "
                    f"(import will fail with a duplicate-id/label error)",
                    component=gid, location="customizations.xml"))
    return findings


def _decode_xaml(raw: bytes) -> str:
    """Decode a XAML member to text, honouring its byte-order mark.

    D365 process/workflow XAML is frequently UTF-16 (BOM-prefixed); decoding it
    as UTF-8 would yield NUL-laden text and silently defeat the StageId regex.
    A UTF-16/UTF-8 BOM picks the codec; otherwise fall back to UTF-8 (the XML
    default) with replacement so a truly undecodable member never raises.
    """
    if raw[:2] in (b"\xff\xfe", b"\xfe\xff"):
        return raw.decode("utf-16", "replace")
    if raw[:3] == b"\xef\xbb\xbf":
        return raw.decode("utf-8-sig", "replace")
    return raw.decode("utf-8", "replace")


def _read_workflow_xaml(zip_path: str | Path) -> tuple[list[str], list[Finding]]:
    """Read the text of every Workflows/*.xaml member from the solution zip.

    Returns (xaml_texts, findings). A member that can't be read — too large
    (the _MAX_XML_BYTES zip-bomb cap), encrypted (RuntimeError), an unsupported
    compression (NotImplementedError), a >4GiB member without zip64
    (LargeZipFile), or corrupt/unreadable bytes (BadZipFile on a bad CRC,
    OSError) — degrades to a 'package' Finding and the scan continues to the
    next member, instead of crashing the CLI (which only catches D365Error). A
    failure re-opening the zip itself
    (BadZipFile/OSError) likewise degrades to a 'package' finding rather than
    raising — _load already opened and vetted the zip, so this is a rare mid-run
    change, but the scan must never be silently skipped nor escape as an
    unexpected exception.
    """
    texts: list[str] = []
    findings: list[Finding] = []
    try:
        with zipfile.ZipFile(Path(zip_path)) as zf:
            members = [n for n in zf.namelist() if _WORKFLOWS_MEMBER.search(n)]
            for name in members:
                try:
                    if zf.getinfo(name).file_size > _MAX_XML_BYTES:
                        findings.append(Finding(
                            "error", "package",
                            f"{name} is too large to scan "
                            f"({zf.getinfo(name).file_size} bytes)", location=name))
                        continue
                    raw = zf.read(name)
                except (zipfile.BadZipFile, zipfile.LargeZipFile, OSError,
                        RuntimeError, NotImplementedError) as exc:
                    # A single corrupt member (bad CRC -> BadZipFile, oversized ->
                    # LargeZipFile, encrypted -> RuntimeError, unsupported
                    # compression -> NotImplementedError, OS read error -> OSError)
                    # degrades to a finding; the loop continues to the next member.
                    findings.append(Finding(
                        "error", "package",
                        f"{name} could not be read from the solution package: {exc}",
                        location=name))
                    continue
                texts.append(_decode_xaml(raw))
    except (zipfile.BadZipFile, OSError) as exc:
        # _load already opened the zip; a failure re-opening it here is rare, but
        # emit a finding rather than silently skipping the scan — and never raise
        # a non-D365Error to the CLI.
        findings.append(Finding(
            "error", "package",
            f"could not re-open the solution package to scan Workflows/*.xaml: {exc}",
            location=str(Path(zip_path))))
        return texts, findings
    return texts, findings


def _check_xaml_stage_collisions(
    zip_path: str | Path, backend: D365Backend
) -> list[Finding]:
    """Report BPF process-stage GUIDs (StageId/NextStageId) already in the org.

    A cloned BPF whose Workflows/*.xaml kept its source stage GUIDs collides on
    import (CreateProcessStage 'Cannot insert duplicate key', #163). Probes the
    `processstages` entity set (PK processstageid); OData v4 GUID filters take the
    bare GUID, no quotes, matching _check_org_collisions.
    """
    texts, findings = _read_workflow_xaml(zip_path)
    guids: set[str] = set()
    for text in texts:
        for raw in _XAML_STAGE_GUID.findall(text):
            gid = _norm(raw)
            # Only probe a well-formed GUID: a malformed/unexpected capture must
            # not reach the OData $filter (would 400 / allow injection and defeat
            # the "never crash on a bad member" goal).
            if _GUID_RE.match(gid):
                guids.add(gid)
    for gid in sorted(guids):
        resp = _force_get(
            backend, "processstages",
            {"$select": "processstageid", "$filter": f"processstageid eq {gid}",
             "$top": "1"})
        if resp.get("value"):
            findings.append(Finding(
                "error", "guid-collision",
                f"process-stage id {gid} already exists in the target org "
                f"(import will fail with a duplicate-key error on CreateProcessStage)",
                component=gid, location="Workflows/*.xaml"))
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
    except (zipfile.LargeZipFile, RuntimeError, NotImplementedError) as exc:
        # A member that can't be read as a manifest (encrypted member ->
        # RuntimeError, unsupported compression -> NotImplementedError, a >4GiB
        # member without zip64 -> LargeZipFile) is a package problem, not an
        # operational failure — report it instead of crashing the CLI (which
        # only catches D365Error). Mirrors solution._sniff_solution_managed,
        # which degrades on any such read failure.
        return None, None, [Finding("error", "package",
                                    f"{p} could not be read as a solution package: {exc}",
                                    location=str(p))]
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
            # BPF stage GUIDs live in Workflows/*.xaml, not the manifests — same
            # guid-collision check name, re-reads the zip for those members (#163).
            findings += _check_xaml_stage_collisions(zip_path, backend)
    valid = not any(f.severity == "error" for f in findings)
    return {
        "valid": valid,
        "findings": [asdict(f) for f in findings],
        "checks_run": checks_run,
    }
