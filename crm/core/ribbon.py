"""Entity ribbon (command-bar) read + edit logic — issue #142.

Reads via RetrieveEntityRibbon (decode the zipped CompressedEntityXml) and edits
the entity's RibbonDiffXml inside a user-supplied solution, applying through the
export -> validate -> import -> publish path.
"""
from __future__ import annotations

import base64
import io
import re
import zipfile
import xml.etree.ElementTree as ET
from dataclasses import dataclass
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
    if actions is None or cmds is None:
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
