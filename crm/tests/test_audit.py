# pyright: basic
"""`audit history` / `audit detail` — server-side audit change history (issue 363).

These wrap the OData functions RetrieveRecordChangeHistory (unbound, Target +
PagingInfo parameter aliases) and RetrieveAuditDetails (bound to an audit row),
which `action function`'s inline parameter encoding cannot express. Distinct
from the local `session audit` journal of this CLI's own mutations.
"""
from __future__ import annotations

import json

from click.testing import CliRunner

from crm.cli import cli

RID = "11111111-1111-1111-1111-111111111111"
AID = "22222222-2222-2222-2222-222222222222"


def _run(inject_backend, make_fake_backend, args, *, responses=None):
    backend = inject_backend(make_fake_backend(responses=responses))
    result = CliRunner().invoke(cli, ["--json", "audit", *args])
    return result, backend


def test_history_builds_unbound_function_with_target_and_paging(inject_backend, make_fake_backend):
    """`audit history <set> <id>` issues the unbound RetrieveRecordChangeHistory
    GET with the Target EntityReference and PagingInfo passed as parameter aliases."""
    result, backend = _run(inject_backend, make_fake_backend, ["history", "accounts", RID])
    assert result.exit_code == 0, result.output
    verb, path, kwargs = backend.calls[-1]
    assert verb == "get"
    assert path == "RetrieveRecordChangeHistory(Target=@target,PagingInfo=@paginginfo)"
    params = kwargs["params"]
    assert json.loads(params["@target"]) == {"@odata.id": f"accounts({RID})"}
    paging = json.loads(params["@paginginfo"])
    assert paging["PageNumber"] == 1
    assert paging["ReturnTotalRecordCount"] is True


def test_history_paging_options_flow_into_paginginfo(inject_backend, make_fake_backend):
    """--page/--count/--paging-cookie populate the PagingInfo parameter."""
    result, backend = _run(
        inject_backend, make_fake_backend,
        ["history", "accounts", RID, "--page", "2", "--count", "10", "--paging-cookie", "ck"],
    )
    assert result.exit_code == 0, result.output
    paging = json.loads(backend.calls[-1][2]["params"]["@paginginfo"])
    assert paging["PageNumber"] == 2
    assert paging["Count"] == 10
    assert paging["PagingCookie"] == "ck"


def test_history_omits_paging_cookie_when_absent(inject_backend, make_fake_backend):
    """No --paging-cookie → PagingInfo carries no PagingCookie key (first page)."""
    result, backend = _run(inject_backend, make_fake_backend, ["history", "accounts", RID])
    assert result.exit_code == 0, result.output
    paging = json.loads(backend.calls[-1][2]["params"]["@paginginfo"])
    assert "PagingCookie" not in paging


def test_detail_builds_bound_function_path(inject_backend, make_fake_backend):
    """`audit detail <auditid>` GETs RetrieveAuditDetails bound to the audit row."""
    result, backend = _run(inject_backend, make_fake_backend, ["detail", AID])
    assert result.exit_code == 0, result.output
    verb, path, _ = backend.calls[-1]
    assert verb == "get"
    assert path == f"audits({AID})/Microsoft.Dynamics.CRM.RetrieveAuditDetails"


def test_detail_decodes_audit_detail_type(inject_backend, make_fake_backend):
    """The AuditDetail's @odata.type discriminator is surfaced as AuditDetailType,
    which survives emit's @odata.* strip (the type would otherwise be erased)."""
    resp = {
        "@odata.context": "…/$metadata#…RetrieveAuditDetailsResponse",
        "AuditDetail": {
            "@odata.type": "#Microsoft.Dynamics.CRM.AttributeAuditDetail",
            "OldValue": {"@odata.type": "#Microsoft.Dynamics.CRM.account"},
        },
    }
    result, _ = _run(inject_backend, make_fake_backend, ["detail", AID], responses={"get": resp})
    assert result.exit_code == 0, result.output
    detail = json.loads(result.output)["data"]["AuditDetail"]
    assert detail["AuditDetailType"] == "AttributeAuditDetail"
    # @odata.* keys are stripped by emit; the decoded type is what remains.
    assert "@odata.type" not in detail
    # A plain entity type (#…account) is not an AuditDetail subtype → not labelled.
    assert "AuditDetailType" not in detail["OldValue"]


def test_history_decodes_audit_detail_types_in_collection(inject_backend, make_fake_backend):
    """Each detail in the AuditDetailCollection is decoded, paging fields preserved."""
    resp = {
        "@odata.context": "…RetrieveRecordChangeHistoryResponse",
        "AuditDetailCollection": {
            "MoreRecords": False,
            "TotalRecordCount": 1,
            "AuditDetails": [
                {"@odata.type": "#Microsoft.Dynamics.CRM.AttributeAuditDetail"},
            ],
        },
    }
    result, _ = _run(
        inject_backend, make_fake_backend, ["history", "accounts", RID], responses={"get": resp}
    )
    assert result.exit_code == 0, result.output
    coll = json.loads(result.output)["data"]["AuditDetailCollection"]
    assert coll["AuditDetails"][0]["AuditDetailType"] == "AttributeAuditDetail"
    assert coll["TotalRecordCount"] == 1
