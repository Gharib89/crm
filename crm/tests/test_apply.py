"""Tests for `crm apply` — declarative desired-state from a spec file (#60).

`apply_spec` orchestrates the existing metadata cores in dependency order
(publisher -> solution -> entities -> optionsets -> attributes -> relationships
-> views) with if_exists='skip', forcing stage-only and publishing once at the
end. It classifies every step into applied / skipped / planned / failed and
returns a result the thin command maps onto the {ok, data, meta} envelope.
"""
# pyright: basic
from __future__ import annotations

import base64
import json

import pytest
import requests_mock
from click.testing import CliRunner

from crm.cli import CLIContext, cli
from crm.core import apply as apply_mod
from crm.utils.d365_backend import D365Error

_GUID = "11111111-1111-1111-1111-111111111111"
_GUID2 = "22222222-2222-2222-2222-222222222222"
_ENT_ID = "33333333-3333-3333-3333-333333333333"
_OS_ID = "44444444-4444-4444-4444-444444444444"
_ATTR_ID = "55555555-5555-5555-5555-555555555555"
_REL_ID = "66666666-6666-6666-6666-666666666666"


def _mock_publisher_create(m, backend, *, exists=False):
    """Mock publisher existence GET (collection $filter) + 204 create."""
    rows = [{"publisherid": _GUID, "uniquename": "contosopub"}] if exists else []
    m.get(backend.url_for("publishers"), json={"value": rows})
    m.post(
        backend.url_for("publishers"),
        status_code=204,
        headers={"OData-EntityId": backend.url_for(f"publishers({_GUID})")},
    )


def _mock_solution_create(m, backend, *, exists=False):
    """Mock solution existence GET (collection $filter) + 204 create."""
    rows = [{"solutionid": _GUID2, "uniquename": "ContosoCore"}] if exists else []
    m.get(backend.url_for("solutions"), json={"value": rows})
    m.post(
        backend.url_for("solutions"),
        status_code=204,
        headers={"OData-EntityId": backend.url_for(f"solutions({_GUID2})")},
    )


def _mock_entity_create(m, backend, *, schema="contoso_Project", logical="contoso_project",
                        exists=False, otc: "int | None" = 10112,
                        display_name="Project", display_collection_name="Projects",
                        ownership="UserOwned"):
    """Mock entity LogicalName GET + 204 create + readback.

    For exists=False the LogicalName GET serves a sequence: the create-time
    existence probe (404), then the views phase resolving ObjectTypeCode. For
    exists=True a single full live definition serves every read — the existence
    probe, the reconcile diff (entity_info), update_entity's merge read, and the
    views OTC resolve — so a re-applied unchanged spec is a no-op. `otc=None`
    simulates an entity whose OTC is not yet readable (e.g. pre-publish greenfield).
    """
    ent_url = backend.url_for(f"EntityDefinitions({_ENT_ID})")
    record = {"LogicalName": logical, "SchemaName": schema, "EntitySetName": logical + "s"}
    name_url = backend.url_for(f"EntityDefinitions(LogicalName='{logical}')")
    if exists:
        live = {**record, "MetadataId": _ENT_ID, "OwnershipType": ownership,
                "DisplayName": _label(display_name),
                "DisplayCollectionName": _label(display_collection_name)}
        if otc is not None:
            live["ObjectTypeCode"] = otc
        m.get(name_url, json=live)
    else:
        otc_resp = {"json": {"ObjectTypeCode": otc} if otc is not None else {}}
        m.get(name_url, [{"status_code": 404}, otc_resp])
    m.post(backend.url_for("EntityDefinitions"), status_code=204,
           headers={"OData-EntityId": ent_url})
    m.get(ent_url, json=record)


def _mock_optionset_create(m, backend, *, name="contoso_priority", exists=False,
                           options=((100000000, "Low"), (100000001, "High"))):
    """Mock global option set Name-keyed GET + 204 create + readback.

    For exists=False the Name GET serves a sequence: the create-time existence
    probe (404) then `_resolve_global_optionset_id` from a referencing picklist.
    For exists=True a single full live definition (carrying live `Options`) serves
    every read — the existence probe, the reconcile diff (get_optionset), the
    picklist resolve, and the solution-component MetadataId read — so a spec whose
    options already exist reconciles to a no-op. `options` are the live (value, label)s.
    """
    os_url = backend.url_for(f"GlobalOptionSetDefinitions({_OS_ID})")
    name_url = backend.url_for(f"GlobalOptionSetDefinitions(Name='{name}')")
    full = {"Name": name, "MetadataId": _OS_ID, "IsCustomOptionSet": True,
            "Options": [{"Value": v, "Label": _label(lbl)} for v, lbl in options]}
    if exists:
        m.get(name_url, json=full)
    else:
        m.get(name_url, [{"status_code": 404}, {"json": full}])
    m.post(backend.url_for("GlobalOptionSetDefinitions"), status_code=204,
           headers={"OData-EntityId": os_url})
    m.get(os_url, json=full)


def _mock_attribute_create(m, backend, *, entity="contoso_project", logical, schema,
                           attr_type="String", exists=False,
                           display_name=None, max_length=100):
    """Mock a non-lookup attribute existence GET + 204 create + readback.

    For exists=True the un-cast probe URL carries @odata.type + DisplayName (so the
    reconcile diff sees a column matching the spec → no-op), and string/memo kinds
    also expose a typed cast GET carrying MaxLength. `display_name` defaults to the
    schema's prefix-stripped tail (e.g. contoso_Code → "Code"), matching the spec."""
    attr_url = backend.url_for(f"EntityDefinitions(LogicalName='{entity}')/Attributes({_ATTR_ID})")
    probe = backend.url_for(
        f"EntityDefinitions(LogicalName='{entity}')/Attributes(LogicalName='{logical}')")
    if exists:
        cast = f"Microsoft.Dynamics.CRM.{attr_type}AttributeMetadata"
        base = {"LogicalName": logical, "SchemaName": schema, "AttributeType": attr_type,
                "@odata.type": "#" + cast, "MetadataId": _ATTR_ID,
                "DisplayName": _label(display_name or schema.split("_", 1)[-1])}
        m.get(probe, json=base)
        if attr_type in ("String", "Memo"):
            m.get(probe + "/" + cast, json={**base, "MaxLength": max_length})
    else:
        m.get(probe, status_code=404)
    m.post(backend.url_for(f"EntityDefinitions(LogicalName='{entity}')/Attributes"),
           status_code=204, headers={"OData-EntityId": attr_url})
    m.get(attr_url, json={"LogicalName": logical, "SchemaName": schema, "AttributeType": attr_type})


def _mock_one_to_many(m, backend, *, schema, exists=False):
    """Mock a one-to-many relationship existence GET + 204 create + readback.

    exists=True delegates to `_mock_relationship_live`: the reconcile path now runs
    for an existing relationship, so the mock must serve a full live 1:N definition
    matching the canonical `_RELATIONSHIP` spec (no drift → skipped), not just a
    bare existence probe."""
    if exists:
        _mock_relationship_live(m, backend, schema=schema)
        return
    rel_url = backend.url_for(f"RelationshipDefinitions({_REL_ID})")
    probe = backend.url_for(f"RelationshipDefinitions(SchemaName='{schema}')")
    m.get(probe, status_code=404)
    m.post(backend.url_for("RelationshipDefinitions"), status_code=204,
           headers={"OData-EntityId": rel_url})
    m.get(rel_url + "/Microsoft.Dynamics.CRM.OneToManyRelationshipMetadata",
          json={"SchemaName": schema, "ReferencingAttribute": "contoso_projectid"})


def _mock_view_create(m, backend, *, name="Active Projects", sqid=_GUID, exists=False):
    """Mock view existence GET (savedqueries $filter) + 204 create + readback."""
    sq_url = backend.url_for(f"savedqueries({sqid})")
    rows = [{"savedqueryid": sqid, "name": name}] if exists else []
    m.get(backend.url_for("savedqueries"), json={"value": rows})
    m.post(backend.url_for("savedqueries"), status_code=204,
           headers={"OData-EntityId": sq_url})
    m.get(sq_url, json={"name": name, "savedqueryid": sqid})


def _publish_hits(m, backend):
    target = backend.url_for("PublishAllXml")
    return [r for r in m.request_history if r.url == target]


def _kinds(entries):
    return [e["kind"] for e in entries]


_PUBLISHER = {
    "unique_name": "contosopub",
    "friendly_name": "Contoso Publisher",
    "prefix": "contoso",
    "option_value_prefix": 10000,
}
_SOLUTION = {
    "unique_name": "ContosoCore",
    "friendly_name": "Contoso Core",
    "version": "1.0.0.0",
}
_ENTITY = {
    "schema_name": "contoso_Project",
    "display_name": "Project",
    "display_collection_name": "Projects",
    "ownership": "UserOwned",
    "primary_attr": {"schema_name": "contoso_Name", "label": "Name"},
}
_OPTIONSET = {
    "name": "contoso_priority",
    "display_name": "Priority",
    "options": [
        {"value": 100000000, "label": "Low"},
        {"value": 100000001, "label": "High"},
    ],
}
_ATTRS = [
    {"kind": "string", "schema_name": "contoso_Code", "display_name": "Code", "max_length": 100},
    {"kind": "picklist", "schema_name": "contoso_Priority", "display_name": "Priority",
     "optionset_name": "contoso_priority"},
    {"kind": "lookup", "schema_name": "contoso_Owner", "display_name": "Owner",
     "target_entity": "systemuser"},
]
_RELATIONSHIP = {
    "schema_name": "contoso_project_task",
    "referenced_entity": "contoso_project",
    "referencing_entity": "contoso_task",
    "lookup_schema": "contoso_ProjectId",
    "lookup_display": "Project",
}
_VIEW = {"name": "Active Projects", "columns": ["contoso_name", "contoso_code"]}
_FULL_SPEC = {
    "publisher": _PUBLISHER,
    "solution": _SOLUTION,
    "optionsets": [_OPTIONSET],
    "entities": [{**_ENTITY, "attributes": _ATTRS,
                  "relationships": [_RELATIONSHIP], "views": [_VIEW]}],
}
_FULL_KINDS = [
    "publisher", "solution", "entity", "optionset",
    "attribute", "attribute", "attribute", "relationship", "view",
]


# ── Tracer: publisher-only spec stands up a publisher ───────────────────────


def test_apply_publisher_only_is_applied(backend):
    spec = {
        "publisher": {
            "unique_name": "contosopub",
            "friendly_name": "Contoso Publisher",
            "prefix": "contoso",
            "option_value_prefix": 10000,
        }
    }
    with requests_mock.Mocker() as m:
        _mock_publisher_create(m, backend)
        m.post(backend.url_for("PublishAllXml"), status_code=204)
        res = apply_mod.apply_spec(backend, spec, stage_only=False)
    assert res["ok"] is True
    assert _kinds(res["applied"]) == ["publisher"]
    assert res["applied"][0]["name"] == "contosopub"
    assert res["skipped"] == []
    assert res["planned"] == []
    assert res["failed"] == []
    assert res["staged"] is False


def test_apply_publisher_already_exists_is_skipped(backend):
    spec = {"publisher": _PUBLISHER}
    with requests_mock.Mocker() as m:
        _mock_publisher_create(m, backend, exists=True)
        res = apply_mod.apply_spec(backend, spec, stage_only=False)
    assert _kinds(res["skipped"]) == ["publisher"]
    assert res["applied"] == []
    assert res["ok"] is True


# ── Slice: solution after publisher, in dependency order ────────────────────


def test_apply_publisher_then_solution_created_in_order(backend):
    spec = {"publisher": _PUBLISHER, "solution": _SOLUTION}
    with requests_mock.Mocker() as m:
        _mock_publisher_create(m, backend)
        _mock_solution_create(m, backend)
        m.post(backend.url_for("PublishAllXml"), status_code=204)
        res = apply_mod.apply_spec(backend, spec, stage_only=False)
    assert _kinds(res["applied"]) == ["publisher", "solution"]
    assert res["applied"][1]["name"] == "ContosoCore"
    assert res["ok"] is True


# ── Slice: entity created after publisher/solution ──────────────────────────


def test_apply_creates_entity_after_publisher_solution(backend):
    spec = {"publisher": _PUBLISHER, "solution": _SOLUTION, "entities": [_ENTITY]}
    with requests_mock.Mocker() as m:
        _mock_publisher_create(m, backend)
        _mock_solution_create(m, backend)
        _mock_entity_create(m, backend)
        m.post(backend.url_for("PublishAllXml"), status_code=204)
        res = apply_mod.apply_spec(backend, spec, stage_only=False)
    assert _kinds(res["applied"]) == ["publisher", "solution", "entity"]
    assert res["applied"][2]["name"] == "contoso_Project"
    assert res["ok"] is True


# ── Slice: global option set created (after entity, before attributes) ───────


def test_apply_creates_global_optionset(backend):
    spec = {
        "publisher": _PUBLISHER, "solution": _SOLUTION,
        "entities": [_ENTITY], "optionsets": [_OPTIONSET],
    }
    with requests_mock.Mocker() as m:
        _mock_publisher_create(m, backend)
        _mock_solution_create(m, backend)
        _mock_entity_create(m, backend)
        _mock_optionset_create(m, backend)
        m.post(backend.url_for("PublishAllXml"), status_code=204)
        res = apply_mod.apply_spec(backend, spec, stage_only=False)
    assert _kinds(res["applied"]) == ["publisher", "solution", "entity", "optionset"]
    assert res["applied"][3]["name"] == "contoso_priority"
    assert res["ok"] is True


# ── Slice: attributes (string, picklist->optionset, lookup->relationship) ───


def test_apply_forwards_source_type_and_formula_on_create(backend):
    # A calculated column in the spec must reach add_attribute as SourceType=1 +
    # FormulaDefinition so a fresh apply re-creates the formula (#554).
    calc = {"kind": "decimal", "schema_name": "contoso_Total", "display_name": "Total",
            "precision": 2, "source_type": "calculated",
            "formula_definition": "<Formula>x</Formula>"}
    spec = {"publisher": _PUBLISHER, "solution": _SOLUTION,
            "entities": [{**_ENTITY, "attributes": [calc]}]}
    with requests_mock.Mocker() as m:
        _mock_publisher_create(m, backend)
        _mock_solution_create(m, backend)
        _mock_entity_create(m, backend)
        _mock_attribute_create(m, backend, logical="contoso_total", schema="contoso_Total",
                               attr_type="Decimal")
        m.post(backend.url_for("PublishAllXml"), status_code=204)
        res = apply_mod.apply_spec(backend, spec, stage_only=False)
    attr_post = backend.url_for("EntityDefinitions(LogicalName='contoso_project')/Attributes")
    body = next(json.loads(r.text) for r in m.request_history
                if r.method == "POST" and r.url == attr_post)
    assert body["SourceType"] == 1
    assert body["FormulaDefinition"] == "<Formula>x</Formula>"
    assert res["ok"] is True


def test_apply_reapply_calculated_reports_no_drift(backend):
    # AC#3 round-trip: re-applying an exported calc column that already exists
    # converges to skipped — source_type/formula_definition ride through the spec
    # but are not reconciled, so an unchanged export reports zero drift (#554).
    calc = {"kind": "decimal", "schema_name": "contoso_Total", "display_name": "Total",
            "precision": 2, "source_type": "calculated",
            "formula_definition": "<Formula>x</Formula>"}
    spec = {"entities": [{**_ENTITY, "attributes": [calc]}]}
    with requests_mock.Mocker() as m:
        _mock_entity_create(m, backend, exists=True)
        _mock_attribute_create(m, backend, logical="contoso_total", schema="contoso_Total",
                               attr_type="Decimal", exists=True)
        m.post(backend.url_for("PublishAllXml"), status_code=204)
        res = apply_mod.apply_spec(backend, spec, stage_only=False)
    assert "contoso_Total" in [e["name"] for e in res["skipped"]]
    assert res["ok"] is True


def test_apply_creates_attributes_of_each_kind(backend):
    entity = {**_ENTITY, "attributes": _ATTRS}
    spec = {
        "publisher": _PUBLISHER, "solution": _SOLUTION,
        "entities": [entity], "optionsets": [_OPTIONSET],
    }
    with requests_mock.Mocker() as m:
        _mock_publisher_create(m, backend)
        _mock_solution_create(m, backend)
        _mock_entity_create(m, backend)
        _mock_optionset_create(m, backend)
        _mock_attribute_create(m, backend, logical="contoso_code", schema="contoso_Code",
                               attr_type="String")
        _mock_attribute_create(m, backend, logical="contoso_priority", schema="contoso_Priority",
                               attr_type="Picklist")
        _mock_one_to_many(m, backend, schema="contoso_project_contoso_owner")
        m.post(backend.url_for("PublishAllXml"), status_code=204)
        res = apply_mod.apply_spec(backend, spec, stage_only=False)
    assert _kinds(res["applied"]) == [
        "publisher", "solution", "entity", "optionset",
        "attribute", "attribute", "attribute",
    ]
    assert [e["name"] for e in res["applied"][4:]] == [
        "contoso_Code", "contoso_Priority", "contoso_Owner"]
    assert res["ok"] is True


# ── Slice: explicit relationships and views (OTC auto-resolve) ──────────────


def test_apply_creates_explicit_relationship(backend):
    entity = {**_ENTITY, "relationships": [_RELATIONSHIP]}
    spec = {"publisher": _PUBLISHER, "solution": _SOLUTION, "entities": [entity]}
    with requests_mock.Mocker() as m:
        _mock_publisher_create(m, backend)
        _mock_solution_create(m, backend)
        _mock_entity_create(m, backend)
        _mock_one_to_many(m, backend, schema="contoso_project_task")
        m.post(backend.url_for("PublishAllXml"), status_code=204)
        res = apply_mod.apply_spec(backend, spec, stage_only=False)
    assert _kinds(res["applied"]) == ["publisher", "solution", "entity", "relationship"]
    assert res["applied"][3]["name"] == "contoso_project_task"
    assert res["ok"] is True


def test_apply_creates_view_resolving_otc(backend):
    entity = {**_ENTITY, "views": [_VIEW]}
    spec = {"publisher": _PUBLISHER, "solution": _SOLUTION, "entities": [entity]}
    with requests_mock.Mocker() as m:
        _mock_publisher_create(m, backend)
        _mock_solution_create(m, backend)
        _mock_entity_create(m, backend, otc=10112)
        _mock_view_create(m, backend)
        m.post(backend.url_for("PublishAllXml"), status_code=204)
        res = apply_mod.apply_spec(backend, spec, stage_only=False)
    assert _kinds(res["applied"]) == ["publisher", "solution", "entity", "view"]
    assert res["applied"][3]["name"] == "Active Projects"
    assert res["ok"] is True


def test_apply_view_planned_when_otc_unresolved(backend):
    entity = {**_ENTITY, "views": [_VIEW]}
    spec = {"publisher": _PUBLISHER, "solution": _SOLUTION, "entities": [entity]}
    with requests_mock.Mocker() as m:
        _mock_publisher_create(m, backend)
        _mock_solution_create(m, backend)
        _mock_entity_create(m, backend, otc=None)
        m.post(backend.url_for("PublishAllXml"), status_code=204)
        res = apply_mod.apply_spec(backend, spec, stage_only=False)
    assert _kinds(res["planned"]) == ["view"]
    assert _kinds(res["applied"]) == ["publisher", "solution", "entity"]
    assert res["ok"] is True


# ── Slice: publish once at the end / stage-only suppresses publish ───────────


def test_apply_publishes_once_at_end(backend):
    spec = {"publisher": _PUBLISHER, "solution": _SOLUTION, "entities": [_ENTITY]}
    with requests_mock.Mocker() as m:
        _mock_publisher_create(m, backend)
        _mock_solution_create(m, backend)
        _mock_entity_create(m, backend)
        m.post(backend.url_for("PublishAllXml"), status_code=204)
        res = apply_mod.apply_spec(backend, spec, stage_only=False)
    assert len(_publish_hits(m, backend)) == 1
    assert res["staged"] is False
    assert res["ok"] is True


def test_apply_stage_only_suppresses_publish_and_marks_staged(backend):
    spec = {"publisher": _PUBLISHER, "solution": _SOLUTION, "entities": [_ENTITY]}
    with requests_mock.Mocker() as m:
        _mock_publisher_create(m, backend)
        _mock_solution_create(m, backend)
        _mock_entity_create(m, backend)
        m.post(backend.url_for("PublishAllXml"), status_code=204)
        res = apply_mod.apply_spec(backend, spec, stage_only=True)
    assert _publish_hits(m, backend) == []
    assert res["staged"] is True
    assert res["ok"] is True


def test_apply_nothing_applied_skips_publish(backend):
    spec = {"publisher": _PUBLISHER}
    with requests_mock.Mocker() as m:
        _mock_publisher_create(m, backend, exists=True)
        m.post(backend.url_for("PublishAllXml"), status_code=204)
        res = apply_mod.apply_spec(backend, spec, stage_only=False)
    assert _publish_hits(m, backend) == []
    assert _kinds(res["skipped"]) == ["publisher"]
    assert res["staged"] is False


# ── Slice: dry-run on a greenfield spec reports dependents as planned ────────


def test_apply_dry_run_greenfield_reports_dependents_planned(dry_backend):
    backend = dry_backend
    entity = {**_ENTITY, "attributes": _ATTRS,
              "relationships": [_RELATIONSHIP], "views": [_VIEW]}
    spec = {
        "publisher": _PUBLISHER, "solution": _SOLUTION,
        "entities": [entity], "optionsets": [_OPTIONSET],
    }
    with requests_mock.Mocker() as m:
        # Only forced-real existence GETs fire under dry-run; everything is absent.
        m.get(backend.url_for("publishers"), json={"value": []})
        # Greenfield: the solution does not exist yet, so prune detection finds
        # nothing to enumerate (its existence probe returns empty).
        m.get(backend.url_for("solutions"), json={"value": []})
        m.get(backend.url_for("EntityDefinitions(LogicalName='contoso_project')"),
              status_code=404)
        m.get(backend.url_for("GlobalOptionSetDefinitions(Name='contoso_priority')"),
              status_code=404)
        res = apply_mod.apply_spec(backend, spec, stage_only=False)
    assert res["ok"] is True
    assert res["applied"] == []
    assert res["skipped"] == []
    assert _publish_hits(m, backend) == []
    assert _kinds(res["planned"]) == [
        "publisher", "solution", "entity", "optionset",
        "attribute", "attribute", "attribute", "relationship", "view",
    ]


# ── Slice: idempotent re-apply (everything exists) and partial-failure ──────


def test_apply_idempotent_reapply_all_skipped(backend):
    entity = {**_ENTITY, "attributes": _ATTRS,
              "relationships": [_RELATIONSHIP], "views": [_VIEW]}
    spec = {
        "publisher": _PUBLISHER, "solution": _SOLUTION,
        "entities": [entity], "optionsets": [_OPTIONSET],
    }
    with requests_mock.Mocker() as m:
        _mock_publisher_create(m, backend, exists=True)
        _mock_solution_create(m, backend, exists=True)
        _mock_entity_create(m, backend, exists=True)
        _mock_optionset_create(m, backend, exists=True)
        _mock_attribute_create(m, backend, logical="contoso_code", schema="contoso_Code",
                               attr_type="String", exists=True)
        _mock_attribute_create(m, backend, logical="contoso_priority", schema="contoso_Priority",
                               attr_type="Picklist", exists=True)
        _mock_one_to_many(m, backend, schema="contoso_project_contoso_owner", exists=True)
        _mock_one_to_many(m, backend, schema="contoso_project_task", exists=True)
        _mock_view_create(m, backend, exists=True)
        # Pre-existing optionset: add_solution_component fires to ensure membership.
        m.post(backend.url_for("AddSolutionComponent"), json={})
        # PublishAllXml: nothing was applied so publish does not run.
        res = apply_mod.apply_spec(backend, spec, stage_only=False)
    assert res["ok"] is True
    # solution-component add is reported as skipped (pre-existed; we can't tell
    # without an extra GET whether it was already a solution member, so we don't
    # count it as applied and don't trigger a redundant publish on every re-apply).
    assert res["applied"] == []
    assert _kinds(res["skipped"]) == [
        "publisher", "solution", "entity", "optionset",
        "solution-component",
        "attribute", "attribute", "attribute", "relationship", "view",
    ]
    assert len(_publish_hits(m, backend)) == 0
    assert res["staged"] is False


def test_apply_partial_failure_aborts_and_reports(backend):
    entity = {**_ENTITY, "attributes": _ATTRS}
    spec = {"publisher": _PUBLISHER, "solution": _SOLUTION, "entities": [entity]}
    with requests_mock.Mocker() as m:
        _mock_publisher_create(m, backend)
        _mock_solution_create(m, backend)
        # Entity create fails: existence probe says absent, then the POST 500s.
        m.get(backend.url_for("EntityDefinitions(LogicalName='contoso_project')"),
              status_code=404)
        m.post(backend.url_for("EntityDefinitions"), status_code=500,
               json={"error": {"message": "boom"}})
        m.post(backend.url_for("PublishAllXml"), status_code=204)
        res = apply_mod.apply_spec(backend, spec, stage_only=False)
    assert res["ok"] is False
    assert _kinds(res["applied"]) == ["publisher", "solution"]
    assert _kinds(res["failed"]) == ["entity"]
    assert "error" in res["failed"][0]
    # Aborted before the attribute phase.
    assert res["skipped"] == []
    # Residue is staged-but-unpublished: created components are not published.
    assert _publish_hits(m, backend) == []
    assert res["staged"] is True


# ── Slice: up-front spec validation (no network on malformed input) ─────────


def test_apply_rejects_entity_missing_schema_name(backend):
    spec = {"entities": [{"display_name": "Project"}]}
    with requests_mock.Mocker() as m:
        with pytest.raises(D365Error, match="schema_name"):
            apply_mod.apply_spec(backend, spec, stage_only=False)
        assert m.request_history == []


def test_apply_rejects_unknown_attribute_kind(backend):
    spec = {"entities": [{
        "schema_name": "contoso_Project", "display_name": "Project",
        "attributes": [{"kind": "frobnicate", "schema_name": "contoso_X",
                        "display_name": "X"}],
    }]}
    with requests_mock.Mocker() as m:
        with pytest.raises(D365Error, match="kind"):
            apply_mod.apply_spec(backend, spec, stage_only=False)
        assert m.request_history == []


def test_apply_rejects_lookup_without_target_entity(backend):
    spec = {"entities": [{
        "schema_name": "contoso_Project", "display_name": "Project",
        "attributes": [{"kind": "lookup", "schema_name": "contoso_Owner",
                        "display_name": "Owner"}],
    }]}
    with requests_mock.Mocker() as m:
        with pytest.raises(D365Error, match="target_entity"):
            apply_mod.apply_spec(backend, spec, stage_only=False)
        assert m.request_history == []


def test_apply_rejects_calculated_without_formula(backend):
    spec = {"entities": [{
        "schema_name": "contoso_Project", "display_name": "Project",
        "attributes": [{"kind": "decimal", "schema_name": "contoso_Total",
                        "display_name": "Total", "source_type": "calculated"}],
    }]}
    with requests_mock.Mocker() as m:
        with pytest.raises(D365Error, match="formula_definition"):
            apply_mod.apply_spec(backend, spec, stage_only=False)
        assert m.request_history == []


def test_apply_rejects_unknown_source_type(backend):
    spec = {"entities": [{
        "schema_name": "contoso_Project", "display_name": "Project",
        "attributes": [{"kind": "decimal", "schema_name": "contoso_Total",
                        "display_name": "Total", "source_type": "wizardry",
                        "formula_definition": "<x/>"}],
    }]}
    with requests_mock.Mocker() as m:
        with pytest.raises(D365Error, match="source_type"):
            apply_mod.apply_spec(backend, spec, stage_only=False)
        assert m.request_history == []


def test_apply_rejects_calculated_on_lookup_kind(backend):
    spec = {"entities": [{
        "schema_name": "contoso_Project", "display_name": "Project",
        "attributes": [{"kind": "lookup", "schema_name": "contoso_Owner",
                        "display_name": "Owner", "target_entity": "systemuser",
                        "source_type": "rollup", "formula_definition": "<x/>"}],
    }]}
    with requests_mock.Mocker() as m:
        with pytest.raises(D365Error, match="not valid for kind"):
            apply_mod.apply_spec(backend, spec, stage_only=False)
        assert m.request_history == []


def test_apply_rejects_formula_on_simple_column(backend):
    spec = {"entities": [{
        "schema_name": "contoso_Project", "display_name": "Project",
        "attributes": [{"kind": "decimal", "schema_name": "contoso_Total",
                        "display_name": "Total", "formula_definition": "<x/>"}],
    }]}
    with requests_mock.Mocker() as m:
        with pytest.raises(D365Error, match="only valid with source_type"):
            apply_mod.apply_spec(backend, spec, stage_only=False)
        assert m.request_history == []


def test_apply_rejects_explicit_null_source_type(backend):
    # An explicit `source_type: null` must fail here — apply_spec's .get(..,'simple')
    # default only fills an ABSENT key, so a present null would reach add_attribute.
    spec = {"entities": [{
        "schema_name": "contoso_Project", "display_name": "Project",
        "attributes": [{"kind": "decimal", "schema_name": "contoso_Total",
                        "display_name": "Total", "source_type": None}],
    }]}
    with requests_mock.Mocker() as m:
        with pytest.raises(D365Error, match="source_type must be one of"):
            apply_mod.apply_spec(backend, spec, stage_only=False)
        assert m.request_history == []


def test_apply_rejects_publisher_missing_prefix(backend):
    spec = {"publisher": {"unique_name": "contosopub", "option_value_prefix": 10000}}
    with requests_mock.Mocker() as m:
        with pytest.raises(D365Error, match="prefix"):
            apply_mod.apply_spec(backend, spec, stage_only=False)
        assert m.request_history == []


def test_apply_rejects_non_list_attributes(backend):
    spec = {"entities": [{"schema_name": "contoso_Project", "display_name": "Project",
                          "attributes": {}}]}
    with requests_mock.Mocker() as m:
        with pytest.raises(D365Error, match="attributes"):
            apply_mod.apply_spec(backend, spec, stage_only=False)
        assert m.request_history == []


def test_apply_rejects_malformed_view_column(backend):
    spec = {"entities": [{
        "schema_name": "contoso_Project", "display_name": "Project",
        "views": [{"name": "V", "columns": [{"width": 100}]}],  # column missing name
    }]}
    with requests_mock.Mocker() as m:
        with pytest.raises(D365Error, match="column"):
            apply_mod.apply_spec(backend, spec, stage_only=False)
        assert m.request_history == []


def test_apply_otc_real_error_is_reported_not_swallowed(backend):
    """A non-404 error resolving ObjectTypeCode must surface, not silently plan the view."""
    entity = {**_ENTITY, "views": [_VIEW]}
    spec = {"publisher": _PUBLISHER, "solution": _SOLUTION, "entities": [entity]}
    ent_url = backend.url_for(f"EntityDefinitions({_ENT_ID})")
    record = {"LogicalName": "contoso_project", "SchemaName": "contoso_Project",
              "EntitySetName": "contoso_projects"}
    with requests_mock.Mocker() as m:
        _mock_publisher_create(m, backend)
        _mock_solution_create(m, backend)
        # entity existence probe (404) creates it; the OTC resolve then 403s.
        m.get(backend.url_for("EntityDefinitions(LogicalName='contoso_project')"),
              [{"status_code": 404},
               {"status_code": 403, "json": {"error": {"message": "forbidden"}}}])
        m.post(backend.url_for("EntityDefinitions"), status_code=204,
               headers={"OData-EntityId": ent_url})
        m.get(ent_url, json=record)
        m.post(backend.url_for("PublishAllXml"), status_code=204)
        res = apply_mod.apply_spec(backend, spec, stage_only=False)
    assert res["ok"] is False
    assert _kinds(res["failed"]) == ["view"]
    assert "error" in res["failed"][0]
    assert _publish_hits(m, backend) == []


def test_apply_rejects_non_int_option_value_prefix(backend):
    spec = {"publisher": {"unique_name": "contosopub", "prefix": "contoso",
                          "option_value_prefix": "10000"}}  # quoted in YAML
    with requests_mock.Mocker() as m:
        with pytest.raises(D365Error, match="option_value_prefix"):
            apply_mod.apply_spec(backend, spec, stage_only=False)
        assert m.request_history == []


def test_apply_rejects_non_int_optionset_value(backend):
    spec = {"optionsets": [{"name": "contoso_p", "display_name": "P",
                            "options": [{"value": "100000000", "label": "Low"}]}]}
    with requests_mock.Mocker() as m:
        with pytest.raises(D365Error, match="value"):
            apply_mod.apply_spec(backend, spec, stage_only=False)
        assert m.request_history == []


def test_apply_forwards_inline_picklist_options(backend):
    """A picklist attribute with inline options must build a local set (no global resolve)."""
    attr = {"kind": "picklist", "schema_name": "contoso_Stage", "display_name": "Stage",
            "options": [{"value": 1, "label": "New"}, {"value": 2, "label": "Done"}]}
    entity = {**_ENTITY, "attributes": [attr]}
    spec = {"publisher": _PUBLISHER, "solution": _SOLUTION, "entities": [entity]}
    with requests_mock.Mocker() as m:
        _mock_publisher_create(m, backend)
        _mock_solution_create(m, backend)
        _mock_entity_create(m, backend)
        _mock_attribute_create(m, backend, logical="contoso_stage", schema="contoso_Stage",
                               attr_type="Picklist")
        m.post(backend.url_for("PublishAllXml"), status_code=204)
        res = apply_mod.apply_spec(backend, spec, stage_only=False)
    assert _kinds(res["applied"]) == ["publisher", "solution", "entity", "attribute"]
    # inline options => no global option set resolution GET
    os_gets = [r for r in m.request_history if "GlobalOptionSetDefinitions(Name=" in r.url]
    assert os_gets == []


def test_apply_rejects_malformed_inline_attribute_options(backend):
    attr = {"kind": "picklist", "schema_name": "contoso_Stage", "display_name": "Stage",
            "options": [{"value": 1}]}  # option missing label
    spec = {"entities": [{"schema_name": "contoso_Project", "display_name": "P",
                          "attributes": [attr]}]}
    with requests_mock.Mocker() as m:
        with pytest.raises(D365Error, match="option"):
            apply_mod.apply_spec(backend, spec, stage_only=False)
        assert m.request_history == []


def test_apply_dry_run_solution_without_publisher_skips_when_exists(dry_backend):
    spec = {"solution": _SOLUTION}  # no publisher block
    with requests_mock.Mocker() as m:
        m.get(dry_backend.url_for("solutions"),
              json={"value": [{"solutionid": _GUID2, "uniquename": "ContosoCore"}]})
        # The solution exists, so dry-run prune detection lists its components.
        m.get(dry_backend.url_for("solutioncomponents"), json={"value": []})
        res = apply_mod.apply_spec(dry_backend, spec, stage_only=False)
    assert res["ok"] is True
    assert _kinds(res["skipped"]) == ["solution"]


def test_apply_dry_run_solution_without_publisher_plans_when_absent(dry_backend):
    spec = {"solution": _SOLUTION}
    with requests_mock.Mocker() as m:
        m.get(dry_backend.url_for("solutions"), json={"value": []})
        res = apply_mod.apply_spec(dry_backend, spec, stage_only=False)
    assert _kinds(res["planned"]) == ["solution"]


def test_apply_validation_accepts_all_builder_attribute_kinds():
    """Validation accepts every kind metadata_attrs supports — no drift."""
    from crm.core import metadata_attrs

    for kind in metadata_attrs.ATTRIBUTE_KINDS:
        attr = {"kind": kind, "schema_name": "contoso_X", "display_name": "X"}
        if kind == "lookup":
            attr["target_entity"] = "systemuser"
        if kind in ("picklist", "multiselect"):
            attr["optionset_name"] = "contoso_p"
        spec = {"entities": [{"schema_name": "contoso_Project", "display_name": "P",
                              "attributes": [attr]}]}
        apply_mod.validate_spec(spec)  # must not raise


# ── e2e: full CLI invocations (acceptance scenarios) ────────────────────────


def _write_spec(tmp_path, spec=_FULL_SPEC):
    """Write the spec as JSON (a valid YAML subset) to a file the CLI can read."""
    path = tmp_path / "spec.yaml"
    path.write_text(json.dumps(spec), encoding="utf-8")
    return path


def _mock_full(m, backend, *, exists):
    _mock_publisher_create(m, backend, exists=exists)
    _mock_solution_create(m, backend, exists=exists)
    _mock_entity_create(m, backend, exists=exists)
    _mock_optionset_create(m, backend, exists=exists)
    _mock_attribute_create(m, backend, logical="contoso_code", schema="contoso_Code",
                           attr_type="String", exists=exists)
    _mock_attribute_create(m, backend, logical="contoso_priority", schema="contoso_Priority",
                           attr_type="Picklist", exists=exists)
    _mock_one_to_many(m, backend, schema="contoso_project_contoso_owner", exists=exists)
    _mock_one_to_many(m, backend, schema="contoso_project_task", exists=exists)
    _mock_view_create(m, backend, exists=exists)
    if exists:
        # Pre-existing optionset: solution-component membership add fires.
        m.post(backend.url_for("AddSolutionComponent"), json={})
    m.post(backend.url_for("PublishAllXml"), status_code=204)


def test_e2e_fresh_apply_stands_up_full_table(backend, monkeypatch, tmp_path):
    monkeypatch.setattr(CLIContext, "backend", lambda self: backend)
    spec_path = _write_spec(tmp_path)
    with requests_mock.Mocker() as m:
        _mock_full(m, backend, exists=False)
        result = CliRunner().invoke(cli, ["--json", "apply", "-f", str(spec_path)])
    assert result.exit_code == 0, result.output
    env = json.loads(result.output)
    assert env["ok"] is True
    assert env["meta"]["staged"] is False
    assert _kinds(env["data"]["applied"]) == _FULL_KINDS
    assert len(_publish_hits(m, backend)) == 1


def test_e2e_idempotent_reapply_all_skipped(backend, monkeypatch, tmp_path):
    monkeypatch.setattr(CLIContext, "backend", lambda self: backend)
    spec_path = _write_spec(tmp_path)
    with requests_mock.Mocker() as m:
        _mock_full(m, backend, exists=True)
        result = CliRunner().invoke(cli, ["--json", "apply", "-f", str(spec_path)])
    assert result.exit_code == 0, result.output
    env = json.loads(result.output)
    assert env["ok"] is True
    # solution-component add is skipped (pre-existed); nothing applied → no publish.
    assert env["data"]["applied"] == []
    # solution-component phase runs after optionsets, before attributes.
    _full_with_sc = (
        _FULL_KINDS[:4] + ["solution-component"] + _FULL_KINDS[4:]
    )
    assert _kinds(env["data"]["skipped"]) == _full_with_sc
    assert len(_publish_hits(m, backend)) == 0


def test_e2e_dry_run_greenfield_plans_dependents(dry_backend, monkeypatch, tmp_path):
    monkeypatch.setattr(CLIContext, "backend", lambda self: dry_backend)
    spec_path = _write_spec(tmp_path)
    with requests_mock.Mocker() as m:
        m.get(dry_backend.url_for("publishers"), json={"value": []})
        m.get(dry_backend.url_for("solutions"), json={"value": []})  # greenfield: no prune
        m.get(dry_backend.url_for("EntityDefinitions(LogicalName='contoso_project')"),
              status_code=404)
        m.get(dry_backend.url_for("GlobalOptionSetDefinitions(Name='contoso_priority')"),
              status_code=404)
        result = CliRunner().invoke(
            cli, ["--dry-run", "--json", "apply", "-f", str(spec_path)])
    assert result.exit_code == 0, result.output
    env = json.loads(result.output)
    assert env["ok"] is True
    assert env["data"]["applied"] == []
    assert _kinds(env["data"]["planned"]) == _FULL_KINDS
    assert _publish_hits(m, dry_backend) == []


# ── precision / format_name round-trip (the forwarding fix) ─────────────────


def test_apply_decimal_attribute_with_precision_succeeds(backend):
    """A decimal attr carrying precision must apply without raising 'precision required'."""
    attr = {"kind": "decimal", "schema_name": "contoso_Amount", "display_name": "Amount",
            "precision": 2}
    entity = {**_ENTITY, "attributes": [attr]}
    spec = {"publisher": _PUBLISHER, "solution": _SOLUTION, "entities": [entity]}
    with requests_mock.Mocker() as m:
        _mock_publisher_create(m, backend)
        _mock_solution_create(m, backend)
        _mock_entity_create(m, backend)
        _mock_attribute_create(m, backend, logical="contoso_amount", schema="contoso_Amount",
                               attr_type="Decimal")
        m.post(backend.url_for("PublishAllXml"), status_code=204)
        res = apply_mod.apply_spec(backend, spec, stage_only=False)
    assert res["ok"] is True
    assert _kinds(res["applied"]) == ["publisher", "solution", "entity", "attribute"]
    attr_posts = [
        r for r in m.request_history
        if "EntityDefinitions(LogicalName='contoso_project')/Attributes" in r.url
        and r.method == "POST"
    ]
    assert len(attr_posts) == 1
    body = attr_posts[0].json()
    assert body.get("Precision") == 2


def test_apply_string_format_name_is_preserved(backend):
    """A string attr with format_name='Email' must POST FormatName={Value:'Email'}."""
    attr = {"kind": "string", "schema_name": "contoso_Email", "display_name": "Email",
            "max_length": 200, "format_name": "Email"}
    entity = {**_ENTITY, "attributes": [attr]}
    spec = {"publisher": _PUBLISHER, "solution": _SOLUTION, "entities": [entity]}
    with requests_mock.Mocker() as m:
        _mock_publisher_create(m, backend)
        _mock_solution_create(m, backend)
        _mock_entity_create(m, backend)
        _mock_attribute_create(m, backend, logical="contoso_email", schema="contoso_Email",
                               attr_type="String")
        m.post(backend.url_for("PublishAllXml"), status_code=204)
        res = apply_mod.apply_spec(backend, spec, stage_only=False)
    assert res["ok"] is True
    attr_posts = [
        r for r in m.request_history
        if "EntityDefinitions(LogicalName='contoso_project')/Attributes" in r.url
        and r.method == "POST"
    ]
    assert len(attr_posts) == 1
    body = attr_posts[0].json()
    assert body.get("FormatName") == {"Value": "Email"}


def test_apply_string_without_format_name_defaults_to_text(backend):
    """A string attr without format_name must POST FormatName={Value:'Text'} (default)."""
    attr = {"kind": "string", "schema_name": "contoso_Code", "display_name": "Code",
            "max_length": 100}
    entity = {**_ENTITY, "attributes": [attr]}
    spec = {"publisher": _PUBLISHER, "solution": _SOLUTION, "entities": [entity]}
    with requests_mock.Mocker() as m:
        _mock_publisher_create(m, backend)
        _mock_solution_create(m, backend)
        _mock_entity_create(m, backend)
        _mock_attribute_create(m, backend, logical="contoso_code", schema="contoso_Code",
                               attr_type="String")
        m.post(backend.url_for("PublishAllXml"), status_code=204)
        res = apply_mod.apply_spec(backend, spec, stage_only=False)
    assert res["ok"] is True
    attr_posts = [
        r for r in m.request_history
        if "EntityDefinitions(LogicalName='contoso_project')/Attributes" in r.url
        and r.method == "POST"
    ]
    assert len(attr_posts) == 1
    body = attr_posts[0].json()
    assert body.get("FormatName") == {"Value": "Text"}


@pytest.mark.parametrize("kind,schema,logical,attr_type,expected", [
    ("string", "contoso_Code", "contoso_code", "String", 100),
    ("memo", "contoso_Notes", "contoso_notes", "Memo", 2000),
])
def test_apply_string_memo_without_max_length_defaults_it(
    backend, kind, schema, logical, attr_type, expected,
):
    """A string/memo attr row that omits max_length must apply, POSTing the
    100/2000 default (#321) — previously errored at real apply."""
    attr = {"kind": kind, "schema_name": schema, "display_name": "Col"}
    entity = {**_ENTITY, "attributes": [attr]}
    spec = {"publisher": _PUBLISHER, "solution": _SOLUTION, "entities": [entity]}
    with requests_mock.Mocker() as m:
        _mock_publisher_create(m, backend)
        _mock_solution_create(m, backend)
        _mock_entity_create(m, backend)
        _mock_attribute_create(m, backend, logical=logical, schema=schema,
                               attr_type=attr_type)
        m.post(backend.url_for("PublishAllXml"), status_code=204)
        res = apply_mod.apply_spec(backend, spec, stage_only=False)
    assert res["ok"] is True
    attr_posts = [
        r for r in m.request_history
        if "EntityDefinitions(LogicalName='contoso_project')/Attributes" in r.url
        and r.method == "POST"
    ]
    assert len(attr_posts) == 1
    assert attr_posts[0].json().get("MaxLength") == expected


def test_apply_integer_attribute_no_precision_still_applies(backend):
    """An integer attr (precision forbidden) with precision=None must apply without error."""
    attr = {"kind": "integer", "schema_name": "contoso_Count", "display_name": "Count"}
    entity = {**_ENTITY, "attributes": [attr]}
    spec = {"publisher": _PUBLISHER, "solution": _SOLUTION, "entities": [entity]}
    with requests_mock.Mocker() as m:
        _mock_publisher_create(m, backend)
        _mock_solution_create(m, backend)
        _mock_entity_create(m, backend)
        _mock_attribute_create(m, backend, logical="contoso_count", schema="contoso_Count",
                               attr_type="Integer")
        m.post(backend.url_for("PublishAllXml"), status_code=204)
        res = apply_mod.apply_spec(backend, spec, stage_only=False)
    assert res["ok"] is True
    assert _kinds(res["applied"]) == ["publisher", "solution", "entity", "attribute"]


# ── Slice: include_referenced_optionsets — add pre-existing global to solution ─


class TestApplyIncludeReferencedOptionsets:
    _OS_META_ID = "aaaaaaaa-aaaa-aaaa-aaaa-aaaaaaaaaaaa"
    _SOL_GUID = "bbbbbbbb-bbbb-bbbb-bbbb-bbbbbbbbbbbb"

    def _spec(self):
        """Minimal spec: solution + one global optionset, no entities."""
        return {
            "solution": {"unique_name": "contoso_test"},
            "optionsets": [{
                "name": "contoso_tagset",
                "display_name": "Tag Set",
                "options": [{"value": 1, "label": "Alpha"}],
            }],
        }

    def test_preexisting_global_added_to_solution_by_default(self, backend):
        """A pre-existing optionset (skipped during create) is added to solution."""
        spec = self._spec()
        with requests_mock.Mocker() as m:
            # solutions endpoint: _solution_exists ($select=solutionid) AND
            # _require_unmanaged_solution via solution_info (no $select) both hit
            # this same base URL; requests_mock ignores query params by default.
            # Return a non-managed solution for _require_unmanaged_solution.
            m.get(backend.url_for("solutions"),
                  json={"value": [{"solutionid": self._SOL_GUID,
                                   "uniquename": "contoso_test",
                                   "ismanaged": False}]})
            # GlobalOptionSetDefinitions(Name='contoso_tagset'): a single full live
            # definition serves the create-time probe, the reconcile diff, and the
            # component-phase MetadataId read. Its live Options already contain the
            # spec's option, so reconcile is a no-op skip and this test stays focused
            # on the solution-component membership add.
            m.get(
                backend.url_for("GlobalOptionSetDefinitions(Name='contoso_tagset')"),
                json={"MetadataId": self._OS_META_ID, "Name": "contoso_tagset",
                      "Options": [{"Value": 1, "Label": _label("Alpha")}]},
            )
            add = m.post(backend.url_for("AddSolutionComponent"), json={})
            m.post(backend.url_for("PublishAllXml"), status_code=204)

            res = apply_mod.apply_spec(backend, spec, include_referenced_optionsets=True)

        assert add.called, "AddSolutionComponent was not called"
        body = add.last_request.json()
        assert body["ComponentType"] == 9
        assert body["ComponentId"] == self._OS_META_ID
        assert body["SolutionUniqueName"] == "contoso_test"
        assert res["ok"]

    def test_no_flag_skips_component_add(self, backend):
        """include_referenced_optionsets=False skips the membership phase."""
        spec = self._spec()
        with requests_mock.Mocker() as m:
            # Solution exists so create_solution skips it (no publisher resolution needed).
            m.get(backend.url_for("solutions"),
                  json={"value": [{"solutionid": self._SOL_GUID,
                                   "uniquename": "contoso_test"}]})
            m.post(backend.url_for("solutions"), status_code=204,
                   headers={"OData-EntityId": backend.url_for(f"solutions({self._SOL_GUID})")})
            # Optionset exists with the spec's option already present: create_optionset
            # skips it and reconcile is a no-op (no InsertOptionValue).
            m.get(
                backend.url_for("GlobalOptionSetDefinitions(Name='contoso_tagset')"),
                json={"MetadataId": self._OS_META_ID, "Name": "contoso_tagset",
                      "Options": [{"Value": 1, "Label": _label("Alpha")}]},
            )
            # No AddSolutionComponent mock — if it fires, NoMockAddress fails the test.
            res = apply_mod.apply_spec(backend, spec, include_referenced_optionsets=False)

        assert res["ok"]

    def test_dry_run_previews_component_add(self, dry_backend):
        """dry_run: component add is reported as planned, no HTTP beyond existence probes."""
        backend = dry_backend
        spec = self._spec()
        with requests_mock.Mocker() as m:
            # solutions probe for _solution_exists (forced-real)
            m.get(backend.url_for("solutions"), json={"value": []})
            # optionset EXISTS → would_skip=True → skipped → NOT in planned_names
            m.get(
                backend.url_for("GlobalOptionSetDefinitions(Name='contoso_tagset')"),
                json={"MetadataId": self._OS_META_ID, "Name": "contoso_tagset"},
            )
            res = apply_mod.apply_spec(backend, spec, include_referenced_optionsets=True)

        assert any(e["kind"] == "solution-component" for e in res["planned"])


# ── Convergent reconciliation: existing components are diffed, not blindly skipped ──
#
# These drive the apply_spec() seam (per project memory: assert returned buckets,
# not internal differ calls). A builder reports {skipped, exists} for a live
# component; apply_spec then reads it, diffs vs the spec, and routes it to
# updated / skipped / replace_blocked.


def _label(text):
    return {
        "UserLocalizedLabel": {"Label": text, "LanguageCode": 1033},
        "LocalizedLabels": [{"Label": text, "LanguageCode": 1033}],
    }


def _mock_entity_live(m, backend, *, logical="contoso_project", schema="contoso_Project",
                      display_name="Project", display_collection_name="Projects",
                      description=None, ownership="UserOwned"):
    """Mock an EXISTING entity. One GET matcher serves the full live definition for
    every read (target_exists probe, entity_info, update_entity's merge read);
    PUT 204 for the write."""
    url = backend.url_for(f"EntityDefinitions(LogicalName='{logical}')")
    live = {
        "MetadataId": _ENT_ID, "LogicalName": logical, "SchemaName": schema,
        "EntitySetName": logical + "s", "OwnershipType": ownership,
        "DisplayName": _label(display_name),
        "DisplayCollectionName": _label(display_collection_name),
    }
    if description is not None:
        live["Description"] = _label(description)
    m.get(url, json=live)
    m.put(url, status_code=204)


def test_apply_updates_entity_display_name_on_drift(backend):
    # Live entity displays "Old Project"; spec wants "Project" → update → updated.
    ent = {"schema_name": "contoso_Project", "display_name": "Project",
           "primary_attr": {"schema_name": "contoso_Name", "label": "Name"}}
    spec = {"entities": [ent]}
    with requests_mock.Mocker() as m:
        _mock_entity_live(m, backend, display_name="Old Project")
        m.post(backend.url_for("PublishAllXml"), status_code=204)
        res = apply_mod.apply_spec(backend, spec, stage_only=False)
    assert res["ok"] is True
    assert _kinds(res["updated"]) == ["entity"]
    assert res["applied"] == []
    assert res["skipped"] == []
    puts = [r for r in m.request_history if r.method == "PUT"]
    assert len(puts) == 1


def test_apply_blocks_entity_ownership_change(backend):
    # Spec asks for OrganizationOwned; live entity is UserOwned. Ownership is
    # immutable post-create → replace_blocked: reported, NO write, ok=false.
    ent = {"schema_name": "contoso_Project", "display_name": "Project",
           "ownership": "OrganizationOwned",
           "primary_attr": {"schema_name": "contoso_Name", "label": "Name"}}
    spec = {"entities": [ent]}
    with requests_mock.Mocker() as m:
        _mock_entity_live(m, backend, ownership="UserOwned")
        m.post(backend.url_for("PublishAllXml"), status_code=204)
        res = apply_mod.apply_spec(backend, spec, stage_only=False)
    assert res["ok"] is False
    assert _kinds(res["replace_blocked"]) == ["entity"]
    assert "reason" in res["replace_blocked"][0]
    assert res["updated"] == []
    assert res["applied"] == []
    assert [r for r in m.request_history if r.method == "PUT"] == []
    assert len(_publish_hits(m, backend)) == 0


def test_apply_entity_unchanged_is_skipped(backend):
    # Live entity already matches the spec → no-op skipped (idempotent re-run).
    ent = {"schema_name": "contoso_Project", "display_name": "Project",
           "display_collection_name": "Projects",
           "primary_attr": {"schema_name": "contoso_Name", "label": "Name"}}
    spec = {"entities": [ent]}
    with requests_mock.Mocker() as m:
        _mock_entity_live(m, backend, display_name="Project", display_collection_name="Projects")
        res = apply_mod.apply_spec(backend, spec, stage_only=False)
    assert res["ok"] is True
    assert _kinds(res["skipped"]) == ["entity"]
    assert res["updated"] == []
    assert [r for r in m.request_history if r.method == "PUT"] == []


def test_apply_command_replace_blocked_exits_nonzero(backend, monkeypatch, tmp_path):
    # The verb surfaces replace_blocked / updated / pruned buckets and exits 1
    # when a component is replace-blocked.
    import yaml
    spec = {"entities": [{
        "schema_name": "contoso_Project", "display_name": "Project",
        "ownership": "OrganizationOwned",
        "primary_attr": {"schema_name": "contoso_Name", "label": "Name"}}]}
    spec_file = tmp_path / "spec.yaml"
    spec_file.write_text(yaml.safe_dump(spec), encoding="utf-8")
    monkeypatch.setattr(CLIContext, "backend", lambda self: backend)
    with requests_mock.Mocker() as m:
        _mock_entity_live(m, backend, ownership="UserOwned")
        result = CliRunner().invoke(cli, ["--json", "apply", "-f", str(spec_file)])
    assert result.exit_code == 1
    payload = json.loads(result.output)
    assert payload["ok"] is False
    assert [e["kind"] for e in payload["data"]["replace_blocked"]] == ["entity"]
    assert "updated" in payload["data"] and "pruned" in payload["data"]


def _mock_attribute_live(m, backend, *, entity="contoso_project", logical, schema,
                         cast="Microsoft.Dynamics.CRM.StringAttributeMetadata",
                         display_name="Code", description=None,
                         required="None", max_length=None):
    """Mock an EXISTING attribute. The un-cast base GET (target_exists probe,
    attribute_info, update_attribute's type-discovery read) carries @odata.type +
    base props; the typed cast GET carries MaxLength and serves update_attribute's
    merge read. PUT 204 to the cast path."""
    base_url = backend.url_for(
        f"EntityDefinitions(LogicalName='{entity}')/Attributes(LogicalName='{logical}')")
    base = {
        "MetadataId": _ATTR_ID, "LogicalName": logical, "SchemaName": schema,
        "@odata.type": "#" + cast,
        "DisplayName": _label(display_name),
        "RequiredLevel": {"Value": required},
    }
    if description is not None:
        base["Description"] = _label(description)
    m.get(base_url, json=base)
    cast_url = base_url + "/" + cast
    typed = dict(base)
    if max_length is not None:
        typed["MaxLength"] = max_length
    m.get(cast_url, json=typed)
    m.put(cast_url, status_code=204)


def _attr_spec(attr):
    """A minimal spec: one existing entity (no-op) carrying one attribute."""
    ent = {"schema_name": "contoso_Project", "display_name": "Project",
           "attributes": [attr],
           "primary_attr": {"schema_name": "contoso_Name", "label": "Name"}}
    return {"entities": [ent]}


def test_apply_updates_attribute_required_level_on_drift(backend):
    attr = {"kind": "string", "schema_name": "contoso_Code", "display_name": "Code",
            "required": "ApplicationRequired"}
    with requests_mock.Mocker() as m:
        _mock_entity_live(m, backend, display_name="Project")
        _mock_attribute_live(m, backend, logical="contoso_code", schema="contoso_Code",
                             display_name="Code", required="None")
        m.post(backend.url_for("PublishAllXml"), status_code=204)
        res = apply_mod.apply_spec(backend, _attr_spec(attr), stage_only=False)
    assert res["ok"] is True
    assert _kinds(res["updated"]) == ["attribute"]
    assert _kinds(res["skipped"]) == ["entity"]
    assert len([r for r in m.request_history if r.method == "PUT"]) == 1


def test_apply_grows_string_max_length(backend):
    attr = {"kind": "string", "schema_name": "contoso_Code", "display_name": "Code",
            "max_length": 200}
    with requests_mock.Mocker() as m:
        _mock_entity_live(m, backend, display_name="Project")
        _mock_attribute_live(m, backend, logical="contoso_code", schema="contoso_Code",
                             display_name="Code", max_length=100)
        m.post(backend.url_for("PublishAllXml"), status_code=204)
        res = apply_mod.apply_spec(backend, _attr_spec(attr), stage_only=False)
    assert res["ok"] is True
    assert _kinds(res["updated"]) == ["attribute"]


def test_apply_does_not_shrink_string_max_length(backend):
    attr = {"kind": "string", "schema_name": "contoso_Code", "display_name": "Code",
            "max_length": 50}
    with requests_mock.Mocker() as m:
        _mock_entity_live(m, backend, display_name="Project")
        _mock_attribute_live(m, backend, logical="contoso_code", schema="contoso_Code",
                             display_name="Code", max_length=100)
        res = apply_mod.apply_spec(backend, _attr_spec(attr), stage_only=False)
    assert res["ok"] is True
    assert "attribute" in _kinds(res["skipped"])
    assert res["updated"] == []
    assert [r for r in m.request_history if r.method == "PUT"] == []


def test_apply_blocks_attribute_datatype_change(backend):
    # Spec declares a string column; live column is an integer → replace_blocked.
    attr = {"kind": "string", "schema_name": "contoso_Code", "display_name": "Code"}
    with requests_mock.Mocker() as m:
        _mock_entity_live(m, backend, display_name="Project")
        _mock_attribute_live(m, backend, logical="contoso_code", schema="contoso_Code",
                             cast="Microsoft.Dynamics.CRM.IntegerAttributeMetadata")
        m.post(backend.url_for("PublishAllXml"), status_code=204)
        res = apply_mod.apply_spec(backend, _attr_spec(attr), stage_only=False)
    assert res["ok"] is False
    assert _kinds(res["replace_blocked"]) == ["attribute"]
    assert res["updated"] == []
    assert [r for r in m.request_history if r.method == "PUT"] == []
    assert len(_publish_hits(m, backend)) == 0


def _mock_relationship_live(m, backend, *, schema, referenced="contoso_project",
                            referencing="contoso_task", referencing_attr="contoso_projectid",
                            rel_type="OneToManyRelationship", cascade=None, menu=None,
                            is_hierarchical=False, lookup_display="Project",
                            lookup_required="None", lookup_description=None):
    """Mock an EXISTING 1:N relationship + its lookup column for reconcile.

    The SchemaName GET serves the create-path existence probe, the reconcile
    base-type read, and update_relationship's MetadataId resolve. The OneToMany
    cast GET (by SchemaName) serves the reconcile diff; the cast GET (by
    MetadataId) serves update_relationship's merge base; the PUT to the un-cast
    MetadataId path is the #267 write. The relationship-backed lookup column on the
    referencing entity is mocked like _mock_attribute_live (base + Lookup cast GET +
    PUT) so the lookup half of the reconcile diffs and can be updated."""
    cast = "Microsoft.Dynamics.CRM.OneToManyRelationshipMetadata"
    live = {
        "SchemaName": schema, "MetadataId": _REL_ID, "@odata.type": "#" + cast,
        "RelationshipType": rel_type,
        "ReferencedEntity": referenced, "ReferencingEntity": referencing,
        "ReferencingAttribute": referencing_attr,
        "CascadeConfiguration": cascade or {
            "Assign": "NoCascade", "Delete": "RemoveLink", "Reparent": "NoCascade",
            "Share": "NoCascade", "Unshare": "NoCascade", "Merge": "NoCascade"},
        "AssociatedMenuConfiguration": menu or {
            "Behavior": "UseCollectionName", "Group": "Details", "Order": 10000},
        "IsHierarchical": is_hierarchical,
    }
    probe = backend.url_for(f"RelationshipDefinitions(SchemaName='{schema}')")
    rel_url = backend.url_for(f"RelationshipDefinitions({_REL_ID})")
    m.get(probe, json=live)
    m.get(probe + "/" + cast, json=live)
    m.get(rel_url + "/" + cast, json=live)
    m.put(rel_url, status_code=204)
    # The relationship-backed lookup column on the referencing entity.
    lk_cast = "Microsoft.Dynamics.CRM.LookupAttributeMetadata"
    lk_base = backend.url_for(
        f"EntityDefinitions(LogicalName='{referencing}')/Attributes(LogicalName='{referencing_attr}')")
    lk = {"MetadataId": _ATTR_ID, "LogicalName": referencing_attr,
          "@odata.type": "#" + lk_cast, "DisplayName": _label(lookup_display),
          "RequiredLevel": {"Value": lookup_required}}
    if lookup_description is not None:
        lk["Description"] = _label(lookup_description)
    m.get(lk_base, json=lk)
    m.get(lk_base + "/" + lk_cast, json=lk)
    m.put(lk_base + "/" + lk_cast, status_code=204)


def _rel_spec(rel):
    """A minimal spec: one existing entity (no-op) carrying one relationship."""
    ent = {"schema_name": "contoso_Project", "display_name": "Project",
           "relationships": [rel],
           "primary_attr": {"schema_name": "contoso_Name", "label": "Name"}}
    return {"entities": [ent]}


def _base_rel(**overrides):
    """A relationship spec block matching `_mock_relationship_live`'s live defaults
    (so it is a no-op unless an override forces drift)."""
    rel = {"schema_name": "contoso_project_task", "referenced_entity": "contoso_project",
           "referencing_entity": "contoso_task", "lookup_schema": "contoso_ProjectId",
           "lookup_display": "Project"}
    rel.update(overrides)
    return rel


def test_apply_updates_relationship_cascade_on_drift(backend):
    # Live cascade Delete=RemoveLink; spec wants Cascade → update → updated.
    with requests_mock.Mocker() as m:
        _mock_entity_live(m, backend, display_name="Project")
        _mock_relationship_live(m, backend, schema="contoso_project_task")
        m.post(backend.url_for("PublishAllXml"), status_code=204)
        res = apply_mod.apply_spec(backend, _rel_spec(_base_rel(cascade_delete="Cascade")),
                                   stage_only=False)
    assert res["ok"] is True
    assert _kinds(res["updated"]) == ["relationship"]
    assert _kinds(res["skipped"]) == ["entity"]
    # One PUT to the relationship definition (the #267 un-cast write); no lookup PUT.
    assert len([r for r in m.request_history if r.method == "PUT"]) == 1


def test_apply_updates_relationship_menu_order_on_drift(backend):
    with requests_mock.Mocker() as m:
        _mock_entity_live(m, backend, display_name="Project")
        _mock_relationship_live(m, backend, schema="contoso_project_task")
        m.post(backend.url_for("PublishAllXml"), status_code=204)
        res = apply_mod.apply_spec(backend, _rel_spec(_base_rel(menu_order=5)),
                                   stage_only=False)
    assert res["ok"] is True
    assert _kinds(res["updated"]) == ["relationship"]
    assert len([r for r in m.request_history if r.method == "PUT"]) == 1


def test_apply_updates_relationship_menu_label_on_drift(backend):
    # Live menu is UseCollectionName; spec wants a custom UseLabel menu → update.
    rel = _base_rel(menu_behavior="UseLabel", menu_label="Tasks")
    with requests_mock.Mocker() as m:
        _mock_entity_live(m, backend, display_name="Project")
        _mock_relationship_live(m, backend, schema="contoso_project_task")
        m.post(backend.url_for("PublishAllXml"), status_code=204)
        res = apply_mod.apply_spec(backend, _rel_spec(rel), stage_only=False)
    assert res["ok"] is True
    assert _kinds(res["updated"]) == ["relationship"]


def test_apply_updates_relationship_is_hierarchical_on_drift(backend):
    with requests_mock.Mocker() as m:
        _mock_entity_live(m, backend, display_name="Project")
        _mock_relationship_live(m, backend, schema="contoso_project_task")
        m.post(backend.url_for("PublishAllXml"), status_code=204)
        res = apply_mod.apply_spec(backend, _rel_spec(_base_rel(is_hierarchical=True)),
                                   stage_only=False)
    assert res["ok"] is True
    assert _kinds(res["updated"]) == ["relationship"]


def test_apply_updates_relationship_lookup_display_on_drift(backend):
    # Only the lookup column drifts → updated via update_attribute (one lookup PUT).
    with requests_mock.Mocker() as m:
        _mock_entity_live(m, backend, display_name="Project")
        _mock_relationship_live(m, backend, schema="contoso_project_task",
                                lookup_display="Project")
        m.post(backend.url_for("PublishAllXml"), status_code=204)
        res = apply_mod.apply_spec(backend, _rel_spec(_base_rel(lookup_display="Renamed")),
                                   stage_only=False)
    assert res["ok"] is True
    assert _kinds(res["updated"]) == ["relationship"]
    assert len([r for r in m.request_history if r.method == "PUT"]) == 1


def test_apply_updates_relationship_lookup_required_on_drift(backend):
    rel = _base_rel(required="ApplicationRequired")
    with requests_mock.Mocker() as m:
        _mock_entity_live(m, backend, display_name="Project")
        _mock_relationship_live(m, backend, schema="contoso_project_task",
                                lookup_required="None")
        m.post(backend.url_for("PublishAllXml"), status_code=204)
        res = apply_mod.apply_spec(backend, _rel_spec(rel), stage_only=False)
    assert res["ok"] is True
    assert _kinds(res["updated"]) == ["relationship"]


def test_apply_relationship_and_lookup_merge_into_one_updated_entry(backend):
    # Both the relationship (cascade) and its lookup (display) drift → TWO PUTs
    # (update_relationship + update_attribute) but ONE merged `updated` entry.
    rel = _base_rel(cascade_delete="Cascade", lookup_display="Renamed")
    with requests_mock.Mocker() as m:
        _mock_entity_live(m, backend, display_name="Project")
        _mock_relationship_live(m, backend, schema="contoso_project_task")
        m.post(backend.url_for("PublishAllXml"), status_code=204)
        res = apply_mod.apply_spec(backend, _rel_spec(rel), stage_only=False)
    assert res["ok"] is True
    assert _kinds(res["updated"]) == ["relationship"]
    assert len(res["updated"]) == 1  # one merged entry, not two
    assert len([r for r in m.request_history if r.method == "PUT"]) == 2


def test_apply_blocks_relationship_referencing_entity_change(backend):
    # Spec references a different referencing entity → identity divergence →
    # replace_blocked: reported, NO write, ok=false.
    rel = _base_rel(referencing_entity="contoso_other")
    with requests_mock.Mocker() as m:
        _mock_entity_live(m, backend, display_name="Project")
        _mock_relationship_live(m, backend, schema="contoso_project_task")
        m.post(backend.url_for("PublishAllXml"), status_code=204)
        res = apply_mod.apply_spec(backend, _rel_spec(rel), stage_only=False)
    assert res["ok"] is False
    assert _kinds(res["replace_blocked"]) == ["relationship"]
    assert "reason" in res["replace_blocked"][0]
    assert res["updated"] == []
    assert [r for r in m.request_history if r.method == "PUT"] == []
    assert len(_publish_hits(m, backend)) == 0


def test_apply_blocks_relationship_referenced_entity_change(backend):
    rel = _base_rel(referenced_entity="contoso_other")
    with requests_mock.Mocker() as m:
        _mock_entity_live(m, backend, display_name="Project")
        _mock_relationship_live(m, backend, schema="contoso_project_task")
        res = apply_mod.apply_spec(backend, _rel_spec(rel), stage_only=False)
    assert res["ok"] is False
    assert _kinds(res["replace_blocked"]) == ["relationship"]
    assert [r for r in m.request_history if r.method == "PUT"] == []


def test_apply_blocks_relationship_type_change_to_many_to_many(backend):
    # The live relationship matched by SchemaName is N:N, not the spec's 1:N →
    # relationship-type divergence → replace_blocked.
    with requests_mock.Mocker() as m:
        _mock_entity_live(m, backend, display_name="Project")
        _mock_relationship_live(m, backend, schema="contoso_project_task",
                                rel_type="ManyToManyRelationship")
        res = apply_mod.apply_spec(backend, _rel_spec(_base_rel()), stage_only=False)
    assert res["ok"] is False
    assert _kinds(res["replace_blocked"]) == ["relationship"]
    assert "one-to-many" in res["replace_blocked"][0]["reason"]
    assert [r for r in m.request_history if r.method == "PUT"] == []


def test_apply_relationship_unchanged_is_skipped(backend):
    # Live relationship already matches the spec → idempotent no-op skipped.
    with requests_mock.Mocker() as m:
        _mock_entity_live(m, backend, display_name="Project")
        _mock_relationship_live(m, backend, schema="contoso_project_task")
        res = apply_mod.apply_spec(backend, _rel_spec(_base_rel()), stage_only=False)
    assert res["ok"] is True
    assert "relationship" in _kinds(res["skipped"])
    assert res["updated"] == []
    assert [r for r in m.request_history if r.method == "PUT"] == []


def test_apply_relationship_omitted_field_never_drifts(backend):
    # Live cascade Assign=Cascade (non-default); the spec omits cascade_assign, so
    # the unspecified field is left as-is → no drift → skipped.
    live_cascade = {"Assign": "Cascade", "Delete": "RemoveLink", "Reparent": "NoCascade",
                    "Share": "NoCascade", "Unshare": "NoCascade", "Merge": "NoCascade"}
    with requests_mock.Mocker() as m:
        _mock_entity_live(m, backend, display_name="Project")
        _mock_relationship_live(m, backend, schema="contoso_project_task",
                                cascade=live_cascade)
        res = apply_mod.apply_spec(backend, _rel_spec(_base_rel()), stage_only=False)
    assert res["ok"] is True
    assert "relationship" in _kinds(res["skipped"])
    assert [r for r in m.request_history if r.method == "PUT"] == []


def test_apply_relationship_invalid_hierarchical_toggle_fails_not_blocked(backend):
    # An invalid is_hierarchical toggle is rejected by the platform (a backend
    # error on the write), surfacing as `failed` — NOT replace_blocked.
    with requests_mock.Mocker() as m:
        _mock_entity_live(m, backend, display_name="Project")
        _mock_relationship_live(m, backend, schema="contoso_project_task")
        # The #267 un-cast PUT is rejected by the server (later registration wins).
        m.put(backend.url_for(f"RelationshipDefinitions({_REL_ID})"), status_code=400,
              json={"error": {"code": "0x80045002", "message": "invalid hierarchical toggle"}})
        res = apply_mod.apply_spec(backend, _rel_spec(_base_rel(is_hierarchical=True)),
                                   stage_only=False)
    assert res["ok"] is False
    assert _kinds(res["failed"]) == ["relationship"]
    assert res["replace_blocked"] == []


def _mock_entity_exists(m, backend, logical):
    """Minimal EntityDefinitions GET so a dry-run create's reference resolution
    (entity_exists on the relationship's two entities) finds the entity."""
    m.get(backend.url_for(f"EntityDefinitions(LogicalName='{logical}')"),
          json={"MetadataId": _ENT_ID, "LogicalName": logical})


def test_apply_dry_run_reports_relationship_cascade_as_drift(dry_backend):
    with requests_mock.Mocker() as m:
        _mock_entity_live(m, dry_backend, display_name="Project")
        _mock_entity_exists(m, dry_backend, "contoso_task")
        _mock_relationship_live(m, dry_backend, schema="contoso_project_task")
        res = apply_mod.apply_spec(dry_backend, _rel_spec(_base_rel(cascade_delete="Cascade")),
                                   stage_only=False)
    assert res["ok"] is True
    assert _kinds(res["updated"]) == ["relationship"]
    assert "CascadeConfiguration" in res["updated"][0]["diff"]
    assert _writes(m) == []  # reads-execute: GETs only, zero writes


def test_apply_dry_run_reports_relationship_replace_blocked(dry_backend):
    rel = _base_rel(referencing_entity="contoso_other")
    with requests_mock.Mocker() as m:
        _mock_entity_live(m, dry_backend, display_name="Project")
        _mock_entity_exists(m, dry_backend, "contoso_other")
        _mock_relationship_live(m, dry_backend, schema="contoso_project_task")
        res = apply_mod.apply_spec(dry_backend, _rel_spec(rel), stage_only=False)
    assert res["ok"] is False
    assert _kinds(res["replace_blocked"]) == ["relationship"]
    assert _writes(m) == []


def test_apply_dry_run_greenfield_relationship_is_planned_not_blocked(dry_backend):
    # A relationship that does NOT exist must be `planned` under --dry-run, never
    # routed through reconcile (which would otherwise mis-read an absent definition):
    # _present is False when the create probe finds no existing relationship, so the
    # reconcile path never fires for a greenfield relationship.
    with requests_mock.Mocker() as m:
        _mock_entity_live(m, dry_backend, display_name="Project")
        _mock_entity_exists(m, dry_backend, "contoso_task")
        _mock_one_to_many(m, dry_backend, schema="contoso_project_task", exists=False)
        res = apply_mod.apply_spec(dry_backend, _rel_spec(_base_rel()), stage_only=False)
    assert res["ok"] is True
    assert "relationship" in _kinds(res["planned"])
    assert res["replace_blocked"] == []
    assert res["failed"] == []
    assert _writes(m) == []


def test_apply_dry_run_relationship_merged_diff_carries_both(dry_backend):
    # A merged relationship+lookup drift reports both field diffs in the one entry.
    rel = _base_rel(cascade_delete="Cascade", lookup_display="Renamed")
    with requests_mock.Mocker() as m:
        _mock_entity_live(m, dry_backend, display_name="Project")
        _mock_entity_exists(m, dry_backend, "contoso_task")
        _mock_relationship_live(m, dry_backend, schema="contoso_project_task")
        res = apply_mod.apply_spec(dry_backend, _rel_spec(rel), stage_only=False)
    assert res["ok"] is True
    assert len(res["updated"]) == 1
    diff = res["updated"][0]["diff"]
    assert "CascadeConfiguration" in diff and "DisplayName" in diff
    assert _writes(m) == []


def _mock_optionset_live(m, backend, *, name="contoso_priority", options):
    """Mock an EXISTING global option set. One GET serves the target_exists probe
    and get_optionset (full def with live Options); InsertOptionValue 204 for adds.
    `options` is a list of (value, label) currently live."""
    url = backend.url_for(f"GlobalOptionSetDefinitions(Name='{name}')")
    live = {"Name": name, "MetadataId": _OS_ID,
            "Options": [{"Value": v, "Label": _label(lbl)} for v, lbl in options]}
    m.get(url, json=live)
    m.post(backend.url_for("InsertOptionValue"), json={})


def test_apply_adds_new_options_to_existing_optionset(backend):
    os_spec = {"name": "contoso_priority", "display_name": "Priority",
               "options": [{"value": 100000000, "label": "Low"},
                           {"value": 100000001, "label": "High"},
                           {"value": 100000002, "label": "Critical"}]}
    spec = {"optionsets": [os_spec]}
    with requests_mock.Mocker() as m:
        _mock_optionset_live(m, backend, options=[(100000000, "Low"), (100000001, "High")])
        m.post(backend.url_for("PublishAllXml"), status_code=204)
        res = apply_mod.apply_spec(backend, spec, stage_only=False)
    assert res["ok"] is True
    assert _kinds(res["updated"]) == ["optionset"]
    inserts = [r for r in m.request_history if r.url.endswith("InsertOptionValue")]
    assert len(inserts) == 1  # only the new "Critical" option, not the two existing


def test_apply_optionset_unchanged_is_skipped(backend):
    os_spec = {"name": "contoso_priority", "display_name": "Priority",
               "options": [{"value": 100000000, "label": "Low"}]}
    spec = {"optionsets": [os_spec]}
    with requests_mock.Mocker() as m:
        _mock_optionset_live(m, backend, options=[(100000000, "Low")])
        res = apply_mod.apply_spec(backend, spec, stage_only=False)
    assert res["ok"] is True
    assert _kinds(res["skipped"]) == ["optionset"]
    assert res["updated"] == []
    assert [r for r in m.request_history if r.url.endswith("InsertOptionValue")] == []


def test_apply_partial_replace_block_leaves_rest_applied(backend):
    # Entity display drifts (updatable); its attribute is retyped (replace-blocked).
    # The entity update lands; the attribute is reported, not written; ok=false.
    # No whole-run rollback — the entity update is not undone.
    ent = {"schema_name": "contoso_Project", "display_name": "Renamed Project",
           "attributes": [{"kind": "string", "schema_name": "contoso_Code",
                           "display_name": "Code"}],
           "primary_attr": {"schema_name": "contoso_Name", "label": "Name"}}
    spec = {"entities": [ent]}
    with requests_mock.Mocker() as m:
        _mock_entity_live(m, backend, display_name="Project")  # drift → update
        _mock_attribute_live(m, backend, logical="contoso_code", schema="contoso_Code",
                             cast="Microsoft.Dynamics.CRM.IntegerAttributeMetadata")
        m.post(backend.url_for("PublishAllXml"), status_code=204)
        res = apply_mod.apply_spec(backend, spec, stage_only=False)
    assert res["ok"] is False
    assert _kinds(res["updated"]) == ["entity"]
    assert _kinds(res["replace_blocked"]) == ["attribute"]
    # Entity update happened (one PUT to the entity definition).
    assert len([r for r in m.request_history if r.method == "PUT"]) == 1
    # A failed run is not published.
    assert len(_publish_hits(m, backend)) == 0


def test_apply_stage_only_defers_publish_on_update(backend):
    ent = {"schema_name": "contoso_Project", "display_name": "Renamed",
           "primary_attr": {"schema_name": "contoso_Name", "label": "Name"}}
    spec = {"entities": [ent]}
    with requests_mock.Mocker() as m:
        _mock_entity_live(m, backend, display_name="Project")
        res = apply_mod.apply_spec(backend, spec, stage_only=True)
    assert res["ok"] is True
    assert _kinds(res["updated"]) == ["entity"]
    assert res["staged"] is True
    assert len(_publish_hits(m, backend)) == 0


def test_apply_command_human_mode_renders_updated_bucket(backend, monkeypatch, tmp_path):
    # ok=True human output renders the data dict — the new `updated` bucket shows.
    import yaml
    spec = {"entities": [{"schema_name": "contoso_Project", "display_name": "Renamed",
                          "primary_attr": {"schema_name": "contoso_Name", "label": "Name"}}]}
    spec_file = tmp_path / "spec.yaml"
    spec_file.write_text(yaml.safe_dump(spec), encoding="utf-8")
    monkeypatch.setattr(CLIContext, "backend", lambda self: backend)
    with requests_mock.Mocker() as m:
        _mock_entity_live(m, backend, display_name="Project")
        m.post(backend.url_for("PublishAllXml"), status_code=204)
        result = CliRunner().invoke(cli, ["apply", "-f", str(spec_file)])  # no --json
    assert result.exit_code == 0, result.output
    assert "updated" in result.output


def test_apply_command_human_mode_shows_replace_blocked_reason(backend, monkeypatch, tmp_path):
    # ok=False human output prints the refusal reason (not a bare "Operation failed").
    import yaml
    spec = {"entities": [{"schema_name": "contoso_Project", "display_name": "Project",
                          "ownership": "OrganizationOwned",
                          "primary_attr": {"schema_name": "contoso_Name", "label": "Name"}}]}
    spec_file = tmp_path / "spec.yaml"
    spec_file.write_text(yaml.safe_dump(spec), encoding="utf-8")
    monkeypatch.setattr(CLIContext, "backend", lambda self: backend)
    with requests_mock.Mocker() as m:
        _mock_entity_live(m, backend, ownership="UserOwned")
        result = CliRunner().invoke(cli, ["apply", "-f", str(spec_file)])  # no --json
    assert result.exit_code == 1
    assert "ownership change" in result.output


def test_apply_updates_attribute_description_on_drift(backend):
    # Description drift alone (display unchanged) → updated.
    attr = {"kind": "string", "schema_name": "contoso_Code", "display_name": "Code",
            "description": "New description"}
    with requests_mock.Mocker() as m:
        _mock_entity_live(m, backend, display_name="Project")
        _mock_attribute_live(m, backend, logical="contoso_code", schema="contoso_Code",
                             display_name="Code", description="Old description")
        m.post(backend.url_for("PublishAllXml"), status_code=204)
        res = apply_mod.apply_spec(backend, _attr_spec(attr), stage_only=False)
    assert res["ok"] is True
    assert _kinds(res["updated"]) == ["attribute"]
    assert len([r for r in m.request_history if r.method == "PUT"]) == 1


def test_apply_rejects_non_int_max_length(backend):
    # A quoted/non-int max_length must fail validation up front, not crash the
    # numeric grow comparison during reconciliation (Copilot round 2).
    attr = {"kind": "string", "schema_name": "contoso_Code", "display_name": "Code",
            "max_length": "200"}
    spec = {"entities": [{"schema_name": "contoso_Project", "display_name": "Project",
                          "attributes": [attr]}]}
    with pytest.raises(D365Error, match="max_length must be an integer"):
        apply_mod.apply_spec(backend, spec)


def test_apply_command_human_mode_shows_failed_reason(backend, monkeypatch, tmp_path):
    # ok=False from a hard failure also surfaces the reason in human output.
    import yaml
    spec = {"entities": [{"schema_name": "contoso_Project", "display_name": "Project",
                          "primary_attr": {"schema_name": "contoso_Name", "label": "Name"}}]}
    spec_file = tmp_path / "spec.yaml"
    spec_file.write_text(yaml.safe_dump(spec), encoding="utf-8")
    monkeypatch.setattr(CLIContext, "backend", lambda self: backend)
    with requests_mock.Mocker() as m:
        m.get(backend.url_for("EntityDefinitions(LogicalName='contoso_project')"),
              status_code=404)
        m.post(backend.url_for("EntityDefinitions"), status_code=500,
               json={"error": {"message": "boom"}})
        result = CliRunner().invoke(cli, ["apply", "-f", str(spec_file)])
    assert result.exit_code == 1
    assert "failed" in result.output


def test_apply_rejects_invalid_ownership(backend):
    # A typo'd ownership must fail validation up front, not be misreported as a
    # destructive (replace-blocked) ownership change during reconciliation (round 3).
    ent = {"schema_name": "contoso_Project", "display_name": "Project",
           "ownership": "UserOwnd"}
    spec = {"entities": [ent]}
    with pytest.raises(D365Error, match="ownership"):
        apply_mod.apply_spec(backend, spec)


# ── Slice: dry-run drift report (#550) ──────────────────────────────────────
# Under --dry-run, apply reads the live org and classifies every declared
# component into the four drift buckets — create (`planned`) / update
# (`updated`) / replace-blocked (`replace_blocked`) / prune-candidate (`pruned`,
# reserved) — WITHOUT issuing a single write (reads-execute rule).


def _writes(m):
    """Every non-GET request that reached the wire (should be empty under dry-run)."""
    return [r for r in m.request_history if r.method != "GET"]


def test_apply_dry_run_reports_entity_update_as_drift(dry_backend):
    # Live entity displays "Old Project"; spec wants "Project". A dry-run must
    # report it in the `updated` drift bucket (not `skipped`) and write nothing.
    ent = {"schema_name": "contoso_Project", "display_name": "Project",
           "primary_attr": {"schema_name": "contoso_Name", "label": "Name"}}
    spec = {"entities": [ent]}
    with requests_mock.Mocker() as m:
        _mock_entity_live(m, dry_backend, display_name="Old Project")
        res = apply_mod.apply_spec(dry_backend, spec, stage_only=False)
    assert res["ok"] is True
    assert _kinds(res["updated"]) == ["entity"]
    assert res["skipped"] == []
    assert res["applied"] == []
    assert res["staged"] is False  # nothing was written, so nothing is staged
    assert _writes(m) == []  # reads-execute: GETs only, zero writes
    # The updated entry carries the field-level diff that powers the report.
    assert "DisplayName" in res["updated"][0]["diff"]


def test_apply_dry_run_reports_replace_blocked(dry_backend):
    # An immutable ownership divergence is a replace-blocked drift even in a
    # dry-run: reported (ok=false), still no write.
    ent = {"schema_name": "contoso_Project", "display_name": "Project",
           "ownership": "OrganizationOwned",
           "primary_attr": {"schema_name": "contoso_Name", "label": "Name"}}
    spec = {"entities": [ent]}
    with requests_mock.Mocker() as m:
        _mock_entity_live(m, dry_backend, ownership="UserOwned")
        res = apply_mod.apply_spec(dry_backend, spec, stage_only=False)
    assert res["ok"] is False
    assert _kinds(res["replace_blocked"]) == ["entity"]
    assert "reason" in res["replace_blocked"][0]
    assert res["updated"] == []
    assert _writes(m) == []


def test_apply_dry_run_reports_attribute_update_as_drift(dry_backend):
    attr = {"kind": "string", "schema_name": "contoso_Code", "display_name": "Code",
            "required": "ApplicationRequired"}
    with requests_mock.Mocker() as m:
        _mock_entity_live(m, dry_backend, display_name="Project")
        _mock_attribute_live(m, dry_backend, logical="contoso_code", schema="contoso_Code",
                             display_name="Code", required="None")
        res = apply_mod.apply_spec(dry_backend, _attr_spec(attr), stage_only=False)
    assert res["ok"] is True
    assert _kinds(res["updated"]) == ["attribute"]
    assert _kinds(res["skipped"]) == ["entity"]  # parent entity matches → skipped
    assert _writes(m) == []


def test_apply_dry_run_reports_attribute_datatype_change_as_replace_blocked(dry_backend):
    attr = {"kind": "string", "schema_name": "contoso_Code", "display_name": "Code"}
    with requests_mock.Mocker() as m:
        _mock_entity_live(m, dry_backend, display_name="Project")
        _mock_attribute_live(m, dry_backend, logical="contoso_code", schema="contoso_Code",
                             cast="Microsoft.Dynamics.CRM.IntegerAttributeMetadata")
        res = apply_mod.apply_spec(dry_backend, _attr_spec(attr), stage_only=False)
    assert res["ok"] is False
    assert _kinds(res["replace_blocked"]) == ["attribute"]
    assert _writes(m) == []


def test_apply_dry_run_unchanged_is_skipped_not_updated(dry_backend):
    # A live component that already matches the spec is a no-op (skipped), never
    # mis-reported as drift, even though the dry-run now runs the reconcile diff.
    ent = {"schema_name": "contoso_Project", "display_name": "Project",
           "display_collection_name": "Projects",
           "primary_attr": {"schema_name": "contoso_Name", "label": "Name"}}
    spec = {"entities": [ent]}
    with requests_mock.Mocker() as m:
        _mock_entity_live(m, dry_backend, display_name="Project",
                          display_collection_name="Projects")
        res = apply_mod.apply_spec(dry_backend, spec, stage_only=False)
    assert res["ok"] is True
    assert _kinds(res["skipped"]) == ["entity"]
    assert res["updated"] == []
    assert _writes(m) == []


def test_apply_dry_run_reports_optionset_new_options_as_drift(dry_backend):
    os_spec = {"name": "contoso_priority", "display_name": "Priority",
               "options": [{"value": 100000000, "label": "Low"},
                           {"value": 100000001, "label": "High"},
                           {"value": 100000002, "label": "Critical"}]}
    spec = {"optionsets": [os_spec]}
    with requests_mock.Mocker() as m:
        _mock_optionset_live(m, dry_backend,
                             options=[(100000000, "Low"), (100000001, "High")])
        res = apply_mod.apply_spec(dry_backend, spec, stage_only=False)
    assert res["ok"] is True
    assert _kinds(res["updated"]) == ["optionset"]
    assert _writes(m) == []


def test_apply_dry_run_command_renders_all_drift_buckets(dry_backend, monkeypatch, tmp_path):
    # The verb's JSON `data` carries the four drift buckets so an agent can branch
    # on them, and meta.dry_run flags the preview.
    import yaml
    ent = {"schema_name": "contoso_Project", "display_name": "Project",
           "primary_attr": {"schema_name": "contoso_Name", "label": "Name"}}
    spec_file = tmp_path / "spec.yaml"
    spec_file.write_text(yaml.safe_dump({"entities": [ent]}), encoding="utf-8")
    monkeypatch.setattr(CLIContext, "backend", lambda self: dry_backend)
    with requests_mock.Mocker() as m:
        _mock_entity_live(m, dry_backend, display_name="Old Project")
        result = CliRunner().invoke(cli, ["--json", "--dry-run", "apply", "-f", str(spec_file)])
    assert result.exit_code == 0
    payload = json.loads(result.output)
    assert payload["ok"] is True
    data = payload["data"]
    for bucket in ("planned", "updated", "replace_blocked", "pruned"):
        assert bucket in data
    assert [e["kind"] for e in data["updated"]] == ["entity"]
    assert payload["meta"]["dry_run"] is True
    assert payload["meta"]["staged"] is False
    assert _writes(m) == []


def test_apply_dry_run_human_mode_renders_drift_buckets(dry_backend, monkeypatch, tmp_path):
    # AC#3: the same drift buckets render in human (non-JSON) mode, not only JSON.
    import yaml
    ent = {"schema_name": "contoso_Project", "display_name": "Project",
           "primary_attr": {"schema_name": "contoso_Name", "label": "Name"}}
    spec_file = tmp_path / "spec.yaml"
    spec_file.write_text(yaml.safe_dump({"entities": [ent]}), encoding="utf-8")
    monkeypatch.setattr(CLIContext, "backend", lambda self: dry_backend)
    with requests_mock.Mocker() as m:
        _mock_entity_live(m, dry_backend, display_name="Old Project")
        result = CliRunner().invoke(cli, ["--dry-run", "apply", "-f", str(spec_file)])
    assert result.exit_code == 0
    # The `updated` drift bucket is rendered as a labelled line in human output.
    assert "updated" in result.output
    assert _writes(m) == []


# ── Web resource kind (#551) ────────────────────────────────────────────────

_WR_ID = "aaaaaaaa-aaaa-aaaa-aaaa-aaaaaaaaaaaa"


def _mock_webresource_absent(m, backend):
    """No web resource by that name; POST create → 204 + id."""
    m.get(backend.url_for("webresourceset"), json={"value": []})
    m.post(backend.url_for("webresourceset"), status_code=204,
           headers={"OData-EntityId": backend.url_for(f"webresourceset({_WR_ID})")})


def _mock_webresource_live(m, backend, *, name="new_app.js", content=b"console.log(1)",
                           display_name="app.js"):
    """An EXISTING web resource. One GET (collection $filter) serves the existence
    check and update's id-resolve, carrying the live base64 `content`; PATCH 204."""
    row = {"webresourceid": _WR_ID, "name": name, "displayname": display_name,
           "webresourcetype": 3, "content": base64.b64encode(content).decode("ascii")}
    m.get(backend.url_for("webresourceset"), json={"value": [row]})
    m.patch(backend.url_for(f"webresourceset({_WR_ID})"), status_code=204)


def _wr_spec(tmp_path, *, name="new_app.js", body=b"console.log(1)", **extra):
    """Build a web resource spec entry backed by a real file under tmp_path."""
    p = tmp_path / "app.js"
    p.write_bytes(body)
    return {"name": name, "file": str(p), **extra}


def test_apply_creates_webresource(backend, tmp_path):
    spec = {"webresources": [_wr_spec(tmp_path)]}
    with requests_mock.Mocker() as m:
        _mock_webresource_absent(m, backend)
        m.post(backend.url_for("PublishAllXml"), status_code=204)
        res = apply_mod.apply_spec(backend, spec, stage_only=False)
    assert res["ok"] is True
    assert _kinds(res["applied"]) == ["webresource"]
    assert res["applied"][0]["name"] == "new_app.js"
    # Web resources are publishable → PublishAllXml runs once at the end.
    assert len(_publish_hits(m, backend)) == 1
    assert res["staged"] is False


def test_apply_updates_webresource_content_on_drift(backend, tmp_path):
    # Spec file content differs from the live `content` column → PATCH + republish.
    spec = {"webresources": [_wr_spec(tmp_path, body=b"console.log(2)")]}
    with requests_mock.Mocker() as m:
        _mock_webresource_live(m, backend, content=b"console.log(1)")
        m.post(backend.url_for("PublishAllXml"), status_code=204)
        res = apply_mod.apply_spec(backend, spec, stage_only=False)
    assert res["ok"] is True
    assert _kinds(res["updated"]) == ["webresource"]
    patches = [r for r in m.request_history if r.method == "PATCH"]
    assert len(patches) == 1 and "content" in json.loads(patches[0].body)
    assert len(_publish_hits(m, backend)) == 1


def test_apply_webresource_unchanged_is_skipped(backend, tmp_path):
    spec = {"webresources": [_wr_spec(tmp_path, body=b"console.log(1)")]}
    with requests_mock.Mocker() as m:
        _mock_webresource_live(m, backend, content=b"console.log(1)")  # identical
        res = apply_mod.apply_spec(backend, spec, stage_only=False)
    assert res["ok"] is True
    assert _kinds(res["skipped"]) == ["webresource"]
    assert res["updated"] == []
    assert [r for r in m.request_history if r.method == "PATCH"] == []
    assert _publish_hits(m, backend) == []  # nothing changed → no publish


def test_apply_dry_run_reports_webresource_content_drift(dry_backend, tmp_path):
    spec = {"webresources": [_wr_spec(tmp_path, body=b"console.log(2)")]}
    with requests_mock.Mocker() as m:
        _mock_webresource_live(m, dry_backend, content=b"console.log(1)")
        res = apply_mod.apply_spec(dry_backend, spec, stage_only=False)
    assert res["ok"] is True
    assert _kinds(res["updated"]) == ["webresource"]
    assert res["updated"][0]["diff"]["fields"] == ["content"]
    assert _writes(m) == []  # PATCH suppressed under dry-run


def test_apply_webresource_stage_only_defers_publish(backend, tmp_path):
    spec = {"webresources": [_wr_spec(tmp_path)]}
    with requests_mock.Mocker() as m:
        _mock_webresource_absent(m, backend)
        res = apply_mod.apply_spec(backend, spec, stage_only=True)
    assert res["ok"] is True
    assert _kinds(res["applied"]) == ["webresource"]
    assert res["staged"] is True
    assert _publish_hits(m, backend) == []


def test_apply_rejects_webresource_missing_file(backend):
    spec = {"webresources": [{"name": "new_app.js"}]}
    with pytest.raises(D365Error, match="missing required field 'file'"):
        apply_mod.apply_spec(backend, spec)


# ── Security role kind (#551) ────────────────────────────────────────────────

_ROLE_ID = "bbbbbbbb-bbbb-bbbb-bbbb-bbbbbbbbbbbb"
_BU_GUID = "cccccccc-cccc-cccc-cccc-cccccccccccc"
_PRV_READ = "dddddddd-dddd-dddd-dddd-dddddddddddd"
_PRV_WRITE = "eeeeeeee-eeee-eeee-eeee-eeeeeeeeeeee"
_REPLACE_ROLE = f"roles({_ROLE_ID})/Microsoft.Dynamics.CRM.ReplacePrivilegesRole"
_RETRIEVE_ROLE = f"RetrieveRolePrivilegesRole(RoleId={_ROLE_ID})"  # unbound function


def _named_priv_row(name, pid, *, basic=True, local=True, deep=True, glob=True):
    """One `privileges` entity row (lower-case shape) for _named_privileges."""
    return {"name": name, "privilegeid": pid, "canbebasic": basic,
            "canbelocal": local, "canbedeep": deep, "canbeglobal": glob}


def _mock_role_absent(m, backend, *, name="Contoso Sales"):
    """No role by that name; create POST echoes the new role record."""
    m.get(backend.url_for("roles"), json={"value": []})
    m.post(backend.url_for("roles"), json={"roleid": _ROLE_ID, "name": name})


def _mock_role_exists(m, backend):
    """An EXISTING role (create_role if_exists='skip' returns it)."""
    m.get(backend.url_for("roles"), json={"value": [{"roleid": _ROLE_ID}]})


def _mock_named_privileges(m, backend, rows):
    """_named_privileges → GET privileges $filter."""
    m.get(backend.url_for("privileges"), json={"value": rows})


def _mock_role_privileges_live(m, backend, privileges):
    """RetrieveRolePrivilegesRole. `privileges`: list of (privilegeid, depth, name)."""
    rps = [{"PrivilegeId": pid, "PrivilegeName": nm, "Depth": depth, "BusinessUnitId": _BU_GUID}
           for pid, depth, nm in privileges]
    m.get(backend.url_for(_RETRIEVE_ROLE), json={"RolePrivileges": rps})


def _mock_role_replace(m, backend):
    m.post(backend.url_for(_REPLACE_ROLE), status_code=204)


def _role_spec(**extra):
    return {"name": "Contoso Sales", "business_unit": _BU_GUID,
            "privileges": [{"privilege_names": ["prvReadAccount"], "depth": "global"}],
            **extra}


def test_apply_creates_security_role_and_sets_privileges(backend):
    spec = {"security_roles": [_role_spec()]}
    with requests_mock.Mocker() as m:
        _mock_role_absent(m, backend)
        _mock_named_privileges(m, backend, [_named_priv_row("prvReadAccount", _PRV_READ)])
        _mock_role_replace(m, backend)
        res = apply_mod.apply_spec(backend, spec, stage_only=False)
    assert res["ok"] is True
    assert _kinds(res["applied"]) == ["security-role"]
    # Declared privileges applied via ReplacePrivilegesRole.
    assert any("ReplacePrivilegesRole" in r.url
               for r in m.request_history if r.method == "POST")
    # Roles are not publishable → no PublishAllXml (no mock → would fail if attempted).
    assert _publish_hits(m, backend) == []
    assert res["staged"] is False


def test_apply_reconciles_role_privileges_to_declared_set(backend):
    # Live role's declared privilege is missing (it carries only an unlisted one), so
    # the reconcile fires and replaces to the declared set, dropping the removable
    # unlisted privilege. (Subset-satisfaction: a removal-only change would be a
    # no-op — see _reconcile_security_role.)
    spec = {"security_roles": [_role_spec()]}
    with requests_mock.Mocker() as m:
        _mock_role_exists(m, backend)
        _mock_role_privileges_live(m, backend, [(_PRV_WRITE, "Global", "prvWriteAccount")])
        _mock_named_privileges(m, backend, [_named_priv_row("prvReadAccount", _PRV_READ)])
        _mock_role_replace(m, backend)
        res = apply_mod.apply_spec(backend, spec, stage_only=False)
    assert res["ok"] is True
    assert _kinds(res["updated"]) == ["security-role"]
    body = json.loads([r for r in m.request_history if r.method == "POST"][-1].body)
    assert {p["PrivilegeId"]: p["Depth"] for p in body["Privileges"]} == {_PRV_READ: "Global"}
    diff = res["updated"][0]["diff"]
    assert "prvReadAccount" in diff["added"] and "prvWriteAccount" in diff["removed"]
    assert _publish_hits(m, backend) == []


def test_apply_role_privileges_unchanged_is_skipped(backend):
    spec = {"security_roles": [_role_spec()]}
    with requests_mock.Mocker() as m:
        _mock_role_exists(m, backend)
        _mock_role_privileges_live(m, backend, [(_PRV_READ, "Global", "prvReadAccount")])
        _mock_named_privileges(m, backend, [_named_priv_row("prvReadAccount", _PRV_READ)])
        res = apply_mod.apply_spec(backend, spec, stage_only=False)
    assert res["ok"] is True
    assert _kinds(res["skipped"]) == ["security-role"]
    assert res["updated"] == []
    # No ReplacePrivilegesRole POST (no mock → would fail if attempted).
    assert [r for r in m.request_history if r.method == "POST"] == []


def test_apply_dry_run_reports_role_privilege_drift(dry_backend):
    spec = {"security_roles": [_role_spec()]}
    with requests_mock.Mocker() as m:
        _mock_role_exists(m, dry_backend)
        _mock_role_privileges_live(m, dry_backend, [(_PRV_WRITE, "Global", "prvWriteAccount")])
        _mock_named_privileges(m, dry_backend, [_named_priv_row("prvReadAccount", _PRV_READ)])
        res = apply_mod.apply_spec(dry_backend, spec, stage_only=False)
    assert res["ok"] is True
    assert _kinds(res["updated"]) == ["security-role"]
    assert _writes(m) == []  # ReplacePrivilegesRole suppressed under dry-run


def test_apply_rejects_security_role_privilege_missing_depth(backend):
    spec = {"security_roles": [{"name": "R",
                                "privileges": [{"privilege_names": ["prvReadAccount"]}]}]}
    with pytest.raises(D365Error, match="missing required field 'depth'"):
        apply_mod.apply_spec(backend, spec)


def test_apply_rejects_security_role_privilege_without_selector(backend):
    spec = {"security_roles": [{"name": "R", "privileges": [{"depth": "global"}]}]}
    with pytest.raises(D365Error, match="privilege_names"):
        apply_mod.apply_spec(backend, spec)


def test_apply_command_webresource_file_relative_to_spec(backend, monkeypatch, tmp_path):
    # A web resource `file` is resolved relative to the spec file's directory, so a
    # bare basename next to the spec is found (proves the command passes base_dir).
    import yaml
    (tmp_path / "app.js").write_bytes(b"console.log(1)")
    spec = {"webresources": [{"name": "new_app.js", "file": "app.js"}]}
    spec_file = tmp_path / "spec.yaml"
    spec_file.write_text(yaml.safe_dump(spec), encoding="utf-8")
    monkeypatch.setattr(CLIContext, "backend", lambda self: backend)
    with requests_mock.Mocker() as m:
        _mock_webresource_absent(m, backend)
        m.post(backend.url_for("PublishAllXml"), status_code=204)
        result = CliRunner().invoke(cli, ["--json", "apply", "-f", str(spec_file)])
    assert result.exit_code == 0, result.output
    payload = json.loads(result.output)
    assert payload["ok"] is True
    assert [e["kind"] for e in payload["data"]["applied"]] == ["webresource"]


# ── Plug-in kind (#552) ──────────────────────────────────────────────────────

_ASM_ID = "f1f1f1f1-f1f1-f1f1-f1f1-f1f1f1f1f1f1"
_TYPE_ID = "f2f2f2f2-f2f2-f2f2-f2f2-f2f2f2f2f2f2"
_STEP_ID = "f3f3f3f3-f3f3-f3f3-f3f3-f3f3f3f3f3f3"
_IMG_ID = "f4f4f4f4-f4f4-f4f4-f4f4-f4f4f4f4f4f4"
_MSG_ID = "f5f5f5f5-f5f5-f5f5-f5f5-f5f5f5f5f5f5"
_ASM_NAME = "Contoso.Plugins"
_TYPE_NAME = "Contoso.Plugins.AccountHandler"
_DLL_BYTES = b"MZ\x00\x01fake-assembly"


def _dll(tmp_path, *, body=_DLL_BYTES):
    """Write a fake assembly DLL under tmp_path and return its path."""
    p = tmp_path / "Contoso.Plugins.dll"
    p.write_bytes(body)
    return str(p)


def _plugin_spec(tmp_path, *, body=_DLL_BYTES, **extra):
    """A plug-in spec entry backed by a real DLL file under tmp_path."""
    return {"assembly": _ASM_NAME, "file": _dll(tmp_path, body=body), **extra}


def _mock_assembly_absent(m, backend):
    """No assembly by that name; POST create → 204 + id."""
    m.get(backend.url_for("pluginassemblies"), json={"value": []})
    m.post(backend.url_for("pluginassemblies"), status_code=204,
           headers={"OData-EntityId": backend.url_for(f"pluginassemblies({_ASM_ID})")})


def _mock_assembly_live(m, backend, *, content=_DLL_BYTES):
    """A pre-existing assembly. The one GET serves find_assembly AND the
    update/resolve id-lookup, carrying the live base64 `content`; PATCH 204."""
    row = {"pluginassemblyid": _ASM_ID, "name": _ASM_NAME,
           "content": base64.b64encode(content).decode("ascii")}
    m.get(backend.url_for("pluginassemblies"), json={"value": [row]})
    m.patch(backend.url_for(f"pluginassemblies({_ASM_ID})"), status_code=204)


def _mock_types(m, backend, typenames=()):
    """plugintypes listing (apply's list_types) + resolve + POST create."""
    rows = [{"plugintypeid": _TYPE_ID, "typename": tn} for tn in typenames]
    m.get(backend.url_for("plugintypes"), json={"value": rows})
    m.post(backend.url_for("plugintypes"), status_code=204,
           headers={"OData-EntityId": backend.url_for(f"plugintypes({_TYPE_ID})")})


def _mock_sdkmessage(m, backend, *, name="Create"):
    m.get(backend.url_for("sdkmessages"),
          json={"value": [{"sdkmessageid": _MSG_ID, "name": name}]})


def _step_row(*, message="Update", typename=_TYPE_NAME, entity="account",
              stage=40, mode=0, rank=1, filtering=None, configuration=None):
    """A live step row as find_step reads it (binding $expand inlined), also
    carrying the flat columns register_image's step-read needs."""
    return {
        "sdkmessageprocessingstepid": _STEP_ID, "stage": stage, "mode": mode,
        "rank": rank, "filteringattributes": filtering, "configuration": configuration,
        "sdkmessageid": {"name": message}, "plugintypeid": {"typename": typename},
        "sdkmessagefilterid": {"primaryobjecttypecode": entity} if entity else None,
        "_sdkmessageid_value": _MSG_ID,
    }


def _mock_step_absent(m, backend):
    m.get(backend.url_for("sdkmessageprocessingsteps"), json={"value": []})
    m.post(backend.url_for("sdkmessageprocessingsteps"), status_code=204,
           headers={"OData-EntityId":
                    backend.url_for(f"sdkmessageprocessingsteps({_STEP_ID})")})


def _mock_step_live(m, backend, row):
    m.get(backend.url_for("sdkmessageprocessingsteps"), json={"value": [row]})
    m.patch(backend.url_for(f"sdkmessageprocessingsteps({_STEP_ID})"), status_code=204)


def _mock_image_absent(m, backend):
    m.get(backend.url_for("sdkmessageprocessingstepimages"), json={"value": []})
    m.post(backend.url_for("sdkmessageprocessingstepimages"), status_code=204,
           headers={"OData-EntityId":
                    backend.url_for(f"sdkmessageprocessingstepimages({_IMG_ID})")})


def _step_spec(**extra):
    return {"name": "Contoso Account Handler", "message": "Create",
            "plugin_type": _TYPE_NAME, **extra}


def test_apply_creates_plugin_assembly(backend, tmp_path):
    spec = {"plugins": [_plugin_spec(tmp_path)]}
    with requests_mock.Mocker() as m:
        _mock_assembly_absent(m, backend)
        res = apply_mod.apply_spec(backend, spec, stage_only=False)
    assert res["ok"] is True
    assert _kinds(res["applied"]) == ["plugin-assembly"]
    assert res["applied"][0]["name"] == _ASM_NAME
    # Plug-in components are not publishable → no PublishAllXml (no mock → would fail).
    assert _publish_hits(m, backend) == []
    assert res["staged"] is False


def test_apply_updates_plugin_assembly_content_on_rebuild(backend, tmp_path):
    # The spec's DLL bytes differ from the live `content` → PATCH the content.
    spec = {"plugins": [_plugin_spec(tmp_path, body=b"MZ\x00\x01rebuilt")]}
    with requests_mock.Mocker() as m:
        _mock_assembly_live(m, backend, content=b"MZ\x00\x01old-build")
        res = apply_mod.apply_spec(backend, spec, stage_only=False)
    assert res["ok"] is True
    assert _kinds(res["updated"]) == ["plugin-assembly"]
    patches = [r for r in m.request_history if r.method == "PATCH"]
    assert len(patches) == 1 and "content" in json.loads(patches[0].body)


def test_apply_plugin_assembly_unchanged_is_skipped(backend, tmp_path):
    spec = {"plugins": [_plugin_spec(tmp_path, body=_DLL_BYTES)]}
    with requests_mock.Mocker() as m:
        _mock_assembly_live(m, backend, content=_DLL_BYTES)  # identical
        res = apply_mod.apply_spec(backend, spec, stage_only=False)
    assert res["ok"] is True
    assert _kinds(res["skipped"]) == ["plugin-assembly"]
    assert [r for r in m.request_history if r.method != "GET"] == []


def test_apply_registers_new_plugin_type(backend, tmp_path):
    # Pre-existing assembly (unchanged) + a newly declared type → register it.
    spec = {"plugins": [_plugin_spec(tmp_path, types=[{"type_name": _TYPE_NAME}])]}
    with requests_mock.Mocker() as m:
        _mock_assembly_live(m, backend)
        _mock_types(m, backend, typenames=[])  # none registered yet
        res = apply_mod.apply_spec(backend, spec, stage_only=False)
    assert res["ok"] is True
    assert _kinds(res["skipped"]) == ["plugin-assembly"]
    assert _kinds(res["applied"]) == ["plugin-type"]
    assert any(r.method == "POST" and "plugintypes" in r.url for r in m.request_history)


def test_apply_skips_existing_plugin_type(backend, tmp_path):
    spec = {"plugins": [_plugin_spec(tmp_path, types=[{"type_name": _TYPE_NAME}])]}
    with requests_mock.Mocker() as m:
        _mock_assembly_live(m, backend)
        _mock_types(m, backend, typenames=[_TYPE_NAME])  # already registered
        res = apply_mod.apply_spec(backend, spec, stage_only=False)
    assert res["ok"] is True
    assert _kinds(res["skipped"]) == ["plugin-assembly", "plugin-type"]
    assert [r for r in m.request_history if r.method == "POST"] == []


def test_apply_registers_new_plugin_step(backend, tmp_path):
    # Pre-existing assembly + a new message-level step → register it.
    spec = {"plugins": [_plugin_spec(tmp_path, steps=[_step_spec()])]}
    with requests_mock.Mocker() as m:
        _mock_assembly_live(m, backend)
        _mock_step_absent(m, backend)
        _mock_sdkmessage(m, backend, name="Create")
        m.get(backend.url_for("plugintypes"),
              json={"value": [{"plugintypeid": _TYPE_ID, "typename": _TYPE_NAME}]})
        res = apply_mod.apply_spec(backend, spec, stage_only=False)
    assert res["ok"] is True
    assert _kinds(res["applied"]) == ["plugin-step"]
    assert any(r.method == "POST" and "sdkmessageprocessingsteps" in r.url
               for r in m.request_history)


def test_apply_updates_plugin_step_config_on_drift(backend, tmp_path):
    # Live step matches the binding but has rank=1; spec says rank=5 → PATCH config.
    step = _step_spec(name="S", message="Update", entity="account", rank=5)
    spec = {"plugins": [_plugin_spec(tmp_path, steps=[step])]}
    with requests_mock.Mocker() as m:
        _mock_assembly_live(m, backend)
        _mock_step_live(m, backend,
                        _step_row(message="Update", entity="account", rank=1))
        res = apply_mod.apply_spec(backend, spec, stage_only=False)
    assert res["ok"] is True
    assert _kinds(res["updated"]) == ["plugin-step"]
    assert res["updated"][0]["diff"]["fields"] == ["rank"]
    patches = [r for r in m.request_history if r.method == "PATCH"]
    assert len(patches) == 1 and json.loads(patches[0].body)["rank"] == 5


def test_apply_plugin_step_message_change_is_replace_blocked(backend, tmp_path):
    # Live step is bound to Create; spec changes the message to Update → the
    # binding change needs a delete-and-recreate → replace_blocked (no write).
    step = _step_spec(name="S", message="Update", entity="account")
    spec = {"plugins": [_plugin_spec(tmp_path, steps=[step])]}
    with requests_mock.Mocker() as m:
        _mock_assembly_live(m, backend)
        _mock_step_live(m, backend, _step_row(message="Create", entity="account"))
        res = apply_mod.apply_spec(backend, spec, stage_only=False)
    assert res["ok"] is False
    assert _kinds(res["replace_blocked"]) == ["plugin-step"]
    assert "delete-and-recreate" in res["replace_blocked"][0]["reason"]
    assert [r for r in m.request_history if r.method != "GET"] == []  # no write


def test_apply_plugin_step_unchanged_is_skipped(backend, tmp_path):
    step = _step_spec(name="S", message="Update", entity="account", rank=1)
    spec = {"plugins": [_plugin_spec(tmp_path, steps=[step])]}
    with requests_mock.Mocker() as m:
        _mock_assembly_live(m, backend)
        _mock_step_live(m, backend,
                        _step_row(message="Update", entity="account", rank=1))
        res = apply_mod.apply_spec(backend, spec, stage_only=False)
    assert res["ok"] is True
    assert _kinds(res["skipped"]) == ["plugin-assembly", "plugin-step"]
    assert [r for r in m.request_history if r.method != "GET"] == []


def test_apply_registers_plugin_step_image(backend, tmp_path):
    # Existing assembly + existing (matching) step + a newly declared image.
    step = _step_spec(name="S", message="Update", entity="account", rank=1,
                      images=[{"alias": "PreImage", "image_type": "pre",
                               "attributes": "name"}])
    spec = {"plugins": [_plugin_spec(tmp_path, steps=[step])]}
    with requests_mock.Mocker() as m:
        _mock_assembly_live(m, backend)
        _mock_step_live(m, backend,
                        _step_row(message="Update", entity="account", rank=1))
        _mock_sdkmessage(m, backend, name="Update")  # _resolve_sdkmessage_name (by id)
        _mock_image_absent(m, backend)
        res = apply_mod.apply_spec(backend, spec, stage_only=False)
    assert res["ok"] is True
    assert _kinds(res["skipped"]) == ["plugin-assembly", "plugin-step"]
    assert _kinds(res["applied"]) == ["plugin-image"]
    assert any(r.method == "POST" and "sdkmessageprocessingstepimages" in r.url
               for r in m.request_history)


def test_apply_dry_run_plugin_greenfield_is_planned(dry_backend, tmp_path):
    # Greenfield under dry-run: assembly absent, so its whole subtree is planned
    # and no write is issued.
    spec = {"plugins": [_plugin_spec(
        tmp_path, types=[{"type_name": _TYPE_NAME}],
        steps=[_step_spec(images=[{"alias": "PreImage", "image_type": "pre"}])])]}
    with requests_mock.Mocker() as m:
        m.get(dry_backend.url_for("pluginassemblies"), json={"value": []})
        res = apply_mod.apply_spec(dry_backend, spec, stage_only=False)
    assert res["ok"] is True
    assert _kinds(res["planned"]) == [
        "plugin-assembly", "plugin-type", "plugin-step", "plugin-image"]
    assert _writes(m) == []


def test_apply_rejects_plugin_missing_file(backend):
    spec = {"plugins": [{"assembly": _ASM_NAME}]}
    with pytest.raises(D365Error, match="missing required field 'file'"):
        apply_mod.apply_spec(backend, spec)


def test_apply_rejects_plugin_step_missing_message(backend, tmp_path):
    spec = {"plugins": [_plugin_spec(tmp_path,
                                     steps=[{"name": "S", "plugin_type": _TYPE_NAME}])]}
    with pytest.raises(D365Error, match="missing required field 'message'"):
        apply_mod.apply_spec(backend, spec)


# ── Prune (#553): solution-bounded, gated removal of org-extras ──────────────
#
# Prune-candidates are members of the TARGET SOLUTION not declared in the spec,
# limited to the six prune-eligible kinds. Detection runs only under --prune or
# --dry-run. Default deletes nothing; --prune + confirmation deletes schema-only
# extras; data-bearing (entity/attribute) need --allow-data-loss too.

# Reuse the existing role / web-resource ids defined earlier in this file
# (_ROLE_ID, _WR_ID) — do NOT redefine them, or the module-level rebind silently
# breaks the role/web-resource tests above. A fresh id is needed only for the
# saved-query (view) case.
_SQ_ID = "cccccccc-cccc-cccc-cccc-cccccccccccc"


def _mock_solution_prune(m, backend, components):
    """Mock the solution existence GET + its component list for prune detection.

    `components` is a list of (componenttype:int, objectid:str) tuples; they
    become the solution's members that detection diffs against the spec.
    """
    m.get(backend.url_for("solutions"),
          json={"value": [{"solutionid": _GUID2, "uniquename": "ContosoCore"}]})
    m.get(backend.url_for("solutioncomponents"),
          json={"value": [{"componenttype": ct, "objectid": oid,
                           "rootcomponentbehavior": 0} for ct, oid in components]})


def test_apply_dry_run_reports_solution_role_extra_as_prune_candidate(dry_backend):
    # A role lives in the target solution; the spec declares no roles → prune-
    # candidate. Dry-run surfaces it (deleted=False) and writes nothing.
    spec = {"solution": {"unique_name": "ContosoCore"}}
    with requests_mock.Mocker() as m:
        _mock_solution_prune(m, dry_backend, [(20, _ROLE_ID)])
        m.get(dry_backend.url_for(f"roles({_ROLE_ID})"), json={"name": "Extra Role"})
        res = apply_mod.apply_spec(dry_backend, spec)
    assert res["ok"] is True
    assert res["pruned"] == [
        {"kind": "security-role", "name": "Extra Role", "deleted": False}]
    assert _writes(m) == []


def test_apply_prune_deletes_schema_only_extra(backend):
    # A web resource in the target solution, absent from the spec, is deleted
    # under --prune — schema-only kinds need no extra force.
    spec = {"solution": {"unique_name": "ContosoCore"}}
    with requests_mock.Mocker() as m:
        _mock_solution_prune(m, backend, [(61, _WR_ID)])
        m.get(backend.url_for(f"webresourceset({_WR_ID})"),
              json={"name": "contoso_/orphan.js"})
        del_mock = m.delete(backend.url_for(f"webresourceset({_WR_ID})"), status_code=204)
        res = apply_mod.apply_spec(backend, spec, prune=True)
    assert res["ok"] is True
    assert res["pruned"] == [
        {"kind": "webresource", "name": "contoso_/orphan.js", "deleted": True}]
    assert del_mock.called


def test_apply_prune_deletes_plugin_step_extra(backend):
    # Plug-in step (solution componenttype 92) prune: resolved via
    # sdkmessageprocessingsteps and deleted with unregister_step (schema-only).
    spec = {"solution": {"unique_name": "ContosoCore"}}
    with requests_mock.Mocker() as m:
        _mock_solution_prune(m, backend, [(92, _STEP_ID)])
        m.get(backend.url_for(f"sdkmessageprocessingsteps({_STEP_ID})"),
              json={"name": "Orphan Step"})
        del_mock = m.delete(
            backend.url_for(f"sdkmessageprocessingsteps({_STEP_ID})"), status_code=204)
        res = apply_mod.apply_spec(backend, spec, prune=True)
    assert res["ok"] is True
    assert res["pruned"] == [
        {"kind": "plugin-step", "name": "Orphan Step", "deleted": True}]
    assert del_mock.called


def test_apply_prune_refuses_data_bearing_without_force(backend):
    # An entity is data-bearing: --prune alone reports it but never deletes it.
    spec = {"solution": {"unique_name": "ContosoCore"}}
    with requests_mock.Mocker() as m:
        _mock_solution_prune(m, backend, [(1, _ENT_ID)])
        m.get(backend.url_for(f"EntityDefinitions({_ENT_ID})"),
              json={"LogicalName": "contoso_orphan"})
        res = apply_mod.apply_spec(backend, spec, prune=True)
    assert res["ok"] is True
    assert res["pruned"] == [{
        "kind": "entity", "name": "contoso_orphan", "deleted": False,
        "reason": "data-bearing; pass --allow-data-loss to delete"}]
    assert _writes(m) == []  # nothing deleted


def test_apply_prune_deletes_data_bearing_with_allow_data_loss(backend):
    spec = {"solution": {"unique_name": "ContosoCore"}}
    with requests_mock.Mocker() as m:
        _mock_solution_prune(m, backend, [(1, _ENT_ID)])
        m.get(backend.url_for(f"EntityDefinitions({_ENT_ID})"),
              json={"LogicalName": "contoso_orphan"})
        # delete_entity's pre-flight reads the live definition by logical name.
        m.get(backend.url_for("EntityDefinitions(LogicalName='contoso_orphan')"),
              json={"IsCustomEntity": True, "IsManaged": False, "MetadataId": _ENT_ID})
        del_mock = m.delete(
            backend.url_for("EntityDefinitions(LogicalName='contoso_orphan')"),
            status_code=204)
        res = apply_mod.apply_spec(backend, spec, prune=True, allow_data_loss=True)
    assert res["ok"] is True
    assert res["pruned"] == [
        {"kind": "entity", "name": "contoso_orphan", "deleted": True}]
    assert del_mock.called


def test_prune_candidates_attribute_scoped_to_declared_entity(backend):
    # Attribute prune is per declared entity: only custom attributes that are
    # solution members AND absent from the entity's declared `attributes:` list.
    spec = {"solution": {"unique_name": "ContosoCore"},
            "entities": [{"schema_name": "contoso_Project",
                          "attributes": [{"schema_name": "contoso_Keep"}]}]}
    with requests_mock.Mocker() as m:
        _mock_solution_prune(m, backend, [(2, _ATTR_ID)])
        m.get(backend.url_for("EntityDefinitions(LogicalName='contoso_project')/Attributes"),
              json={"value": [
                  {"LogicalName": "contoso_keep", "IsCustomAttribute": True,
                   "MetadataId": "00000000-0000-0000-0000-000000000001"},
                  {"LogicalName": "contoso_orphan", "IsCustomAttribute": True,
                   "MetadataId": _ATTR_ID},
                  # a non-custom column that happens to be a solution member is ignored
                  {"LogicalName": "createdon", "IsCustomAttribute": False,
                   "MetadataId": _ATTR_ID},
              ]})
        cands = apply_mod._prune_candidates(backend, spec, "ContosoCore")
    assert cands == [{"kind": "attribute", "name": "contoso_orphan",
                      "ref": "contoso_orphan", "entity": "contoso_project"}]


def test_prune_candidates_view_scoped_to_declared_entity(backend):
    spec = {"solution": {"unique_name": "ContosoCore"},
            "entities": [{"schema_name": "contoso_Project",
                          "views": [{"name": "Active Projects"}]}]}
    with requests_mock.Mocker() as m:
        _mock_solution_prune(m, backend, [(26, _SQ_ID)])
        m.get(backend.url_for("savedqueries"),
              json={"value": [
                  {"name": "Active Projects", "querytype": 0, "isdefault": False,
                   "savedqueryid": "00000000-0000-0000-0000-0000000000aa"},
                  {"name": "Orphan View", "querytype": 0, "isdefault": False,
                   "savedqueryid": _SQ_ID},
              ]})
        cands = apply_mod._prune_candidates(backend, spec, "ContosoCore")
    assert cands == [{"kind": "view", "name": "Orphan View",
                      "ref": _SQ_ID, "entity": None}]


def test_prune_candidates_ignores_undeclared_collection(backend):
    # The entity declares no `views:` key → the spec is NOT authoritative over its
    # views, so a solution-member view is never a prune-candidate.
    spec = {"solution": {"unique_name": "ContosoCore"},
            "entities": [{"schema_name": "contoso_Project"}]}
    with requests_mock.Mocker() as m:
        _mock_solution_prune(m, backend, [(26, _SQ_ID)])
        sq = m.get(backend.url_for("savedqueries"), json={"value": []})
        cands = apply_mod._prune_candidates(backend, spec, "ContosoCore")
    assert cands == []
    assert not sq.called  # the views collection is never even read


def test_prune_candidates_matches_declared_name_case_insensitively(backend):
    # The org stores the web resource lower-cased; the spec declares it with mixed
    # case. The Web API's `name eq` is case-insensitive, so apply already treats
    # them as the same component — prune must NOT report the declared one as an
    # extra and delete it.
    spec = {"solution": {"unique_name": "ContosoCore"},
            "webresources": [{"name": "Contoso_/Orphan.js", "file": "x.js"}]}
    with requests_mock.Mocker() as m:
        _mock_solution_prune(m, backend, [(61, _WR_ID)])
        m.get(backend.url_for(f"webresourceset({_WR_ID})"),
              json={"name": "contoso_/orphan.js"})  # org's stored (lower) casing
        cands = apply_mod._prune_candidates(backend, spec, "ContosoCore")
    assert cands == []  # declared (case-insensitively) → not a prune-candidate


def test_apply_prune_requires_solution(backend):
    spec = {"entities": [{"schema_name": "contoso_X", "display_name": "X"}]}
    with pytest.raises(D365Error, match="--prune requires a target solution"):
        apply_mod.apply_spec(backend, spec, prune=True)


def test_apply_without_prune_or_dry_run_skips_detection(backend):
    # A plain apply (no --prune, no --dry-run) never reads solution components.
    spec = {"solution": {"unique_name": "ContosoCore"}}
    with requests_mock.Mocker() as m:
        m.get(backend.url_for("solutions"),
              json={"value": [{"solutionid": _GUID2, "uniquename": "ContosoCore"}]})
        sc = m.get(backend.url_for("solutioncomponents"), json={"value": []})
        res = apply_mod.apply_spec(backend, spec)
    assert res["pruned"] == []
    assert not sc.called


def test_apply_cmd_prune_json_without_yes_aborts(backend, monkeypatch, tmp_path):
    # Under --json (no TTY) --prune without --yes must abort before any write.
    monkeypatch.setattr(CLIContext, "backend", lambda self: backend)
    spec_path = tmp_path / "s.yaml"
    spec_path.write_text("solution:\n  unique_name: ContosoCore\n")
    with requests_mock.Mocker() as m:
        result = CliRunner().invoke(
            cli, ["--json", "apply", "-f", str(spec_path), "--prune"])
    assert result.exit_code == 1, result.output
    env = json.loads(result.output)
    assert env["ok"] is False
    assert "--yes" in env["error"]
    assert _writes(m) == []  # refused before touching the backend


def test_apply_cmd_allow_data_loss_requires_prune(backend, monkeypatch, tmp_path):
    # --allow-data-loss without --prune is a usage error, not a silent no-op.
    monkeypatch.setattr(CLIContext, "backend", lambda self: backend)
    spec_path = tmp_path / "s.yaml"
    spec_path.write_text("solution:\n  unique_name: ContosoCore\n")
    result = CliRunner().invoke(
        cli, ["--json", "apply", "-f", str(spec_path), "--allow-data-loss"])
    assert result.exit_code == 2, result.output  # click.UsageError
    assert "--allow-data-loss only applies with --prune" in result.output


def test_apply_cmd_prune_requires_solution_usage_error(backend, monkeypatch, tmp_path):
    # --prune with no target solution is a CLI usage error (exit 2), rejected at the
    # command layer before any backend call.
    monkeypatch.setattr(CLIContext, "backend", lambda self: backend)
    spec_path = tmp_path / "s.yaml"
    spec_path.write_text("entities:\n  - schema_name: contoso_X\n    display_name: X\n")
    result = CliRunner().invoke(
        cli, ["--json", "apply", "-f", str(spec_path), "--prune", "--yes"])
    assert result.exit_code == 2, result.output  # click.UsageError
    assert "--prune requires a target solution" in result.output


def test_apply_dry_run_prune_suppresses_would_prune_on_replace_blocked(dry_backend):
    # When a reconcile is replace-blocked, a real --prune run suppresses deletes;
    # the dry-run preview must mirror that — the candidate carries no would_prune.
    spec = {
        "solution": {"unique_name": "ContosoCore"},
        "entities": [{"schema_name": "contoso_Project", "display_name": "Project",
                      "ownership": "OrganizationOwned"}],  # drift vs live UserOwned
    }
    with requests_mock.Mocker() as m:
        _mock_solution_prune(m, dry_backend, [(20, _ROLE_ID)])
        _mock_entity_create(m, dry_backend, exists=True, ownership="UserOwned")
        m.get(dry_backend.url_for(f"roles({_ROLE_ID})"), json={"name": "Extra Role"})
        res = apply_mod.apply_spec(dry_backend, spec, prune=True)
    assert res["replace_blocked"], "expected an ownership replace-block"
    assert res["pruned"] == [
        {"kind": "security-role", "name": "Extra Role", "deleted": False}]  # no would_prune


def test_apply_cmd_prune_yes_deletes_schema_only(backend, monkeypatch, tmp_path):
    monkeypatch.setattr(CLIContext, "backend", lambda self: backend)
    spec_path = tmp_path / "s.yaml"
    spec_path.write_text("solution:\n  unique_name: ContosoCore\n")
    with requests_mock.Mocker() as m:
        _mock_solution_prune(m, backend, [(61, _WR_ID)])
        m.get(backend.url_for(f"webresourceset({_WR_ID})"),
              json={"name": "contoso_/orphan.js"})
        del_mock = m.delete(backend.url_for(f"webresourceset({_WR_ID})"), status_code=204)
        result = CliRunner().invoke(
            cli, ["--json", "apply", "-f", str(spec_path), "--prune", "--yes"])
    assert result.exit_code == 0, result.output
    env = json.loads(result.output)
    assert {"kind": "webresource", "name": "contoso_/orphan.js", "deleted": True} \
        in env["data"]["pruned"]
    assert del_mock.called


# ── Backfill: validate_spec branches not yet covered (#592) ────────────────────
#
# Each test targets a specific uncovered line / branch in validate_spec or
# apply_spec; no network calls needed for pure-validation tests (assert
# m.request_history == [] where applicable).


# L53-54: _columns with an explicit int width on a dict column.
def test_columns_dict_with_width():
    """A dict column carrying an explicit int width is forwarded correctly."""
    from crm.core.apply import _columns
    result = _columns([{"name": "contoso_code", "width": 150}])
    assert result == [("contoso_code", 150)]


# L83: _require raises when obj is not a dict.
def test_require_non_mapping_raises():
    from crm.core.apply import _require
    with pytest.raises(Exception, match="must be a mapping"):
        _require("not-a-dict", ("key",), "publisher")


# L99: _validate_option raises when opt is not a dict.
def test_validate_option_non_mapping_raises():
    spec = {"optionsets": [{"name": "contoso_p", "display_name": "P",
                            "options": ["bad"]}]}  # string, not dict
    with pytest.raises(Exception, match="each option must be a mapping"):
        apply_mod.validate_spec(spec)


# L111: _validate_column empty string.
def test_validate_column_empty_string_raises():
    spec = {"entities": [{"schema_name": "contoso_Project", "display_name": "P",
                          "views": [{"name": "V", "columns": [""]}]}]}
    with pytest.raises(Exception, match="column name must not be empty"):
        apply_mod.validate_spec(spec)


# L117: dict column with non-string name raises.
def test_validate_column_dict_non_string_name_raises():
    spec = {"entities": [{"schema_name": "contoso_Project", "display_name": "P",
                          "views": [{"name": "V", "columns": [{"name": 123}]}]}]}
    with pytest.raises(Exception, match="non-empty string name"):
        apply_mod.validate_spec(spec)


# L118: dict column with non-int width raises.
def test_validate_column_dict_non_int_width_raises():
    spec = {"entities": [{"schema_name": "contoso_Project", "display_name": "P",
                          "views": [{"name": "V",
                                     "columns": [{"name": "col", "width": "100"}]}]}]}
    with pytest.raises(Exception, match="width must be an integer"):
        apply_mod.validate_spec(spec)


# L120: column that is neither string nor dict raises.
def test_validate_column_non_string_non_dict_raises():
    spec = {"entities": [{"schema_name": "contoso_Project", "display_name": "P",
                          "views": [{"name": "V", "columns": [42]}]}]}
    with pytest.raises(Exception, match="column must be a string or a mapping"):
        apply_mod.validate_spec(spec)


# L126: spec is not a dict.
def test_validate_spec_non_dict_raises():
    with pytest.raises(Exception, match="spec must be a mapping"):
        apply_mod.validate_spec(["entities"])


# L138: top-level key present but not a list.
def test_validate_spec_entities_not_list_raises():
    spec = {"entities": "contoso_Project"}
    with pytest.raises(Exception, match="entities must be a list"):
        apply_mod.validate_spec(spec)


# L142: spec with no recognised keys is empty.
def test_validate_spec_empty_raises():
    spec = {"unknown_key": "value"}
    with pytest.raises(Exception, match="spec is empty"):
        apply_mod.validate_spec(spec)


# L161: picklist missing both optionset_name and options.
def test_validate_spec_picklist_missing_optionset_or_options():
    spec = {"entities": [{"schema_name": "contoso_Project", "display_name": "P",
                          "attributes": [{"kind": "picklist", "schema_name": "contoso_Stage",
                                          "display_name": "Stage"}]}]}
    with pytest.raises(Exception, match="requires optionset_name or options"):
        apply_mod.validate_spec(spec)


# L186: formula_definition is not a string.
def test_validate_spec_formula_not_string_raises():
    spec = {"entities": [{"schema_name": "contoso_Project", "display_name": "P",
                          "attributes": [{"kind": "decimal", "schema_name": "contoso_X",
                                          "display_name": "X", "source_type": "calculated",
                                          "formula_definition": 123}]}]}
    with pytest.raises(Exception, match="formula_definition must be a string"):
        apply_mod.validate_spec(spec)


# L207 (non-list branch): view columns is a string, not a list — caught after _require.
def test_validate_spec_view_columns_not_list_raises():
    spec = {"entities": [{"schema_name": "contoso_Project", "display_name": "P",
                          "views": [{"name": "V", "columns": "contoso_name"}]}]}
    with pytest.raises(Exception, match="columns must be a non-empty list"):
        apply_mod.validate_spec(spec)


# L222: webresource name field is not a string (non-string name key).
def test_validate_spec_webresource_name_not_string_raises():
    spec = {"webresources": [{"name": 123, "file": "app.js"}]}
    with pytest.raises(Exception, match="must be a string"):
        apply_mod.validate_spec(spec)


# L224: webresource webresourcetype not an int.
def test_validate_spec_webresource_type_not_int_raises():
    spec = {"webresources": [{"name": "app.js", "file": "app.js",
                               "webresourcetype": "3"}]}
    with pytest.raises(Exception, match="webresourcetype must be an integer"):
        apply_mod.validate_spec(spec)


# L233: security_role name not a string.
def test_validate_spec_role_name_not_string_raises():
    spec = {"security_roles": [{"name": 123,
                                "privileges": [{"depth": "global",
                                                "privilege_names": ["prvRead"]}]}]}
    with pytest.raises(Exception, match="name must be a string"):
        apply_mod.validate_spec(spec)


# L236: security_role business_unit not a string.
def test_validate_spec_role_business_unit_not_string_raises():
    spec = {"security_roles": [{"name": "R", "business_unit": 42,
                                "privileges": [{"depth": "global",
                                                "privilege_names": ["prvRead"]}]}]}
    with pytest.raises(Exception, match="business_unit must be a string"):
        apply_mod.validate_spec(spec)


# L241: security_role with no privileges.
def test_validate_spec_role_no_privileges_raises():
    spec = {"security_roles": [{"name": "R", "privileges": []}]}
    with pytest.raises(Exception, match="at least one privilege row is required"):
        apply_mod.validate_spec(spec)


# L245: privilege depth not a string.
def test_validate_spec_privilege_depth_not_string_raises():
    spec = {"security_roles": [{"name": "R",
                                "privileges": [{"depth": 1,
                                                "privilege_names": ["prvRead"]}]}]}
    with pytest.raises(Exception, match="depth must be a string"):
        apply_mod.validate_spec(spec)


# L253: privilege access field is not a list.
def test_validate_spec_privilege_access_not_list_raises():
    spec = {"security_roles": [{"name": "R",
                                "privileges": [{"depth": "global",
                                                "access": "Create"}]}]}
    with pytest.raises(Exception, match="'access' must be a list"):
        apply_mod.validate_spec(spec)


# L255: privilege privilege_names items not strings.
def test_validate_spec_privilege_names_items_not_strings_raises():
    spec = {"security_roles": [{"name": "R",
                                "privileges": [{"depth": "global",
                                                "privilege_names": [123]}]}]}
    with pytest.raises(Exception, match="items must be strings"):
        apply_mod.validate_spec(spec)


# L263: plugin file is not a string.
def test_validate_spec_plugin_file_not_string_raises():
    spec = {"plugins": [{"file": 123}]}
    with pytest.raises(Exception, match="file must be a string"):
        apply_mod.validate_spec(spec)


# L265: plugin assembly not a string.
def test_validate_spec_plugin_assembly_not_string_raises():
    spec = {"plugins": [{"file": "p.dll", "assembly": 123}]}
    with pytest.raises(Exception, match="assembly must be a string"):
        apply_mod.validate_spec(spec)


# L272: plugin type type_name not a string.
def test_validate_spec_plugin_type_typename_not_string_raises():
    spec = {"plugins": [{"file": "p.dll",
                         "types": [{"type_name": 123}]}]}
    with pytest.raises(Exception, match="type_name must be a string"):
        apply_mod.validate_spec(spec)


# L280: plugin step field not a string (e.g. entity as int).
def test_validate_spec_plugin_step_field_not_string_raises():
    spec = {"plugins": [{"file": "p.dll",
                         "steps": [{"name": "S", "message": "Create",
                                    "plugin_type": "My.Type", "entity": 42}]}]}
    with pytest.raises(Exception, match="'entity' must be a string"):
        apply_mod.validate_spec(spec)


# L282: plugin step rank not an int.
def test_validate_spec_plugin_step_rank_not_int_raises():
    spec = {"plugins": [{"file": "p.dll",
                         "steps": [{"name": "S", "message": "Create",
                                    "plugin_type": "My.Type", "rank": "1"}]}]}
    with pytest.raises(Exception, match="rank must be an integer"):
        apply_mod.validate_spec(spec)


# L290: plugin step image field not a string.
def test_validate_spec_plugin_step_image_field_not_string_raises():
    spec = {"plugins": [{"file": "p.dll",
                         "steps": [{"name": "S", "message": "Create",
                                    "plugin_type": "My.Type",
                                    "images": [{"alias": "Pre", "image_type": "pre",
                                                "attributes": 999}]}]}]}
    with pytest.raises(Exception, match="'attributes' must be a string"):
        apply_mod.validate_spec(spec)


# L312-313: _classify dry_run path (would_skip → skipped vs planned).
def test_classify_dry_run_would_skip_goes_to_skipped():
    from crm.core.apply import _classify
    applied: list = []; skipped: list = []; planned: list = []
    entry = {"kind": "publisher", "name": "p"}
    bucket = _classify({"_dry_run": True, "would_skip": True}, entry, applied, skipped, planned)
    assert bucket == "skipped" and skipped == [entry]
    assert planned == [] and applied == []


def test_classify_dry_run_would_create_goes_to_planned():
    from crm.core.apply import _classify
    applied: list = []; skipped: list = []; planned: list = []
    entry = {"kind": "publisher", "name": "p"}
    bucket = _classify({"_dry_run": True}, entry, applied, skipped, planned)
    assert bucket == "planned" and planned == [entry]
    assert applied == [] and skipped == []


# L397-399: _reconcile D365Error routes to failed + aborts.
def test_reconcile_d365error_goes_to_failed_and_aborts():
    from crm.core.apply import _reconcile, _Aborted
    from crm.utils.d365_backend import D365Error
    failed: list = []
    routes = {"updated": [], "skipped": [], "replace_blocked": []}
    entry = {"kind": "entity", "name": "X"}

    def boom():
        raise D365Error("boom")

    with pytest.raises(_Aborted):
        _reconcile(entry, boom, failed, routes)
    assert failed[0]["error"] == "boom"


# L426/428: _reconcile_entity "no changes → skipped" branch directly.
def test_reconcile_entity_no_drift_returns_skipped(backend):
    with requests_mock.Mocker() as m:
        _mock_entity_live(m, backend, display_name="Project", display_collection_name="Projects")
        ent = {"schema_name": "contoso_Project", "display_name": "Project",
               "display_collection_name": "Projects"}
        from crm.core.apply import _reconcile_entity
        verdict, payload = _reconcile_entity(backend, ent, "contoso_project", None,
                                             {"kind": "entity", "name": "contoso_Project"})
    assert verdict == "skipped"


# L466: _reconcile_attribute lookup kind → skipped immediately (no attribute_info GET).
def test_reconcile_attribute_lookup_kind_is_skipped(backend):
    """_reconcile_attribute with kind='lookup' returns skipped without reading attribute_info."""
    from crm.core.apply import _reconcile_attribute
    attr = {"kind": "lookup", "schema_name": "contoso_Owner", "display_name": "Owner",
            "target_entity": "systemuser"}
    entry = {"kind": "attribute", "name": "contoso_Owner"}
    with requests_mock.Mocker() as m:
        # No mocks needed — lookup returns skipped before any GET.
        verdict, payload = _reconcile_attribute(backend, attr, "contoso_project", None, entry)
    assert verdict == "skipped"
    assert m.call_count == 0  # no network calls for lookup kind


# L527-528: _read_file_bytes OSError → D365Error, recorded as failed entry.
def test_read_file_bytes_missing_file_raises_d365error(backend):
    spec = {"webresources": [{"name": "app.js", "file": "/nonexistent/path/app.js"}]}
    with requests_mock.Mocker() as m:
        m.get(backend.url_for("webresourceset"), json={"value": []})
        res = apply_mod.apply_spec(backend, spec, stage_only=False)
    assert res["ok"] is False
    assert any("cannot read file" in e.get("error", "") for e in res["failed"])


# L548: _reconcile_webresource display_name drift triggers update.
def test_apply_updates_webresource_display_name_on_drift(backend, tmp_path):
    spec = {"webresources": [_wr_spec(tmp_path, body=b"console.log(1)",
                                      display_name="New Name")]}
    with requests_mock.Mocker() as m:
        # Same content but different display_name → PATCH display_name only.
        _mock_webresource_live(m, backend, content=b"console.log(1)",
                               display_name="Old Name")
        m.post(backend.url_for("PublishAllXml"), status_code=204)
        res = apply_mod.apply_spec(backend, spec, stage_only=False)
    assert res["ok"] is True
    assert _kinds(res["updated"]) == ["webresource"]
    assert res["updated"][0]["diff"]["fields"] == ["display_name"]


# L695/697: _reconcile_plugin_step filtering_attributes / configuration drift.
def test_apply_updates_plugin_step_filtering_and_configuration(backend, tmp_path):
    step = _step_spec(name="S", message="Update", entity="account",
                      filtering_attributes="name,address1_city",
                      configuration="new-config")
    spec = {"plugins": [_plugin_spec(tmp_path, steps=[step])]}
    with requests_mock.Mocker() as m:
        _mock_assembly_live(m, backend)
        _mock_step_live(m, backend, _step_row(message="Update", entity="account",
                                               filtering=None, configuration=None))
        res = apply_mod.apply_spec(backend, spec, stage_only=False)
    assert res["ok"] is True
    assert _kinds(res["updated"]) == ["plugin-step"]
    patched = sorted(res["updated"][0]["diff"]["fields"])
    assert "configuration" in patched and "filtering_attributes" in patched


# L705/707: _reconcile_plugin_step returns unchanged → skipped.
def test_reconcile_plugin_step_unchanged_returns_skipped(backend):
    from crm.core.apply import _reconcile_plugin_step
    live = _step_row(message="Create", entity="account", stage=40, mode=0, rank=1)
    step = {"name": "S", "message": "Create", "plugin_type": _TYPE_NAME, "entity": "account"}
    verdict, _ = _reconcile_plugin_step(backend, step, live, None,
                                        {"kind": "plugin-step", "name": "S"})
    assert verdict == "skipped"


# L768->777 / L769->774: assembly_created=True → register type directly (no list_types).
def test_apply_creates_plugin_type_when_assembly_freshly_created(backend, tmp_path):
    # After a fresh assembly create, register_type resolves the assembly id via
    # a second GET on pluginassemblies — mock it to return the new id.
    spec = {"plugins": [_plugin_spec(tmp_path, types=[{"type_name": _TYPE_NAME}])]}
    asm_row = {"pluginassemblyid": _ASM_ID, "name": _ASM_NAME,
               "content": ""}
    with requests_mock.Mocker() as m:
        # First GET: find_assembly (absence probe) → empty.
        # Second GET: register_type's _resolve_id_by_name → return new assembly.
        m.get(backend.url_for("pluginassemblies"),
              [{"json": {"value": []}}, {"json": {"value": [asm_row]}}])
        m.post(backend.url_for("pluginassemblies"), status_code=204,
               headers={"OData-EntityId": backend.url_for(f"pluginassemblies({_ASM_ID})")})
        m.post(backend.url_for("plugintypes"), status_code=204,
               headers={"OData-EntityId": backend.url_for(f"plugintypes({_TYPE_ID})")})
        res = apply_mod.apply_spec(backend, spec, stage_only=False)
    assert res["ok"] is True, res["failed"]
    assert _kinds(res["applied"]) == ["plugin-assembly", "plugin-type"]
    # list_types must NOT have been called (no plugintypes GET for listing).
    type_gets = [r for r in m.request_history if r.method == "GET"
                 and "plugintypes" in r.url]
    assert type_gets == []


# L808-810: step is replace_blocked → images under it are skipped (step_blocked=True).
def test_apply_plugin_images_skipped_when_step_replace_blocked(backend, tmp_path):
    step = _step_spec(name="S", message="Update", entity="account",
                      images=[{"alias": "PreImage", "image_type": "pre"}])
    spec = {"plugins": [_plugin_spec(tmp_path, steps=[step])]}
    with requests_mock.Mocker() as m:
        _mock_assembly_live(m, backend)
        # Binding change → replace_blocked
        _mock_step_live(m, backend, _step_row(message="Create", entity="account"))
        res = apply_mod.apply_spec(backend, spec, stage_only=False)
    assert res["ok"] is False
    assert _kinds(res["replace_blocked"]) == ["plugin-step"]
    # image is silently skipped (not reported at all) when the step is blocked.
    all_entries = res["applied"] + res["skipped"] + res["planned"] + res["failed"]
    assert not any(e["kind"] == "plugin-image" for e in all_entries)


# L818: step_id is None (dry-run, step would be created) → image reported planned.
def test_apply_dry_run_plugin_step_new_image_planned(dry_backend, tmp_path):
    step = _step_spec(name="S", message="Create",
                      images=[{"alias": "PreImage", "image_type": "pre"}])
    spec = {"plugins": [_plugin_spec(tmp_path, steps=[step])]}
    with requests_mock.Mocker() as m:
        _mock_assembly_live(m, dry_backend)
        m.get(dry_backend.url_for("sdkmessageprocessingsteps"), json={"value": []})
        _mock_sdkmessage(m, dry_backend, name="Create")
        m.get(dry_backend.url_for("plugintypes"),
              json={"value": [{"plugintypeid": _TYPE_ID, "typename": _TYPE_NAME}]})
        res = apply_mod.apply_spec(dry_backend, spec, stage_only=False)
    assert res["ok"] is True
    assert any(e["kind"] == "plugin-image" and e["name"] == "PreImage"
               for e in res["planned"])
    assert _writes(m) == []


# L820-821: existing image → skipped (find_step_image returns non-None).
def test_apply_plugin_existing_image_is_skipped(backend, tmp_path):
    step = _step_spec(name="S", message="Update", entity="account", rank=1,
                      images=[{"alias": "PreImage", "image_type": "pre"}])
    spec = {"plugins": [_plugin_spec(tmp_path, steps=[step])]}
    img_row = {"sdkmessageprocessingstepimageid": _IMG_ID, "alias": "PreImage"}
    with requests_mock.Mocker() as m:
        _mock_assembly_live(m, backend)
        _mock_step_live(m, backend, _step_row(message="Update", entity="account", rank=1))
        m.get(backend.url_for("sdkmessageprocessingstepimages"),
              json={"value": [img_row]})
        res = apply_mod.apply_spec(backend, spec, stage_only=False)
    assert res["ok"] is True
    assert any(e["kind"] == "plugin-image" and e["name"] == "PreImage"
               for e in res["skipped"])


# L1130: missing MetadataId in solution-component phase → failed (not abort).
def test_apply_solution_component_missing_metadata_id_is_failed(backend):
    spec = {
        "solution": {"unique_name": "contoso_test"},
        "optionsets": [{"name": "contoso_tagset", "display_name": "Tag Set",
                        "options": [{"value": 1, "label": "Alpha"}]}],
    }
    with requests_mock.Mocker() as m:
        m.get(backend.url_for("solutions"),
              json={"value": [{"solutionid": _GUID2, "uniquename": "contoso_test",
                               "ismanaged": False}]})
        # Optionset EXISTS with all spec options already present → reconcile is a no-op.
        # Return a record WITHOUT MetadataId so the component-phase GET finds nothing.
        m.get(backend.url_for("GlobalOptionSetDefinitions(Name='contoso_tagset')"),
              json={"Name": "contoso_tagset",
                    "Options": [{"Value": 1, "Label": _label("Alpha")}]})  # no MetadataId
        m.post(backend.url_for("PublishAllXml"), status_code=204)
        res = apply_mod.apply_spec(backend, spec, include_referenced_optionsets=True)
    assert any("MetadataId" in e.get("error", "") for e in res["failed"])


# L1144-1147: AddSolutionComponent fails → recorded in failed, does NOT abort.
def test_apply_solution_component_add_failure_is_nonfatal(backend):
    spec = {
        "solution": {"unique_name": "contoso_test"},
        "optionsets": [{"name": "contoso_tagset", "display_name": "Tag Set",
                        "options": [{"value": 1, "label": "Alpha"}]}],
    }
    with requests_mock.Mocker() as m:
        m.get(backend.url_for("solutions"),
              json={"value": [{"solutionid": _GUID2, "uniquename": "contoso_test",
                               "ismanaged": False}]})
        m.get(backend.url_for("GlobalOptionSetDefinitions(Name='contoso_tagset')"),
              json={"MetadataId": _OS_ID, "Name": "contoso_tagset",
                    "Options": [{"Value": 1, "Label": _label("Alpha")}]})
        m.post(backend.url_for("AddSolutionComponent"),
               status_code=500, json={"error": {"message": "component add failed"}})
        m.post(backend.url_for("PublishAllXml"), status_code=204)
        res = apply_mod.apply_spec(backend, spec, include_referenced_optionsets=True)
    assert any("solution-component" == e.get("kind") for e in res["failed"])


# L1289-1290: security role dry_run → planned + continue (no roleid resolved).
def test_apply_dry_run_security_role_greenfield_is_planned(dry_backend):
    spec = {"security_roles": [_role_spec()]}
    with requests_mock.Mocker() as m:
        m.get(dry_backend.url_for("roles"), json={"value": []})
        res = apply_mod.apply_spec(dry_backend, spec, stage_only=False)
    assert res["ok"] is True
    assert _kinds(res["planned"]) == ["security-role"]
    assert _writes(m) == []


# L1336: prune dry_run would_prune=True when prune=True and not suppressed.
def test_apply_dry_run_prune_true_sets_would_prune(dry_backend):
    spec = {"solution": {"unique_name": "ContosoCore"}}
    with requests_mock.Mocker() as m:
        _mock_solution_prune(m, dry_backend, [(20, _ROLE_ID)])
        m.get(dry_backend.url_for(f"roles({_ROLE_ID})"), json={"name": "Extra Role"})
        res = apply_mod.apply_spec(dry_backend, spec, prune=True)
    pruned = res["pruned"]
    assert len(pruned) == 1
    assert pruned[0]["would_prune"] is True
    assert _writes(m) == []


# L1338: prune dry_run data_bearing without allow_data_loss → reason set (no would_prune).
def test_apply_dry_run_prune_data_bearing_no_force_has_reason(dry_backend):
    spec = {"solution": {"unique_name": "ContosoCore"}}
    with requests_mock.Mocker() as m:
        _mock_solution_prune(m, dry_backend, [(1, _ENT_ID)])
        m.get(dry_backend.url_for(f"EntityDefinitions({_ENT_ID})"),
              json={"LogicalName": "contoso_orphan"})
        res = apply_mod.apply_spec(dry_backend, spec, prune=True)
    pruned = res["pruned"]
    assert len(pruned) == 1
    assert "reason" in pruned[0]
    assert "data-bearing" in pruned[0]["reason"]
    assert pruned[0].get("would_prune") is None


# L1347-1350: prune delete failure → lands in failed (not abort).
def test_apply_prune_delete_failure_lands_in_failed(backend):
    spec = {"solution": {"unique_name": "ContosoCore"}}
    with requests_mock.Mocker() as m:
        _mock_solution_prune(m, backend, [(61, _WR_ID)])
        m.get(backend.url_for(f"webresourceset({_WR_ID})"),
              json={"name": "contoso_/orphan.js"})
        m.delete(backend.url_for(f"webresourceset({_WR_ID})"),
                 status_code=500, json={"error": {"message": "delete failed"}})
        res = apply_mod.apply_spec(backend, spec, prune=True)
    assert res["ok"] is False
    assert any(e.get("kind") == "webresource" and "error" in e for e in res["failed"])


# L876->874: entity-scoped loop is skipped when solution has no attr or view components.
def test_prune_candidates_no_entity_scoped_when_no_members(backend):
    spec = {"solution": {"unique_name": "ContosoCore"},
            "entities": [{"schema_name": "contoso_Project",
                          "attributes": [{"schema_name": "contoso_Keep"}],
                          "views": [{"name": "V"}]}]}
    with requests_mock.Mocker() as m:
        _mock_solution_prune(m, backend, [])
        cands = apply_mod._prune_candidates(backend, spec, "ContosoCore")
    assert cands == []


# L938: _prune_delete "view" kind.
# Views are entity-scoped (componenttype=26), so the spec must declare an entity
# with a `views:` key; the orphan view is in the solution but not in the spec.
def test_prune_delete_view_kind(backend):
    spec = {"solution": {"unique_name": "ContosoCore"},
            "entities": [{"schema_name": "contoso_Project", "display_name": "Project",
                          "views": [{"name": "Active Projects",
                                     "columns": ["contoso_name"]}]}]}
    with requests_mock.Mocker() as m:
        _mock_solution_prune(m, backend, [(26, _SQ_ID)])
        # read_entity_views for contoso_project: one declared ("Active Projects")
        # + one orphan ("Orphan View") that is a solution member.
        m.get(backend.url_for("savedqueries"), json={"value": [
            {"name": "Active Projects", "savedqueryid": "00000000-0000-0000-0000-0000000000aa",
             "querytype": 0, "isdefault": False},
            {"name": "Orphan View", "savedqueryid": _SQ_ID,
             "querytype": 0, "isdefault": False},
        ]})
        del_mock = m.delete(backend.url_for(f"savedqueries({_SQ_ID})"), status_code=204)
        # Also need entity probe for the create phase (spec declares entity).
        m.get(backend.url_for("EntityDefinitions(LogicalName='contoso_project')"),
              status_code=404)
        m.post(backend.url_for("EntityDefinitions"), status_code=204,
               headers={"OData-EntityId": backend.url_for(f"EntityDefinitions({_ENT_ID})")})
        m.get(backend.url_for(f"EntityDefinitions({_ENT_ID})"),
              json={"LogicalName": "contoso_project", "SchemaName": "contoso_Project"})
        m.post(backend.url_for("PublishAllXml"), status_code=204)
        res = apply_mod.apply_spec(backend, spec, prune=True)
    assert del_mock.called
    assert any(e["kind"] == "view" and e["name"] == "Orphan View" and e["deleted"]
               for e in res["pruned"])


# L943-946: _prune_delete "security-role" kind.
def test_prune_delete_security_role_kind(backend):
    spec = {"solution": {"unique_name": "ContosoCore"}}
    with requests_mock.Mocker() as m:
        _mock_solution_prune(m, backend, [(20, _ROLE_ID)])
        m.get(backend.url_for(f"roles({_ROLE_ID})"), json={"name": "Orphan Role"})
        del_mock = m.delete(backend.url_for(f"roles({_ROLE_ID})"), status_code=204)
        res = apply_mod.apply_spec(backend, spec, prune=True)
    assert del_mock.called
    assert res["pruned"] == [
        {"kind": "security-role", "name": "Orphan Role", "deleted": True}]


# L1106->1084: prune phase suppressed when convergence has failed entries.
def test_apply_prune_suppressed_on_failed_convergence(backend):
    # Entity create fails; the prune phase detects a candidate but does NOT delete
    # it because failed is non-empty (suppressed = True).
    spec = {"solution": {"unique_name": "ContosoCore"},
            "entities": [{"schema_name": "contoso_Project", "display_name": "P"}]}
    with requests_mock.Mocker() as m:
        _mock_solution_prune(m, backend, [(61, _WR_ID)])
        m.get(backend.url_for(f"webresourceset({_WR_ID})"),
              json={"name": "contoso_/orphan.js"})
        # entity create fails
        m.get(backend.url_for("EntityDefinitions(LogicalName='contoso_project')"),
              status_code=404)
        m.post(backend.url_for("EntityDefinitions"), status_code=500,
               json={"error": {"message": "entity create failed"}})
        del_mock = m.delete(backend.url_for(f"webresourceset({_WR_ID})"), status_code=204)
        res = apply_mod.apply_spec(backend, spec, prune=True)
    # The prune candidate is reported but NOT deleted.
    assert not del_mock.called
    assert res["pruned"] == [
        {"kind": "webresource", "name": "contoso_/orphan.js", "deleted": False}]


# ── Backfill round-2: remaining branch gaps (#592) ────────────────────────────


# L119: valid dict column passes through _validate_column without raising.
def test_validate_column_valid_dict_with_int_width_passes():
    spec = {"entities": [{"schema_name": "contoso_Project", "display_name": "P",
                          "views": [{"name": "V",
                                     "columns": [{"name": "contoso_name", "width": 150}]}]}]}
    apply_mod.validate_spec(spec)  # must not raise


# L426: display_collection_name drift on entity triggers update.
def test_apply_updates_entity_collection_name_on_drift(backend):
    ent = {"schema_name": "contoso_Project", "display_name": "Project",
           "display_collection_name": "Projects Renamed"}
    spec = {"entities": [ent]}
    with requests_mock.Mocker() as m:
        _mock_entity_live(m, backend, display_name="Project",
                          display_collection_name="Projects")
        m.post(backend.url_for("PublishAllXml"), status_code=204)
        res = apply_mod.apply_spec(backend, spec, stage_only=False)
    assert res["ok"] is True
    assert _kinds(res["updated"]) == ["entity"]
    puts = [r for r in m.request_history if r.method == "PUT"]
    assert len(puts) == 1


# L428: description drift on entity triggers update.
def test_apply_updates_entity_description_on_drift(backend):
    ent = {"schema_name": "contoso_Project", "display_name": "Project",
           "description": "New description"}
    spec = {"entities": [ent]}
    with requests_mock.Mocker() as m:
        # Live entity has no description → drift detected.
        _mock_entity_live(m, backend, display_name="Project")
        m.post(backend.url_for("PublishAllXml"), status_code=204)
        res = apply_mod.apply_spec(backend, spec, stage_only=False)
    assert res["ok"] is True
    assert _kinds(res["updated"]) == ["entity"]


# L466: display_name drift on an attribute triggers update.
def test_apply_updates_attribute_display_name_on_drift(backend):
    attr = {"kind": "string", "schema_name": "contoso_Code", "display_name": "New Code"}
    with requests_mock.Mocker() as m:
        _mock_entity_live(m, backend, display_name="Project")
        _mock_attribute_live(m, backend, logical="contoso_code", schema="contoso_Code",
                             display_name="Old Code")
        m.post(backend.url_for("PublishAllXml"), status_code=204)
        res = apply_mod.apply_spec(backend, _attr_spec(attr), stage_only=False)
    assert res["ok"] is True
    assert _kinds(res["updated"]) == ["attribute"]


# L472->474: required level drift on attribute (desired_required != live_required).
# Already covered by test_apply_updates_attribute_required_level_on_drift but
# that test does not explicitly assert the branch; call the function directly.
def test_reconcile_attribute_required_drift_adds_to_changes(backend):
    from crm.core.apply import _reconcile_attribute
    attr = {"kind": "string", "schema_name": "contoso_Code", "display_name": "Code",
            "required": "ApplicationRequired"}
    entry = {"kind": "attribute", "name": "contoso_Code"}
    with requests_mock.Mocker() as m:
        _mock_attribute_live(m, backend, logical="contoso_code", schema="contoso_Code",
                             display_name="Code", required="None")
        m.put(backend.url_for(
            "EntityDefinitions(LogicalName='contoso_project')/Attributes(LogicalName='contoso_code')"
            "/Microsoft.Dynamics.CRM.StringAttributeMetadata"), status_code=204)
        verdict, _ = _reconcile_attribute(backend, attr, "contoso_project", None, entry)
    assert verdict == "updated"


# L695: stage change in plugin step reconcile triggers update.
def test_apply_updates_plugin_step_stage_on_drift(backend, tmp_path):
    # Live step is postoperation (40); spec changes stage to preoperation (20).
    step = _step_spec(name="S", message="Update", entity="account",
                      stage="preoperation")
    spec = {"plugins": [_plugin_spec(tmp_path, steps=[step])]}
    with requests_mock.Mocker() as m:
        _mock_assembly_live(m, backend)
        # Live step: stage=40 (postoperation), spec wants preoperation (20).
        _mock_step_live(m, backend, _step_row(message="Update", entity="account",
                                               stage=40))
        res = apply_mod.apply_spec(backend, spec, stage_only=False)
    assert res["ok"] is True
    assert _kinds(res["updated"]) == ["plugin-step"]
    assert "stage" in res["updated"][0]["diff"]["fields"]


# L697: mode change in plugin step reconcile triggers update.
def test_apply_updates_plugin_step_mode_on_drift(backend, tmp_path):
    # Live step is sync (0); spec changes mode to async (1).
    step = _step_spec(name="S", message="Update", entity="account", mode="async")
    spec = {"plugins": [_plugin_spec(tmp_path, steps=[step])]}
    with requests_mock.Mocker() as m:
        _mock_assembly_live(m, backend)
        _mock_step_live(m, backend, _step_row(message="Update", entity="account",
                                               mode=0))
        res = apply_mod.apply_spec(backend, spec, stage_only=False)
    assert res["ok"] is True
    assert _kinds(res["updated"]) == ["plugin-step"]
    assert "mode" in res["updated"][0]["diff"]["fields"]


# L769->774: second declared type in a pre-existing assembly reuses live_typenames.
def test_apply_skips_second_type_using_cached_live_typenames(backend, tmp_path):
    # Pre-existing assembly with both types already registered → both skipped.
    # On the FIRST type iteration live_typenames=None → list_types GET fires.
    # On the SECOND type iteration live_typenames is already populated → no extra GET.
    types = [{"type_name": _TYPE_NAME},
             {"type_name": "Contoso.Plugins.OrderHandler"}]
    spec = {"plugins": [_plugin_spec(tmp_path, types=types)]}
    with requests_mock.Mocker() as m:
        _mock_assembly_live(m, backend)
        # list_types returns both types already registered.
        m.get(backend.url_for("plugintypes"), json={"value": [
            {"plugintypeid": _TYPE_ID, "typename": _TYPE_NAME},
            {"plugintypeid": "f6f6f6f6-f6f6-f6f6-f6f6-f6f6f6f6f6f6",
             "typename": "Contoso.Plugins.OrderHandler"},
        ]})
        res = apply_mod.apply_spec(backend, spec, stage_only=False)
    assert res["ok"] is True
    assert _kinds(res["skipped"]) == [
        "plugin-assembly", "plugin-type", "plugin-type"]
    # Only one GET to plugintypes (list_types fired once, not twice).
    type_gets = [r for r in m.request_history if r.method == "GET"
                 and "plugintypes" in r.url]
    assert len(type_gets) == 1


# L808-810: D365Error from update_step → fails + _Aborted.
def test_apply_plugin_step_update_failure_lands_in_failed(backend, tmp_path):
    # Live step exists with rank drift → update_step fires but the PATCH fails.
    step = _step_spec(name="S", message="Update", entity="account", rank=5)
    spec = {"plugins": [_plugin_spec(tmp_path, steps=[step])]}
    with requests_mock.Mocker() as m:
        _mock_assembly_live(m, backend)
        _mock_step_live(m, backend, _step_row(message="Update", entity="account", rank=1))
        # PATCH fails → update_step raises D365Error → L808-810.
        m.patch(backend.url_for(f"sdkmessageprocessingsteps({_STEP_ID})"),
                status_code=500, json={"error": {"message": "update failed"}})
        res = apply_mod.apply_spec(backend, spec, stage_only=False)
    assert res["ok"] is False
    assert any(e.get("kind") == "plugin-step" and "error" in e for e in res["failed"])


# L876->874: malformed component (no objectid string) is silently skipped in loop.
def test_prune_candidates_skips_malformed_component(backend):
    spec = {"solution": {"unique_name": "ContosoCore"}}
    with requests_mock.Mocker() as m:
        # Component with missing objectid → if isinstance(ct, int) and isinstance(oid, str) is False.
        m.get(backend.url_for("solutions"),
              json={"value": [{"solutionid": _GUID2, "uniquename": "ContosoCore"}]})
        m.get(backend.url_for("solutioncomponents"),
              json={"value": [
                  {"componenttype": 61, "objectid": None, "rootcomponentbehavior": 0},
              ]})
        cands = apply_mod._prune_candidates(backend, spec, "ContosoCore")
    assert cands == []  # malformed component silently skipped


# L938: attribute prune delete — call _prune_delete directly (avoids full apply setup).
def test_prune_delete_attribute_kind(backend):
    from crm.core.apply import _prune_delete
    cand = {"kind": "attribute", "name": "contoso_orphan",
            "ref": "contoso_orphan", "entity": "contoso_project"}
    attr_path = backend.url_for(
        "EntityDefinitions(LogicalName='contoso_project')"
        "/Attributes(LogicalName='contoso_orphan')")
    with requests_mock.Mocker() as m:
        # delete_attribute does a preflight GET before DELETE.
        m.get(attr_path, json={
            "IsCustomAttribute": True, "IsManaged": False,
            "IsPrimaryId": False, "IsPrimaryName": False,
            "AttributeOf": None, "MetadataId": _ATTR_ID,
        })
        del_mock = m.delete(attr_path, status_code=204)
        _prune_delete(backend, cand)
    assert del_mock.called


# L472->474 False branch: desired_required == live_required → no change needed.
def test_reconcile_attribute_required_matches_no_drift(backend):
    from crm.core.apply import _reconcile_attribute
    # Both spec and live have required="ApplicationRequired" → no change.
    attr = {"kind": "string", "schema_name": "contoso_Code", "display_name": "Code",
            "required": "ApplicationRequired"}
    entry = {"kind": "attribute", "name": "contoso_Code"}
    with requests_mock.Mocker() as m:
        _mock_attribute_live(m, backend, logical="contoso_code", schema="contoso_Code",
                             display_name="Code", required="ApplicationRequired")
        verdict, _ = _reconcile_attribute(backend, attr, "contoso_project", None, entry)
    assert verdict == "skipped"


# L1106->1084 False branch: optionset bucket is "skipped" (dry-run created but
# then bucket == "skipped" in a freshly-created-but-not-applied scenario).
# The simplest real-world trigger: a spec with a greenfield optionset under dry-run
# but where create_optionset returns would_skip=True (exists) → bucket="skipped" →
# L1106 is False → loop continues without adding to os_created.
def test_apply_optionset_skipped_bucket_not_in_os_created(dry_backend):
    # Optionset EXISTS → would_skip=True → bucket="skipped" → L1106 False branch.
    spec = {"optionsets": [_OPTIONSET]}
    with requests_mock.Mocker() as m:
        m.get(dry_backend.url_for("GlobalOptionSetDefinitions(Name='contoso_priority')"),
              json={"Name": "contoso_priority", "MetadataId": _OS_ID,
                    "Options": [{"Value": 100000000, "Label": _label("Low")},
                                 {"Value": 100000001, "Label": _label("High")}]})
        res = apply_mod.apply_spec(dry_backend, spec, stage_only=False)
    assert res["ok"] is True
    # optionset is skipped (already exists and options match) → bucket="skipped"
    assert _kinds(res["skipped"]) == ["optionset"]


# ── spec-adapter registry (#596) ─────────────────────────────────────────────
# The adapters in apply.REGISTRY are the single source of truth for "what spec
# keys kind X accepts". These tests lock the adapter ↔ builder contract so the
# spec's expressible surface stays equal to the builders' full surface.
import inspect

from crm.core import metadata as meta_mod
from crm.core import metadata_attrs as attrs_mod
from crm.core import relationships as rel_mod
from crm.core import views as views_mod

_ADAPTER_BUILDERS = {
    "attribute": attrs_mod.add_attribute,
    "relationship": rel_mod.create_one_to_many,
    "entity": meta_mod.create_entity,
    "view": views_mod.create_view,
}


def _kwonly(fn):
    """The keyword-only parameter names of a builder (the part after ``*``)."""
    return {
        n for n, p in inspect.signature(fn).parameters.items()
        if p.kind is inspect.Parameter.KEYWORD_ONLY
    }


@pytest.mark.parametrize("kind", sorted(_ADAPTER_BUILDERS))
def test_adapter_covers_full_builder_surface(kind):
    """Every builder keyword arg is reachable from a spec (mapped, transformed, or
    driver-injected) — add a builder kwarg without an adapter entry and this fails."""
    adapter = apply_mod.REGISTRY[kind]
    covered = set(adapter.map.values()) | adapter.transform_targets | adapter.injected
    uncovered = _kwonly(_ADAPTER_BUILDERS[kind]) - covered
    assert not uncovered, f"{kind}: adapter does not reach builder kwargs {sorted(uncovered)}"


@pytest.mark.parametrize("kind", sorted(_ADAPTER_BUILDERS))
def test_adapter_targets_are_real_builder_params(kind):
    """Conversely, no adapter maps to a kwarg the builder does not accept (a typo
    would otherwise raise TypeError only at apply time)."""
    adapter = apply_mod.REGISTRY[kind]
    produced = set(adapter.map.values()) | adapter.transform_targets
    real = _kwonly(_ADAPTER_BUILDERS[kind])
    bogus = produced - real
    assert not bogus, f"{kind}: adapter targets non-existent builder kwargs {sorted(bogus)}"


# ── capability: previously-dropped builder kwargs now reach the builder (#596) ─
# Before the adapter registry these kwargs were unreachable from a spec — they
# silently fell to the builder default. Each test sets a NON-default value and
# asserts it reaches the outgoing create body.
def test_apply_forwards_cascade_on_relationship_create(backend):
    rel = {**_RELATIONSHIP, "cascade_delete": "Cascade"}
    spec = {"publisher": _PUBLISHER, "solution": _SOLUTION,
            "entities": [{**_ENTITY, "relationships": [rel]}]}
    with requests_mock.Mocker() as m:
        _mock_publisher_create(m, backend)
        _mock_solution_create(m, backend)
        _mock_entity_create(m, backend)
        _mock_one_to_many(m, backend, schema=rel["schema_name"])
        m.post(backend.url_for("PublishAllXml"), status_code=204)
        res = apply_mod.apply_spec(backend, spec, stage_only=False)
    body = next(json.loads(r.text) for r in m.request_history
                if r.method == "POST" and r.url == backend.url_for("RelationshipDefinitions"))
    assert body["CascadeConfiguration"]["Delete"] == "Cascade"
    assert res["ok"] is True


def test_apply_forwards_true_label_on_boolean_attribute(backend):
    attr = {"kind": "boolean", "schema_name": "contoso_Flag", "display_name": "Flag",
            "true_label": "On", "false_label": "Off"}
    spec = {"publisher": _PUBLISHER, "solution": _SOLUTION,
            "entities": [{**_ENTITY, "attributes": [attr]}]}
    with requests_mock.Mocker() as m:
        _mock_publisher_create(m, backend)
        _mock_solution_create(m, backend)
        _mock_entity_create(m, backend)
        _mock_attribute_create(m, backend, logical="contoso_flag", schema="contoso_Flag",
                               attr_type="Boolean")
        m.post(backend.url_for("PublishAllXml"), status_code=204)
        res = apply_mod.apply_spec(backend, spec, stage_only=False)
    attr_post = backend.url_for("EntityDefinitions(LogicalName='contoso_project')/Attributes")
    body = next(json.loads(r.text) for r in m.request_history
                if r.method == "POST" and r.url == attr_post)
    assert body["OptionSet"]["TrueOption"]["Label"]["LocalizedLabels"][0]["Label"] == "On"
    assert body["OptionSet"]["FalseOption"]["Label"]["LocalizedLabels"][0]["Label"] == "Off"
    assert res["ok"] is True


def test_apply_forwards_filter_active_on_view_create(backend):
    view = {"name": "Open Projects", "columns": ["contoso_name"], "filter_active": True}
    spec = {"publisher": _PUBLISHER, "solution": _SOLUTION,
            "entities": [{**_ENTITY, "views": [view]}]}
    with requests_mock.Mocker() as m:
        _mock_publisher_create(m, backend)
        _mock_solution_create(m, backend)
        _mock_entity_create(m, backend)
        _mock_view_create(m, backend, name="Open Projects")
        m.post(backend.url_for("PublishAllXml"), status_code=204)
        res = apply_mod.apply_spec(backend, spec, stage_only=False)
    body = next(json.loads(r.text) for r in m.request_history
                if r.method == "POST" and r.url == backend.url_for("savedqueries"))
    assert "statecode" in body["fetchxml"]
    assert res["ok"] is True


def test_apply_forwards_has_notes_on_entity_create(backend):
    spec = {"publisher": _PUBLISHER, "solution": _SOLUTION,
            "entities": [{**_ENTITY, "has_notes": True}]}
    with requests_mock.Mocker() as m:
        _mock_publisher_create(m, backend)
        _mock_solution_create(m, backend)
        _mock_entity_create(m, backend)
        m.post(backend.url_for("PublishAllXml"), status_code=204)
        res = apply_mod.apply_spec(backend, spec, stage_only=False)
    body = next(json.loads(r.text) for r in m.request_history
                if r.method == "POST" and r.url == backend.url_for("EntityDefinitions"))
    assert body["HasNotes"] is True
    assert res["ok"] is True


# ── gap closure: a malformed block fails in the up-front pass, before any HTTP ─
# Pre-#596 these passed validate_spec and raised mid-apply (after publisher /
# solution / entity had already been written). Now adapter.validate routes through
# the same mc.* primitive the builder calls, so the failure is fail-fast.
def test_apply_rejects_attribute_schema_name_without_prefix_before_http(backend):
    bad = {"kind": "string", "schema_name": "Code", "display_name": "Code"}
    spec = {"publisher": _PUBLISHER, "solution": _SOLUTION,
            "entities": [{**_ENTITY, "attributes": [bad]}]}
    with requests_mock.Mocker() as m:
        with pytest.raises(D365Error, match="must include a publisher prefix"):
            apply_mod.apply_spec(backend, spec, stage_only=False)
        assert m.request_history == []


def test_apply_rejects_bad_required_value_before_http(backend):
    bad = {"kind": "string", "schema_name": "contoso_Code", "display_name": "Code",
           "required": "Mandatory"}
    spec = {"publisher": _PUBLISHER, "solution": _SOLUTION,
            "entities": [{**_ENTITY, "attributes": [bad]}]}
    with requests_mock.Mocker() as m:
        with pytest.raises(D365Error, match="required must be one of"):
            apply_mod.apply_spec(backend, spec, stage_only=False)
        assert m.request_history == []


def test_apply_rejects_entity_schema_name_without_prefix_before_http(backend):
    # The entity schema_name is routed through adapter.validate too — a no-prefix
    # name fails in the up-front pass before any publisher/solution write.
    spec = {"publisher": _PUBLISHER, "solution": _SOLUTION,
            "entities": [{"schema_name": "Project", "display_name": "Project"}]}
    with requests_mock.Mocker() as m:
        with pytest.raises(D365Error, match="must include a publisher prefix"):
            apply_mod.apply_spec(backend, spec, stage_only=False)
        assert m.request_history == []


def test_apply_rejects_primary_attr_schema_without_prefix_before_http(backend):
    # The primary attribute's nested schema_name (forwarded as primary_attr_schema)
    # is validated up front too — a no-prefix value fails before any write.
    spec = {"publisher": _PUBLISHER, "solution": _SOLUTION,
            "entities": [{"schema_name": "contoso_Project", "display_name": "Project",
                          "primary_attr": {"schema_name": "Name", "label": "Name"}}]}
    with requests_mock.Mocker() as m:
        with pytest.raises(D365Error, match="primary_attr_schema must include a publisher prefix"):
            apply_mod.apply_spec(backend, spec, stage_only=False)
        assert m.request_history == []


def test_apply_rejects_uselabel_without_menu_label_before_http(backend):
    # menu_behavior 'UseLabel' needs menu_label (create_one_to_many enforces it);
    # the relationship phase writes after entities/attrs, so this must fail up front.
    rel = {**_RELATIONSHIP, "menu_behavior": "UseLabel"}
    spec = {"publisher": _PUBLISHER, "solution": _SOLUTION,
            "entities": [{**_ENTITY, "relationships": [rel]}]}
    with requests_mock.Mocker() as m:
        with pytest.raises(D365Error, match="UseLabel"):
            apply_mod.apply_spec(backend, spec, stage_only=False)
        assert m.request_history == []


def test_apply_rejects_unknown_query_type_before_http(backend):
    # query_type is spec-expressible now; an unknown value fails up front, not in
    # the views phase (the last phase to write).
    view = {"name": "Bad", "columns": ["contoso_name"], "query_type": "nope"}
    spec = {"publisher": _PUBLISHER, "solution": _SOLUTION,
            "entities": [{**_ENTITY, "views": [view]}]}
    with requests_mock.Mocker() as m:
        with pytest.raises(D365Error, match="unknown query_type"):
            apply_mod.apply_spec(backend, spec, stage_only=False)
        assert m.request_history == []
