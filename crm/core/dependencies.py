"""Dependency resolution for Dataverse metadata components.

Resolves a metadata target (entity, attribute, optionset, relationship) to its
`(MetadataId, componenttype)` pair and calls the Web API function
`RetrieveDependenciesForDelete` or `RetrieveDependentComponents` to check whether
the component can be safely deleted or to enumerate its dependents.

All GETs in this module fire even when the backend is in dry-run mode (reads
always execute — the reads-execute rule) so previews reflect live state.
"""

from __future__ import annotations

from typing import Any

from crm.utils.d365_backend import D365Backend, D365Error, as_dict

# ── component type constants ─────────────────────────────────────────────

_KIND_TO_COMPONENT_TYPE: dict[str, int] = {
    "entity": 1,
    "attribute": 2,
    "optionset": 9,
    "relationship": 10,
}

# Stable subset from Microsoft docs; fall back to str(code) for unknowns.
_COMPONENT_TYPE_LABELS: dict[int, str] = {
    1: "Entity",
    2: "Attribute",
    3: "Relationship",
    4: "Attribute Picklist Value",
    5: "Attribute Lookup Value",
    6: "View Attribute",
    7: "Localized Label",
    8: "Relationship Extra Condition",
    9: "Option Set",
    10: "Entity Relationship",
    11: "Entity Relationship Role",
    12: "Entity Relationship Relationships",
    13: "Managed Property",
    14: "Entity Key",
    16: "Privilege",
    20: "Role",
    22: "Display String",
    24: "Form",
    25: "Organization",
}

_FUNCTION_NAMES: dict[str, str] = {
    "delete": "RetrieveDependenciesForDelete",
    "dependents": "RetrieveDependentComponents",
}


# ── helpers ───────────────────────────────────────────────────────────────


def _component_label(code: Any) -> str:
    """Return human-readable label for a componenttype code, or str(code)."""
    try:
        int_code = int(code)
    except (TypeError, ValueError):
        return str(code)
    return _COMPONENT_TYPE_LABELS.get(int_code, str(int_code))


def _map_blocker(record: dict[str, Any]) -> dict[str, Any]:
    """Map a raw dependency record to a clean blocker dict."""
    return {
        "dependent_type": _component_label(record.get("dependentcomponenttype")),
        "dependent_id": record.get("dependentcomponentobjectid"),
        "dependent_parent_id": record.get("dependentcomponentparentid"),
        "required_type": _component_label(record.get("requiredcomponenttype")),
        "dependency_type": record.get("dependencytype"),  # raw int; no label map for dependencytype enum
    }


def _resolve_path(kind: str, target: str) -> str:
    """Build the metadata GET path for a given kind + target."""
    if kind == "entity":
        return f"EntityDefinitions(LogicalName='{target}')"
    if kind == "attribute":
        entity, _, attr = target.partition(".")
        return (
            f"EntityDefinitions(LogicalName='{entity}')"
            f"/Attributes(LogicalName='{attr}')"
        )
    if kind == "optionset":
        return f"GlobalOptionSetDefinitions(Name='{target}')"
    if kind == "relationship":
        return f"RelationshipDefinitions(SchemaName='{target}')"
    # Should never reach here — caller validates kind first.
    raise D365Error(f"unknown kind {kind!r}")  # pragma: no cover


def _get_metadata_id(backend: D365Backend, path: str, kind: str, target: str) -> str:
    """GET path (runs live even under dry-run); return MetadataId or raise D365Error."""
    try:
        result = as_dict(backend.get(path, params={"$select": "MetadataId"}))
        metadata_id = result.get("MetadataId")
        if not isinstance(metadata_id, str) or not metadata_id:
            raise D365Error(
                f"MetadataId missing in response for {kind} {target!r}",
                response_body=result,
            )
        return metadata_id
    except D365Error as exc:
        if exc.status == 404:
            raise D365Error(f"{kind} {target!r} not found", code="NotFound",
                            status=exc.status, response_body=exc.response_body) from exc
        raise


# ── public surface ────────────────────────────────────────────────────────


def resolve_target(backend: D365Backend, kind: str, target: str) -> tuple[str, int]:
    """Resolve a metadata target to ``(MetadataId, component_type)``.

    Args:
        backend: Configured D365Backend.
        kind: One of ``entity``, ``attribute``, ``optionset``, ``relationship``.
        target: Logical name / schema name. Attribute targets must be dotted
            ``entity.attribute``; the split is on the first ``.``.

    Returns:
        ``(metadata_id, component_type)`` where component_type is the int code
        used by Dataverse dependency functions.

    Raises:
        D365Error: empty target, unknown kind, attribute without dot, 404, or
            other server error.
    """
    if not target:
        raise D365Error("target is required.")
    if kind not in _KIND_TO_COMPONENT_TYPE:
        raise D365Error(f"unknown kind {kind!r}; valid: {sorted(_KIND_TO_COMPONENT_TYPE)}")
    if kind == "attribute":
        _entity, _, _attr = target.partition(".")
        if not _entity or not _attr:
            raise D365Error(
                f"attribute target must be dotted 'entity.attribute' with both parts non-empty,"
                f" got {target!r}"
            )
    component_type = _KIND_TO_COMPONENT_TYPE[kind]
    path = _resolve_path(kind, target)
    metadata_id = _get_metadata_id(backend, path, kind, target)
    return metadata_id, component_type


def build_dependency_path(
    metadata_id: str,
    component_type: int,
    for_: str = "delete",
) -> str:
    """Build the inline function URL for a dependency query.

    This is the single place that encodes the Dataverse inline function syntax
    so the GUID and int encoding can be fixed in one spot if the server differs.

    Inline literal encoding: both GUID and int are UNQUOTED.
    e.g. ``RetrieveDependenciesForDelete(ObjectId=<guid>,ComponentType=9)``
    """
    func = _FUNCTION_NAMES.get(for_)
    if func is None:
        raise D365Error(
            f"unknown for_ {for_!r}; valid: {sorted(_FUNCTION_NAMES)}"
        )
    return f"{func}(ObjectId={metadata_id},ComponentType={component_type})"


def build_uninstall_dependency_path(solution_unique_name: str) -> str:
    """Inline-function URL for ``RetrieveDependenciesForUninstall``.

    ``SolutionUniqueName`` is ``Edm.String`` → SINGLE-QUOTED (embedded quotes
    doubled per OData), unlike the unquoted GUID/int encoding in
    ``build_dependency_path``.
    """
    escaped = solution_unique_name.replace("'", "''")
    return f"RetrieveDependenciesForUninstall(SolutionUniqueName='{escaped}')"


def dependencies_by_id(
    backend: D365Backend,
    metadata_id: str,
    component_type: int,
    *,
    for_: str = "delete",
    kind: str | None = None,
) -> dict[str, Any]:
    """Retrieve dependencies for a pre-resolved ``(metadata_id, component_type)``.

    Skips the resolve GET — useful when the caller already holds the MetadataId
    (e.g. a delete pre-flight that fetched it for its own reasons).

    ``kind`` is an optional label (e.g. ``"entity"``) that is echoed back in the
    result dict; pass it when known so callers get a complete, honest shape.

    Returns the same shape as ``retrieve_dependencies``.
    """
    path = build_dependency_path(metadata_id, component_type, for_=for_)
    result = as_dict(backend.get(path))

    records: list[dict[str, Any]] = result.get("value") or []
    blockers = [_map_blocker(r) for r in records]
    return {
        "can_delete": len(blockers) == 0,
        "blockers": blockers,
        "metadata_id": metadata_id,
        "component_type": component_type,
        "kind": kind,
        "for": for_,
    }


def retrieve_dependencies(
    backend: D365Backend,
    kind: str,
    target: str,
    *,
    for_: str = "delete",
) -> dict[str, Any]:
    """Resolve ``target`` and return its dependency information.

    Calls ``RetrieveDependenciesForDelete`` (default) or
    ``RetrieveDependentComponents`` (``for_="dependents"``).

    Both GETs (resolve + function) fire even when the backend is in dry-run
    mode so the returned preview reflects live state.

    Returns::

        {
            "can_delete": bool,
            "blockers": [{"dependent_type", "dependent_id",
                          "dependent_parent_id", "required_type",
                          "dependency_type"}, ...],
            "metadata_id": str,
            "component_type": int,
            "kind": str,
            "for": str,
        }
    """
    metadata_id, component_type = resolve_target(backend, kind, target)
    return dependencies_by_id(backend, metadata_id, component_type, for_=for_, kind=kind)


def retrieve_dependencies_for_uninstall(
    backend: D365Backend, solution_unique_name: str
) -> dict[str, Any]:
    """Return dependency records that would block uninstalling a managed solution.

    Calls the Web API function
    ``RetrieveDependenciesForUninstall(SolutionUniqueName='<name>')``. Unlike the
    delete/dependents path there is no component target to resolve — the function
    takes only the solution unique name.

    The GET fires even when the backend is in dry-run mode (read-only preview).

    Returns::

        {
            "solution": str,
            "blockers": [{"dependent_type", "dependent_id",
                          "dependent_parent_id", "required_type",
                          "dependency_type"}, ...],
            "count": int,
        }

    Raises:
        D365Error: empty/whitespace solution name, or a server error.
    """
    name = solution_unique_name.strip()
    if not name:
        raise D365Error("solution unique name is required.")
    path = build_uninstall_dependency_path(name)
    result = as_dict(backend.get(path))

    records: list[dict[str, Any]] = result.get("value") or []
    blockers = [_map_blocker(r) for r in records]
    return {"solution": name, "blockers": blockers, "count": len(blockers)}
