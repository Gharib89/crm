# `crm solution validate <zip>` Implementation Plan (#141)

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Add `crm solution validate <zip>` — a static analyzer that reports every discoverable pre-import problem in a Dynamics solution package in one pass (offline), with an opt-in `--against-org` flag for online GUID-collision / existence checks.

**Architecture:** New strict core module `crm/core/solution_validate.py` holds a `Finding` dataclass and one focused checker function per rule; `validate_solution(zip_path, backend=None)` parses the package once and concatenates findings. A thin Click wrapper `solution validate` in `crm/commands/solution.py` mirrors the offline `extract`/`pack` commands and only acquires `ctx.backend()` when `--against-org` is passed. Reuses the `zipfile`+`xml.etree.ElementTree` pattern from `solution.py::_sniff_solution_managed` and the component-type ints from `SOLUTION_COMPONENT_TYPES`.

**Tech Stack:** Python 3.9+, Click, `xml.etree.ElementTree`, `zipfile`, pytest, `requests_mock`, `click.testing.CliRunner`. pyright **strict** on `crm/core/solution_validate.py`.

**Spec:** `docs/superpowers/specs/2026-06-08-solution-validate-141-design.md`

**Schema note (real-package fidelity):** Fixtures encode the documented op-9-1 solution schema: zip root carries `solution.xml`, `customizations.xml`, `[Content_Types].xml`; `solution.xml` has `<SolutionManifest><RootComponents><RootComponent type="N" schemaName="..."|id="{guid}"/>`; `customizations.xml` has container nodes (`<optionsets>`, `<WebResources>`, `<Entities>`, `<InteractionCentricDashboards>`, …). The cross-direction parity finding (`RootComponent declared but no definition`) is the one most likely to need tuning against a real exported package — flagged as a follow-up, kept as an `error` per the issue's "and vice-versa".

---

## File Structure

- **Create** `crm/core/solution_validate.py` — all parsing + checker logic (strict).
- **Create** `crm/tests/test_solution_validate.py` — unit + CLI + acceptance tests.
- **Modify** `crm/commands/solution.py` — add `solution validate` command + module import.
- **Modify** `README.md`, `docs/how-to/solution.md`, `docs/reference/cli.md`, `crm/skills/SKILL.md` — docs (Task 8).

`Finding.check` values used throughout: `"package"`, `"root-parity"`, `"webresource-ref"`, `"optionset-binding"`, `"guid-collision"`.

---

## Task 1: Module skeleton — `Finding`, `_load`, `validate_solution` (package check)

**Files:**
- Create: `crm/core/solution_validate.py`
- Test: `crm/tests/test_solution_validate.py`

- [ ] **Step 1: Write the failing tests**

Create `crm/tests/test_solution_validate.py`:

```python
# pyright: basic
"""Tests for offline solution validation (#141)."""
from __future__ import annotations

import re
import zipfile

import pytest
import requests_mock
from click.testing import CliRunner

from crm.cli import cli
from crm.core import solution_validate as sv
from crm.utils.d365_backend import ConnectionProfile, D365Backend, D365Error


# ── fixtures ────────────────────────────────────────────────────────────────

def _make_pkg(path, solution_xml, customizations_xml, content_types=True):
    """Write a minimal solution zip (solution.xml + customizations.xml [+ Content_Types])."""
    with zipfile.ZipFile(path, "w") as zf:
        zf.writestr("solution.xml", solution_xml)
        zf.writestr("customizations.xml", customizations_xml)
        if content_types:
            zf.writestr("[Content_Types].xml", "<Types/>")


def _sol(roots=""):
    return (
        '<?xml version="1.0"?>\n'
        "<ImportExportXml><SolutionManifest><UniqueName>cwx_test</UniqueName>"
        f"<Managed>0</Managed><RootComponents>{roots}</RootComponents>"
        "</SolutionManifest></ImportExportXml>"
    )


def _cust(optionsets="", dashboards="", webresources="", entities="", forms=""):
    return (
        '<?xml version="1.0"?>\n'
        f"<ImportExportXml><Entities>{entities}</Entities>"
        f"<optionsets>{optionsets}</optionsets>"
        f"<InteractionCentricDashboards>{dashboards}</InteractionCentricDashboards>"
        f"<WebResources>{webresources}</WebResources>{forms}</ImportExportXml>"
    )


@pytest.fixture
def backend() -> D365Backend:
    profile = ConnectionProfile(
        name="t", url="https://crm.contoso.local/contoso", domain="C",
        username="u", api_version="v9.2", verify_ssl=False,
    )
    return D365Backend(profile, password="pw", dry_run=False)


# ── Task 1: package-level checks ──────────────────────────────────────────────

class TestPackageChecks:
    def test_good_empty_package_is_valid(self, tmp_path):
        p = tmp_path / "good.zip"
        _make_pkg(p, _sol(), _cust())
        report = sv.validate_solution(p)
        assert report["valid"] is True
        assert report["findings"] == []
        assert "package" in report["checks_run"]

    def test_missing_customizations_member_is_fatal(self, tmp_path):
        p = tmp_path / "nocust.zip"
        with zipfile.ZipFile(p, "w") as zf:
            zf.writestr("solution.xml", _sol())
            zf.writestr("[Content_Types].xml", "<Types/>")
        report = sv.validate_solution(p)
        assert report["valid"] is False
        assert any(f["check"] == "package" and "customizations.xml" in f["message"]
                   for f in report["findings"])
        # fatal short-circuit: parity etc. did not run
        assert report["checks_run"] == ["package"]

    def test_not_a_zip_is_fatal_finding(self, tmp_path):
        p = tmp_path / "junk.zip"
        p.write_bytes(b"not a zip")
        report = sv.validate_solution(p)
        assert report["valid"] is False
        assert any(f["check"] == "package" for f in report["findings"])

    def test_unparseable_xml_is_fatal(self, tmp_path):
        p = tmp_path / "bad.zip"
        _make_pkg(p, _sol(), "<ImportExportXml><unclosed>")
        report = sv.validate_solution(p)
        assert report["valid"] is False
        assert any("well-formed" in f["message"] for f in report["findings"])

    def test_finding_envelope_shape(self, tmp_path):
        p = tmp_path / "nocust.zip"
        with zipfile.ZipFile(p, "w") as zf:
            zf.writestr("solution.xml", _sol())
        report = sv.validate_solution(p)
        f = report["findings"][0]
        assert set(f.keys()) == {"severity", "check", "message", "component", "location"}
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `pytest crm/tests/test_solution_validate.py -q`
Expected: FAIL with `ModuleNotFoundError: No module named 'crm.core.solution_validate'`.

- [ ] **Step 3: Create the module**

Create `crm/core/solution_validate.py`:

```python
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
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `pytest crm/tests/test_solution_validate.py -q`
Expected: PASS (5 tests). `_CT`, `re`, `D365Error`, `as_dict` are imported but not all used yet — that is intentional; they are consumed in Tasks 2–5. If pyright (next step) flags `re`/`as_dict`/`D365Backend` as unused, leave them — they are used by later tasks in this same plan; do not delete.

- [ ] **Step 5: Run pyright (strict) on the new module**

Run: `pyright --pythonpath .venv/bin/python crm/core/solution_validate.py`
Expected: no errors on `solution_validate.py`. (Unused-import warnings for symbols consumed in later tasks are acceptable mid-plan; they resolve by Task 5.)

- [ ] **Step 6: Commit**

```bash
git add crm/core/solution_validate.py crm/tests/test_solution_validate.py
git commit -m "feat(solution): add solution_validate module skeleton + package checks (#141)"
```

---

## Task 2: RootComponents ⇄ customizations parity (`_check_root_parity`)

**Files:**
- Modify: `crm/core/solution_validate.py`
- Test: `crm/tests/test_solution_validate.py`

- [ ] **Step 1: Write the failing tests**

Append to `crm/tests/test_solution_validate.py`:

```python
# ── Task 2: root-component parity ─────────────────────────────────────────────

_DASH_GUID = "11111111-1111-1111-1111-111111111111"


class TestRootParity:
    def test_optionset_missing_from_rootcomponents(self, tmp_path):
        # Issue class #1: optionset in <optionsets> but not in <RootComponents>.
        p = tmp_path / "bad_optionset.zip"
        _make_pkg(p, _sol(), _cust(optionsets='<optionset Name="cwx_slatier"/>'))
        report = sv.validate_solution(p)
        assert report["valid"] is False
        errs = [f for f in report["findings"] if f["check"] == "root-parity"]
        assert len(errs) == 1
        assert errs[0]["component"] == "cwx_slatier"
        assert "not declared in <RootComponents>" in errs[0]["message"]

    def test_dashboard_missing_from_rootcomponents(self, tmp_path):
        # Issue class #3: dashboard (type 60) in node but not in <RootComponents>.
        p = tmp_path / "bad_dashboard.zip"
        _make_pkg(p, _sol(),
                  _cust(dashboards=f"<Dashboard><FormId>{_DASH_GUID}</FormId></Dashboard>"))
        report = sv.validate_solution(p)
        errs = [f for f in report["findings"] if f["check"] == "root-parity"]
        assert len(errs) == 1
        assert errs[0]["component"] == _DASH_GUID

    def test_rootcomponent_with_no_definition(self, tmp_path):
        # Reverse direction: declared in <RootComponents> but absent from customizations.
        p = tmp_path / "orphan_root.zip"
        _make_pkg(p, _sol('<RootComponent type="9" schemaName="cwx_ghost"/>'), _cust())
        report = sv.validate_solution(p)
        errs = [f for f in report["findings"] if f["check"] == "root-parity"]
        assert len(errs) == 1
        assert errs[0]["component"] == "cwx_ghost"
        assert "no definition in customizations.xml" in errs[0]["message"]

    def test_clean_parity_is_valid(self, tmp_path):
        p = tmp_path / "good.zip"
        _make_pkg(p, _sol('<RootComponent type="9" schemaName="cwx_slatier"/>'),
                  _cust(optionsets='<optionset Name="cwx_slatier"/>'))
        report = sv.validate_solution(p)
        assert report["valid"] is True
        assert "root-parity" in report["checks_run"]
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `pytest crm/tests/test_solution_validate.py::TestRootParity -q`
Expected: FAIL (parity not wired; findings empty).

- [ ] **Step 3: Implement the checker**

In `crm/core/solution_validate.py`, add these functions after `_norm`:

```python
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
```

Then wire it into `validate_solution` — replace the `pass` placeholder block:

```python
    if sol_root is not None and cust_root is not None:
        checks_run.append("root-parity")
        findings += _check_root_parity(sol_root, cust_root)
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `pytest crm/tests/test_solution_validate.py -q`
Expected: PASS (all Task 1 + Task 2 tests).

- [ ] **Step 5: Commit**

```bash
git add crm/core/solution_validate.py crm/tests/test_solution_validate.py
git commit -m "feat(solution): root-component parity check in validate (#141)"
```

---

## Task 3: `$webresource:` ribbon reference resolution (`_check_webresource_refs`)

**Files:**
- Modify: `crm/core/solution_validate.py`
- Test: `crm/tests/test_solution_validate.py`

- [ ] **Step 1: Write the failing tests**

Append to `crm/tests/test_solution_validate.py`:

```python
# ── Task 3: $webresource: ribbon refs ─────────────────────────────────────────

def _ribbon(*refs):
    cmds = "".join(
        f'<CommandDefinition Id="c{i}"><JavaScriptFunction Library="$webresource:{r}"/>'
        f"</CommandDefinition>"
        for i, r in enumerate(refs)
    )
    return f"<Entity><RibbonDiffXml><CommandDefinitions>{cmds}</CommandDefinitions></RibbonDiffXml></Entity>"


class TestWebresourceRefs:
    def test_unresolved_ref_is_error(self, tmp_path):
        p = tmp_path / "bad_ref.zip"
        _make_pkg(p, _sol(), _cust(entities=_ribbon("cwx_/missing.js")))
        report = sv.validate_solution(p)
        errs = [f for f in report["findings"] if f["check"] == "webresource-ref"]
        assert len(errs) == 1
        assert errs[0]["component"] == "cwx_/missing.js"

    def test_ref_resolved_in_package_is_ok(self, tmp_path):
        p = tmp_path / "good_ref.zip"
        _make_pkg(p, _sol(),
                  _cust(entities=_ribbon("cwx_/present.js"),
                        webresources="<WebResource><Name>cwx_/present.js</Name></WebResource>"))
        report = sv.validate_solution(p)
        assert [f for f in report["findings"] if f["check"] == "webresource-ref"] == []

    def test_ref_resolved_against_org(self, tmp_path, backend):
        p = tmp_path / "org_ref.zip"
        _make_pkg(p, _sol(), _cust(entities=_ribbon("cwx_/inorg.js")))
        with requests_mock.Mocker() as m:
            m.get(re.compile(r"webresourceset"),
                  json={"value": [{"webresourceid": "x"}]})
            report = sv.validate_solution(p, backend=backend)
        assert [f for f in report["findings"] if f["check"] == "webresource-ref"] == []
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `pytest crm/tests/test_solution_validate.py::TestWebresourceRefs -q`
Expected: FAIL.

- [ ] **Step 3: Implement the checker + org helper**

In `crm/core/solution_validate.py`, add the module-level regex near the other constants:

```python
_WEBRESOURCE_REF = re.compile(r"\$webresource:([^\"'\s)<]+)")
```

Add after `_check_root_parity`:

```python
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
```

Wire into `validate_solution` inside the `if sol_root is not None ...` block, after the parity lines:

```python
        checks_run.append("webresource-ref")
        findings += _check_webresource_refs(cust_root, backend)
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `pytest crm/tests/test_solution_validate.py -q`
Expected: PASS.

- [ ] **Step 5: Commit**

```bash
git add crm/core/solution_validate.py crm/tests/test_solution_validate.py
git commit -m "feat(solution): resolve \$webresource: ribbon refs in validate (#141)"
```

---

## Task 4: Global option-set binding check (`_check_optionset_bindings`)

**Files:**
- Modify: `crm/core/solution_validate.py`
- Test: `crm/tests/test_solution_validate.py`

- [ ] **Step 1: Write the failing tests**

Append to `crm/tests/test_solution_validate.py`:

```python
# ── Task 4: global option-set bindings ────────────────────────────────────────

def _attr_global_optionset(name):
    return (f'<Entity><attributes><attribute><OptionSet Name="{name}">'
            f"<IsGlobal>1</IsGlobal></OptionSet></attribute></attributes></Entity>")


class TestOptionsetBindings:
    def test_undeclared_global_binding_is_error(self, tmp_path):
        p = tmp_path / "bad_os.zip"
        _make_pkg(p, _sol(), _cust(entities=_attr_global_optionset("cwx_missingset")))
        report = sv.validate_solution(p)
        errs = [f for f in report["findings"] if f["check"] == "optionset-binding"]
        assert len(errs) == 1
        assert errs[0]["component"] == "cwx_missingset"

    def test_declared_global_binding_is_ok(self, tmp_path):
        p = tmp_path / "good_os.zip"
        _make_pkg(p, _sol(),
                  _cust(optionsets='<optionset Name="cwx_set"/>',
                        entities=_attr_global_optionset("cwx_set")))
        report = sv.validate_solution(p)
        assert [f for f in report["findings"] if f["check"] == "optionset-binding"] == []

    def test_binding_resolved_against_org(self, tmp_path, backend):
        p = tmp_path / "org_os.zip"
        _make_pkg(p, _sol(), _cust(entities=_attr_global_optionset("cwx_inorg")))
        with requests_mock.Mocker() as m:
            m.get(re.compile(r"GlobalOptionSetDefinitions"),
                  json={"value": [{"Name": "cwx_inorg"}]})
            report = sv.validate_solution(p, backend=backend)
        assert [f for f in report["findings"] if f["check"] == "optionset-binding"] == []
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `pytest crm/tests/test_solution_validate.py::TestOptionsetBindings -q`
Expected: FAIL.

- [ ] **Step 3: Implement the checker + org helper**

In `crm/core/solution_validate.py`, add after `_check_webresource_refs`:

```python
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
```

Wire into `validate_solution` after the webresource lines:

```python
        checks_run.append("optionset-binding")
        findings += _check_optionset_bindings(cust_root, backend)
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `pytest crm/tests/test_solution_validate.py -q`
Expected: PASS.

- [ ] **Step 5: Commit**

```bash
git add crm/core/solution_validate.py crm/tests/test_solution_validate.py
git commit -m "feat(solution): global option-set binding check in validate (#141)"
```

---

## Task 5: Org GUID-collision scan (`_check_org_collisions`, `--against-org`)

**Files:**
- Modify: `crm/core/solution_validate.py`
- Test: `crm/tests/test_solution_validate.py`

- [ ] **Step 1: Write the failing tests**

Append to `crm/tests/test_solution_validate.py`:

```python
# ── Task 5: org GUID collisions (--against-org) ───────────────────────────────

_FORM_GUID = "22222222-2222-2222-2222-222222222222"


def _form(guid):
    return f'<Entity><FormXml><forms><systemform><formid>{guid}</formid></systemform></forms></FormXml></Entity>'


class TestOrgCollisions:
    def test_colliding_formid_is_error(self, tmp_path, backend):
        p = tmp_path / "collide.zip"
        _make_pkg(p, _sol(), _cust(entities=_form(_FORM_GUID)))
        with requests_mock.Mocker() as m:
            m.get(re.compile(r"systemforms"), json={"value": [{"formid": _FORM_GUID}]})
            m.get(re.compile(r"savedqueries"), json={"value": []})
            report = sv.validate_solution(p, backend=backend)
        errs = [f for f in report["findings"] if f["check"] == "guid-collision"]
        assert len(errs) == 1
        assert _FORM_GUID in errs[0]["message"]
        assert "guid-collision" in report["checks_run"]

    def test_no_collision_is_ok(self, tmp_path, backend):
        p = tmp_path / "nocollide.zip"
        _make_pkg(p, _sol(), _cust(entities=_form(_FORM_GUID)))
        with requests_mock.Mocker() as m:
            m.get(re.compile(r"systemforms"), json={"value": []})
            m.get(re.compile(r"savedqueries"), json={"value": []})
            report = sv.validate_solution(p, backend=backend)
        assert [f for f in report["findings"] if f["check"] == "guid-collision"] == []

    def test_collisions_skipped_without_backend(self, tmp_path):
        p = tmp_path / "offline.zip"
        _make_pkg(p, _sol(), _cust(entities=_form(_FORM_GUID)))
        report = sv.validate_solution(p)
        assert "guid-collision" not in report["checks_run"]
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `pytest crm/tests/test_solution_validate.py::TestOrgCollisions -q`
Expected: FAIL.

- [ ] **Step 3: Implement the checker**

In `crm/core/solution_validate.py`, add near the other constants:

```python
# (customizations element tag, id-field candidates, org entity set, org id attr)
_COLLISION_SOURCES: tuple[tuple[str, tuple[str, ...], str, str], ...] = (
    ("systemform", ("formid", "FormId", "id"), "systemforms", "formid"),
    ("savedquery", ("savedqueryid", "SavedQueryId", "id"), "savedqueries", "savedqueryid"),
)
```

Add after `_check_optionset_bindings`:

```python
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
```

Wire into `validate_solution` — add inside the `if sol_root is not None ...` block, after the optionset lines:

```python
        if backend is not None:
            checks_run.append("guid-collision")
            findings += _check_org_collisions(cust_root, backend)
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `pytest crm/tests/test_solution_validate.py -q`
Expected: PASS (all unit tests).

- [ ] **Step 5: Run pyright (strict) — all imports now consumed**

Run: `pyright --pythonpath .venv/bin/python crm/core/solution_validate.py`
Expected: no errors, no unused-import warnings.

- [ ] **Step 6: Commit**

```bash
git add crm/core/solution_validate.py crm/tests/test_solution_validate.py
git commit -m "feat(solution): org GUID-collision scan for validate --against-org (#141)"
```

---

## Task 6: `solution validate` command wiring

**Files:**
- Modify: `crm/commands/solution.py` (import block near line 7-10; add command after `solution_pack_cmd` / before `import-result`, around line 549)
- Test: `crm/tests/test_solution_validate.py`

- [ ] **Step 1: Write the failing tests**

Append to `crm/tests/test_solution_validate.py`:

```python
# ── Task 6: CLI wiring ────────────────────────────────────────────────────────

class TestValidateCli:
    def test_good_package_exit_zero(self, tmp_path):
        p = tmp_path / "good.zip"
        _make_pkg(p, _sol('<RootComponent type="9" schemaName="cwx_s"/>'),
                  _cust(optionsets='<optionset Name="cwx_s"/>'))
        result = CliRunner().invoke(cli, ["--json", "solution", "validate", str(p)])
        assert result.exit_code == 0, result.output
        import json
        data = json.loads(result.output)
        assert data["ok"] is True
        assert data["data"]["valid"] is True

    def test_parity_problem_exit_one(self, tmp_path):
        p = tmp_path / "bad.zip"
        _make_pkg(p, _sol(), _cust(optionsets='<optionset Name="cwx_orphan"/>'))
        result = CliRunner().invoke(cli, ["--json", "solution", "validate", str(p)])
        assert result.exit_code == 1, result.output
        import json
        data = json.loads(result.output)
        assert data["ok"] is False
        assert data["data"]["valid"] is False
        assert "error" in data and data["error"]

    def test_against_org_uses_backend(self, tmp_path, backend, monkeypatch):
        p = tmp_path / "collide.zip"
        _make_pkg(p, _sol(), _cust(entities=_form(_FORM_GUID)))
        monkeypatch.setattr("crm.cli.CLIContext.backend", lambda self: backend)
        import json
        with requests_mock.Mocker() as m:
            m.get(re.compile(r"systemforms"), json={"value": [{"formid": _FORM_GUID}]})
            m.get(re.compile(r"savedqueries"), json={"value": []})
            result = CliRunner().invoke(
                cli, ["--json", "solution", "validate", str(p), "--against-org"])
        assert result.exit_code == 1, result.output
        data = json.loads(result.output)
        assert any(f["check"] == "guid-collision" for f in data["data"]["findings"])
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `pytest crm/tests/test_solution_validate.py::TestValidateCli -q`
Expected: FAIL with `No such command 'validate'`.

- [ ] **Step 3: Add the import**

In `crm/commands/solution.py`, add to the core-imports block (after line 9, `from crm.core import solution as sol_mod`):

```python
from crm.core import solution_validate as sv_mod
```

- [ ] **Step 4: Add the command**

In `crm/commands/solution.py`, add immediately before the `@solution_group.command("import-result")` definition (around line 551):

```python
@solution_group.command("validate")
@click.argument("zip_path", type=click.Path(exists=True, dir_okay=False))
@click.option("--against-org", "against_org", is_flag=True,
              help="Also run online checks against the connected org "
                   "(GUID collisions, web-resource & option-set existence). "
                   "Requires a connection/profile.")
@pass_ctx
def solution_validate_cmd(ctx: CLIContext, zip_path, against_org):
    """Statically validate a solution zip before import.

    OFFLINE by default — no connection or profile required. --against-org adds
    online checks (GUID collisions, web-resource & option-set existence). Exits
    non-zero when any error-severity problem is found.
    """
    backend = ctx.backend() if against_org else None
    try:
        report = sv_mod.validate_solution(zip_path, backend=backend)
    except D365Error as exc:
        _handle_d365_error(ctx, exc)
        return
    if report["valid"]:
        ctx.emit(True, data=report)
        return
    n = sum(1 for f in report["findings"] if f["severity"] == "error")
    ctx.emit(False, data=report, error=f"{n} validation error(s) found")
```

- [ ] **Step 5: Run tests to verify they pass**

Run: `pytest crm/tests/test_solution_validate.py -q`
Expected: PASS (all tests).

- [ ] **Step 6: Commit**

```bash
git add crm/commands/solution.py crm/tests/test_solution_validate.py
git commit -m "feat(solution): wire crm solution validate command (#141)"
```

---

## Task 7: Acceptance — one `--against-org` pass reports all 3 issue classes

**Files:**
- Test: `crm/tests/test_solution_validate.py`

- [ ] **Step 1: Write the acceptance tests**

Append to `crm/tests/test_solution_validate.py`:

```python
# ── Task 7: acceptance (issue #141) ───────────────────────────────────────────

class TestAcceptance:
    def test_all_three_classes_in_one_pass(self, tmp_path, backend, monkeypatch):
        """One `validate --against-org` pass reports class #1 (optionset not in
        RootComponents), #3 (dashboard not in RootComponents), and #2 (colliding
        formid in org); exit non-zero."""
        p = tmp_path / "all_three.zip"
        _make_pkg(
            p,
            _sol(),  # empty RootComponents → optionset + dashboard are orphans
            _cust(
                optionsets='<optionset Name="cwx_slatier"/>',
                dashboards=f"<Dashboard><FormId>{_DASH_GUID}</FormId></Dashboard>",
                entities=_form(_FORM_GUID),
            ),
        )
        monkeypatch.setattr("crm.cli.CLIContext.backend", lambda self: backend)
        import json
        with requests_mock.Mocker() as m:
            m.get(re.compile(r"systemforms"), json={"value": [{"formid": _FORM_GUID}]})
            m.get(re.compile(r"savedqueries"), json={"value": []})
            result = CliRunner().invoke(
                cli, ["--json", "solution", "validate", str(p), "--against-org"])
        assert result.exit_code == 1, result.output
        data = json.loads(result.output)
        checks = {f["check"] for f in data["data"]["findings"]}
        assert "root-parity" in checks      # classes #1 + #3
        assert "guid-collision" in checks    # class #2
        parity = [f for f in data["data"]["findings"] if f["check"] == "root-parity"]
        assert {"cwx_slatier", _DASH_GUID} <= {f["component"] for f in parity}

    def test_good_package_against_org_exit_zero(self, tmp_path, backend, monkeypatch):
        p = tmp_path / "good.zip"
        _make_pkg(p, _sol('<RootComponent type="9" schemaName="cwx_s"/>'),
                  _cust(optionsets='<optionset Name="cwx_s"/>'))
        monkeypatch.setattr("crm.cli.CLIContext.backend", lambda self: backend)
        import json
        with requests_mock.Mocker() as m:
            m.get(re.compile(r"systemforms"), json={"value": []})
            m.get(re.compile(r"savedqueries"), json={"value": []})
            result = CliRunner().invoke(
                cli, ["--json", "solution", "validate", str(p), "--against-org"])
        assert result.exit_code == 0, result.output
        assert json.loads(result.output)["data"]["valid"] is True
```

- [ ] **Step 2: Run tests to verify they pass**

Run: `pytest crm/tests/test_solution_validate.py::TestAcceptance -q`
Expected: PASS (the implementation from Tasks 1-6 already satisfies acceptance; if either fails, fix the underlying checker, not the test).

- [ ] **Step 3: Run the full suite + pyright**

Run: `pytest crm/tests/test_solution_validate.py -q && pyright --pythonpath .venv/bin/python crm/core/solution_validate.py`
Expected: all PASS, no pyright errors.

- [ ] **Step 4: Commit**

```bash
git add crm/tests/test_solution_validate.py
git commit -m "test(solution): acceptance for validate --against-org all-3-classes (#141)"
```

---

## Task 8: Docs (README, how-to, CLI reference, SKILL.md)

**Files:**
- Modify: `README.md`
- Modify: `docs/how-to/solution.md`
- Modify: `docs/reference/cli.md`
- Modify: `crm/skills/SKILL.md`

- [ ] **Step 1: README — add `validate` to the solution capabilities**

Find the solution-commands section in `README.md` (grep `solution import` / `solution export`) and add a line in the same style as its siblings:

```markdown
- `crm solution validate <zip>` — statically check a solution package before import (offline: RootComponents⇄customizations parity, `$webresource:` refs, option-set bindings, well-formedness; `--against-org` adds GUID-collision and existence checks). Exits non-zero on any problem.
```

- [ ] **Step 2: how-to — `docs/how-to/solution.md`**

Add a section mirroring the existing `solution import`/`extract` how-to entries:

```markdown
## Validate a solution package before import

Catch packaging problems offline in one pass instead of one-error-per-import round-trip:

```bash
crm solution validate ./MySolution.zip
```

Checks (offline): every component in `customizations.xml` is declared in
`solution.xml` `<RootComponents>` and vice-versa; `$webresource:` references in
ribbon XML resolve to a web resource in the package; every global option-set
binding is declared; both manifests are well-formed and all required members
(`solution.xml`, `customizations.xml`, `[Content_Types].xml`) are present.

Add `--against-org` to also check the connected org for colliding `formid` /
`savedqueryid` GUIDs and for the existence of referenced web resources and
global option sets (requires a connection/profile):

```bash
crm solution validate ./MySolution.zip --against-org
```

`validate` exits non-zero when any error-severity problem is found, so it drops
straight into a pre-import CI gate.
```

- [ ] **Step 3: CLI reference — `docs/reference/cli.md`**

Add a `crm solution validate` entry in the same format as the surrounding command entries (locate `solution import` and copy its heading/option-table shape):

```markdown
### `crm solution validate`

Statically validate a solution zip before import.

| Argument / Option | Description |
| --- | --- |
| `ZIP_PATH` | Path to the solution `.zip` to validate. |
| `--against-org` | Also run online checks against the connected org (GUID collisions, web-resource & option-set existence). Requires a connection/profile. |

Offline by default — no connection required. Exits non-zero when any
error-severity problem is found.
```

- [ ] **Step 4: SKILL.md — keep skill ⇄ CLI in sync**

In `crm/skills/SKILL.md`, find where `solution import` / `solution export` are listed and add `solution validate` in the identical style (one entry; note offline + `--against-org`).

- [ ] **Step 5: Build docs strictly**

Run: `mkdocs build --strict`
Expected: build succeeds, no warnings (stale refs / broken links fail CI).

- [ ] **Step 6: Commit**

```bash
git add README.md docs/how-to/solution.md docs/reference/cli.md crm/skills/SKILL.md
git commit -m "docs(solution): document crm solution validate (#141)"
```

---

## Final verification (after all tasks)

- [ ] Run the whole test suite: `pytest -q` — all green (no regressions).
- [ ] Run pyright: `pyright --pythonpath .venv/bin/python` — no new errors on `crm/core/solution_validate.py`.
- [ ] Run `mkdocs build --strict` — clean.
- [ ] Confirm acceptance: `crm solution validate` on a bad package exits 1 and lists every class; on a good package exits 0.

**Commit subjects use `feat(solution): …` / `docs(solution): …`** so python-semantic-release cuts a **minor** bump on merge to `main` and generates the CHANGELOG entry (do not hand-edit CHANGELOG).

## Out of scope (YAGNI)

- No auto-repair / package rewrite.
- No managed-solution-specific rules beyond well-formedness + parity.
- Only `solution.xml` + `customizations.xml` are inspected.
