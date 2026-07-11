# pyright: basic
"""E2E tests for read-only metadata commands."""

from __future__ import annotations

import json
import os

from crm.tests.e2e.coverage import covers


@covers("metadata attribute")
def test_metadata_attribute(cli):
    r = cli(["--json", "metadata", "attribute", "account", "name"])
    assert r.returncode == 0, r.stderr
    env = json.loads(r.stdout)
    assert env["ok"]
    assert env["data"]["LogicalName"] == "name"


@covers("metadata attributes")
def test_metadata_attributes(cli):
    r = cli(["--json", "metadata", "attributes", "account"])
    assert r.returncode == 0, r.stderr
    env = json.loads(r.stdout)
    assert env["ok"]
    assert isinstance(env["data"], list)
    assert len(env["data"]) > 0
    by_name = {a.get("LogicalName"): a for a in env["data"]}
    assert "name" in by_name
    # Write/read validity + required level are projected (#337); RequiredLevel
    # is flattened to its string value, never the raw {"Value": ...} object.
    name_attr = by_name["name"]
    assert name_attr["IsValidForCreate"] is True
    assert name_attr["IsValidForUpdate"] is True
    assert name_attr["IsValidForRead"] is True
    assert isinstance(name_attr["RequiredLevel"], str)


@covers("metadata entity")
def test_metadata_entity(cli):
    r = cli(["--json", "metadata", "entity", "account"])
    assert r.returncode == 0, r.stderr
    env = json.loads(r.stdout)
    assert env["ok"]
    assert env["data"]["LogicalName"] == "account"


@covers("metadata describe")
def test_metadata_describe(cli):
    r = cli(["--json", "metadata", "describe", "account"])
    assert r.returncode == 0, r.stderr
    env = json.loads(r.stdout)
    assert env["ok"]
    data = env["data"]
    assert "logical_name" in data
    assert data["logical_name"] == "account"
    assert "writable_attributes" in data


@covers("metadata keys")
def test_metadata_keys(cli):
    r = cli(["--json", "metadata", "keys", "account"])
    assert r.returncode == 0, r.stderr
    env = json.loads(r.stdout)
    assert env["ok"]
    # May be empty on any org — assert structure only.
    assert isinstance(env["data"], list)


@covers("metadata relationships")
def test_metadata_relationships(cli):
    r = cli(["--json", "metadata", "relationships", "account"])
    assert r.returncode == 0, r.stderr
    env = json.loads(r.stdout)
    assert env["ok"]
    data = env["data"]
    # account always has relationships
    assert isinstance(data.get("OneToMany"), list)
    assert isinstance(data.get("ManyToOne"), list)
    assert isinstance(data.get("ManyToMany"), list)
    total = len(data["OneToMany"]) + len(data["ManyToOne"]) + len(data["ManyToMany"])
    assert total > 0


@covers("metadata list-actions")
def test_metadata_list_actions(cli):
    r = cli(["--json", "metadata", "list-actions"], check=False)
    assert r.returncode == 0, r.stderr
    env = json.loads(r.stdout)
    assert env["ok"]
    # May be empty on a minimal org — assert structure only.
    assert isinstance(env["data"], list)
    for a in env["data"]:
        assert isinstance(a["is_bound"], bool)
        assert a["return_type"] is None or isinstance(a["return_type"], str)


@covers("metadata list-functions")
def test_metadata_list_functions(cli):
    r = cli(["--json", "metadata", "list-functions"], check=False)
    assert r.returncode == 0, r.stderr
    env = json.loads(r.stdout)
    assert env["ok"]
    assert isinstance(env["data"], list)
    for f in env["data"]:
        assert isinstance(f["is_bound"], bool)
        assert isinstance(f["is_composable"], bool)
        assert f["return_type"] is None or isinstance(f["return_type"], str)


@covers("metadata list-optionsets")
def test_metadata_list_optionsets(cli):
    r = cli(["--json", "metadata", "list-optionsets"])
    assert r.returncode == 0, r.stderr
    env = json.loads(r.stdout)
    assert env["ok"]
    assert isinstance(env["data"], list)
    # Every org has at least one global option set.
    assert len(env["data"]) > 0


@covers("metadata picklist")
def test_metadata_picklist(cli):
    # account.statuscode is a StatusAttribute backed by a local OptionSet on every
    # D365 / Dataverse org. Fall back to account.industrycode (PicklistAttribute)
    # if statuscode returns no options (should not happen in practice).
    r = cli(["--json", "metadata", "picklist", "account", "statuscode"])
    assert r.returncode == 0, r.stderr
    env = json.loads(r.stdout)
    assert env["ok"]
    options = env.get("meta", {}).get("options", [])
    assert isinstance(options, list)
    assert len(options) > 0, "statuscode on account returned no options; raw data: " + json.dumps(
        env["data"]
    )


@covers("metadata dependencies")
def test_metadata_dependencies(cli):
    # account is a built-in entity that cannot be deleted — it will always have
    # blockers, confirming the API path works.
    r = cli(["--json", "metadata", "dependencies", "account", "--kind", "entity"])
    assert r.returncode == 0, r.stderr
    env = json.loads(r.stdout)
    assert env["ok"]
    data = env["data"]
    assert "can_delete" in data
    assert isinstance(data.get("blockers"), list)


@covers("metadata dependencies")
def test_metadata_dependencies_for_required(cli):
    # --for required lists what the target depends on (RetrieveRequiredComponents),
    # the reverse direction of the default delete/dependents paths.
    r = cli(
        ["--json", "metadata", "dependencies", "account", "--kind", "entity", "--for", "required"]
    )
    assert r.returncode == 0, r.stderr
    env = json.loads(r.stdout)
    assert env["ok"]
    assert env["data"].get("for") == "required"
    assert isinstance(env["data"].get("blockers"), list)


@covers("metadata export-spec")
def test_metadata_export_spec(cli, tmp_path):
    out = str(tmp_path / "account_spec.json")
    # Without -o: spec emitted in JSON envelope.
    r = cli(["--json", "metadata", "export-spec", "account"])
    assert r.returncode == 0, r.stderr
    env = json.loads(r.stdout)
    assert env["ok"]
    spec = env["data"]
    assert "entities" in spec
    assert len(spec["entities"]) == 1
    entity = spec["entities"][0]
    # Export-spec shape: schema_name and display_name are required fields.
    assert "schema_name" in entity
    assert "display_name" in entity

    # With -o: YAML file written to disk.
    r2 = cli(["--json", "metadata", "export-spec", "account", "-o", out])
    assert r2.returncode == 0, r2.stderr
    env2 = json.loads(r2.stdout)
    assert env2["ok"]
    assert os.path.isfile(out)
    assert os.path.getsize(out) > 0


@covers("metadata export-spec")
def test_export_spec_emits_customer_column(cli, backend, ephemeral_entity):
    """#700: a custom Customer column must be exported as kind 'customer' (not
    dropped with a warning). Add a Customer column to the scratch entity, export
    the entity, and assert the column rides the spec as the customer kind — with
    no target_entity (apply's customer path forbids one) and no …idtype companion.
    """
    from crm.core import metadata_attrs as ma

    schema = "new_E2ECustSpec"
    info = ma.add_attribute(
        backend,
        entity=ephemeral_entity,
        kind="customer",
        schema_name=schema,
        display_name="E2E CustSpec",
        publish=False,
    )
    assert info.get("created"), info

    r = cli(["--json", "metadata", "export-spec", ephemeral_entity])
    assert r.returncode == 0, r.stderr
    env = json.loads(r.stdout)
    assert env["ok"], env
    attrs = env["data"]["entities"][0].get("attributes", [])
    match = [a for a in attrs if a["schema_name"].lower() == schema.lower()]
    assert match, f"customer column {schema!r} missing from export: {attrs}"
    col = match[0]
    assert col["kind"] == "customer"
    assert "target_entity" not in col
    # The server-managed …idtype companion is not user-declared → must not appear.
    assert not any(a["schema_name"].lower() == f"{schema.lower()}type" for a in attrs), (
        f"…idtype companion leaked into export: {attrs}"
    )


@covers("metadata export-spec")
def test_export_spec_roundtrips_lookup_casing_and_descriptions(
    cli, backend, ephemeral_solution, unique, tmp_path
):
    """#701: exported lookups must carry the referencing attribute's SchemaName
    casing (not the lowercase logical name), and entity / optionset / view
    descriptions must round-trip. Build a scratch entity carrying all four, export
    it, assert the casing + descriptions, then re-apply (dry-run) and assert zero
    description drift and that the lookup is NOT re-planned (a casing mismatch on
    re-apply would create a divergent column).
    """
    import yaml

    from crm.core import apply as apply_mod
    from crm.core import metadata as meta_mod
    from crm.core import metadata_attrs as ma
    from crm.core import optionsets as os_mod
    from crm.core import relationships as rel_mod
    from crm.core import views as view_mod

    ent_schema = f"new_E2eRt{unique}"
    lookup_schema = f"new_E2eRt{unique}Acct"  # PascalCase → the casing under test
    rel_schema = f"new_e2ert{unique}_account"
    os_name = f"new_e2ert{unique}os"
    pick_schema = f"new_E2eRt{unique}Pick"
    ent_desc = "E2E round-trip entity description"
    os_desc = "E2E round-trip optionset description"
    view_desc = "E2E round-trip view description"

    created = meta_mod.create_entity(
        backend,
        schema_name=ent_schema,
        display_name=f"E2eRt {unique}",
        description=ent_desc,
    )
    logical = created["logical_name"]
    try:
        info = meta_mod.entity_info(backend, logical)
        otc = int(info["ObjectTypeCode"])
        primary = info["PrimaryNameAttribute"]

        # Global option set with a description + a picklist bound to it.
        os_mod.create_optionset(
            backend,
            name=os_name,
            display_name="E2eRt OS",
            description=os_desc,
            options=[(None, "One"), (None, "Two")],
        )
        ma.add_attribute(
            backend,
            entity=logical,
            kind="picklist",
            schema_name=pick_schema,
            display_name="E2eRt Pick",
            optionset_name=os_name,
        )
        # Self-referential 1:N so the scratch entity is the referenced ("1") side
        # `read_entity_relationships` enumerates; the lookup column (PascalCase
        # schema) lands on the same entity, keeping the fixture self-contained.
        rel_mod.create_one_to_many(
            backend,
            schema_name=rel_schema,
            referenced_entity=logical,
            referencing_entity=logical,
            lookup_schema=lookup_schema,
            lookup_display="E2eRt Parent",
        )
        # A view with a description.
        view_mod.create_view(
            backend,
            entity=logical,
            object_type_code=otc,
            name=f"E2eRt View {unique}",
            columns=[(primary, 200)],
            description=view_desc,
        )

        # Export the whole entity, baking in the solution block for re-apply.
        out = tmp_path / "spec.yaml"
        r = cli(
            [
                "--json",
                "metadata",
                "export-spec",
                logical,
                "--with-relationships",
                "--with-views",
                "--solution",
                ephemeral_solution,
                "-o",
                str(out),
            ]
        )
        assert r.returncode == 0, r.stderr
        spec = yaml.safe_load(out.read_text(encoding="utf-8"))
        entity = spec["entities"][0]

        # 1) Entity description round-trips.
        assert entity["description"] == ent_desc

        # 2) Lookup schema-name casing preserved (not the lowercase logical name).
        rels = entity["relationships"]
        rel = next(r for r in rels if r["schema_name"].lower() == rel_schema.lower())
        assert rel["lookup_schema"] == lookup_schema

        # 3) Global option set description round-trips.
        os_entry = next(o for o in spec["optionsets"] if o["name"].lower() == os_name.lower())
        assert os_entry["description"] == os_desc

        # 4) View description round-trips.
        view = next(v for v in entity["views"] if v["name"] == f"E2eRt View {unique}")
        assert view["description"] == view_desc

        # Round-trip: re-apply the exported spec read-only (dry-run). The lookup
        # already exists with matching casing, so it must NOT be planned for
        # creation, and no entity description drift may be reported. dry_run is
        # construction-only, so build a throwaway read-only backend.
        from crm.core.connection import resolve_credentials
        from crm.utils.d365_backend import D365Backend

        dry = resolve_credentials("e2e")
        dry_backend = D365Backend(dry.profile, dry.password, dry_run=True)
        report = apply_mod.apply_spec(dry_backend, spec)
        planned_rels = [
            e
            for e in report.get("planned", [])
            if str(e.get("name", "")).lower() == rel_schema.lower()
        ]
        assert not planned_rels, f"lookup re-planned (casing mismatch?): {planned_rels}"
        # Reconcile diff shapes vary by kind ({"description": …} vs
        # {"fields": [… "description" …]}), so scan the serialized diff.
        desc_drift = [
            e for e in report.get("updated", []) if "description" in json.dumps(e.get("diff") or {})
        ]
        assert not desc_drift, f"description drift on re-apply: {desc_drift}"
    finally:
        try:
            meta_mod.delete_entity(backend, logical)
        except Exception:  # noqa: BLE001 — best-effort cleanup, never mask the test
            pass
        try:
            os_mod.delete_optionset(backend, os_name)
        except Exception:  # noqa: BLE001
            pass


@covers("metadata cache-clear")
def test_metadata_cache_clear(cli):
    r = cli(["--json", "metadata", "cache-clear"])
    assert r.returncode == 0, r.stderr
    env = json.loads(r.stdout)
    assert env["ok"]
    # data.cleared is True when a cache file existed, False when there was nothing
    # to clear — both outcomes are success.
    assert "cleared" in env["data"]


@covers("metadata changes")
def test_metadata_changes(cli):
    """RetrieveMetadataChanges: a baseline call returns a fresh ServerVersionStamp;
    feeding it back as --since returns a (smaller) delta + a new stamp. Scoped to
    `account` so the baseline payload stays light on a real org.
    """
    # Baseline (no --since): returns a fresh stamp + the scoped entity.
    r = cli(["--json", "metadata", "changes", "--entity", "account"])
    assert r.returncode == 0, r.stderr
    env = json.loads(r.stdout)
    assert env["ok"]
    stamp = env["data"]["server_version_stamp"]
    assert stamp  # a non-empty version stamp to save for next time
    assert "account" in [e["logical_name"] for e in env["data"]["entities"]]

    # Delta: passing the stamp back returns only changes since (typically none)
    # plus a new stamp and a deleted_count.
    r2 = cli(["--json", "metadata", "changes", "--entity", "account", "--since", stamp])
    assert r2.returncode == 0, r2.stderr
    env2 = json.loads(r2.stdout)
    assert env2["ok"]
    assert env2["data"]["server_version_stamp"]
    assert "deleted_count" in env2["data"]
