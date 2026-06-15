"""Entity record CRUD via the D365 Web API.

Every public function returns a plain dict (or list of dicts) — callers are responsible
for formatting.
"""

from __future__ import annotations

import difflib
import re
from typing import Any

from crm.utils.d365_backend import (
    D365Backend,
    D365Error,
    as_dict,
    normalize_guid,
    odata_literal,
)
from crm.core import entity_names
from crm.utils.d365_types import BatchOperation


def _normalize_id(record_id: str) -> str:
    """Strip braces and validate GUID format."""
    rid = normalize_guid(record_id)
    if rid is None:
        raise D365Error(f"Invalid record id (expected GUID): {record_id!r}")
    return rid


def build_record_path(entity_set: str, record_id: str) -> str:
    """Build an OData record path ``<entity_set>(<guid>)`` from a GUID.

    ``record_id`` is normalized (braces stripped) and validated as a GUID;
    raises ``D365Error`` if it is not one.
    """
    return f"{entity_set}({_normalize_id(record_id)})"


def entity_id_fields(
    backend: D365Backend, entity_set: str, record_id: str
) -> dict[str, str]:
    """The normalized-id pair for *record_id* (ADR 0008 / #303).

    ``_entity_id`` is the record GUID and ``_entity_id_url`` its full Web API URL,
    matching the shape the backend already surfaces from the ``OData-EntityId``
    header on update/delete-by-header. Used by the write verbs and single-record
    get so chaining needs no per-entity primary-key knowledge. *record_id* is
    normalized + GUID-validated (raises ``D365Error`` otherwise)."""
    rid = _normalize_id(record_id)
    return {
        "_entity_id": rid,
        "_entity_id_url": backend.url_for(build_record_path(entity_set, rid)),
    }


def inject_create_entity_id(
    backend: D365Backend, entity_set: str, record: dict[str, Any]
) -> None:
    """Inject `_entity_id`/`_entity_id_url` into a create's returned record (#303).

    With `Prefer: return=representation` (the create default) Dataverse returns
    the new GUID only inside the body, under the entity's PrimaryIdAttribute key
    (not via the `OData-EntityId` header), and that attribute is not derivable
    from the entity-set name (activity entities break the `<logical>+id`
    convention). So resolve it through the read-through name map (warm cache = no
    GET, cold = one EntityDefinitions GET that also warms the cache). Best-effort:
    the record was already created, so a metadata miss must not fail the create —
    we simply skip the synthetic key. Mutates *record*. Called from the command
    layer, not `create`, so internal `create` callers are unaffected."""
    if "_dry_run" in record:
        return
    try:
        pk = entity_names.load_name_map(backend).primary_id_for(entity_set)
    except D365Error:
        return
    if not pk:
        return
    guid = record.get(pk)
    if isinstance(guid, str) and guid:
        record.update(entity_id_fields(backend, entity_set, guid))


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

    Pure reads build the set of valid payload keys:
      1. resolve the entity-SET name to its LOGICAL name via the shared
         name-resolution seam (#261) — served read-through from the metadata
         cache, so a warm cache costs no GET;
      2. the entity's logical attribute names;
      3. the ManyToOne navigation-property names
         (`ReferencingEntityNavigationPropertyName`) — these are the `<nav>` in a
         `<nav>@odata.bind` deep-link, so a bound lookup is NOT a bogus field.
         GET #3 is skipped when the payload contains no `@odata.bind` keys;
      4. on the create path only, `PrimaryIdAttribute` for the primary-id warning
         (a targeted describe GET).

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
    # body, or one carrying only control annotations like `@odata.etag`).
    needs_nav = any(key.endswith("@odata.bind") for key in payload)

    # Resolve the entity-set name to its logical name through the shared seam
    # (#261) — served read-through from the metadata cache, no per-call GET when
    # warm. Replaces the hand-rolled `EntitySetName eq` filter GET.
    logical_name = entity_names.load_name_map(backend).logical_for(entity_set)
    if not logical_name:
        raise D365Error(f"Unknown entity set: {entity_set!r}")

    # PrimaryIdAttribute is only consumed by the create-path warning below, so
    # fetch it lazily — and only then — with a targeted describe GET (not name
    # resolution). The non-create path pays nothing for it.
    primary_id_attr: str | None = None
    if is_create:
        ent = as_dict(backend.get(
            f"EntityDefinitions(LogicalName={odata_literal(logical_name)})",
            params={"$select": "PrimaryIdAttribute"},
        ))
        primary_id_attr = ent.get("PrimaryIdAttribute") or None

    attrs = as_dict(backend.get(
        f"EntityDefinitions(LogicalName={odata_literal(logical_name)})/Attributes",
        params={"$select": "LogicalName"},
    ))
    nav_rows: list[dict[str, Any]] = []
    if needs_nav:
        m2o = as_dict(backend.get(
            f"EntityDefinitions(LogicalName={odata_literal(logical_name)})/ManyToOneRelationships",
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
    `OData-EntityId` header and return `{ "_entity_id": "<guid>", "_entity_id_url": ... }`.
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

    # 204 path: response carried OData-EntityId — surface it under the normalized
    # id keys so --no-return agrees with the rest of the write verbs (ADR 0008).
    entity_id_url = result_dict.get("_entity_id_url")
    entity_id = result_dict.get("_entity_id")
    if entity_id_url and entity_id:
        return {"_entity_id": entity_id, "_entity_id_url": entity_id_url}
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
    # Dry-run returns the backend's preview dict; a real 204 carries no body, so
    # synthesize the success envelope with the normalized id key (ADR 0008 / #303).
    if isinstance(result, dict):
        return result
    return {"deleted": True, **entity_id_fields(backend, entity_set, record_id)}


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
    guid = _normalize_id(record_id)
    if batch_chunk_size < 1:
        raise D365Error("batch_chunk_size must be a positive integer.")
    # Compile the user regex before any round trip (validate-before-backend); an
    # invalid pattern is a clean D365Error, not an uncaught re.error traceback.
    try:
        pattern = re.compile(filter_entities) if filter_entities else None
    except re.error as exc:
        raise D365Error(f"--filter-entities is not a valid regular expression: {exc}")

    # Logical↔set map via the shared seam (#261): resolves the parent set→logical
    # and each child logical→set, served read-through from the metadata cache.
    name_map = entity_names.load_name_map(backend)
    parent_logical = name_map.logical_for(entity_set)
    if not parent_logical:
        raise D365Error(
            f"Could not resolve entity set {entity_set!r} to a logical name.",
            code="UnknownEntitySet",
        )

    # 1:N relationships where the parent is the referenced side (one GET).
    rels_raw: list[dict[str, Any]] = backend.get_collection(
        f"EntityDefinitions(LogicalName='{parent_logical}')/OneToManyRelationships",
        params={"$select": "ReferencingEntity,ReferencingAttribute"},
    )

    rels: list[dict[str, str]] = []
    for r in rels_raw:
        child = str(r.get("ReferencingEntity") or "")
        attr = str(r.get("ReferencingAttribute") or "")
        child_set = name_map.set_for(child) or ""
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


def _plan_from_specs(
    specs: list[entity_names.AttrSpec],
) -> tuple[dict[str, str], set[str]]:
    """Reduce normalised attribute specs to the clone's attribute plan.

    Consumes :class:`entity_names.AttrSpec` (the shared create/update-validity
    walk, #261) rather than re-reading the raw ``IsValidForCreate`` flag. Returns
    ``(create_attrs, all_attr_names)``: `create_attrs` maps logical name ->
    AttributeType for every attribute valid for create and not in the never-copy
    set (Uniqueidentifier dropped by type — covers primary + address child ids);
    `all_attr_names` is every attribute (any validity) so `unset` is validated
    against the full schema, not just the copied subset.
    """
    create_attrs: dict[str, str] = {}
    all_attr_names: set[str] = set()
    for s in specs:
        all_attr_names.add(s.logical_name)
        if not s.valid_for_create:
            continue
        if s.attribute_type == "Uniqueidentifier" or s.logical_name in _NEVER_COPY_NAMES:
            continue
        create_attrs[s.logical_name] = s.attribute_type
    return create_attrs, all_attr_names


def _build_clone_body(
    source: dict[str, Any],
    create_attrs: dict[str, str],
    all_attr_names: set[str],
    logical_to_set: dict[str, str],
    entity_logical: str,
    *,
    overrides: dict[str, Any],
    unset: list[str],
    repoint: tuple[str, str] | None = None,
) -> tuple[dict[str, Any], list[str]]:
    """Build a clone create body from a source record + its attribute plan.

    Converts each set lookup to a `<nav>@odata.bind` (nav + target from the
    record's own annotations), applies `unset` then `overrides`, and returns
    ``(body, errors)``. `errors` is batched so the caller sees every offending
    field at once; the caller decides whether to raise (parent pre-flight) or
    record a per-row failure (a child row).

    `repoint=(source_parent_guid, new_parent_id)` rebinds **any** lookup whose
    current value equals the source parent to the new parent instead (the
    child→new-parent rule) — the navigation property and target set are
    unchanged, only the bound id. `None` (the parent's own clone) binds every
    lookup to its existing target.
    """
    body: dict[str, Any] = {}
    # logical lookup name -> the `<nav>@odata.bind` key it produced, so an
    # `unset` of the lookup's logical name finds and drops the right key.
    lookup_bind_keys: dict[str, str] = {}
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
                    f"`crm metadata describe {entity_logical}`)"
                )
                continue
            bind_guid = guid
            if repoint is not None and str(guid).lower() == repoint[0].lower():
                bind_guid = repoint[1]
            bind_key = f"{nav}@odata.bind"
            body[bind_key] = f"/{target_set}({bind_guid})"
            lookup_bind_keys[name] = bind_key
        else:
            value = source.get(name)
            if value is not None:
                body[name] = value

    # Drop unset fields by logical name (a lookup's logical name removes the
    # `<nav>@odata.bind` key it produced). Unsetting a field the schema does not
    # have is a failure, not a silent no-op, so a typo surfaces.
    for field in unset:
        if field not in all_attr_names:
            errors.append(
                f"{field}: --unset names a field that is not an attribute of "
                f"{entity_logical}"
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

    return body, errors


def clone_record(
    backend: D365Backend,
    entity_set: str,
    record_id: str,
    *,
    overrides: dict[str, Any] | None = None,
    unset: list[str] | None = None,
    return_record: bool = True,
    with_children: bool = False,
    skip_child_entities: list[str] | None = None,
) -> dict[str, Any]:
    """Clone a single record over the Web API (#255), optionally its children (#256).

    Start from the source's `IsValidForCreate` attributes, drop the never-copy
    set, convert each set lookup to a `<nav>@odata.bind` (nav + target taken
    from the record's own annotations), then apply `unset`/`overrides`. All
    resolution runs as a clone pre-flight before the single create write; on a
    pre-flight failure the org is untouched. Returns the created record (or just
    its id with `return_record=False`); under `--dry-run`, returns
    `{_dry_run, would_create: {entity_set, body}}` with the resolved payload.

    With `with_children`, after the parent is created the verb also clones the
    direct child rows of every **custom** 1:N relationship where this entity is
    the parent (`skip_child_entities` prunes child entities by logical name).
    `overrides`/`unset` apply to the parent only. Each child row's lookups that
    point at the source parent are repointed to the new parent; other lookups
    copy as-is. A child create that fails does not roll back or abort — it is
    recorded and the rest continue (ADR 0007); the return is
    ``{created: {parent, children: {logical: [ids]}}, failures: [...]}``. Under
    `--dry-run`, the parent preview gains a `children` list of per-entity counts
    (with skipped entities marked), all from read-only GETs.
    """
    overrides = overrides or {}
    unset = unset or []
    skip = set(skip_child_entities or [])
    # Validate-before-backend: reject a bad record id before any metadata GET so
    # a typo costs no round-trip (mirrors count_children); reuse it from here on.
    record_id = _normalize_id(record_id)

    # Logical↔set map via the shared seam (#261): the parent set→logical here, and
    # each child logical→set is served from the same warm map below. Read-through
    # from the metadata cache, so a warm cache costs no live GET.
    name_map = entity_names.load_name_map(backend)
    logical_to_set = name_map.logical_to_set
    logical_name = name_map.logical_for(entity_set)
    if not logical_name:
        raise D365Error(f"Unknown entity set: {entity_set!r}")

    create_attrs, all_attr_names = _plan_from_specs(
        entity_names.attribute_specs(backend, logical_name)
    )

    source = retrieve(backend, entity_set, record_id, include_annotations=True)
    body, errors = _build_clone_body(
        source, create_attrs, all_attr_names, logical_to_set, logical_name,
        overrides=overrides, unset=unset,
    )
    if errors:
        raise D365Error(
            f"Clone pre-flight failed for {entity_set}({record_id}):\n  - "
            + "\n  - ".join(errors)
        )

    if backend.dry_run:
        # Pre-flight has already run against a live org (the reads execute under
        # dry-run); surface the fully resolved create body instead of letting
        # the backend's generic POST short-circuit return an opaque echo.
        preview: dict[str, Any] = {
            "_dry_run": True,
            "would_create": {"entity_set": entity_set, "body": body},
        }
        if with_children:
            preview["children"] = _preview_children(
                backend, logical_name, logical_to_set, record_id, skip
            )
        return preview

    if not with_children:
        return create(backend, entity_set, body, return_record=return_record)

    # Parent first. A parent create failure raises (clean operational failure,
    # no children attempted). Create id-only so the new parent id is always
    # available for repointing, regardless of the caller's return_record.
    parent_result = create(backend, entity_set, body, return_record=False)
    new_parent_id = str(parent_result.get("_entity_id") or "")
    if not new_parent_id:
        # Parent was created but its id was not in the response (no
        # OData-EntityId). Repointing children to /<set>() would fail every row;
        # fail fast and clear instead of emitting a confusing partial failure.
        raise D365Error(
            f"Cloned {entity_set} but could not read the new record's id from the "
            "create response (no OData-EntityId); children were not cloned. Find "
            "the new parent and clone its children separately."
        )
    children, failures = _clone_children(
        backend, logical_name, logical_to_set,
        source_parent_guid=record_id, new_parent_id=new_parent_id, skip=skip,
    )
    return {
        "created": {"parent": new_parent_id, "children": children},
        "failures": failures,
    }


def _custom_child_relationships(
    backend: D365Backend,
    parent_logical: str,
    logical_to_set: dict[str, str],
    skip: set[str],
) -> tuple[list[dict[str, str]], list[str]]:
    """Custom 1:N relationships where `parent_logical` is the parent.

    Returns ``(rels, skipped)`` where each rel is ``{entity, attribute, set}``.
    Only `IsCustomRelationship == true` relationships qualify (a custom lookup
    on a system entity still counts — it is a pure metadata signal, no
    entity-name lists). `skip` (child logical names) prunes within that default;
    a pruned child is listed once in `skipped`.
    """
    rels_raw: list[dict[str, Any]] = backend.get_collection(
        f"EntityDefinitions(LogicalName='{parent_logical}')/OneToManyRelationships",
        params={"$select": "ReferencingEntity,ReferencingAttribute,IsCustomRelationship"},
    )
    rels: list[dict[str, str]] = []
    skipped: list[str] = []
    for r in rels_raw:
        if not r.get("IsCustomRelationship"):
            continue
        child = str(r.get("ReferencingEntity") or "")
        attr = str(r.get("ReferencingAttribute") or "")
        child_set = logical_to_set.get(child, "")
        if not child or not attr or not child_set:
            continue
        if child in skip:
            if child not in skipped:
                skipped.append(child)
            continue
        rels.append({"entity": child, "attribute": attr, "set": child_set})
    return rels, skipped


def _clone_children(
    backend: D365Backend,
    parent_logical: str,
    logical_to_set: dict[str, str],
    *,
    source_parent_guid: str,
    new_parent_id: str,
    skip: set[str],
) -> tuple[dict[str, list[str]], list[dict[str, Any]]]:
    """Clone every direct child row of the custom 1:N relationships (live path).

    Per relationship: one entity-def GET (the child's PrimaryIdAttribute +
    create attributes in a single `$expand`), one annotated collection GET of
    the rows that reference the source parent, then a create per row. A row that
    fails to build or create is recorded in `failures` and the rest continue
    (ADR 0007: no rollback, no abort). Returns ``(children, failures)`` where
    `children` maps child logical name -> created ids.
    """
    rels, _ = _custom_child_relationships(backend, parent_logical, logical_to_set, skip)
    children: dict[str, list[str]] = {}
    failures: list[dict[str, Any]] = []
    # Child entity may appear in more than one relationship; fetch its attribute
    # plan once.
    plan_cache: dict[str, tuple[str, dict[str, str], set[str]]] = {}
    # A child row can satisfy >1 custom relationship to the same parent (two
    # lookup attributes); clone each source row at most once, keyed by
    # (child entity, source primary id).
    seen: set[tuple[str, str]] = set()
    for rel in rels:
        child_logical, child_set, attr = rel["entity"], rel["set"], rel["attribute"]
        if child_logical not in plan_cache:
            entdef = as_dict(backend.get(
                f"EntityDefinitions(LogicalName='{child_logical}')",
                params={
                    "$select": "PrimaryIdAttribute",
                    "$expand": "Attributes($select=LogicalName,AttributeType,IsValidForCreate)",
                },
            ))
            child_create, child_all = _plan_from_specs(
                entity_names.specs_from_rows(entdef.get("Attributes") or [])
            )
            plan_cache[child_logical] = (
                str(entdef.get("PrimaryIdAttribute") or ""), child_create, child_all,
            )
        primary_id, child_create, child_all = plan_cache[child_logical]

        rows = backend.get_collection(
            child_set,
            params={"$filter": f"_{attr}_value eq {source_parent_guid}"},
            extra_headers={"Prefer": 'odata.include-annotations="*"'},
        )

        for row in rows:
            src_child_id = str(row.get(primary_id) or "")
            if src_child_id:
                key = (child_logical, src_child_id)
                if key in seen:
                    continue
                seen.add(key)
            child_body, child_errors = _build_clone_body(
                row, child_create, child_all, logical_to_set, child_logical,
                overrides={}, unset=[],
                repoint=(source_parent_guid, new_parent_id),
            )
            if child_errors:
                failures.append({"entity": child_logical, "source_id": src_child_id,
                                 "reason": "; ".join(child_errors)})
                continue
            try:
                created = create(backend, child_set, child_body, return_record=False)
            except D365Error as exc:
                failures.append({"entity": child_logical, "source_id": src_child_id,
                                 "reason": str(exc)})
                continue
            new_child_id = str(created.get("_entity_id") or "")
            if not new_child_id:
                # Created but no parsable id (no OData-EntityId) — record a
                # failure rather than poison meta.created with an empty id.
                failures.append({"entity": child_logical, "source_id": src_child_id,
                                 "reason": "child created but its new id was not in "
                                           "the create response (no OData-EntityId)"})
                continue
            children.setdefault(child_logical, []).append(new_child_id)
    return children, failures


def _preview_children(
    backend: D365Backend,
    parent_logical: str,
    logical_to_set: dict[str, str],
    source_parent_guid: str,
    skip: set[str],
) -> list[dict[str, Any]]:
    """Dry-run child preview: per-entity row counts + skipped entities.

    Read-only — counts run as direct GETs (the on-prem-safe `$count=true` form
    `_count_via_get` uses), never a write. Each entry is
    ``{entity, would_create: N}`` for a relationship that would be cloned, or
    ``{entity, skipped: True}`` for one pruned by `skip`, so the preview shows
    what won't happen too.
    """
    rels, skipped = _custom_child_relationships(
        backend, parent_logical, logical_to_set, skip
    )
    preview: list[dict[str, Any]] = []
    for rel in rels:
        row = _count_via_get(backend, rel, source_parent_guid)
        entry: dict[str, Any] = {"entity": rel["entity"], "would_create": row["count"]}
        # A child that rejects the count read surfaces as would_create:null + the
        # reason, not a bare null (mirrors count_children's degradation).
        if row.get("error"):
            entry["error"] = row["error"]
        preview.append(entry)
    preview.extend({"entity": s, "skipped": True} for s in skipped)
    return preview
