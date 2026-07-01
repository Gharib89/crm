# pyright: basic
"""E2E: --dry-run reference resolution (#281).

Under --dry-run a name-taking write resolves the server objects it would point
at and reports each under data.references[] = {kind, value, _exists}; a dangling
reference stays a non-failing preview (ok:true) and adds a meta.warnings
advisory. These are READ-ONLY: dry-run issues no writes, only the resolution
GETs fire, so they are safe on both targets with no fixtures. System entities
(account/contact) exist on every org, so a "resolvable" case is deterministic;
an obviously-fake name is the "dangling" case.

scaffold table shares this path but is a LOCAL_GROUP (out of the e2e gate) and
its dry-run reference behaviour is covered by the command-layer unit tests in
crm/tests/test_scaffold.py.
"""
from __future__ import annotations

import json

from crm.tests.e2e.coverage import covers

_GHOST_ENTITY = "zzz_nonexistent_entity_e2e"
_GHOST_OPTIONSET = "zzz_nonexistent_optionset_e2e"
_GHOST_TYPE = "Zzz.Nonexistent.PluginType.E2E"


def _refs(env):
    return {r["kind"]: r["_exists"] for r in env["data"]["references"]}


@covers("metadata create-one-to-many")
def test_create_one_to_many_dry_run_resolves_entities(cli, unique, ephemeral_solution):
    r = cli([
        "--dry-run", "--json", "metadata", "create-one-to-many",
        "--schema-name", f"new_e2eref_{unique}",
        "--referenced-entity", "account",
        "--referencing-entity", "contact",
        "--lookup-schema", f"new_E2eRef{unique}",
        "--lookup-display", "E2E Ref Probe",
        "--no-publish",
        "--solution", ephemeral_solution,
    ])
    assert r.returncode == 0, r.stderr
    env = json.loads(r.stdout)
    assert env["ok"] is True
    assert env["meta"]["dry_run"] is True
    refs = _refs(env)
    assert refs["referenced_entity"] is True
    assert refs["referencing_entity"] is True


@covers("metadata create-one-to-many")
def test_create_one_to_many_dry_run_flags_dangling_entity(cli, unique, ephemeral_solution):
    r = cli([
        "--dry-run", "--json", "metadata", "create-one-to-many",
        "--schema-name", f"new_e2eref_{unique}",
        "--referenced-entity", _GHOST_ENTITY,
        "--referencing-entity", "account",
        "--lookup-schema", f"new_E2eRef{unique}",
        "--lookup-display", "E2E Ref Probe",
        "--no-publish",
        "--solution", ephemeral_solution,
    ])
    assert r.returncode == 0, r.stderr
    env = json.loads(r.stdout)
    assert env["ok"] is True  # dry-run never hard-fails on a dangling reference
    refs = _refs(env)
    assert refs["referenced_entity"] is False
    assert refs["referencing_entity"] is True
    assert any(_GHOST_ENTITY in w for w in env["meta"]["warnings"])


@covers("metadata add-attribute")
def test_add_attribute_lookup_dry_run_target_entity(cli, unique, ephemeral_solution):
    # Resolvable target (a real system entity) on a system host entity.
    r = cli([
        "--dry-run", "--json", "metadata", "add-attribute", "account",
        "--kind", "lookup",
        "--schema-name", f"new_E2eRefProbe{unique}",
        "--display", "E2E Ref Probe",
        "--target-entity", "contact",
        "--no-publish",
        "--solution", ephemeral_solution,
    ])
    assert r.returncode == 0, r.stderr
    env = json.loads(r.stdout)
    assert env["ok"] is True
    assert _refs(env)["target_entity"] is True


@covers("metadata add-attribute")
def test_add_attribute_lookup_dry_run_flags_dangling_target(cli, unique, ephemeral_solution):
    r = cli([
        "--dry-run", "--json", "metadata", "add-attribute", "account",
        "--kind", "lookup",
        "--schema-name", f"new_E2eRefProbe{unique}",
        "--display", "E2E Ref Probe",
        "--target-entity", _GHOST_ENTITY,
        "--no-publish",
        "--solution", ephemeral_solution,
    ])
    assert r.returncode == 0, r.stderr
    env = json.loads(r.stdout)
    assert env["ok"] is True
    assert _refs(env)["target_entity"] is False
    assert any(_GHOST_ENTITY in w for w in env["meta"]["warnings"])


@covers("metadata add-attribute")
def test_add_attribute_picklist_dry_run_flags_dangling_optionset(cli, unique, ephemeral_solution):
    r = cli([
        "--dry-run", "--json", "metadata", "add-attribute", "account",
        "--kind", "picklist",
        "--schema-name", f"new_E2eRefProbe{unique}",
        "--display", "E2E Ref Probe",
        "--optionset-name", _GHOST_OPTIONSET,
        "--no-publish",
        "--solution", ephemeral_solution,
    ])
    assert r.returncode == 0, r.stderr
    env = json.loads(r.stdout)
    assert env["ok"] is True
    assert _refs(env)["optionset"] is False
    assert any(_GHOST_OPTIONSET in w for w in env["meta"]["warnings"])


@covers("metadata add-attribute")
def test_add_attribute_calculated_dry_run_sets_source_type(cli, unique, tmp_path, ephemeral_solution):
    # Rollup/calculated turn the typed --kind column into a specialized column by
    # setting SourceType (1=calculated, 2=rollup) + FormulaDefinition on the body.
    # Dry-run echoes the would-be POST body, so we can assert the wiring without a
    # write. A live-WRITE e2e is deliberately not shipped: a successful create
    # requires valid editor-authored formula XAML, which is out of scope per #427
    # (the server rejects a hand-written body with "FormulaDefinition is not valid
    # Xaml" — the documented caveat). This dry-run case is the @covers coverage.
    f = tmp_path / "formula.xaml"
    f.write_text("<formula/>", encoding="utf-8")
    r = cli([
        "--dry-run", "--json", "metadata", "add-attribute", "account",
        "--kind", "integer",
        "--schema-name", f"new_E2eCalc{unique}",
        "--display", "E2E Calc Probe",
        "--type", "calculated",
        "--formula-file", str(f),
        "--no-publish",
        "--solution", ephemeral_solution,
    ])
    assert r.returncode == 0, r.stderr
    env = json.loads(r.stdout)
    assert env["ok"] is True
    body = env["data"]["body"]
    assert body["SourceType"] == 1
    assert body["FormulaDefinition"] == "<formula/>"


@covers("metadata add-attribute")
def test_add_attribute_rollup_dry_run_sets_source_type(cli, unique, tmp_path, ephemeral_solution):
    f = tmp_path / "formula.xaml"
    f.write_text("<formula/>", encoding="utf-8")
    r = cli([
        "--dry-run", "--json", "metadata", "add-attribute", "account",
        "--kind", "integer",
        "--schema-name", f"new_E2eRollup{unique}",
        "--display", "E2E Rollup Probe",
        "--type", "rollup",
        "--formula-file", str(f),
        "--no-publish",
        "--solution", ephemeral_solution,
    ])
    assert r.returncode == 0, r.stderr
    env = json.loads(r.stdout)
    assert env["ok"] is True
    body = env["data"]["body"]
    assert body["SourceType"] == 2
    assert body["FormulaDefinition"] == "<formula/>"


@covers("plugin register-step")
def test_register_step_dry_run_references(cli, ephemeral_solution):
    # Create (a built-in SDK message) and account (a system entity supporting it)
    # resolve on every org; the plug-in type is deliberately absent. No assembly
    # is needed because the write never happens under --dry-run.
    r = cli([
        "--dry-run", "--json", "plugin", "register-step",
        "--message", "Create",
        "--plugin-type", _GHOST_TYPE,
        "--entity", "account",
        "--solution", ephemeral_solution,
    ])
    assert r.returncode == 0, r.stderr
    env = json.loads(r.stdout)
    assert env["ok"] is True
    refs = _refs(env)
    assert refs["message"] is True       # built-in SDK message resolves
    assert refs["plugin_type"] is False  # no such type registered
    assert refs["entity"] is True        # account supports Create (filter exists)
    assert any(_GHOST_TYPE in w for w in env["meta"]["warnings"])
