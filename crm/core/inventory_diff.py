"""Compare two migration inventories by component key (ADR 0032).

Records are matched by (kind, component key), never by GUID, and every key gets
one verdict. The diff is evidence, not proof: equal keys, fields and normalized
artifact hashes say the recorded customization agrees, never that two orgs
behave alike. It touches no org; both sides are manifests on disk.
"""

from __future__ import annotations

import json
import re
from collections.abc import Callable
from dataclasses import asdict, dataclass, replace
from pathlib import Path
from typing import Any, Literal, cast

from crm.core.inventory import (
    ATTRIBUTE_FACET_FIELDS,
    ENTITY_OWNED_KINDS,
    KINDS,
    READ_VERSIONS,
    READ_VERSIONS_TEXT,
    RELATIONSHIP_SIDES,
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
_ORIGIN_RANK = ("custom", "unknown", "system")
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
        "scope": inv.scope.to_dict(),
    }


def _same_scope(a: Inventory, b: Inventory) -> bool:
    """Whether both read the same components; reading usage on one side changes no scope."""
    return (
        replace(a.scope, usage_read=False).to_dict() == replace(b.scope, usage_read=False).to_dict()
    )


def _gaps(inv: Inventory) -> list[dict[str, Any]]:
    gaps = [asdict(g) for g in inv.gaps]
    gaps.sort(key=lambda g: json.dumps(g, sort_keys=True))
    return gaps


#: A diff side: ``a`` the earlier inventory, ``b`` the later.
Side = Literal["a", "b"]


@dataclass(frozen=True)
class Restriction:
    """The comparison was cut to a ``--solution`` side's solutions, so it is not a full diff.

    *narrowed* is the ``--solution`` side (``a`` or ``b``) and *solutions* its scope. *missing*
    names the solutions the whole-org side does not hold, which it can only compare by the
    narrowed side's keys. *left_out* counts the whole-org keys outside the comparison.
    """

    narrowed: Side
    solutions: tuple[str, ...]
    missing: tuple[str, ...]
    left_out: int

    def to_dict(self) -> dict[str, Any]:
        return {
            "narrowed": self.narrowed,
            "solutions": list(self.solutions),
            "missing": list(self.missing),
            "left_out": self.left_out,
        }

    def describe(self) -> str:
        """One line for the table output."""
        text = (
            f"{self.narrowed.upper()} narrowed to {', '.join(self.solutions)}; "
            f"{self.left_out} key(s) of the whole-org side left out"
        )
        if self.missing:
            whole = "B" if self.narrowed == "a" else "A"
            text += (
                f"; {', '.join(self.missing)} not on {whole}, compared by the narrowed keys alone"
            )
        return text


@dataclass(frozen=True)
class InventoryDiff:
    """The per-key comparison of inventory *a* (the earlier stage) with *b*."""

    a: Inventory
    b: Inventory
    entries: tuple[KeyDiff, ...]
    restriction: Restriction | None = None

    def _tally(self, group: Callable[[KeyDiff], str]) -> dict[str, dict[str, int]]:
        out: dict[str, dict[str, int]] = {}
        for entry in self.entries:
            per = out.setdefault(group(entry), {})
            per[entry.verdict] = per.get(entry.verdict, 0) + 1
        return out

    def counts(self) -> dict[str, dict[str, int]]:
        """Return each kind's count per verdict, verdicts with no key left out."""
        return self._tally(lambda e: e.kind)

    def by_origin(self) -> dict[str, dict[str, int]]:
        """Return the count per verdict for each origin, so system drift stays apart from custom."""
        return self._tally(lambda e: e.origin)

    def incomplete(self) -> dict[str, dict[str, str]]:
        """Return each v1 kind not read ``complete`` on both sides, with each side's status.

        Such a kind is listed even when it holds no record on either side, so a
        kind neither extraction could read still shows in the result.
        """
        out: dict[str, dict[str, str]] = {}
        for kind in KINDS:
            sa, sb = _statuses(kind, self.a, self.b)
            if not sa == sb == "complete":
                out[kind] = {"a": sa, "b": sb}
        return out

    def summary(self) -> dict[str, Any]:
        """Return the header, counts, incomplete kinds and gaps, without per-key entries."""
        a, b = _side(self.a), _side(self.b)
        header: dict[str, Any] = {
            "a": a,
            "b": b,
            "same_platform": a["platform"] == b["platform"],
            "same_scope": _same_scope(self.a, self.b),
            "usage_compared": self.a.scope.usage_read and self.b.scope.usage_read,
            "basis": _BASIS,
        }
        if self.restriction is not None:
            header["restricted"] = self.restriction.to_dict()
        return {
            "header": header,
            "counts": self.counts(),
            "by_origin": self.by_origin(),
            "incomplete": self.incomplete(),
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
    # Only when both decode to objects: a non-object is refused below as not an object.
    readable = all(isinstance(d, dict) for d in data)
    if readable and any(type(v) is not int or v not in READ_VERSIONS for v in versions):
        raise InventoryError(
            f"cannot diff: A has schema_version {versions[0]!r} and B has schema_version "
            f"{versions[1]!r}; this crm reads schema_version "
            f"{READ_VERSIONS_TEXT}"
        )
    out: list[Inventory] = []
    for label, folder, decoded in (("A", a, data[0]), ("B", b, data[1])):
        try:
            out.append(parse_manifest(decoded))
        except InventoryError as exc:
            raise InventoryError(f"{label} ({folder}): {exc}") from exc
    # A --solution side keeps stock, so against a whole-org side the check would always fire;
    # diff_inventories restricts that pair to the solutions instead.
    if _narrowed_side(*out) is None and out[0].scope.system != out[1].scope.system:
        raise InventoryError(
            "cannot diff: A and B differ in stock scope (scope.system"
            f" {out[0].scope.system} vs {out[1].scope.system}), so every stock component would"
            " read as added or removed; re-extract one side with or without --include-system"
        )
    return out[0], out[1]


def _same(x: object, y: object) -> bool:
    # JSON equality, so True never equals 1 the way it does in Python.
    return json.dumps(x, sort_keys=True) == json.dumps(y, sort_keys=True)


def _field_changes(x: Record, y: Record, usage: bool = False) -> tuple[FieldChange, ...]:
    out = [
        FieldChange(name, getattr(x, name), getattr(y, name))
        for name in _RECORD_FIELDS
        if not _same(getattr(x, name), getattr(y, name))
    ]
    for name in sorted(set(x.fields) | set(y.fields)):
        # A manifest written before the facets existed lacks them: one side's absence is
        # not a change, or every column would read as changed (#1156).
        if x.kind == "attribute" and name in ATTRIBUTE_FACET_FIELDS:
            if name not in x.fields or name not in y.fields:
                continue
        va, vb = x.fields.get(name), y.fields.get(name)
        if not _same(va, vb):
            out.append(FieldChange(f"fields.{name}", va, vb))
    # Counts and read times drift between runs, so only a state change is a difference; a row
    # one side did not read (no `usage`) is not compared, never read as unused.
    if usage and x.usage and y.usage and x.usage["state"] != y.usage["state"]:
        out.append(FieldChange("usage.state", x.usage["state"], y.usage["state"]))
    return tuple(out)


_ARTIFACT_SUFFIX = re.compile(r".*-[0-9a-f]{12}\.(.+)")


def _artifact_name(path: str) -> str:
    """The artifact's name within its record (`form.xml`), without the slug and digest."""
    m = _ARTIFACT_SUFFIX.fullmatch(path.rsplit("/", 1)[-1])
    return m.group(1) if m else path


def _artifact_changes(x: Record, y: Record) -> tuple[ArtifactChange, ...]:
    # Paired records pair their artifacts by name within the record, not by path: the
    # path carries the key's slug and digest, which a key change (v1 to v2 for the
    # application ribbon and default sitemap, #1070) moves for the same artifact.
    ha = {_artifact_name(art.path): art for art in x.artifacts}
    hb = {_artifact_name(art.path): art for art in y.artifacts}
    changes: list[ArtifactChange] = []
    for name in ha.keys() | hb.keys():
        a, b = ha.get(name), hb.get(name)
        shown = b or a
        ah = a.normalized_sha256 if a else None
        bh = b.normalized_sha256 if b else None
        if shown and ah != bh:
            changes.append(ArtifactChange(shown.path, ah, bh))
    return tuple(sorted(changes, key=lambda c: c.path))


def _statuses(kind: str, a: Inventory, b: Inventory) -> tuple[str, str]:
    ca, cb = a.coverage.get(kind), b.coverage.get(kind)
    return (ca.status if ca else "absent", cb.status if cb else "absent")


def _not_comparable(kind: str, a: Inventory, b: Inventory) -> str | None:
    sa, sb = _statuses(kind, a, b)
    if sa == sb == "complete":
        return None
    return f"{kind} coverage is {sa} on A and {sb} on B"


def _gap_for(inv: Inventory, kind: str, key: str) -> ExtractionGap | None:
    for gap in inv.gaps:
        if gap.key == key and gap.kind in (None, kind):
            return gap
    return None


def _gap_reason(label: str, gap: ExtractionGap) -> str:
    return f"extraction gap on {label}: {gap.reason}"


def _diff_key(
    kind: str,
    key: str,
    ra: list[Record],
    rb: list[Record],
    a: Inventory,
    b: Inventory,
    usage: bool,
) -> KeyDiff:
    # The first origin in rank order across both sides, so swapping A and B keeps the bucket.
    origin = min((r.origin for r in (*ra, *rb)), key=_ORIGIN_RANK.index)
    reason = _not_comparable(kind, a, b)
    if reason is not None:
        return KeyDiff(kind, key, "not_comparable", origin, reason=reason)
    if len(ra) > 1 or len(rb) > 1:
        return KeyDiff(
            kind,
            key,
            "ambiguous",
            origin,
            reason=f"the key repeats: {len(ra)} on A, {len(rb)} on B",
        )
    gap_a, gap_b = _gap_for(a, kind, key), _gap_for(b, kind, key)
    # What a side cannot expose is a gap there: a record or an artifact missing on a
    # side whose gap names the key is never a removal, an addition or a change.
    if not ra or not rb:
        label, gap = ("B", gap_b) if not rb else ("A", gap_a)
        if gap is not None:
            return KeyDiff(kind, key, "not_comparable", origin, reason=_gap_reason(label, gap))
        return KeyDiff(kind, key, "removed" if not rb else "added", origin)
    x, y = ra[0], rb[0]
    # Both sides' gaps, A first, so swapping A and B mirrors the labels and drops neither.
    gap = "; ".join(_gap_reason(s, g) for s, g in (("A", gap_a), ("B", gap_b)) if g) or None
    fields = _field_changes(x, y, usage)
    artifacts = tuple(
        c
        for c in _artifact_changes(x, y)
        if not (c.a is None and gap_a) and not (c.b is None and gap_b)
    )
    ids = (x.id, y.id) if x.id and y.id and x.id != y.id else None
    if fields or artifacts:
        verdict = "changed"
    elif gap is not None:
        # Nothing differs, but a side could not read all of it: no claim of sameness.
        return KeyDiff(kind, key, "not_comparable", origin, ids=ids, reason=gap)
    else:
        verdict = "identity_only" if ids else "unchanged"
    return KeyDiff(kind, key, verdict, origin, fields, artifacts, ids, gap)


def _narrowed_side(a: Inventory, b: Inventory) -> Side | None:
    """Return ``a`` or ``b`` when only that side is a ``--solution`` run, the other whole-org."""
    modes = (a.scope.mode, b.scope.mode)
    if modes == ("solutions", "org"):
        return "a"
    return "b" if modes == ("org", "solutions") else None


def _restricted_keys(
    narrowed: Inventory, whole: Inventory
) -> tuple[set[tuple[str, str]], tuple[str, ...]]:
    """Return the keys to compare and the narrowed solutions the whole-org side lacks.

    The narrowed side's own keys, plus the whole-org side's rows of the same-named
    solutions: each solution, its memberships and the members they name, and the
    subcomponents and ribbon diff of each custom entity member (a solution holds its own
    entity whole; a stock entity may be held as a shell, which the manifest cannot tell
    from a full hold), and the dependencies of every component kept.
    A source member the target dropped then reads ``removed``, not falls out of the comparison.
    """
    named = {name.casefold() for name in narrowed.scope.solutions}
    keys = {(r.kind, r.key) for r in narrowed.records}
    found: set[str] = set()
    entity_members: set[str] = set()
    for r in whole.records:
        if r.kind == "solution" and str(r.fields["unique_name"]).casefold() in named:
            found.add(str(r.fields["unique_name"]).casefold())
            keys.add((r.kind, r.key))
        elif r.kind == "solution_membership" and str(r.fields["solution"]).casefold() in named:
            keys.add((r.kind, r.key))
            keys.add((str(r.fields["member_kind"]), str(r.fields["member_key"])))
            if r.fields["member_kind"] == "entity":
                entity_members.add(str(r.fields["member_key"]))
    owned = {r.key for r in whole.records if r.kind == "entity" and r.origin == "custom"}
    owned &= entity_members
    for r in whole.records:
        if r.kind in (*ENTITY_OWNED_KINDS, "ribbon_diff") and r.fields.get("entity") in owned:
            keys.add((r.kind, r.key))
        elif r.kind == "relationship" and owned.intersection(
            r.fields.get(side) for side in RELATIONSHIP_SIDES
        ):
            keys.add((r.kind, r.key))
    keys |= {
        (r.kind, r.key)
        for r in whole.records
        if r.kind == "dependency"
        and (str(r.fields["dependent_kind"]), str(r.fields["dependent_key"])) in keys
    }
    missing = tuple(n for n in narrowed.scope.solutions if n.casefold() not in found)
    return keys, missing


def diff_inventories(a: Inventory, b: Inventory) -> InventoryDiff:
    """Compare *a* (the earlier stage) with *b*, one `KeyDiff` per component key.

    A verdict is decided in this order: ``not_comparable`` when either side's
    coverage of the kind is not ``complete``, ``ambiguous`` when the key repeats
    on either side, then ``added``, ``removed``, ``changed``, ``identity_only``
    or ``unchanged``. A usage ``state`` change counts only where both sides read usage
    (``scope.usage_read``); counts and read times are never compared. A record or
    artifact missing on a side whose extraction gap names the key is evidence that side
    could not read, not a difference, and a key a gap names is ``not_comparable``
    unless something else differs.
    GUIDs are compared only when both sides record one. Entries sort by kind,
    then key, so the result does not depend on manifest order.

    One ``--solution`` side against a whole-org side is *restricted*: only the narrowed
    side's keys and the whole-org side's rows of the same-named solutions (the solution,
    its memberships, their members and a custom entity member's subcomponents) are
    compared, and ``InventoryDiff.restriction`` says so and counts the keys left out.
    """
    groups: tuple[dict[tuple[str, str], list[Record]], ...] = ({}, {})
    for inv, group in zip((a, b), groups, strict=True):
        for record in inv.records:
            group.setdefault((record.kind, record.key), []).append(record)
    ga, gb = groups
    usage = a.scope.usage_read and b.scope.usage_read
    all_keys = set(ga) | set(gb)
    restriction = None
    side = _narrowed_side(a, b)
    if side is not None:
        narrowed, whole = (a, b) if side == "a" else (b, a)
        keep, missing = _restricted_keys(narrowed, whole)
        restriction = Restriction(
            narrowed=side,
            solutions=narrowed.scope.solutions,
            missing=missing,
            left_out=len(all_keys - keep),
        )
        all_keys &= keep
    entries = tuple(
        _diff_key(kind, key, ga.get((kind, key), []), gb.get((kind, key), []), a, b, usage)
        for kind, key in sorted(all_keys)
    )
    return InventoryDiff(a, b, entries, restriction)
