"""Org-level orientation — the one-call agent-first inventory (#790).

`org_brief` consolidates what an agent needs to orient in an unfamiliar org into a
single read-only call, priced for a context window: **counts and key names, never
full component rows**. It is the org-level analogue of `metadata describe` (the
per-entity write-readiness brief).

The number of HTTP round-trips is a fixed constant (`EXPECTED_REQUESTS`),
independent of org size — that bound is the feature, so `crm org brief` stays cheap
where the six fat list verbs it replaces (`solution list`, `metadata entities`,
`webresource list`, …) each return hundreds to tens of thousands of rows. Every
read is either a single-page GET (`$top` cap + `$count=true`, so the capped rows
feed the "key names" list and `@odata.count` feeds the true total in one round-trip
that never follows `@odata.nextLink`), a bare `$count` for a set that is only
counted, or a metadata query (`EntityDefinitions` / `GlobalOptionSetDefinitions`)
that returns its whole result in one response. No read scales with the number of
rows it summarizes.
"""

from __future__ import annotations

from typing import Any

from crm.core import metadata as metadata_mod
from crm.core import optionsets as optionsets_mod
from crm.utils.d365_backend import D365Backend, as_dict

# Cap for the "key names" lists carried in the brief. The true total is always
# reported alongside (from `@odata.count`), so a capped list never masks the real
# size.
_NAME_CAP = 200

# System solutions excluded from the `--solution` candidate list (but still
# counted). Candidate hygiene, not server rejection: the server rejects `Active`
# and `Basic` as component-add targets (0x80040203) but accepts `Default` — it is
# excluded anyway because customizations belong in a real unmanaged solution.
_SYSTEM_SOLUTIONS = frozenset({"Default", "Active", "Basic"})

# workflow.category id -> stable brief key. Mirrors crm.core.workflow's CATEGORY_*.
_WORKFLOW_CATEGORIES: dict[int, str] = {
    0: "workflow",
    1: "dialog",
    2: "business_rule",
    3: "action",
    4: "bpf",
    5: "modern_flow",
}
_WORKFLOW_TYPE_DEFINITION = 1
_WORKFLOW_STATE_ACTIVATED = 1

# Web resource types worth breaking out (webresourcetype option-set values). The
# code assets an agent cares about; the total covers every type including these.
_WEBRESOURCE_TYPES: dict[str, int] = {"html": 1, "css": 2, "script": 3}

# Fixed request budget — see module docstring. Counted by the offline suite so a
# regression that reintroduces a per-row sweep fails loudly.
#   identity: WhoAmI + organizations + RetrieveVersion                = 3
#   solutions: managed $count + unmanaged single-page                 = 2
#   publishers, apps single-page; custom entities, optionsets metadata= 4
#   workflows single-page; plugin assemblies + steps; slas            = 4
#   webresources: total + one per _WEBRESOURCE_TYPES                  = 1 + 3
#   custom security roles, duplicate rules                            = 2
EXPECTED_REQUESTS = 3 + 2 + 4 + 4 + (1 + len(_WEBRESOURCE_TYPES)) + 2


def _count(
    backend: D365Backend,
    entity_set: str,
    *,
    select: str,
    filter_expr: str | None = None,
) -> int:
    """Return the total row count of *entity_set* via one narrow `$count` read.

    Uses `?$count=true&$top=1` (not the `/<set>/$count` path) so a `$filter` can
    ride along: on-prem v9.1 rejects a `$filter` bound to the `/$count` Edm.Int32
    result, whereas `$count=true` returns the full `@odata.count` regardless of
    `$top` on both targets (see crm.core.entity._count_url). `$top=1` caps the
    returned rows to one, and `select` (the set's primary key) narrows that one row
    to a single column so the count read stays tiny — only `@odata.count` is
    consumed. Minimizing payload is part of the feature.

    Ceiling: Dataverse saturates the `@odata.count` annotation at 5000 — a set
    with more rows reports exactly 5000. An exact count past that needs
    RetrieveTotalRecordCount, which is deliberately out of scope for the brief
    (its availability differs by target); a saturated count still tells an agent
    "large" without a fat sweep.
    """
    params: dict[str, str] = {"$count": "true", "$top": "1", "$select": select}
    if filter_expr is not None:
        params["$filter"] = filter_expr
    body = as_dict(backend.get(entity_set, params=params))
    return int(body.get("@odata.count", 0))


def _page(
    backend: D365Backend,
    entity_set: str,
    *,
    select: str,
    top: int = _NAME_CAP,
    filter_expr: str | None = None,
    orderby: str | None = None,
) -> tuple[list[dict[str, Any]], int]:
    """One single-page GET → (capped rows, true total).

    `$top` bounds the returned rows to one page (a `$top` request never emits
    `@odata.nextLink`, so this is exactly one round-trip regardless of org size),
    while `$count=true` returns the full `@odata.count` for the "*_total" fields.
    The 5000 count ceiling in `_count` applies to the total here too.
    """
    params: dict[str, str] = {"$select": select, "$top": str(top), "$count": "true"}
    if filter_expr is not None:
        params["$filter"] = filter_expr
    if orderby is not None:
        params["$orderby"] = orderby
    body = as_dict(backend.get(entity_set, params=params))
    rows: list[dict[str, Any]] = body.get("value") or []
    total = int(body.get("@odata.count", len(rows)))
    return rows, total


def _identity(backend: D365Backend) -> dict[str, Any]:
    who = as_dict(backend.get("WhoAmI"))
    org_id = who.get("OrganizationId")
    org_name: str | None = None
    if org_id:
        org_name = as_dict(backend.get(f"organizations({org_id})?$select=name")).get("name")
    version = as_dict(backend.get("RetrieveVersion()")).get("Version")
    return {
        "profile": backend.profile.name,
        "url": backend.profile.api_base,
        "org_name": org_name,
        "version": version,
        "api_version": backend.profile.api_version,
        "user_id": who.get("UserId"),
        "organization_id": org_id,
    }


def _solutions(backend: D365Backend) -> dict[str, Any]:
    managed = _count(backend, "solutions", select="solutionid", filter_expr="ismanaged eq true")
    rows, unmanaged = _page(
        backend,
        "solutions",
        select="uniquename",
        filter_expr="ismanaged eq false",
        orderby="uniquename",
    )
    candidates = [
        r["uniquename"]
        for r in rows
        if r.get("uniquename") and r.get("uniquename") not in _SYSTEM_SOLUTIONS
    ]
    # `unmanaged` (from @odata.count) is the true total; `unmanaged_names` is the
    # capped, system-solution-excluded candidate list. Unmanaged solutions number in
    # the low dozens even on large orgs, so the cap effectively never truncates.
    return {
        "managed": managed,
        "unmanaged": unmanaged,
        "unmanaged_names": candidates,
    }


def _publishers(backend: D365Backend) -> dict[str, Any]:
    rows, total = _page(
        backend,
        "publishers",
        select="uniquename,friendlyname,customizationprefix",
        orderby="uniquename",
    )
    items = [
        {
            "unique_name": r.get("uniquename"),
            "friendly_name": r.get("friendlyname"),
            "prefix": r.get("customizationprefix"),
        }
        for r in rows
    ]
    # `count` is the true total (@odata.count); `items` is capped at _NAME_CAP.
    return {"count": total, "items": items}


def _schema(backend: D365Backend) -> dict[str, Any]:
    # EntityDefinitions / GlobalOptionSetDefinitions are metadata queries: each
    # returns its whole result in a single response (no `@odata.nextLink`), so a
    # plain read is one round-trip. Only narrow `$select`s are pulled.
    entities = metadata_mod.list_entities(backend, custom_only=True)
    names = [e["LogicalName"] for e in entities if e.get("LogicalName")]
    optionsets = optionsets_mod.list_optionsets(backend)
    # `list_entities` returns the full metadata set in one response, so
    # `custom_entities` is the true total; only the names list is capped.
    return {
        "custom_entities": len(names),
        "custom_entity_names": names[:_NAME_CAP],
        "global_optionsets": len(optionsets),
    }


def _apps(backend: D365Backend) -> dict[str, Any]:
    rows, total = _page(backend, "appmodules", select="name,uniquename", orderby="name")
    names = [r["name"] for r in rows if r.get("name")]
    # `count` is the true total (@odata.count); `names` is capped at _NAME_CAP.
    return {"count": total, "names": names}


def _automation(backend: D365Backend) -> dict[str, Any]:
    # One page of workflow *definitions* (a narrow two-column projection), capped at
    # a single page so the request count stays constant. Dataverse's default page
    # size is 5000 and no smaller `Prefer: odata.maxpagesize` is sent, so this page
    # holds up to 5000 rows — the same ceiling the `$count` reads saturate at. The
    # `total` and `by_category` breakdown therefore carry the same "at least 5000"
    # semantics as every other count in the brief, never a tighter undercount.
    workflows = backend.get_collection(
        "workflows",
        params={
            "$select": "category,statecode",
            "$filter": f"type eq {_WORKFLOW_TYPE_DEFINITION}",
        },
        max_pages=1,
    )
    by_category: dict[str, dict[str, int]] = {}
    for wf in workflows:
        key = _WORKFLOW_CATEGORIES.get(int(wf.get("category", -1)), "other")
        bucket = by_category.setdefault(key, {"total": 0, "activated": 0})
        bucket["total"] += 1
        if wf.get("statecode") == _WORKFLOW_STATE_ACTIVATED:
            bucket["activated"] += 1
    return {
        "plugin_assemblies": _count(backend, "pluginassemblies", select="pluginassemblyid"),
        "plugin_steps": _count(
            backend, "sdkmessageprocessingsteps", select="sdkmessageprocessingstepid"
        ),
        "workflows": {"total": len(workflows), "by_category": by_category},
        "slas": _count(backend, "slas", select="slaid"),
    }


def _components(backend: D365Backend) -> dict[str, Any]:
    by_type = {
        label: _count(
            backend,
            "webresourceset",
            select="webresourceid",
            filter_expr=f"webresourcetype eq {code}",
        )
        for label, code in _WEBRESOURCE_TYPES.items()
    }
    return {
        "webresources": {
            "total": _count(backend, "webresourceset", select="webresourceid"),
            "by_type": by_type,
        },
        "security_roles_custom": _count(
            backend, "roles", select="roleid", filter_expr="ismanaged eq false"
        ),
        "duplicate_rules": _count(backend, "duplicaterules", select="duplicateruleid"),
    }


def org_brief(backend: D365Backend) -> dict[str, Any]:
    """Assemble the summarized, read-only org inventory (#790).

    Issues exactly `EXPECTED_REQUESTS` narrow GETs and returns a section dict —
    identity, solutions, publishers, schema, apps, automation, components — of
    counts and capped key-name lists. Never fetches full component rows.
    """
    return {
        "identity": _identity(backend),
        "solutions": _solutions(backend),
        "publishers": _publishers(backend),
        "schema": _schema(backend),
        "apps": _apps(backend),
        "automation": _automation(backend),
        "components": _components(backend),
    }
