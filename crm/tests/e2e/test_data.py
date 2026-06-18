# pyright: basic
"""E2E tests for data verbs: export, import."""
from __future__ import annotations

import csv
import json

import pytest

from crm.tests.e2e.coverage import covers


# ── data export ───────────────────────────────────────────────────────────────


@covers("data export")
def test_data_export_contacts_csv(cli, tmp_path):
    """Export a small slice of contacts to CSV; assert file exists and has a header."""
    out_file = tmp_path / "contacts_export.csv"
    result = cli([
        "--json", "data", "export", "contacts",
        "--output", str(out_file),
        "--select", "contactid,firstname,lastname",
        "--max-records", "5",
    ])
    assert result.returncode == 0, (
        f"data export failed:\n{result.stderr}\nstdout: {result.stdout}"
    )
    env = json.loads(result.stdout)
    assert env["ok"], env
    data = env["data"]
    assert data.get("format") == "csv", f"unexpected format: {data}"
    assert data.get("entity_set") == "contacts", f"unexpected entity_set: {data}"
    assert out_file.exists(), f"output file not written: {out_file}"
    # File must have a header line with the selected columns.
    content = out_file.read_text(encoding="utf-8")
    assert content.strip(), "exported CSV is empty"
    first_line = content.splitlines()[0]
    assert "contactid" in first_line, (
        f"CSV header does not contain 'contactid': {first_line!r}"
    )


# ── data delete (BulkDelete) ────────────────────────────────────────────────────


@covers("data delete")
@pytest.mark.slow
def test_data_delete_bulkdelete_by_fetchxml(backend, cli, tmp_path, unique):
    """Submit a BulkDelete job over a FetchXML filter and wait; rows are deleted."""
    lastname = f"E2EBulkDel{unique[:6]}"
    ids: list[str] = []
    for fn in ("BulkA", "BulkB"):
        created = backend.post(
            "contacts",
            json_body={"firstname": fn, "lastname": lastname},
            extra_headers={"Prefer": "return=representation"},
        )
        ids.append(str(created["contactid"]))

    def _cleanup():
        for cid in ids:
            try:
                backend.delete(f"contacts({cid})")
            except Exception:
                pass

    try:
        ln_lit = lastname.replace("'", "''")
        fetch = (
            '<fetch><entity name="contact"><attribute name="contactid"/>'
            f'<filter><condition attribute="lastname" operator="eq" value="{lastname}"/>'
            "</filter></entity></fetch>"
        )
        result = cli([
            "--json", "data", "delete", "contacts",
            "--fetchxml", fetch, "--yes", "--wait", "--timeout", "120",
        ])
        assert result.returncode == 0, (
            f"data delete failed:\n{result.stderr}\nstdout: {result.stdout}"
        )
        env = json.loads(result.stdout)
        assert env["ok"], env
        data = env["data"]
        assert data.get("status") == "completed", f"job did not complete: {data}"
        assert data.get("match_count") == 2, f"expected 2 matched; got: {data}"
        assert data.get("succeeded") == 2, f"expected 2 deleted; got: {data}"
        # Both records are gone.
        page = backend.get(
            "contacts",
            params={"$filter": f"lastname eq '{ln_lit}'", "$select": "contactid"},
        )
        assert (page.get("value") or []) == [], f"records not deleted: {page}"
    finally:
        _cleanup()


# ── data import ───────────────────────────────────────────────────────────────


@covers("data import")
@pytest.mark.slow
def test_data_import_contacts_csv(backend, cli, tmp_path, unique):
    """Import a small CSV of contacts; assert records created; clean up."""
    lastname1 = f"E2EImp{unique[:6]}A"
    lastname2 = f"E2EImp{unique[:6]}B"

    csv_file = tmp_path / "contacts_import.csv"
    with csv_file.open("w", encoding="utf-8", newline="") as fh:
        writer = csv.writer(fh)
        writer.writerow(["firstname", "lastname"])
        writer.writerow(["E2EFirst", lastname1])
        writer.writerow(["E2ESecond", lastname2])

    created_ids: list[str] = []

    def _cleanup():
        for cid in created_ids:
            try:
                backend.delete(f"contacts({cid})")
            except Exception:
                pass

    try:
        result = cli([
            "--json", "data", "import", "contacts", str(csv_file),
            "--format", "csv",
            "--mode", "create",
        ])
        assert result.returncode == 0, (
            f"data import failed:\n{result.stderr}\nstdout: {result.stdout}"
        )
        env = json.loads(result.stdout)
        assert env["ok"], env
        data = env["data"]

        # Resolve the created IDs FIRST, by querying for the unique lastnames, so the
        # finalizer can clean up even if the assertions below fail (a partial import
        # still creates records).
        for ln in (lastname1, lastname2):
            ln_lit = ln.replace("'", "''")
            page = backend.get(
                "contacts",
                params={
                    "$filter": f"lastname eq '{ln_lit}'",
                    "$select": "contactid",
                },
            )
            rows = page.get("value", []) if isinstance(page, dict) else []
            for row in rows:
                cid = row.get("contactid")
                if cid:
                    created_ids.append(str(cid))

        assert data.get("imported", 0) >= 2, (
            f"expected at least 2 imported records; got: {data}"
        )
        assert data.get("failed", 0) == 0, (
            f"import had failures: {data}"
        )
        assert data.get("failures") == [], (
            f"clean import should report no per-record failures; got: {data}"
        )
    finally:
        _cleanup()


@covers("data import")
@pytest.mark.slow
def test_data_import_rebinds_exported_lookups(backend, cli, tmp_path, unique):
    """A lookup exported as ``_<attr>_value`` round-trips on import (#333).

    ``data export`` emits lookups in READ shape (``_primarycontactid_value`` GUID),
    which the Web API cannot write directly. Import must rebind it to
    ``<nav>@odata.bind`` from metadata so the relationship lands — and must drop a
    polymorphic ``_ownerid_value`` (no annotation in a plain export) rather than
    fail the row. Creates a contact + an account pointing at it, exports the
    account, imports a fresh account carrying the exported lookup, and asserts the
    relationship landed.
    """
    lastname = f"E2EBind{unique[:6]}"
    acct_name = f"E2EBindAcct {unique[:6]}"
    created_contacts: list[str] = []
    created_accounts: list[str] = []

    def _cleanup():
        for aid in created_accounts:
            try:
                backend.delete(f"accounts({aid})")
            except Exception:
                pass
        for cid in created_contacts:
            try:
                backend.delete(f"contacts({cid})")
            except Exception:
                pass

    try:
        # Setup: a contact, and a source account whose primary contact is that contact.
        r = cli(["--json", "entity", "create", "contacts",
                 "--data", json.dumps({"lastname": lastname})])
        assert r.returncode == 0, f"contact create failed:\n{r.stderr}\n{r.stdout}"
        contact_id = str(json.loads(r.stdout)["data"]["_entity_id"])
        created_contacts.append(contact_id)

        r = cli(["--json", "entity", "create", "accounts", "--data", json.dumps({
            "name": f"{acct_name} src",
            "primarycontactid@odata.bind": f"/contacts({contact_id})",
        })])
        assert r.returncode == 0, f"account create failed:\n{r.stderr}\n{r.stdout}"
        src_account_id = str(json.loads(r.stdout)["data"]["_entity_id"])
        created_accounts.append(src_account_id)

        # Export the source account (READ shape: _primarycontactid_value GUID).
        export_file = tmp_path / "account.json"
        r = cli(["--json", "data", "export", "accounts",
                 "--output", str(export_file), "--format", "json",
                 "--filter", f"accountid eq {src_account_id}"])
        assert r.returncode == 0, f"data export failed:\n{r.stderr}\n{r.stdout}"
        exported = json.loads(export_file.read_text(encoding="utf-8"))[0]
        assert exported.get("_primarycontactid_value") == contact_id, (
            f"export did not emit the lookup in READ form: {exported.get('_primarycontactid_value')!r}"
        )

        # Import a fresh account carrying the exported lookup (plus a polymorphic
        # _ownerid_value with no annotation, which must be dropped, not fail).
        import_file = tmp_path / "account_import.jsonl"
        import_file.write_text(json.dumps({
            "name": acct_name,
            "_primarycontactid_value": exported["_primarycontactid_value"],
            "_ownerid_value": exported.get("_ownerid_value"),
        }) + "\n", encoding="utf-8")

        r = cli(["--json", "data", "import", "accounts", str(import_file),
                 "--format", "jsonl", "--mode", "create",
                 "--no-transaction", "--continue-on-error"])
        assert r.returncode == 0, f"data import failed:\n{r.stderr}\n{r.stdout}"
        data = json.loads(r.stdout)["data"]

        # Resolve the new account id FIRST so cleanup runs even if asserts fail.
        name_lit = acct_name.replace("'", "''")
        page = backend.get("accounts", params={
            "$filter": f"name eq '{name_lit}'",
            "$select": "accountid,_primarycontactid_value",
        })
        rows = page.get("value", []) if isinstance(page, dict) else []
        for row in rows:
            if row.get("accountid"):
                created_accounts.append(str(row["accountid"]))

        assert data.get("imported", 0) == 1, f"expected 1 imported; got: {data}"
        assert data.get("failed", 0) == 0, f"import had failures: {data}"
        assert rows, "imported account not found on query-back"
        assert rows[0].get("_primarycontactid_value") == contact_id, (
            f"lookup did not round-trip; new account primarycontactid="
            f"{rows[0].get('_primarycontactid_value')!r}, expected {contact_id!r}"
        )
    finally:
        _cleanup()


@covers("data import")
@pytest.mark.slow
def test_data_import_partial_failure_reports_per_record(backend, cli, tmp_path, unique):
    """A row the server rejects yields a data.failures entry with index/status/error (#332).

    Row 1 is valid; row 2 carries an unknown attribute the Web API rejects with
    400, so with --continue-on-error the good row commits and the bad row is
    reported per-record.
    """
    lastname = f"E2EFail{unique[:6]}"
    jsonl_file = tmp_path / "contacts_partial.jsonl"
    jsonl_file.write_text(
        json.dumps({"firstname": "E2EGood", "lastname": lastname}) + "\n"
        + json.dumps({"firstname": "E2EBad", "lastname": lastname,
                      "this_attribute_does_not_exist_zzz": "x"}) + "\n",
        encoding="utf-8",
    )

    created_ids: list[str] = []

    def _cleanup():
        for cid in created_ids:
            try:
                backend.delete(f"contacts({cid})")
            except Exception:
                pass

    try:
        result = cli([
            "--json", "data", "import", "contacts", str(jsonl_file),
            "--format", "jsonl", "--mode", "create",
            "--no-transaction", "--continue-on-error",
        ])
        assert result.returncode == 0, (
            f"data import failed:\n{result.stderr}\nstdout: {result.stdout}"
        )
        env = json.loads(result.stdout)
        assert env["ok"], env
        data = env["data"]

        # Resolve the created id FIRST so cleanup runs even if asserts fail.
        ln_lit = lastname.replace("'", "''")
        page = backend.get(
            "contacts",
            params={"$filter": f"lastname eq '{ln_lit}'", "$select": "contactid"},
        )
        rows = page.get("value", []) if isinstance(page, dict) else []
        for row in rows:
            cid = row.get("contactid")
            if cid:
                created_ids.append(str(cid))

        assert data.get("imported", 0) == 1, f"expected 1 imported; got: {data}"
        assert data.get("failed", 0) == 1, f"expected 1 failed; got: {data}"
        failures = data.get("failures")
        assert isinstance(failures, list) and len(failures) == 1, (
            f"expected one failure entry; got: {data}"
        )
        entry = failures[0]
        assert entry["index"] == 2, f"failure should point at row 2; got: {entry}"
        assert entry["status"] == 400, f"expected HTTP 400; got: {entry}"
        assert entry.get("error"), f"failure entry missing error text: {entry}"
    finally:
        _cleanup()


@covers("data import")
@pytest.mark.slow
def test_data_import_delete_by_id_column(backend, cli, tmp_path, unique):
    """--mode delete removes the rows named in the file via batch DELETE."""
    lastname = f"E2EDel{unique[:6]}"
    ids: list[str] = []
    for fn in ("DelA", "DelB"):
        created = backend.post(
            "contacts",
            json_body={"firstname": fn, "lastname": lastname},
            extra_headers={"Prefer": "return=representation"},
        )
        ids.append(str(created["contactid"]))

    def _cleanup():
        for cid in ids:
            try:
                backend.delete(f"contacts({cid})")
            except Exception:
                pass

    try:
        del_file = tmp_path / "to_delete.jsonl"
        del_file.write_text(
            "\n".join(json.dumps({"contactid": cid}) for cid in ids) + "\n",
            encoding="utf-8",
        )
        result = cli([
            "--json", "data", "import", "contacts", str(del_file),
            "--mode", "delete", "--id-column", "contactid",
        ])
        assert result.returncode == 0, (
            f"data import --mode delete failed:\n{result.stderr}\nstdout: {result.stdout}"
        )
        env = json.loads(result.stdout)
        assert env["ok"], env
        data = env["data"]
        assert data.get("mode") == "delete", f"unexpected mode: {data}"
        assert data.get("imported", 0) == 2, f"expected 2 deleted; got: {data}"
        assert data.get("failed", 0) == 0, f"delete had failures: {data}"
        # Both records are gone.
        for cid in ids:
            page = backend.get(
                "contacts",
                params={"$filter": f"contactid eq {cid}", "$select": "contactid"},
            )
            assert (page.get("value") or []) == [], f"contact {cid} was not deleted"
    finally:
        _cleanup()
