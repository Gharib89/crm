"""Entity ribbon (command-bar) read + edit logic — issue #142.

Reads via RetrieveEntityRibbon (decode the zipped CompressedEntityXml) and edits
the entity's RibbonDiffXml inside a user-supplied solution, applying through the
export -> validate -> import -> publish path.
"""
from __future__ import annotations

import base64
import io
import re
import tempfile
import zipfile
import xml.etree.ElementTree as ET
from dataclasses import dataclass
from pathlib import Path
from typing import TYPE_CHECKING, Any, Callable

from crm.core.solution import export_solution, import_solution, publish_all
from crm.core.solution_validate import validate_solution
from crm.core.webresource import resolve_webresource_id  # pyright: ignore[reportUnusedImport]; re-exported for the command layer

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
    try:
        zf_obj = zipfile.ZipFile(io.BytesIO(raw))
    except zipfile.BadZipFile as exc:
        raise ValueError(f"CompressedEntityXml is not a valid ZIP archive: {exc}") from exc
    with zf_obj as zf:
        names = zf.namelist()
        if not names:
            raise ValueError("CompressedEntityXml ZIP archive contains no members")
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
        export_result = export_solution(backend, solution, src,
                                        export_customizations=True,
                                        timeout=timeout)
        if "_dry_run" in export_result:
            return export_result
        _rewrite_customizations(src, dst, mutate)
        if validate:
            report = validate_solution(dst, backend=backend)
            if not report["valid"]:
                errs = [f for f in report["findings"]
                        if f.get("severity") == "error"]
                msgs = "; ".join(e.get("message", "") for e in errs[:3])
                raise ValueError(
                    f"pre-import validation failed ({len(errs)} error(s)): {msgs}")
        result = import_solution(backend, dst, timeout=timeout)
        if publish:
            publish_all(backend)
        return result
