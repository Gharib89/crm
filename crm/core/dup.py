"""Duplicate-detection rules via the D365 Web API.

Wraps the ``duplicaterule`` + ``duplicaterulecondition`` entities and the
``PublishDuplicateRule`` / ``UnpublishDuplicateRule`` actions, plus the
``RetrieveDuplicates`` function used to test a candidate record against the
published rules.

A duplicate rule binds a *base* entity to a *matching* entity (the same entity
for the common within-table case) and carries one or more conditions, each
comparing a base column to a matching column with an operator. A freshly
created rule is **unpublished** — only published rules participate in detection.
The two state transitions are asymmetric: ``PublishDuplicateRule`` is a *bound*,
**asynchronous** action (it submits a match-code build job, polled via
:meth:`D365Backend.poll_async_operation`), whereas ``UnpublishDuplicateRule`` is
an *unbound*, **synchronous** action that completes immediately.

Every public function takes the backend first and returns a plain dict (or list
of dicts) — the Click layer owns all formatting. Click-free so the module stays
pyright-strict.
"""

from __future__ import annotations

import json
from typing import Any, cast

from crm.core import entity as entity_mod
from crm.utils.d365_backend import (
    D365Backend,
    D365Error,
    as_dict,
    normalize_guid,
    odata_literal,
    solution_headers,
)

# ── Constants ──────────────────────────────────────────────────────────────

RULES_SET = "duplicaterules"
CONDITIONS_SET = "duplicateruleconditions"
_RULE_ID = "duplicateruleid"
_CONDITION_ID = "duplicateruleconditionid"
# A condition links to its rule through the polymorphic `regardingobjectid`
# lookup (the bind property the server requires on create), so its rule filter
# is `_regardingobjectid_value` — NOT `_duplicateruleid_value`, which does not
# exist on duplicaterulecondition.
_RULE_LOOKUP_VALUE = "_regardingobjectid_value"

# duplicaterulecondition.operatorcode — the duplicaterulecondition_operatorcode
# global choice. Friendly CLI names map to these integer codes. "same-first" /
# "same-last" require an OperatorParam (the N character count); the others must
# NOT carry one (the server rejects OperatorParam on ExactMatch et al.).
OPERATORS: dict[str, int] = {
    "exact": 0,
    "same-first": 1,
    "same-last": 2,
    "same-date": 3,
    "same-datetime": 4,
    "exact-picklist-label": 5,
    "exact-picklist-value": 6,
}
# Operators that take (and require) an OperatorParam character count.
_PARAM_OPERATORS = frozenset({"same-first", "same-last"})


# ── Rule resolution ──────────────────────────────────────────────────────


def resolve_rule_id(backend: D365Backend, rule: str) -> str:
    """Resolve a duplicate-rule reference (GUID or name) to its id.

    A GUID is used as-is; anything else is treated as the rule ``name`` and
    looked up with an exact match. Raises :class:`D365Error` (``NotFound``) when
    no rule matches. The read runs for real even under dry-run so a preview path
    still resolves the id it needs.
    """
    gid = normalize_guid(rule)
    if gid is not None:
        return gid
    rid = backend.resolve_id_by_name(
        RULES_SET, filter_field="name", id_field=_RULE_ID, value=rule,
    )
    if rid is None:
        raise D365Error(f"No duplicate rule named {rule!r}.", code="NotFound")
    return rid


# ── Reads ──────────────────────────────────────────────────────────────────


def list_rules(
    backend: D365Backend, *, entity: str | None = None,
) -> list[dict[str, Any]]:
    """List duplicate rules (id, name, base/matching entity, status).

    ``entity`` filters to rules whose base entity is that logical name.
    """
    params: dict[str, Any] = {
        "$select": f"{_RULE_ID},name,baseentityname,matchingentityname,statuscode,statecode",
        "$orderby": "name",
    }
    if entity:
        params["$filter"] = f"baseentityname eq {odata_literal(entity)}"
    return backend.get_collection(RULES_SET, params=params)


def get_rule(backend: D365Backend, rule: str) -> dict[str, Any]:
    """Retrieve one rule plus the conditions it carries.

    ``rule`` is a GUID or a rule name. Returns the rule fields with a
    ``conditions`` list of ``{duplicateruleconditionid, baseattributename,
    matchingattributename, operatorcode, operatorparam}`` entries.
    """
    rule_id = resolve_rule_id(backend, rule)
    record = as_dict(backend.get(
        entity_mod.build_record_path(RULES_SET, rule_id),
        params={
            "$select": (
                f"{_RULE_ID},name,baseentityname,matchingentityname,"
                "statuscode,statecode,description"
            ),
        },
    ))
    conditions = backend.get_collection(
        CONDITIONS_SET,
        params={
            "$select": (
                f"{_CONDITION_ID},baseattributename,matchingattributename,"
                "operatorcode,operatorparam"
            ),
            "$filter": f"{_RULE_LOOKUP_VALUE} eq {rule_id}",
        },
    )
    record["conditions"] = conditions
    return record


# ── Writes ─────────────────────────────────────────────────────────────────


def create_rule(
    backend: D365Backend,
    *,
    name: str,
    entity: str,
    matching_entity: str | None = None,
    description: str | None = None,
    solution: str | None = None,
) -> dict[str, Any]:
    """Create an (unpublished) duplicate-detection rule.

    ``entity`` is the base entity logical name; ``matching_entity`` defaults to
    the same entity (the common within-table case). The rule is created
    unpublished — call :func:`publish_rule` to activate it. Returns
    ``{created, duplicateruleid, ...}``.
    """
    if not name:
        raise D365Error("name is required.")
    if not entity:
        raise D365Error("entity is required.")
    match = matching_entity or entity
    body: dict[str, Any] = {
        "name": name,
        "baseentityname": entity,
        "matchingentityname": match,
    }
    if description is not None:
        body["description"] = description
    result = as_dict(backend.post(
        RULES_SET, json_body=body, extra_headers=solution_headers(solution),
    ))
    if result.get("_dry_run"):
        result["would_create"] = True
        return result
    rule_id = result.get("_entity_id")
    out: dict[str, Any] = {
        "created": True,
        "name": name,
        _RULE_ID: rule_id,
        "baseentityname": entity,
        "matchingentityname": match,
        "solution": solution,
    }
    if not rule_id:
        out["duplicaterule_lookup_error"] = (
            "Could not parse duplicateruleid from response: "
            f"{result.get('_entity_id_url')!r}"
        )
    return out


def add_condition(
    backend: D365Backend,
    *,
    rule: str,
    attribute: str,
    operator: str,
    matching_attribute: str | None = None,
    operator_param: int | None = None,
    ignore_blank_values: bool = False,
    solution: str | None = None,
) -> dict[str, Any]:
    """Add a match condition to a duplicate rule.

    ``rule`` is a GUID or a rule name. ``attribute`` is the base column;
    ``matching_attribute`` defaults to the same column. ``operator`` is one of
    :data:`OPERATORS`. ``same-first`` / ``same-last`` require ``operator_param``
    (the N character count, ``>= 1``); the other operators reject it (the server
    forbids OperatorParam on ExactMatch et al.).
    """
    if not attribute:
        raise D365Error("attribute is required.")
    if operator not in OPERATORS:
        raise D365Error(
            f"unknown operator {operator!r}; expected one of {sorted(OPERATORS)}.",
        )
    needs_param = operator in _PARAM_OPERATORS
    if needs_param and (operator_param is None or operator_param < 1):
        raise D365Error(
            f"operator {operator!r} requires --operator-param >= 1 "
            "(the character count to compare).",
        )
    if not needs_param and operator_param is not None:
        raise D365Error(
            f"operator {operator!r} does not take --operator-param.",
        )
    rule_id = resolve_rule_id(backend, rule)
    match_attr = matching_attribute or attribute
    body: dict[str, Any] = {
        "baseattributename": attribute,
        "matchingattributename": match_attr,
        "operatorcode": OPERATORS[operator],
        "ignoreblankvalues": ignore_blank_values,
        "regardingobjectid@odata.bind": f"/{RULES_SET}({rule_id})",
    }
    if needs_param:
        body["operatorparam"] = operator_param
    result = as_dict(backend.post(
        CONDITIONS_SET, json_body=body, extra_headers=solution_headers(solution),
    ))
    if result.get("_dry_run"):
        result["would_create"] = True
        return result
    condition_id = result.get("_entity_id")
    out: dict[str, Any] = {
        "created": True,
        _CONDITION_ID: condition_id,
        "rule": rule_id,
        "baseattributename": attribute,
        "matchingattributename": match_attr,
        "operatorcode": OPERATORS[operator],
        "operator": operator,
        "solution": solution,
    }
    if needs_param:
        out["operatorparam"] = operator_param
    if not condition_id:
        out["duplicaterulecondition_lookup_error"] = (
            "Could not parse duplicateruleconditionid from response: "
            f"{result.get('_entity_id_url')!r}"
        )
    return out


# ── Publish (async) / unpublish (sync) ───────────────────────────────────────


def publish_rule(
    backend: D365Backend,
    rule: str,
    *,
    wait: bool = False,
    timeout: int | None = None,
) -> dict[str, Any]:
    """Publish a duplicate rule via ``PublishDuplicateRule`` (async).

    ``PublishDuplicateRule`` is a **bound** action on the ``duplicaterule`` entity;
    it submits a background job that builds the match codes and returns the
    ``asyncoperation`` record inline (the job id is its ``asyncoperationid``).
    ``rule`` is a GUID or a rule name. With ``wait=True`` this polls the async
    operation to completion; otherwise it returns once the job is submitted.

    The rule must carry at least one condition — the server rejects publishing a
    condition-less rule (``0x80048414``).
    """
    rule_id = resolve_rule_id(backend, rule)
    path = f"{RULES_SET}({rule_id})/Microsoft.Dynamics.CRM.PublishDuplicateRule"
    resp = as_dict(backend.post(path))
    if resp.get("_dry_run"):
        return {"_dry_run": True, "would_submit": "PublishDuplicateRule", _RULE_ID: rule_id}
    job_id = resp.get("asyncoperationid")
    job_id = job_id if isinstance(job_id, str) else None
    result: dict[str, Any] = {
        _RULE_ID: rule_id,
        "action": "PublishDuplicateRule",
        "job_id": job_id,
    }
    if not wait or not job_id:
        # The match-code build job is still running — the rule is not yet active.
        # Report the submitted state without claiming completion.
        result["status"] = "submitted"
        result["published"] = False
        return result
    backend.poll_async_operation(job_id, timeout=timeout)
    result["status"] = "completed"
    result["published"] = True
    return result


def unpublish_rule(backend: D365Backend, rule: str) -> dict[str, Any]:
    """Unpublish a duplicate rule via ``UnpublishDuplicateRule`` (synchronous).

    Unlike publishing, ``UnpublishDuplicateRule`` is an **unbound** action taking
    the rule id in its body, and it completes synchronously (204) — it deletes
    the match codes immediately, with no async job to poll. ``rule`` is a GUID or
    a rule name.
    """
    rule_id = resolve_rule_id(backend, rule)
    resp = backend.post("UnpublishDuplicateRule", json_body={"DuplicateRuleId": rule_id})
    if isinstance(resp, dict) and resp.get("_dry_run"):
        return {"_dry_run": True, "would_submit": "UnpublishDuplicateRule", _RULE_ID: rule_id}
    return {
        _RULE_ID: rule_id,
        "action": "UnpublishDuplicateRule",
        "unpublished": True,
    }


# ── Check a candidate record (RetrieveDuplicates) ─────────────────────────────


def check(
    backend: D365Backend,
    *,
    entity: str,
    record: dict[str, Any],
    matching_entity: str | None = None,
    top: int = 50,
) -> dict[str, Any]:
    """Test a candidate record against the published rules for ``entity``.

    Calls the ``RetrieveDuplicates`` function with the candidate ``record`` as
    the ``BusinessEntity`` (a not-yet-created record is fine — its attribute
    values are matched against existing rows). ``matching_entity`` defaults to
    ``entity``; ``top`` caps the page size. Returns ``{entity, matching_entity,
    count, duplicates}`` where ``duplicates`` is the list of matching existing
    records (empty when none).

    Detection only fires for **published** rules on a duplicate-detection-enabled
    entity; with no published rule the result is always empty.
    """
    if not entity:
        raise D365Error("entity is required.")
    if not record:
        raise D365Error("record is required (the candidate to check).")
    if top < 1:
        raise D365Error("top must be >= 1 (it is the RetrieveDuplicates page size).")
    match = matching_entity or entity
    # The @odata.type cast must match ENTITY and win over any caller-supplied
    # "@odata.type" in the payload, so set it last (after spreading the record).
    business_entity = {**record, "@odata.type": f"Microsoft.Dynamics.CRM.{entity}"}
    # RetrieveDuplicates is a function whose BusinessEntity parameter is an
    # entity; entity-typed and complex function params must travel as parameter
    # aliases in the query string (the server rejects them inline). PagingInfo is
    # required — the server 400s ("Required field 'PagingInfo' missing") if it is
    # omitted or null. The read runs for real even under dry-run.
    paging = {"PageNumber": 1, "Count": top, "ReturnTotalRecordCount": False}
    path = "RetrieveDuplicates(BusinessEntity=@p1,MatchingEntityName=@p2,PagingInfo=@p3)"
    params = {
        "@p1": json.dumps(business_entity, separators=(",", ":")),
        "@p2": odata_literal(match),
        "@p3": json.dumps(paging, separators=(",", ":")),
    }
    resp = as_dict(backend.get(path, params=params))
    raw = resp.get("value")
    duplicates = cast("list[dict[str, Any]]", raw) if isinstance(raw, list) else []
    return {
        "entity": entity,
        "matching_entity": match,
        "count": len(duplicates),
        "duplicates": duplicates,
    }
