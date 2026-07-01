# pyright: basic
"""E2E tests for query, async, connection diagnostics, describe, doctor, and service-document."""
from __future__ import annotations

import json
from datetime import datetime, timedelta, timezone

import pytest

from crm.core.views import build_fetchxml, build_layoutxml
from crm.tests.e2e.conftest import _safe_delete
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


@covers("query odata")
def test_query_odata_default_page_with_more_rows_is_self_describing(cli):
    """A default (non-`--all`) read that leaves a live `@odata.nextLink` cursor
    sets `meta.has_more: true` and warns to use `--all`/`--max-records` (#626,
    #625). `--page-size 1` forces a cursor cheaply — no need for a huge table —
    as long as contacts has at least 2 rows on this org."""
    count_env = json.loads(cli(["--json", "query", "count", "contact"]).stdout)
    if count_env["data"]["count"] < 2:
        pytest.skip("contacts has fewer than 2 rows on this org; cannot force a cursor")

    r = cli(["--json", "query", "odata", "contacts", "--select", "fullname",
             "--page-size", "1"])
    assert r.returncode == 0, r.stderr
    env = json.loads(r.stdout)
    assert env["ok"] is True
    assert env["meta"].get("has_more") is True
    assert any("more rows exist" in w for w in env["meta"]["warnings"])


@covers("query odata")
def test_query_odata_count_clamped_at_server_ceiling_warns(cli):
    """A `--count` that lands exactly on the server's 5000-row standard-table
    ceiling alongside a live cursor is flagged as a clamped lower bound, not an
    exact total (#626). Needs a table with >5000 rows reachable on this org;
    skips with instructions when none is known to qualify."""
    for entity_set, logical in (("contacts", "contact"), ("accounts", "account")):
        count_env = json.loads(cli(["--json", "query", "count", logical]).stdout)
        if count_env["data"]["count"] > 5000:
            # No `--top`: `$top` is a hard limit that suppresses `@odata.nextLink`,
            # which the clamp signal keys on. `--page-size 1` keeps the page cheap
            # while still leaving a live cursor.
            r = cli(["--json", "query", "odata", entity_set, "--count",
                      "--page-size", "1"])
            assert r.returncode == 0, r.stderr
            env = json.loads(r.stdout)
            assert env["ok"] is True
            if env["meta"].get("count") == 5000 and env["meta"].get("has_more"):
                assert any("clamped at 5000" in w for w in env["meta"]["warnings"])
                return
    pytest.skip(
        "no reachable table with >5000 rows on this org to exercise the "
        "server $count clamp; seed one (e.g. bulk-import >5000 contacts) to "
        "run this assertion live"
    )


# ── query saved / query user (self-seeded) ─────────────────────────────────────
#
# Both verbs need a stored query to execute. A bare test org has none, so rather
# than discover-or-skip — which left the verb "covered" but exercised on no rows,
# and whose returnedtypecode filter was itself fragile — each test self-seeds a
# throwaway contact view, runs the verb, and a finalizer removes it. Runs on any
# target. `returnedtypecode` is POSTed as the entity LOGICAL NAME (the shape
# crm/core/views.py uses); the server may echo it back as the int OTC, but we
# never read it back — we seed for contact and query `contacts` directly.

_SEED_COLUMNS = [("fullname", 200)]
_CONTACT_OTC = 2  # contact ObjectTypeCode — used only in layoutxml's grid object=


def _seed_savedquery(backend, suffix: str) -> str:
    """Create a throwaway public savedquery for contact; return its id."""
    body = {
        "name": f"E2E SavedQuery {suffix}",
        "returnedtypecode": "contact",
        "querytype": 0,  # public view
        "fetchxml": build_fetchxml("contact", _SEED_COLUMNS, None, False),
        "layoutxml": build_layoutxml("contact", _CONTACT_OTC, _SEED_COLUMNS),
    }
    created = backend.post(
        "savedqueries", json_body=body,
        extra_headers={"Prefer": "return=representation"},
    )
    return str(created["savedqueryid"])


def _seed_userquery(backend, suffix: str) -> str:
    """Create a throwaway userquery for contact (owned by the caller); return its id."""
    body = {
        "name": f"E2E UserQuery {suffix}",
        "returnedtypecode": "contact",
        "querytype": 0,  # required on userquery create ("The query type is missing.")
        "fetchxml": build_fetchxml("contact", _SEED_COLUMNS, None, False),
        "layoutxml": build_layoutxml("contact", _CONTACT_OTC, _SEED_COLUMNS),
    }
    created = backend.post(
        "userqueries", json_body=body,
        extra_headers={"Prefer": "return=representation"},
    )
    return str(created["userqueryid"])


@covers("query saved")
def test_query_saved_seeds_and_executes(backend, cli, unique, request):
    """Self-seed a contact public view (savedquery), execute it via the CLI, then
    remove it. Runs on any org — no pre-existing view required."""
    sqid = _seed_savedquery(backend, unique)
    request.addfinalizer(lambda: _safe_delete(backend, f"savedqueries({sqid})"))

    r = cli(["--json", "query", "saved", "contacts", sqid])
    assert r.returncode == 0, r.stderr
    env = json.loads(r.stdout)
    assert env["ok"] is True
    # data is a bare array of rows (ADR 0008).
    assert isinstance(env["data"], list)


@covers("query user")
def test_query_user_seeds_and_executes(backend, cli, unique, request):
    """Self-seed a contact userquery owned by the caller, execute it via the CLI,
    then remove it. Runs on any org — no pre-existing view required."""
    uqid = _seed_userquery(backend, unique)
    request.addfinalizer(lambda: _safe_delete(backend, f"userqueries({uqid})"))

    r = cli(["--json", "query", "user", "contacts", uqid])
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
