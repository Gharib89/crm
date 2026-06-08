"""Read and clone systemform records.

Mirrors views.py (read_entity_views / create_view). A form is read from the
`systemforms` set, its formxml entity-retargeted, and recreated against a new
`objecttypecode`. Form retarget logic is isolated here so it is testable
independently of the clone orchestrator, and so a future `crm form` command
can wrap it the way `view` wraps `views.py`.
"""

from __future__ import annotations

import re
from typing import Any

from crm.core.metadata import maybe_publish
from crm.utils.d365_backend import D365Backend, D365Error, as_dict

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
    entity_lit = entity_logical_name.replace("'", "''")
    type_clause = " or ".join(f"type eq {t}" for t in form_types)
    filt = f"objecttypecode eq '{entity_lit}' and ({type_clause})"
    rows = as_dict(backend.get(
        "systemforms",
        params={"$select": _FORM_SELECT, "$filter": filt},
    )).get("value", [])
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

    Retargets ``formxml`` and sets ``objecttypecode`` to the clone. The server
    assigns a fresh formid. Read-back is via the OData-EntityId header, matching
    the view/metadata-write precedent.
    """
    src_entity = form.get("objecttypecode")
    if not src_entity:
        raise D365Error("form is missing objecttypecode; cannot retarget.")
    body: dict[str, Any] = {
        "name": form.get("name"),
        "objecttypecode": new_entity,
        "type": form.get("type"),
        "formxml": retarget_formxml(
            form.get("formxml", ""), src_entity=src_entity, dst_entity=new_entity),
    }
    if form.get("description") is not None:
        body["description"] = form["description"]
    headers = {"MSCRM.SolutionUniqueName": solution} if solution else None
    result = as_dict(backend.post("systemforms", json_body=body, extra_headers=headers))
    if result.get("_dry_run"):
        return result

    entity_id_url = result.get("_entity_id_url") or ""
    match = re.search(r"systemforms\(([0-9a-fA-F-]{36})\)", entity_id_url)
    formid = match.group(1) if match else None
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
