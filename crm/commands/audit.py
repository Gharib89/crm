"""Server-side audit change-history commands.

Wraps the OData audit functions that `action function`/`action invoke` cannot
reach: RetrieveRecordChangeHistory needs a Target EntityReference and PagingInfo
passed as parameter aliases (`?@target=...&@paginginfo=...`), which the inline
`Fn(k=v)` encoding can't express. Distinct from the local `session audit`
journal, which records only this CLI's own mutations.
"""
# pyright: basic
from __future__ import annotations
import json
import click
from crm.cli import CLIContext, pass_ctx
from crm.commands._helpers import d365_errors

_CRM_NS = "#Microsoft.Dynamics.CRM."


def _decode_audit_types(obj):
    """Promote the AuditDetail subtype discriminator into a plain field.

    The Web API tags each detail with `@odata.type`
    (`#Microsoft.Dynamics.CRM.AttributeAuditDetail`, …), but the emit envelope
    strips every `@odata.*` key (ADR 0008), which would erase the one piece of
    data that tells the AuditDetail-derived types apart. Copy the short name into
    `AuditDetailType` so the decoded type survives the strip. Walks nested dicts
    and lists in place."""
    if isinstance(obj, dict):
        t = obj.get("@odata.type")
        if isinstance(t, str) and t.startswith(_CRM_NS) and t.endswith("AuditDetail"):
            obj["AuditDetailType"] = t[len(_CRM_NS):]
        for v in obj.values():
            _decode_audit_types(v)
    elif isinstance(obj, list):
        for v in obj:
            _decode_audit_types(v)
    return obj


@click.group("audit")
def audit_group():
    """Retrieve server-side audit change history (Web API audit functions)."""


@audit_group.command("history")
@click.argument("entity_set")
@click.argument("record_id")
@click.option("--page", type=int, default=1, show_default=True,
              help="1-based page number to retrieve.")
@click.option("--count", type=int, default=50, show_default=True,
              help="Page size — audit rows to return per page.")
@click.option("--paging-cookie",
              help="PagingCookie from a prior page's AuditDetailCollection, to fetch the next page.")
@pass_ctx
def audit_history(ctx: CLIContext, entity_set, record_id, page, count, paging_cookie):
    """Retrieve a record's audited data changes (RetrieveRecordChangeHistory).

    ENTITY_SET is the target entity set (e.g. 'accounts'); RECORD_ID its GUID.
    Returns an AuditDetailCollection whose MoreRecords/PagingCookie/TotalRecordCount
    drive paging — pass the returned PagingCookie via --paging-cookie for the next page.
    """
    paging: dict = {"PageNumber": page, "Count": count, "ReturnTotalRecordCount": True}
    if paging_cookie:
        paging["PagingCookie"] = paging_cookie
    params = {
        "@target": json.dumps({"@odata.id": f"{entity_set}({record_id})"}),
        "@paginginfo": json.dumps(paging),
    }
    path = "RetrieveRecordChangeHistory(Target=@target,PagingInfo=@paginginfo)"
    with d365_errors(ctx):
        result = ctx.backend().get(path, params=params)
    ctx.emit(True, data=_decode_audit_types(result or {}))


@audit_group.command("detail")
@click.argument("audit_id")
@pass_ctx
def audit_detail(ctx: CLIContext, audit_id):
    """Retrieve the decoded AuditDetail for one audit row (RetrieveAuditDetails).

    AUDIT_ID is the auditid GUID of a row in the audit table. The returned
    AuditDetail's concrete type is surfaced as AuditDetailType.
    """
    path = f"audits({audit_id})/Microsoft.Dynamics.CRM.RetrieveAuditDetails"
    with d365_errors(ctx):
        result = ctx.backend().get(path)
    ctx.emit(True, data=_decode_audit_types(result or {}))
