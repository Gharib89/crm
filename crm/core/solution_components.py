"""Pure component algebra for solutions: type-code map, normalise, diff, layer-conflicts.

Backend-free by design — no `D365Backend` / HTTP dependency, so these functions are
trivially unit-testable in isolation. Every name here is re-exported from
`crm.core.solution` for backward compatibility (callers and tests that reach them
via `crm.core.solution.<name>` keep working unchanged).
"""

from __future__ import annotations

from typing import Any


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
            raise ValueError(
                f"objectid must be a string, got {type(objectid).__name__}"
            )
        rcb_raw = row.get("rootcomponentbehavior")
        rcb: int | None = None if rcb_raw is None else int(rcb_raw)
        out.append({
            "componenttype": int(row["componenttype"]),
            "objectid": objectid.lower(),
            "rootcomponentbehavior": rcb,
        })
    out.sort(key=lambda c: (
        c["componenttype"],
        c["objectid"],
        c["rootcomponentbehavior"] if c["rootcomponentbehavior"] is not None else -1,
    ))
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

    missing    = [c for c in norm_expected if _key(c) not in live_keys]
    unexpected = [c for c in norm_live    if _key(c) not in expected_keys]
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
    form when the type is not in SOLUTION_COMPONENT_TYPES."""
    return _COMPONENT_TYPE_NAMES.get(componenttype, str(componenttype))


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
        conflicts.append({
            "componenttype": c["componenttype"],
            "type_name": component_type_name(c["componenttype"]),
            "objectid": c["objectid"],
            "managed_rootcomponentbehavior": c["rootcomponentbehavior"],
            "unmanaged_rootcomponentbehavior": match["rootcomponentbehavior"],
        })
    conflicts.sort(key=lambda c: (c["componenttype"], c["objectid"]))
    return conflicts
