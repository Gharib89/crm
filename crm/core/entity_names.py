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

import time
from dataclasses import dataclass
from typing import TYPE_CHECKING, Any

from crm.core import metadata_cache
from crm.utils.d365_backend import D365Error, odata_literal

if TYPE_CHECKING:
    from crm.utils.d365_backend import D365Backend


@dataclass(frozen=True)
class NameMap:
    """Bidirectional logical ⇄ entity-set name map for one org.

    Both lookups return ``None`` for an unknown name so callers raise their own
    domain-specific error (the messages differ: "unknown logical name" vs
    "unknown entity set").
    """

    logical_to_set: dict[str, str]
    set_to_logical: dict[str, str]

    def set_for(self, logical_name: str) -> str | None:
        """Entity-set name for *logical_name*, or ``None`` if unknown."""
        return self.logical_to_set.get(logical_name) or None

    def logical_for(self, entity_set: str) -> str | None:
        """Logical name for *entity_set*, or ``None`` if unknown."""
        return self.set_to_logical.get(entity_set) or None


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
        params={"$select": "LogicalName,EntitySetName"},
    )
    items: list[dict[str, str]] = []
    for e in rows:
        logical = e.get("LogicalName") or ""
        set_name = e.get("EntitySetName") or ""
        if logical:
            items.append({"logical": logical, "set_name": set_name})
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
    for d in lookup.definitions:
        set_name = d["set_name"]
        if not set_name:
            continue
        logical_to_set[d["logical"]] = set_name
        set_to_logical[set_name] = d["logical"]
    return NameMap(logical_to_set=logical_to_set, set_to_logical=set_to_logical)
