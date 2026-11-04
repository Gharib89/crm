"""Compare two migration inventories by component key (ADR 0032).

Records are matched by (kind, component key), never by GUID, and every key gets
one verdict. The diff is evidence, not proof: equal keys, fields and normalized
artifact hashes say the recorded customization agrees, never that two orgs
behave alike. It touches no org; both sides are manifests on disk.
"""

from __future__ import annotations

import json
from dataclasses import asdict, dataclass
from pathlib import Path
from typing import Any, cast

from crm.core.inventory import (
    SCHEMA_VERSION,
    ExtractionGap,
    Inventory,
    InventoryError,
    Record,
    load_manifest,
    parse_manifest,
)

#: Every verdict, in report order. ``identity_only``: only the GUID differs, kept
#: apart from ``changed`` because a migration route may re-create components.
VERDICTS = (
    "added",
    "removed",
    "changed",
    "identity_only",
    "unchanged",
    "ambiguous",
    "not_comparable",
)
_RECORD_FIELDS = ("display_name", "origin", "managed", "publication_state")
_BASIS = (
    "matched by component key; compared on recorded fields and normalized artifact "
    "hashes; agreement is evidence, not proof of equivalent behavior"
)


@dataclass(frozen=True)
class FieldChange:
    """A differing value: a record field by name, a ``fields`` entry as ``fields.<name>``."""

    name: str
    a: Any
    b: Any


@dataclass(frozen=True)
class ArtifactChange:
    """An artifact whose ``normalized_sha256`` differs; ``None`` on the side that lacks it."""

    path: str
    a: str | None
    b: str | None


@dataclass(frozen=True)
class KeyDiff:
    """One component key's verdict and the evidence behind it."""

    kind: str
    key: str
    verdict: str
    origin: str
    fields: tuple[FieldChange, ...] = ()
    artifacts: tuple[ArtifactChange, ...] = ()
    ids: tuple[str, str] | None = None
    reason: str | None = None

    def to_dict(self) -> dict[str, Any]:
        out: dict[str, Any] = {
            "kind": self.kind,
            "key": self.key,
            "verdict": self.verdict,
            "origin": self.origin,
        }
        if self.fields:
            out["fields"] = [asdict(f) for f in self.fields]
        if self.artifacts:
            out["artifacts"] = [asdict(a) for a in self.artifacts]
        if self.ids is not None:
            out["ids"] = {"a": self.ids[0], "b": self.ids[1]}
        if self.reason is not None:
            out["reason"] = self.reason
        return out


def _side(inv: Inventory) -> dict[str, Any]:
    return {
        "platform": inv.source.platform,
        "version": inv.source.version,
        "org_url": inv.source.org_url,
        "extracted_at": inv.extracted_at,
        "scope": {"mode": inv.scope.mode, "solutions": sorted(inv.scope.solutions)},
    }


def _gaps(inv: Inventory) -> list[dict[str, Any]]:
    gaps = [asdict(g) for g in inv.gaps]
    gaps.sort(key=lambda g: json.dumps(g, sort_keys=True))
    return gaps


@dataclass(frozen=True)
class InventoryDiff:
    """The per-key comparison of inventory *a* (the earlier stage) with *b*."""

    a: Inventory
    b: Inventory
    entries: tuple[KeyDiff, ...]

    def counts(self) -> dict[str, dict[str, int]]:
        """Return each kind's count per verdict, verdicts with no key left out."""
        out: dict[str, dict[str, int]] = {}
        for entry in self.entries:
            per = out.setdefault(entry.kind, {})
            per[entry.verdict] = per.get(entry.verdict, 0) + 1
        return out

    def by_origin(self) -> dict[str, dict[str, int]]:
        """Return the count per verdict for each origin, so system drift stays apart from custom."""
        out: dict[str, dict[str, int]] = {}
        for entry in self.entries:
            per = out.setdefault(entry.origin, {})
            per[entry.verdict] = per.get(entry.verdict, 0) + 1
        return out

    def summary(self) -> dict[str, Any]:
        """Return the header, counts and both sides' extraction gaps, without per-key entries."""
        a, b = _side(self.a), _side(self.b)
        return {
            "header": {
                "a": a,
                "b": b,
                "same_platform": a["platform"] == b["platform"],
                "same_scope": a["scope"] == b["scope"],
                "basis": _BASIS,
            },
            "counts": self.counts(),
            "by_origin": self.by_origin(),
            "gaps": {"a": _gaps(self.a), "b": _gaps(self.b)},
        }

    def to_dict(self) -> dict[str, Any]:
        """Return the summary plus every key's entry."""
        return {**self.summary(), "entries": [e.to_dict() for e in self.entries]}


def dump_diff(diff: InventoryDiff) -> str:
    """Serialize *diff* deterministically: the same two inventories give the same bytes."""
    return json.dumps(diff.to_dict(), sort_keys=True, ensure_ascii=False, indent=2) + "\n"


def _schema_version(data: object) -> object:
    return cast("dict[str, Any]", data).get("schema_version") if isinstance(data, dict) else None


def read_pair(a: Path, b: Path) -> tuple[Inventory, Inventory]:
    """Read the migration inventories at folders *a* and *b*.

    Raises:
        InventoryError: either manifest is unreadable or breaks the contract; a
            schema_version this crm cannot read names both sides' versions.
    """
    data = (load_manifest(a), load_manifest(b))
    versions = [_schema_version(d) for d in data]
    if any(type(v) is not int or v != SCHEMA_VERSION for v in versions):
        raise InventoryError(
            f"cannot diff: A has schema_version {versions[0]!r} and B has schema_version "
            f"{versions[1]!r}; this crm reads schema_version {SCHEMA_VERSION}"
        )
    out: list[Inventory] = []
    for label, folder, decoded in (("A", a, data[0]), ("B", b, data[1])):
        try:
            out.append(parse_manifest(decoded))
        except InventoryError as exc:
            raise InventoryError(f"{label} ({folder}): {exc}") from exc
    return out[0], out[1]


def _same(x: object, y: object) -> bool:
    # JSON equality, so True never equals 1 the way it does in Python.
    return json.dumps(x, sort_keys=True) == json.dumps(y, sort_keys=True)


def _field_changes(x: Record, y: Record) -> tuple[FieldChange, ...]:
    out = [
        FieldChange(name, getattr(x, name), getattr(y, name))
        for name in _RECORD_FIELDS
        if not _same(getattr(x, name), getattr(y, name))
    ]
    for name in sorted(set(x.fields) | set(y.fields)):
        va, vb = x.fields.get(name), y.fields.get(name)
        if not _same(va, vb):
            out.append(FieldChange(f"fields.{name}", va, vb))
    return tuple(out)


def _artifact_changes(x: Record, y: Record) -> tuple[ArtifactChange, ...]:
    # A unique key's artifact path is a function of kind, key and artifact name
    # (`artifact_path`), so the same artifact has the same path in both inventories.
    ha = {art.path: art.normalized_sha256 for art in x.artifacts}
    hb = {art.path: art.normalized_sha256 for art in y.artifacts}
    return tuple(
        ArtifactChange(path, ha.get(path), hb.get(path))
        for path in sorted(set(ha) | set(hb))
        if ha.get(path) != hb.get(path)
    )


def _not_comparable(kind: str, a: Inventory, b: Inventory) -> str | None:
    ca, cb = a.coverage.get(kind), b.coverage.get(kind)
    sa = ca.status if ca else "absent"
    sb = cb.status if cb else "absent"
    if sa == sb == "complete":
        return None
    return f"{kind} coverage is {sa} on A and {sb} on B"


def _gap_for(inv: Inventory, kind: str, key: str) -> ExtractionGap | None:
    for gap in inv.gaps:
        if gap.key == key and gap.kind in (None, kind):
            return gap
    return None


def _diff_key(
    kind: str, key: str, ra: list[Record], rb: list[Record], a: Inventory, b: Inventory
) -> KeyDiff:
    origin = (rb or ra)[0].origin
    reason = _not_comparable(kind, a, b)
    if reason is not None:
        return KeyDiff(kind, key, "not_comparable", origin, reason=reason)
    for label, inv in (("A", a), ("B", b)):
        gap = _gap_for(inv, kind, key)
        if gap is not None:
            # What a side cannot expose is a gap there, never a change, removal or addition.
            reason = f"extraction gap on {label}: {gap.reason}"
            return KeyDiff(kind, key, "not_comparable", origin, reason=reason)
    if len(ra) > 1 or len(rb) > 1:
        return KeyDiff(
            kind,
            key,
            "ambiguous",
            origin,
            reason=f"the key repeats: {len(ra)} on A, {len(rb)} on B",
        )
    if not ra or not rb:
        return KeyDiff(kind, key, "removed" if not rb else "added", origin)
    x, y = ra[0], rb[0]
    fields, artifacts = _field_changes(x, y), _artifact_changes(x, y)
    ids = (x.id, y.id) if x.id and y.id and x.id != y.id else None
    if fields or artifacts:
        verdict = "changed"
    else:
        verdict = "identity_only" if ids else "unchanged"
    return KeyDiff(kind, key, verdict, origin, fields, artifacts, ids)


def diff_inventories(a: Inventory, b: Inventory) -> InventoryDiff:
    """Compare *a* (the earlier stage) with *b*, one `KeyDiff` per component key.

    A verdict is decided in this order: ``not_comparable`` when either side's
    coverage of the kind is not ``complete`` or either side names the key in an
    extraction gap, ``ambiguous`` when the key repeats on either side,
    then ``added``, ``removed``, ``changed``, ``identity_only`` or ``unchanged``.
    GUIDs are compared only when both sides record one. Entries sort by kind,
    then key, so the result does not depend on manifest order.
    """
    groups: tuple[dict[tuple[str, str], list[Record]], ...] = ({}, {})
    for inv, group in zip((a, b), groups, strict=True):
        for record in inv.records:
            group.setdefault((record.kind, record.key), []).append(record)
    ga, gb = groups
    entries = tuple(
        _diff_key(kind, key, ga.get((kind, key), []), gb.get((kind, key), []), a, b)
        for kind, key in sorted(set(ga) | set(gb))
    )
    return InventoryDiff(a, b, entries)
