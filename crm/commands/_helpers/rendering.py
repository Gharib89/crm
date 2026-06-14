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
    meta: dict[str, Any] = {"entity_set": entity_set, "count": len(values)}
    if "@odata.count" in (result or {}):
        meta["odata_count"] = result["@odata.count"]
    if "@odata.nextLink" in (result or {}):
        meta["next_link"] = "(present)"
    if ctx.json_mode:
        if minimal:
            result = {**result, "value": [
                _prune_annotations(r) if isinstance(r, dict) else r for r in values
            ]}
        ctx.emit(True, data=result, meta=meta)
        return
    if not values:
        ctx.skin.info("No results.")
        return
    headers = _infer_columns(values)
    rows = [[_short_repr(rec.get(h, ""), 40) for h in headers] for rec in values[:50]]
    ctx.emit(True, table={"headers": headers, "rows": rows}, meta=meta)
    if len(values) > 50:
        ctx.skin.hint(f"... {len(values) - 50} more rows")


def _infer_columns(values: list[dict]) -> list[str]:
    cols: list[str] = []
    seen: set[str] = set()
    for rec in values[:5]:
        for k in rec.keys():
            if k.startswith("@") or k in seen:
                continue
            cols.append(k)
            seen.add(k)
    return cols[:8]
