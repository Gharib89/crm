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
    names = [a.get("LogicalName") for a in env["data"]]
    assert "name" in names


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


@covers("metadata list-functions")
def test_metadata_list_functions(cli):
    r = cli(["--json", "metadata", "list-functions"], check=False)
    assert r.returncode == 0, r.stderr
    env = json.loads(r.stdout)
    assert env["ok"]
    assert isinstance(env["data"], list)


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
