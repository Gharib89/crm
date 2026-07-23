"""Pure component algebra for solutions: type-code map, normalise, diff, layer-conflicts.

Backend-free by design — no `D365Backend` / HTTP dependency, so these functions are
trivially unit-testable in isolation. Every name here is re-exported from
`crm.core.solution` for backward compatibility (callers and tests that reach them
via `crm.core.solution.<name>` keep working unchanged).
"""

from __future__ import annotations

from typing import Any, NamedTuple

# ── Solution component type codes (#71) ──────────────────────────────────────
#
# Flat friendly-name → integer map for the `componenttype` global optionset
# (values verified against the Dataverse SolutionComponent reference). Keys are
# canonical lower-case, separator-free; `resolve_component_type` normalises input
# so 'WebResource' / 'web resource' / 'web-resource' all map to 61. Note the
# canonical split: 'relationship' is 3 (base relationship), 'entityrelationship'
# is 10 — not interchangeable. Pass a raw int for any type not listed here.

SOLUTION_COMPONENT_TYPES: dict[str, int] = {
    "entity": 1,
    "attribute": 2,
    "relationship": 3,
    "optionset": 9,
    "entityrelationship": 10,
    "entitykey": 14,
    "role": 20,
    "form": 24,
    "savedquery": 26,
    "workflow": 29,
    "emailtemplate": 36,
    "duplicaterule": 44,
    "savedqueryvisualization": 59,
    "systemform": 60,
    "webresource": 61,
    "sitemap": 62,
    "appmodule": 80,
    "connectionrole": 63,
    "fieldsecurityprofile": 70,
    "plugintype": 90,
    "pluginassembly": 91,
    "sdkmessageprocessingstep": 92,
    "serviceendpoint": 95,
    # Customer-Service family (#627). These live above the common range and were
    # undiscoverable from `solution components` output until surfaced here.
    "routingrule": 150,
    "routingruleitem": 151,
    "sla": 152,
    "slaitem": 153,
    "convertrule": 154,
    "convertruleitem": 155,
}


# ── Component normalisation / diff ──────────────────────────────────────────


def normalize_components(items: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """Return a new, sorted list with exactly the three canonical keys.

    - ``componenttype``        → coerced to ``int``
    - ``objectid``             → lowercased ``str`` (stable GUID matching);
      a non-string ``objectid`` raises ``ValueError`` rather than being coerced,
      so a malformed snapshot (e.g. ``{"objectid": null}``) fails fast instead
      of silently becoming the literal string ``"none"``
    - ``rootcomponentbehavior`` → ``int`` or ``None`` (missing/None preserved)

    Input rows are not mutated.  The sort key is
    ``(componenttype, objectid, rootcomponentbehavior_or_minus1)``
    where ``None`` maps to ``-1`` for ordering only — the stored value stays
    ``None``.
    """
    out: list[dict[str, Any]] = []
    for row in items:
        objectid = row["objectid"]
        if not isinstance(objectid, str):
            raise ValueError(f"objectid must be a string, got {type(objectid).__name__}")
        rcb_raw = row.get("rootcomponentbehavior")
        rcb: int | None = None if rcb_raw is None else int(rcb_raw)
        out.append(
            {
                "componenttype": int(row["componenttype"]),
                "objectid": objectid.lower(),
                "rootcomponentbehavior": rcb,
            }
        )
    out.sort(
        key=lambda c: (
            c["componenttype"],
            c["objectid"],
            c["rootcomponentbehavior"] if c["rootcomponentbehavior"] is not None else -1,
        )
    )
    return out


def diff_components(
    live: list[dict[str, Any]],
    expected: list[dict[str, Any]],
) -> dict[str, Any]:
    """Compare two component lists and return a diff summary.

    Each component is keyed on ``(componenttype, objectid, rootcomponentbehavior)``
    after normalisation, so a same-ID component with a different
    ``rootcomponentbehavior`` value counts as **both** missing and unexpected.

    Returns::

        {
            "matches": bool,
            "missing":    [...],   # in expected, not in live
            "unexpected": [...],   # in live, not in expected
        }
    """
    norm_live = normalize_components(live)
    norm_expected = normalize_components(expected)

    def _key(c: dict[str, Any]) -> tuple[int, str, int | None]:
        return (c["componenttype"], c["objectid"], c["rootcomponentbehavior"])

    live_keys = {_key(c): c for c in norm_live}
    expected_keys = {_key(c): c for c in norm_expected}

    missing = [c for c in norm_expected if _key(c) not in live_keys]
    unexpected = [c for c in norm_live if _key(c) not in expected_keys]
    return {
        "matches": len(missing) == 0 and len(unexpected) == 0,
        "missing": missing,
        "unexpected": unexpected,
    }


# Reverse of SOLUTION_COMPONENT_TYPES for friendly-name display. The forward map's
# values are unique, so the inversion is lossless; unmapped types fall back to the
# raw int as a string.
_COMPONENT_TYPE_NAMES: dict[int, str] = {v: k for k, v in SOLUTION_COMPONENT_TYPES.items()}


def component_type_name(componenttype: int) -> str:
    """Friendly name for a ``componenttype`` int (e.g. 1 → 'entity'), or its string
    form when the type is not in SOLUTION_COMPONENT_TYPES.
    """
    return _COMPONENT_TYPE_NAMES.get(componenttype, str(componenttype))


# ── rootcomponentbehavior labels (#913, shared with #916) ────────────────────
#
# The `rootcomponentbehavior` optionset on a solution component says how much of
# a root component's sub-tree the solution carries. Labels verified against the
# Dataverse SolutionComponent reference.

ROOT_COMPONENT_BEHAVIORS: dict[int, str] = {
    0: "whole-entity (all subcomponents)",
    1: "shell (no subcomponents)",
    2: "shell + metadata",
}


def root_behavior_name(behavior: int | None) -> str | None:
    """Friendly label for a ``rootcomponentbehavior`` value.

    Returns ``None`` when ``behavior`` is ``None`` (the field is absent for
    non-root components), the raw int as a string for an unknown value, and the
    mapped label otherwise.
    """
    if behavior is None:
        return None
    return ROOT_COMPONENT_BEHAVIORS.get(behavior, str(behavior))


# ── objectid → name resolution specs (#913) ──────────────────────────────────
#
# Per component type, how to resolve a bare ``objectid`` to a friendly name via a
# single by-id GET (batchable). ``path`` is a ``{id}``-templated relative URL,
# ``select`` its ``$select``, ``name_field`` the response field to read as the
# name, and ``entity_field`` the parent-entity field (or ``None`` when the
# component is not entity-scoped). Attributes (type 2) are entity-scoped and have
# no top-level by-id path, so they are resolved separately via a bulk metadata
# pull rather than through this table.


def component_key(componenttype: Any, objectid: Any) -> tuple[int, str]:
    """Normalized ``(componenttype, objectid)`` key for matching a resolved name
    back to its row. Kept in one place so the store side (resolution) and the
    lookup side (display) can never drift in how they coerce/normalize.
    """
    return (int(componenttype or 0), str(objectid or "").strip().lower())


class _ResolveSpec(NamedTuple):
    path: str
    select: str
    name_field: str
    entity_field: str | None


RESOLVE_SPECS: dict[int, _ResolveSpec] = {
    1: _ResolveSpec("EntityDefinitions({id})", "LogicalName", "LogicalName", None),
    3: _ResolveSpec("RelationshipDefinitions({id})", "SchemaName", "SchemaName", None),
    10: _ResolveSpec("RelationshipDefinitions({id})", "SchemaName", "SchemaName", None),
    20: _ResolveSpec("roles({id})", "name", "name", None),
    26: _ResolveSpec("savedqueries({id})", "name,returnedtypecode", "name", "returnedtypecode"),
    29: _ResolveSpec("workflows({id})", "name,primaryentity", "name", "primaryentity"),
    60: _ResolveSpec("systemforms({id})", "name,objecttypecode", "name", "objecttypecode"),
    61: _ResolveSpec("webresourceset({id})", "name", "name", None),
    91: _ResolveSpec("pluginassemblies({id})", "name", "name", None),
    92: _ResolveSpec("sdkmessageprocessingsteps({id})", "name", "name", None),
}


def layer_conflicts(
    managed: list[dict[str, Any]],
    unmanaged: list[dict[str, Any]],
) -> list[dict[str, Any]]:
    """Components present in BOTH a managed and an unmanaged solution.

    A managed component that also appears in an unmanaged solution carries
    unmanaged-layer customizations — the potential unmanaged-layer conflict. Keyed
    on ``(componenttype, objectid)``, deliberately IGNORING ``rootcomponentbehavior``
    (the same component included with a different behavior is still an overlap).

    Each conflict row::

        {
            "componenttype": int,
            "type_name": str,                          # friendly name or str(int)
            "objectid": str,
            "managed_rootcomponentbehavior": int | None,
            "unmanaged_rootcomponentbehavior": int | None,
        }

    Sorted by ``(componenttype, objectid)``.
    """
    norm_managed = normalize_components(managed)
    norm_unmanaged = normalize_components(unmanaged)

    def _key(c: dict[str, Any]) -> tuple[int, str]:
        return (c["componenttype"], c["objectid"])

    unmanaged_by_key = {_key(c): c for c in norm_unmanaged}
    conflicts: list[dict[str, Any]] = []
    for c in norm_managed:
        match = unmanaged_by_key.get(_key(c))
        if match is None:
            continue
        conflicts.append(
            {
                "componenttype": c["componenttype"],
                "type_name": component_type_name(c["componenttype"]),
                "objectid": c["objectid"],
                "managed_rootcomponentbehavior": c["rootcomponentbehavior"],
                "unmanaged_rootcomponentbehavior": match["rootcomponentbehavior"],
            }
        )
    conflicts.sort(key=lambda c: (c["componenttype"], c["objectid"]))
    return conflicts


# ── solution audit (#916) ────────────────────────────────────────────────────
#
# Classify a solution's inventory to surface AddRequiredComponents cascade drift:
# which entities are carried whole (behavior 0, where accidental bloat hides) vs
# as shells, and which components are only present because another component in
# the same solution requires them (cascade candidates, not authored here). The
# required-by graph is fetched by the caller (a live-only step); this classifier
# is pure so the bucketing/summary logic is unit-testable in isolation.

_WHOLE_ENTITY_BEHAVIOR = 0
_SHELL_BEHAVIORS = (1, 2)


def build_audit(
    components: list[dict[str, Any]],
    *,
    required_by: dict[tuple[int, str], list[str]],
    names: dict[tuple[int, str], dict[str, Any]],
) -> dict[str, Any]:
    """Classify a normalized component list into an audit report.

    Args:
        components: rows from :func:`normalize_components`
            (``componenttype`` / ``objectid`` / ``rootcomponentbehavior``).
        required_by: ``{(componenttype, objectid): [requirer_label, ...]}`` — the
            in-solution components that another in-solution component directly
            requires (built from ``RetrieveRequiredComponents`` by the caller).
            A key present here is a cascade candidate.
        names: ``{(componenttype, objectid): {"name": str|None, "entity"?: str}}``
            resolved friendly names (best-effort; a missing key leaves ``name``
            ``None``).

    Returns::

        {
            "summary": {"total_components", "entity_count", "whole_entity_count",
                        "shell_count", "required_only_count", "by_type": {name: n}},
            "whole_entities": [{objectid, name, rootcomponentbehavior, behavior_label}],
            "shell_entities": [{objectid, name, rootcomponentbehavior, behavior_label}],
            "required_only_candidates": [{componenttype, type_name, objectid, name,
                                          rootcomponentbehavior, required_by}],
        }

    Entity buckets cover ``componenttype == 1`` only; an entity with a
    ``rootcomponentbehavior`` outside ``{0, 1, 2}`` (unexpected) lands in neither
    list but still counts toward ``entity_count``.
    """

    def _name(componenttype: int, objectid: str) -> str | None:
        return names.get(component_key(componenttype, objectid), {}).get("name")

    whole_entities: list[dict[str, Any]] = []
    shell_entities: list[dict[str, Any]] = []
    by_type: dict[str, int] = {}
    entity_count = 0
    for c in components:
        ctype = c["componenttype"]
        oid = c["objectid"]
        by_type[component_type_name(ctype)] = by_type.get(component_type_name(ctype), 0) + 1
        if ctype != 1:
            continue
        entity_count += 1
        behavior = c["rootcomponentbehavior"]
        entry = {
            "objectid": oid,
            "name": _name(ctype, oid),
            "rootcomponentbehavior": behavior,
            "behavior_label": root_behavior_name(behavior),
        }
        if behavior == _WHOLE_ENTITY_BEHAVIOR:
            whole_entities.append(entry)
        elif behavior in _SHELL_BEHAVIORS:
            shell_entities.append(entry)

    candidates: list[dict[str, Any]] = []
    for c in components:
        key = component_key(c["componenttype"], c["objectid"])
        requirers = required_by.get(key)
        if not requirers:
            continue
        candidates.append(
            {
                "componenttype": c["componenttype"],
                "type_name": component_type_name(c["componenttype"]),
                "objectid": c["objectid"],
                "name": _name(c["componenttype"], c["objectid"]),
                "rootcomponentbehavior": c["rootcomponentbehavior"],
                "required_by": requirers,
            }
        )

    return {
        "summary": {
            "total_components": len(components),
            "entity_count": entity_count,
            "whole_entity_count": len(whole_entities),
            "shell_count": len(shell_entities),
            "required_only_count": len(candidates),
            "by_type": by_type,
        },
        "whole_entities": whole_entities,
        "shell_entities": shell_entities,
        "required_only_candidates": candidates,
    }
