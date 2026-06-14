"""Dry-run reference resolution (#281).

Name-taking structured writes (``metadata create-one-to-many``,
``metadata add-attribute``, ``scaffold table``, ``plugin register-step``) name
other server objects — a referenced entity, a global option set, an SDK message,
a plug-in type. Under ``--dry-run`` the command resolves each such reference for
real (the reads-execute rule lets GETs fall through to the wire) and folds the
outcome into the preview as ``data.references[]``, so a dangling reference is a
cheap pre-flight finding instead of a server 400/404 at write time.

This module is the one place the existence probes and the advisory wording live,
so every command resolves references identically and a real (non-dry-run) write
and its preview stay in lockstep. The probes reuse the existing read-only seams
(``target_exists`` for metadata paths); a dangling reference is never a hard
failure — it is reported ``_exists: False`` and turned into a ``meta.warnings``
advisory by the caller.
"""

from __future__ import annotations

from typing import Any, Callable, TypedDict, cast

from crm.core.metadata import target_exists
from crm.utils.d365_backend import D365Backend, D365Error, as_dict


class Reference(TypedDict):
    """One ``data.references[]`` entry: a named server object a write would
    dereference, and whether it currently exists."""

    kind: str
    value: str
    _exists: bool


def make_reference(kind: str, value: str, exists: bool) -> Reference:
    """Build one ``data.references[]`` entry."""
    return {"kind": kind, "value": value, "_exists": exists}


def entity_exists(backend: D365Backend, logical_name: str) -> bool:
    """Whether an entity (table) with this logical name exists."""
    return target_exists(
        backend, f"EntityDefinitions(LogicalName='{logical_name}')")


def resolve_global_optionset_id(backend: D365Backend, name: str) -> str | None:
    """MetadataId of a global option set looked up by Name, or None when absent.

    The single existence+id seam for global option sets under dry-run: a 404 and
    a 200 without a MetadataId both map to None, so every caller agrees on
    existence — `add_attribute` reuses the returned id to match the real write's
    bind, while `scaffold`/spec callers need only the boolean.
    """
    try:
        rb = as_dict(backend.get(
            f"GlobalOptionSetDefinitions(Name='{name}')",
            params={"$select": "MetadataId"},
        ))
    except D365Error as exc:
        if exc.status == 404:
            return None
        raise
    metadata_id = rb.get("MetadataId")
    return str(metadata_id) if metadata_id else None


def global_optionset_exists(backend: D365Backend, name: str) -> bool:
    """Whether a global option set with this Name exists."""
    return resolve_global_optionset_id(backend, name) is not None


def resolve_spec_references(
    backend: D365Backend, spec: dict[str, Any],
) -> list[Reference]:
    """Resolve the cross-references an apply/scaffold spec's columns name (#281).

    Walks each entity's attributes for the objects a column dereferences — a
    lookup column's ``target_entity`` and a picklist/multiselect column's
    ``optionset_name`` — and probes each (deduplicated by kind+value). Used by
    ``scaffold table``, whose greenfield dry-run otherwise reports columns as
    planned without ever probing the objects they reference.
    """
    references: list[Reference] = []
    seen: set[tuple[str, str]] = set()

    def add(kind: str, value: Any, probe: Callable[[D365Backend, str], bool]) -> None:
        if isinstance(value, str) and (kind, value) not in seen:
            seen.add((kind, value))
            references.append(make_reference(kind, value, probe(backend, value)))

    entities = spec.get("entities")
    for ent in cast("list[Any]", entities) if isinstance(entities, list) else []:
        if not isinstance(ent, dict):
            continue
        attrs = cast("dict[str, Any]", ent).get("attributes")
        for attr in cast("list[Any]", attrs) if isinstance(attrs, list) else []:
            if not isinstance(attr, dict):
                continue
            cattr = cast("dict[str, Any]", attr)
            kind = cattr.get("kind")
            if kind == "lookup":
                add("target_entity", cattr.get("target_entity"), entity_exists)
            elif kind in ("picklist", "multiselect"):
                add("optionset", cattr.get("optionset_name"), global_optionset_exists)
    return references


def reference_warnings(references: list[Reference] | None) -> list[str]:
    """One advisory string per dangling reference (``_exists`` falsy)."""
    return [
        f"reference not found: {r['kind']}={r['value']!r}"
        for r in references or []
        if not r.get("_exists", True)
    ]
