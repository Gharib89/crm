"""Model-driven app (appmodule) + component binding.

create_app POSTs an appmodules row (read-back non-fatal). add_app_components
wraps the unbound AddAppComponents action. set_sitemap creates a sitemaps row
from raw XML.

Shapes verified live against D365 CE on-prem 9.1 (walkthrough §11):
  * appmodules requires a non-null `webresourceid`; the platform ships a default
    icon (953b9fac-…) used here when no custom icon is given.
  * AddAppComponents takes **typed entity references** — each component is its
    primary-key field plus an `@odata.type`, not a `{Type, Id}` pair. Only
    record-backed components can be bound this way; tables reach the app through
    the sitemap's `Entity=` subareas.
"""

from __future__ import annotations

from typing import Any

from crm.core import metadata_constraints as mc
from crm.core.batch import run_batched
from crm.core.entity_names import load_name_map
from crm.core.metadata import maybe_publish
from crm.utils.d365_backend import (
    D365Backend,
    D365Error,
    as_dict,
    classify_d365_error,
    normalize_guid,
    odata_literal,
)
from crm.utils.d365_types import BatchOperation

# Default app-icon web resource the platform ships (MS Learn, op-9-1).
DEFAULT_APP_ICON = "953b9fac-1e5e-e611-80d6-00155ded156f"

# A Web-API-created appmodule is Unpublished until an app-scoped publish, and the
# plain `appmodules` collection / by-id GET return **published apps only** — so a
# fresh app reads back `Does Not Exist` (MS Learn, "Create, manage, and publish
# model-driven apps using code"; live-confirmed on-prem v9.1 + Dataverse online,
# #809). `RetrieveUnpublishedMultiple` returns the current unpublished view (the
# same data as a plain retrieve once published), so an app resolves regardless of
# publish state. The by-id `RetrieveUnpublished` is **not** bound to appmodule
# ("does not support entities of type 'appmodule'", live-confirmed on-prem v9.1),
# so every read — even by id — goes through the collection function with a filter.
_RETRIEVE_UNPUBLISHED_MULTIPLE = "appmodules/Microsoft.Dynamics.CRM.RetrieveUnpublishedMultiple()"


def _retrieve_unpublished(
    backend: D365Backend,
    *,
    filter_expr: str,
    select: str,
) -> list[dict[str, Any]]:
    """Rows from the appmodule **unpublished** view matching *filter_expr* (#809)."""
    return backend.get_collection(
        _RETRIEVE_UNPUBLISHED_MULTIPLE, params={"$filter": filter_expr, "$select": select}
    )


def _appmodule_publish_xml(app_id: str) -> str:
    """PublishXml ParameterXml that publishes a single appmodule (MS Learn).

    A blanket `PublishAllXml` does **not** flip an appmodule to Published — only
    this app-scoped publish makes a Web-API-created app GET-visible (#809).
    """
    return (
        f"<importexportxml><appmodules><appmodule>{app_id}"
        f"</appmodule></appmodules></importexportxml>"
    )


def publish_app(backend: D365Backend, app_id: str) -> dict[str, Any]:
    """App-scoped publish (PublishXml) that flips a single appmodule to Published.

    The generic `PublishAllXml` does not publish an appmodule, so an app that is
    not yet published — a newly created app with a sitemap bound, or a previously
    bare app that just gained its first sitemap — must be published this way to
    become GET-visible in the `appmodules` collection (#809). A write —
    short-circuits under dry-run.
    """
    # Lazy import: `solution` pulls the wider write stack; keeping it off
    # appmodule's module-load path preserves cold-start CLI latency (one-shot
    # `crm` invocations dominate; see the startup-perf note in project memory).
    from crm.core.solution import publish_xml

    return publish_xml(backend, _appmodule_publish_xml(app_id))


# On-prem v9.x surfaces a duplicate uniquename in the publish-before-read window
# as a SQL uniqueness violation — code 0x80040216 at HTTP 500 — rather than the
# duplicate-detected code family cloud returns (which classify_d365_error maps).
# Scoped to the create+skip path only (see _is_duplicate_create_fault): 0x80040216
# is a generic SQL fault, so it must NOT be treated as a duplicate elsewhere.
_ONPREM_SQL_DUPLICATE_CODE = "0x80040216"


def _is_duplicate_create_fault(exc: D365Error) -> bool:
    """True if *exc* is a duplicate-uniquename fault from the appmodule POST.

    Covers both shapes seen live: the duplicate-detected code family cloud
    returns (classified) and the on-prem SQL uniqueness violation
    (0x80040216 at HTTP 500), which classifies as a generic server_error.
    """
    category, _ = classify_d365_error(exc.status, exc.code, str(exc))
    if category == "duplicate_detected":
        return True
    haystack = f"{exc.code or ''} {exc}".lower()
    return exc.status == 500 and _ONPREM_SQL_DUPLICATE_CODE in haystack


# Friendly component kind -> (primary-key field, OData entity type) for
# AddAppComponents. Tables (entity metadata) are intentionally absent: they are
# not crmbaseentity records, so the action can't bind them — they surface via
# the sitemap instead.
_COMPONENT_REFS: dict[str, tuple[str, str]] = {
    "view": ("savedqueryid", "savedquery"),
    "chart": ("savedqueryvisualizationid", "savedqueryvisualization"),
    "form": ("formid", "systemform"),
    "dashboard": ("formid", "systemform"),
    "sitemap": ("sitemapid", "sitemap"),
    "bpf": ("workflowid", "workflow"),
}

# The three maps below are one relation (friendly-kind ↔ appmodulecomponent
# componenttype ↔ Web-API ref) read in different directions; keep them in step when
# adding a kind. `_COMPONENT_REFS` (above) is the fourth face: kind → (pk, otype).
#
# appmodulecomponent.componenttype (the `appmodulecomponent_componenttype` global
# choice — MS Learn) for each friendly kind. `form` and `dashboard` are both a
# `systemform` (type 60), indistinguishable by component type — which is fine: the
# reconcile diffs by (type, id) and both unbind through the same systemform ref.
_KIND_COMPONENT_TYPE: dict[str, int] = {
    "view": 26,
    "chart": 59,
    "form": 60,
    "dashboard": 60,
    "bpf": 29,
    "sitemap": 62,
}
# The component types the reconcile converges — those the `components:` list binds
# via AddAppComponents. The sitemap (62) converges through its own whole-document
# path; an app's tables (type 1) are bound implicitly by the sitemap's subareas and
# never listed as components. Both are excluded from the component-set diff so the
# reconcile never tries to unbind them.
_RECONCILED_COMPONENT_TYPES = frozenset({26, 29, 59, 60})
# Reverse map for building a RemoveAppComponents reference from a live component's
# type, and for labelling a removal in the drift report. A 60 unbinds as `form`
# whether it was bound as a form or a dashboard — both are systemform, so the ref is
# correct either way; the report label is a systemform approximation (a removed
# dashboard is reported `kind: "form"`), since componenttype alone can't tell the two
# apart and reading each systemform's `type` back purely to relabel is not worth it.
_COMPONENT_TYPE_KIND: dict[int, str] = {26: "view", 29: "bpf", 59: "chart", 60: "form"}


def _norm_guid(guid: str) -> str:
    """Canonical lowercase GUID for set-membership comparison across sources.

    Live `objectid`s arrive lowercased from OData; declared component ids come from
    the spec in any case. Normalizing both sides keeps the component-set diff from
    reporting spurious drift on a mere case difference.
    """
    return (normalize_guid(guid) or guid.strip()).lower()


# The component kinds an app's spec `components:` block may declare — the record-
# backed kinds AddAppComponents can bind (tables reach the app via the sitemap).
APP_COMPONENT_KINDS = frozenset(_COMPONENT_REFS)


def create_app(
    backend: D365Backend,
    *,
    name: str,
    unique_name: str,
    description: str | None = None,
    web_resource_id: str = DEFAULT_APP_ICON,
    client_type: int = 4,
    navigation_type: int = 0,
    publish: bool = False,
    solution: str | None = None,
    if_exists: str = "error",
) -> dict[str, Any]:
    """Create a model-driven app module. Returns `{created, appmoduleid, ...}`."""
    if not name:
        raise D365Error("name is required.")
    mc.validate_schema_name(unique_name, subject="unique_name", example="cwx_crmworx")
    if if_exists not in ("error", "skip"):
        raise D365Error("if_exists must be 'error' or 'skip'.")

    existing = backend.get_collection(
        "appmodules",
        params={
            "$filter": f"uniquename eq {odata_literal(unique_name)}",
            "$select": "appmoduleid,uniquename",
        },
    )
    if existing and not backend.dry_run:
        if if_exists == "error":
            raise D365Error(f"App {unique_name!r} already exists.", code="AlreadyExists")
        return {
            "skipped": True,
            "exists": True,
            "uniquename": unique_name,
            "appmoduleid": existing[0].get("appmoduleid"),
        }

    body: dict[str, Any] = {
        "name": name,
        "uniquename": unique_name,
        "clienttype": client_type,
        "navigationtype": navigation_type,
        "webresourceid": web_resource_id,
    }
    if description:
        body["description"] = description
    try:
        result = as_dict(backend.post("appmodules", json_body=body, solution=solution))
    except D365Error as exc:
        # Skip semantics must survive the publish-before-read window: a freshly
        # created appmodule isn't query-visible until published (see the read-back
        # note below), so a racing `--if-exists skip` can miss the $filter hit
        # above and POST a duplicate. Treat the server's duplicate fault — in
        # either the cloud or on-prem shape (see _is_duplicate_create_fault) — as
        # the skip the empty query couldn't, re-querying for a best-effort id.
        # `if_exists == "error"` still propagates — it genuinely collided.
        if if_exists == "skip" and _is_duplicate_create_fault(exc):
            requery = backend.get_collection(
                "appmodules",
                params={
                    "$filter": f"uniquename eq {odata_literal(unique_name)}",
                    "$select": "appmoduleid,uniquename",
                },
            )
            return {
                "skipped": True,
                "exists": True,
                "uniquename": unique_name,
                "appmoduleid": requery[0].get("appmoduleid") if requery else None,
            }
        raise
    if result.get("_dry_run"):
        result["_exists"] = bool(existing)
        result["would_skip"] = bool(existing) and if_exists == "skip"
        return result

    entity_id_url = result.get("_entity_id_url") or ""
    app_id = result.get("_entity_id")
    out: dict[str, Any] = {
        "created": True,
        "name": name,
        "uniquename": unique_name,
        "appmoduleid": app_id,
        "solution": solution,
    }
    # A freshly created appmodule is bare (no bound sitemap) and therefore not yet
    # publishable as an app — an app-scoped publish would fail ValidateApp. So
    # `create_app` keeps the generic publish (PublishAllXml, publishes surrounding
    # customizations); an app becomes app-published once it is complete — the apply
    # apps phase app-publishes a created app once its sitemap is bound (and an
    # existing app that just gained its first sitemap on reconcile).
    maybe_publish(backend, out, publish)
    # Read-back through the unpublished view (RetrieveUnpublishedMultiple) so it
    # succeeds whether or not the app is published — the plain collection returns
    # published apps only, spuriously failing the create read-back (#809).
    if app_id:
        try:
            rows = _retrieve_unpublished(
                backend,
                filter_expr=f"appmoduleid eq {app_id}",
                select="name,uniquename,appmoduleid",
            )
            if rows:
                out["name"] = rows[0].get("name", name)
            else:
                out["app_lookup_error"] = (
                    "Read-back failed: app not found in the unpublished appmodule view."
                )
        except D365Error as exc:
            out["app_lookup_error"] = f"Read-back failed: {exc}"
    else:
        out["app_lookup_error"] = f"Could not parse appmoduleid from response: {entity_id_url!r}"
    return out


# Server fault when a dependent data row's FK still blocks the appmodule DELETE.
# Verified live to be the SAME code on on-prem v9.x AND Dataverse online — online
# surfaces it for `appsetting` too, even though that relationship's metadata
# `CascadeConfiguration.Delete` reports `Cascade` (its SQL foreign key does not
# actually cascade). So the sweep canNOT trust cascade metadata; it removes every
# row that *references* the app and lets the parent DELETE cascade the rest.
_FK_RESTRICT_CODE = "0x80048d21"

# Attributes read off the resolved appmodule row: its ids (both the primary key
# and the alternate `appmoduleidunique` some children reference), the managed
# guard flag, and the human identifiers used in errors/results.
_APPMODULE_SELECT = "appmoduleid,appmoduleidunique,uniquename,name,ismanaged"


def _resolve_appmodule(backend: D365Backend, name_or_id: str) -> dict[str, Any]:
    """Resolve *name_or_id* to its appmodule row.

    A GUID is read back directly as the appmoduleid; otherwise the row is found
    by ``uniquename``, falling back to the display ``name``. Raises ``D365Error``
    on no match or an ambiguous name.
    """
    target = name_or_id.strip()
    rid = normalize_guid(target)
    if rid is not None:
        rows = _retrieve_unpublished(
            backend, filter_expr=f"appmoduleid eq {rid}", select=_APPMODULE_SELECT
        )
        if rows:
            return rows[0]
        raise D365Error(f"App {name_or_id!r} was not found.", code="AppNotFound")
    for field in ("uniquename", "name"):
        rows = _retrieve_unpublished(
            backend, filter_expr=f"{field} eq {odata_literal(target)}", select=_APPMODULE_SELECT
        )
        if len(rows) > 1:
            raise D365Error(
                f"App {name_or_id!r} is ambiguous — {len(rows)} apps match "
                f"{field}; pass the appmoduleid instead.",
                code="AmbiguousApp",
            )
        if rows:
            return rows[0]
    raise D365Error(f"App {name_or_id!r} was not found.", code="AppNotFound")


def _child_relationships(backend: D365Backend) -> list[dict[str, str]]:
    """Every 1:N relationship where the appmodule is the parent.

    Each row carries the child entity, its referencing (FK) attribute, and the
    appmodule attribute the FK points at (``ReferencedAttribute`` — NOT assumed to
    be ``appmoduleid``, since e.g. ``appmodulecomponent`` references
    ``appmoduleidunique``). Cascade behavior is deliberately ignored: online
    metadata reports ``appsetting`` as ``Cascade`` yet its FK still blocks the
    delete, so the sweep keys off whether a row references the app, not metadata.
    """
    rows: list[dict[str, Any]] = backend.get_collection(
        "EntityDefinitions(LogicalName='appmodule')/OneToManyRelationships",
        params={"$select": "ReferencingEntity,ReferencingAttribute,ReferencedAttribute"},
    )
    rels: list[dict[str, str]] = []
    for r in rows:
        child = str(r.get("ReferencingEntity") or "")
        referencing = str(r.get("ReferencingAttribute") or "")
        referenced = str(r.get("ReferencedAttribute") or "")
        if child and referencing and referenced:
            rels.append({"entity": child, "referencing": referencing, "referenced": referenced})
    return rels


def _discover_dependents(
    backend: D365Backend,
    app_row: dict[str, Any],
    rels: list[dict[str, str]],
) -> list[dict[str, str]]:
    """Every dependent row that references this app, one entry per row.

    Keys each query off the relationship's ``ReferencedAttribute`` value on the
    app row, so a non-primary-key reference is matched correctly. A child entity
    that isn't OData-addressable (or can't be filtered on the lookup) is skipped —
    it can't be swept here; if it then blocks the delete, the parent fault names
    it. Returns ``[{entity, set, id}]``.
    """
    if not rels:
        return []
    name_map = load_name_map(backend)
    dependents: list[dict[str, str]] = []
    for rel in rels:
        child_set = name_map.set_for(rel["entity"])
        child_pk = name_map.primary_id_for(rel["entity"])
        if not child_set or not child_pk:
            continue
        ref_value = app_row.get(rel["referenced"])
        if not ref_value:
            continue
        try:
            rows = backend.get_collection(
                child_set,
                params={
                    "$filter": f"_{rel['referencing']}_value eq {ref_value}",
                    "$select": child_pk,
                },
            )
        except D365Error as exc:
            # A 4xx means this child isn't filterable on the lookup → skip it (if
            # it then blocks the delete, the parent fault names it). A 5xx is a
            # real server fault (already retried by the backend) → propagate.
            if exc.status and exc.status >= 500:
                raise
            continue
        for row in rows:
            cid = row.get(child_pk)
            if cid:
                dependents.append({"entity": rel["entity"], "set": child_set, "id": str(cid)})
    return dependents


def _remaining_blocker_error(
    backend: D365Backend,
    app_row: dict[str, Any],
    rels: list[dict[str, str]],
    exc: D365Error,
) -> D365Error:
    """Build a clear error after the appmodule DELETE is still FK-blocked.

    Re-queries the child relationships ONCE (bounded — never a retry loop) and
    names any that still reference the app, so the user sees the live blocker
    rather than an opaque ``0x80048d21``.
    """
    remaining = _discover_dependents(backend, app_row, rels)
    if remaining:
        names = ", ".join(sorted({d["entity"] for d in remaining}))
        return D365Error(
            f"App delete still blocked after sweeping dependents; these still "
            f"reference it: {names}. Original fault: {exc}",
            status=exc.status,
            code=exc.code,
        )
    return D365Error(
        f"App delete blocked by a dependency that could not be swept "
        f"(child not addressable / not directly deletable): {exc}",
        status=exc.status,
        code=exc.code,
    )


def delete_app(backend: D365Backend, name_or_id: str) -> dict[str, Any]:
    """Delete a model-driven app, sweeping its FK-blocking dependent rows first.

    Resolves *name_or_id* (GUID, else ``uniquename``, else display ``name``),
    refuses a managed app, then removes the dependent data rows that reference the
    app across its 1:N relationships (e.g. ``appsetting`` — which blocks the
    delete on BOTH on-prem and online, the latter despite its ``Cascade``
    metadata) and DELETEs the appmodule. A dependent that won't delete is left for
    the parent DELETE to cascade; if the appmodule DELETE is still blocked
    afterwards, the still-referencing entity is named (no retry loop).

    Honors ``backend.dry_run``: only the read/discovery GETs run (the
    reads-execute rule) and a preview is returned; no DELETE is issued.

    Real run:  ``{deleted, appmodule, dependents_deleted: [{entity, id}]}``
               (plus ``dependents_skipped`` if a dependent would not delete)
    Dry run:   ``{_dry_run, would_delete: {appmodule, dependents: [{entity, id}]}}``
    """
    app_row = _resolve_appmodule(backend, name_or_id)
    app_id = str(app_row.get("appmoduleid") or "")
    if not app_id:
        raise D365Error(f"App {name_or_id!r} has no appmoduleid.", code="AppNotFound")
    if app_row.get("ismanaged"):
        raise D365Error(
            f"App {name_or_id!r} is managed and cannot be deleted directly — "
            "uninstall its parent solution instead.",
            code="ManagedApp",
        )

    rels = _child_relationships(backend)
    dependents = _discover_dependents(backend, app_row, rels)

    if backend.dry_run:
        public = [{"entity": d["entity"], "id": d["id"]} for d in dependents]
        return {"_dry_run": True, "would_delete": {"appmodule": app_id, "dependents": public}}

    deleted: list[dict[str, str]] = []
    skipped: list[dict[str, str]] = []
    # One $batch of DELETEs in place of one round trip per dependent (issue #703).
    # Non-transactional + continue-on-error keeps each delete independent: a
    # dependent that won't delete (e.g. a Cascade child the platform manages) is
    # recorded and left for the parent DELETE to cascade, or surfaced as the
    # blocker below — never aborting the sweep or dropping it silently (ADR 0007).
    ops: list[BatchOperation] = [
        {"method": "DELETE", "url": f"{dep['set']}({dep['id']})"} for dep in dependents
    ]
    for dep, res in zip(dependents, run_batched(backend, ops)):
        (skipped if res.get("error") else deleted).append(dep)
    try:
        backend.delete(f"appmodules({app_id})")
    except D365Error as exc:
        if exc.code == _FK_RESTRICT_CODE:
            raise _remaining_blocker_error(backend, app_row, rels, exc) from exc
        raise
    result: dict[str, Any] = {
        "deleted": True,
        "appmodule": app_id,
        "dependents_deleted": [{"entity": d["entity"], "id": d["id"]} for d in deleted],
    }
    if skipped:
        result["dependents_skipped"] = [{"entity": d["entity"], "id": d["id"]} for d in skipped]
    return result


def _component_refs(components: list[tuple[str, str]]) -> list[dict[str, Any]]:
    """Translate `(kind, guid)` pairs into typed entity references.

    Each kind maps through _COMPONENT_REFS to its primary-key field plus an
    `@odata.type` — the shape both AddAppComponents and RemoveAppComponents
    take. Raises D365Error on an unknown kind before any HTTP call.
    """
    refs: list[dict[str, Any]] = []
    for kind, guid in components:
        if kind not in _COMPONENT_REFS:
            raise D365Error(
                f"unknown component kind {kind!r}; expected one of {sorted(_COMPONENT_REFS)}."
            )
        pk, otype = _COMPONENT_REFS[kind]
        refs.append({pk: guid, "@odata.type": f"Microsoft.Dynamics.CRM.{otype}"})
    return refs


def add_app_components(
    backend: D365Backend,
    *,
    app_id: str,
    components: list[tuple[str, str]],
) -> dict[str, Any]:
    """Bind components to an app via the AddAppComponents action.

    `components` is a list of `(kind, guid)` where kind is one of
    _COMPONENT_REFS. Each becomes a typed entity reference in the action body.
    Raises D365Error on an unknown kind before any HTTP call.
    """
    if not app_id:
        raise D365Error("app_id is required.")
    if not components:
        raise D365Error("at least one component is required.")
    refs = _component_refs(components)
    result = as_dict(
        backend.post("AddAppComponents", json_body={"AppId": app_id, "Components": refs})
    )
    if result.get("_dry_run"):
        result["app_id"] = app_id
        result["components"] = len(refs)
        return result
    return {"added": len(refs), "app_id": app_id}


def remove_app_components(
    backend: D365Backend,
    *,
    app_id: str,
    components: list[tuple[str, str]],
) -> dict[str, Any]:
    """Unbind components from an app via the RemoveAppComponents action.

    Mirror of add_app_components: `components` is a list of `(kind, guid)` where
    kind is one of _COMPONENT_REFS, each becoming the same typed entity reference
    in the action body. Raises D365Error on an unknown kind before any HTTP call.
    """
    if not app_id:
        raise D365Error("app_id is required.")
    if not components:
        raise D365Error("at least one component is required.")
    refs = _component_refs(components)
    result = as_dict(
        backend.post("RemoveAppComponents", json_body={"AppId": app_id, "Components": refs})
    )
    if result.get("_dry_run"):
        result["app_id"] = app_id
        result["components"] = len(refs)
        return result
    return {"removed": len(refs), "app_id": app_id}


def set_sitemap(
    backend: D365Backend,
    *,
    sitemap_name: str,
    sitemap_xml: str,
    unique_name: str | None = None,
    solution: str | None = None,
    publish: bool = False,
) -> dict[str, Any]:
    """Create a sitemaps row from raw SiteMapXml. Returns `{created, sitemapid}`.

    Pass `unique_name` equal to the app's uniquename to auto-associate the
    sitemap with that app (Dataverse links them by sitemapnameunique). With
    `publish=True`, run PublishAllXml after creation (mirrors `build_sitemap`).
    """
    if not sitemap_name.strip():
        raise D365Error("sitemap_name must not be empty.")
    if not sitemap_xml.strip():
        raise D365Error("sitemap_xml must not be empty.")
    body: dict[str, Any] = {"sitemapname": sitemap_name, "sitemapxml": sitemap_xml}
    if unique_name:
        body["sitemapnameunique"] = unique_name
    result = as_dict(backend.post("sitemaps", json_body=body, solution=solution))
    if result.get("_dry_run"):
        return result
    entity_id_url = result.get("_entity_id_url") or ""
    smid = result.get("_entity_id")
    out: dict[str, Any] = {
        "created": True,
        "sitemapid": smid,
        "sitemapname": sitemap_name,
        "solution": solution,
    }
    if not smid:
        out["sitemap_lookup_error"] = f"Could not parse sitemapid from response: {entity_id_url!r}"
    maybe_publish(backend, out, publish)
    return out


def update_sitemap(
    backend: D365Backend,
    *,
    sitemap_id: str,
    sitemap_xml: str,
    solution: str | None = None,
    publish: bool = False,
) -> dict[str, Any]:
    """Replace a sitemap's SiteMapXml wholesale (whole-document converge, ADR 0024).

    A sitemap is edited as one XML document — converge replaces the declared XML
    rather than diffing nodes. ``solution`` scopes the PATCH to the target unmanaged
    solution (same as ``set_sitemap``), so the converged sitemap lands in that
    solution's layer rather than the default. Honors ``backend.dry_run`` (the PATCH
    is suppressed).
    """
    if not sitemap_id:
        raise D365Error("sitemap_id is required.")
    if not sitemap_xml.strip():
        raise D365Error("sitemap_xml must not be empty.")
    result = as_dict(
        backend.patch(
            f"sitemaps({sitemap_id})", json_body={"sitemapxml": sitemap_xml}, solution=solution
        )
    )
    if result.get("_dry_run"):
        return result
    out: dict[str, Any] = {"updated": True, "sitemapid": sitemap_id}
    maybe_publish(backend, out, publish)
    return out


def _read_app_components(
    backend: D365Backend,
    app_id_unique: str,
) -> set[tuple[int, str]]:
    """The app's live component set, limited to the reconciled component types.

    Reads ``appmodulecomponents`` by the app's ``appmoduleidunique`` FK and returns
    ``{(componenttype, objectid)}`` for the types the reconcile owns (views, charts,
    forms/dashboards, BPFs). The sitemap component and implicit table components are
    excluded — they are not managed through the ``components:`` list.
    """
    rows = backend.get_collection(
        "appmodulecomponents",
        params={
            "$filter": f"_appmoduleidunique_value eq {app_id_unique}",
            "$select": "componenttype,objectid",
        },
    )
    live: set[tuple[int, str]] = set()
    for r in rows:
        ct = r.get("componenttype")
        oid = r.get("objectid")
        if ct in _RECONCILED_COMPONENT_TYPES and oid:
            live.add((int(ct), _norm_guid(str(oid))))
    return live


def _read_app_sitemap(
    backend: D365Backend,
    unique_name: str,
) -> tuple[str | None, str | None]:
    """The ``(sitemapid, sitemapxml)`` of the app's linked sitemap, or ``(None, None)``.

    Apps link their sitemap by ``sitemapnameunique == <app uniquename>`` (the
    association ``set_sitemap`` writes). The first match is used.
    """
    rows = backend.get_collection(
        "sitemaps",
        params={
            "$filter": f"sitemapnameunique eq {odata_literal(unique_name)}",
            "$select": "sitemapid,sitemapxml",
        },
    )
    if not rows:
        return None, None
    return rows[0].get("sitemapid"), rows[0].get("sitemapxml")


def reconcile_app(
    backend: D365Backend,
    *,
    unique_name: str,
    components: list[tuple[str, str]],
    sitemap_xml: str | None,
    solution: str | None = None,
) -> dict[str, Any]:
    """Converge an existing app's component set + sitemap to the declared block.

    Reads the live app by ``unique_name``, then converges the two updatable
    surfaces (ADR 0024): the **component set** (add declared-but-absent, remove
    present-but-undeclared, over the reconciled component types) and the
    **sitemap** (whole-document replacement when the declared XML differs). A
    **managed** app is refused with no write (``blocked``) — its components and
    sitemap are owned by its parent solution, so `apply` never mutates it.

    Honors ``backend.dry_run``: the reads run (the reads-execute rule) and the diff
    is computed, but AddAppComponents / RemoveAppComponents / the sitemap write are
    suppressed — so a dry run yields the full drift classification with no write.

    Returns ``{appmoduleid, blocked?, component_changes?, sitemap_change?}``, or
    ``{unreadable: True}`` when the app cannot be read back. The read goes through
    ``_resolve_appmodule``, which reads the **unpublished** view
    (``RetrieveUnpublishedMultiple``) so a freshly Web-API-created — and therefore
    still unpublished — app resolves regardless of publish state (#809);
    ``unreadable`` now means the app genuinely does not exist. With nothing to read,
    there is nothing to converge, so the caller reports it ``skipped``.
    """
    try:
        row = _resolve_appmodule(backend, unique_name)
    except D365Error as exc:
        if exc.code == "AppNotFound":
            return {"unreadable": True}
        raise
    out: dict[str, Any] = {"appmoduleid": str(row.get("appmoduleid") or "")}
    if row.get("ismanaged"):
        out["blocked"] = [
            {
                "name": unique_name,
                "reason": "app is managed; converge it through its parent solution, not apply.",
            }
        ]
        return out
    app_id = out["appmoduleid"]
    app_id_unique = str(row.get("appmoduleidunique") or "")

    # ── component set ──
    declared = [
        (k, i) for k, i in components if _KIND_COMPONENT_TYPE.get(k) in _RECONCILED_COMPONENT_TYPES
    ]
    declared_set = {(_KIND_COMPONENT_TYPE[k], _norm_guid(i)) for k, i in declared}
    live_set: set[tuple[int, str]] = (
        _read_app_components(backend, app_id_unique) if app_id_unique else set()
    )
    to_add = [
        (k, i) for k, i in declared if (_KIND_COMPONENT_TYPE[k], _norm_guid(i)) not in live_set
    ]
    to_remove = [
        (_COMPONENT_TYPE_KIND[t], i) for t, i in sorted(live_set) if (t, i) not in declared_set
    ]
    changes: list[dict[str, str]] = []
    if to_add:
        add_app_components(backend, app_id=app_id, components=to_add)
        changes += [{"kind": k, "id": i, "change": "added"} for k, i in to_add]
    if to_remove:
        remove_app_components(backend, app_id=app_id, components=to_remove)
        changes += [{"kind": k, "id": i, "change": "removed"} for k, i in to_remove]
    if changes:
        out["component_changes"] = changes

    # ── sitemap (whole-document replacement) ──
    # Exact-string compare: the platform stores SiteMapXml verbatim (verified live
    # on both targets — an unchanged declared sitemap re-compares equal, so re-apply
    # is idempotent), and `build_sitemapxml` is deterministic. An org that reformats
    # the stored XML would make every re-apply report a spurious converge; none seen.
    if sitemap_xml is not None:
        smid, live_xml = _read_app_sitemap(backend, unique_name)
        if live_xml is None:
            sm = set_sitemap(
                backend,
                sitemap_name=unique_name,
                sitemap_xml=sitemap_xml,
                unique_name=unique_name,
                solution=solution,
            )
            # Bind the new sitemap to the app (component 62) so the app contains a
            # sitemap and can be published — the sitemapnameunique link alone does
            # not satisfy ValidateApp (#809, live-confirmed on-prem v9.1).
            if sm.get("sitemapid"):
                add_app_components(
                    backend, app_id=app_id, components=[("sitemap", str(sm["sitemapid"]))]
                )
            out["sitemap_change"] = "added"
        elif live_xml != sitemap_xml:
            update_sitemap(
                backend, sitemap_id=str(smid), sitemap_xml=sitemap_xml, solution=solution
            )
            out["sitemap_change"] = "converged"
    return out


def build_sitemapxml(
    areas: list[tuple[str, str]],
    groups: list[tuple[str, str, str]],
    subareas: list[tuple[str, str, str, str | None]],
) -> str:
    """Build a compact SiteMapXml from structured Area/Group/SubArea inputs.

    `areas` = [(area_id, title)], `groups` = [(area_id, group_id, title)],
    `subareas` = [(area_id, group_id, entity, title_or_None)]. SubArea Ids are
    auto-allocated from the entity logical name (suffixed `_2`, `_3`, … to stay
    unique across the whole document); a SubArea Title is emitted only when a
    non-empty one is given, else the platform derives the label from the entity.
    All attribute values are quoteattr-escaped. Raises D365Error on empty input,
    duplicate Ids, or broken Area/Group references.
    """
    # Lazy: saxutils is needed only by this sitemap-XML builder, not by the
    # common appmodule paths — keep it off the module import cost (#702).
    from xml.sax.saxutils import quoteattr

    if not areas:
        raise D365Error("a sitemap needs at least one area.")

    # Strip identifier fields up front: whitespace-only Ids are then caught as
    # empty and emitted Ids stay clean for programmatic callers (the CLI already
    # strips; this mirrors set_sitemap's .strip() discipline). Titles keep their
    # own blank-handling below.
    areas = [(area_id.strip(), title) for area_id, title in areas]
    groups = [(a.strip(), g.strip(), t) for a, g, t in groups]
    subareas = [(a.strip(), g.strip(), e.strip(), t) for a, g, e, t in subareas]

    area_ids: set[str] = set()
    for area_id, _ in areas:
        if not area_id:
            raise D365Error("area_id must not be empty.")
        if area_id in area_ids:
            raise D365Error(f"duplicate area Id {area_id!r}.")
        area_ids.add(area_id)

    # group_id -> area_id, for both uniqueness and subarea referential checks.
    group_area: dict[str, str] = {}
    # area_id -> ordered list of (group_id, title)
    groups_by_area: dict[str, list[tuple[str, str]]] = {a: [] for a, _ in areas}
    for area_id, group_id, title in groups:
        if not group_id:
            raise D365Error("group_id must not be empty.")
        if group_id in group_area:
            raise D365Error(f"duplicate group Id {group_id!r}.")
        if area_id not in area_ids:
            raise D365Error(f"group {group_id!r} references unknown area {area_id!r}.")
        group_area[group_id] = area_id
        groups_by_area[area_id].append((group_id, title))

    # group_id -> ordered list of (sub_id, entity, title_or_None)
    subs_by_group: dict[str, list[tuple[str, str, str | None]]] = {g: [] for g in group_area}
    used_sub_ids: set[str] = set()
    for area_id, group_id, entity, title in subareas:
        if not entity:
            raise D365Error("subarea entity must not be empty.")
        if group_area.get(group_id) != area_id:
            raise D365Error(
                f"subarea on entity {entity!r} does not reference a defined "
                f"group {group_id!r} in area {area_id!r}."
            )
        sub_id = entity
        n = 1
        while sub_id in used_sub_ids:
            n += 1
            sub_id = f"{entity}_{n}"
        used_sub_ids.add(sub_id)
        subs_by_group[group_id].append((sub_id, entity, title))

    parts: list[str] = ["<SiteMap>"]
    for area_id, area_title in areas:
        a_title = area_title if area_title.strip() else area_id
        parts.append(f"<Area Id={quoteattr(area_id)} Title={quoteattr(a_title)}>")
        for group_id, group_title in groups_by_area[area_id]:
            g_title = group_title if group_title.strip() else group_id
            parts.append(f"<Group Id={quoteattr(group_id)} Title={quoteattr(g_title)}>")
            for sub_id, entity, title in subs_by_group[group_id]:
                attrs = f"Id={quoteattr(sub_id)} Entity={quoteattr(entity)}"
                if title and title.strip():
                    attrs += f" Title={quoteattr(title)}"
                parts.append(f"<SubArea {attrs} />")
            parts.append("</Group>")
        parts.append("</Area>")
    parts.append("</SiteMap>")
    return "".join(parts)


def build_sitemap(
    backend: D365Backend,
    *,
    sitemap_name: str,
    areas: list[tuple[str, str]],
    groups: list[tuple[str, str, str]],
    subareas: list[tuple[str, str, str, str | None]],
    unique_name: str | None = None,
    solution: str | None = None,
    publish: bool = False,
) -> dict[str, Any]:
    """Build a SiteMapXml from structured inputs and create the sitemaps row.

    Under dry-run, returns `{_dry_run, sitemapname, sitemapxml}` with the built
    XML and does NOT POST. Otherwise delegates to `set_sitemap` so the POST body
    is byte-identical to the existing set-sitemap path, then optionally publishes.
    """
    if not sitemap_name.strip():
        raise D365Error("sitemap_name must not be empty.")
    xml = build_sitemapxml(areas, groups, subareas)
    if backend.dry_run:
        return {"_dry_run": True, "sitemapname": sitemap_name, "sitemapxml": xml}
    out = set_sitemap(
        backend,
        sitemap_name=sitemap_name,
        sitemap_xml=xml,
        unique_name=unique_name,
        solution=solution,
    )
    maybe_publish(backend, out, publish)
    return out
