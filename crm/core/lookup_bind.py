"""Rewrite READ-format lookups into WRITE-format bindings (#333).

A retrieve / ``data export`` returns each lookup in the server's READ shape — a
raw ``_<attr>_value`` GUID plus OData annotations — but the Web API only *writes*
a lookup through its single-valued navigation property as
``<NavProp>@odata.bind: "/<set>(<guid>)"``. The two formats are asymmetric, so
export output is not round-trip importable without hand-converting every lookup.

This module closes that gap on the write side: :func:`build_resolver` reads the
entity's relationship metadata once, and :func:`bind_lookups` rewrites a record's
``_<attr>_value`` lookups to the bind form (and drops read-only OData annotation
keys). It is shared by ``data import`` and the ``entity create`` / ``upsert``
command payloads, so any export / ``query odata`` row imports unedited.
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from typing import Any

from crm.core import entity_names, metadata
from crm.utils.d365_backend import D365Backend, D365Error

# Polymorphic lookup types: a single column points at more than one target table
# (Customer → account|contact, Owner → systemuser|team). The concrete target is
# not in the column itself — its single-target relationship is often the abstract
# base table (`owner`), so binding it requires the per-value lookuplogicalname
# annotation. Plain `Lookup` columns have exactly one concrete target.
POLYMORPHIC_TYPES = frozenset({"Customer", "Owner"})
# All lookup-flavoured attribute types whose value travels as a `_<attr>_value`
# property and must be rebound via `<nav>@odata.bind`.
LOOKUP_TYPES = frozenset({"Lookup"}) | POLYMORPHIC_TYPES

# Per-value annotation naming the target table's logical name — the only way to
# pick the right target (hence nav property) for a polymorphic lookup.
LOOKUP_LOGICAL_ANNOTATION = "Microsoft.Dynamics.CRM.lookuplogicalname"

# A READ-format lookup property: `_<attr>_value`.
_VALUE_RE = re.compile(r"^_(?P<attr>.+)_value$")


@dataclass(frozen=True)
class LookupResolver:
    """Per-entity metadata needed to rebind READ-format lookups.

    ``by_attr`` maps each lookup column to its ``[(referenced_logical, nav)]``
    targets; ``writable`` is the set of lookup columns valid for create or
    update; ``polymorphic`` is the subset of those whose concrete target needs
    the lookuplogicalname annotation; ``logical_to_set`` resolves a target's
    logical name to its entity set.
    """

    by_attr: dict[str, list[tuple[str, str]]]
    writable: set[str]
    polymorphic: set[str]
    logical_to_set: dict[str, str]


def build_resolver(backend: D365Backend, entity_set: str) -> LookupResolver:
    """Read the relationship metadata for *entity_set* once, for reuse per record.

    Resolves the entity-set name to its logical name, the lookup columns that are
    writable (valid for create or update) and which of those are polymorphic, and
    each lookup column's navigation-property targets. Raises ``D365Error`` for an
    unknown entity set.
    """
    name_map = entity_names.load_name_map(backend)
    logical = name_map.logical_for(entity_set)
    if not logical:
        raise D365Error(f"Unknown entity set: {entity_set!r}")
    by_attr = metadata.lookup_nav_map(backend, logical)
    writable: set[str] = set()
    polymorphic: set[str] = set()
    for s in entity_names.attribute_specs(backend, logical):
        if s.attribute_type in LOOKUP_TYPES and (s.valid_for_create or s.valid_for_update):
            writable.add(s.logical_name)
            # Polymorphic when the type says so (Customer/Owner → abstract base
            # table) OR when the column simply has more than one target table
            # (e.g. `regardingobjectid`, which reports type Lookup): either way
            # the concrete target is not knowable without the annotation.
            if s.attribute_type in POLYMORPHIC_TYPES or len(by_attr.get(s.logical_name, [])) > 1:
                polymorphic.add(s.logical_name)
    return LookupResolver(
        by_attr=by_attr,
        writable=writable,
        polymorphic=polymorphic,
        logical_to_set=name_map.logical_to_set,
    )


def needs_binding(record: dict[str, Any]) -> bool:
    """Whether *record* carries READ-format lookups or annotations to rewrite.

    A payload that is already in write shape — plain columns and hand-written
    ``<nav>@odata.bind`` directives, with no ``_<attr>_value`` and no read-only
    annotation — is left untouched, so the common path pays for no metadata fetch.
    """
    return any(
        _VALUE_RE.match(key) or ("@" in key and not key.endswith("@odata.bind"))
        for key in record
    )


def bind_lookups(record: dict[str, Any], resolver: LookupResolver) -> dict[str, Any]:
    """Return a copy of *record* with READ-format lookups rebound for writing.

    Each ``_<attr>_value`` for a writable lookup becomes
    ``<nav>@odata.bind: "/<set>(<guid>)"``; all other keys are carried verbatim.
    """
    out: dict[str, Any] = {}
    for key, value in record.items():
        if "@" in key:
            # Read-only OData annotations (@odata.etag/context, formatted values,
            # per-value lookup annotations) are rejected on write — drop them. A
            # caller-supplied `<nav>@odata.bind` is a write directive: keep it.
            if key.endswith("@odata.bind"):
                out[key] = value
            continue
        m = _VALUE_RE.match(key)
        if m:
            attr = m.group("attr")
            # A `_<attr>_value` that is not a writable lookup (a read-only system
            # lookup such as createdby, or one with no relationship metadata)
            # cannot be written — drop it rather than letting the server reject a
            # direct write to an entity-reference property.
            if attr not in resolver.writable:
                continue
            if value is None:
                # Clear the relationship: binding any one participating nav prop
                # to null clears the lookup (including all targets of a
                # polymorphic one), so the target table need not be resolved.
                nav = _first_nav(attr, resolver)
                _emit_bind(out, f"{nav}@odata.bind", None)
                continue
            resolved = _resolve_target(attr, resolver, record)
            if resolved is None:
                # Polymorphic lookup with no annotation to name the concrete
                # target — drop it rather than bind to the abstract base table.
                continue
            nav, target_set = resolved
            _emit_bind(out, f"{nav}@odata.bind", f"/{target_set}({value})")
            continue
        out[key] = value
    return out


def _emit_bind(out: dict[str, Any], bind_key: str, value: Any) -> None:
    """Write a rewritten ``<nav>@odata.bind`` without clobbering a hand-written one.

    A record may carry both a ``_<attr>_value`` and an explicit ``<nav>@odata.bind``
    for the same lookup; the caller-supplied bind always wins, regardless of key
    order, so a rewrite never overwrites a bind key already in the payload.
    """
    out.setdefault(bind_key, value)


def _first_nav(attr: str, resolver: LookupResolver) -> str:
    """The navigation property of *attr*'s first relationship (used to clear it)."""
    candidates = resolver.by_attr.get(attr) or []
    if not candidates or not candidates[0][1]:
        raise D365Error(
            f"{attr}: cannot resolve the lookup's navigation property from metadata."
        )
    return candidates[0][1]


def _resolve_target(
    attr: str, resolver: LookupResolver, record: dict[str, Any]
) -> tuple[str, str] | None:
    """Resolve a lookup column to its ``(nav_property, target_set)`` for binding.

    A single-target ``Lookup`` resolves directly from its one concrete
    relationship. A polymorphic ``Customer`` / ``Owner`` column needs the
    record's ``…@lookuplogicalname`` annotation to name the concrete target:
    returns ``None`` when it is absent (the caller drops the lookup rather than
    bind the abstract base table), and raises ``D365Error`` when an annotation
    is present but names an entity that cannot be resolved.
    """
    candidates = resolver.by_attr.get(attr) or []
    if attr not in resolver.polymorphic:
        if not candidates or not candidates[0][1]:
            raise D365Error(
                f"{attr}: cannot resolve the navigation property from metadata."
            )
        ref_logical, nav = candidates[0]
        target_set = resolver.logical_to_set.get(ref_logical, "")
        if not target_set:
            raise D365Error(
                f"{attr}: cannot resolve the target entity set from metadata."
            )
        return nav, target_set

    target_logical = record.get(f"_{attr}_value@{LOOKUP_LOGICAL_ANNOTATION}")
    if not target_logical:
        return None
    target_set = resolver.logical_to_set.get(target_logical)
    if not target_set:
        raise D365Error(
            f"{attr}: lookuplogicalname {target_logical!r} is not a known entity."
        )
    # The nav property is the concrete relationship's, or — when the column's
    # only relationship is to the abstract base table (Owner → owner) — that
    # single relationship's nav, with the concrete set from the annotation.
    concrete = [nv for rl, nv in candidates if rl == target_logical]
    if concrete:
        nav = concrete[0]
    elif len(candidates) == 1:
        nav = candidates[0][1]
    else:
        raise D365Error(
            f"{attr}: cannot resolve the navigation property for target "
            f"{target_logical!r} from metadata."
        )
    if not nav:
        raise D365Error(
            f"{attr}: cannot resolve the navigation property from metadata."
        )
    return nav, target_set
