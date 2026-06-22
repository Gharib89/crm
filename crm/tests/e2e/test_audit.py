# pyright: basic
"""E2E tests for the audit group (server-side audit change history).

`audit history` (RetrieveRecordChangeHistory) returns a well-formed (empty)
AuditDetailCollection even when auditing is off, so its live call + parameter-alias
path are covered without depending on audit data.

`audit detail` (RetrieveAuditDetails) needs a real audit row to decode, so its test
generates one inline (create + audited update) and runs only where auditing is
enabled — it skips with setup instructions otherwise (e.g. the general cloud org,
which has org-level auditing off). See the test's docstring for why the auditid is
resolved from the `audits` table rather than from `audit history`.
"""
from __future__ import annotations

import json
import time

import pytest

from crm.tests.e2e.coverage import covers


def _safe(backend, path: str) -> None:
    """Best-effort teardown — never raises so finalizers don't mask test results."""
    try:
        backend.delete(path)
    except Exception:
        pass


@covers("audit history")
def test_audit_history_returns_collection(cli):
    """`audit history` calls the unbound RetrieveRecordChangeHistory function for a
    real record (the current user) and returns an AuditDetailCollection envelope.

    The target id comes from WhoAmI so no org-specific GUID is embedded. systemuser
    is auditable; with auditing off the collection is simply empty — the assertion
    is on the response shape, not on the presence of audit rows.
    """
    who = json.loads(cli(["--json", "action", "function", "WhoAmI"]).stdout)
    user_id = who["data"]["UserId"]
    result = cli(["--json", "audit", "history", "systemusers", user_id])
    data = json.loads(result.stdout)
    assert data["ok"] is True, f"audit history failed: {data}"
    coll = data["data"].get("AuditDetailCollection")
    assert isinstance(coll, dict), f"AuditDetailCollection missing: {data['data']}"
    assert isinstance(coll.get("AuditDetails"), list), (
        f"AuditDetails list missing from collection: {coll}"
    )


@pytest.mark.requires_cloud
@covers("audit detail")
def test_audit_detail_decodes_attribute_change(backend, cli, unique, request):
    """`audit detail <auditid>` decodes an AttributeAuditDetail's old→new values
    for a real audited update, generated inline.

    Creates an account with telephone1 set, updates telephone1 (an Update audit
    row), then decodes that row via the CLI and asserts OldValue/NewValue.

    The auditid is resolved from the `audits` table directly, NOT from `audit
    history`: the Web API's RetrieveRecordChangeHistory (which `audit history`
    wraps) deliberately omits the AuditRecord navigation property, so its returned
    AuditDetails carry no auditid to feed `audit detail`. The read-only `audits`
    table is the only Web API source for the id. (Confirmed against the Dataverse
    auditing docs — this is a documented Web API limitation, not an org quirk.)

    Skips with setup instructions when no audit row is produced — the signal that
    org/entity auditing is disabled on the target (e.g. the general cloud org).
    """
    old_phone, new_phone = "1000000000", "2000000000"

    acct = backend.post(
        "accounts",
        json_body={"name": f"E2E-Audit-{unique}", "telephone1": old_phone},
        extra_headers={"Prefer": "return=representation"},
    )
    acct_id = acct["accountid"]
    request.addfinalizer(lambda: _safe(backend, f"accounts({acct_id})"))

    # Mutate an audited column to generate an Update audit row (operation 2).
    backend.patch(f"accounts({acct_id})", json_body={"telephone1": new_phone})

    # Resolve the newest Update audit row's id from the `audits` table. Audit
    # writes can lag the mutation by a moment, so poll briefly before concluding
    # auditing is off — a single immediate read could otherwise false-skip on an
    # org that *is* audited.
    audit_id = None
    for _ in range(6):
        rows = backend.get(
            "audits",
            params={
                "$select": "auditid",
                "$filter": f"_objectid_value eq {acct_id} and operation eq 2",
                "$orderby": "createdon desc",
                "$top": "1",
            },
        )
        audit_rows = rows.get("value", []) if isinstance(rows, dict) else []
        if audit_rows:
            audit_id = audit_rows[0]["auditid"]
            break
        time.sleep(2)
    if audit_id is None:
        pytest.skip(
            "no audit row was generated — the target has auditing disabled. "
            "Enable org-level auditing (organization.isauditenabled=true) and turn "
            "on auditing for the account entity, then re-run (see ADR 0012 / #503)."
        )

    result = cli(["--json", "audit", "detail", audit_id])
    env = json.loads(result.stdout)
    assert env["ok"] is True, f"audit detail failed: {env}"
    detail = env["data"]["AuditDetail"]
    assert detail["AuditDetailType"] == "AttributeAuditDetail", detail
    assert detail["OldValue"].get("telephone1") == old_phone, detail
    assert detail["NewValue"].get("telephone1") == new_phone, detail
