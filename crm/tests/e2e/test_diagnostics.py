# pyright: basic
"""E2E tests for query, async, connection diagnostics, describe, doctor, and service-document."""
from __future__ import annotations

import json
from datetime import datetime, timedelta, timezone

import pytest

from crm.tests.e2e.coverage import covers

# Reused from the production BulkDelete path so the seed goes through the exact
# FetchXmlToQueryExpression conversion the CLI uses — the BulkDelete action's
# QuerySet accepts only QueryExpression, not raw FetchXml.
from crm.core.bulk_delete import _to_query_expression


# ── query count ──────────────────────────────────────────────────────────────


@covers("query count")
def test_query_count_contacts(cli):
    """RetrieveTotalRecordCount returns a non-negative integer for contacts."""
    r = cli(["--json", "query", "count", "contact"])
    assert r.returncode == 0, r.stderr
    env = json.loads(r.stdout)
    assert env["ok"] is True
    assert env["data"]["entity"] == "contact"
    assert isinstance(env["data"]["count"], int)
    assert env["data"]["count"] >= 0


@covers("query count")
def test_query_count_accepts_entity_set_name_and_case(cli):
    """`count contacts` (entity-set name) and a mixed-case form both resolve to the
    logical name and return the same cached count as `count contact` (#305)."""
    logical = json.loads(cli(["--json", "query", "count", "contact"]).stdout)
    assert logical["ok"] is True
    n = logical["data"]["count"]

    for name in ("contacts", "Contact", "CONTACTS"):
        r = cli(["--json", "query", "count", name])
        assert r.returncode == 0, r.stderr
        env = json.loads(r.stdout)
        assert env["ok"] is True
        # Resolved to the canonical logical name, same cached count.
        assert env["data"]["entity"] == "contact"
        assert env["data"]["count"] == n


@covers("query count")
def test_query_count_unknown_entity_errors_cleanly(cli):
    """A genuine miss errors with exit 1 and names the bad token (no false success)."""
    r = cli(["--json", "query", "count", "definitelynotanentity"], check=False)
    assert r.returncode == 1
    env = json.loads(r.stdout)
    assert env["ok"] is False
    assert "definitelynotanentity" in env["error"]


# ── query odata ───────────────────────────────────────────────────────────────


@covers("query odata")
def test_query_odata_contacts(cli):
    """OData query against contacts returns an ok envelope with a bare row array."""
    r = cli(["--json", "query", "odata", "contacts", "--top", "2"])
    assert r.returncode == 0, r.stderr
    env = json.loads(r.stdout)
    assert env["ok"] is True
    # data is a bare array of rows (ADR 0008); no OData envelope, paging in meta.
    assert isinstance(env["data"], list)


# ── query saved ───────────────────────────────────────────────────────────────


@covers("query saved")
def test_query_saved_discovers_and_executes(backend, cli):
    """Fetch a saved query GUID for contacts from the backend, then execute it via the CLI."""
    # querytype=0 means "Public View"; returnedtypecode 2 is contact.
    # Use separate filters to avoid OData type-mismatch (returnedtypecode may be
    # Edm.Int32 or Edm.String depending on server version).
    resp = backend.get(
        "savedqueries",
        params={
            "$select": "savedqueryid,name,returnedtypecode",
            "$filter": "querytype eq 0",
            "$top": "50",
        },
    )
    rows = (resp or {}).get("value", [])
    # Filter client-side for returnedtypecode == 2 (contact) to avoid server-side type issues.
    contact_views = [r for r in rows if str(r.get("returnedtypecode", "")).strip() in ("2", "2.0")]
    if not contact_views:
        pytest.skip("no savedquery for contacts found on this org")
    sqid = contact_views[0]["savedqueryid"]

    r = cli(["--json", "query", "saved", "contacts", sqid])
    assert r.returncode == 0, r.stderr
    env = json.loads(r.stdout)
    assert env["ok"] is True
    # data is a bare array of rows (ADR 0008).
    assert isinstance(env["data"], list)


# ── query user ────────────────────────────────────────────────────────────────


@covers("query user")
def test_query_user_discovers_and_executes(backend, cli):
    """Fetch a user query GUID if any exist, then execute it via the CLI.
    Skips at runtime when the org has no userqueries (fresh/test orgs often don't)."""
    resp = backend.get(
        "userqueries",
        params={
            "$select": "userqueryid,name",
            "$top": "1",
        },
    )
    rows = (resp or {}).get("value", [])
    if not rows:
        pytest.skip("no userquery found on this org")
    uqid = rows[0]["userqueryid"]

    # userqueries can target any entity — derive entity set from returnedtypecode
    # or just try contacts (returnedtypecode=2). If the org has any user query at
    # all, use whichever entity set matches via the first row's returnedtypecode.
    detail = backend.get(
        f"userqueries({uqid})",
        params={"$select": "userqueryid,returnedtypecode"},
    )
    code = detail.get("returnedtypecode")
    # Map common type codes to entity sets; fall back to contacts.
    _CODE_MAP = {1: "accounts", 2: "contacts", 3: "opportunities"}
    entity_set = _CODE_MAP.get(code, "contacts")

    r = cli(["--json", "query", "user", entity_set, uqid])
    assert r.returncode == 0, r.stderr
    env = json.loads(r.stdout)
    assert env["ok"] is True
    # data is a bare array of rows (ADR 0008).
    assert isinstance(env["data"], list)


# ── async list ────────────────────────────────────────────────────────────────


@covers("async list")
def test_async_list_returns_list(cli):
    """async list returns an ok envelope whose data is a list."""
    r = cli(["--json", "async", "list", "--top", "5"])
    assert r.returncode == 0, r.stderr
    env = json.loads(r.stdout)
    assert env["ok"] is True
    assert isinstance(env["data"], list)


@covers("async list")
def test_async_list_order_by(cli):
    """async list --order-by works."""
    r = cli(["--json", "async", "list", "--top", "2", "--order-by", "completedon desc"])
    assert r.returncode == 0, r.stderr
    env = json.loads(r.stdout)
    assert env["ok"] is True
    assert isinstance(env["data"], list)


@covers("async list")
def test_async_list_filter(cli):
    """async list --filter works."""
    r = cli(["--json", "async", "list", "--top", "2", "--filter", "statuscode eq 30"])
    assert r.returncode == 0, r.stderr
    env = json.loads(r.stdout)
    assert env["ok"] is True
    assert isinstance(env["data"], list)


# ── async get ─────────────────────────────────────────────────────────────────


@covers("async get")
def test_async_get_first_operation(cli):
    """async get fetches a single asyncoperation row by GUID, or skips if none exist."""
    r = cli(["--json", "async", "list", "--top", "1"])
    assert r.returncode == 0, r.stderr
    env = json.loads(r.stdout)
    rows = env.get("data", [])
    if not rows:
        pytest.skip("no async operation rows available on this org")
    op_id = rows[0]["asyncoperationid"]

    r2 = cli(["--json", "async", "get", op_id])
    assert r2.returncode == 0, r2.stderr
    env2 = json.loads(r2.stdout)
    assert env2["ok"] is True
    assert env2["data"]["asyncoperationid"] == op_id


# ── async cancel / solution job-cancel ──────────────────────────────────────────
#
# Both verbs call the same cancel_async_operation (PATCH any asyncoperation in
# state {Ready, Suspended} to Completed/Cancelled); `solution job-cancel` is a
# literal alias of `async cancel`, so both are exercised here against the same
# seed. To cancel safely we seed a *future-dated, match-nothing* BulkDelete: a
# self-owned async operation the service queues but never runs (its start date is
# years out) and that would delete nothing even if it did (the filter matches no
# rows). So it sits in a cancellable state, deletes nothing, and needs no
# dedicated-org setup — cancelling it before its start time is deterministic on
# any cloud/on-prem target.


def _seed_future_bulkdelete(backend, unique):
    """Submit a future-dated BulkDelete matching no rows; return its asyncoperationid."""
    nomatch = f"E2ENoMatch{unique}"
    # Prove the filter matches zero rows up front, so even a (defensive) run would
    # delete nothing — the deterministic half of the no-pollution guarantee.
    existing = backend.get(
        "contacts",
        params={"$filter": f"lastname eq '{nomatch}'", "$select": "contactid", "$top": "1"},
    )
    assert (existing.get("value") or []) == [], f"seed filter unexpectedly matched rows: {existing}"
    fetch = (
        '<fetch><entity name="contact"><attribute name="contactid"/>'
        f'<filter><condition attribute="lastname" operator="eq" value="{nomatch}"/>'
        "</filter></entity></fetch>"
    )
    query = _to_query_expression(backend, fetch)
    # Start a decade out so the async service never picks the job up mid-test.
    start = (datetime.now(timezone.utc) + timedelta(days=3650)).strftime("%Y-%m-%dT%H:%M:%SZ")
    resp = backend.post("BulkDelete", json_body={
        "QuerySet": [query],
        "JobName": f"E2E async-cancel probe {unique}",
        "SendEmailNotification": False,
        "ToRecipients": [],
        "CCRecipients": [],
        "RecurrencePattern": "",
        "StartDateTime": start,
    })
    job_id = resp.get("JobId") if isinstance(resp, dict) else None
    assert job_id, f"BulkDelete returned no JobId: {resp}"
    return str(job_id)


@covers("async cancel", "solution job-cancel")
@pytest.mark.parametrize("verb", [["async", "cancel"], ["solution", "job-cancel"]])
def test_cancel_future_bulkdelete(backend, cli, unique, verb):
    """`async cancel` and its `solution job-cancel` alias cancel a seeded, never-run
    BulkDelete; the op flips to Completed/Cancelled and deleted nothing."""
    job_id = _seed_future_bulkdelete(backend, unique)
    try:
        # Pre-state: queued in a cancellable state (0=Ready / 1=Suspended), proving
        # the job has not run, so nothing was deleted.
        pre = backend.get(
            f"asyncoperations({job_id})", params={"$select": "statecode,statuscode"}
        )
        assert pre.get("statecode") in (0, 1), f"seeded op not in a cancellable state: {pre}"

        r = cli(["--json", *verb, job_id, "--yes"])
        assert r.returncode == 0, (
            f"{' '.join(verb)} failed:\n{r.stderr}\nstdout: {r.stdout}"
        )
        env = json.loads(r.stdout)
        assert env["ok"], env
        assert env["data"] == {"cancelled": True, "id": job_id}, env

        # statecode=3 (Completed) + statuscode=32 (Cancelled) is the cancelled envelope.
        post = backend.get(
            f"asyncoperations({job_id})", params={"$select": "statecode,statuscode"}
        )
        assert post.get("statecode") == 3 and post.get("statuscode") == 32, (
            f"operation did not move to cancelled: {post}"
        )
    finally:
        try:
            backend.delete(f"asyncoperations({job_id})")
        except Exception:
            pass


# ── connection doctor ─────────────────────────────────────────────────────────


@covers("connection doctor")
def test_connection_doctor_ok(cli):
    """connection doctor passes all checks and returns a valid JSON envelope."""
    r = cli(["--json", "connection", "doctor"])
    assert r.returncode == 0, r.stderr
    env = json.loads(r.stdout)
    assert env["ok"] is True
    assert isinstance(env["data"]["checks"], list)
    assert env["data"]["checks"], "doctor returned no checks"


# ── connection test ────────────────────────────────────────────────────────────


@covers("connection test")
def test_connection_test_returns_api_info(cli):
    """connection test returns an ok envelope with api_base."""
    r = cli(["--json", "connection", "test"])
    assert r.returncode == 0, r.stderr
    env = json.loads(r.stdout)
    assert env["ok"] is True
    assert "api_base" in env["data"]


# ── describe ──────────────────────────────────────────────────────────────────


@covers("describe")
def test_describe_whole_tree(cli):
    """describe emits an ok envelope with a non-empty commands list."""
    r = cli(["--json", "describe"])
    assert r.returncode == 0, r.stderr
    env = json.loads(r.stdout)
    assert env["ok"] is True
    assert isinstance(env["data"]["commands"], list)
    assert len(env["data"]["commands"]) > 10


# ── doctor (top-level alias) ──────────────────────────────────────────────────


@covers("doctor")
def test_doctor_top_level_ok(cli):
    """Top-level crm doctor alias passes and returns the same check list."""
    r = cli(["--json", "doctor"])
    assert r.returncode == 0, r.stderr
    env = json.loads(r.stdout)
    assert env["ok"] is True
    assert isinstance(env["data"]["checks"], list)


# ── service-document ──────────────────────────────────────────────────────────


@covers("service-document")
def test_service_document_lists_entity_sets(cli):
    """service-document returns an ok envelope with a non-empty bare set list."""
    r = cli(["--json", "service-document"])
    assert r.returncode == 0, r.stderr
    env = json.loads(r.stdout)
    assert env["ok"] is True
    # data is the bare array of entity sets (ADR 0008); count is in meta.
    sets = env["data"]
    assert isinstance(sets, list)
    assert len(sets) > 0
