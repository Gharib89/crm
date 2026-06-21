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
from typing import TYPE_CHECKING, Any, Callable, Sequence

from crm.core.solution import export_solution, import_solution, publish_all
from crm.core.solution_validate import validate_solution
from crm.core.webresource import resolve_webresource_id  # pyright: ignore[reportUnusedImport]; re-exported for the command layer
from crm.utils.d365_backend import odata_literal

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
    path = (f"RetrieveEntityRibbon(EntityName={odata_literal(entity)},"
            f"RibbonLocationFilter='All')")
    resp = backend.get(path)
    if not isinstance(resp, dict) or "CompressedEntityXml" not in resp:
        raise ValueError(
            f"RetrieveEntityRibbon returned no CompressedEntityXml for {entity!r}")
    return decode_compressed_ribbon(str(resp["CompressedEntityXml"]))


def retrieve_application_ribbon(backend: "D365Backend") -> ET.Element:
    """Call RetrieveApplicationRibbon and return the decoded ribbon root.

    The application-wide ribbon (everything not bound to a specific table). The
    function takes no parameters and returns the zipped XML under a different key
    (``CompressedApplicationRibbonXml``, not the entity path's
    ``CompressedEntityXml``) — both verified live.
    """
    resp = backend.get("RetrieveApplicationRibbon()")
    if not isinstance(resp, dict) or "CompressedApplicationRibbonXml" not in resp:
        raise ValueError(
            "RetrieveApplicationRibbon returned no CompressedApplicationRibbonXml")
    return decode_compressed_ribbon(str(resp["CompressedApplicationRibbonXml"]))


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
    if not base_override:
        slug = slugify(label)
        if not slug:
            raise ValueError(
                f"label {label!r} produces an empty slug; use --id to set a base ID")
        base = f"{entity}.{location}.{slug}"
    else:
        base = base_override
    return ButtonIds(
        custom_action=f"{base}.CustomAction",
        button=f"{base}.Button",
        command=f"{base}.Command",
    )


# The two platform DisplayRules that, required together, can never both be true
# (a command cannot be legacy-web-only AND modern-only) — so a CommandDefinition
# carrying both is always hidden. MS-documented reversible hide; reuse verbatim as
# fixed platform refs, never regenerate. See `hide_button_display_rule`.
RIBBON_HIDE_DISPLAY_RULES: tuple[str, str] = (
    "Mscrm.HideOnModern", "Mscrm.ShowOnlyOnModern")

# Composed-ribbon control elements that carry an ``Id`` a hide can target.
_COMPOSED_CONTROL_TAGS = frozenset({
    "Button", "SplitButton", "ToggleButton", "FlyoutAnchor", "Group", "Control",
    "MenuSection",
})


def find_composed_element(composed_root: ET.Element, target_id: str) -> ET.Element | None:
    """Locate a control in a composed ribbon (RetrieveEntityRibbon) by its ``Id``.

    Searches the control-bearing tags only, so a typo'd ``--target-id`` resolves to
    None (a hard error upstream) rather than silently no-op'ing — the #1 ribbon
    defect. Returns the element (so the caller can read its ``Command``) or None.
    """
    for el in composed_root.iter():
        if el.tag in _COMPOSED_CONTROL_TAGS and el.get("Id") == target_id:
            return el
    return None


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


def hide_button_display_rule(ribbon_diff: ET.Element, command_id: str) -> None:
    """Hide an OOB button reversibly by overriding its CommandDefinition.

    Emits ``<CommandDefinition Id="{command_id}">`` with empty ``EnableRules`` /
    ``Actions`` and ``DisplayRules`` carrying both ``Mscrm.HideOnModern`` and
    ``Mscrm.ShowOnlyOnModern`` — a pair that can never both be true, so the command
    is always hidden. This is the Microsoft-documented reversible alternative to the
    one-way ``HideCustomAction``; deleting the override restores the default.

    Raises ValueError if the command is already overridden in this diff.
    """
    cmds = ribbon_diff.find("CommandDefinitions")
    if cmds is None:
        raise ValueError("RibbonDiffXml missing CommandDefinitions")
    if any(c.get("Id") == command_id for c in cmds.findall("CommandDefinition")):
        raise ValueError(
            f"command {command_id!r} is already overridden in this solution's ribbon")
    cdef = ET.SubElement(cmds, "CommandDefinition", {"Id": command_id})
    ET.SubElement(cdef, "EnableRules")
    rules = ET.SubElement(cdef, "DisplayRules")
    for rule_id in RIBBON_HIDE_DISPLAY_RULES:
        ET.SubElement(rules, "DisplayRule", {"Id": rule_id})
    ET.SubElement(cdef, "Actions")


def hide_button_hide_action(ribbon_diff: ET.Element, target_id: str) -> None:
    """Hide an OOB ribbon element via a ``<HideCustomAction>`` — a one-way trapdoor.

    Unlike `hide_button_display_rule`, a HideCustomAction removes the element from
    ribbon processing entirely and **cannot be removed except by a new version of
    the installing solution** (MS-documented). Callers must gate this behind an
    explicit irreversibility confirmation.

    Raises ValueError if ``target_id`` is already hidden in this diff.
    """
    actions = ribbon_diff.find("CustomActions")
    if actions is None:
        raise ValueError("RibbonDiffXml missing CustomActions")
    if any(h.get("Location") == target_id
           for h in actions.findall("HideCustomAction")):
        raise ValueError(f"element {target_id!r} is already hidden in this solution's ribbon")
    ET.SubElement(actions, "HideCustomAction", {
        "HideActionId": f"{target_id}.HideAction",
        "Location": target_id,
    })


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


# ── Enable/display rules (B3, issue #465) ───────────────────────────────────
# Curated allow-list of predefined platform rule ids. The server SILENTLY
# IGNORES an unknown `Mscrm.*` rule reference (the command then misbehaves with
# no error), so a misspelled platform id is a footgun — we reject any `Mscrm.*`
# id not on this list as a likely typo. Custom (non-`Mscrm.`) rule ids are
# accepted as-is: they reference rules defined in the same solution (e.g. via
# `add_custom_rule`). The lists are grouped by the rule kind they may reference,
# grounded in the documented predefined rules; extend as new ids are needed.
# https://learn.microsoft.com/power-apps/developer/model-driven-apps/define-ribbon-enable-rules
PLATFORM_ENABLE_RULES: frozenset[str] = frozenset({
    "Mscrm.SelectionCountExactlyOne",
    "Mscrm.ShowOnGrid",
    "Mscrm.ShowOnQuickAction",
    "Mscrm.ShowOnGridAndQuickAction",
})
# Mscrm.HideOnModern + Mscrm.ShowOnlyOnModern are the always-false modern-UI
# display rules (the pair the hide-button editor reuses verbatim).
PLATFORM_DISPLAY_RULES: frozenset[str] = frozenset({
    "Mscrm.HideOnModern",
    "Mscrm.ShowOnlyOnModern",
})

_OOB_COMMAND_PREFIX = "Mscrm."

# CommandDefinition children occur in this schema order; a created container must
# be inserted so the sequence stays XSD-valid.
_COMMAND_CHILD_ORDER = ("EnableRules", "DisplayRules", "Actions")


def is_oob_command(command_id: str) -> bool:
    """Heuristic: out-of-the-box (platform) command ids are ``Mscrm.*`` prefixed.

    Custom commands created by `add_custom_action` are ``{entity}.{location}.…``.
    Editing rules on an OOB command is unsupported ground (warned, not blocked).
    """
    return command_id.startswith(_OOB_COMMAND_PREFIX)


def validate_rule_ids(rule_ids: "Sequence[str]", *, kind: str) -> None:
    """Reject any ``Mscrm.*`` id not in the curated allow-list for ``kind``.

    ``kind`` is ``"enable"`` or ``"display"``. Non-``Mscrm.`` (custom) ids pass —
    they reference rules defined in the solution. Raises ValueError on an
    unrecognized platform id (which the server would otherwise silently ignore).
    """
    allowed = PLATFORM_ENABLE_RULES if kind == "enable" else PLATFORM_DISPLAY_RULES
    for rid in rule_ids:
        if rid.startswith(_OOB_COMMAND_PREFIX) and rid not in allowed:
            raise ValueError(
                f"{kind}-rule id {rid!r} is not a recognized platform rule — the "
                f"server silently ignores an unknown Mscrm.* rule. Allowed platform "
                f"{kind} rules: {sorted(allowed)}. For a custom rule, define it with "
                f"`ribbon add-custom-rule`.")


def find_command_definition(ribbon_diff: ET.Element, command_id: str) -> ET.Element:
    """Locate the ``<CommandDefinition Id=command_id>`` in a RibbonDiffXml."""
    cmds = ribbon_diff.find("CommandDefinitions")
    if cmds is not None:
        cdef = next((c for c in cmds.findall("CommandDefinition")
                     if c.get("Id") == command_id), None)
        if cdef is not None:
            return cdef
    available = [c.get("Id") for c in ribbon_diff.iter("CommandDefinition")]
    raise ValueError(
        f"command-id {command_id!r} not found; available: {available}")


def _ensure_command_child(cdef: ET.Element, tag: str) -> ET.Element:
    """Return the CommandDefinition's ``<tag>`` child, creating it in schema order."""
    child = cdef.find(tag)
    if child is not None:
        return child
    child = ET.Element(tag)
    after = set(_COMMAND_CHILD_ORDER[_COMMAND_CHILD_ORDER.index(tag) + 1:])
    idx = next((i for i, el in enumerate(list(cdef)) if el.tag in after), len(cdef))
    cdef.insert(idx, child)
    return child


def _set_rule_refs(
    cdef: ET.Element, container_tag: str, ref_tag: str, rule_ids: "Sequence[str]"
) -> None:
    """Replace ``cdef``'s ``<container_tag>`` children with one ref per id, in order."""
    container = _ensure_command_child(cdef, container_tag)
    for el in list(container):
        container.remove(el)
    for rid in rule_ids:
        ET.SubElement(container, ref_tag, {"Id": rid})


def set_command_rules(
    ribbon_diff: ET.Element,
    *,
    command_id: str,
    enable_rules: "Sequence[str]",
    display_rules: "Sequence[str]",
) -> None:
    """Set a command's enable/display rule references to exactly the given ids.

    Replaces the ``<EnableRules>`` / ``<DisplayRules>`` children of the target
    CommandDefinition with one reference element per id, in the order given (so
    the exported set matches with no drop or reorder). A category passed an empty
    list is left untouched. The CommandDefinition ``Id`` is never modified.
    """
    cdef = find_command_definition(ribbon_diff, command_id)
    if enable_rules:
        _set_rule_refs(cdef, "EnableRules", "EnableRule", enable_rules)
    if display_rules:
        _set_rule_refs(cdef, "DisplayRules", "DisplayRule", display_rules)


def build_custom_rule_id(command_id: str, function: str) -> str:
    """Deterministic id for a custom enable rule: ``{command_id}.{slug(fn)}.EnableRule``."""
    slug = slugify(function)
    if not slug:
        raise ValueError(
            f"function {function!r} produces an empty slug; cannot derive a rule id")
    return f"{command_id}.{slug}.EnableRule"


def add_custom_rule(
    ribbon_diff: ET.Element,
    *,
    command_id: str,
    rule_id: str,
    webresource: str,
    function: str,
) -> None:
    """Define a custom (JavaScript) enable rule and reference it on a command.

    Adds an ``<EnableRule Id=rule_id><CustomRule Library=$webresource:.. FunctionName=..>``
    to ``/RuleDefinitions/EnableRules`` and a matching ``<EnableRule Id=rule_id>``
    reference under the command's ``<EnableRules>``. The CommandDefinition ``Id``
    is never modified. Raises ValueError if ``rule_id`` is already defined.
    """
    cdef = find_command_definition(ribbon_diff, command_id)
    rule_defs = ribbon_diff.find("RuleDefinitions")
    if rule_defs is None:
        rule_defs = ET.SubElement(ribbon_diff, "RuleDefinitions")
    enable_defs = rule_defs.find("EnableRules")
    if enable_defs is None:
        enable_defs = ET.SubElement(rule_defs, "EnableRules")
    if any(r.get("Id") == rule_id for r in enable_defs.findall("EnableRule")):
        raise ValueError(
            f"custom rule id {rule_id!r} already exists — that function is already "
            f"wired on this command")
    rule = ET.SubElement(enable_defs, "EnableRule", {"Id": rule_id})
    ET.SubElement(rule, "CustomRule", {
        "Library": f"$webresource:{webresource}", "FunctionName": function})
    refs = _ensure_command_child(cdef, "EnableRules")
    if not any(r.get("Id") == rule_id for r in refs.findall("EnableRule")):
        ET.SubElement(refs, "EnableRule", {"Id": rule_id})


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
            # A ribbon edit is a round-trip update-import: the exported package
            # re-carries the entity's existing form/view GUIDs, which the
            # fresh-install GUID-collision check would flag as false positives and
            # abort the import (#269). Skip only those checks; keep `backend` so
            # the web-resource-ref check (the new button's JS) still runs.
            report = validate_solution(dst, backend=backend, check_collisions=False)
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
