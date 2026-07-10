# pyright: basic
"""E2E: `metadata export-spec --with-forms` projects a seedable main form (#794).

Places a custom field on a throwaway entity's platform-generated main form (via the
ADR 0024 forms-apply path), projects the form with `export-spec --with-forms`,
removes the field from the form, then re-applies the exported spec and asserts the
form converges the field back — the export→apply round-trip that seeds a form onto
an org that lacks it (ADR 0019 seedable invariant). Runs on both the on-prem (NTLM)
and Dataverse-online targets.
"""
from __future__ import annotations

import json

import yaml

from crm.tests.e2e.coverage import covers


@covers("metadata export-spec")
def test_export_spec_forms_round_trip(
    cli, backend, ephemeral_solution, ephemeral_entity, tmp_path, request
):
    from crm.core import forms as forms_mod

    suffix = ephemeral_entity[-8:]
    attr_schema = f"new_xpfld_{suffix}"
    attr_logical = attr_schema.lower()
    tab, section = f"new_xptab_{suffix}", f"new_xpsec_{suffix}"

    # Setup: create the attribute and place it on the entity's main form in one
    # apply (the proven ADR 0024 create path).
    setup = {
        "solution": {"unique_name": ephemeral_solution},
        "entities": [{
            "schema_name": ephemeral_entity,
            "display_name": f"E2E {suffix}",
            "attributes": [{
                "kind": "string", "schema_name": attr_schema,
                "display_name": f"Export Field {suffix}", "max_length": 50,
            }],
            "forms": [{
                "tabs": [{
                    "name": tab, "label": "Export Tab",
                    "sections": [{
                        "name": section, "label": "Export Section",
                        "fields": [{"name": attr_logical}],
                    }],
                }],
            }],
        }],
    }
    setup_path = tmp_path / "setup.json"
    setup_path.write_text(json.dumps(setup), encoding="utf-8")

    def _cleanup():
        try:
            backend.delete(
                f"EntityDefinitions(LogicalName='{ephemeral_entity}')"
                f"/Attributes(LogicalName='{attr_logical}')")
        except Exception:  # noqa: BLE001 — best-effort cleanup, never mask the test
            pass

    request.addfinalizer(_cleanup)

    r_setup = cli(["--json", "apply", "-f", str(setup_path)])
    assert r_setup.returncode == 0, r_setup.stderr
    assert not json.loads(r_setup.stdout)["data"].get("failed")

    # 1) Project the form: --with-forms emits a forms: block carrying the custom
    #    field's placement (the primary-name field is a platform default, omitted).
    out = tmp_path / "spec.yaml"
    r_exp = cli(["--json", "metadata", "export-spec", ephemeral_entity,
                 "--with-forms", "--solution", ephemeral_solution, "-o", str(out)])
    assert r_exp.returncode == 0, r_exp.stderr
    assert json.loads(r_exp.stdout)["data"]["forms"] == 1

    written = yaml.safe_load(out.read_text(encoding="utf-8"))
    block = written["entities"][0]["forms"][0]
    placed = {
        f["name"]
        for t in block["tabs"] for s in t["sections"] for f in s["fields"]
    }
    assert attr_logical in placed, f"custom field not projected: {block}"
    assert attr_logical != ephemeral_entity  # sanity: not the primary field

    # 2) Remove the field from the live form (the attribute stays), so the form no
    #    longer has it — the "org that lacks it" side of the seed round-trip.
    r_rm = cli(["--json", "form", "remove-field", ephemeral_entity, attr_logical,
                "--publish", "--solution", ephemeral_solution])
    assert r_rm.returncode == 0, r_rm.stderr

    # 3) Re-apply the exported spec: converge seeds the field back onto the form.
    r_apply = cli(["--json", "apply", "-f", str(out)])
    assert r_apply.returncode == 0, r_apply.stderr
    data = json.loads(r_apply.stdout)["data"]
    assert not data.get("failed"), f"apply reported failures: {data}"
    form_applied = [e for e in data["applied"] if e["kind"] == "form"]
    assert form_applied, f"form not re-seeded: {data}"
    assert any(c["kind"] == "field" and c["name"] == attr_logical
               for c in form_applied[0]["components"]), form_applied

    # The projected form actually carries the custom field again, live.
    form_row = forms_mod._select_form(
        forms_mod.read_entity_forms(backend, ephemeral_entity), None)
    assert f'datafieldname="{attr_logical}"' in form_row["formxml"]
