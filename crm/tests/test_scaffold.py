"""Tests for `crm.core.scaffold.build_table_spec` — pure spec-builder (#90).

`build_table_spec` converts `scaffold table` CLI shorthand into an entity-spec
dict that `apply_spec` can consume directly. No backend, no IO, no network.
"""
# pyright: basic
from __future__ import annotations

import json

import pytest
import requests_mock
from click.testing import CliRunner

from crm.cli import CLIContext, cli
from crm.core import apply as apply_mod
from crm.core.scaffold import build_table_spec
from crm.utils.d365_backend import ConnectionProfile, D365Backend, D365Error


# ── Helpers ──────────────────────────────────────────────────────────────────


def _entity(spec: dict) -> dict:
    """Pull the single entity dict out of the spec."""
    return spec["entities"][0]


def _attr(spec: dict, idx: int) -> dict:
    return _entity(spec)["attributes"][idx]


# ── Happy path: multi-kind columns ───────────────────────────────────────────


def test_build_table_spec_happy_path():
    spec = build_table_spec(
        display_name="Project",
        prefix="new",
        columns=[
            "Code:string:max_length=50",
            "Notes:memo",
            "Count:integer",
            "Owner:lookup:target_entity=account",
            "Status:picklist:optionset_name=new_status",
        ],
    )
    ent = _entity(spec)
    assert ent["schema_name"] == "new_Project"
    assert ent["display_name"] == "Project"
    assert ent["ownership"] == "UserOwned"
    assert "display_collection_name" not in ent
    assert len(ent["attributes"]) == 5

    code = _attr(spec, 0)
    assert code["kind"] == "string"
    assert code["schema_name"] == "new_Code"
    assert code["display_name"] == "Code"
    assert code["max_length"] == 50

    notes = _attr(spec, 1)
    assert notes["kind"] == "memo"
    assert notes["schema_name"] == "new_Notes"
    assert notes["max_length"] == 2000  # default

    count = _attr(spec, 2)
    assert count["kind"] == "integer"
    assert count["schema_name"] == "new_Count"
    assert "max_length" not in count

    owner = _attr(spec, 3)
    assert owner["kind"] == "lookup"
    assert owner["schema_name"] == "new_Owner"
    assert owner["target_entity"] == "account"

    status = _attr(spec, 4)
    assert status["kind"] == "picklist"
    assert status["schema_name"] == "new_Status"
    assert status["optionset_name"] == "new_status"
    assert "options" not in status


# ── string / memo default max_length + override ──────────────────────────────


def test_string_default_max_length():
    spec = build_table_spec(display_name="T", prefix="x", columns=["Name:string"])
    assert _attr(spec, 0)["max_length"] == 100


def test_memo_default_max_length():
    spec = build_table_spec(display_name="T", prefix="x", columns=["Body:memo"])
    assert _attr(spec, 0)["max_length"] == 2000


def test_string_explicit_max_length_overrides_default():
    spec = build_table_spec(display_name="T", prefix="x", columns=["Name:string:max_length=200"])
    assert _attr(spec, 0)["max_length"] == 200


def test_memo_explicit_max_length_overrides_default():
    spec = build_table_spec(display_name="T", prefix="x", columns=["Body:memo:max_length=500"])
    assert _attr(spec, 0)["max_length"] == 500


# ── Optional opts: required, description ─────────────────────────────────────


@pytest.mark.parametrize("level", ["ApplicationRequired", "Recommended", "None"])
def test_required_opt_parsed_correctly(level):
    spec = build_table_spec(
        display_name="T", prefix="x",
        columns=[f"Name:string:required={level}"],
    )
    assert _attr(spec, 0)["required"] == level


def test_description_opt_parsed():
    spec = build_table_spec(
        display_name="T", prefix="x",
        columns=["Name:string:description=A short text field"],
    )
    assert _attr(spec, 0)["description"] == "A short text field"


def test_target_entity_opt_parsed():
    spec = build_table_spec(
        display_name="T", prefix="x",
        columns=["Owner:lookup:target_entity=systemuser"],
    )
    assert _attr(spec, 0)["target_entity"] == "systemuser"


def test_optionset_name_opt_parsed():
    spec = build_table_spec(
        display_name="T", prefix="x",
        columns=["Stage:picklist:optionset_name=my_stage"],
    )
    assert _attr(spec, 0)["optionset_name"] == "my_stage"


def test_multiselect_optionset_name_parsed():
    spec = build_table_spec(
        display_name="T", prefix="x",
        columns=["Tags:multiselect:optionset_name=my_tags"],
    )
    assert _attr(spec, 0)["optionset_name"] == "my_tags"


# ── Multi-word display → PascalCase schema ───────────────────────────────────


def test_multiword_entity_pascal_schema():
    spec = build_table_spec(
        display_name="Project Task",
        prefix="new",
        columns=[],
    )
    ent = _entity(spec)
    assert ent["schema_name"] == "new_ProjectTask"
    assert ent["display_name"] == "Project Task"


def test_multiword_column_pascal_schema():
    spec = build_table_spec(
        display_name="T", prefix="new",
        columns=["Project Task:string"],
    )
    assert _attr(spec, 0)["schema_name"] == "new_ProjectTask"
    assert _attr(spec, 0)["display_name"] == "Project Task"


def test_hyphenated_display_pascal():
    """Hyphens are word separators — each word is Pascal-cased."""
    spec = build_table_spec(
        display_name="T", prefix="new",
        columns=["Due-Date:datetime"],
    )
    assert _attr(spec, 0)["schema_name"] == "new_DueDate"


# ── Explicit schema_name honored verbatim ────────────────────────────────────


def test_explicit_schema_name_honored():
    spec = build_table_spec(
        display_name="Widget",
        prefix="new",
        schema_name="contoso_Widget",
        columns=[],
    )
    assert _entity(spec)["schema_name"] == "contoso_Widget"


def test_display_collection_included_when_given():
    spec = build_table_spec(
        display_name="Project",
        prefix="new",
        display_collection="Projects",
        columns=[],
    )
    assert _entity(spec)["display_collection_name"] == "Projects"


def test_display_collection_absent_when_not_given():
    spec = build_table_spec(display_name="Project", prefix="new", columns=[])
    assert "display_collection_name" not in _entity(spec)


def test_ownership_passed_through():
    spec = build_table_spec(
        display_name="T", prefix="x",
        ownership="OrganizationOwned",
        columns=[],
    )
    assert _entity(spec)["ownership"] == "OrganizationOwned"


def test_invalid_ownership_raises():
    with pytest.raises(D365Error, match="ownership must be one of"):
        build_table_spec(display_name="T", prefix="x", ownership="OrgOwned", columns=[])


def test_target_entity_on_non_lookup_raises():
    with pytest.raises(D365Error, match="target_entity is only valid for lookup"):
        build_table_spec(display_name="T", prefix="x",
                         columns=["Name:string:target_entity=account"])


def test_optionset_name_on_non_picklist_raises():
    with pytest.raises(D365Error, match="optionset_name is only valid"):
        build_table_spec(display_name="T", prefix="x",
                         columns=["Amount:money:optionset_name=new_set"])


# ── Empty columns list ────────────────────────────────────────────────────────


def test_empty_columns_produces_empty_attributes():
    spec = build_table_spec(display_name="Empty", prefix="new", columns=[])
    assert _entity(spec)["attributes"] == []


# ── Error: unknown kind ───────────────────────────────────────────────────────


def test_error_unknown_kind():
    with pytest.raises(D365Error, match="unknown kind"):
        build_table_spec(display_name="T", prefix="x", columns=["Name:frobnicate"])


# ── Error: missing kind (no second segment) ───────────────────────────────────


def test_error_missing_kind():
    with pytest.raises(D365Error, match="kind"):
        build_table_spec(display_name="T", prefix="x", columns=["NameOnly"])


# ── Error: empty display name ─────────────────────────────────────────────────


def test_error_empty_column_display():
    with pytest.raises(D365Error, match="empty"):
        build_table_spec(display_name="T", prefix="x", columns=[":string"])


# ── Error: unknown opt key ────────────────────────────────────────────────────


def test_error_unknown_opt_key():
    with pytest.raises(D365Error, match="unknown opt"):
        build_table_spec(display_name="T", prefix="x", columns=["Name:string:bogus=hi"])


# ── Error: malformed opt (no '=') ────────────────────────────────────────────


def test_error_malformed_opt_no_equals():
    with pytest.raises(D365Error, match="malformed"):
        build_table_spec(display_name="T", prefix="x", columns=["Name:string:noequalssign"])


# ── Error: non-int max_length ─────────────────────────────────────────────────


def test_error_non_int_max_length():
    with pytest.raises(D365Error, match="max_length"):
        build_table_spec(display_name="T", prefix="x", columns=["Name:string:max_length=abc"])


def test_error_zero_max_length():
    with pytest.raises(D365Error, match="max_length"):
        build_table_spec(display_name="T", prefix="x", columns=["Name:string:max_length=0"])


# ── Error: bad required value ─────────────────────────────────────────────────


def test_error_bad_required_value():
    with pytest.raises(D365Error, match="required"):
        build_table_spec(display_name="T", prefix="x", columns=["Name:string:required=bad"])


# ── Error: lookup without target_entity ──────────────────────────────────────


def test_error_lookup_without_target_entity():
    with pytest.raises(D365Error, match="target_entity"):
        build_table_spec(display_name="T", prefix="x", columns=["Owner:lookup"])


# ── Error: picklist without optionset_name ────────────────────────────────────


def test_error_picklist_without_optionset_name():
    with pytest.raises(D365Error, match="optionset_name"):
        build_table_spec(display_name="T", prefix="x", columns=["Stage:picklist"])


# ── No-drift cross-check: validate_spec accepts the output ───────────────────


def test_no_drift_validate_spec_accepts_multi_kind_output():
    """apply.validate_spec must not raise for a representative multi-kind spec."""
    spec = build_table_spec(
        display_name="Contoso Project",
        prefix="contoso",
        display_collection="Contoso Projects",
        columns=[
            "Code:string",
            "Notes:memo",
            "Count:integer",
            "Budget:money",
            "Active:boolean",
            "DueDate:datetime",
            "Owner:lookup:target_entity=account",
            "Status:picklist:optionset_name=contoso_status",
        ],
    )
    # Must not raise — this is the drift guard.
    apply_mod.validate_spec(spec)


# ── Error: max_length on non-string/memo kinds ────────────────────────────────


def test_error_max_length_on_money():
    with pytest.raises(D365Error, match="max_length is only valid"):
        build_table_spec(display_name="T", prefix="x", columns=["Amount:money:max_length=10"])


# ── e2e: scaffold table CLI command ─────────────────────────────────────────

_ENT_ID = "33333333-3333-3333-3333-333333333333"
_ATTR_ID = "55555555-5555-5555-5555-555555555555"
_REL_ID = "66666666-6666-6666-6666-666666666666"

_CONTOSO_PROFILE = ConnectionProfile(
    name="contoso",
    url="https://crm.contoso.local/contoso",
    domain="CONTOSO",
    username="alice",
    api_version="v9.2",
    verify_ssl=False,
    publisher_prefix="contoso",
)


@pytest.fixture
def backend() -> D365Backend:
    return D365Backend(_CONTOSO_PROFILE, password="pw", dry_run=False)


@pytest.fixture
def dry_backend() -> D365Backend:
    return D365Backend(_CONTOSO_PROFILE, password="pw", dry_run=True)


def _mock_entity_create(m, backend, *, schema="contoso_Project",
                        logical="contoso_project", exists=False, otc=10112):
    """Mock entity LogicalName existence GET + 204 create + readback.

    The LogicalName GET serves a sequence: first call is the create-time
    existence probe. scaffold never emits views, so the second (ObjectTypeCode
    readback) response is currently unused — but mirror apply's helper exactly
    so the mock stays correct if scaffold ever grows a views phase.
    """
    ent_url = backend.url_for(f"EntityDefinitions({_ENT_ID})")
    record = {"LogicalName": logical, "SchemaName": schema,
              "EntitySetName": logical + "s"}
    probe = {"json": record} if exists else {"status_code": 404}
    otc_resp = {"json": {"ObjectTypeCode": otc} if otc is not None else {}}
    m.get(backend.url_for(f"EntityDefinitions(LogicalName='{logical}')"), [probe, otc_resp])
    m.post(backend.url_for("EntityDefinitions"), status_code=204,
           headers={"OData-EntityId": ent_url})
    m.get(ent_url, json=record)


def _mock_attribute_create(m, backend, *, entity="contoso_project",
                           logical, schema, attr_type="String", exists=False):
    """Mock a non-lookup attribute existence GET + 204 create + readback."""
    attr_url = backend.url_for(
        f"EntityDefinitions(LogicalName='{entity}')/Attributes({_ATTR_ID})")
    probe = backend.url_for(
        f"EntityDefinitions(LogicalName='{entity}')/Attributes(LogicalName='{logical}')")
    if exists:
        m.get(probe, json={"LogicalName": logical, "SchemaName": schema,
                           "AttributeType": attr_type})
    else:
        m.get(probe, status_code=404)
    m.post(backend.url_for(f"EntityDefinitions(LogicalName='{entity}')/Attributes"),
           status_code=204, headers={"OData-EntityId": attr_url})
    m.get(attr_url, json={"LogicalName": logical, "SchemaName": schema,
                          "AttributeType": attr_type})


def _mock_one_to_many(m, backend, *, schema, exists=False):
    """Mock a one-to-many relationship existence GET + 204 create + readback."""
    rel_url = backend.url_for(f"RelationshipDefinitions({_REL_ID})")
    probe = backend.url_for(f"RelationshipDefinitions(SchemaName='{schema}')")
    if exists:
        m.get(probe, json={"SchemaName": schema})
    else:
        m.get(probe, status_code=404)
    m.post(backend.url_for("RelationshipDefinitions"), status_code=204,
           headers={"OData-EntityId": rel_url})
    m.get(rel_url + "/Microsoft.Dynamics.CRM.OneToManyRelationshipMetadata",
          json={"SchemaName": schema, "ReferencingAttribute": "contoso_projectid"})


def _publish_hits(m, backend):
    target = backend.url_for("PublishAllXml")
    return [r for r in m.request_history if r.url == target]


def _kinds(entries):
    return [e["kind"] for e in entries]


def _monkeypatch_profile(monkeypatch, profile=_CONTOSO_PROFILE):
    """Patch _active_profile in the scaffold command module and CLIContext.backend."""
    monkeypatch.setattr(
        "crm.commands.scaffold._active_profile",
        lambda ctx: profile,
    )


# Test 1: create + columns publishes once
def test_e2e_scaffold_table_creates_entity_and_columns(backend, monkeypatch):
    _monkeypatch_profile(monkeypatch)
    monkeypatch.setattr(CLIContext, "backend", lambda self: backend)

    with requests_mock.Mocker() as m:
        _mock_entity_create(m, backend)
        _mock_attribute_create(m, backend, logical="contoso_code",
                               schema="contoso_Code", attr_type="String")
        # lookup column creates a one-to-many relationship
        _mock_one_to_many(m, backend, schema="contoso_project_contoso_owner")
        m.post(backend.url_for("PublishAllXml"), status_code=204)

        result = CliRunner().invoke(cli, [
            "--json", "scaffold", "table", "Project",
            "--column", "Code:string:max_length=100",
            "--column", "Owner:lookup:target_entity=systemuser",
        ])

    assert result.exit_code == 0, result.output
    env = json.loads(result.output)
    assert env["ok"] is True
    assert env["meta"]["staged"] is False
    kinds = _kinds(env["data"]["applied"])
    assert kinds == ["entity", "attribute", "attribute"]
    names = [e["name"] for e in env["data"]["applied"]]
    assert "contoso_Project" in names
    assert "contoso_Code" in names
    # The lookup column's applied entry is keyed off the attribute schema
    # (contoso_Owner), not the underlying relationship schema.
    assert "contoso_Owner" in names
    assert len(_publish_hits(m, backend)) == 1


# Test 2: dry-run preview — no creates, planned reported
def test_e2e_scaffold_table_dry_run_greenfield(dry_backend, monkeypatch):
    _monkeypatch_profile(monkeypatch)
    monkeypatch.setattr(CLIContext, "backend", lambda self: dry_backend)

    with requests_mock.Mocker() as m:
        # The forced-real entity existence GET fires under dry-run...
        m.get(
            dry_backend.url_for("EntityDefinitions(LogicalName='contoso_project')"),
            status_code=404,
        )
        # ...as does the lookup column's target-entity reference probe (#281).
        m.get(
            dry_backend.url_for("EntityDefinitions(LogicalName='systemuser')"),
            json={"MetadataId": "77777777-7777-7777-7777-777777777777"},
        )

        result = CliRunner().invoke(cli, [
            "--dry-run", "--json", "scaffold", "table", "Project",
            "--column", "Code:string:max_length=100",
            "--column", "Owner:lookup:target_entity=systemuser",
        ])

    assert result.exit_code == 0, result.output
    env = json.loads(result.output)
    assert env["ok"] is True
    assert env["data"]["applied"] == []
    assert _kinds(env["data"]["planned"]) == ["entity", "attribute", "attribute"]
    assert _publish_hits(m, dry_backend) == []
    # The lookup column's target entity is resolved and reported as existing.
    refs = env["data"]["references"]
    assert refs == [{"kind": "target_entity", "value": "systemuser", "_exists": True}]
    # A resolvable reference adds no reference-not-found warning. (An unrelated
    # solution-resolution advisory may or may not be present depending on the
    # active profile's default_solution, so assert on the reference channel only.)
    assert not any(
        "reference not found" in w for w in env["meta"].get("warnings", []))


# Test 2b: dry-run with a dangling reference — reported + warned, never written
def test_e2e_scaffold_table_dry_run_dangling_optionset(dry_backend, monkeypatch):
    _monkeypatch_profile(monkeypatch)
    monkeypatch.setattr(CLIContext, "backend", lambda self: dry_backend)

    with requests_mock.Mocker() as m:
        m.get(
            dry_backend.url_for("EntityDefinitions(LogicalName='contoso_project')"),
            status_code=404,
        )
        # The picklist column names an option set that does not exist.
        m.get(
            dry_backend.url_for("GlobalOptionSetDefinitions(Name='ghost_set')"),
            status_code=404,
        )

        result = CliRunner().invoke(cli, [
            "--dry-run", "--json", "scaffold", "table", "Project",
            "--column", "Status:picklist:optionset_name=ghost_set",
        ])

    assert result.exit_code == 0, result.output
    env = json.loads(result.output)
    assert env["ok"] is True
    assert env["meta"]["dry_run"] is True
    assert env["data"]["references"] == [
        {"kind": "optionset", "value": "ghost_set", "_exists": False}]
    # The dangling option set is named in the warnings channel (alongside any
    # unrelated solution-resolution advisory).
    assert "reference not found: optionset='ghost_set'" in env["meta"]["warnings"]
    # No write was attempted (dry-run): nothing applied, nothing published.
    assert env["data"]["applied"] == []
    assert _publish_hits(m, dry_backend) == []


# Test 3: stage-only — creates but no publish, staged=True
def test_e2e_scaffold_table_stage_only(backend, monkeypatch):
    _monkeypatch_profile(monkeypatch)
    monkeypatch.setattr(CLIContext, "backend", lambda self: backend)

    with requests_mock.Mocker() as m:
        _mock_entity_create(m, backend)
        _mock_attribute_create(m, backend, logical="contoso_code",
                               schema="contoso_Code", attr_type="String")
        _mock_one_to_many(m, backend, schema="contoso_project_contoso_owner")
        m.post(backend.url_for("PublishAllXml"), status_code=204)

        result = CliRunner().invoke(cli, [
            "--stage-only", "--json", "scaffold", "table", "Project",
            "--column", "Code:string:max_length=100",
            "--column", "Owner:lookup:target_entity=systemuser",
        ])

    assert result.exit_code == 0, result.output
    env = json.loads(result.output)
    assert env["ok"] is True
    assert env["meta"]["staged"] is True
    assert _kinds(env["data"]["applied"]) == ["entity", "attribute", "attribute"]
    assert env["data"]["planned"] == []
    assert _publish_hits(m, backend) == []


# Test 4: malformed column → clean D365Error failure, no HTTP calls
def test_e2e_scaffold_table_malformed_column_fails_clean(backend, monkeypatch):
    _monkeypatch_profile(monkeypatch)
    monkeypatch.setattr(CLIContext, "backend", lambda self: backend)

    with requests_mock.Mocker() as m:
        result = CliRunner().invoke(cli, [
            "--json", "scaffold", "table", "Project",
            "--column", "Bad:notakind",
        ])

    assert result.exit_code != 0
    env = json.loads(result.output)
    assert env["ok"] is False
    assert "notakind" in env["error"] or "kind" in env["error"]
    assert m.request_history == []


# Test 5: missing publisher prefix → UsageError (exit 2)
def test_e2e_scaffold_table_missing_prefix_is_usage_error(backend, monkeypatch):
    # Profile with no publisher prefix.
    no_prefix_profile = ConnectionProfile(
        name="noprefix",
        url="https://crm.contoso.local/contoso",
        domain="CONTOSO",
        username="alice",
        api_version="v9.2",
        verify_ssl=False,
    )
    monkeypatch.setattr(
        "crm.commands.scaffold._active_profile",
        lambda ctx: no_prefix_profile,
    )
    monkeypatch.setattr(CLIContext, "backend", lambda self: backend)

    with requests_mock.Mocker() as m:
        result = CliRunner().invoke(cli, [
            "--json", "scaffold", "table", "Project",
            "--column", "Code:string",
        ])

    assert result.exit_code == 2
    assert m.request_history == []
