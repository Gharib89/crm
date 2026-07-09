# pyright: basic
"""E2E tests for dup verbs: list, create, add-condition, publish, unpublish,
check, get.

Works on both targets (duplicate detection exists on on-prem v9.1 and cloud).
The lifecycle test builds a throwaway rule on `account` (a duplicate-detection-
enabled entity on every org), publishes it (async), checks a candidate record,
then unpublishes and deletes — leaving the org as it found it.
"""
from __future__ import annotations

import json

import pytest

from crm.tests.e2e.coverage import covers


# ── dup list ──────────────────────────────────────────────────────────────────


@covers("dup list")
def test_dup_list(cli):
    """`dup list` returns a (possibly empty) collection of rule rows."""
    result = cli(["--json", "dup", "list"])
    assert result.returncode == 0, (
        f"dup list failed:\n{result.stderr}\nstdout: {result.stdout}"
    )
    env = json.loads(result.stdout)
    assert env["ok"], env
    assert isinstance(env["data"], list), env


# ── create / add-condition / publish / check / unpublish / get lifecycle ──────


@covers("dup create", "dup add-condition", "dup publish", "dup unpublish",
        "dup check", "dup get")
@pytest.mark.slow
def test_dup_lifecycle(backend, cli, request, unique, ephemeral_solution):
    """Build a rule on account, publish it, check a candidate, then clean up."""
    name = f"E2E Dup {unique}"
    created_id: list[str] = []

    def _cleanup():
        if created_id:
            rid = created_id[0]
            try:
                backend.post("UnpublishDuplicateRule", json_body={"DuplicateRuleId": rid})
            except Exception:
                pass
            try:
                backend.delete(f"duplicaterules({rid})")
            except Exception:
                pass

    request.addfinalizer(_cleanup)

    # create (unpublished)
    result = cli([
        "--json", "dup", "create", "account", "--name", name,
        "--solution", ephemeral_solution,
    ])
    assert result.returncode == 0, (
        f"dup create failed:\n{result.stderr}\nstdout: {result.stdout}"
    )
    data = json.loads(result.stdout)["data"]
    assert data.get("created") is True, data
    rid = data.get("duplicateruleid")
    assert rid, f"duplicateruleid missing: {data}"
    created_id.append(rid)

    # add-condition (name exact-match)
    result = cli([
        "--json", "dup", "add-condition", rid, "--attr", "name", "--operator", "exact",
        "--solution", ephemeral_solution,
    ])
    assert result.returncode == 0, (
        f"dup add-condition failed:\n{result.stderr}\nstdout: {result.stdout}"
    )
    assert json.loads(result.stdout)["data"].get("created") is True

    # get — rule fields + a conditions list (one entry)
    result = cli(["--json", "dup", "get", rid])
    assert result.returncode == 0, (
        f"dup get failed:\n{result.stderr}\nstdout: {result.stdout}"
    )
    got = json.loads(result.stdout)["data"]
    assert got.get("name") == name, got
    assert isinstance(got.get("conditions"), list) and got["conditions"], got

    # publish (async — wait for completion)
    result = cli(["--json", "dup", "publish", rid, "--wait"])
    assert result.returncode == 0, (
        f"dup publish failed:\n{result.stderr}\nstdout: {result.stdout}"
    )
    pub = json.loads(result.stdout)["data"]
    assert pub.get("published") is True, pub
    assert pub.get("status") == "completed", pub

    # check — a candidate account record against the now-published rule
    result = cli([
        "--json", "dup", "check", "account", "--data", '{"name": "Contoso"}',
    ])
    assert result.returncode == 0, (
        f"dup check failed:\n{result.stderr}\nstdout: {result.stdout}"
    )
    chk = json.loads(result.stdout)["data"]
    assert isinstance(chk.get("duplicates"), list), chk
    assert "count" in chk, chk

    # unpublish (synchronous)
    result = cli(["--json", "dup", "unpublish", rid])
    assert result.returncode == 0, (
        f"dup unpublish failed:\n{result.stderr}\nstdout: {result.stdout}"
    )
    assert json.loads(result.stdout)["data"].get("unpublished") is True


# ── bulk-detect (async job over a query scope) ────────────────────────────────


@covers("dup bulk-detect")
@pytest.mark.slow
def test_dup_bulk_detect(backend, cli, request, unique, ephemeral_solution):
    """Publish a name-match rule, seed a duplicate account pair, then sweep for it.

    Narrows the sweep with a FetchXML filter on the unique name so the job only
    considers the two seeded rows — fast and deterministic regardless of org data.
    """
    name = f"E2E BulkDetect {unique}"
    acct_name = f"E2E Dupe Co {unique}"
    rule_id: list[str] = []
    accounts: list[str] = []

    def _cleanup():
        for aid in accounts:
            try:
                backend.delete(f"accounts({aid})")
            except Exception:
                pass
        if rule_id:
            rid = rule_id[0]
            try:
                backend.post("UnpublishDuplicateRule", json_body={"DuplicateRuleId": rid})
            except Exception:
                pass
            try:
                backend.delete(f"duplicaterules({rid})")
            except Exception:
                pass

    request.addfinalizer(_cleanup)

    # A published exact-name rule on account.
    result = cli([
        "--json", "dup", "create", "account", "--name", name,
        "--solution", ephemeral_solution,
    ])
    assert result.returncode == 0, f"dup create failed:\n{result.stderr}"
    rid = json.loads(result.stdout)["data"]["duplicateruleid"]
    rule_id.append(rid)
    result = cli([
        "--json", "dup", "add-condition", rid, "--attr", "name", "--operator", "exact",
        "--solution", ephemeral_solution,
    ])
    assert result.returncode == 0, f"dup add-condition failed:\n{result.stderr}"
    result = cli(["--json", "dup", "publish", rid, "--wait"])
    assert result.returncode == 0, f"dup publish failed:\n{result.stderr}"

    # Seed two accounts sharing the same name — a duplicate pair.
    for _ in range(2):
        row = backend.post("accounts", json_body={"name": acct_name})
        aid = row.get("_entity_id") if isinstance(row, dict) else None
        if aid:
            accounts.append(str(aid))
    assert len(accounts) == 2, f"failed to seed duplicate pair: {accounts}"

    # Sweep, narrowed to just the seeded name so the job stays small/deterministic.
    fetch = (
        f'<fetch><entity name="account"><attribute name="accountid"/>'
        f'<filter><condition attribute="name" operator="eq" value="{acct_name}"/></filter>'
        "</entity></fetch>"
    )
    result = cli([
        "--json", "dup", "bulk-detect", "account", "--fetchxml", fetch, "--wait",
    ])
    assert result.returncode == 0, (
        f"dup bulk-detect failed:\n{result.stderr}\nstdout: {result.stdout}"
    )
    data = json.loads(result.stdout)["data"]
    assert data.get("status") == "completed", data
    assert isinstance(data.get("duplicates"), list), data
    # Both seeded rows are flagged, each as a base record under the job. (The async
    # job does NOT populate the matched-counterpart lookup `_duplicaterecordid_value`
    # — verified live 2026-07-09 — so the base ref is the only usable record id.)
    base = {d.get("_baserecordid_value") for d in data["duplicates"]}
    assert set(accounts) & base, (
        f"seeded pair {accounts} not among detected base records {base}"
    )
