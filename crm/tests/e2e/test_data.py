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
    finally:
        _cleanup()
