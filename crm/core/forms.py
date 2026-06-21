"""Read and clone systemform records.

Mirrors views.py (read_entity_views / create_view). A form is read from the
`systemforms` set, its formxml entity-retargeted, and recreated against a new
`objecttypecode`. Form retarget logic is isolated here so it is testable
independently of the clone orchestrator, and so a future `crm form` command
can wrap it the way `view` wraps `views.py`.
"""

from __future__ import annotations

import re
import xml.etree.ElementTree as ET
from typing import Any, cast

from crm.core import webresource, xml_edit
from crm.core.metadata import attribute_info, label_text, maybe_publish
from crm.utils.d365_backend import D365Backend, D365Error, as_dict, odata_literal

FORM_TYPE_MAIN = 2

# User-facing subset of the `systemform_type` global choice (Dataverse), keyed by
# a stable lowercase token → optionset value. Only the form types worth filtering
# on are exposed; backup/internal types (Preview, MainBackup, …) are omitted —
# `form list --all` (form_types=None) returns every type regardless. Values are
# from the SystemForm `type` reference, not guessed:
# learn.microsoft.com/power-apps/developer/data-platform/reference/entities/systemform
FORM_TYPE_BY_NAME: dict[str, int] = {
    "dashboard": 0,
    "main": FORM_TYPE_MAIN,
    "quickview": 6,
    "quickcreate": 7,
    "dialog": 8,
    "card": 11,
}

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
        (?P<brace>\{)?(?P<guid>""" + xml_edit.GUID + r""")(?(brace)\})(?P=q)""",
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
    new_xml, mapping = xml_edit.regenerate_guids(formxml, _REGEN_ATTR_RE)
    xml_edit.assert_external_guids_intact(
        formxml, new_xml, regenerated=mapping,
        message="form-clone id regeneration altered a non-target GUID (external "
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

# Layout columns are constrained 1–4 (the Dynamics form designer's range): a tab
# may host 1–4 layout columns, a section may lay its cells out in 1–4 columns.
_MAX_LAYOUT_COLUMNS = 4


def _fresh_cell_id() -> str:
    """A brace-wrapped uuid4 for a newly inserted cell, matching FormXml style."""
    return xml_edit.fresh_guid()


def _parse_formxml(formxml: str) -> "ET.Element":
    """Parse FormXml, turning a malformed payload into a ``D365Error`` so the CLI
    emits its standard error envelope rather than a raw ``ParseError`` traceback."""
    return xml_edit.parse_xml(formxml, label="form's FormXml")


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


def _resolve_target_tab(root: "ET.Element", tab: str | None) -> "ET.Element":
    """Pick the ``<tab>`` to operate on: the first tab by default, or the one
    matched by ``tab`` (name or id). Raises ``D365Error`` naming the available
    tabs when a requested tab is absent."""
    tabs = root.findall("./tabs/tab")
    if not tabs:
        raise D365Error("Form has no <tab> layout.")
    if tab is None:
        return tabs[0]
    target_tab = next(
        (t for t in tabs
         if t.get("name") == tab or _id_matches(t.get("id"), tab)), None)
    if target_tab is None:
        names = ", ".join(t.get("name") or "?" for t in tabs)
        raise D365Error(f"No tab {tab!r} on the form. Tabs: {names}.")
    return target_tab


def _resolve_target_section(
    root: "ET.Element", tab: str | None, section: str | None
) -> "ET.Element":
    """Pick the ``<section>`` to place a field in.

    Defaults to the first section of the first tab; ``tab`` / ``section`` narrow
    by name or id. Raises ``D365Error`` naming the available choices when a
    requested tab/section is absent.
    """
    target_tab = _resolve_target_tab(root, tab)
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
    return xml_edit.serialize_xml(root)


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
    return xml_edit.serialize_xml(root)


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
    return xml_edit.serialize_xml(root)


# --- FormXml event-handler & library wiring (issue #459) ------------------------
#
# JS event handlers and script libraries live OUTSIDE the <tabs> layout, in the
# top-level <events> and <formLibraries> elements. The form element is xs:all, so
# the position of these among the form's children is free — we append. A handler
# is a <Handler> under <event>/<Handlers>; we target the customizer-owned
# <Handlers>, never the platform's sibling <InternalHandlers>. Handler execution
# follows the <Handlers> sequence order, so a new handler is always appended (the
# existing order is preserved). Fresh handlerUniqueId/libraryUniqueId GUIDs are
# minted brace-wrapped to match the rest of the FormXml the CLI writes; lookups
# tolerate braces because a server read-back may return them unbraced. classid is
# never touched, so each transform re-asserts classid integrity as a backstop.
#
# The schema (xs:string) accepts both braced and unbraced ids; FormXml booleans
# are the literals "true"/"false".

EVENT_CHOICES = ("onload", "onsave", "onchange")


def _xml_bool(value: str | None, *, default: bool) -> bool:
    """Read a FormXml boolean attribute (``"true"``/``"false"``)."""
    if value is None:
        return default
    return value.strip().lower() == "true"


def _ensure_library_registered(root: "ET.Element", library_name: str) -> None:
    """Register ``library_name`` in ``<formLibraries>`` if absent (deduped no-op).

    A handler resolves its function through a registered library, so wiring a
    handler also ensures its library is present; ``add-library`` reuses this for
    the standalone case. ``<formLibraries>`` requires at least one ``<Library>``,
    so it is only created alongside the entry it holds.
    """
    libs = root.find("formLibraries")
    if libs is not None and any(
            lib.get("name") == library_name for lib in libs.findall("Library")):
        return
    if libs is None:
        libs = ET.SubElement(root, "formLibraries")
    lib = ET.SubElement(libs, "Library")
    lib.set("name", library_name)
    lib.set("libraryUniqueId", xml_edit.fresh_guid())


def add_library_to_formxml(formxml: str, *, library_name: str) -> str:
    """Return ``formxml`` with ``library_name`` registered in ``<formLibraries>``.

    Idempotent: a library already registered is left untouched (no duplicate
    ``<Library>``).
    """
    root = _parse_formxml(formxml)
    _ensure_library_registered(root, library_name)
    new_xml = xml_edit.serialize_xml(root)
    xml_edit.assert_classids_intact(formxml, new_xml)
    return new_xml


def _find_event(
    root: "ET.Element", *, event: str, field: str | None
) -> "ET.Element | None":
    """The ``<event>`` for this event name (matching ``attribute`` for onchange)."""
    events = root.find("events")
    if events is None:
        return None
    for ev in events.findall("event"):
        if ev.get("name") != event:
            continue
        if event == "onchange":
            if (ev.get("attribute") or "") == (field or ""):
                return ev
        else:
            return ev
    return None


def _ensure_event(root: "ET.Element", *, event: str, field: str | None) -> "ET.Element":
    """The existing ``<event>`` for this event, or a freshly appended one.

    Merging into the existing element (never a duplicate ``<event>``) keeps a
    single event node per form event, as the platform requires.
    """
    ev = _find_event(root, event=event, field=field)
    if ev is not None:
        return ev
    events = root.find("events")
    if events is None:
        events = ET.SubElement(root, "events")
    ev = ET.SubElement(events, "event")
    ev.set("name", event)
    if event == "onchange" and field:
        ev.set("attribute", field)
    return ev


def _validate_event_field(
    root: "ET.Element", event: str, field: str | None
) -> None:
    """Enforce the onchange/``--field`` pairing (T2).

    onchange targets a specific attribute, so it needs a ``--field`` that is
    actually on the form; the other events take no field.
    """
    if event == "onchange":
        if not field:
            raise D365Error("onchange handlers require --field <attribute>.")
        if _find_field_control(root, field) is None:
            raise D365Error(
                f"Field {field!r} is not on the form; add it before wiring an "
                f"onchange handler to it.")
    elif field:
        raise D365Error(f"--field applies only to onchange, not {event!r}.")


def _handler_label(event: str, function: str, field: str | None) -> str:
    """Human label for a handler in error text (includes the field for onchange)."""
    where = f"{event} of {field!r}" if event == "onchange" and field else event
    return f"{function!r} on {where}"


def add_handler_to_formxml(
    formxml: str, *, event: str, function: str, library_name: str,
    field: str | None = None, params: "tuple[str, ...]" = (),
    pass_context: bool = True, enabled: bool = True,
) -> str:
    """Return ``formxml`` with a ``<Handler>`` wiring ``function`` to ``event``.

    Ensures ``library_name`` is registered, merges the handler into the event's
    ``<Handlers>`` (creating the ``<event>``/``<Handlers>`` only when absent),
    appends it last so existing handler order is preserved, and mints a fresh
    ``handlerUniqueId``. Raises ``D365Error`` for an unsupported event, a bad
    onchange/``--field`` pairing, or a handler already wired (no silent
    duplicate).
    """
    if event not in EVENT_CHOICES:
        raise D365Error(
            f"Unsupported event {event!r}. Choose from: {', '.join(EVENT_CHOICES)}.")
    root = _parse_formxml(formxml)
    _validate_event_field(root, event, field)
    _ensure_library_registered(root, library_name)
    ev = _ensure_event(root, event=event, field=field)
    handlers = ev.find("Handlers")
    if handlers is None:
        handlers = ET.SubElement(ev, "Handlers")
    if any(h.get("functionName") == function for h in handlers.findall("Handler")):
        raise D365Error(
            f"Handler {_handler_label(event, function, field)} is already wired; "
            f"refusing to duplicate it.")
    handler = ET.SubElement(handlers, "Handler")
    handler.set("functionName", function)
    handler.set("libraryName", library_name)
    handler.set("handlerUniqueId", xml_edit.fresh_guid())
    handler.set("enabled", "true" if enabled else "false")
    handler.set("passExecutionContext", "true" if pass_context else "false")
    if params:
        handler.set("parameters", ",".join(params))
    new_xml = xml_edit.serialize_xml(root)
    xml_edit.assert_classids_intact(formxml, new_xml)
    return new_xml


def remove_handler_from_formxml(
    formxml: str, *, event: str, function: str, field: str | None = None,
) -> str:
    """Return ``formxml`` with the ``function`` handler removed from ``event``.

    Identifies the handler by event + function (+ field for onchange) and tidies
    the now-empty ``<Handlers>``/``<event>``/``<events>`` so no invalid empty
    container is left behind. Raises ``D365Error`` if the handler is absent.
    """
    if event not in EVENT_CHOICES:
        raise D365Error(
            f"Unsupported event {event!r}. Choose from: {', '.join(EVENT_CHOICES)}.")
    # onchange handlers are keyed by event + field, so removal needs the field to
    # name one unambiguously (symmetry with add-handler); the others take none.
    if event == "onchange" and not field:
        raise D365Error("onchange handler removal requires --field <attribute>.")
    if event != "onchange" and field:
        raise D365Error(f"--field applies only to onchange, not {event!r}.")
    root = _parse_formxml(formxml)
    ev = _find_event(root, event=event, field=field)
    handlers = ev.find("Handlers") if ev is not None else None
    target = None
    if handlers is not None:
        target = next(
            (h for h in handlers.findall("Handler")
             if h.get("functionName") == function), None)
    if ev is None or handlers is None or target is None:
        raise D365Error(
            f"No handler {_handler_label(event, function, field)} on the form.")
    handlers.remove(target)
    if not handlers.findall("Handler"):
        ev.remove(handlers)
    # An <event> with neither <Handlers> nor <InternalHandlers> is inert; drop it,
    # then drop an emptied <events> (an empty <events> is schema-invalid).
    events = root.find("events")
    if ev.find("Handlers") is None and ev.find("InternalHandlers") is None:
        if events is not None and ev in list(events):
            events.remove(ev)
    if events is not None and not events.findall("event"):
        root.remove(events)
    new_xml = xml_edit.serialize_xml(root)
    xml_edit.assert_classids_intact(formxml, new_xml)
    return new_xml


def list_handlers_in_formxml(formxml: str) -> "list[dict[str, Any]]":
    """List the customizer-wired ``<Handler>`` entries, in form order.

    Reports only the editable ``<Handlers>`` (not the platform-owned
    ``<InternalHandlers>``), one row per handler with its event, the onchange
    field (or ``None``), function, library and flags.
    """
    root = _parse_formxml(formxml)
    events = root.find("events")
    out: list[dict[str, Any]] = []
    if events is None:
        return out
    for ev in events.findall("event"):
        handlers = ev.find("Handlers")
        if handlers is None:
            continue
        for h in handlers.findall("Handler"):
            # Read defaults mirror the platform's absent-attribute semantics: a
            # handler with no `enabled` is active, one with no `passExecutionContext`
            # does not receive it. (CLI-written handlers always set both explicitly,
            # so they round-trip exactly; the defaults only cover externally-authored
            # handlers that omit an attribute.)
            out.append({
                "event": ev.get("name") or "",
                "field": ev.get("attribute"),
                "function": h.get("functionName"),
                "library": h.get("libraryName"),
                "enabled": _xml_bool(h.get("enabled"), default=True),
                "pass_context": _xml_bool(
                    h.get("passExecutionContext"), default=False),
                "handler_unique_id": h.get("handlerUniqueId"),
            })


# --- FormXml tab / section structure surgery (add / remove / rename / move) -----
#
# Like the field transforms above, these parse the FormXml as a tree and touch
# only the tab/section they target. They additionally run the shared protected-id
# guard (#275) over the result: a tab/section edit must leave every *sibling*
# tab/section/control GUID byte-identical, regenerating no id but the fresh ones a
# new tab/section carries. The guard is fed the precise ids the edit introduced or
# removed, so an accidental mutation of any other GUID is still caught.

_SIBLING_GUARD_MSG = (
    "tab/section edit altered a sibling GUID (an external reference or another "
    "element's id); refusing to POST a possibly corrupt form.")


def _validate_columns(columns: int) -> None:
    """Reject a layout-column count outside the designer's 1–4 range."""
    if not 1 <= columns <= _MAX_LAYOUT_COLUMNS:
        raise D365Error(
            f"--columns must be between 1 and {_MAX_LAYOUT_COLUMNS}; got {columns}.")


def _build_labels(description: str) -> "ET.Element":
    """A ``<labels>`` element carrying one 1033 ``<label>`` with ``description``."""
    labels = ET.Element("labels")
    lab = ET.SubElement(labels, "label")
    lab.set("description", description)
    lab.set("languagecode", _LABEL_LANGUAGECODE)
    return labels


def _build_section(name: str, label: str, columns: int) -> "ET.Element":
    """A fresh ``<section>`` (fresh id, ``IsUserDefined=1``, empty ``<rows>``)."""
    section = ET.Element("section")
    section.set("name", name)
    section.set("id", xml_edit.fresh_guid())
    section.set("showlabel", "true")
    section.set("columns", str(columns))
    section.set("IsUserDefined", "1")
    section.append(_build_labels(label))
    ET.SubElement(section, "rows")
    return section


def _build_tab(name: str, label: str, columns: int) -> "ET.Element":
    """A fresh ``<tab>`` (fresh id, ``IsUserDefined=1``) with ``columns`` layout
    columns; the first column carries a non-empty starter ``<section>`` so the tab
    renders (an empty tab is XSD-valid but renders broken)."""
    tab = ET.Element("tab")
    tab.set("name", name)
    tab.set("id", xml_edit.fresh_guid())
    tab.set("IsUserDefined", "1")
    tab.append(_build_labels(label))
    columns_el = ET.SubElement(tab, "columns")
    width = f"{100 // columns}%"
    for i in range(columns):
        col = ET.SubElement(columns_el, "column")
        col.set("width", width)
        sections = ET.SubElement(col, "sections")
        if i == 0:  # non-empty skeleton in the first column
            sections.append(_build_section(f"{name}_section", label, 1))
    return tab


def _set_label(element: "ET.Element", label: str) -> None:
    """Set the 1033 ``<label>`` description of a tab/section, creating the
    ``<labels>``/``<label>`` if absent (only the base-language label is touched)."""
    labels = element.find("labels")
    if labels is None:
        element.insert(0, _build_labels(label))
        return
    label_els = labels.findall("label")
    target = next(
        (lab for lab in label_els
         if lab.get("languagecode") == _LABEL_LANGUAGECODE), None)
    if target is None:
        target = label_els[0] if label_els else ET.SubElement(labels, "label")
        target.set("languagecode", _LABEL_LANGUAGECODE)
    target.set("description", label)


def _element_guids(element: "ET.Element") -> "set[str]":
    """Every GUID under ``element`` (lowercased) — the ids an add/remove changes."""
    return xml_edit.guid_set(xml_edit.serialize_xml(element))


def _bound_fields_under(element: "ET.Element") -> "list[str]":
    """Datafieldnames of bound ``<control>``s anywhere under ``element`` — the
    fields a tab/section remove would orphan."""
    out: list[str] = []
    for control in element.iter("control"):
        datafieldname = control.get("datafieldname")
        if datafieldname:
            out.append(datafieldname)
    return out


def _sections_parent(
    target_tab: "ET.Element", section: "ET.Element"
) -> "ET.Element | None":
    """The ``<sections>`` element (within one of ``target_tab``'s columns) that
    directly contains ``section``."""
    for sections in target_tab.findall("./columns/column/sections"):
        if section in list(sections):
            return sections
    return None


def _assert_siblings_intact(
    before: str, after: str, *, changed: "set[str] | frozenset[str]" = frozenset()
) -> None:
    """Assert the edit changed only the ids it intentionally added/removed.

    ``changed`` is excluded from the before/after GUID multiset comparison (the
    shared protected-id guard, #275); every *other* GUID must be byte-identical,
    so a stray rewrite of a sibling id or external reference still raises.
    """
    delta = {g.lower(): g.lower() for g in changed}
    xml_edit.assert_external_guids_intact(
        before, after, regenerated=delta, message=_SIBLING_GUARD_MSG)


def add_tab_to_formxml(
    formxml: str, *, name: str, label: str, columns: int = 1,
    after: str | None = None,
) -> str:
    """Return ``formxml`` with a new tab (carrying a starter section) appended, or
    inserted after the ``after`` sibling tab. Raises ``D365Error`` if a tab of
    that name already exists or ``columns`` is out of range."""
    _validate_columns(columns)
    root = _parse_formxml(formxml)
    tabs_el = root.find("./tabs")
    if tabs_el is None:
        raise D365Error("Form has no <tabs> container; cannot add a tab.")
    if any(t.get("name") == name for t in tabs_el.findall("tab")):
        raise D365Error(f"Tab {name!r} already exists on the form.")
    new_tab = _build_tab(name, label, columns)
    changed = _element_guids(new_tab)
    if after is None:
        tabs_el.append(new_tab)
    else:
        anchor = _resolve_target_tab(root, after)
        tabs_el.insert(list(tabs_el).index(anchor) + 1, new_tab)
    out = xml_edit.serialize_xml(root)
    _assert_siblings_intact(formxml, out, changed=changed)
    return out


def remove_tab_from_formxml(
    formxml: str, *, tab: str, force: bool = False
) -> str:
    """Return ``formxml`` with the ``tab`` (name or id) removed. Refuses to remove
    the only tab, or a tab still holding bound fields unless ``force``."""
    root = _parse_formxml(formxml)
    tabs_el = root.find("./tabs")
    target = _resolve_target_tab(root, tab)
    if tabs_el is None or len(tabs_el.findall("tab")) <= 1:
        raise D365Error(
            "Refusing to remove the only tab; a form must keep at least one tab.")
    orphans = _bound_fields_under(target)
    if orphans and not force:
        raise D365Error(
            f"Tab {tab!r} still holds bound fields: {', '.join(orphans)}. "
            f"Pass --force to remove it and orphan them.")
    changed = _element_guids(target)
    tabs_el.remove(target)
    out = xml_edit.serialize_xml(root)
    _assert_siblings_intact(formxml, out, changed=changed)
    return out


def rename_tab_in_formxml(formxml: str, *, tab: str, label: str) -> str:
    """Return ``formxml`` with the ``tab``'s display label set to ``label`` (the
    logical ``name`` is left intact, since form scripts bind to it)."""
    root = _parse_formxml(formxml)
    _set_label(_resolve_target_tab(root, tab), label)
    out = xml_edit.serialize_xml(root)
    _assert_siblings_intact(formxml, out)
    return out


def move_tab_in_formxml(
    formxml: str, *, tab: str, after: str | None = None
) -> str:
    """Return ``formxml`` with the ``tab`` reordered: to the front by default, or
    immediately after the ``after`` sibling tab."""
    root = _parse_formxml(formxml)
    tabs_el = root.find("./tabs")
    target = _resolve_target_tab(root, tab)
    if tabs_el is None:
        raise D365Error("Form has no <tabs> container.")
    tabs_el.remove(target)
    if after is None:
        tabs_el.insert(0, target)
    else:
        anchor = _resolve_target_tab(root, after)
        tabs_el.insert(list(tabs_el).index(anchor) + 1, target)
    out = xml_edit.serialize_xml(root)
    _assert_siblings_intact(formxml, out)
    return out


def add_section_to_formxml(
    formxml: str, *, name: str, label: str, tab: str | None = None,
    columns: int = 1, after: str | None = None,
) -> str:
    """Return ``formxml`` with a new section added to the target tab (default: the
    first tab), appended to its first column or inserted after the ``after``
    sibling section. Raises ``D365Error`` on a duplicate name or bad ``columns``."""
    _validate_columns(columns)
    root = _parse_formxml(formxml)
    target_tab = _resolve_target_tab(root, tab)
    existing = target_tab.findall("./columns/column/sections/section")
    if any(s.get("name") == name for s in existing):
        raise D365Error(
            f"Section {name!r} already exists in tab {target_tab.get('name')!r}.")
    new_section = _build_section(name, label, columns)
    changed = _element_guids(new_section)
    if after is None:
        sections_el = target_tab.find("./columns/column/sections")
        if sections_el is None:
            raise D365Error(
                f"Tab {target_tab.get('name')!r} has no <sections> container.")
        sections_el.append(new_section)
    else:
        anchor = next(
            (s for s in existing
             if s.get("name") == after or _id_matches(s.get("id"), after)), None)
        if anchor is None:
            names = ", ".join(s.get("name") or "?" for s in existing)
            raise D365Error(
                f"No section {after!r} in tab {target_tab.get('name')!r} to place "
                f"after. Sections: {names}.")
        parent = _sections_parent(target_tab, anchor)
        assert parent is not None  # anchor came from this tab's sections
        parent.insert(list(parent).index(anchor) + 1, new_section)
    out = xml_edit.serialize_xml(root)
    _assert_siblings_intact(formxml, out, changed=changed)
    return out


def remove_section_from_formxml(
    formxml: str, *, section: str, tab: str | None = None, force: bool = False,
) -> str:
    """Return ``formxml`` with the ``section`` (in the target tab) removed. Refuses
    a section still holding bound fields unless ``force``."""
    root = _parse_formxml(formxml)
    target_tab = _resolve_target_tab(root, tab)
    target = _resolve_target_section(root, tab, section)
    orphans = _bound_fields_under(target)
    if orphans and not force:
        raise D365Error(
            f"Section {section!r} still holds bound fields: {', '.join(orphans)}. "
            f"Pass --force to remove it and orphan them.")
    parent = _sections_parent(target_tab, target)
    assert parent is not None  # target came from this tab's sections
    changed = _element_guids(target)
    parent.remove(target)
    out = xml_edit.serialize_xml(root)
    _assert_siblings_intact(formxml, out, changed=changed)
    return out


def rename_section_in_formxml(
    formxml: str, *, section: str, label: str, tab: str | None = None,
) -> str:
    """Return ``formxml`` with the ``section``'s display label set to ``label``."""
    root = _parse_formxml(formxml)
    _set_label(_resolve_target_section(root, tab, section), label)
    out = xml_edit.serialize_xml(root)
    _assert_siblings_intact(formxml, out)
    return out


def move_section_in_formxml(
    formxml: str, *, section: str, tab: str | None = None,
    after: str | None = None,
) -> str:
    """Return ``formxml`` with the ``section`` reordered within its tab: to the
    front of its column by default, or after the ``after`` sibling section."""
    root = _parse_formxml(formxml)
    target_tab = _resolve_target_tab(root, tab)
    target = _resolve_target_section(root, tab, section)
    parent = _sections_parent(target_tab, target)
    assert parent is not None  # target came from this tab's sections
    parent.remove(target)
    if after is None:
        parent.insert(0, target)
    else:
        remaining = target_tab.findall("./columns/column/sections/section")
        anchor = next(
            (s for s in remaining
             if s.get("name") == after or _id_matches(s.get("id"), after)), None)
        if anchor is None:
            names = ", ".join(s.get("name") or "?" for s in remaining)
            raise D365Error(
                f"No section {after!r} in tab {target_tab.get('name')!r} to place "
                f"after. Sections: {names}.")
        anchor_parent = _sections_parent(target_tab, anchor)
        assert anchor_parent is not None
        anchor_parent.insert(list(anchor_parent).index(anchor) + 1, target)
    out = xml_edit.serialize_xml(root)
    _assert_siblings_intact(formxml, out)
    return out


def read_entity_forms(
    backend: D365Backend,
    entity_logical_name: str,
    *,
    form_types: tuple[int, ...] | None = (FORM_TYPE_MAIN,),
) -> list[dict[str, Any]]:
    """Read an entity's forms as projection dicts.

    Defaults to Main forms only (``type=2``); pass ``form_types`` to widen to
    specific systemform ``type`` values, or ``None`` to list every type (no
    ``type`` filter). Returns dicts with keys ``formid, name, objecttypecode,
    type, formxml, description, isdefault``.
    """
    filt = f"objecttypecode eq {odata_literal(entity_logical_name)}"
    if form_types is not None:
        if not form_types:
            raise D365Error("form_types must not be empty.")
        type_clause = " or ".join(f"type eq {t}" for t in form_types)
        filt = f"{filt} and ({type_clause})"
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
    "add-library": "would_add_library",
    "add-handler": "would_add_handler",
    "remove-handler": "would_remove_handler",
    "add-tab": "would_add",
    "remove-tab": "would_remove",
    "rename-tab": "would_rename",
    "move-tab": "would_move",
    "add-section": "would_add",
    "remove-section": "would_remove",
    "rename-section": "would_rename",
    "move-section": "would_move",
}


def _commit_form_change(
    backend: D365Backend, form_row: dict[str, Any], new_formxml: str,
    *, action: str, publish: bool, solution: str | None,
    extra: dict[str, Any] | None = None,
) -> dict[str, Any]:
    """PATCH a form's ``formxml`` (or preview under dry-run), then maybe publish.

    Thin forms-specific adapter over ``xml_edit.commit_xml_patch`` (the shared
    direct-PATCH commit): builds the result dict (form identity + the edit's
    ``extra`` metadata) and delegates the dry-run / PATCH / publish flow. Forms do
    not opt into the read-back T3 (``read_back=None``), preserving the original
    behavior.
    """
    out: dict[str, Any] = {
        "formid": form_row.get("formid"), "form": form_row.get("name"),
        "action": action,
    }
    if extra:
        out.update({k: v for k, v in extra.items() if v is not None})
    return xml_edit.commit_xml_patch(
        backend, entity_set="systemforms", record_id=str(form_row.get("formid")),
        column="formxml", new_xml=new_formxml, result=out,
        dry_run_flag=_DRY_RUN_FLAG[action], publish=publish, solution=solution)


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
        backend, form_row, new_xml, action="add-field",
        publish=publish, solution=solution,
        extra={"attribute": attribute, "classid": classid,
               "attribute_type": attr_type, "tab": tab, "section": section})


def remove_form_field(
    backend: D365Backend, entity: str, attribute: str, *,
    form: str | None = None, publish: bool = False, solution: str | None = None,
) -> dict[str, Any]:
    """Remove ``attribute``'s field from an entity form. Errors if absent."""
    form_row = _select_form(read_entity_forms(backend, entity), form)
    new_xml = remove_field_from_formxml(
        form_row.get("formxml", ""), datafieldname=attribute)
    return _commit_form_change(
        backend, form_row, new_xml, action="remove-field",
        publish=publish, solution=solution, extra={"attribute": attribute})


def set_form_field(
    backend: D365Backend, entity: str, attribute: str, *,
    form: str | None = None, tab: str | None = None, section: str | None = None,
    publish: bool = False, solution: str | None = None,
) -> dict[str, Any]:
    """Relocate ``attribute``'s existing field to a different tab/section. Errors
    (suggesting add-field) if the field is not already on the form."""
    form_row = _select_form(read_entity_forms(backend, entity), form)
    formxml = form_row.get("formxml", "")
    # Explicit presence pre-check: distinguish "field absent" (a user error worth a
    # hint) from any other transform failure (bad --tab/--section), which should
    # propagate unchanged — rather than fragile error-message matching.
    if _find_field_control(_parse_formxml(formxml), attribute) is None:
        raise D365Error(
            f"Field {attribute!r} is not on the form; use add-field to add it first.")
    new_xml = move_field_in_formxml(
        formxml, datafieldname=attribute, tab=tab, section=section)
    return _commit_form_change(
        backend, form_row, new_xml, action="set-field",
        publish=publish, solution=solution,
        extra={"attribute": attribute, "tab": tab, "section": section})


def add_form_tab(
    backend: D365Backend, entity: str, name: str, *, label: str | None = None,
    columns: int = 1, after: str | None = None, form: str | None = None,
    publish: bool = False, solution: str | None = None,
) -> dict[str, Any]:
    """Add a tab (with a starter section) to an entity form."""
    form_row = _select_form(read_entity_forms(backend, entity), form)
    new_xml = add_tab_to_formxml(
        form_row.get("formxml", ""), name=name, label=label or name,
        columns=columns, after=after)
    return _commit_form_change(
        backend, form_row, new_xml, action="add-tab", publish=publish,
        solution=solution,
        extra={"tab": name, "label": label or name, "columns": columns,
               "after": after})


def remove_form_tab(
    backend: D365Backend, entity: str, tab: str, *, force: bool = False,
    form: str | None = None, publish: bool = False, solution: str | None = None,
) -> dict[str, Any]:
    """Remove a tab from an entity form (refuses the only tab, or a tab holding
    bound fields unless ``force`` — which surfaces the orphaned fields)."""
    form_row = _select_form(read_entity_forms(backend, entity), form)
    formxml = form_row.get("formxml", "")
    orphans = _bound_fields_under(_resolve_target_tab(_parse_formxml(formxml), tab))
    new_xml = remove_tab_from_formxml(formxml, tab=tab, force=force)
    extra: dict[str, Any] = {"tab": tab}
    if orphans:  # only reached when force=True (else remove_tab_from_formxml raised)
        extra["orphaned"] = orphans
    return _commit_form_change(
        backend, form_row, new_xml, action="remove-tab", publish=publish,
        solution=solution, extra=extra)


def rename_form_tab(
    backend: D365Backend, entity: str, tab: str, *, label: str,
    form: str | None = None, publish: bool = False, solution: str | None = None,
) -> dict[str, Any]:
    """Set a tab's display label on an entity form."""
    form_row = _select_form(read_entity_forms(backend, entity), form)
    new_xml = rename_tab_in_formxml(form_row.get("formxml", ""), tab=tab, label=label)
    return _commit_form_change(
        backend, form_row, new_xml, action="rename-tab", publish=publish,
        solution=solution, extra={"tab": tab, "label": label})


def move_form_tab(
    backend: D365Backend, entity: str, tab: str, *, after: str | None = None,
    form: str | None = None, publish: bool = False, solution: str | None = None,
) -> dict[str, Any]:
    """Reorder a tab on an entity form (to the front, or after ``after``)."""
    form_row = _select_form(read_entity_forms(backend, entity), form)
    new_xml = move_tab_in_formxml(form_row.get("formxml", ""), tab=tab, after=after)
    return _commit_form_change(
        backend, form_row, new_xml, action="move-tab", publish=publish,
        solution=solution, extra={"tab": tab, "after": after})


def add_form_section(
    backend: D365Backend, entity: str, name: str, *, tab: str | None = None,
    label: str | None = None, columns: int = 1, after: str | None = None,
    form: str | None = None, publish: bool = False, solution: str | None = None,
) -> dict[str, Any]:
    """Add a section to a tab of an entity form (closes the 'no section to target'
    gap for ``form add-field`` on a sectionless tab)."""
    form_row = _select_form(read_entity_forms(backend, entity), form)
    new_xml = add_section_to_formxml(
        form_row.get("formxml", ""), name=name, label=label or name, tab=tab,
        columns=columns, after=after)
    return _commit_form_change(
        backend, form_row, new_xml, action="add-section", publish=publish,
        solution=solution,
        extra={"section": name, "tab": tab, "label": label or name,
               "columns": columns, "after": after})


def remove_form_section(
    backend: D365Backend, entity: str, section: str, *, tab: str | None = None,
    force: bool = False, form: str | None = None, publish: bool = False,
    solution: str | None = None,
) -> dict[str, Any]:
    """Remove a section from a tab of an entity form (refuses a section holding
    bound fields unless ``force`` — which surfaces the orphaned fields)."""
    form_row = _select_form(read_entity_forms(backend, entity), form)
    formxml = form_row.get("formxml", "")
    orphans = _bound_fields_under(
        _resolve_target_section(_parse_formxml(formxml), tab, section))
    new_xml = remove_section_from_formxml(
        formxml, section=section, tab=tab, force=force)
    extra: dict[str, Any] = {"section": section, "tab": tab}
    if orphans:  # only reached when force=True
        extra["orphaned"] = orphans
    return _commit_form_change(
        backend, form_row, new_xml, action="remove-section", publish=publish,
        solution=solution, extra=extra)


def rename_form_section(
    backend: D365Backend, entity: str, section: str, *, label: str,
    tab: str | None = None, form: str | None = None, publish: bool = False,
    solution: str | None = None,
) -> dict[str, Any]:
    """Set a section's display label on an entity form."""
    form_row = _select_form(read_entity_forms(backend, entity), form)
    new_xml = rename_section_in_formxml(
        form_row.get("formxml", ""), section=section, label=label, tab=tab)
    return _commit_form_change(
        backend, form_row, new_xml, action="rename-section", publish=publish,
        solution=solution, extra={"section": section, "tab": tab, "label": label})


def move_form_section(
    backend: D365Backend, entity: str, section: str, *, tab: str | None = None,
    after: str | None = None, form: str | None = None, publish: bool = False,
    solution: str | None = None,
) -> dict[str, Any]:
    """Reorder a section within its tab on an entity form."""
    form_row = _select_form(read_entity_forms(backend, entity), form)
    new_xml = move_section_in_formxml(
        form_row.get("formxml", ""), section=section, tab=tab, after=after)
    return _commit_form_change(
        backend, form_row, new_xml, action="move-section", publish=publish,
        solution=solution, extra={"section": section, "tab": tab, "after": after})


def _resolve_library_name(backend: D365Backend, library: str) -> str:
    """The canonical web-resource name for ``library``, asserting it EXISTS.

    The FormXml references a library by web-resource name (``<Library name>`` /
    ``<Handler libraryName>``), and the editor never *creates* the resource — so
    a missing one is a typed error here rather than a broken form later. Returns
    the server's canonical name (a read that runs even under dry-run, so a
    preview validates too).
    """
    return webresource.get_webresource(backend, library).get("name") or library


def add_form_library(
    backend: D365Backend, entity: str, library: str, *,
    form: str | None = None, publish: bool = False, solution: str | None = None,
) -> dict[str, Any]:
    """Register a JS library on an entity form (idempotent). Errors if the web
    resource does not exist."""
    library_name = _resolve_library_name(backend, library)
    form_row = _select_form(read_entity_forms(backend, entity), form)
    new_xml = add_library_to_formxml(
        form_row.get("formxml", ""), library_name=library_name)
    return _commit_form_change(
        backend, form_row, new_xml, action="add-library",
        publish=publish, solution=solution,
        extra={"attribute": library_name, "library": library_name})


def add_form_handler(
    backend: D365Backend, entity: str, *, event: str, function: str, library: str,
    field: str | None = None, params: "tuple[str, ...]" = (),
    pass_context: bool = True, enabled: bool = True,
    form: str | None = None, publish: bool = False, solution: str | None = None,
) -> dict[str, Any]:
    """Wire a JS event handler on an entity form, registering its library.

    Errors if the web resource does not exist, the event is unsupported, or the
    onchange/``--field`` pairing is invalid.
    """
    library_name = _resolve_library_name(backend, library)
    form_row = _select_form(read_entity_forms(backend, entity), form)
    new_xml = add_handler_to_formxml(
        form_row.get("formxml", ""), event=event, function=function,
        library_name=library_name, field=field, params=params,
        pass_context=pass_context, enabled=enabled)
    return _commit_form_change(
        backend, form_row, new_xml, action="add-handler",
        publish=publish, solution=solution,
        extra={"attribute": function, "event": event, "library": library_name,
               "field": field})


def remove_form_handler(
    backend: D365Backend, entity: str, *, event: str, function: str,
    field: str | None = None,
    form: str | None = None, publish: bool = False, solution: str | None = None,
) -> dict[str, Any]:
    """Remove a JS event handler from an entity form. Errors if it is absent."""
    form_row = _select_form(read_entity_forms(backend, entity), form)
    new_xml = remove_handler_from_formxml(
        form_row.get("formxml", ""), event=event, function=function, field=field)
    return _commit_form_change(
        backend, form_row, new_xml, action="remove-handler",
        publish=publish, solution=solution,
        extra={"attribute": function, "event": event, "field": field})


def list_form_handlers(
    backend: D365Backend, entity: str, *, form: str | None = None,
) -> dict[str, Any]:
    """Report the JS event handlers wired on an entity form (read-only)."""
    form_row = _select_form(read_entity_forms(backend, entity), form)
    return {
        "formid": form_row.get("formid"),
        "form": form_row.get("name"),
        "handlers": list_handlers_in_formxml(form_row.get("formxml", "")),
    }


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
