"""Shared safety primitives for the customization-XML editors.

Forms, dashboards, charts, the ribbon, the site map and views are all stored as
XML and need the same guards to edit safely: parse/re-serialize (T0), protect the
GUIDs and ``classid`` constants that reference external objects while
consistently regenerating the internal ids a family must refresh, and read the
result back to assert the edit landed un-corrupted (T3).

The guards exist because the dangerous corruptions here — a mutated ``classid``,
the #275 form-clone internal-GUID collision, a stray external reference — are
well-formed *and* XSD-valid, so they pass a naive check and break the artifact
silently at runtime. Parsing uses stdlib ``xml.etree.ElementTree`` only (no
``lxml``): on these namespace-free payloads it round-trips faithfully and adds no
bundle weight.
"""

from __future__ import annotations

import re
import uuid
import xml.etree.ElementTree as ET
from typing import Any, Callable

from crm.core.metadata import maybe_publish
from crm.utils.d365_backend import D365Backend, D365Error, as_dict

# --- T0: parse / re-serialize ---------------------------------------------------


def parse_xml(xml: str, *, label: str = "XML") -> "ET.Element":
    """Parse customization XML, turning a malformed payload into a ``D365Error``.

    A ``ParseError`` becomes a typed error naming ``label`` so the CLI emits its
    standard error envelope rather than a raw traceback.
    """
    try:
        return ET.fromstring(xml)
    except ET.ParseError as exc:
        raise D365Error(f"Could not parse the {label}: {exc}") from exc


def serialize_xml(root: "ET.Element") -> str:
    """Re-serialize a parsed element tree back to an XML string."""
    return ET.tostring(root, encoding="unicode")


# --- Protected-id guard + internal-GUID regeneration ----------------------------

# The bare GUID character pattern, shared with every family that builds an
# internal-id attribute regex (e.g. forms' clone-id pattern) so the GUID grammar
# has a single source of truth.
GUID = r"[0-9a-fA-F]{8}-[0-9a-fA-F]{4}-[0-9a-fA-F]{4}-[0-9a-fA-F]{4}-[0-9a-fA-F]{12}"
ANY_GUID_RE = re.compile(GUID)
_CLASSID_RE = re.compile(
    r"""classid(?P<eq>\s*=\s*)(?P<q>["'])(?P<v>\{?""" + GUID + r"""\}?)(?P=q)""",
    re.IGNORECASE,
)


def fresh_guid(*, braced: bool = True) -> str:
    """A fresh ``uuid4``, brace-wrapped by default to match customization-XML style."""
    g = str(uuid.uuid4())
    return "{" + g + "}" if braced else g


def regenerate_guids(
    xml: str, pattern: "re.Pattern[str]"
) -> "tuple[str, dict[str, str]]":
    """Replace each GUID matched by ``pattern`` with a fresh ``uuid4``, consistently.

    One new value per *distinct* source GUID, so any intra-document reference
    (a label pointed at by id, a handler's own registration) stays internally
    consistent. ``pattern`` must expose the named groups ``attr``, ``eq``, ``q``,
    ``brace`` and ``guid`` — the family supplies which attributes hold its
    *internal* ids; everything else (``classid`` and external refs) is left
    untouched and should be policed by :func:`assert_external_guids_intact`.

    Returns the rewritten XML and the ``{old_lower: new}`` mapping — feed that
    mapping straight into the guard.
    """
    mapping: dict[str, str] = {}

    def _repl(m: "re.Match[str]") -> str:
        old = m.group("guid").lower()
        if old not in mapping:
            mapping[old] = str(uuid.uuid4())
        brace = "{" if m.group("brace") else ""
        close = "}" if m.group("brace") else ""
        return (f"{m.group('attr')}{m.group('eq')}{m.group('q')}"
                f"{brace}{mapping[old]}{close}{m.group('q')}")

    return pattern.sub(_repl, xml), mapping


def assert_external_guids_intact(
    before: str,
    after: str,
    *,
    regenerated: "dict[str, str] | None" = None,
    message: str = (
        "XML edit altered a non-target external GUID; refusing to write a "
        "possibly corrupt artifact."
    ),
) -> None:
    """Refuse a transform that changed any GUID it was not meant to.

    Compares the *multiset* of GUIDs present before and after the edit (sorted
    lists, so a duplicate-count change is caught too), excluding the ones a family
    *deliberately* regenerated (``regenerated`` maps each replaced source GUID to
    its fresh value, as returned by :func:`regenerate_guids`). If the remaining —
    external — GUIDs differ, the edit touched a ``classid``, a
    security-role ref, a view/quick-form lookup or some other external object, so
    raise ``D365Error`` rather than write a possibly corrupt artifact. This is the
    generalized form-clone guard (#275): a mutated external GUID is well-formed and
    XSD-valid, so only this before/after diff catches it.
    """
    regenerated = regenerated or {}
    old_ids = set(regenerated)
    new_ids = set(regenerated.values())
    untouched_before = sorted(
        g.lower() for g in ANY_GUID_RE.findall(before) if g.lower() not in old_ids)
    untouched_after = sorted(
        g.lower() for g in ANY_GUID_RE.findall(after) if g.lower() not in new_ids)
    if untouched_before != untouched_after:
        raise D365Error(message)


# --- T3: structural read-back diff helpers --------------------------------------


def guid_set(xml: str) -> "set[str]":
    """Every GUID in ``xml``, lowercased — for an id-set delta assertion."""
    return {g.lower() for g in ANY_GUID_RE.findall(xml)}


def assert_classids_intact(before: str, after: str) -> None:
    """Raise ``D365Error`` if the multiset of control ``classid`` values changed.

    A mutated ``classid`` points the control at a different type — well-formed and
    XSD-valid, but it renders a broken control. A targeted field edit never
    rewrites a ``classid``, so any change is corruption.
    """
    before_ids = sorted(m.group("v").lower() for m in _CLASSID_RE.finditer(before))
    after_ids = sorted(m.group("v").lower() for m in _CLASSID_RE.finditer(after))
    if before_ids != after_ids:
        raise D365Error(
            "XML edit changed a control classid; refusing to write a corrupt "
            "artifact.")


def node_present(root: "ET.Element", tag: str, **attrs: str) -> bool:
    """Whether any descendant ``<tag>`` matches every given attribute value."""
    for el in root.iter(tag):
        if all(el.get(k) == v for k, v in attrs.items()):
            return True
    return False


# --- Shared direct-PATCH commit -------------------------------------------------


def commit_xml_patches(
    backend: D365Backend,
    *,
    entity_set: str,
    record_id: str,
    columns: "dict[str, str]",
    result: "dict[str, Any]",
    dry_run_flag: str,
    publish: bool,
    solution: "str | None" = None,
    read_back: "Callable[[dict[str, str]], None] | None" = None,
) -> "dict[str, Any]":
    """PATCH one or more writable columns in a single request, then maybe publish.

    The multi-column form of :func:`commit_xml_patch`, for an editor that must
    keep two coupled columns consistent in one write — a chart edit that touches
    both ``datadescription`` and ``presentationdescription`` (add/remove-series)
    cannot split them across two PATCHes without risking a half-applied chart.
    ``columns`` are typically XML columns but may include plain scalar columns the
    same edit sets (e.g. a chart's ``name`` / ``description``). The caller has
    already read and mutated every column in ``columns`` and pre-seeded ``result``
    with the edit's metadata.

    - Under ``backend.dry_run`` no write is issued — stamp ``result`` with
      ``_dry_run`` and ``dry_run_flag`` and return it (zero HTTP).
    - Otherwise PATCH ``columns`` onto ``entity_set(record_id)`` (under the
      solution header when ``solution`` is set), then ``maybe_publish``, then —
      if ``read_back`` is given — GET the columns back and hand the
      server-returned ``{column: xml}`` map to ``read_back`` for a T3 verify.

    The publish-before-read-back order is mandatory: a Web API GET returns the
    *published* layer, so a read-back before publish false-negatives. A
    ``read_back`` without ``publish`` is therefore a programming error and is
    rejected up front rather than allowed to silently false-negative.
    """
    if read_back is not None and not publish:
        raise ValueError(
            "commit_xml_patches: read_back requires publish=True — a Web API GET "
            "returns the published layer, so a read-back before publish "
            "false-negatives the T3 verification.")
    if backend.dry_run:
        result["_dry_run"] = True
        result[dry_run_flag] = True
        return result
    backend.patch(f"{entity_set}({record_id})",
                  json_body=dict(columns), solution=solution)
    result["updated"] = True
    maybe_publish(backend, result, publish)
    if read_back is not None:
        row = as_dict(backend.get(f"{entity_set}({record_id})",
                                  params={"$select": ",".join(columns)}))
        read_back({c: str(row.get(c) or "") for c in columns})
    return result


def commit_xml_patch(
    backend: D365Backend,
    *,
    entity_set: str,
    record_id: str,
    column: str,
    new_xml: str,
    result: "dict[str, Any]",
    dry_run_flag: str,
    publish: bool,
    solution: "str | None" = None,
    read_back: "Callable[[str], None] | None" = None,
) -> "dict[str, Any]":
    """PATCH one writable XML column (or preview under dry-run), then maybe publish.

    The shared commit for every direct-PATCH editor family (forms, dashboards,
    charts, sitemap, views): the caller has already read and mutated the column
    and pre-seeded ``result`` with the edit's metadata. A thin single-column
    adapter over :func:`commit_xml_patches`.

    - Under ``backend.dry_run`` no write is issued — stamp ``result`` with
      ``_dry_run`` and ``dry_run_flag`` and return it (zero HTTP).
    - Otherwise PATCH ``{column: new_xml}`` onto ``entity_set(record_id)`` (under
      the solution header when ``solution`` is set), then ``maybe_publish``, then
      — if ``read_back`` is given — GET the column back and hand the
      server-returned XML to ``read_back`` for a T3 verify.

    The publish-before-read-back order is mandatory: a Web API GET returns the
    *published* layer, so a read-back before publish false-negatives. A
    ``read_back`` without ``publish`` is therefore a programming error and is
    rejected up front rather than allowed to silently false-negative.
    """
    def _read_back_one(cols: "dict[str, str]") -> None:
        assert read_back is not None  # rb is only wired when read_back is set
        read_back(cols[column])

    rb = _read_back_one if read_back is not None else None
    return commit_xml_patches(
        backend, entity_set=entity_set, record_id=record_id,
        columns={column: new_xml}, result=result, dry_run_flag=dry_run_flag,
        publish=publish, solution=solution, read_back=rb)
