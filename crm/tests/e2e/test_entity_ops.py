# pyright: basic
"""E2E tests for entity associate/disassociate, set-lookup/clear-lookup,
clone, upsert, and children verbs."""
from __future__ import annotations

import json
import uuid

from crm.tests.e2e.coverage import covers


def _safe(backend, path: str) -> None:
    """Best-effort teardown — never raises so finalizers don't mask test results."""
    try:
        backend.delete(path)
    except Exception:
        pass


# ── entity upsert ────────────────────────────────────────────────────────────


@covers("entity upsert")
def test_entity_upsert(cli, unique, request):
    """Upsert creates then updates a contact by explicit GUID."""
    cid = str(uuid.uuid4())
    request.addfinalizer(lambda: _safe_cli_delete(cli, cid))

    # First call — creates (no existing record for this GUID)
    body = json.dumps({"firstname": "UpsertNew", "lastname": f"Test-{unique}"})
    result = cli(["--json", "entity", "upsert", "contacts", cid, "--data", body])
    assert result.returncode == 0, result.stderr
    env = json.loads(result.stdout)
    assert env["ok"], env

    # Second call — updates (record now exists)
    body2 = json.dumps({"firstname": "UpsertUpdated"})
    result2 = cli(["--json", "entity", "upsert", "contacts", cid, "--data", body2])
    assert result2.returncode == 0, result2.stderr
    assert json.loads(result2.stdout)["ok"]


def _safe_cli_delete(cli, cid: str) -> None:
    cli(["--json", "entity", "delete", "contacts", cid, "--yes"], check=False)


# ── entity set-lookup + entity clear-lookup ──────────────────────────────────


@covers("entity set-lookup", "entity clear-lookup")
def test_set_and_clear_lookup(backend, cli, unique, request):
    """Set a contact's parentcustomerid lookup to an account, then clear it."""
    # Create a throwaway account
    acct = backend.post(
        "accounts",
        json_body={"name": f"E2E-Acct-{unique}"},
        extra_headers={"Prefer": "return=representation"},
    )
    acct_id = acct["accountid"]
    request.addfinalizer(lambda: _safe(backend, f"accounts({acct_id})"))

    # Create a throwaway contact
    ctct = backend.post(
        "contacts",
        json_body={"firstname": "E2E", "lastname": f"Lookup-{unique}"},
        extra_headers={"Prefer": "return=representation"},
    )
    ctct_id = ctct["contactid"]
    request.addfinalizer(lambda: _safe(backend, f"contacts({ctct_id})"))

    # set-lookup: wire contact.parentcustomerid → account.
    # parentcustomerid is a polymorphic Customer type; the typed single-valued
    # nav property for binding to an account is parentcustomerid_account.
    set_res = cli([
        "--json", "entity", "set-lookup",
        "contacts", ctct_id,
        "parentcustomerid_account",  # typed nav for Customer→account
        "accounts", acct_id,
    ])
    assert set_res.returncode == 0, set_res.stderr
    env = json.loads(set_res.stdout)
    assert env["ok"], env

    # Verify the lookup is set
    got = backend.get(f"contacts({ctct_id})", params={"$select": "_parentcustomerid_value"})
    assert got.get("_parentcustomerid_value") == acct_id

    # clear-lookup: remove it via the typed nav property
    clr_res = cli([
        "--json", "entity", "clear-lookup",
        "contacts", ctct_id,
        "parentcustomerid_account",
    ])
    assert clr_res.returncode == 0, clr_res.stderr
    assert json.loads(clr_res.stdout)["ok"]

    # Verify the lookup is gone
    got2 = backend.get(f"contacts({ctct_id})", params={"$select": "_parentcustomerid_value"})
    assert got2.get("_parentcustomerid_value") is None


# ── entity associate + entity disassociate ────────────────────────────────────


@covers("entity associate", "entity disassociate")
def test_associate_and_disassociate(backend, cli, unique, request):
    """Associate a contact to an account via contact_customer_accounts (1:N from account),
    then disassociate it.  The nav property on the account side is `contact_customer_accounts`;
    this is a system 1:N where account is the parent and contact references it via
    parentcustomerid.  We drive it through the associate/disassociate CLI verbs."""
    # Create throwaway account + contact
    acct = backend.post(
        "accounts",
        json_body={"name": f"E2E-AssocAcct-{unique}"},
        extra_headers={"Prefer": "return=representation"},
    )
    acct_id = acct["accountid"]
    request.addfinalizer(lambda: _safe(backend, f"accounts({acct_id})"))

    ctct = backend.post(
        "contacts",
        json_body={"firstname": "E2E", "lastname": f"Assoc-{unique}"},
        extra_headers={"Prefer": "return=representation"},
    )
    ctct_id = ctct["contactid"]
    request.addfinalizer(lambda: _safe(backend, f"contacts({ctct_id})"))

    # associate: POST accounts(<id>)/contact_customer_accounts/$ref → contacts(<id>)
    assoc_res = cli([
        "--json", "entity", "associate",
        "accounts", acct_id,
        "contact_customer_accounts",   # collection-valued nav property on account
        "contacts", ctct_id,
    ])
    assert assoc_res.returncode == 0, assoc_res.stderr
    env = json.loads(assoc_res.stdout)
    assert env["ok"], env

    # Verify the contact's parentcustomerid now points to the account
    got = backend.get(f"contacts({ctct_id})", params={"$select": "_parentcustomerid_value"})
    assert got.get("_parentcustomerid_value") == acct_id

    # disassociate: DELETE accounts(<id>)/contact_customer_accounts/$ref?$id=contacts(<id>)
    disassoc_res = cli([
        "--json", "entity", "disassociate",
        "accounts", acct_id,
        "contact_customer_accounts",
        "--related-set", "contacts",
        "--related-id", ctct_id,
    ])
    assert disassoc_res.returncode == 0, disassoc_res.stderr
    assert json.loads(disassoc_res.stdout)["ok"]

    # Verify the lookup is cleared
    got2 = backend.get(f"contacts({ctct_id})", params={"$select": "_parentcustomerid_value"})
    assert got2.get("_parentcustomerid_value") is None


# ── entity clone ─────────────────────────────────────────────────────────────


@covers("entity clone")
def test_entity_clone(backend, cli, unique, request):
    """Clone a contact — assert a new distinct id is returned, then clean both up."""
    ctct = backend.post(
        "contacts",
        json_body={"firstname": "E2E", "lastname": f"Clone-{unique}"},
        extra_headers={"Prefer": "return=representation"},
    )
    src_id = ctct["contactid"]
    request.addfinalizer(lambda: _safe(backend, f"contacts({src_id})"))

    clone_res = cli([
        "--json", "entity", "clone", "contacts", src_id,
    ])
    assert clone_res.returncode == 0, clone_res.stderr
    env = json.loads(clone_res.stdout)
    assert env["ok"], env

    # The clone returns either the full record (with contactid) or {"id": "<guid>"}
    data = env["data"]
    new_id = data.get("contactid") or data.get("id")
    assert new_id, f"no id in clone response: {data}"
    assert new_id != src_id
    request.addfinalizer(lambda: _safe(backend, f"contacts({new_id})"))


# ── entity children ───────────────────────────────────────────────────────────


@covers("entity children")
def test_entity_children(backend, cli, unique, request):
    """Count child contacts under an account via entity children.

    Creates an account and one contact referencing it, then verifies that
    children returns a list with at least the contact_customer_accounts row
    showing count >= 1.
    """
    acct = backend.post(
        "accounts",
        json_body={"name": f"E2E-ChildAcct-{unique}"},
        extra_headers={"Prefer": "return=representation"},
    )
    acct_id = acct["accountid"]
    request.addfinalizer(lambda: _safe(backend, f"accounts({acct_id})"))

    ctct = backend.post(
        "contacts",
        json_body={
            "firstname": "E2E",
            "lastname": f"Child-{unique}",
            "parentcustomerid_account@odata.bind": f"/accounts({acct_id})",
        },
        extra_headers={"Prefer": "return=representation"},
    )
    ctct_id = ctct["contactid"]
    request.addfinalizer(lambda: _safe(backend, f"contacts({ctct_id})"))

    children_res = cli([
        "--json", "entity", "children", "accounts", acct_id,
        "--non-empty",
    ])
    assert children_res.returncode == 0, children_res.stderr
    env = json.loads(children_res.stdout)
    assert env["ok"], env

    rows = env["data"]
    assert isinstance(rows, list), rows

    # At least one row should correspond to contacts referencing this account
    contact_rows = [r for r in rows if r.get("entity") == "contact"]
    assert contact_rows, (
        f"No 'contact' row in children output (all rows: {rows[:5]})"
    )
    assert any(r.get("count", 0) >= 1 for r in contact_rows), contact_rows
