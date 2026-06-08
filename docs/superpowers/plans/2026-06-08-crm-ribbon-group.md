# crm ribbon command group Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Add a `crm ribbon` command group (`export` / `list` / `add-button` / `remove`) so entity command-bar buttons can be read and modified without manual solution-XML surgery.

**Architecture:** One strict-typed core module (`crm/core/ribbon.py`) holding the `RetrieveEntityRibbon` decode, pure RibbonDiffXml parse/mutate functions, and an apply orchestrator that reuses `export_solution` → in-memory customizations.xml rewrite → `validate_solution` → `import_solution` → `publish_all`. One thin Click command module (`crm/commands/ribbon.py`) wired into `crm/cli.py`. Write verbs require `--solution`; `export` does not.

**Tech Stack:** Python 3.14, Click, `xml.etree.ElementTree`, `zipfile`, pytest + `requests_mock`, `click.testing.CliRunner`. Spec: `docs/superpowers/specs/2026-06-08-crm-ribbon-design.md`.

---

## File Structure

- **Create `crm/core/ribbon.py`** — pyright **strict**. All logic:
  - `decode_compressed_ribbon(b64) -> ET.Element` — base64 → ZIP → parse `RibbonXml.xml`.
  - `retrieve_entity_ribbon(backend, entity) -> ET.Element` — Web API call + decode.
  - `RibbonButton` dataclass + `list_custom_buttons(ribbon_diff) -> list[RibbonButton]`.
  - `slugify`, `DEFAULT_GROUPS`, `resolve_group`, `ButtonIds`, `build_button_ids`.
  - customizations.xml nav: `find_entity_node`, `get_or_create_ribbon_diff`.
  - mutations: `add_custom_action`, `remove_custom_action`.
  - apply: `apply_ribbon_change`.
- **Create `crm/commands/ribbon.py`** — Click group + `export`/`list`/`add-button`/`remove` wrappers.
- **Modify `crm/cli.py`** — register `"ribbon"` in the `_lazy_commands` dict (`crm/cli.py:273`).
- **Create `crm/tests/test_ribbon.py`** — core unit tests (pure functions + decode + apply via monkeypatch).
- **Create `crm/tests/test_ribbon_cmd.py`** — command tests via `CliRunner`.
- **Create `crm/tests/fixtures/ribbon_account.b64`** — a captured `CompressedEntityXml` base64 fixture for decode tests.
- **Modify** `README.md`, **create** `docs/how-to/ribbon.md`, **modify** `crm/skills/SKILL.md`.

Reused as-is (no edits): `export_solution`/`import_solution`/`publish_all` (`crm/core/solution.py:556`/`:685`/`:970`), `validate_solution` (`crm/core/solution_validate.py:323`), `resolve_webresource_id` (`crm/core/webresource.py:219`), `D365Backend.get/post` (`crm/utils/d365_backend.py:659`/`:662`).

---

## Verified facts (live MOCE org, 2026-06-08)

- `RetrieveEntityRibbon` MUST use inline string literals: `RetrieveEntityRibbon(EntityName='x',RibbonLocationFilter='All')`. Parameter-alias forms fail (400/500).
- `CompressedEntityXml` decodes base64 → ZIP (`PK\x03\x04`), members `RibbonXml.xml` + `[Content_Types].xml`.
- Default groups (parametric on logical name): form `Mscrm.Form.{e}.MainTab.Save`, homegrid `Mscrm.HomepageGrid.{e}.MainTab.Management`, subgrid `Mscrm.SubGrid.{e}.MainTab.Management`.
- CustomAction `Location` injects into the group's child controls: `{group}.Controls._children` (the one item to re-confirm during the live `add-button` run).

---

### Task 1: Scaffold core module + decode primitive

**Files:**
- Create: `crm/core/ribbon.py`
- Create: `crm/tests/fixtures/ribbon_account.b64`
- Test: `crm/tests/test_ribbon.py`

- [ ] **Step 1: Capture the decode fixture**

Generate a tiny valid fixture (a 2-member zip matching the live shape) so the decode test needs no live org:

```python
# scratch_make_fixture.py  (run once, then delete)
import base64, io, zipfile
buf = io.BytesIO()
with zipfile.ZipFile(buf, "w", zipfile.ZIP_DEFLATED) as zf:
    zf.writestr("RibbonXml.xml", "<RibbonDiffXml><CustomActions/></RibbonDiffXml>")
    zf.writestr("[Content_Types].xml", "<Types/>")
from pathlib import Path
Path("crm/tests/fixtures/ribbon_account.b64").write_text(
    base64.b64encode(buf.getvalue()).decode("ascii"))
```

Run: `python scratch_make_fixture.py && del scratch_make_fixture.py` (PowerShell: `Remove-Item scratch_make_fixture.py`)
Expected: `crm/tests/fixtures/ribbon_account.b64` exists.

- [ ] **Step 2: Write the failing test**

```python
# crm/tests/test_ribbon.py
from pathlib import Path
import xml.etree.ElementTree as ET
import pytest
from crm.core import ribbon

FIXTURE = Path(__file__).parent / "fixtures" / "ribbon_account.b64"


def test_decode_compressed_ribbon_returns_ribbondiff_root():
    b64 = FIXTURE.read_text()
    root = ribbon.decode_compressed_ribbon(b64)
    assert isinstance(root, ET.Element)
    assert root.tag == "RibbonDiffXml"


def test_decode_compressed_ribbon_rejects_non_zip():
    import base64
    not_a_zip = base64.b64encode(b"plain text, not PK").decode("ascii")
    with pytest.raises(ValueError, match="not a ZIP"):
        ribbon.decode_compressed_ribbon(not_a_zip)
```

- [ ] **Step 3: Run test to verify it fails**

Run: `pytest crm/tests/test_ribbon.py -v`
Expected: FAIL — `ModuleNotFoundError`/`AttributeError: module 'crm.core.ribbon' has no attribute 'decode_compressed_ribbon'`.

- [ ] **Step 4: Write minimal implementation**

```python
# crm/core/ribbon.py
"""Entity ribbon (command-bar) read + edit logic — issue #142.

Reads via RetrieveEntityRibbon (decode the zipped CompressedEntityXml) and edits
the entity's RibbonDiffXml inside a user-supplied solution, applying through the
export -> validate -> import -> publish path.
"""
from __future__ import annotations

import base64
import io
import zipfile
import xml.etree.ElementTree as ET
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from crm.utils.d365_backend import D365Backend

_RIBBON_MEMBER = "RibbonXml.xml"


def decode_compressed_ribbon(compressed_b64: str) -> ET.Element:
    """Decode a RetrieveEntityRibbon ``CompressedEntityXml`` value.

    The value is base64 over a ZIP archive (PK header — NOT gzip) whose
    ``RibbonXml.xml`` member is the ribbon document. Returns its root element.
    """
    raw = base64.b64decode(compressed_b64)
    if raw[:2] != b"PK":
        raise ValueError("CompressedEntityXml is not a ZIP archive (no PK header)")
    with zipfile.ZipFile(io.BytesIO(raw)) as zf:
        names = zf.namelist()
        member = _RIBBON_MEMBER if _RIBBON_MEMBER in names else next(
            (n for n in names if n.lower().endswith(".xml")
             and not n.startswith("[")), names[0])
        xml_bytes = zf.read(member)
    return ET.fromstring(xml_bytes)
```

- [ ] **Step 5: Run test to verify it passes**

Run: `pytest crm/tests/test_ribbon.py -v`
Expected: PASS (2 passed).

- [ ] **Step 6: Commit**

```bash
git add crm/core/ribbon.py crm/tests/test_ribbon.py crm/tests/fixtures/ribbon_account.b64
git commit -m "feat(ribbon): decode RetrieveEntityRibbon CompressedEntityXml zip"
```

---

### Task 2: `retrieve_entity_ribbon` — the Web API call

**Files:**
- Modify: `crm/core/ribbon.py`
- Test: `crm/tests/test_ribbon.py`

- [ ] **Step 1: Write the failing test**

Mock the backend with a lightweight stub (no HTTP). Append to `crm/tests/test_ribbon.py`:

```python
class _FakeBackend:
    """Minimal D365Backend stand-in: records the GET path, returns canned JSON."""
    def __init__(self, compressed_b64: str) -> None:
        self.compressed_b64 = compressed_b64
        self.last_path: str | None = None

    def get(self, path: str, **kw: object) -> dict[str, object]:
        self.last_path = path
        return {"CompressedEntityXml": self.compressed_b64}


def test_retrieve_entity_ribbon_uses_inline_string_literals():
    be = _FakeBackend(FIXTURE.read_text())
    root = ribbon.retrieve_entity_ribbon(be, "cwx_ticket")  # type: ignore[arg-type]
    assert root.tag == "RibbonDiffXml"
    # Verified live: inline literals, NOT parameter aliases.
    assert be.last_path == (
        "RetrieveEntityRibbon(EntityName='cwx_ticket',RibbonLocationFilter='All')")
```

- [ ] **Step 2: Run test to verify it fails**

Run: `pytest crm/tests/test_ribbon.py::test_retrieve_entity_ribbon_uses_inline_string_literals -v`
Expected: FAIL — `AttributeError: ... has no attribute 'retrieve_entity_ribbon'`.

- [ ] **Step 3: Write minimal implementation**

Append to `crm/core/ribbon.py`:

```python
def retrieve_entity_ribbon(backend: "D365Backend", entity: str) -> ET.Element:
    """Call RetrieveEntityRibbon for ``entity`` and return the decoded ribbon root.

    The enum filter MUST be an inline OData string literal (``'All'``); parameter
    aliases are rejected by the server (verified live). Returns the full composed
    ribbon (system + custom).
    """
    safe = entity.replace("'", "''")
    path = (f"RetrieveEntityRibbon(EntityName='{safe}',"
            f"RibbonLocationFilter='All')")
    resp = backend.get(path)
    if not isinstance(resp, dict) or "CompressedEntityXml" not in resp:
        raise ValueError(
            f"RetrieveEntityRibbon returned no CompressedEntityXml for {entity!r}")
    return decode_compressed_ribbon(str(resp["CompressedEntityXml"]))
```

- [ ] **Step 4: Run test to verify it passes**

Run: `pytest crm/tests/test_ribbon.py -v`
Expected: PASS (3 passed).

- [ ] **Step 5: Commit**

```bash
git add crm/core/ribbon.py crm/tests/test_ribbon.py
git commit -m "feat(ribbon): retrieve_entity_ribbon via inline-literal RetrieveEntityRibbon"
```

---

### Task 3: Parse custom buttons (`RibbonButton` + `list_custom_buttons`)

**Files:**
- Modify: `crm/core/ribbon.py`
- Test: `crm/tests/test_ribbon.py`

- [ ] **Step 1: Write the failing test**

```python
SAMPLE_DIFF = """
<RibbonDiffXml>
  <CustomActions>
    <CustomAction Id="cwx_ticket.form.Validate.CustomAction"
                  Location="Mscrm.Form.cwx_ticket.MainTab.Save.Controls._children"
                  Sequence="50">
      <CommandUIDefinition>
        <Button Id="cwx_ticket.form.Validate.Button"
                Command="cwx_ticket.form.Validate.Command" LabelText="Validate"/>
      </CommandUIDefinition>
    </CustomAction>
  </CustomActions>
  <CommandDefinitions>
    <CommandDefinition Id="cwx_ticket.form.Validate.Command">
      <Actions>
        <JavaScriptFunction Library="$webresource:cwx_/scripts/x.js" FunctionName="ns.fn">
          <CrmParameter Value="PrimaryControl"/>
        </JavaScriptFunction>
      </Actions>
    </CommandDefinition>
  </CommandDefinitions>
</RibbonDiffXml>
"""


def test_list_custom_buttons_extracts_fields():
    diff = ET.fromstring(SAMPLE_DIFF)
    buttons = ribbon.list_custom_buttons(diff)
    assert len(buttons) == 1
    b = buttons[0]
    assert b.button_id == "cwx_ticket.form.Validate.CustomAction"
    assert b.label == "Validate"
    assert b.command == "cwx_ticket.form.Validate.Command"
    assert b.location == "Mscrm.Form.cwx_ticket.MainTab.Save.Controls._children"
    assert b.function == "ns.fn"
    assert b.library == "$webresource:cwx_/scripts/x.js"


def test_list_custom_buttons_empty_when_no_customactions():
    diff = ET.fromstring("<RibbonDiffXml><CustomActions/></RibbonDiffXml>")
    assert ribbon.list_custom_buttons(diff) == []
```

- [ ] **Step 2: Run test to verify it fails**

Run: `pytest crm/tests/test_ribbon.py -k list_custom_buttons -v`
Expected: FAIL — no attribute `list_custom_buttons`.

- [ ] **Step 3: Write minimal implementation**

Append to `crm/core/ribbon.py` (add `from dataclasses import dataclass` to the imports):

```python
@dataclass(frozen=True)
class RibbonButton:
    """One custom command-bar button parsed from a RibbonDiffXml."""
    button_id: str          # the CustomAction Id (what `remove --button-id` takes)
    label: str
    location: str
    command: str
    function: str
    library: str


def list_custom_buttons(ribbon_diff: ET.Element) -> list[RibbonButton]:
    """Enumerate the custom buttons declared in a RibbonDiffXml element."""
    commands: dict[str, tuple[str, str]] = {}  # command id -> (function, library)
    for cdef in ribbon_diff.iter("CommandDefinition"):
        cid = cdef.get("Id") or ""
        jsf = cdef.find(".//JavaScriptFunction")
        if jsf is not None:
            commands[cid] = (jsf.get("FunctionName") or "", jsf.get("Library") or "")
    buttons: list[RibbonButton] = []
    for action in ribbon_diff.iter("CustomAction"):
        btn = action.find(".//Button")
        if btn is None:
            continue
        command = btn.get("Command") or ""
        fn, lib = commands.get(command, ("", ""))
        buttons.append(RibbonButton(
            button_id=action.get("Id") or "",
            label=btn.get("LabelText") or "",
            location=action.get("Location") or "",
            command=command,
            function=fn,
            library=lib,
        ))
    return buttons
```

- [ ] **Step 4: Run test to verify it passes**

Run: `pytest crm/tests/test_ribbon.py -k list_custom_buttons -v`
Expected: PASS (2 passed).

- [ ] **Step 5: Commit**

```bash
git add crm/core/ribbon.py crm/tests/test_ribbon.py
git commit -m "feat(ribbon): parse custom buttons from RibbonDiffXml"
```

---

### Task 4: ID + group resolution helpers

**Files:**
- Modify: `crm/core/ribbon.py`
- Test: `crm/tests/test_ribbon.py`

- [ ] **Step 1: Write the failing test**

```python
def test_slugify_label():
    assert ribbon.slugify("Validate Ticket!") == "ValidateTicket"
    assert ribbon.slugify("re-open") == "reopen"


def test_resolve_group_defaults_are_parametric():
    assert ribbon.resolve_group("form", "cwx_ticket", None) == \
        "Mscrm.Form.cwx_ticket.MainTab.Save"
    assert ribbon.resolve_group("homegrid", "cwx_ticket", None) == \
        "Mscrm.HomepageGrid.cwx_ticket.MainTab.Management"
    assert ribbon.resolve_group("subgrid", "cwx_ticket", None) == \
        "Mscrm.SubGrid.cwx_ticket.MainTab.Management"


def test_resolve_group_override_wins():
    assert ribbon.resolve_group("form", "cwx_ticket", "My.Custom.Group") == \
        "My.Custom.Group"


def test_build_button_ids():
    ids = ribbon.build_button_ids("cwx_ticket", "form", "Validate", None)
    assert ids.custom_action == "cwx_ticket.form.Validate.CustomAction"
    assert ids.button == "cwx_ticket.form.Validate.Button"
    assert ids.command == "cwx_ticket.form.Validate.Command"


def test_build_button_ids_with_override_base():
    ids = ribbon.build_button_ids("cwx_ticket", "form", "Validate", "my.base")
    assert ids.custom_action == "my.base.CustomAction"
```

- [ ] **Step 2: Run test to verify it fails**

Run: `pytest crm/tests/test_ribbon.py -k "slugify or resolve_group or build_button_ids" -v`
Expected: FAIL — no attribute `slugify`.

- [ ] **Step 3: Write minimal implementation**

Append to `crm/core/ribbon.py` (add `import re` to imports):

```python
DEFAULT_GROUPS: dict[str, str] = {
    "form": "Mscrm.Form.{entity}.MainTab.Save",
    "homegrid": "Mscrm.HomepageGrid.{entity}.MainTab.Management",
    "subgrid": "Mscrm.SubGrid.{entity}.MainTab.Management",
}


def slugify(label: str) -> str:
    """Reduce a label to an ID-safe token (alphanumerics only)."""
    return re.sub(r"[^A-Za-z0-9]+", "", label)


def resolve_group(location: str, entity: str, group_override: str | None) -> str:
    """Map a --location to its default ribbon group, or honor --group override."""
    if group_override:
        return group_override
    try:
        return DEFAULT_GROUPS[location].format(entity=entity)
    except KeyError:
        raise ValueError(
            f"unknown location {location!r}; expected one of {sorted(DEFAULT_GROUPS)}")


@dataclass(frozen=True)
class ButtonIds:
    """The three deterministic IDs a custom button needs."""
    custom_action: str
    button: str
    command: str


def build_button_ids(
    entity: str, location: str, label: str, base_override: str | None
) -> ButtonIds:
    """Deterministic, human-readable IDs: ``{entity}.{location}.{slug(label)}.*``."""
    base = base_override or f"{entity}.{location}.{slugify(label)}"
    return ButtonIds(
        custom_action=f"{base}.CustomAction",
        button=f"{base}.Button",
        command=f"{base}.Command",
    )
```

- [ ] **Step 4: Run test to verify it passes**

Run: `pytest crm/tests/test_ribbon.py -k "slugify or resolve_group or build_button_ids" -v`
Expected: PASS (5 passed).

- [ ] **Step 5: Commit**

```bash
git add crm/core/ribbon.py crm/tests/test_ribbon.py
git commit -m "feat(ribbon): group mapping + deterministic button id helpers"
```

---

### Task 5: customizations.xml navigation

**Files:**
- Modify: `crm/core/ribbon.py`
- Test: `crm/tests/test_ribbon.py`

- [ ] **Step 1: Write the failing test**

```python
CUST_XML = """<ImportExportXml>
  <Entities>
    <Entity><Name>cwx_ticket</Name><RibbonDiffXml><CustomActions/></RibbonDiffXml></Entity>
    <Entity><Name>account</Name></Entity>
  </Entities>
</ImportExportXml>"""


def test_find_entity_node_case_insensitive():
    root = ET.fromstring(CUST_XML)
    node = ribbon.find_entity_node(root, "CWX_Ticket")
    assert node is not None
    assert node.findtext("Name") == "cwx_ticket"


def test_find_entity_node_missing_raises():
    root = ET.fromstring(CUST_XML)
    with pytest.raises(ValueError, match="not found in solution"):
        ribbon.find_entity_node(root, "cwx_missing")


def test_get_or_create_ribbon_diff_returns_existing():
    root = ET.fromstring(CUST_XML)
    entity = ribbon.find_entity_node(root, "cwx_ticket")
    diff = ribbon.get_or_create_ribbon_diff(entity)
    assert diff.tag == "RibbonDiffXml"
    assert diff.find("CustomActions") is not None


def test_get_or_create_ribbon_diff_creates_skeleton():
    root = ET.fromstring(CUST_XML)
    entity = ribbon.find_entity_node(root, "account")
    diff = ribbon.get_or_create_ribbon_diff(entity)
    assert diff.tag == "RibbonDiffXml"
    for child in ("CustomActions", "Templates", "CommandDefinitions",
                  "RuleDefinitions", "LocLabels"):
        assert diff.find(child) is not None
    # idempotent: second call returns the same element, no duplicate
    again = ribbon.get_or_create_ribbon_diff(entity)
    assert again is diff
    assert len(entity.findall("RibbonDiffXml")) == 1
```

- [ ] **Step 2: Run test to verify it fails**

Run: `pytest crm/tests/test_ribbon.py -k "entity_node or ribbon_diff" -v`
Expected: FAIL — no attribute `find_entity_node`.

- [ ] **Step 3: Write minimal implementation**

Append to `crm/core/ribbon.py`:

```python
def find_entity_node(cust_root: ET.Element, entity: str) -> ET.Element:
    """Locate the ``<Entity>`` whose ``<Name>`` matches ``entity`` (case-insensitive)."""
    target = entity.lower()
    for node in cust_root.iter("Entity"):
        name = node.findtext("Name")
        if name is not None and name.lower() == target:
            return node
    raise ValueError(f"entity {entity!r} not found in solution customizations")


def get_or_create_ribbon_diff(entity_node: ET.Element) -> ET.Element:
    """Return the entity's ``<RibbonDiffXml>``, creating an empty skeleton if absent."""
    diff = entity_node.find("RibbonDiffXml")
    if diff is None:
        diff = ET.SubElement(entity_node, "RibbonDiffXml")
    for child in ("CustomActions", "Templates", "CommandDefinitions",
                  "RuleDefinitions", "LocLabels"):
        if diff.find(child) is None:
            ET.SubElement(diff, child)
    return diff
```

- [ ] **Step 4: Run test to verify it passes**

Run: `pytest crm/tests/test_ribbon.py -k "entity_node or ribbon_diff" -v`
Expected: PASS (4 passed).

- [ ] **Step 5: Commit**

```bash
git add crm/core/ribbon.py crm/tests/test_ribbon.py
git commit -m "feat(ribbon): customizations.xml entity + RibbonDiffXml navigation"
```

---

### Task 6: `add_custom_action` mutation

**Files:**
- Modify: `crm/core/ribbon.py`
- Test: `crm/tests/test_ribbon.py`

- [ ] **Step 1: Write the failing test**

```python
def _empty_diff() -> ET.Element:
    root = ET.fromstring(
        "<ImportExportXml><Entities><Entity><Name>cwx_ticket</Name>"
        "</Entity></Entities></ImportExportXml>")
    return ribbon.get_or_create_ribbon_diff(ribbon.find_entity_node(root, "cwx_ticket"))


def test_add_custom_action_injects_three_nodes():
    diff = _empty_diff()
    ids = ribbon.build_button_ids("cwx_ticket", "form", "Validate", None)
    ribbon.add_custom_action(
        diff, ids=ids, group="Mscrm.Form.cwx_ticket.MainTab.Save",
        label="Validate", webresource="cwx_/scripts/x.js", function="ns.fn",
        param="PrimaryControl", sequence=50)
    buttons = ribbon.list_custom_buttons(diff)
    assert len(buttons) == 1
    b = buttons[0]
    assert b.button_id == ids.custom_action
    assert b.label == "Validate"
    assert b.function == "ns.fn"
    assert b.library == "$webresource:cwx_/scripts/x.js"
    action = diff.find(f".//CustomAction[@Id='{ids.custom_action}']")
    assert action is not None
    assert action.get("Location") == \
        "Mscrm.Form.cwx_ticket.MainTab.Save.Controls._children"
    crm_param = diff.find(".//JavaScriptFunction/CrmParameter")
    assert crm_param is not None and crm_param.get("Value") == "PrimaryControl"


def test_add_custom_action_rejects_id_collision():
    diff = _empty_diff()
    ids = ribbon.build_button_ids("cwx_ticket", "form", "Validate", None)
    kw = dict(ids=ids, group="G", label="Validate",
              webresource="cwx_/scripts/x.js", function="ns.fn",
              param="PrimaryControl", sequence=50)
    ribbon.add_custom_action(diff, **kw)
    with pytest.raises(ValueError, match="already exists"):
        ribbon.add_custom_action(diff, **kw)
```

- [ ] **Step 2: Run test to verify it fails**

Run: `pytest crm/tests/test_ribbon.py -k add_custom_action -v`
Expected: FAIL — no attribute `add_custom_action`.

- [ ] **Step 3: Write minimal implementation**

Append to `crm/core/ribbon.py`:

```python
def add_custom_action(
    ribbon_diff: ET.Element,
    *,
    ids: ButtonIds,
    group: str,
    label: str,
    webresource: str,
    function: str,
    param: str,
    sequence: int,
) -> None:
    """Inject a CustomAction + CommandDefinition for a JS button into RibbonDiffXml.

    Raises ValueError if any of the three IDs already exists in the diff.
    """
    existing = {el.get("Id") for el in ribbon_diff.iter()
                if el.tag in ("CustomAction", "Button", "CommandDefinition")}
    for new_id in (ids.custom_action, ids.button, ids.command):
        if new_id in existing:
            raise ValueError(
                f"ribbon id {new_id!r} already exists — use a different --label/--id")

    actions = ribbon_diff.find("CustomActions")
    cmds = ribbon_diff.find("CommandDefinitions")
    if actions is None or cmds is None:  # defensive — skeleton guarantees these
        raise ValueError("RibbonDiffXml missing CustomActions/CommandDefinitions")

    action = ET.SubElement(actions, "CustomAction", {
        "Id": ids.custom_action,
        "Location": f"{group}.Controls._children",
        "Sequence": str(sequence),
    })
    uidef = ET.SubElement(action, "CommandUIDefinition")
    ET.SubElement(uidef, "Button", {
        "Id": ids.button, "Command": ids.command, "LabelText": label,
        "ToolTipTitle": label, "TemplateAlias": "o1", "Sequence": str(sequence),
    })

    cdef = ET.SubElement(cmds, "CommandDefinition", {"Id": ids.command})
    ET.SubElement(cdef, "EnableRules")
    ET.SubElement(cdef, "DisplayRules")
    actions_el = ET.SubElement(cdef, "Actions")
    jsf = ET.SubElement(actions_el, "JavaScriptFunction", {
        "Library": f"$webresource:{webresource}", "FunctionName": function,
    })
    ET.SubElement(jsf, "CrmParameter", {"Value": param})
```

- [ ] **Step 4: Run test to verify it passes**

Run: `pytest crm/tests/test_ribbon.py -k add_custom_action -v`
Expected: PASS (2 passed).

- [ ] **Step 5: Commit**

```bash
git add crm/core/ribbon.py crm/tests/test_ribbon.py
git commit -m "feat(ribbon): add_custom_action injects button + command nodes"
```

---

### Task 7: `remove_custom_action` mutation

**Files:**
- Modify: `crm/core/ribbon.py`
- Test: `crm/tests/test_ribbon.py`

- [ ] **Step 1: Write the failing test**

```python
def test_remove_custom_action_deletes_action_and_command():
    diff = _empty_diff()
    ids = ribbon.build_button_ids("cwx_ticket", "form", "Validate", None)
    ribbon.add_custom_action(
        diff, ids=ids, group="G", label="Validate",
        webresource="cwx_/scripts/x.js", function="ns.fn",
        param="PrimaryControl", sequence=50)
    removed = ribbon.remove_custom_action(diff, ids.custom_action)
    assert removed is True
    assert ribbon.list_custom_buttons(diff) == []
    assert diff.find(f".//CommandDefinition[@Id='{ids.command}']") is None


def test_remove_custom_action_unknown_returns_false():
    diff = _empty_diff()
    assert ribbon.remove_custom_action(diff, "nope.CustomAction") is False
```

- [ ] **Step 2: Run test to verify it fails**

Run: `pytest crm/tests/test_ribbon.py -k remove_custom_action -v`
Expected: FAIL — no attribute `remove_custom_action`.

- [ ] **Step 3: Write minimal implementation**

Append to `crm/core/ribbon.py`:

```python
def remove_custom_action(ribbon_diff: ET.Element, button_id: str) -> bool:
    """Remove the CustomAction with ``button_id`` and its orphaned CommandDefinition.

    Returns True if a matching CustomAction was found and removed, else False.
    """
    actions = ribbon_diff.find("CustomActions")
    cmds = ribbon_diff.find("CommandDefinitions")
    if actions is None:
        return False
    target = next((a for a in actions.findall("CustomAction")
                   if a.get("Id") == button_id), None)
    if target is None:
        return False
    btn = target.find(".//Button")
    command_id = btn.get("Command") if btn is not None else None
    actions.remove(target)
    if command_id and cmds is not None:
        cdef = next((c for c in cmds.findall("CommandDefinition")
                     if c.get("Id") == command_id), None)
        if cdef is not None:
            cmds.remove(cdef)
    return True
```

- [ ] **Step 4: Run test to verify it passes**

Run: `pytest crm/tests/test_ribbon.py -k remove_custom_action -v`
Expected: PASS (2 passed).

- [ ] **Step 5: Commit**

```bash
git add crm/core/ribbon.py crm/tests/test_ribbon.py
git commit -m "feat(ribbon): remove_custom_action drops action + orphaned command"
```

---

### Task 8: `apply_ribbon_change` orchestrator

**Files:**
- Modify: `crm/core/ribbon.py`
- Test: `crm/tests/test_ribbon.py`

This rewrites customizations.xml inside the exported zip in memory, validates, imports, publishes. It takes a `mutate` callback receiving the loaded customizations root so add/remove share one path.

- [ ] **Step 1: Write the failing test (monkeypatch the heavy solution calls)**

```python
def _make_solution_zip(path, cust_xml: str) -> None:
    import zipfile as zf
    with zf.ZipFile(path, "w") as z:
        z.writestr("solution.xml", "<ImportExportXml><SolutionManifest/></ImportExportXml>")
        z.writestr("customizations.xml", cust_xml)
        z.writestr("[Content_Types].xml", "<Types/>")


def test_apply_ribbon_change_rewrites_and_imports(monkeypatch, tmp_path):
    captured: dict[str, object] = {}

    def fake_export(backend, name, output_path, **kw):
        _make_solution_zip(output_path, CUST_XML)
        return {"output": str(output_path), "bytes": 1}

    def fake_validate(zip_path, *, backend=None):
        # read back the rewritten customizations so the test can assert on it
        import zipfile as zf
        with zf.ZipFile(zip_path) as z:
            captured["customizations"] = z.read("customizations.xml").decode()
        return {"valid": True, "findings": [], "checks_run": ["package"]}

    def fake_import(backend, zip_path, **kw):
        captured["imported"] = str(zip_path)
        return {"status": "succeeded"}

    def fake_publish(backend):
        captured["published"] = True
        return {"ok": True}

    monkeypatch.setattr(ribbon, "export_solution", fake_export)
    monkeypatch.setattr(ribbon, "validate_solution", fake_validate)
    monkeypatch.setattr(ribbon, "import_solution", fake_import)
    monkeypatch.setattr(ribbon, "publish_all", fake_publish)

    def mutate(cust_root: ET.Element) -> None:
        entity = ribbon.find_entity_node(cust_root, "cwx_ticket")
        diff = ribbon.get_or_create_ribbon_diff(entity)
        ids = ribbon.build_button_ids("cwx_ticket", "form", "Validate", None)
        ribbon.add_custom_action(
            diff, ids=ids, group="Mscrm.Form.cwx_ticket.MainTab.Save",
            label="Validate", webresource="cwx_/scripts/x.js", function="ns.fn",
            param="PrimaryControl", sequence=50)

    result = ribbon.apply_ribbon_change(
        object(), solution="MySol", entity="cwx_ticket", mutate=mutate)

    assert result["status"] == "succeeded"
    assert captured["published"] is True
    assert "cwx_ticket.form.Validate.Button" in captured["customizations"]  # type: ignore[operator]


def test_apply_ribbon_change_aborts_on_validation_error(monkeypatch, tmp_path):
    def fake_export(backend, name, output_path, **kw):
        _make_solution_zip(output_path, CUST_XML)
        return {"output": str(output_path)}

    def fake_validate(zip_path, *, backend=None):
        return {"valid": False,
                "findings": [{"severity": "error", "message": "bad ribbon"}],
                "checks_run": ["package"]}

    imported: list[str] = []
    monkeypatch.setattr(ribbon, "export_solution", fake_export)
    monkeypatch.setattr(ribbon, "validate_solution", fake_validate)
    monkeypatch.setattr(ribbon, "import_solution",
                        lambda *a, **k: imported.append("x"))
    monkeypatch.setattr(ribbon, "publish_all", lambda *a, **k: None)

    with pytest.raises(ValueError, match="validation failed"):
        ribbon.apply_ribbon_change(
            object(), solution="MySol", entity="cwx_ticket", mutate=lambda r: None)
    assert imported == []  # never imported a failing package
```

- [ ] **Step 2: Run test to verify it fails**

Run: `pytest crm/tests/test_ribbon.py -k apply_ribbon_change -v`
Expected: FAIL — no attribute `apply_ribbon_change`.

- [ ] **Step 3: Write minimal implementation**

Add these imports at the top of `crm/core/ribbon.py` (module-level, so `monkeypatch.setattr(ribbon, ...)` works):

```python
import tempfile
from pathlib import Path
from typing import Any, Callable

from crm.core.solution import export_solution, import_solution, publish_all
from crm.core.solution_validate import validate_solution
```

Then append:

```python
def _rewrite_customizations(
    src_zip: Path, dst_zip: Path, mutate: Callable[[ET.Element], None]
) -> None:
    """Copy every member of ``src_zip`` to ``dst_zip``, applying ``mutate`` to the
    parsed customizations.xml root before writing it back."""
    with zipfile.ZipFile(src_zip) as zin:
        members = {name: zin.read(name) for name in zin.namelist()}
    cust_root = ET.fromstring(members["customizations.xml"])
    mutate(cust_root)
    members["customizations.xml"] = ET.tostring(cust_root, encoding="utf-8")
    with zipfile.ZipFile(dst_zip, "w", zipfile.ZIP_DEFLATED) as zout:
        for name, data in members.items():
            zout.writestr(name, data)


def apply_ribbon_change(
    backend: "D365Backend",
    *,
    solution: str,
    entity: str,
    mutate: Callable[[ET.Element], None],
    validate: bool = True,
    publish: bool = True,
    timeout: int | None = None,
) -> dict[str, Any]:
    """Export ``solution``, rewrite ``entity``'s RibbonDiffXml via ``mutate``,
    validate, import, and publish. Reuses #140 import + #141 validate."""
    with tempfile.TemporaryDirectory() as td:
        src = Path(td) / "export.zip"
        dst = Path(td) / "import.zip"
        export_solution(backend, solution, src, export_customizations=True,
                        timeout=timeout)
        _rewrite_customizations(src, dst, mutate)
        if validate:
            report = validate_solution(dst, backend=backend)
            if not report["valid"]:
                errs = [f for f in report["findings"]
                        if f.get("severity") == "error"]
                raise ValueError(f"pre-import validation failed: {errs}")
        result = import_solution(backend, dst, timeout=timeout)
        if publish:
            publish_all(backend)
        return result
```

> Note: `mutate` raising (e.g. ID collision, entity-not-found) propagates before any import — that is the desired fail-fast behavior.

- [ ] **Step 4: Run test to verify it passes**

Run: `pytest crm/tests/test_ribbon.py -k apply_ribbon_change -v`
Expected: PASS (2 passed).

- [ ] **Step 5: Run the full core test file + pyright strict**

Run: `pytest crm/tests/test_ribbon.py -v`
Expected: all pass.
Run: `pyright crm/core/ribbon.py` (strict via pyproject config)
Expected: 0 errors (if env lacks deps, see CLAUDE.md note about ~56 false errors — confirm none are in `ribbon.py`).

- [ ] **Step 6: Commit**

```bash
git add crm/core/ribbon.py crm/tests/test_ribbon.py
git commit -m "feat(ribbon): apply_ribbon_change export->validate->import->publish"
```

---

### Task 9: Command group scaffold + `export` command + cli.py registration

**Files:**
- Create: `crm/commands/ribbon.py`
- Modify: `crm/cli.py` (the `_lazy_commands` dict around line 273)
- Test: `crm/tests/test_ribbon_cmd.py`

- [ ] **Step 1: Write the failing test**

```python
# crm/tests/test_ribbon_cmd.py
import base64, io, zipfile
import xml.etree.ElementTree as ET
from click.testing import CliRunner
import pytest
from crm.cli import cli
from crm.core import ribbon as ribbon_mod


def _compressed(diff_xml: str) -> str:
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w") as zf:
        zf.writestr("RibbonXml.xml", diff_xml)
        zf.writestr("[Content_Types].xml", "<Types/>")
    return base64.b64encode(buf.getvalue()).decode("ascii")


def test_ribbon_export_prints_xml(monkeypatch):
    xml = "<RibbonDiffXml><CustomActions/></RibbonDiffXml>"
    monkeypatch.setattr(
        ribbon_mod, "retrieve_entity_ribbon",
        lambda backend, entity: ET.fromstring(xml))
    # avoid building a real backend
    monkeypatch.setattr("crm.cli.CLIContext.backend", lambda self: object())
    res = CliRunner().invoke(cli, ["ribbon", "export", "cwx_ticket"])
    assert res.exit_code == 0, res.output
    assert "RibbonDiffXml" in res.output
```

- [ ] **Step 2: Run test to verify it fails**

Run: `pytest crm/tests/test_ribbon_cmd.py::test_ribbon_export_prints_xml -v`
Expected: FAIL — `Error: No such command 'ribbon'`.

- [ ] **Step 3: Register the group in `crm/cli.py`**

In the `_lazy_commands` dict (the block starting near `crm/cli.py:273`, where `"webresource": "crm.commands.webresource:webresource_group"` lives), add:

```python
        "ribbon": "crm.commands.ribbon:ribbon_group",
```

- [ ] **Step 4: Create `crm/commands/ribbon.py` with the group + `export`**

```python
"""Entity ribbon (command-bar) commands — issue #142."""
# pyright: basic
from __future__ import annotations
import xml.dom.minidom as minidom
import xml.etree.ElementTree as ET
from pathlib import Path
import click
from crm.core import ribbon as ribbon_mod
from crm.utils.d365_backend import D365Error
from crm.cli import CLIContext, pass_ctx
from crm.commands._helpers import (
    _handle_d365_error, _journal, _confirm_destructive,
    _solution_option, _require_solution, _resolve_solution,
)


@click.group("ribbon")
def ribbon_group():
    """Read and edit entity command-bar (ribbon) buttons."""


@ribbon_group.command("export")
@click.argument("entity")
@click.option("--output", type=click.Path(dir_okay=False),
              help="Write the ribbon XML to this file instead of stdout.")
@pass_ctx
def ribbon_export(ctx: CLIContext, entity, output):
    """Export an entity's composed ribbon as readable XML."""
    try:
        root = ribbon_mod.retrieve_entity_ribbon(ctx.backend(), entity)
    except D365Error as exc:
        _handle_d365_error(ctx, exc)
        return
    pretty = minidom.parseString(ET.tostring(root)).toprettyxml(indent="  ")
    if output:
        Path(output).write_text(pretty, encoding="utf-8")
        ctx.emit(True, data={"entity": entity, "output": output})
    else:
        click.echo(pretty)
```

- [ ] **Step 5: Run test to verify it passes**

Run: `pytest crm/tests/test_ribbon_cmd.py::test_ribbon_export_prints_xml -v`
Expected: PASS.

- [ ] **Step 6: Commit**

```bash
git add crm/commands/ribbon.py crm/cli.py crm/tests/test_ribbon_cmd.py
git commit -m "feat(ribbon): ribbon export command + group registration"
```

---

### Task 10: `list` command

**Files:**
- Modify: `crm/commands/ribbon.py`
- Test: `crm/tests/test_ribbon_cmd.py`

- [ ] **Step 1: Write the failing test**

```python
def test_ribbon_list_shows_custom_buttons(monkeypatch, tmp_path):
    cust = ("<ImportExportXml><Entities><Entity><Name>cwx_ticket</Name>"
            "<RibbonDiffXml><CustomActions>"
            "<CustomAction Id='cwx_ticket.form.Validate.CustomAction' "
            "Location='Mscrm.Form.cwx_ticket.MainTab.Save.Controls._children'>"
            "<CommandUIDefinition><Button Id='b' "
            "Command='cwx_ticket.form.Validate.Command' LabelText='Validate'/>"
            "</CommandUIDefinition></CustomAction></CustomActions>"
            "<CommandDefinitions><CommandDefinition "
            "Id='cwx_ticket.form.Validate.Command'><Actions>"
            "<JavaScriptFunction Library='$webresource:cwx_/x.js' FunctionName='ns.fn'/>"
            "</Actions></CommandDefinition></CommandDefinitions>"
            "</RibbonDiffXml></Entity></Entities></ImportExportXml>")

    def fake_export(backend, name, output_path, **kw):
        import zipfile as zf
        with zf.ZipFile(output_path, "w") as z:
            z.writestr("customizations.xml", cust)
        return {"output": str(output_path)}

    monkeypatch.setattr(ribbon_mod, "export_solution", fake_export)
    monkeypatch.setattr("crm.cli.CLIContext.backend", lambda self: object())
    res = CliRunner().invoke(
        cli, ["--json", "ribbon", "list", "cwx_ticket", "--solution", "MySol"])
    assert res.exit_code == 0, res.output
    assert "cwx_ticket.form.Validate.CustomAction" in res.output
    assert "Validate" in res.output
```

- [ ] **Step 2: Run test to verify it fails**

Run: `pytest crm/tests/test_ribbon_cmd.py::test_ribbon_list_shows_custom_buttons -v`
Expected: FAIL — `No such command 'list'`.

- [ ] **Step 3: Add a shared loader + the `list` command**

Append to `crm/commands/ribbon.py` (add `import tempfile` and `from crm.core.ribbon import export_solution`-free — call through `ribbon_mod` so tests can monkeypatch):

```python
def _load_solution_ribbon_diff(ctx: CLIContext, solution: str, entity: str):
    """Export the solution and return (cust_root, entity_node, ribbon_diff)."""
    import tempfile
    with tempfile.TemporaryDirectory() as td:
        src = Path(td) / "export.zip"
        ribbon_mod.export_solution(ctx.backend(), solution, src,
                                   export_customizations=True)
        import zipfile
        with zipfile.ZipFile(src) as z:
            cust_root = ET.fromstring(z.read("customizations.xml"))
    entity_node = ribbon_mod.find_entity_node(cust_root, entity)
    diff = ribbon_mod.get_or_create_ribbon_diff(entity_node)
    return cust_root, entity_node, diff


@ribbon_group.command("list")
@click.argument("entity")
@_solution_option
@pass_ctx
def ribbon_list(ctx: CLIContext, entity, solution, require_solution):
    """List the custom buttons declared in a solution's RibbonDiffXml."""
    solution, warning = _resolve_solution(
        ctx, solution, require=_require_solution(True))
    try:
        _, _, diff = _load_solution_ribbon_diff(ctx, solution, entity)
    except D365Error as exc:
        _handle_d365_error(ctx, exc)
        return
    except ValueError as exc:
        ctx.emit(False, error=str(exc))
        return
    buttons = ribbon_mod.list_custom_buttons(diff)
    rows = [[b.button_id, b.label, b.location, b.command, b.function, b.library]
            for b in buttons]
    ctx.emit(True, data=[b.__dict__ for b in buttons], table={
        "headers": ["button-id", "label", "location", "command",
                    "function", "library"],
        "rows": rows,
    }, warnings=[warning] if warning else None)
```

> `_require_solution(True)` makes `--solution` mandatory for `list`. Confirm the `table=` kwarg shape against `CLIContext.emit` (`crm/cli.py:50`); if it expects `{"headers", "rows"}` adjust accordingly — the signature there is `table: dict | None`.

- [ ] **Step 4: Run test to verify it passes**

Run: `pytest crm/tests/test_ribbon_cmd.py::test_ribbon_list_shows_custom_buttons -v`
Expected: PASS.

- [ ] **Step 5: Commit**

```bash
git add crm/commands/ribbon.py crm/tests/test_ribbon_cmd.py
git commit -m "feat(ribbon): ribbon list reads custom buttons from solution"
```

---

### Task 11: `add-button` command

**Files:**
- Modify: `crm/commands/ribbon.py`
- Test: `crm/tests/test_ribbon_cmd.py`

- [ ] **Step 1: Write the failing test**

```python
def test_ribbon_add_button_applies(monkeypatch):
    calls: dict[str, object] = {}

    def fake_apply(backend, *, solution, entity, mutate, **kw):
        # exercise the mutate callback against a minimal solution root
        root = ET.fromstring(
            "<ImportExportXml><Entities><Entity><Name>cwx_ticket</Name>"
            "</Entity></Entities></ImportExportXml>")
        mutate(root)
        calls["solution"] = solution
        calls["entity"] = entity
        calls["has_button"] = (
            root.find(".//Button[@LabelText='Validate']") is not None)
        return {"status": "succeeded"}

    monkeypatch.setattr(ribbon_mod, "apply_ribbon_change", fake_apply)
    monkeypatch.setattr(ribbon_mod, "resolve_webresource_id",
                        lambda backend, name: "guid-1")
    monkeypatch.setattr("crm.cli.CLIContext.backend", lambda self: object())
    res = CliRunner().invoke(cli, [
        "ribbon", "add-button", "cwx_ticket", "--solution", "MySol",
        "--label", "Validate", "--location", "form",
        "--webresource", "cwx_/scripts/x.js", "--function", "ns.fn",
        "--param", "PrimaryControl"])
    assert res.exit_code == 0, res.output
    assert calls["solution"] == "MySol"
    assert calls["has_button"] is True


def test_ribbon_add_button_rejects_missing_webresource(monkeypatch):
    def boom(backend, name):
        raise ValueError(f"web resource {name!r} not found")
    monkeypatch.setattr(ribbon_mod, "resolve_webresource_id", boom)
    monkeypatch.setattr("crm.cli.CLIContext.backend", lambda self: object())
    res = CliRunner().invoke(cli, [
        "--json", "ribbon", "add-button", "cwx_ticket", "--solution", "MySol",
        "--label", "Validate", "--location", "form",
        "--webresource", "cwx_/missing.js", "--function", "ns.fn",
        "--param", "PrimaryControl"])
    assert res.exit_code == 0
    assert "not found" in res.output
```

- [ ] **Step 2: Run test to verify it fails**

Run: `pytest crm/tests/test_ribbon_cmd.py -k add_button -v`
Expected: FAIL — `No such command 'add-button'`.

- [ ] **Step 3: Add the `add-button` command**

Append to `crm/commands/ribbon.py` (add `from crm.core.ribbon import resolve_webresource_id`-free — reference via `ribbon_mod` for monkeypatchability; ensure `crm/core/ribbon.py` re-exports `resolve_webresource_id` by importing it there, or import the symbol into `ribbon_mod` namespace in Task 8 imports):

```python
@ribbon_group.command("add-button")
@click.argument("entity")
@click.option("--label", required=True, help="Button label text.")
@click.option("--location", required=True,
              type=click.Choice(["form", "homegrid", "subgrid"]),
              help="Where the button appears.")
@click.option("--group", "group_override", default=None,
              help="Override the target ribbon group id.")
@click.option("--webresource", required=True,
              help="JS web resource name, e.g. 'cwx_/scripts/x.js'.")
@click.option("--function", required=True,
              help="JavaScript function name, e.g. 'ns.fn'.")
@click.option("--param", required=True,
              type=click.Choice(["PrimaryControl", "SelectedControlSelectedItemIds"]),
              help="CrmParameter passed to the function.")
@click.option("--sequence", type=int, default=50, show_default=True)
@click.option("--id", "id_base", default=None,
              help="Override the generated id base ({entity}.{location}.{label}).")
@_solution_option
@pass_ctx
def ribbon_add_button(ctx, entity, label, location, group_override, webresource,
                      function, param, sequence, id_base, solution, require_solution):
    """Add a JavaScript command-bar button to an entity (no manual XML editing)."""
    solution, warning = _resolve_solution(
        ctx, solution, require=_require_solution(True))
    try:
        ribbon_mod.resolve_webresource_id(ctx.backend(), webresource)  # pre-flight
    except (D365Error, ValueError) as exc:
        if isinstance(exc, D365Error):
            _handle_d365_error(ctx, exc)
        else:
            ctx.emit(False, error=str(exc))
        return

    group = ribbon_mod.resolve_group(location, entity, group_override)
    ids = ribbon_mod.build_button_ids(entity, location, label, id_base)

    def mutate(cust_root):
        node = ribbon_mod.find_entity_node(cust_root, entity)
        diff = ribbon_mod.get_or_create_ribbon_diff(node)
        ribbon_mod.add_custom_action(
            diff, ids=ids, group=group, label=label, webresource=webresource,
            function=function, param=param, sequence=sequence)

    try:
        result = ribbon_mod.apply_ribbon_change(
            ctx.backend(), solution=solution, entity=entity, mutate=mutate)
    except D365Error as exc:
        _handle_d365_error(ctx, exc)
        return
    except ValueError as exc:
        ctx.emit(False, error=str(exc))
        return
    ctx.emit(True, data={"button_id": ids.custom_action, "group": group,
                         "result": result},
             warnings=[warning] if warning else None)
    _journal(ctx, "ribbon add-button", ids.custom_action, result, solution=solution)
```

> Requires `resolve_webresource_id` and `resolve_group`/`build_button_ids`/`add_custom_action`/`find_entity_node`/`get_or_create_ribbon_diff`/`apply_ribbon_change` to be accessible as attributes of `crm.core.ribbon`. Confirm Task 8 imported `resolve_webresource_id` into `crm/core/ribbon.py` (`from crm.core.webresource import resolve_webresource_id`) so `ribbon_mod.resolve_webresource_id` resolves and is monkeypatchable.

- [ ] **Step 4: Add the import to `crm/core/ribbon.py`**

In `crm/core/ribbon.py` imports (Task 8 block), add:

```python
from crm.core.webresource import resolve_webresource_id  # re-exported for the command layer
```

- [ ] **Step 5: Run test to verify it passes**

Run: `pytest crm/tests/test_ribbon_cmd.py -k add_button -v`
Expected: PASS (2 passed).

- [ ] **Step 6: Commit**

```bash
git add crm/commands/ribbon.py crm/core/ribbon.py crm/tests/test_ribbon_cmd.py
git commit -m "feat(ribbon): ribbon add-button with webresource pre-flight"
```

---

### Task 12: `remove` command (destructive)

**Files:**
- Modify: `crm/commands/ribbon.py`
- Test: `crm/tests/test_ribbon_cmd.py`

- [ ] **Step 1: Write the failing test**

```python
def test_ribbon_remove_deletes_button(monkeypatch):
    cust = ("<ImportExportXml><Entities><Entity><Name>cwx_ticket</Name>"
            "<RibbonDiffXml><CustomActions>"
            "<CustomAction Id='cwx_ticket.form.Validate.CustomAction'>"
            "<CommandUIDefinition><Button Id='b' "
            "Command='cwx_ticket.form.Validate.Command' LabelText='Validate'/>"
            "</CommandUIDefinition></CustomAction></CustomActions>"
            "<CommandDefinitions/></RibbonDiffXml></Entity></Entities></ImportExportXml>")
    captured: dict[str, object] = {}

    def fake_apply(backend, *, solution, entity, mutate, **kw):
        root = ET.fromstring(cust)
        mutate(root)
        captured["remaining"] = len(root.findall(".//CustomAction"))
        return {"status": "succeeded"}

    monkeypatch.setattr(ribbon_mod, "apply_ribbon_change", fake_apply)
    monkeypatch.setattr("crm.cli.CLIContext.backend", lambda self: object())
    res = CliRunner().invoke(cli, [
        "ribbon", "remove", "cwx_ticket", "--solution", "MySol",
        "--button-id", "cwx_ticket.form.Validate.CustomAction", "--yes"])
    assert res.exit_code == 0, res.output
    assert captured["remaining"] == 0


def test_ribbon_remove_unknown_button_errors(monkeypatch):
    cust = ("<ImportExportXml><Entities><Entity><Name>cwx_ticket</Name>"
            "<RibbonDiffXml><CustomActions/><CommandDefinitions/></RibbonDiffXml>"
            "</Entity></Entities></ImportExportXml>")

    def fake_apply(backend, *, solution, entity, mutate, **kw):
        mutate(ET.fromstring(cust))  # mutate raises -> propagate
        return {"status": "succeeded"}

    monkeypatch.setattr(ribbon_mod, "apply_ribbon_change", fake_apply)
    monkeypatch.setattr("crm.cli.CLIContext.backend", lambda self: object())
    res = CliRunner().invoke(cli, [
        "--json", "ribbon", "remove", "cwx_ticket", "--solution", "MySol",
        "--button-id", "does.not.exist", "--yes"])
    assert res.exit_code == 0
    assert "not found" in res.output
```

- [ ] **Step 2: Run test to verify it fails**

Run: `pytest crm/tests/test_ribbon_cmd.py -k ribbon_remove -v`
Expected: FAIL — `No such command 'remove'`.

- [ ] **Step 3: Add the `remove` command**

Append to `crm/commands/ribbon.py`:

```python
@ribbon_group.command("remove")
@click.argument("entity")
@click.option("--button-id", "button_id", required=True,
              help="The CustomAction Id to remove (see `crm ribbon list`).")
@click.option("--yes", is_flag=True, help="Skip interactive confirmation.")
@_solution_option
@pass_ctx
def ribbon_remove(ctx, entity, button_id, yes, solution, require_solution):
    """Remove a custom button (CustomAction + its CommandDefinition)."""
    solution, warning = _resolve_solution(
        ctx, solution, require=_require_solution(True))
    if not _confirm_destructive("ribbon button", button_id, yes):
        ctx.emit(False, error="aborted by user")
        return

    def mutate(cust_root):
        node = ribbon_mod.find_entity_node(cust_root, entity)
        diff = ribbon_mod.get_or_create_ribbon_diff(node)
        if not ribbon_mod.remove_custom_action(diff, button_id):
            available = [b.button_id
                         for b in ribbon_mod.list_custom_buttons(diff)]
            raise ValueError(
                f"button-id {button_id!r} not found; available: {available}")

    try:
        result = ribbon_mod.apply_ribbon_change(
            ctx.backend(), solution=solution, entity=entity, mutate=mutate)
    except D365Error as exc:
        _handle_d365_error(ctx, exc)
        return
    except ValueError as exc:
        ctx.emit(False, error=str(exc))
        return
    ctx.emit(True, data={"removed": button_id, "result": result},
             warnings=[warning] if warning else None)
    _journal(ctx, "ribbon remove", button_id, result, solution=solution)
```

- [ ] **Step 4: Run test to verify it passes**

Run: `pytest crm/tests/test_ribbon_cmd.py -k ribbon_remove -v`
Expected: PASS (2 passed).

- [ ] **Step 5: Run the full ribbon test suite**

Run: `pytest crm/tests/test_ribbon.py crm/tests/test_ribbon_cmd.py -v`
Expected: all pass.

- [ ] **Step 6: Commit**

```bash
git add crm/commands/ribbon.py crm/tests/test_ribbon_cmd.py
git commit -m "feat(ribbon): ribbon remove with destructive confirm"
```

---

### Task 13: Docs — README, how-to, SKILL.md

**Files:**
- Modify: `README.md`
- Create: `docs/how-to/ribbon.md`
- Modify: `crm/skills/SKILL.md`

- [ ] **Step 1: Add a `docs/how-to/ribbon.md`**

```markdown
# Manage entity ribbon buttons

`crm ribbon` reads and edits an entity's command-bar (ribbon) buttons.

## Export the current ribbon

```bash
crm ribbon export cwx_ticket            # readable XML to stdout
crm ribbon export cwx_ticket --output ribbon.xml
```

## List custom buttons

```bash
crm ribbon list cwx_ticket --solution MySolution
```

## Add a JavaScript button

```bash
crm ribbon add-button cwx_ticket --solution MySolution \
    --label Validate --location form \
    --webresource cwx_/scripts/x.js --function ns.fn --param PrimaryControl
```

`--location` is `form`, `homegrid`, or `subgrid`. Override the target group with
`--group <id>`. The web resource must already exist in the org/solution.

## Remove a button

```bash
crm ribbon remove cwx_ticket --solution MySolution \
    --button-id cwx_ticket.form.Validate.CustomAction --yes
```
```

- [ ] **Step 2: Add a capability line to `README.md`**

Find the command/feature list section and add (matching the surrounding bullet style):

```markdown
- `crm ribbon` — read and edit entity command-bar buttons (export / list / add-button / remove) without manual solution-XML editing.
```

- [ ] **Step 3: Add a ribbon section to `crm/skills/SKILL.md`**

Match the existing per-group format in that file; add a `ribbon` entry documenting the four verbs and the `--solution` requirement for `list`/`add-button`/`remove`.

- [ ] **Step 4: Verify docs build**

Run: `mkdocs build --strict`
Expected: builds with no warnings (broken-link/stale-ref failures must be zero). If `docs/how-to/ribbon.md` needs a nav entry, add it to `mkdocs.yml` under the How-to section.

- [ ] **Step 5: Commit**

```bash
git add README.md docs/how-to/ribbon.md crm/skills/SKILL.md mkdocs.yml
git commit -m "docs(ribbon): how-to, README capability, SKILL entry"
```

---

### Task 14: Full verification + live smoke (manual)

**Files:** none (verification only)

- [ ] **Step 1: Full test suite**

Run: `pytest crm/tests/test_ribbon.py crm/tests/test_ribbon_cmd.py -v`
Expected: all green.

- [ ] **Step 2: pyright strict on the core module**

Run: `pyright crm/core/ribbon.py`
Expected: 0 errors in `ribbon.py` (ignore the documented ~56 optional-extra false errors elsewhere).

- [ ] **Step 3: Smoke `export` against the live org (read-only, safe)**

Run: `crm --profile localmoce ribbon export cwx_ticket --output /tmp/ribbon.xml` (PowerShell: use a Windows temp path)
Expected: file written, contains `<RibbonDiffXml>`/`<Tab>` nodes.

- [ ] **Step 4: LIVE write round-trip (manual, requires a real solution + JS web resource)**

This confirms the one unverified detail — the `Location` suffix (`{group}.Controls._children`).

```bash
crm --profile localmoce ribbon add-button cwx_ticket --solution <YourUnmanagedSolution> \
    --label Validate --location form \
    --webresource <existing cwx_ js> --function <ns.fn> --param PrimaryControl
crm --profile localmoce ribbon list cwx_ticket --solution <YourUnmanagedSolution>
```

Expected: `list` shows `cwx_ticket.form.Validate.CustomAction`. Open the `cwx_ticket`
form in the D365 UI — the **Validate** button renders in the Save group and fires the JS.

> If the button does not render, the `Location` suffix is wrong — adjust `add_custom_action`
> in `crm/core/ribbon.py` (Task 6) and update Task 6's test + this plan's verified-facts note.

- [ ] **Step 5: Clean up the live test button**

```bash
crm --profile localmoce ribbon remove cwx_ticket --solution <YourUnmanagedSolution> \
    --button-id cwx_ticket.form.Validate.CustomAction --yes
```

Expected: button gone from the form.

- [ ] **Step 6: Final commit (if Task 4 Location fix was needed)**

```bash
git add -A && git commit -m "fix(ribbon): correct CustomAction Location suffix per live test"
```

---

## Self-Review

**Spec coverage:**
- `export` decode (zip + enum) → Tasks 1, 2, 9. ✓
- `list` from solution RibbonDiffXml → Tasks 3, 10. ✓
- `add-button` (ids, group map, `--group`, `--param`, `--sequence`, `--id`, webresource pre-flight, apply) → Tasks 4, 5, 6, 8, 11. ✓
- `remove` (destructive confirm, orphan command cleanup) → Tasks 7, 12. ✓
- Apply via export→validate→import→publish (#140/#141 reuse) → Task 8. ✓
- `--solution` required for list/add/remove, not export → Tasks 9–12. ✓
- Verified group IDs + inline-literal enum → Tasks 2, 4 + verified-facts note. ✓
- Docs sync (README/how-to/SKILL/cli auto-gen) → Task 13. ✓
- Live "working button" criterion → Task 14. ✓

**Placeholder scan:** No TBD/TODO; every code step has full code; commands and expected output are concrete. The only deliberately deferred item is the `Location` suffix, which is implemented (`{group}.Controls._children`) with a live-verify gate in Task 14 — not a placeholder.

**Type consistency:** `ButtonIds(custom_action, button, command)`, `RibbonButton(button_id, label, location, command, function, library)`, `apply_ribbon_change(backend, *, solution, entity, mutate, validate, publish, timeout)`, `add_custom_action(diff, *, ids, group, label, webresource, function, param, sequence)`, `remove_custom_action(diff, button_id)`, `resolve_group(location, entity, group_override)`, `build_button_ids(entity, location, label, base_override)` — names used identically across core tasks and the command tasks that call them. `ribbon_mod.*` attribute access (export_solution, validate_solution, import_solution, publish_all, resolve_webresource_id, apply_ribbon_change) all backed by module-level imports/defs in Tasks 8/11.

**Open risk flagged in-plan:** `ctx.emit(..., table=...)` shape (Task 10) — verify against `CLIContext.emit` at `crm/cli.py:50` during implementation; signature is `table: dict | None`.
