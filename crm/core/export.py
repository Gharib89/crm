"""Bulk dataset export/import via CSV/JSON."""

from __future__ import annotations

import csv
import json
from collections.abc import Iterable
from pathlib import Path
from typing import Any

from crm.core import query as query_mod
from crm.core.lookup_bind import LOOKUP_LOGICAL_ANNOTATION
from crm.utils.d365_backend import D365Backend, D365Error, as_dict


def export_records(
    backend: D365Backend,
    entity_set: str,
    output_path: str | Path,
    *,
    select: list[str] | None = None,
    filter_: str | None = None,
    page_size: int = 500,
    max_records: int | None = None,
    fmt: str | None = None,
    include_lookups: bool = False,
) -> dict[str, Any]:
    """Page through an entity set and write to CSV, JSON or JSONL.

    `fmt` is `"csv"`, `"json"` or `"jsonl"`. If omitted, inferred from
    `output_path` suffix. `jsonl` writes one record per line with each lookup's
    `lookuplogicalname` annotation, the shape `data import` binds back (#1283).
    `include_lookups` adds that annotation to `json` output and the
    `_<attr>_value` columns to `csv` output; without it both are unchanged.
    """
    out = Path(output_path)
    fmt = (fmt or out.suffix.lstrip(".") or "json").lower()
    if fmt not in ("csv", "json", "jsonl"):
        raise D365Error(f"Unsupported export format: {fmt!r} (use csv, json or jsonl)")

    # Only json-shaped output carries the annotation; csv's lookup columns are
    # the `_<attr>_value` keys every read returns.
    annotate = fmt == "jsonl" or (fmt == "json" and include_lookups)
    records = list(
        _iter_records(
            backend,
            entity_set,
            select=select,
            filter_=filter_,
            page_size=page_size,
            max_records=max_records,
            annotate=annotate,
        )
    )

    try:
        out.parent.mkdir(parents=True, exist_ok=True)
        if fmt == "json":
            out.write_text(json.dumps(records, indent=2, default=str), encoding="utf-8")
        elif fmt == "jsonl":
            out.write_text(
                "".join(json.dumps(r, default=str) + "\n" for r in records), encoding="utf-8"
            )
        else:
            _write_csv(out, records, select=select, include_lookups=include_lookups)
    except OSError as exc:
        raise D365Error(f"cannot write export file {out}: {exc}") from exc

    return {
        "output": str(out),
        "format": fmt,
        "count": len(records),
        "entity_set": entity_set,
    }


def _iter_records(
    backend: D365Backend,
    entity_set: str,
    *,
    select: list[str] | None,
    filter_: str | None,
    page_size: int,
    max_records: int | None,
    annotate: bool = False,
) -> Iterable[dict[str, Any]]:
    fetched = 0
    next_link: str | None = None
    # A next page carries only the annotations its own request asks for, so an
    # annotated export resends the annotation request; a default export keeps
    # today's bare next-link GET.
    next_kw: dict[str, Any] = (
        {"extra_headers": {"Prefer": f'odata.include-annotations="{LOOKUP_LOGICAL_ANNOTATION}"'}}
        if annotate
        else {}
    )
    while True:
        if next_link:
            page = as_dict(backend.get(next_link, **next_kw))
        else:
            page = query_mod.odata_query(
                backend,
                entity_set,
                select=select,
                filter_=filter_,
                page_size=page_size,
                include_annotations=LOOKUP_LOGICAL_ANNOTATION if annotate else False,
            )
        for rec in page.get("value", []):
            if max_records is not None and fetched >= max_records:
                return
            fetched += 1
            yield rec
        next_link = page.get("@odata.nextLink")
        if not next_link:
            return


def _write_csv(
    out: Path, records: list[dict[str, Any]], *, select: list[str] | None, include_lookups: bool
) -> None:
    if not records:
        out.write_text("", encoding="utf-8")
        return
    fieldnames = select or _ordered_keys(records, include_lookups=include_lookups)
    with out.open("w", encoding="utf-8", newline="") as f:
        w = csv.DictWriter(f, fieldnames=fieldnames, extrasaction="ignore")
        w.writeheader()
        for rec in records:
            w.writerow({k: _flatten(rec.get(k)) for k in fieldnames})


def _ordered_keys(records: list[dict[str, Any]], *, include_lookups: bool = False) -> list[str]:
    seen: list[str] = []
    seen_set: set[str] = set()
    for rec in records:
        for k in rec.keys():
            # `_<attr>_value` lookups only on request; annotations never.
            if "@" in k or (k.startswith("_") and not include_lookups):
                continue
            if k not in seen_set:
                seen.append(k)
                seen_set.add(k)
    return seen


def _flatten(v: Any) -> Any:
    if isinstance(v, (dict, list)):
        return json.dumps(v, default=str)
    return v
