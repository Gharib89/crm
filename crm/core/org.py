"""Org-level orientation — the one-call agent-first inventory (#790).

`org_brief` consolidates what an agent needs to orient in an unfamiliar org into a
single read-only call, priced for a context window: **counts and key names, never
full component rows**. It is the org-level analogue of `metadata describe` (the
per-entity write-readiness brief).

The number of HTTP round-trips is a fixed constant (`EXPECTED_REQUESTS`),
independent of org size — that bound is the feature, so `crm org brief` stays cheap
where the six fat list verbs it replaces (`solution list`, `metadata entities`,
`webresource list`, …) each return hundreds to tens of thousands of rows. The
budget holds because inherently-small sets (solutions, publishers, custom entities,
apps, workflow *definitions*) are read once with a narrow `$select` and summarized
client-side, while sets that can grow without bound (web resources, plug-in steps,
SLAs, duplicate rules, security roles) are never fetched — only counted via
`$count`.
"""

from __future__ import annotations

from typing import Any

from crm.core import metadata as metadata_mod
from crm.core import optionsets as optionsets_mod
from crm.core import solution as solution_mod
from crm.utils.d365_backend import D365Backend, as_dict

# Cap for the "key names" lists carried in the brief. The true total is always
# reported alongside, so a capped list never masks the real size.
_NAME_CAP = 200

# System solutions that are never valid `--solution` customization targets, so
# they are excluded from the candidate list (but still counted).
_SYSTEM_SOLUTIONS = frozenset({"Default", "Active"})

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
#   identity: WhoAmI + organizations + RetrieveVersion            = 3
#   solutions, publishers, custom entities, optionsets, apps      = 5
#   plugin assemblies + steps, workflows, slas                    = 4
#   webresources: total + one per _WEBRESOURCE_TYPES              = 1 + 3
#   custom security roles, duplicate rules                        = 2
EXPECTED_REQUESTS = 3 + 5 + 4 + (1 + len(_WEBRESOURCE_TYPES)) + 2


def _count(backend: D365Backend, entity_set: str, *, filter_expr: str | None = None) -> int:
    """Return the total row count of *entity_set* via one narrow `$count` read.

    Uses `?$count=true&$top=1` (not the `/<set>/$count` path) so a `$filter` can
    ride along: on-prem v9.1 rejects a `$filter` bound to the `/$count` Edm.Int32
    result, whereas `$count=true` returns the full `@odata.count` regardless of
    `$top` on both targets (see crm.core.entity._count_url). `$top=1` caps the
    returned rows to one; only `@odata.count` is consumed.

    Ceiling: Dataverse saturates the `@odata.count` annotation at 5000 — a set
    with more rows reports exactly 5000. An exact count past that needs
    RetrieveTotalRecordCount, which is deliberately out of scope for the brief
    (its availability differs by target); a saturated count still tells an agent
    "large" without a fat sweep.
    """
    params: dict[str, str] = {"$count": "true", "$top": "1"}
    if filter_expr is not None:
        params["$filter"] = filter_expr
    body = as_dict(backend.get(entity_set, params=params))
    return int(body.get("@odata.count", 0))


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
    rows = solution_mod.list_solutions(backend)
    managed = sum(1 for r in rows if r.get("ismanaged"))
    candidate = [
        r["uniquename"] for r in rows
        if not r.get("ismanaged") and r.get("uniquename") not in _SYSTEM_SOLUTIONS
    ]
    return {
        "managed": managed,
        "unmanaged": len(rows) - managed,
        "unmanaged_names": candidate[:_NAME_CAP],
        "unmanaged_names_total": len(candidate),
    }


def _publishers(backend: D365Backend) -> dict[str, Any]:
    rows = backend.get_collection("publishers", params={
        "$select": "uniquename,friendlyname,customizationprefix",
        "$orderby": "uniquename",
    })
    items = [
        {
            "unique_name": r.get("uniquename"),
            "friendly_name": r.get("friendlyname"),
            "prefix": r.get("customizationprefix"),
        }
        for r in rows
    ]
    return {"count": len(items), "items": items[:_NAME_CAP], "items_total": len(items)}


def _schema(backend: D365Backend) -> dict[str, Any]:
    entities = metadata_mod.list_entities(backend, custom_only=True)
    names = [e["LogicalName"] for e in entities if e.get("LogicalName")]
    optionsets = optionsets_mod.list_optionsets(backend)
    return {
        "custom_entities": len(names),
        "custom_entity_names": names[:_NAME_CAP],
        "custom_entity_names_total": len(names),
        "global_optionsets": len(optionsets),
    }


def _apps(backend: D365Backend) -> dict[str, Any]:
    rows = backend.get_collection("appmodules", params={
        "$select": "name,uniquename",
        "$orderby": "name",
    })
    names = [r["name"] for r in rows if r.get("name")]
    return {"count": len(names), "names": names[:_NAME_CAP], "names_total": len(names)}


def _automation(backend: D365Backend) -> dict[str, Any]:
    workflows = backend.get_collection("workflows", params={
        "$select": "category,statecode",
        "$filter": f"type eq {_WORKFLOW_TYPE_DEFINITION}",
    })
    by_category: dict[str, dict[str, int]] = {}
    for wf in workflows:
        key = _WORKFLOW_CATEGORIES.get(int(wf.get("category", -1)), "other")
        bucket = by_category.setdefault(key, {"total": 0, "activated": 0})
        bucket["total"] += 1
        if wf.get("statecode") == _WORKFLOW_STATE_ACTIVATED:
            bucket["activated"] += 1
    return {
        "plugin_assemblies": _count(backend, "pluginassemblies"),
        "plugin_steps": _count(backend, "sdkmessageprocessingsteps"),
        "workflows": {"total": len(workflows), "by_category": by_category},
        "slas": _count(backend, "slas"),
    }


def _components(backend: D365Backend) -> dict[str, Any]:
    by_type = {
        label: _count(backend, "webresourceset", filter_expr=f"webresourcetype eq {code}")
        for label, code in _WEBRESOURCE_TYPES.items()
    }
    return {
        "webresources": {"total": _count(backend, "webresourceset"), "by_type": by_type},
        "security_roles_custom": _count(backend, "roles", filter_expr="ismanaged eq false"),
        "duplicate_rules": _count(backend, "duplicaterules"),
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
