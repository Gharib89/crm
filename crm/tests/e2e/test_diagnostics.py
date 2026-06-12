# pyright: basic
"""E2E tests for query, async, connection diagnostics, describe, doctor, and service-document."""
from __future__ import annotations

import json

import pytest

from crm.tests.e2e.coverage import covers


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


# ── query odata ───────────────────────────────────────────────────────────────


@covers("query odata")
def test_query_odata_contacts(cli):
    """OData query against contacts returns an ok envelope wrapping the OData collection."""
    r = cli(["--json", "query", "odata", "contacts", "--top", "2"])
    assert r.returncode == 0, r.stderr
    env = json.loads(r.stdout)
    assert env["ok"] is True
    # The CLI emits the raw OData envelope dict ({"value": [...], ...})
    assert isinstance(env["data"], dict)
    assert "value" in env["data"]
    assert isinstance(env["data"]["value"], list)


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
    # The CLI emits the raw OData envelope dict ({"value": [...], ...})
    assert isinstance(env["data"], dict)
    assert "value" in env["data"]


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
    # The CLI emits the raw OData envelope dict ({"value": [...], ...})
    assert isinstance(env["data"], dict)
    assert "value" in env["data"]


# ── async list ────────────────────────────────────────────────────────────────


@covers("async list")
def test_async_list_returns_list(cli):
    """async list returns an ok envelope whose data is a list."""
    r = cli(["--json", "async", "list", "--top", "5"])
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
    """service-document returns an ok envelope with a non-empty value list."""
    r = cli(["--json", "service-document"])
    assert r.returncode == 0, r.stderr
    env = json.loads(r.stdout)
    assert env["ok"] is True
    sets = env["data"].get("value", [])
    assert isinstance(sets, list)
    assert len(sets) > 0
