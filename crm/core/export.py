"""Bulk dataset export/import via CSV/JSON."""

from __future__ import annotations

import csv
import json
from pathlib import Path
from typing import Iterable

from crm.utils.d365_backend import D365Backend, D365Error
from crm.core import query as query_mod


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
) -> dict:
    """Page through an entity set and write to CSV or JSON.

    `fmt` is `"csv"` or `"json"`. If omitted, inferred from `output_path` suffix.
    """
    out = Path(output_path)
    fmt = (fmt or out.suffix.lstrip(".") or "json").lower()
    if fmt not in ("csv", "json"):
        raise D365Error(f"Unsupported export format: {fmt!r} (use csv or json)")

    records = list(_iter_records(
        backend, entity_set,
        select=select, filter_=filter_,
        page_size=page_size, max_records=max_records,
    ))

    out.parent.mkdir(parents=True, exist_ok=True)
    if fmt == "json":
        out.write_text(json.dumps(records, indent=2, default=str), encoding="utf-8")
    else:
        _write_csv(out, records, select=select)

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
) -> Iterable[dict]:
    fetched = 0
    next_link: str | None = None
    while True:
        if next_link:
            page = backend.get(next_link) or {}
        else:
            page = query_mod.odata_query(
                backend, entity_set,
                select=select, filter_=filter_,
                page_size=page_size,
            )
        for rec in page.get("value", []):
            fetched += 1
            yield rec
            if max_records is not None and fetched >= max_records:
                return
        next_link = page.get("@odata.nextLink")
        if not next_link:
            return


def _write_csv(out: Path, records: list[dict], *, select: list[str] | None) -> None:
    if not records:
        out.write_text("", encoding="utf-8")
        return
    fieldnames = select or _ordered_keys(records)
    with out.open("w", encoding="utf-8", newline="") as f:
        w = csv.DictWriter(f, fieldnames=fieldnames, extrasaction="ignore")
        w.writeheader()
        for rec in records:
            w.writerow({k: _flatten(rec.get(k)) for k in fieldnames})


def _ordered_keys(records: list[dict]) -> list[str]:
    seen: list[str] = []
    seen_set: set[str] = set()
    for rec in records:
        for k in rec.keys():
            if k.startswith("@") or k.startswith("_") and not k.startswith("_"):
                continue
            if k.startswith("@"):
                continue
            if k not in seen_set:
                seen.append(k)
                seen_set.add(k)
    return seen


def _flatten(v):
    if isinstance(v, (dict, list)):
        return json.dumps(v, default=str)
    return v
