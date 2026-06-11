"""Entity record CRUD via the D365 Web API.

Every public function returns a plain dict (or list of dicts) — callers are responsible
for formatting.
"""

from __future__ import annotations

import difflib
import re
from typing import Any

from crm.utils.d365_backend import D365Backend, D365Error, as_dict
from crm.utils.d365_types import BatchOperation


_GUID_RE = re.compile(
    r"^[{(]?[0-9a-fA-F]{8}-[0-9a-fA-F]{4}-[0-9a-fA-F]{4}-[0-9a-fA-F]{4}-[0-9a-fA-F]{12}[)}]?$"
)


def _normalize_id(record_id: str) -> str:
    """Strip braces and validate GUID format."""
    rid = record_id.strip().lstrip("{(").rstrip("})")
    if not _GUID_RE.match(rid):
        raise D365Error(f"Invalid record id (expected GUID): {record_id!r}")
    return rid


def build_record_path(entity_set: str, record_id: str) -> str:
    """Build an OData record path ``<entity_set>(<guid>)`` from a GUID.

    ``record_id`` is normalized (braces stripped) and validated as a GUID;
    raises ``D365Error`` if it is not one.
    """
    return f"{entity_set}({_normalize_id(record_id)})"


# ── Read ────────────────────────────────────────────────────────────────


def retrieve(
    backend: D365Backend,
    entity_set: str,
    record_id: str,
    *,
    select: list[str] | None = None,
    expand: list[str] | None = None,
    include_annotations: bool = False,
) -> dict[str, Any]:
    """GET a single record by GUID."""
    params: dict[str, Any] = {}
    if select:
        params["$select"] = ",".join(select)
    if expand:
        params["$expand"] = ",".join(expand)
    headers = {"Prefer": 'odata.include-annotations="*"'} if include_annotations else None
    result = backend.get(
        build_record_path(entity_set, record_id),
        params=params or None,
        extra_headers=headers,
    )
    return as_dict(result)


# ── Validate ────────────────────────────────────────────────────────────


_NUMBER_WORDS = {
    "zero": "0", "one": "1", "two": "2", "three": "3", "four": "4",
    "five": "5", "six": "6", "seven": "7", "eight": "8", "nine": "9",
}


def _suggest_field(field: str, valid_list: list[str]) -> str | None:
    """Best single `did_you_mean` suggestion for an unknown field, or ``None``.

    Pure and deterministic. Prefers, in order:
      1. a trailing spelled-out number word mapped to its digit
         (`telephoneone` → `telephone1`) that lands an exact valid member;
      2. the closest fuzzy match — but ties (common in numbered families, where
         `telephone1/2/3` all score equally) break to the lexicographically
         *smallest* candidate, i.e. the lowest-numbered, most-common member.
         `difflib.get_close_matches` breaks ties the other way (largest), which
         is the #198 bug, so the ranking is done here.
    """
    for word, digit in _NUMBER_WORDS.items():
        if field.endswith(word):
            normalized = field[: -len(word)] + digit
            if normalized in valid_list:
                return normalized
            break
    best: tuple[float, str] | None = None
    for cand in valid_list:
        ratio = difflib.SequenceMatcher(None, field, cand).ratio()
        if ratio < 0.6:
            continue
        if best is None or ratio > best[0] or (ratio == best[0] and cand < best[1]):
            best = (ratio, cand)
    return best[1] if best else None


def validate_payload(
    backend: D365Backend,
    entity_set: str,
    payload: dict[str, Any],
    *,
    is_create: bool = False,
) -> dict[str, Any]:
    """Field-NAME pre-write validation for a create/update payload (#72, #233).

    One to three pure GETs build the set of valid payload keys:
      1. resolve the entity-SET name to its LOGICAL name (also fetches
         `PrimaryIdAttribute` for the create-path warning — no extra round-trip);
      2. the entity's logical attribute names;
      3. the ManyToOne navigation-property names
         (`ReferencingEntityNavigationPropertyName`) — these are the `<nav>` in a
         `<nav>@odata.bind` deep-link, so a bound lookup is NOT a bogus field.
         GET #3 is skipped when the payload contains no `@odata.bind` keys.

    Valid keys are the UNION of (2) and (3). Each payload key is stripped of its
    `@odata.bind` / `@odata.type` suffix before the membership check; control
    annotations that strip to empty (e.g. a bare `@odata.type`) are ignored.

    Returns `{"ok": True}` when every field is known, else
    `{"ok": False, "meta": {"unknown_fields": [...], "did_you_mean": {...}}}`.
    `did_you_mean` maps an unknown field to its closest valid key, when one is
    close enough. Scope is FIELD-NAME only: option-set VALUES are not checked.

    When `is_create=True` and the payload contains the entity's primary id
    attribute, `ok` is still True but a warning is added:
    `{"ok": True, "meta": {"warnings": ["payload contains primary id '...' — ..."]}}`

    The probe is always real GETs even when the backend is in dry-run mode (it
    never mutates) so `--validate --dry-run` composes — mirrors `target_exists`.
    """
    # Navigation-property names only matter for `<nav>@odata.bind` deep-links, so
    # the ManyToOne GET is skipped for any payload without one (a plain-attribute
    # body, or one carrying only control annotations like `@odata.etag`) — keeping
    # the documented cost at 1-3 GETs rather than a flat 3.
    needs_nav = any(key.endswith("@odata.bind") for key in payload)

    # Double single quotes per OData literal escaping so an entity set with an
    # apostrophe cannot break (or alter) the $filter expression.
    safe_set = entity_set.replace("'", "''")
    sets = as_dict(backend.get(
        "EntityDefinitions",
        params={
            "$select": "LogicalName,EntitySetName,PrimaryIdAttribute",
            "$filter": f"EntitySetName eq '{safe_set}'",
        },
    ))
    matches: list[dict[str, Any]] = sets.get("value", [])
    if not matches:
        raise D365Error(f"Unknown entity set: {entity_set!r}")
    logical_name = matches[0].get("LogicalName")
    if not logical_name:
        raise D365Error(f"Unknown entity set: {entity_set!r}")
    primary_id_attr: str | None = matches[0].get("PrimaryIdAttribute") or None

    attrs = as_dict(backend.get(
        f"EntityDefinitions(LogicalName='{logical_name}')/Attributes",
        params={"$select": "LogicalName"},
    ))
    nav_rows: list[dict[str, Any]] = []
    if needs_nav:
        m2o = as_dict(backend.get(
            f"EntityDefinitions(LogicalName='{logical_name}')/ManyToOneRelationships",
            params={"$select": "ReferencingEntityNavigationPropertyName"},
        ))
        nav_rows = m2o.get("value", [])

    valid: set[str] = {
        a["LogicalName"] for a in attrs.get("value", []) if a.get("LogicalName")
    }
    valid |= {
        r["ReferencingEntityNavigationPropertyName"]
        for r in nav_rows
        if r.get("ReferencingEntityNavigationPropertyName")
    }
    # Sorted so did_you_mean is deterministic; a set's iteration order is not
    # stable across runs/builds. `_suggest_field` ranks ties itself.
    valid_list = sorted(valid)

    unknown: list[str] = []
    did_you_mean: dict[str, str] = {}
    for key in payload:
        field = key.split("@", 1)[0]
        # `field in unknown` de-dupes when a base key and its annotated form (e.g.
        # `foo` and `foo@odata.bind`) both strip to the same unknown name.
        if not field or field in valid or field in unknown:
            continue
        unknown.append(field)
        suggestion = _suggest_field(field, valid_list)
        if suggestion:
            did_you_mean[field] = suggestion

    if unknown:
        return {
            "ok": False,
            "meta": {"unknown_fields": unknown, "did_you_mean": did_you_mean},
        }

    warnings: list[str] = []
    if is_create and primary_id_attr:
        if any(key.split("@", 1)[0] == primary_id_attr for key in payload):
            warnings.append(
                f"payload contains primary id {primary_id_attr!r} — "
                "remove it unless you intend to create with an explicit GUID"
            )

    if warnings:
        return {"ok": True, "meta": {"warnings": warnings}}
    return {"ok": True}


# ── Create ──────────────────────────────────────────────────────────────


def create(
    backend: D365Backend,
    entity_set: str,
    payload: dict[str, Any],
    *,
    return_record: bool = True,
    caller_id: str | None = None,
    caller_object_id: str | None = None,
    suppress_duplicate_detection: bool | None = None,
    bypass_custom_plugin_execution: bool | None = None,
) -> dict[str, Any]:
    """POST a new record.

    With return_record=True we add `Prefer: return=representation` to get the created
    record back in the response. Otherwise we extract the GUID from the
    `OData-EntityId` header and return `{ "id": "<guid>" }`.
    """
    headers: dict[str, str] = {}
    if return_record:
        headers["Prefer"] = "return=representation"

    result = backend.post(
        entity_set,
        json_body=payload,
        extra_headers=headers or None,
        caller_id=caller_id,
        caller_object_id=caller_object_id,
        suppress_duplicate_detection=suppress_duplicate_detection,
        bypass_custom_plugin_execution=bypass_custom_plugin_execution,
    )
    result_dict = as_dict(result)
    if not result_dict:
        return {}

    if "_dry_run" in result_dict:
        return result_dict

    if return_record:
        return result_dict

    # 204 path: response carried OData-EntityId we surfaced through _entity_id_url
    entity_id_url = result_dict.get("_entity_id_url")
    if entity_id_url:
        m = re.search(r"\(([0-9a-fA-F-]{36})\)", entity_id_url)
        if m:
            return {"id": m.group(1), "entity_id_url": entity_id_url}
    return result_dict


# ── Update ──────────────────────────────────────────────────────────────


def update(
    backend: D365Backend,
    entity_set: str,
    record_id: str,
    payload: dict[str, Any],
    *,
    prevent_create: bool = True,
    return_record: bool = False,
    if_match: str | None = None,
    caller_id: str | None = None,
    caller_object_id: str | None = None,
    suppress_duplicate_detection: bool | None = None,
    bypass_custom_plugin_execution: bool | None = None,
) -> dict[str, Any]:
    """PATCH an existing record. By default prevents accidental upsert via If-Match: *."""
    headers: dict[str, str] = {}
    if return_record:
        headers["Prefer"] = "return=representation"

    effective_etag: str | None = if_match
    if effective_etag is None and prevent_create:
        effective_etag = "*"

    result = backend.patch(
        build_record_path(entity_set, record_id),
        json_body=payload,
        extra_headers=headers or None,
        etag=effective_etag,
        caller_id=caller_id,
        caller_object_id=caller_object_id,
        suppress_duplicate_detection=suppress_duplicate_detection,
        bypass_custom_plugin_execution=bypass_custom_plugin_execution,
    )
    return as_dict(result)


# ── Upsert ──────────────────────────────────────────────────────────────


def upsert(
    backend: D365Backend,
    entity_set: str,
    record_id: str,
    payload: dict[str, Any],
    *,
    caller_id: str | None = None,
    caller_object_id: str | None = None,
    suppress_duplicate_detection: bool | None = None,
    bypass_custom_plugin_execution: bool | None = None,
) -> dict[str, Any]:
    """PATCH that creates if missing (no If-Match header)."""
    result = backend.patch(
        build_record_path(entity_set, record_id),
        json_body=payload,
        caller_id=caller_id,
        caller_object_id=caller_object_id,
        suppress_duplicate_detection=suppress_duplicate_detection,
        bypass_custom_plugin_execution=bypass_custom_plugin_execution,
    )
    return as_dict(result)


# ── Delete ──────────────────────────────────────────────────────────────


def delete(
    backend: D365Backend,
    entity_set: str,
    record_id: str,
    *,
    if_match: str | None = None,
    caller_id: str | None = None,
    caller_object_id: str | None = None,
    suppress_duplicate_detection: bool | None = None,
    bypass_custom_plugin_execution: bool | None = None,
) -> dict[str, Any]:
    """DELETE a record."""
    result = backend.delete(
        build_record_path(entity_set, record_id),
        etag=if_match,
        caller_id=caller_id,
        caller_object_id=caller_object_id,
        suppress_duplicate_detection=suppress_duplicate_detection,
        bypass_custom_plugin_execution=bypass_custom_plugin_execution,
    )
    return result if isinstance(result, dict) else {"deleted": True, "id": _normalize_id(record_id)}


# ── Associate / Disassociate ────────────────────────────────────────────


def associate(
    backend: D365Backend,
    target_set: str,
    target_id: str,
    navigation_property: str,
    related_set: str,
    related_id: str,
    *,
    caller_id: str | None = None,
    caller_object_id: str | None = None,
    suppress_duplicate_detection: bool | None = None,
    bypass_custom_plugin_execution: bool | None = None,
) -> dict[str, Any]:
    """POST to a collection-valued navigation property to associate two records.

    Use for 1:N (from the "one" side) and N:N relationships. For setting a
    single-valued lookup (N:1), use `update()` with `@odata.bind` instead.

    Reference: https://learn.microsoft.com/power-apps/developer/data-platform/webapi/associate-disassociate-entities-using-web-api
    """
    target_path = build_record_path(target_set, target_id)
    related_url = backend.url_for(build_record_path(related_set, related_id))
    path = f"{target_path}/{navigation_property}/$ref"
    result = as_dict(backend.post(
        path,
        json_body={"@odata.id": related_url},
        caller_id=caller_id,
        caller_object_id=caller_object_id,
        suppress_duplicate_detection=suppress_duplicate_detection,
        bypass_custom_plugin_execution=bypass_custom_plugin_execution,
    ))
    return result if result else {"associated": True, "target": target_id, "related": related_id}


def disassociate(
    backend: D365Backend,
    target_set: str,
    target_id: str,
    navigation_property: str,
    *,
    related_set: str | None = None,
    related_id: str | None = None,
    caller_id: str | None = None,
    caller_object_id: str | None = None,
    suppress_duplicate_detection: bool | None = None,
    bypass_custom_plugin_execution: bool | None = None,
) -> dict[str, Any]:
    """DELETE a relationship.

    For collection-valued nav properties (1:N from the one side, or N:N) the related
    set + id MUST be supplied — the URL is /<nav>/$ref?$id=<related url>.

    For single-valued nav properties (N:1 lookup), omit related_set/related_id; the
    URL becomes /<nav>/$ref and removes the reference.
    """
    target_path = build_record_path(target_set, target_id)
    if related_set and related_id:
        related_url = backend.url_for(build_record_path(related_set, related_id))
        from urllib.parse import quote
        path = f"{target_path}/{navigation_property}/$ref?$id={quote(related_url, safe='')}"
    else:
        path = f"{target_path}/{navigation_property}/$ref"
    backend.delete(
        path,
        caller_id=caller_id,
        caller_object_id=caller_object_id,
        suppress_duplicate_detection=suppress_duplicate_detection,
        bypass_custom_plugin_execution=bypass_custom_plugin_execution,
    )
    return {"disassociated": True, "target": target_id, "related": related_id}


def set_lookup(
    backend: D365Backend,
    entity_set: str,
    record_id: str,
    navigation_property: str,
    related_set: str,
    related_id: str,
    *,
    caller_id: str | None = None,
    caller_object_id: str | None = None,
    suppress_duplicate_detection: bool | None = None,
    bypass_custom_plugin_execution: bool | None = None,
) -> dict[str, Any]:
    """Set or change a single-valued lookup by `@odata.bind` PATCH.

    Equivalent to: PATCH /<set>(<id>)  { "<nav>@odata.bind": "/<related_set>(<related_id>)" }
    """
    bind_value = f"/{related_set}({_normalize_id(related_id)})"
    payload = {f"{navigation_property}@odata.bind": bind_value}
    return update(
        backend, entity_set, record_id, payload,
        prevent_create=True,
        caller_id=caller_id,
        caller_object_id=caller_object_id,
        suppress_duplicate_detection=suppress_duplicate_detection,
        bypass_custom_plugin_execution=bypass_custom_plugin_execution,
    )


def clear_lookup(
    backend: D365Backend,
    entity_set: str,
    record_id: str,
    navigation_property: str,
    *,
    caller_id: str | None = None,
    caller_object_id: str | None = None,
    suppress_duplicate_detection: bool | None = None,
    bypass_custom_plugin_execution: bool | None = None,
) -> dict[str, Any]:
    """Clear a single-valued lookup via DELETE /<set>(<id>)/<nav>/$ref."""
    target_path = build_record_path(entity_set, record_id)
    backend.delete(
        f"{target_path}/{navigation_property}/$ref",
        caller_id=caller_id,
        caller_object_id=caller_object_id,
        suppress_duplicate_detection=suppress_duplicate_detection,
        bypass_custom_plugin_execution=bypass_custom_plugin_execution,
    )
    return {"cleared": True, "id": _normalize_id(record_id), "nav": navigation_property}


# ── Children (1:N related-record counts) ─────────────────────────────────


def count_children(
    backend: D365Backend,
    entity_set: str,
    record_id: str,
    *,
    non_empty: bool = False,
    filter_entities: str | None = None,
    batch_chunk_size: int = 100,
) -> list[dict[str, Any]]:
    """Count related records per 1:N relationship where this entity is the parent.

    Returns one row per relationship — ``{"entity", "attribute", "set", "count"}`` —
    where ``entity`` is the child logical name, ``attribute`` its referencing
    lookup, and ``set`` the child entity set. Round trips are
    O(relationships / ``batch_chunk_size``): one metadata GET for the logical↔set
    map, one for the relationships, then ``$batch`` chunks of ``$count`` queries.

    ``filter_entities`` (regex) drops non-matching child entities *before* the
    counts are issued — fewer requests, not a post-filter. ``non_empty`` drops
    rows whose count is 0.

    Read-only: under ``--dry-run`` the counts run as direct GETs (the ``$batch``
    POST would otherwise be short-circuited to a useless preview), so a dry-run
    still reports real counts — sequential, but ``--dry-run`` is a preview path.
    """
    from crm.core import metadata as metadata_mod

    guid = _normalize_id(record_id)
    if batch_chunk_size < 1:
        raise D365Error("batch_chunk_size must be a positive integer.")
    # Compile the user regex before any round trip (validate-before-backend); an
    # invalid pattern is a clean D365Error, not an uncaught re.error traceback.
    try:
        pattern = re.compile(filter_entities) if filter_entities else None
    except re.error as exc:
        raise D365Error(f"--filter-entities is not a valid regular expression: {exc}")

    # Logical↔set map in one GET — resolves both the parent set→logical and each
    # child logical→set (the sole entity-set-name resolution round trip).
    defs = metadata_mod.list_entity_definitions(backend)
    set_to_logical = {d["set_name"]: d["logical"] for d in defs if d["set_name"]}
    logical_to_set = {d["logical"]: d["set_name"] for d in defs if d["set_name"]}
    parent_logical = set_to_logical.get(entity_set)
    if not parent_logical:
        raise D365Error(
            f"Could not resolve entity set {entity_set!r} to a logical name.",
            code="UnknownEntitySet",
        )

    # 1:N relationships where the parent is the referenced side (one GET).
    rels_raw: list[dict[str, Any]] = as_dict(backend.get(
        f"EntityDefinitions(LogicalName='{parent_logical}')/OneToManyRelationships",
        params={"$select": "ReferencingEntity,ReferencingAttribute"},
    )).get("value", [])

    rels: list[dict[str, str]] = []
    for r in rels_raw:
        child = str(r.get("ReferencingEntity") or "")
        attr = str(r.get("ReferencingAttribute") or "")
        child_set = logical_to_set.get(child, "")
        if not child or not attr or not child_set:
            continue
        if pattern is not None and not pattern.search(child):
            continue
        rels.append({"entity": child, "attribute": attr, "set": child_set})

    rows: list[dict[str, Any]] = []
    if backend.dry_run:
        # $batch is a POST and is short-circuited under --dry-run; a read-only count
        # must not be stubbed into uselessness (issue #234 / migration-assess
        # precedent), so issue the side-effect-free GETs directly. Sequential, but
        # --dry-run is a preview path where round-trip count is not the concern.
        for rel in rels:
            rows.append(_count_via_get(backend, rel, guid))
        if non_empty:
            rows = [row for row in rows if row["count"] != 0]
        return rows

    for start in range(0, len(rels), batch_chunk_size):
        chunk = rels[start:start + batch_chunk_size]
        ops: list[BatchOperation] = [
            {"method": "GET", "url": _count_url(rel["set"], rel["attribute"], guid)}
            for rel in chunk
        ]
        # Non-transactional + continue-on-error: an account's 1:N set can include
        # child entities that reject RetrieveMultiple (activity-feed system types
        # like postregarding). A transactional batch would abort the whole audit on
        # the first such row; here each count is independent and an uncountable one
        # is reported as count=null + error rather than dropped or fatal.
        results = backend.batch(ops, transactional=False, continue_on_error=True)
        for rel, res in zip(chunk, results):
            err = res.get("error")
            if err:
                rows.append({**rel, "count": None, "error": str(err)})
            else:
                rows.append({**rel, "count": _odata_count(res.get("body"))})

    if non_empty:
        rows = [row for row in rows if row["count"] != 0]
    return rows


def _count_url(child_set: str, attribute: str, guid: str) -> str:
    """Relative URL counting child rows that reference `guid` via `attribute`.

    `?$count=true&$top=1` not `/$count?$filter=`: on-prem v9.1 binds a $filter on
    the `/$count` path segment to the Edm.Int32 result and 400s ("no property
    '_x_value' on type 'Edm.Int32'"). $count=true returns the full @odata.count
    regardless of $top (live-verified both targets); $top=1 caps the row count.

    `$select=_<attr>_value` keeps each returned row to the one lookup column the
    filter already names (only @odata.count is consumed) instead of every column,
    shrinking each $batch sub-response. The `_<attr>_value` form is required —
    on-prem rejects $select on the bare lookup name (`parentid` → 404 property).
    """
    from urllib.parse import quote

    value_attr = f"_{attribute}_value"
    return (
        f"{child_set}?$filter="
        + quote(f"{value_attr} eq {guid}", safe="")
        + f"&$count=true&$top=1&$select={value_attr}"
    )


def _count_via_get(backend: D365Backend, rel: dict[str, str], guid: str) -> dict[str, Any]:
    """Count one relationship via a direct GET (the --dry-run / non-batched path).

    Mirrors the batch path's degradation: a child entity that rejects the read
    (e.g. RetrieveMultiple-unsupported system types) becomes count=null + error.
    """
    try:
        body = as_dict(backend.get(_count_url(rel["set"], rel["attribute"], guid)))
        return {**rel, "count": _odata_count(body)}
    except D365Error as exc:
        return {**rel, "count": None, "error": str(exc)}


def _odata_count(body: "dict[str, Any] | str | None") -> int:
    """Read `@odata.count` from a successful `$count=true` response body."""
    if isinstance(body, dict):
        n = body.get("@odata.count")
        if n is not None:
            return int(n)
    raise D365Error(f"Unexpected count response: {body!r}")


# ── Record clone (#255) ───────────────────────────────────────────────────

# Attributes a record clone never copies even when metadata marks them writable.
# Uniqueidentifier-typed columns are dropped generically by type (covers the
# primary id AND address1_addressid-class child ids — no per-entity lists); the
# rest are dropped by name. All are re-addable via `overrides`.
_NEVER_COPY_NAMES = frozenset(
    {"statecode", "statuscode", "ownerid", "overriddencreatedon"}
)
# Attribute types whose value is carried as a `_<name>_value` lookup property
# and must be rebound with `<nav>@odata.bind`, not copied verbatim. `ownerid`
# itself is in the never-copy set, but `Owner` stays here so any other
# owner-typed column is rebound rather than silently dropped as a plain field.
_LOOKUP_TYPES = frozenset({"Lookup", "Customer", "Owner"})

# Per-value lookup annotations (present on an annotated retrieve). The first is
# the exact case-sensitive single-valued navigation property for the value
# currently set — authoritative for both single-target and polymorphic lookups
# (avoids guessing nav-prop casing, the #228 hazard); the second is the target
# table's logical name, which selects its entity set for the bind URL.
_ASSOC_NAV_ANNOTATION = "Microsoft.Dynamics.CRM.associatednavigationproperty"
_LOOKUP_LOGICAL_ANNOTATION = "Microsoft.Dynamics.CRM.lookuplogicalname"


def clone_record(
    backend: D365Backend,
    entity_set: str,
    record_id: str,
    *,
    overrides: dict[str, Any] | None = None,
    unset: list[str] | None = None,
    return_record: bool = True,
) -> dict[str, Any]:
    """Clone a single record over the Web API (#255).

    Start from the source's `IsValidForCreate` attributes, drop the never-copy
    set, convert each set lookup to a `<nav>@odata.bind` (nav + target taken
    from the record's own annotations), then apply `unset`/`overrides`. All
    resolution runs as a clone pre-flight before the single create write; on a
    pre-flight failure the org is untouched. Returns the created record (or just
    its id with `return_record=False`); under `--dry-run`, returns
    `{_dry_run, would_create: {entity_set, body}}` with the resolved payload.
    """
    from crm.core import metadata as metadata_mod

    overrides = overrides or {}
    unset = unset or []

    defs = metadata_mod.list_entity_definitions(backend)
    set_to_logical = {d["set_name"]: d["logical"] for d in defs if d["set_name"]}
    logical_to_set = {d["logical"]: d["set_name"] for d in defs if d["set_name"]}
    logical_name = set_to_logical.get(entity_set)
    if not logical_name:
        raise D365Error(f"Unknown entity set: {entity_set!r}")

    attrs = as_dict(backend.get(
        f"EntityDefinitions(LogicalName='{logical_name}')/Attributes",
        params={"$select": "LogicalName,AttributeType,IsValidForCreate"},
    )).get("value", [])
    # logical_name -> AttributeType for every attribute valid for create and not
    # in the never-copy set; this is the canonical column set the clone copies.
    # `all_attr_names` is every attribute (any validity) so `unset` can be
    # validated against the real schema, not just the copied subset.
    create_attrs: dict[str, str] = {}
    all_attr_names: set[str] = set()
    for a in attrs:
        name = a.get("LogicalName")
        if not name:
            continue
        all_attr_names.add(name)
        if not a.get("IsValidForCreate"):
            continue
        attr_type = a.get("AttributeType") or ""
        if attr_type == "Uniqueidentifier" or name in _NEVER_COPY_NAMES:
            continue
        create_attrs[name] = attr_type

    source = retrieve(backend, entity_set, record_id, include_annotations=True)

    body: dict[str, Any] = {}
    # logical lookup name -> the `<nav>@odata.bind` key it produced, so an
    # `unset` of the lookup's logical name finds and drops the right key.
    lookup_bind_keys: dict[str, str] = {}
    # Clone pre-flight failures, batched so the caller sees every offending
    # field at once; raised before any write, so the org is untouched.
    errors: list[str] = []
    for name, attr_type in create_attrs.items():
        if attr_type in _LOOKUP_TYPES:
            value_key = f"_{name}_value"
            guid = source.get(value_key)
            if not guid:
                continue
            nav = source.get(f"{value_key}@{_ASSOC_NAV_ANNOTATION}")
            target_logical = source.get(f"{value_key}@{_LOOKUP_LOGICAL_ANNOTATION}")
            target_set = logical_to_set.get(target_logical or "")
            if not nav or not target_set:
                errors.append(
                    f"{name}: cannot resolve the lookup target (navigation "
                    "property + entity set) from the source record — drop it "
                    f"with --unset {name}, or re-add it with the lookup's "
                    "case-sensitive navigation property as --override "
                    "'<nav>@odata.bind=/<set>(<id>)' (get <nav> from "
                    f"`crm metadata describe {logical_name}`)"
                )
                continue
            bind_key = f"{nav}@odata.bind"
            body[bind_key] = f"/{target_set}({guid})"
            lookup_bind_keys[name] = bind_key
        else:
            value = source.get(name)
            if value is not None:
                body[name] = value

    # Drop unset fields by logical name (a lookup's logical name removes the
    # `<nav>@odata.bind` key it produced). Unsetting a field the schema does not
    # have is a pre-flight failure, not a silent no-op, so a typo surfaces.
    for field in unset:
        if field not in all_attr_names:
            errors.append(
                f"{field}: --unset names a field that is not an attribute of "
                f"{entity_set}"
            )
            continue
        body.pop(field, None)
        bind_key = lookup_bind_keys.get(field)
        if bind_key:
            body.pop(bind_key, None)

    # Overrides apply last and pass raw: the key is used verbatim (so an
    # `@odata.bind` key re-adds a never-copy lookup) and an override wins over a
    # cloned value. Override keys are deliberately not field-name validated.
    for key, value in overrides.items():
        body[key] = value

    if errors:
        raise D365Error(
            "Clone pre-flight failed for "
            f"{entity_set}({_normalize_id(record_id)}):\n  - "
            + "\n  - ".join(errors)
        )

    if backend.dry_run:
        # Pre-flight has already run against a live org (the reads execute under
        # dry-run); surface the fully resolved create body instead of letting
        # the backend's generic POST short-circuit return an opaque echo.
        return {"_dry_run": True,
                "would_create": {"entity_set": entity_set, "body": body}}

    return create(backend, entity_set, body, return_record=return_record)
