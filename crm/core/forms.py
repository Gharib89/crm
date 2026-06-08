"""Read and clone systemform records.

Mirrors views.py (read_entity_views / create_view). A form is read from the
`systemforms` set, its formxml entity-retargeted, and recreated against a new
`objecttypecode`. Form retarget logic is isolated here so it is testable
independently of the clone orchestrator, and so a future `crm form` command
can wrap it the way `view` wraps `views.py`.
"""

from __future__ import annotations

from typing import Any

from crm.utils.d365_backend import D365Backend, as_dict

FORM_TYPE_MAIN = 2

_FORM_SELECT = "formid,name,objecttypecode,type,formxml,description,isdefault"


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
