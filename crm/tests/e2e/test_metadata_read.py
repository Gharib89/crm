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
    assert len(options) > 0, (
        "statuscode on account returned no options; raw data: " + json.dumps(env["data"])
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
    r = cli(["--json", "metadata", "dependencies", "account",
             "--kind", "entity", "--for", "required"])
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
    `account` so the baseline payload stays light on a real org."""
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
