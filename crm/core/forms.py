"""Read and clone systemform records.

Mirrors views.py (read_entity_views / create_view). A form is read from the
`systemforms` set, its formxml entity-retargeted, and recreated against a new
`objecttypecode`. Form retarget logic is isolated here so it is testable
independently of the clone orchestrator, and so a future `crm form` command
can wrap it the way `view` wraps `views.py`.
"""

from __future__ import annotations

import re
import uuid
import xml.etree.ElementTree as ET
from typing import Any, cast

from crm.core.metadata import attribute_info, label_text, maybe_publish
from crm.utils.d365_backend import D365Backend, D365Error, as_dict, odata_literal

FORM_TYPE_MAIN = 2

_FORM_SELECT = "formid,name,objecttypecode,type,formxml,description,isdefault"

# Control-type `classid` GUIDs keyed by the attribute's `AttributeType`. These are
# stable D365 platform constants (the FormXml control *type* id), live-verified
# against a stock org — never regenerated (see regenerate_form_clone_ids, which
# deliberately preserves `classid`). Customer/Owner reuse the plain-lookup control;
# Picklist/State share the option-set control; Status has its own status-reason
# control. Types absent here (Double, MultiSelectPicklist, BigInt, …) have no
# live-verified constant, so classid_for_attribute_type raises rather than guess.
_CONTROL_CLASSIDS: dict[str, str] = {
    "String": "{4273EDBD-AC1D-40D3-9FB2-095C621B552D}",
    "Memo": "{E0DECE4B-6FC8-4A8F-A065-082708572369}",
    "Integer": "{C6D124CA-7EDA-4A60-AEA9-7FB8D318B68F}",
    "Decimal": "{C3EFE0C3-0EC6-42BE-8349-CBD9079DFD8E}",
    "Money": "{533B9E00-756B-4312-95A0-DC888637AC78}",
    "DateTime": "{5B773807-9FB2-42DB-97C3-7A91EFF8ADFF}",
    "Boolean": "{67FAC785-CD58-4F9F-ABB3-4B7DDC6ED5ED}",
    "Picklist": "{3EF39988-22BB-4F0B-BBBE-64B5A3748AEE}",
    "State": "{3EF39988-22BB-4F0B-BBBE-64B5A3748AEE}",
    "Status": "{5D68B988-0661-4DB2-BC3E-17598AD3BE6C}",
    "Lookup": "{270BD3DB-D9AF-4782-9025-509E298DEC0A}",
    "Customer": "{270BD3DB-D9AF-4782-9025-509E298DEC0A}",
    "Owner": "{270BD3DB-D9AF-4782-9025-509E298DEC0A}",
    "PartyList": "{CBFB742C-14E7-4A17-96BB-1A13F7F64AA2}",
}


def classid_for_attribute_type(attribute_type: str) -> str:
    """Map an attribute's ``AttributeType`` to its control ``classid`` constant.

    Raises ``D365Error`` for a type with no live-verified constant, naming the
    supported set — rather than emit a guessed ``classid`` that would publish
    without error but render a broken control.
    """
    classid = _CONTROL_CLASSIDS.get(attribute_type)
    if classid is None:
        supported = ", ".join(sorted(_CONTROL_CLASSIDS))
        raise D365Error(
            f"No control classid is mapped for attribute type "
            f"{attribute_type!r}. Supported types: {supported}. Add the field "
            f"by hand-splicing FormXml for other types."
        )
    return classid


def retarget_formxml(formxml: str, *, src_entity: str, dst_entity: str) -> str:
    """Rewrite a form's formxml to reference the clone entity.

    Swaps whole-token occurrences of ``src_entity`` for ``dst_entity``. Word
    boundaries protect attribute logical names that merely start with the entity
    name (e.g. ``new_projectid``, ``new_project_code`` are left intact) — the
    clone reuses those attribute names verbatim, so their bindings must not
    change. Only the entity name itself (subgrid/navigation entity refs) moves.
    """
    if not formxml:
        return formxml
    return re.sub(rf"\b{re.escape(src_entity)}\b", dst_entity, formxml)


_GUID = r"[0-9a-fA-F]{8}-[0-9a-fA-F]{4}-[0-9a-fA-F]{4}-[0-9a-fA-F]{4}-[0-9a-fA-F]{12}"
_ANY_GUID_RE = re.compile(_GUID)

# The form-INTERNAL registration ids whose GUID values must be fresh per clone.
# Matched by attribute name, case-sensitively and value-as-GUID only, so that:
#   - `(?<!\w)id`  hits ANY element's lowercase `id="{GUID}"` (tab/section/cell/
#                  control instance ids — all form-internal) but NOT `classid`
#                  (control TYPE id), `labelid`, `uniqueid`, nor the capital `Id`
#                  of `<Role Id="…">` (a security-role ref).
#   - non-GUID ids (`id="WebResource_…"`, `id="new_code"`) never match.
# Everything NOT listed here — `classid`, `Role Id`, `<ViewId>`/`<ViewIds>`,
# `<QuickFormId>` — references an external object and is deliberately preserved;
# the guard in regenerate_form_clone_ids is the backstop if that ever slips.
_REGEN_ATTR_RE = re.compile(
    r"""(?P<attr>(?<![\w])id|labelid|uniqueid|handlerUniqueId|libraryUniqueId)
        (?P<eq>\s*=\s*)(?P<q>["'])
        (?P<brace>\{)?(?P<guid>""" + _GUID + r""")(?(brace)\})(?P=q)""",
    re.VERBOSE,
)


def regenerate_form_clone_ids(formxml: str) -> str:
    """Give a cloned form's internal registration ids fresh GUIDs so repeat
    clones of one source never collide on on-prem id uniqueness.

    On-prem v9.x enforces org-wide uniqueness on a form's ``labelid`` and layout
    element ``id`` GUIDs (and the per-instance ``uniqueid``/``handlerUniqueId``/
    ``libraryUniqueId`` registrations). Cloning reuses the source's values
    verbatim, so the create fails with ``0x8004f658`` — e.g. *"The label '…', id:
    '…' already exists. Supply unique labelid values."* (Dataverse online silently
    reassigns them, which is why cloud never saw this; the source ``<form>`` root
    carries no ``id`` at all, so there is no single PK to regenerate.)

    Each matched GUID is replaced with a fresh ``uuid4``, *consistently* — one new
    value per distinct source GUID — so any intra-form reference stays intact.
    GUIDs that point at external objects (``classid`` control types, ``<Role Id>``
    security roles, ``<ViewId>``/``<QuickFormId>`` lookups) are left untouched. A
    guard re-reads every GUID we did **not** target and refuses to return a form
    whose external references changed, rather than POST a corrupt clone.
    """
    if not formxml:
        return formxml
    mapping: dict[str, str] = {}

    def _repl(m: "re.Match[str]") -> str:
        old = m.group("guid").lower()
        if old not in mapping:
            mapping[old] = str(uuid.uuid4())
        brace = "{" if m.group("brace") else ""
        close = "}" if m.group("brace") else ""
        return (f"{m.group('attr')}{m.group('eq')}{m.group('q')}"
                f"{brace}{mapping[old]}{close}{m.group('q')}")

    new_xml = _REGEN_ATTR_RE.sub(_repl, formxml)
    new_ids = set(mapping.values())
    untouched_before = sorted(
        g.lower() for g in _ANY_GUID_RE.findall(formxml) if g.lower() not in mapping)
    untouched_after = sorted(
        g.lower() for g in _ANY_GUID_RE.findall(new_xml) if g.lower() not in new_ids)
    if untouched_before != untouched_after:
        raise D365Error(
            "form-clone id regeneration altered a non-target GUID (external "
            "reference); refusing to POST a possibly corrupt form.")
    return new_xml


# --- FormXml field surgery (add / remove / move a field's <cell>) ---------------
#
# Structural edits parse the FormXml as XML (etree) rather than regex-splice it:
# locating the right <section> and removing exactly one <cell> is a tree
# operation. The clone path stays regex-based — it does whole-document token
# swaps + GUID regeneration where a parse-reserialize would needlessly normalize
# untouched markup. These transforms touch only the one <cell> they target, so
# every external GUID (other controls' classid, <Role Id>, <ViewId>) is preserved
# by virtue of never being visited; the e2e round-trip is the backstop.
#
# Language code 1033 (English) is used for the inserted <label> — it matches the
# base language of the project's target orgs and every label already in their
# stock forms; multi-language label authoring is out of scope.
_LABEL_LANGUAGECODE = "1033"


def _fresh_cell_id() -> str:
    """A brace-wrapped uuid4 for a newly inserted cell, matching FormXml style."""
    return "{" + str(uuid.uuid4()) + "}"


def _parse_formxml(formxml: str) -> "ET.Element":
    """Parse FormXml, turning a malformed payload into a ``D365Error`` so the CLI
    emits its standard error envelope rather than a raw ``ParseError`` traceback."""
    try:
        return ET.fromstring(formxml)
    except ET.ParseError as exc:
        raise D365Error(f"Could not parse the form's FormXml: {exc}") from exc


def _id_matches(value: str | None, given: str) -> bool:
    """Whether a FormXml ``id`` attribute matches a user-supplied id, tolerating
    braces and case (FormXml ids are brace-wrapped, case-insensitive GUIDs)."""
    if not value:
        return False
    return value.strip("{}").lower() == given.strip("{}").lower()


def _find_field_control(root: "ET.Element", datafieldname: str) -> "ET.Element | None":
    """The bound ``<control>`` for ``datafieldname`` anywhere on the form, or None."""
    for control in root.iter("control"):
        if control.get("datafieldname") == datafieldname:
            return control
    return None


def _resolve_target_section(
    root: "ET.Element", tab: str | None, section: str | None
) -> "ET.Element":
    """Pick the ``<section>`` to place a field in.

    Defaults to the first section of the first tab; ``tab`` / ``section`` narrow
    by name or id. Raises ``D365Error`` naming the available choices when a
    requested tab/section is absent.
    """
    tabs = root.findall("./tabs/tab")
    if not tabs:
        raise D365Error("Form has no <tab> layout; cannot place a field.")
    if tab is None:
        target_tab = tabs[0]
    else:
        target_tab = next(
            (t for t in tabs
             if t.get("name") == tab or _id_matches(t.get("id"), tab)), None)
        if target_tab is None:
            names = ", ".join(t.get("name") or "?" for t in tabs)
            raise D365Error(f"No tab {tab!r} on the form. Tabs: {names}.")
    sections = target_tab.findall("./columns/column/sections/section")
    if not sections:
        raise D365Error(f"Tab {target_tab.get('name')!r} has no <section>.")
    if section is None:
        return sections[0]
    target = next(
        (s for s in sections
         if s.get("name") == section or _id_matches(s.get("id"), section)), None)
    if target is None:
        names = ", ".join(s.get("name") or "?" for s in sections)
        raise D365Error(
            f"No section {section!r} in tab {target_tab.get('name')!r}. "
            f"Sections: {names}.")
    return target


def _append_cell(section: "ET.Element", cell: "ET.Element") -> None:
    """Append ``cell`` as a new single-cell ``<row>`` in the section's ``<rows>``."""
    rows = section.find("rows")
    if rows is None:
        rows = ET.SubElement(section, "rows")
    row = ET.SubElement(rows, "row")
    row.append(cell)


def _build_field_cell(datafieldname: str, classid: str, label: str) -> "ET.Element":
    """A fresh bound-field ``<cell>`` (fresh id, label, control) for the field."""
    cell = ET.Element("cell")
    cell.set("id", _fresh_cell_id())
    labels = ET.SubElement(cell, "labels")
    lab = ET.SubElement(labels, "label")
    lab.set("description", label)
    lab.set("languagecode", _LABEL_LANGUAGECODE)
    control = ET.SubElement(cell, "control")
    control.set("id", datafieldname)
    control.set("classid", classid)
    control.set("datafieldname", datafieldname)
    return cell


def add_field_to_formxml(
    formxml: str, *, datafieldname: str, classid: str, label: str,
    tab: str | None = None, section: str | None = None,
) -> str:
    """Return ``formxml`` with a new bound-field ``<cell>`` for ``datafieldname``.

    Inserts into the target section (default: first section of the first tab) as
    a new single-cell row carrying a fresh cell id and a ``<control>`` bound by
    ``datafieldname`` with the resolved ``classid``. Raises ``D365Error`` if the
    field is already on the form (no silent duplicate) or the tab/section is
    absent.
    """
    root = _parse_formxml(formxml)
    if _find_field_control(root, datafieldname) is not None:
        raise D365Error(
            f"Field {datafieldname!r} is already on the form; refusing to "
            f"duplicate it. Use set-field to move it.")
    target = _resolve_target_section(root, tab, section)
    _append_cell(target, _build_field_cell(datafieldname, classid, label))
    return ET.tostring(root, encoding="unicode")


def _parent_map(root: "ET.Element") -> "dict[ET.Element, ET.Element]":
    """Map each element to its parent (etree elements carry no parent pointer)."""
    return {child: parent for parent in root.iter() for child in parent}


def _detach_field_cell(
    root: "ET.Element", parents: "dict[ET.Element, ET.Element]", datafieldname: str
) -> "ET.Element":
    """Remove and return the ``<cell>`` holding ``datafieldname``'s control,
    tidying an emptied ``<row>``. Raises ``D365Error`` if the field is absent."""
    control = _find_field_control(root, datafieldname)
    if control is None:
        raise D365Error(f"Field {datafieldname!r} is not on the form.")
    cell = parents.get(control)
    row = parents.get(cell) if cell is not None else None
    if cell is None or row is None:
        raise D365Error(
            f"Field {datafieldname!r} is not in a removable cell/row layout.")
    row.remove(cell)
    if not row.findall("cell"):  # tidy the now-empty row
        rows = parents.get(row)
        if rows is not None:
            rows.remove(row)
    return cell


def remove_field_from_formxml(formxml: str, *, datafieldname: str) -> str:
    """Return ``formxml`` with ``datafieldname``'s ``<cell>`` removed.

    Removes exactly the targeted control's cell (tidying an emptied row) and
    nothing else. Raises ``D365Error`` if the field is not on the form.
    """
    root = _parse_formxml(formxml)
    _detach_field_cell(root, _parent_map(root), datafieldname)
    return ET.tostring(root, encoding="unicode")


def move_field_in_formxml(
    formxml: str, *, datafieldname: str,
    tab: str | None = None, section: str | None = None,
) -> str:
    """Return ``formxml`` with ``datafieldname``'s existing ``<cell>`` relocated
    to the target tab/section, preserving the cell (its id and control).

    Raises ``D365Error`` if the field is not already on the form (the caller
    should suggest add-field).
    """
    root = _parse_formxml(formxml)
    target = _resolve_target_section(root, tab, section)
    cell = _detach_field_cell(root, _parent_map(root), datafieldname)
    _append_cell(target, cell)
    return ET.tostring(root, encoding="unicode")


def read_entity_forms(
    backend: D365Backend,
    entity_logical_name: str,
    *,
    form_types: tuple[int, ...] = (FORM_TYPE_MAIN,),
) -> list[dict[str, Any]]:
    """Read an entity's forms as projection dicts.

    Defaults to Main forms only (``type=2``); pass ``form_types`` to widen.
    Returns dicts with keys ``formid, name, objecttypecode, type, formxml,
    description, isdefault``.
    """
    if not form_types:
        raise D365Error("form_types must not be empty.")
    type_clause = " or ".join(f"type eq {t}" for t in form_types)
    filt = f"objecttypecode eq {odata_literal(entity_logical_name)} and ({type_clause})"
    rows = backend.get_collection(
        "systemforms",
        params={"$select": _FORM_SELECT, "$filter": filt},
    )
    result: list[dict[str, Any]] = []
    for row in rows:
        result.append({
            "formid": row.get("formid"),
            "name": row.get("name", ""),
            "objecttypecode": row.get("objecttypecode"),
            "type": row.get("type"),
            "formxml": row.get("formxml") or "",
            "description": row.get("description"),
            "isdefault": bool(row.get("isdefault", False)),
        })
    return result


def _select_form(forms_list: list[dict[str, Any]], form: str | None) -> dict[str, Any]:
    """Pick exactly one form by ``--form`` name/id, or the entity's primary form.

    With ``--form`` set, matches by name or id (errors on no / ambiguous match).
    Without it, uses the sole main form, else the sole ``isdefault`` (primary)
    form when one stands out; only when the primary is still ambiguous does it
    raise ``D365Error`` asking for ``--form``.
    """
    if form is not None:
        matches = [f for f in forms_list
                   if f.get("name") == form or _id_matches(str(f.get("formid")), form)]
        if not matches:
            raise D365Error(f"No form matching {form!r} found.")
        if len(matches) > 1:
            names = ", ".join(f"{m.get('name')!r} ({m.get('formid')})" for m in matches)
            raise D365Error(
                f"Multiple forms matching {form!r}: {names}. "
                f"Pass --form <name|id> to choose one.")
        return matches[0]
    if not forms_list:
        raise D365Error("No form for this entity found.")
    if len(forms_list) == 1:
        return forms_list[0]
    defaults = [f for f in forms_list if f.get("isdefault")]
    if len(defaults) == 1:
        return defaults[0]
    names = ", ".join(f"{m.get('name')!r} ({m.get('formid')})" for m in forms_list)
    raise D365Error(
        f"Multiple main forms for this entity: {names}. "
        f"Pass --form <name|id> to choose one.")


def _attr_label(info: dict[str, Any], fallback: str) -> str:
    """The attribute's localized display label, falling back to its logical name.

    Delegates to the shared ``metadata.label_text`` so the ``UserLocalizedLabel`` →
    ``LocalizedLabels`` fallback is handled consistently with the rest of the CLI.
    """
    display = info.get("DisplayName")
    if not isinstance(display, dict):
        return fallback
    return label_text(cast("dict[str, Any]", display)) or fallback


_DRY_RUN_FLAG = {
    "add-field": "would_add",
    "remove-field": "would_remove",
    "set-field": "would_move",
}


def _commit_form_change(
    backend: D365Backend, form_row: dict[str, Any], new_formxml: str,
    attribute: str, *, action: str, publish: bool, solution: str | None,
    extra: dict[str, Any] | None = None,
) -> dict[str, Any]:
    """PATCH a form's ``formxml`` (or preview under dry-run), then maybe publish.

    Under ``backend.dry_run`` no write is issued: the form/attribute reads have
    already run and the returned dict carries a ``would_*`` flag previewing the
    change. Otherwise the new FormXml is PATCHed onto the systemform and, if
    ``publish`` is set, published.
    """
    formid = form_row.get("formid")
    out: dict[str, Any] = {
        "formid": formid, "form": form_row.get("name"),
        "attribute": attribute, "action": action,
    }
    if extra:
        out.update({k: v for k, v in extra.items() if v is not None})
    if backend.dry_run:
        out["_dry_run"] = True
        out[_DRY_RUN_FLAG[action]] = True
        return out
    headers = {"MSCRM.SolutionUniqueName": solution} if solution else None
    backend.patch(f"systemforms({formid})",
                  json_body={"formxml": new_formxml}, extra_headers=headers)
    out["updated"] = True
    maybe_publish(backend, out, publish)
    return out


def add_form_field(
    backend: D365Backend, entity: str, attribute: str, *,
    form: str | None = None, tab: str | None = None, section: str | None = None,
    publish: bool = False, solution: str | None = None,
) -> dict[str, Any]:
    """Add ``attribute`` to an entity form, resolving its control ``classid`` from
    live metadata. Errors if the attribute is absent, its type is unmapped, or
    the field is already on the form."""
    info = attribute_info(backend, entity, attribute)
    attr_type = info.get("AttributeType") or ""
    classid = classid_for_attribute_type(attr_type)
    label = _attr_label(info, attribute)
    form_row = _select_form(read_entity_forms(backend, entity), form)
    new_xml = add_field_to_formxml(
        form_row.get("formxml", ""), datafieldname=attribute, classid=classid,
        label=label, tab=tab, section=section)
    return _commit_form_change(
        backend, form_row, new_xml, attribute, action="add-field",
        publish=publish, solution=solution,
        extra={"classid": classid, "attribute_type": attr_type,
               "tab": tab, "section": section})


def remove_form_field(
    backend: D365Backend, entity: str, attribute: str, *,
    form: str | None = None, publish: bool = False, solution: str | None = None,
) -> dict[str, Any]:
    """Remove ``attribute``'s field from an entity form. Errors if absent."""
    form_row = _select_form(read_entity_forms(backend, entity), form)
    new_xml = remove_field_from_formxml(
        form_row.get("formxml", ""), datafieldname=attribute)
    return _commit_form_change(
        backend, form_row, new_xml, attribute, action="remove-field",
        publish=publish, solution=solution)


def set_form_field(
    backend: D365Backend, entity: str, attribute: str, *,
    form: str | None = None, tab: str | None = None, section: str | None = None,
    publish: bool = False, solution: str | None = None,
) -> dict[str, Any]:
    """Relocate ``attribute``'s existing field to a different tab/section. Errors
    (suggesting add-field) if the field is not already on the form."""
    form_row = _select_form(read_entity_forms(backend, entity), form)
    try:
        new_xml = move_field_in_formxml(
            form_row.get("formxml", ""), datafieldname=attribute,
            tab=tab, section=section)
    except D365Error as exc:
        if "is not on the form" in str(exc):
            raise D365Error(
                f"Field {attribute!r} is not on the form; use add-field to add "
                f"it first.") from exc
        raise
    return _commit_form_change(
        backend, form_row, new_xml, attribute, action="set-field",
        publish=publish, solution=solution, extra={"tab": tab, "section": section})


def clone_form_to_entity(
    backend: D365Backend,
    form: dict[str, Any],
    new_entity: str,
    *,
    publish: bool = False,
    solution: str | None = None,
) -> dict[str, Any]:
    """Create a systemform on ``new_entity`` from a ``read_entity_forms`` dict.

    Retargets ``formxml``, regenerates its internal registration GUIDs (so repeat
    clones of one source never collide on on-prem id uniqueness — see
    ``regenerate_form_clone_ids``), and sets ``objecttypecode`` to the clone.
    Read-back is via the OData-EntityId header, matching the view/metadata-write
    precedent.
    """
    src_entity = form.get("objecttypecode")
    if not src_entity:
        raise D365Error("form is missing objecttypecode; cannot retarget.")
    body: dict[str, Any] = {
        "name": form.get("name"),
        "objecttypecode": new_entity,
        "type": form.get("type"),
        "formxml": regenerate_form_clone_ids(retarget_formxml(
            form.get("formxml", ""), src_entity=src_entity, dst_entity=new_entity)),
    }
    if form.get("description") is not None:
        body["description"] = form["description"]
    headers = {"MSCRM.SolutionUniqueName": solution} if solution else None
    result = as_dict(backend.post("systemforms", json_body=body, extra_headers=headers))
    if result.get("_dry_run"):
        return result

    entity_id_url = result.get("_entity_id_url") or ""
    formid = result.get("_entity_id")
    out: dict[str, Any] = {
        "created": True,
        "name": form.get("name", ""),
        "formid": formid,
        "type": form.get("type"),
        "objecttypecode": new_entity,
    }
    if formid is None:
        out["form_lookup_error"] = (
            f"Could not parse formid from response: {entity_id_url!r}")
    maybe_publish(backend, out, publish)
    return out
