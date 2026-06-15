"""Output / envelope rendering helpers for crm.commands.*."""
# pyright: basic
from __future__ import annotations
import json
from typing import TYPE_CHECKING, Any
if TYPE_CHECKING:
    from crm.cli import CLIContext


def _sanitize(obj: Any) -> Any:
    if isinstance(obj, dict):
        return {k: _sanitize(v) for k, v in obj.items()}
    if isinstance(obj, list):
        return [_sanitize(x) for x in obj]
    if isinstance(obj, bytes):
        return obj.decode("utf-8", errors="replace")
    return obj


# Lowercase, case-sensitive: matches OData protocol keys (`@odata.context`,
# `@odata.etag`, `@odata.nextLink`, `@odata.type`, `<nav>@odata.bind`, …) while
# deliberately NOT matching opted-in annotations whose marker is capitalized
# (`<attr>@OData.Community.Display.V1.FormattedValue`, `@Microsoft.Dynamics.CRM.*`).
_ODATA_PROTOCOL_MARKER = "@odata."


def _strip_odata_keys(obj: Any) -> Any:
    """Recursively drop OData protocol keys from the curated `data` payload.

    The emit envelope is a CLI-owned shape (ADR 0008), not a passthrough of the
    raw Web API response: `@odata.*` protocol keys (etag/context/type/…) carry no
    business value and differ per command, so they are stripped everywhere — list
    rows, single records, and nested expansions alike. Lookup GUIDs (`_*_value`),
    the synthesized `_entity_id`, and formatted-value annotations (capitalized
    `@OData.Community`/`@Microsoft.*`, surfaced only under `--annotations`) are
    kept.
    """
    if isinstance(obj, dict):
        return {k: _strip_odata_keys(v)
                for k, v in obj.items() if _ODATA_PROTOCOL_MARKER not in k}
    if isinstance(obj, list):
        return [_strip_odata_keys(x) for x in obj]
    return obj


def _normalize_odata_envelope(data: Any) -> "tuple[Any, dict[str, Any]]":
    """Unwrap a Web API collection envelope to a bare array, lifting paging.

    Returns ``(payload, paging)``. When *data* is a collection response (a dict
    with an ``@odata.context`` and a list ``value``), *payload* is the bare
    ``value`` array and *paging* carries ``next_link`` (← ``@odata.nextLink``) and
    ``count`` (← ``@odata.count``) when the server supplied them. Otherwise *data*
    is returned unchanged with an empty *paging*. Detection keys on
    ``@odata.context`` so a hand-built ``{"value": [...]}`` (no protocol keys) is
    left alone.
    """
    if (isinstance(data, dict) and "@odata.context" in data
            and isinstance(data.get("value"), list)):
        paging: dict[str, Any] = {}
        if "@odata.nextLink" in data:
            paging["next_link"] = data["@odata.nextLink"]
        if "@odata.count" in data:
            paging["count"] = data["@odata.count"]
        return data["value"], paging
    return data, {}


def _short_repr(v: Any, limit: int = 80) -> str:
    s = json.dumps(v, default=str) if isinstance(v, (dict, list)) else str(v)
    return s if len(s) <= limit else s[: limit - 3] + "..."


def _emit_with_warning(
    ctx: "CLIContext", data: Any, warning: str | None,
    *, meta: dict[str, Any] | None = None,
) -> None:
    """Emit a successful result, surfacing advisories via the warnings channel.

    Rolls the solution `warning` (if any), any `*_lookup_error` read-back keys,
    and any dangling `data["references"]` entries (#281) into the structured
    `meta.warnings` array (#64) — appending, never clobbering. The
    `*_lookup_error` keys and the `references` array stay in `data`. In human
    mode emit prints each via skin.warning.
    """
    from crm.core.references import reference_warnings

    warnings: list[str] = []
    if warning:
        warnings.append(warning)
    if isinstance(data, dict):
        for key, value in data.items():
            if key.endswith("_lookup_error") and value:
                warnings.append(str(value))
        warnings.extend(reference_warnings(data.get("references")))
    ctx.emit(True, data=data, meta=meta, warnings=warnings or None)


def _emit_expectation_failure(ctx: "CLIContext", miss: dict[str, Any]) -> None:
    """Emit the standard `--expect` mismatch envelope (exit 1).

    `miss` is the {attr, expected, actual} dict from `_check_expectations`. The
    human-readable error string embeds the same three values because `emit`'s
    human-mode failure path renders only `error`, not `meta`."""
    ctx.emit(
        False,
        error=f"Expectation failed: {miss['attr']}={miss['expected']!r} "
              f"(actual {miss['actual']!r})",
        meta=miss,
    )


def _prune_annotations(record: dict[str, Any]) -> dict[str, Any]:
    """Drop OData annotation keys (any key containing '@') from a record,
    keeping business fields, `_*_value` lookup GUIDs, and the primary id.

    Shallow prune: only top-level keys are stripped — annotations nested
    inside expanded records (under `--expand`) are not pruned."""
    return {k: v for k, v in record.items() if "@" not in k}


def _emit_query_result(
    ctx: "CLIContext", result: dict, entity_set: str, *, minimal: bool = False,
) -> None:
    values = result.get("value", []) if isinstance(result, dict) else []
    meta: dict[str, Any] = {"entity_set": entity_set}
    if ctx.json_mode:
        # Hand emit the raw OData envelope; the central normalizer unwraps it to a
        # bare array, relocates `@odata.nextLink`/`@odata.count` → `meta`, and
        # strips per-row `@odata.*` (ADR 0008). `--minimal` additionally drops the
        # opted-in formatted-value annotations the central strip keeps.
        if minimal:
            result = {**result, "value": [
                _prune_annotations(r) if isinstance(r, dict) else r for r in values
            ]}
        ctx.emit(True, data=result, meta=meta)
        return
    if not values:
        ctx.skin.info("No results.")
        return
    # Hoist the entity's primary-name column when its metadata is already cached
    # (never via an added round-trip — ADR 0008): a cold cache simply leaves the
    # column order as-is.
    from crm.core.entity_names import cached_primary_name
    primary_name = cached_primary_name(ctx.backend(), entity_set)
    headers = _infer_columns(values, primary_name=primary_name)
    rows = [[_short_repr(rec.get(h, ""), 40) for h in headers] for rec in values[:50]]
    ctx.emit(True, table={"headers": headers, "rows": rows}, meta=meta)
    if len(values) > 50:
        ctx.skin.hint(f"... {len(values) - 50} more rows")


def _infer_columns(values: list[dict], primary_name: str | None = None) -> list[str]:
    """Pick up to 8 table columns, hoisting the primary-name column first.

    *primary_name* (the entity's PrimaryNameAttribute, supplied only when already
    cached — never via an added round-trip) is placed first so a list table
    surfaces the record's name rather than burying it behind system columns or
    dropping it past the 8-column cap (#302 repro #2)."""
    cols: list[str] = []
    seen: set[str] = set()
    if primary_name and any(primary_name in rec for rec in values[:5]):
        cols.append(primary_name)
        seen.add(primary_name)
    for rec in values[:5]:
        for k in rec.keys():
            if k.startswith("@") or k in seen:
                continue
            cols.append(k)
            seen.add(k)
    return cols[:8]
