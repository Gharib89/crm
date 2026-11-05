"""Usage in `crm inventory packages` and `assess`: derived from the manifest, then filtered.

A record's usage is the one `extract --with-usage` read, or one derived here from the manifest
alone, never from a live read. Two derivations exist, each honest about what it cannot prove:

- A web resource a form, ribbon or sitemap points at is `used` (signal `referenced_by`, a static
  reference). One nothing points at is `unknown`, never `unused`: a script can be loaded by name
  at run time or from outside the org.
- A form, view or ribbon of an entity whose own usage is `unused` (an existence read found no
  record) is `unused` (signal `entity_rollup`). Low confidence: the data may have been deleted or
  migrated. Any other entity state leaves the row as it was.

A row with no usage was not read, which is not `unused`: every filter keeps it.
"""

from __future__ import annotations

from collections.abc import Iterable, Sequence
from dataclasses import replace
from typing import TYPE_CHECKING

from crm.core.inventory import Record
from crm.core.inventory_assess import AssessResult, Finding
from crm.core.usage import Usage, roll_up

if TYPE_CHECKING:
    from crm.core.inventory_packages import Edge

REFERENCE_SIGNAL = "referenced_by"
ENTITY_SIGNAL = "entity_rollup"
MEMBER_SIGNAL = "member_rollup"
SORTS = ("default", "usage")

_REFERRERS = frozenset(("form", "ribbon", "sitemap"))
_ENTITY_OWNED = frozenset(("form", "view", "ribbon"))
_RANK = {"used": 0, "unknown": 1, "unused": 2}
_SHOWN = 3  # referrers named in a `used` reason


def _own(record: Record) -> Usage | None:
    d = record.usage
    if d is None:
        return None
    return Usage(d["state"], d["signal"], d.get("value"), d["as_of"], d.get("reason"))


def derive_usage(
    records: Sequence[Record], edges: Iterable[Edge], as_of: str
) -> dict[str, Usage | None]:
    """Each record's usage by node id (``kind:key``); None where it was not read or derived."""
    referrers: dict[str, set[str]] = {}
    for e in edges:
        if e.target and e.source.split(":", 1)[0] in _REFERRERS:
            referrers.setdefault(e.target, set()).add(e.source)
    entity_state = {r.key: r.usage["state"] for r in records if r.kind == "entity" and r.usage}
    out: dict[str, Usage | None] = {}
    for r in records:
        node = f"{r.kind}:{r.key}"
        usage = _own(r)
        if usage is None and r.kind == "web_resource":
            by = sorted(referrers.get(node, ()))
            if by:
                more = f" and {len(by) - _SHOWN} more" if len(by) > _SHOWN else ""
                why = f"static, referenced by {', '.join(by[:_SHOWN])}{more}"
                usage = Usage("used", REFERENCE_SIGNAL, None, as_of, why)
            else:
                usage = Usage("unknown", REFERENCE_SIGNAL, None, as_of, "unreferenced")
        elif usage is None and r.kind in _ENTITY_OWNED:
            entity = r.fields.get("entity")
            if isinstance(entity, str) and entity_state.get(entity) == "unused":
                why = (
                    "entity has no records (low confidence: data may have been deleted or migrated)"
                )
                usage = Usage("unused", ENTITY_SIGNAL, None, as_of, why)
        out[node] = usage
    return out


def package_usage(members: Iterable[Usage | None], as_of: str) -> Usage | None:
    """A package's usage from its members: `used` if any is, `unused` only if every one is.

    A member with no usage counts as `unknown`, so it keeps a package from reading `unused`.
    None when no member has a usage at all (nothing was read or derived).
    """
    items = list(members)
    if not any(items):
        return None
    unread = Usage("unknown", MEMBER_SIGNAL, None, as_of)
    rolled = roll_up([u or unread for u in items], as_of)
    why = {"unused": "every member is unused", "unknown": "no member is known used"}
    return replace(rolled, signal=MEMBER_SIGNAL, reason=why.get(rolled.state))


def keeps(usage: Usage | None, *, in_use: bool, exclude_unknown: bool) -> bool:
    """Whether a row stays: `--in-use` drops `unused`, `--exclude-unknown` also `unknown`.

    A row with no usage was not read, so it stays under both flags.
    """
    if usage is None:
        return True
    return not (
        (in_use and usage.state == "unused") or (exclude_unknown and usage.state == "unknown")
    )


def rank(state: str | None) -> int:
    """Sort position for ``--sort usage``: used first, then unknown (or not read), then unused."""
    return _RANK[state or "unknown"]


def filter_findings(
    result: AssessResult,
    records: Sequence[Record],
    edges: Iterable[Edge],
    as_of: str,
    *,
    in_use: bool,
    exclude_unknown: bool,
    sort: str,
) -> AssessResult:
    """Give each finding its row's usage, drop the rows the flags exclude, order if asked."""
    usages = derive_usage(records, edges, as_of)
    findings: list[Finding] = []
    for f in result.findings:
        usage = usages.get(f"{f.kind}:{f.key}")
        if keeps(usage, in_use=in_use, exclude_unknown=exclude_unknown):
            findings.append(replace(f, usage=usage.to_dict()) if usage else f)
    if sort == "usage":
        findings.sort(key=lambda f: rank(f.usage["state"] if f.usage else None))
    return replace(result, findings=tuple(findings))
