# pyright: basic
"""E2E tests for `crm legacy` against a live CRM 2011 org (read-only).

Run with ``D365_E2E=1 D365_E2E_LEGACY_PROFILE=<crm2011 profile>``; see TEST.md.
"""

from __future__ import annotations

import json
import os
import re
from pathlib import Path

import pytest

from crm.tests.e2e.coverage import covers

pytestmark = pytest.mark.requires_legacy

# Captured at import, before the v9 live_profile fixture can repoint CRM_HOME at
# its throwaway home: the crm2011 profile lives in the developer's real home.
_REAL_HOME = os.environ.get("CRM_HOME", "")
_GUID = re.compile(r"^[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}$")


def _legacy(cli, verb: str, *args: str) -> dict:
    name = os.environ["D365_E2E_LEGACY_PROFILE"].strip()
    proc = cli(
        ["--json", "--profile", name, "legacy", verb, *args],
        check=False,
        env={"CRM_HOME": _REAL_HOME},
    )
    assert proc.returncode == 0, proc.stdout + proc.stderr
    env = json.loads(proc.stdout)
    assert env["ok"] is True
    assert env["meta"]["profile"] == name
    assert env["meta"]["url"].endswith("/XRMServices/2011/Organization.svc/web")
    return env["data"]


@covers("legacy version")
def test_legacy_version_is_a_crm2011_build(cli):
    assert _legacy(cli, "version")["version"].startswith("5.")


@covers("legacy whoami")
def test_legacy_whoami_returns_the_three_ids(cli):
    data = _legacy(cli, "whoami")
    for key in ("user_id", "business_unit_id", "organization_id"):
        assert _GUID.match(data[key]), key


@covers("legacy metadata")
def test_legacy_metadata_lists_entities_and_reads_account(cli):
    entities = _legacy(cli, "metadata")
    assert entities
    for entity in entities:
        assert {"logical_name", "custom", "object_type_code"} <= entity.keys()
    detail = _legacy(cli, "metadata", "account")
    assert detail["errors"] == []
    (account,) = detail["entities"]
    assert account["attributes"]
    assert account["relationships"]["one_to_many"]


@pytest.mark.slow  # one existence read per entity: about 2 minutes on a full org
@covers("legacy metadata")
def test_legacy_metadata_in_use_labels_every_row_and_drops_unused(cli):
    plain = _legacy(cli, "metadata")
    rows = _legacy(cli, "metadata", "--in-use")
    assert {"account", "systemuser"} <= {r["logical_name"] for r in rows}
    assert len(rows) < len(plain)
    assert {r["usage"]["state"] for r in rows} <= {"used", "unknown"}
    assert all(r["usage"]["reason"] for r in rows if r["usage"]["state"] == "unknown")


@covers("legacy components")
def test_legacy_components_pages_and_writes_payloads(cli, tmp_path):
    data = _legacy(
        cli, "components", "web-resources", "plugin-steps", "step-images", "--out", str(tmp_path)
    )
    assert data["errors"] == []
    written = [r for r in data["web-resources"] if "content_file" in r]
    assert written
    assert all((tmp_path / r["content_file"]).is_file() for r in written)
    step_ids = {s["id"] for s in data["plugin-steps"]}
    assert step_ids
    assert any(i["sdkmessageprocessingstepid"]["id"] in step_ids for i in data["step-images"])


@covers("legacy components")
def test_legacy_components_in_use_labels_plugin_rows(cli):
    data = _legacy(cli, "components", "plugin-steps", "--in-use")
    assert data["errors"] == []
    assert {s["usage"]["state"] for s in data["plugin-steps"]} <= {"used", "unknown"}
    assert all(
        s["usage"]["reason"] for s in data["plugin-steps"] if s["usage"]["state"] == "unknown"
    )


@covers("legacy components")
def test_legacy_components_in_use_labels_process_rows(cli):
    data = _legacy(cli, "components", "processes", "--in-use")
    assert data["errors"] == []
    assert {p["usage"]["state"] for p in data["processes"]} <= {"used", "unknown"}
    assert all(p["statecode"] != 0 for p in data["processes"])  # a draft is dropped as unused


@covers("legacy components")
def test_legacy_components_scoped_by_solution_and_entity(cli):
    solutions = _legacy(cli, "components", "solutions")["solutions"]
    system = {"Default", "Active", "Basic", "System"}
    name = sorted(
        s["uniquename"] for s in solutions if not s["ismanaged"] and s["uniquename"] not in system
    )[0]
    scoped = _legacy(cli, "components", "--solution", name)
    assert scoped["errors"] == []
    assert [s["uniquename"] for s in scoped["solutions"]] == [name]
    # Parent-carried members come with their parent.
    assemblies = {a["id"] for a in scoped["plugin-assemblies"]}
    assert all(t["pluginassemblyid"]["id"] in assemblies for t in scoped["plugin-types"])
    steps = {s["id"] for s in scoped["plugin-steps"]}
    assert all(i["sdkmessageprocessingstepid"]["id"] in steps for i in scoped["step-images"])

    account = _legacy(cli, "components", "--entity", "account")
    assert [k for k in account if k != "errors"] == [
        "plugin-steps",
        "step-images",
        "processes",
        "forms",
        "views",
    ]
    assert account["forms"] and account["views"]
    assert all(f["objecttypecode"] == "account" for f in account["forms"])
    assert all(v["returnedtypecode"] == "account" for v in account["views"])
    assert all(p["primaryentity"] == "account" for p in account["processes"])


@covers("legacy dependencies")
def test_legacy_dependencies_reads_what_depends_on_account(cli):
    (account,) = [e for e in _legacy(cli, "metadata") if e["logical_name"] == "account"]
    rows = _legacy(
        cli, "dependencies", account["metadata_id"], "--component-type", "1", "--for", "dependents"
    )
    assert rows
    assert all(r["requiredcomponentobjectid"] == account["metadata_id"] for r in rows)
    assert all(isinstance(r["dependentcomponenttype"], int) for r in rows)


@covers("legacy export-solution")
def test_legacy_export_solution_writes_a_solution_zip(cli, tmp_path):
    import zipfile

    solutions = _legacy(cli, "components", "solutions")["solutions"]
    system = {"Default", "Active", "Basic", "System"}
    name = sorted(
        s["uniquename"] for s in solutions if not s["ismanaged"] and s["uniquename"] not in system
    )[0]
    target = tmp_path / "solution.zip"
    data = _legacy(cli, "export-solution", name, "-o", str(target))
    assert data["bytes"] == target.stat().st_size
    with zipfile.ZipFile(target) as archive:
        assert {"solution.xml", "customizations.xml"} <= set(archive.namelist())


@covers("inventory extract")
def test_inventory_extract_writes_a_valid_crm2011_inventory(cli, tmp_path, monkeypatch):
    """The full read takes minutes on a real org: every kind, every page."""
    import jsonschema

    from crm.core import inventory

    name = os.environ["D365_E2E_LEGACY_PROFILE"].strip()
    out = tmp_path / "inventory"
    proc = cli(
        ["--json", "--profile", name, "inventory", "extract", "--out", str(out)],
        check=False,
        env={"CRM_HOME": _REAL_HOME},
    )
    assert proc.returncode == 0, proc.stdout + proc.stderr
    assert "[crm] extract stage=source" in proc.stderr, proc.stderr
    summary = json.loads(proc.stdout)["data"]
    assert summary["complete"] is True, proc.stdout
    assert summary["manifest"] == str(out / "manifest.json"), proc.stdout + proc.stderr
    manifest = json.loads((out / "manifest.json").read_text())
    schema = json.loads(
        (
            Path(inventory.__file__).parent.parent / "schemas" / "inventory-manifest-v3.schema.json"
        ).read_text()
    )
    jsonschema.Draft202012Validator(schema).validate(manifest)
    parsed = inventory.read_inventory(out)
    assert parsed.source.platform == "crm2011"
    assert parsed.source.version.startswith("5.")
    assert any(
        g.description and g.description.startswith("external integrations") for g in parsed.gaps
    )
    # crm writes no credential into any output file: not even the profile's user name.
    # Each is searched raw and in the escaped forms the JSON manifest and XML artifacts use.
    from xml.sax.saxutils import escape

    from crm.core import connection as conn_mod

    monkeypatch.setenv("CRM_HOME", _REAL_HOME)
    resolved = conn_mod.resolve_credentials(profile_name=name)
    secrets = {
        # bytes.lower() folds ASCII only, so fold each form the same way as the file.
        form.encode().lower()
        for value in (resolved.profile.username, resolved.password)
        if value
        for form in (
            value,
            json.dumps(value, ensure_ascii=False)[1:-1],
            escape(value, {'"': "&quot;", "'": "&apos;"}),
        )
    }
    for path in out.rglob("*"):
        if path.is_file():
            data = path.read_bytes().lower()
            # The message names the file only, so a failure never prints a secret.
            assert not any(s in data for s in secrets), f"a credential appears in {path}"
