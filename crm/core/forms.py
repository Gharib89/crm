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
from typing import Any

from crm.core.metadata import maybe_publish
from crm.utils.d365_backend import D365Backend, D365Error, as_dict, odata_literal

FORM_TYPE_MAIN = 2

_FORM_SELECT = "formid,name,objecttypecode,type,formxml,description,isdefault"


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
