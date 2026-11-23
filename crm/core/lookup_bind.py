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
from crm.utils.d365_backend import D365Backend, D365Error, is_cloud

POLYMORPHIC_TYPES = frozenset({"Customer", "Owner"})
"""Lookup types whose one column points at more than one target table.

Customer targets account or contact, Owner targets systemuser or team. The
concrete target is not in the column itself: its single-target relationship is
often the abstract base table (``owner``), so binding it needs the per-value
``lookuplogicalname`` annotation. A plain ``Lookup`` column has exactly one
concrete target.
"""

LOOKUP_TYPES = frozenset({"Lookup"}) | POLYMORPHIC_TYPES
"""Every lookup attribute type: its value travels as ``_<attr>_value``.

Each one is rebound for writing via ``<nav>@odata.bind``.
"""

LOOKUP_LOGICAL_ANNOTATION = "Microsoft.Dynamics.CRM.lookuplogicalname"
"""The per-value annotation naming the target table's logical name.

It is the only way to pick the right target, and so the nav property, for a
polymorphic lookup.
"""

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

    A column is polymorphic when its type says so (Customer or Owner, bound
    through an abstract base table) or when it simply has more than one target
    table (``regardingobjectid`` reports type Lookup): either way the concrete
    target is not knowable without the annotation.
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
        _VALUE_RE.match(key) or ("@" in key and not key.endswith("@odata.bind")) for key in record
    )


def bind_lookups(
    record: dict[str, Any], resolver: LookupResolver, *, dropped: list[str] | None = None
) -> dict[str, Any]:
    """Return a copy of *record* with READ-format lookups rebound for writing.

    Each ``_<attr>_value`` for a writable lookup becomes
    ``<nav>@odata.bind: "/<set>(<guid>)"``; all other keys are carried verbatim.
    When *dropped* is given, the name of each polymorphic lookup left out for
    want of its ``lookuplogicalname`` annotation is appended to it, so a bulk
    caller can report the loss (#1283).

    Read-only OData annotations (``@odata.etag``, ``@odata.context``, formatted
    values, per-value lookup annotations) are rejected on write, so they are
    dropped; a caller-supplied ``<nav>@odata.bind`` is a write directive and is
    kept. A ``_<attr>_value`` that is not a writable lookup (a read-only system
    lookup such as createdby, or a non-lookup column) is dropped rather than let
    the server reject a direct write to an entity-reference property; a writable
    lookup missing its relationship metadata cannot happen and surfaces as a clear
    error from the resolver, not a silent drop. A null value binds the first
    participating nav property to null, which clears the whole lookup (every
    target of a polymorphic one), so the target table need not be resolved. A
    polymorphic lookup with no annotation is dropped rather than bound to the
    abstract base table; a hand-written bind on one of its navs still binds it,
    so nothing is lost and it is not reported in *dropped*.
    """
    out: dict[str, Any] = {}
    for key, value in record.items():
        if "@" in key:
            if key.endswith("@odata.bind"):
                out[key] = value
            continue
        m = _VALUE_RE.match(key)
        if m:
            attr = m.group("attr")
            if attr not in resolver.writable:
                continue
            if value is None:
                nav = _first_nav(attr, resolver)
                _emit_bind(out, f"{nav}@odata.bind", None)
                continue
            resolved = _resolve_target(attr, resolver, record)
            if resolved is None:
                bind_keys = {f"{nav}@odata.bind" for _, nav in resolver.by_attr.get(attr) or []}
                if dropped is not None and not bind_keys & record.keys():
                    dropped.append(attr)
                continue
            nav, target_set = resolved
            _emit_bind(out, f"{nav}@odata.bind", f"/{target_set}({value})")
            continue
        out[key] = value
    return out


def split_null_binds(
    backend: D365Backend, record: dict[str, Any]
) -> tuple[dict[str, Any], list[str]]:
    """Take each null ``<nav>@odata.bind`` out of an on-premises write body (#1325).

    On-premises v9 rejects a null bind with 400 ("The 'odata.bind' instance or
    property annotation has a null value") and fails the whole write, so an
    on-premises caller clears those lookups with ``DELETE <record>/<nav>/$ref``
    instead (a create has nothing to clear and drops them). Returns the body
    without the null binds and their navigation properties, in body order. On
    Dataverse online, which clears a lookup through a null bind, the body is
    returned unchanged with no clears.
    """
    suffix = "@odata.bind"
    clears = [k.removesuffix(suffix) for k, v in record.items() if k.endswith(suffix) and v is None]
    if not clears or is_cloud(backend.profile):
        return record, []
    return {k: v for k, v in record.items() if not (k.endswith(suffix) and v is None)}, clears


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
        raise D365Error(f"{attr}: cannot resolve the lookup's navigation property from metadata.")
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
    is present but names an entity that cannot be resolved. The nav property is
    the concrete relationship's, or, when the column's only relationship is to
    the abstract base table (Owner to owner), that single relationship's nav,
    with the concrete set taken from the annotation.
    """
    candidates = resolver.by_attr.get(attr) or []
    if attr not in resolver.polymorphic:
        if not candidates or not candidates[0][1]:
            raise D365Error(f"{attr}: cannot resolve the navigation property from metadata.")
        ref_logical, nav = candidates[0]
        target_set = resolver.logical_to_set.get(ref_logical, "")
        if not target_set:
            raise D365Error(f"{attr}: cannot resolve the target entity set from metadata.")
        return nav, target_set

    target_logical = record.get(f"_{attr}_value@{LOOKUP_LOGICAL_ANNOTATION}")
    if not target_logical:
        return None
    target_set = resolver.logical_to_set.get(target_logical)
    if not target_set:
        raise D365Error(f"{attr}: lookuplogicalname {target_logical!r} is not a known entity.")
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
        raise D365Error(f"{attr}: cannot resolve the navigation property from metadata.")
    return nav, target_set
