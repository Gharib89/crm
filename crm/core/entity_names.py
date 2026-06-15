"""Single seam for entity-name resolution and writable-attribute reduction (#261).

This module is the one place ``crm.core`` resolves logical ⇄ entity-set names and
the one home for the ``IsValidForCreate`` / ``IsValidForUpdate`` walk. It replaces
three idioms that used to be scattered across core: the per-logical
``resolve_entity_set_name`` GET, the inline ``set_to_logical`` / ``logical_to_set``
rebuilds, and the duplicated writable-attribute reduction.

Design:

- **Read-through cache.** Name resolution loads the bidirectional map through
  ``metadata_cache.load_definitions``, so a warm cache (within its TTL) is served
  without a live GET. Cache *invalidation* stays at the existing write sites — this
  module only consolidates the read side.
- **Builds on #263.** The live fetch uses ``D365Backend.get_collection`` (the
  deepened OData response surface), so a paged ``EntityDefinitions`` collection is
  followed to exhaustion rather than truncated at the first page.
- **Core-pure (ADR-0002).** Takes the backend (hence its ``ConnectionProfile``) as
  a parameter the way the cache already does; imports no session/config.
"""

from __future__ import annotations

import difflib
import time
from dataclasses import dataclass, field
from typing import TYPE_CHECKING, Any

from crm.core import metadata_cache
from crm.utils.d365_backend import D365Error, odata_literal

if TYPE_CHECKING:
    from crm.utils.d365_backend import D365Backend


def _empty_str_map() -> dict[str, str]:
    """Typed default factory for the NameMap primary-attribute maps (keeps
    pyright strict from widening the field to ``dict[Unknown, Unknown]``)."""
    return {}


@dataclass(frozen=True)
class NameMap:
    """Bidirectional logical ⇄ entity-set name map for one org.

    Both lookups return ``None`` for an unknown name so callers raise their own
    domain-specific error (the messages differ: "unknown logical name" vs
    "unknown entity set").
    """

    logical_to_set: dict[str, str]
    set_to_logical: dict[str, str]
    # Keyed by logical name. Populated from PrimaryIdAttribute/PrimaryNameAttribute
    # for the normalized `_entity_id` (create) and the human primary-name column
    # (ADR 0008 / #304). Default-empty so a hand-built NameMap stays valid.
    primary_id: dict[str, str] = field(default_factory=_empty_str_map)
    primary_name: dict[str, str] = field(default_factory=_empty_str_map)

    def set_for(self, logical_name: str) -> str | None:
        """Entity-set name for *logical_name*, or ``None`` if unknown."""
        return self.logical_to_set.get(logical_name) or None

    def logical_for(self, entity_set: str) -> str | None:
        """Logical name for *entity_set*, or ``None`` if unknown."""
        return self.set_to_logical.get(entity_set) or None

    def primary_id_for(self, name: str) -> str | None:
        """PrimaryIdAttribute for a logical OR entity-set *name*, or ``None``."""
        logical = self.set_to_logical.get(name, name)
        return self.primary_id.get(logical) or None

    def primary_name_for(self, name: str) -> str | None:
        """PrimaryNameAttribute for a logical OR entity-set *name*, or ``None``."""
        logical = self.set_to_logical.get(name, name)
        return self.primary_name.get(logical) or None

    def resolve(self, name: str) -> str:
        """Resolve a user-supplied entity *name* to its canonical logical name.

        Accepts either a logical name (``account``) or an entity-set name
        (``accounts``), matched case-insensitively, and returns the logical
        name. Raises :class:`D365Error` on a genuine miss, appending a
        ``difflib`` close-match suggestion only when one actually exists (so the
        message never fabricates a false correction).
        """
        if not name:
            raise D365Error("entity name is required")
        # Fast paths: exact logical, then exact entity-set.
        if name in self.logical_to_set:
            return name
        logical = self.set_to_logical.get(name)
        if logical:
            return logical
        # Case-insensitive fallback over logical names, then entity-set names.
        lowered = name.lower()
        for logical_name in self.logical_to_set:
            if logical_name.lower() == lowered:
                return logical_name
        for set_name, logical_name in self.set_to_logical.items():
            if set_name.lower() == lowered:
                return logical_name
        # Genuine miss: suggest a close logical name only when one is found.
        match = difflib.get_close_matches(
            lowered, list(self.logical_to_set), n=1, cutoff=0.6
        )
        hint = f" — did you mean {match[0]!r}?" if match else ""
        raise D365Error(
            f"Entity {name!r} was not found in the CRM system{hint}",
            code="UnknownEntity",
        )


@dataclass(frozen=True)
class AttrSpec:
    """One normalised attribute-metadata row.

    Carries the create/update validity as booleans so callers reason about
    write-readiness without re-reading the raw ``IsValidForCreate`` /
    ``IsValidForUpdate`` keys themselves. ``required_level`` is the raw
    ``RequiredLevel.Value`` string (``None`` when absent).
    """

    logical_name: str
    attribute_type: str
    required_level: str | None
    valid_for_create: bool
    valid_for_update: bool


# Superset $select shared by both consumers (describe + clone planner) so the
# attribute fetch is identical and cacheable-looking regardless of caller.
_ATTR_SELECT = "LogicalName,AttributeType,RequiredLevel,IsValidForCreate,IsValidForUpdate"


def specs_from_rows(rows: list[dict[str, Any]]) -> list[AttrSpec]:
    """Normalise raw attribute-metadata rows into :class:`AttrSpec`.

    This is the one home for the ``IsValidForCreate`` / ``IsValidForUpdate`` walk.
    Use it directly when you already hold attribute rows (e.g. fetched via an
    ``$expand=Attributes`` on the entity definition); :func:`attribute_specs`
    wraps it with the fetch. Rows missing a logical name are dropped.
    """
    specs: list[AttrSpec] = []
    for a in rows:
        name = a.get("LogicalName")
        if not name:
            continue
        required: dict[str, Any] = a.get("RequiredLevel") or {}
        level = required.get("Value")
        specs.append(AttrSpec(
            logical_name=name,
            attribute_type=a.get("AttributeType") or "",
            required_level=level if isinstance(level, str) else None,
            valid_for_create=bool(a.get("IsValidForCreate")),
            valid_for_update=bool(a.get("IsValidForUpdate")),
        ))
    return specs


def attribute_specs(backend: D365Backend, logical_name: str) -> list[AttrSpec]:
    """Return normalised :class:`AttrSpec` rows for *logical_name*'s attributes.

    Fetches the entity's attributes (one ``get_collection``) and normalises them
    via :func:`specs_from_rows` — the one home for the create/update-validity
    walk. Both ``metadata.describe_entity`` (writable = create-or-update) and the
    record clone planner (create-only, minus the never-copy set) consume these
    specs instead of re-reading the raw validity flags. Raises ``D365Error`` for
    an empty *logical_name*.
    """
    if not logical_name:
        raise D365Error("logical_name is required.")
    rows = backend.get_collection(
        f"EntityDefinitions(LogicalName={odata_literal(logical_name)})/Attributes",
        params={"$select": _ATTR_SELECT},
    )
    return specs_from_rows(rows)


def _fetch_definitions(backend: D365Backend) -> list[dict[str, str]]:
    """Live GET of ``[{logical, set_name}]`` for every entity (one collection read).

    Uses ``get_collection`` so a paged ``EntityDefinitions`` response is followed
    to exhaustion. The result shape matches what ``metadata_cache`` persists, so it
    is written through the cache unchanged.
    """
    rows = backend.get_collection(
        "EntityDefinitions",
        params={"$select":
                "LogicalName,EntitySetName,PrimaryIdAttribute,PrimaryNameAttribute"},
    )
    items: list[dict[str, str]] = []
    for e in rows:
        logical = e.get("LogicalName") or ""
        set_name = e.get("EntitySetName") or ""
        if logical:
            items.append({
                "logical": logical,
                "set_name": set_name,
                "primary_id": e.get("PrimaryIdAttribute") or "",
                "primary_name": e.get("PrimaryNameAttribute") or "",
            })
    return items


def load_name_map(backend: D365Backend, *, refresh: bool = False) -> NameMap:
    """Return the bidirectional name map, served read-through from the cache.

    ``refresh=False`` (default) serves a warm cache without a live GET and falls
    back to one ``EntityDefinitions`` collection GET on a miss. ``refresh=True``
    forces the live GET and repopulates the cache. Rows with no entity-set name
    (a handful of system entities are not OData-addressable) are dropped from both
    directions.
    """
    lookup = metadata_cache.load_definitions(
        backend.profile,
        fetch=lambda: _fetch_definitions(backend),
        refresh=refresh,
        now=time.time(),
    )
    logical_to_set: dict[str, str] = {}
    set_to_logical: dict[str, str] = {}
    primary_id: dict[str, str] = {}
    primary_name: dict[str, str] = {}
    for d in lookup.definitions:
        set_name = d["set_name"]
        if not set_name:
            continue
        logical = d["logical"]
        logical_to_set[logical] = set_name
        set_to_logical[set_name] = logical
        # `.get`: a legacy/hand-built row may predate the v2 cache shape.
        if d.get("primary_id"):
            primary_id[logical] = d["primary_id"]
        if d.get("primary_name"):
            primary_name[logical] = d["primary_name"]
    return NameMap(
        logical_to_set=logical_to_set,
        set_to_logical=set_to_logical,
        primary_id=primary_id,
        primary_name=primary_name,
    )


def cached_primary_name(backend: D365Backend, entity_set: str) -> str | None:
    """PrimaryNameAttribute for *entity_set* from the WARM cache only — no GET.

    Returns ``None`` on a cold/missing cache (or unknown entity). The human
    primary-name table column is best-effort and must never add a round-trip to a
    plain query (ADR 0008), so this reads the cache directly rather than going
    through the read-through :func:`load_name_map`."""
    cached = metadata_cache.read_definitions(backend.profile, now=time.time())
    if not cached:
        return None
    lowered = entity_set.lower()
    for d in cached:
        if (d.get("set_name", "").lower() == lowered
                or d.get("logical", "").lower() == lowered):
            return d.get("primary_name") or None
    return None


def resolve_logical_name(backend: D365Backend, name: str) -> str:
    """Resolve *name* (a logical or entity-set name, any case) to a logical name.

    Loads the cached bidirectional name map and delegates to
    :meth:`NameMap.resolve`. Lets a caller accept the entity-set name learned
    everywhere else in the CLI for an API that requires the logical name.
    """
    return load_name_map(backend).resolve(name)
