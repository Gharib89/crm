# pyright: basic
"""E2E tests for the audit group (server-side audit change history).

`audit detail` is in E2E_SKIP: it needs a pre-existing audit row, and the cloud
test org has org-level auditing disabled (audits table empty), so no auditid is
available to decode. `audit history` is exercised below — RetrieveRecordChangeHistory
returns a well-formed (empty) AuditDetailCollection even when auditing is off, so
the live call and parameter-alias path are covered without depending on audit data.
"""
from __future__ import annotations

import json

from crm.tests.e2e.coverage import covers


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
