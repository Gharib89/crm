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

from crm.utils.d365_backend import D365Backend, as_dict

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
