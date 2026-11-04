# pyright: basic
"""E2E: a `forms:` tab and section adopt a new table's unnamed default tab and
section by label (#1053).

The platform-generated main form's first tab and section carry a label
("General") but no `name`. Applying a spec whose tab and section are both named
`general` and labelled `General` must name those elements and place the declared
fields there, not add a second "General" tab. Then a re-apply and an
export-spec round trip both report the form `skipped`.
"""

from __future__ import annotations

import json
import uuid
import xml.etree.ElementTree as ET

import yaml

from crm.tests.e2e.conftest import E2E_MARKER
from crm.tests.e2e.coverage import covers


def _forms(data, bucket):
    return [e for e in data.get(bucket) or [] if e["kind"] == "form"]


@covers("apply")
def test_apply_forms_adopts_default_tab_and_section(
    cli, backend, ephemeral_solution, tmp_path, request
):
    from crm.core import forms as forms_mod
    from crm.core import metadata as meta_mod

    suffix = uuid.uuid4().hex[:8]
    entity_schema = f"new_{E2E_MARKER}dt{suffix}"
    entity = entity_schema.lower()
    city, kind = f"new_dtcity_{suffix}", f"new_dtkind_{suffix}"
    spec = {
        "solution": {"unique_name": ephemeral_solution},
        "entities": [
            {
                "schema_name": entity_schema,
                "display_name": f"E2E default tab {suffix}",
                "display_collection_name": f"E2E default tabs {suffix}",
                "attributes": [
                    {"kind": "string", "schema_name": city, "display_name": "City"},
                    {"kind": "string", "schema_name": kind, "display_name": "Kind"},
                ],
                "forms": [
                    {
                        "tabs": [
                            {
                                "name": "general",
                                "label": "General",
                                "sections": [
                                    {
                                        "name": "general",
                                        "label": "General",
                                        "fields": [{"name": city}, {"name": kind}],
                                    }
                                ],
                            }
                        ]
                    }
                ],
            }
        ],
    }
    spec_path = tmp_path / "spec.json"
    spec_path.write_text(json.dumps(spec), encoding="utf-8")

    def _cleanup():
        try:
            meta_mod.delete_entity(backend, entity)
        except Exception:  # noqa: BLE001 — best-effort cleanup, never mask the test
            pass

    request.addfinalizer(_cleanup)

    # 1) The first apply adopts the default tab and section and reports `updated`.
    r1 = cli(["--json", "apply", "-f", str(spec_path)])
    assert r1.returncode == 0, r1.stderr
    data = json.loads(r1.stdout)["data"]
    assert not data.get("failed"), data
    updated = _forms(data, "updated")
    assert updated, f"form not reported updated: {data}"
    adopted = {
        (c["kind"], c["name"])
        for c in updated[0]["components"]
        if c.get("diff", {}).get("name") == {"old": None, "new": "general"}
    }
    assert adopted == {("tab", "general"), ("section", "general")}, updated[0]["components"]

    formxml = forms_mod._select_form(forms_mod.read_entity_forms(backend, entity), None)["formxml"]
    root = ET.fromstring(formxml)
    tabs = root.findall("./tabs/tab")
    assert [t.get("name") for t in tabs] == ["general"], formxml
    sections = tabs[0].findall("./columns/column/sections/section")
    assert sections[0].get("name") == "general", formxml
    assert "general_section" not in formxml
    primary = backend.get(
        f"EntityDefinitions(LogicalName='{entity}')", params={"$select": "PrimaryNameAttribute"}
    )["PrimaryNameAttribute"]
    # The platform binds the primary column's control by its schema-cased name.
    bound = [(c.get("datafieldname") or "").lower() for c in sections[0].iter("control")]
    for field in (primary, "ownerid", city, kind):
        assert field in bound, (field, bound)

    # 2) Re-applying the same spec changes nothing.
    r2 = cli(["--json", "apply", "-f", str(spec_path)])
    assert r2.returncode == 0, r2.stderr
    data2 = json.loads(r2.stdout)["data"]
    assert _forms(data2, "skipped"), f"re-apply wrote the form: {data2}"

    # 3) export-spec emits the adopted tab and section; applying it back is a no-op.
    out = tmp_path / "exported.yaml"
    r_exp = cli(
        [
            "--json",
            "metadata",
            "export-spec",
            entity,
            "--with-forms",
            "--solution",
            ephemeral_solution,
            "-o",
            str(out),
        ]
    )
    assert r_exp.returncode == 0, r_exp.stderr
    block = yaml.safe_load(out.read_text(encoding="utf-8"))["entities"][0]["forms"][0]
    tab = block["tabs"][0]
    assert (tab["name"], tab["sections"][0]["name"]) == ("general", "general"), block
    assert {f["name"] for f in tab["sections"][0]["fields"]} == {city, kind}, block

    r3 = cli(["--json", "apply", "-f", str(out)])
    assert r3.returncode == 0, r3.stderr
    data3 = json.loads(r3.stdout)["data"]
    assert not data3.get("failed"), data3
    assert _forms(data3, "skipped"), f"exported spec re-wrote the form: {data3}"
