"""Attribute (field) mappings between mapped entities.

When a 1:N relationship supports mapping, an ``entitymap`` row exists for the
source→target entity pair and ``attributemap`` rows under it copy field values
from the parent (referenced) record onto a child (referencing) record created in
its context.

- ``create_mapping`` resolves the relationship to its source/target entities,
  finds the ``entitymap``, and POSTs one ``attributemap`` (``--from``/``--to``).
- ``auto_map`` calls the ``AutoMapEntity`` action to bulk-generate the likely
  attribute maps for the pair (replacing any existing maps — Dataverse semantics).

Mapping direction follows Dataverse: the relationship's ``ReferencedEntity``
(the "1"/parent side) is the map *source*, ``ReferencingEntity`` (the "N"/child
side) is the *target*.

References:
  https://learn.microsoft.com/dynamics365/customerengagement/on-premises/developer/customize-entity-attribute-mappings
  https://learn.microsoft.com/power-apps/developer/data-platform/webapi/reference/automapentity
"""

from __future__ import annotations

from typing import Any

from crm.utils.d365_backend import D365Backend, D365Error, as_dict, odata_literal


def _resolve_pair(backend: D365Backend, relationship: str) -> tuple[str, str]:
    """Resolve a 1:N relationship schema name to its (source, target) entities.

    Source is ``ReferencedEntity`` (parent), target is ``ReferencingEntity``
    (child) — the direction mapping copies values along.
    """
    rb = as_dict(backend.get(
        f"RelationshipDefinitions(SchemaName='{relationship}')"
        "/Microsoft.Dynamics.CRM.OneToManyRelationshipMetadata",
        params={"$select": "ReferencedEntity,ReferencingEntity"},
    ))
    source = rb.get("ReferencedEntity")
    target = rb.get("ReferencingEntity")
    if not source or not target:
        raise D365Error(
            f"Could not resolve a 1:N relationship named {relationship!r} "
            "(mappings require a one-to-many relationship)."
        )
    return source, target


def _find_entity_map(backend: D365Backend, source: str, target: str) -> str:
    """Return the entitymapid for a source→target pair, or raise if none exists."""
    rb = as_dict(backend.get(
        "entitymaps",
        params={
            "$select": "entitymapid",
            "$filter": (
                f"sourceentityname eq {odata_literal(source)} "
                f"and targetentityname eq {odata_literal(target)}"
            ),
        },
    ))
    rows: list[dict[str, Any]] = rb.get("value") or []
    if not rows:
        raise D365Error(
            f"No entity map exists for {source!r} → {target!r}; the relationship "
            "may not support mapping."
        )
    return str(rows[0]["entitymapid"])


def create_mapping(
    backend: D365Backend,
    relationship: str,
    *,
    source_attr: str,
    target_attr: str,
    solution: str | None = None,
) -> dict[str, Any]:
    """Create one ``attributemap`` under the relationship's entity map."""
    if not source_attr or not target_attr:
        raise D365Error("both --from and --to are required.")
    source, target = _resolve_pair(backend, relationship)
    entity_map_id = _find_entity_map(backend, source, target)

    body: dict[str, Any] = {
        "sourceattributename": source_attr,
        "targetattributename": target_attr,
        "entitymapid@odata.bind": f"/entitymaps({entity_map_id})",
    }
    headers = {"MSCRM.SolutionUniqueName": solution} if solution else None
    result = as_dict(backend.post("attributemaps", json_body=body, extra_headers=headers))
    if result.get("_dry_run"):
        result["would_create_mapping"] = True
        result["source_entity"] = source
        result["target_entity"] = target
        result["entity_map_id"] = entity_map_id
        return result

    return {
        "created": True,
        "relationship": relationship,
        "source_entity": source,
        "target_entity": target,
        "source_attribute": source_attr,
        "target_attribute": target_attr,
        "entity_map_id": entity_map_id,
        "attribute_map_id": result.get("_entity_id"),
        "solution": solution,
    }


def auto_map(
    backend: D365Backend,
    relationship: str,
    *,
    solution: str | None = None,
) -> dict[str, Any]:
    """Bulk-generate attribute maps for the pair via ``AutoMapEntity``.

    Dataverse replaces any existing maps for the pair, so this is destructive to
    prior manual maps (documented platform behavior).
    """
    source, target = _resolve_pair(backend, relationship)
    body = {"SourceEntityName": source, "TargetEntityName": target}
    headers = {"MSCRM.SolutionUniqueName": solution} if solution else None
    result = as_dict(backend.post("AutoMapEntity", json_body=body, extra_headers=headers))
    if result.get("_dry_run"):
        result["would_auto_map"] = True
        result["source_entity"] = source
        result["target_entity"] = target
        return result

    entity_map = as_dict(result.get("EntityMap"))
    attribute_maps = entity_map.get("AttributeMaps") if entity_map else None
    return {
        "auto_mapped": True,
        "relationship": relationship,
        "source_entity": source,
        "target_entity": target,
        "mapping_count": len(attribute_maps) if isinstance(attribute_maps, list) else None,
        "solution": solution,
    }
