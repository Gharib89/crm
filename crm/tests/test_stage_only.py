"""Tests for the global --stage-only flag (and CRM_STAGE_ONLY env var).

--stage-only forces every metadata-mutating command to behave as --no-publish:
no PublishAllXml fires, the --json meta records staged: true, and combining an
explicit --publish with --stage-only is rejected. Default behaviour (flag absent)
is unchanged — --publish still defaults True and auto-publish still fires.
"""
# pyright: basic
from __future__ import annotations

import json

import pytest
import requests_mock
from click.testing import CliRunner

from crm.cli import CLIContext, cli
from crm.utils.d365_backend import ConnectionProfile, D365Backend

_ATTR_ID = "33333333-3333-3333-3333-333333333333"
_ENTITY = "new_widget"


@pytest.fixture
def backend() -> D365Backend:
    profile = ConnectionProfile(
        name="testp",
        url="https://crm.contoso.local/contoso",
        domain="CONTOSO",
        username="alice",
        api_version="v9.2",
        verify_ssl=False,
    )
    return D365Backend(profile, password="pw", dry_run=False)


@pytest.fixture
def use_backend(backend, monkeypatch):
    """Point CLIContext.backend at the requests_mock-able real backend."""
    monkeypatch.setattr(CLIContext, "backend", lambda self: backend)
    return backend


def _mock_add_attribute(m, backend):
    attr_url = backend.url_for(
        f"EntityDefinitions(LogicalName='{_ENTITY}')/Attributes({_ATTR_ID})"
    )
    m.get(
        backend.url_for(
            f"EntityDefinitions(LogicalName='{_ENTITY}')/Attributes(LogicalName='new_label')"
        ),
        status_code=404,
    )
    m.post(
        backend.url_for(f"EntityDefinitions(LogicalName='{_ENTITY}')/Attributes"),
        status_code=204,
        headers={"OData-EntityId": attr_url},
    )
    m.get(
        attr_url,
        json={"LogicalName": "new_label", "SchemaName": "new_Label",
              "AttributeType": "String"},
    )


def _publish_matcher(backend):
    return requests_mock.ANY, backend.url_for("PublishAllXml")


def _add_attribute_args(extra=()):
    return [
        "--json", "metadata", "add-attribute", _ENTITY,
        "--kind", "string", "--schema-name", "new_Label",
        "--display", "Label", "--max-length", "10", *extra,
    ]


def _publish_hits(m, backend):
    target = backend.url_for("PublishAllXml")
    return [r for r in m.request_history if r.url == target]


def test_stage_only_flag_suppresses_publish_for_add_attribute(use_backend):
    backend = use_backend
    with requests_mock.Mocker() as m:
        _mock_add_attribute(m, backend)
        m.post(backend.url_for("PublishAllXml"), status_code=204)
        result = CliRunner().invoke(cli, ["--stage-only", *_add_attribute_args()])
    assert result.exit_code == 0, result.output
    assert _publish_hits(m, backend) == []


def test_stage_only_meta_records_staged_true(use_backend):
    backend = use_backend
    with requests_mock.Mocker() as m:
        _mock_add_attribute(m, backend)
        m.post(backend.url_for("PublishAllXml"), status_code=204)
        result = CliRunner().invoke(cli, ["--stage-only", *_add_attribute_args()])
    assert result.exit_code == 0, result.output
    env = json.loads(result.output)
    assert env["meta"]["staged"] is True


def test_without_flag_auto_publish_still_fires_regression(use_backend):
    backend = use_backend
    with requests_mock.Mocker() as m:
        _mock_add_attribute(m, backend)
        m.post(backend.url_for("PublishAllXml"), status_code=204)
        result = CliRunner().invoke(cli, _add_attribute_args())
    assert result.exit_code == 0, result.output
    assert len(_publish_hits(m, backend)) == 1
    env = json.loads(result.output)
    assert env["data"]["published"] is True


def test_stage_only_plus_explicit_publish_rejected(use_backend):
    backend = use_backend
    with requests_mock.Mocker() as m:
        _mock_add_attribute(m, backend)
        m.post(backend.url_for("PublishAllXml"), status_code=204)
        result = CliRunner().invoke(
            cli, ["--stage-only", *_add_attribute_args(extra=["--publish"])]
        )
    assert result.exit_code != 0, result.output
    assert "--publish" in result.output
    assert "--stage-only" in result.output
    assert _publish_hits(m, backend) == []


def test_stage_only_with_explicit_no_publish_is_allowed(use_backend):
    backend = use_backend
    with requests_mock.Mocker() as m:
        _mock_add_attribute(m, backend)
        m.post(backend.url_for("PublishAllXml"), status_code=204)
        result = CliRunner().invoke(
            cli, ["--stage-only", *_add_attribute_args(extra=["--no-publish"])]
        )
    assert result.exit_code == 0, result.output
    assert _publish_hits(m, backend) == []


def test_stage_only_persists_across_repl_lines(use_backend):
    """`crm --stage-only` then a bare per-line mutation must still suppress
    PublishAllXml. The REPL re-invokes cli.main(args=..., obj=ctx) with the SAME
    CLIContext per line; a per-line command carries no --stage-only token, so the
    sticky flag must not be cleared back to False (regression for #19 round 2)."""
    backend = use_backend
    ctx = CLIContext()
    with requests_mock.Mocker() as m:
        _mock_add_attribute(m, backend)
        m.post(backend.url_for("PublishAllXml"), status_code=204)
        # Line 1: user launches with --stage-only, sets the flag on this shared ctx.
        try:
            cli.main(
                args=["--stage-only", *_add_attribute_args()],
                obj=ctx, standalone_mode=False, prog_name="crm",
            )
        except SystemExit:
            pass
        assert ctx.stage_only is True
        # Line 2: bare per-line mutation, no --stage-only token.
        try:
            cli.main(
                args=_add_attribute_args(), obj=ctx,
                standalone_mode=False, prog_name="crm",
            )
        except SystemExit:
            pass
    assert ctx.stage_only is True
    assert _publish_hits(m, backend) == []


def test_crm_stage_only_env_var_recognized(use_backend, monkeypatch):
    backend = use_backend
    monkeypatch.setenv("CRM_STAGE_ONLY", "1")
    with requests_mock.Mocker() as m:
        _mock_add_attribute(m, backend)
        m.post(backend.url_for("PublishAllXml"), status_code=204)
        result = CliRunner().invoke(cli, _add_attribute_args())
    assert result.exit_code == 0, result.output
    assert _publish_hits(m, backend) == []
    env = json.loads(result.output)
    assert env["meta"]["staged"] is True


def test_stage_only_applies_to_create_entity(use_backend):
    backend = use_backend
    ent_url = backend.url_for("EntityDefinitions(11111111-1111-1111-1111-111111111111)")
    with requests_mock.Mocker() as m:
        m.get(
            backend.url_for("EntityDefinitions(LogicalName='new_project')"),
            status_code=404,
        )
        m.post(
            backend.url_for("EntityDefinitions"),
            status_code=204,
            headers={"OData-EntityId": ent_url},
        )
        m.get(ent_url, json={"LogicalName": "new_project", "SchemaName": "new_Project"})
        m.post(backend.url_for("PublishAllXml"), status_code=204)
        result = CliRunner().invoke(cli, [
            "--stage-only", "--json", "metadata", "create-entity",
            "--schema-name", "new_Project", "--display", "Project",
        ])
    assert result.exit_code == 0, result.output
    assert _publish_hits(m, backend) == []
    env = json.loads(result.output)
    assert env["meta"]["staged"] is True


_REL_ID = "44444444-4444-4444-4444-444444444444"


def _setup_one_to_many(m, backend):
    rel_url = backend.url_for(f"RelationshipDefinitions({_REL_ID})")
    m.get(
        backend.url_for("RelationshipDefinitions(SchemaName='new_a_new_b')"),
        status_code=404,
    )
    m.post(
        backend.url_for("RelationshipDefinitions"),
        status_code=204,
        headers={"OData-EntityId": rel_url},
    )
    m.get(
        rel_url + "/Microsoft.Dynamics.CRM.OneToManyRelationshipMetadata",
        json={"SchemaName": "new_a_new_b", "ReferencingAttribute": "new_aid"},
    )
    return [
        "--json", "metadata", "create-one-to-many",
        "--schema-name", "new_a_new_b",
        "--referenced-entity", "new_a", "--referencing-entity", "new_b",
        "--lookup-schema", "new_AId", "--lookup-display", "A",
    ]


def _setup_many_to_many(m, backend):
    rel_url = backend.url_for(f"RelationshipDefinitions({_REL_ID})")
    m.get(
        backend.url_for("RelationshipDefinitions(SchemaName='new_a_new_b')"),
        status_code=404,
    )
    m.post(
        backend.url_for("RelationshipDefinitions"),
        status_code=204,
        headers={"OData-EntityId": rel_url},
    )
    m.get(
        rel_url + "/Microsoft.Dynamics.CRM.ManyToManyRelationshipMetadata",
        json={"SchemaName": "new_a_new_b", "IntersectEntityName": "new_a_new_b"},
    )
    return [
        "--json", "metadata", "create-many-to-many",
        "--schema-name", "new_a_new_b",
        "--entity1", "new_a", "--entity2", "new_b",
        "--intersect-entity", "new_a_new_b",
    ]


def _setup_update_optionset(m, backend):
    m.post(backend.url_for("InsertOptionValue"), status_code=204)
    return [
        "--json", "metadata", "update-optionset", "new_priority",
        "--insert-option", "3:Critical",
    ]


@pytest.mark.parametrize(
    "setup",
    [_setup_one_to_many, _setup_many_to_many, _setup_update_optionset],
    ids=["create-one-to-many", "create-many-to-many", "update-optionset"],
)
def test_stage_only_applies_to_metadata_command(use_backend, setup):
    backend = use_backend
    with requests_mock.Mocker() as m:
        args = setup(m, backend)
        m.post(backend.url_for("PublishAllXml"), status_code=204)
        result = CliRunner().invoke(cli, ["--stage-only", *args])
    assert result.exit_code == 0, result.output
    assert _publish_hits(m, backend) == []
    env = json.loads(result.output)
    assert env["meta"]["staged"] is True


def test_stage_only_applies_to_create_optionset(use_backend):
    backend = use_backend
    os_url = backend.url_for("GlobalOptionSetDefinitions(22222222-2222-2222-2222-222222222222)")
    with requests_mock.Mocker() as m:
        m.get(
            backend.url_for("GlobalOptionSetDefinitions(Name='new_priority')"),
            status_code=404,
        )
        m.post(
            backend.url_for("GlobalOptionSetDefinitions"),
            status_code=204,
            headers={"OData-EntityId": os_url},
        )
        m.get(os_url, json={"Name": "new_priority", "MetadataId": "22222222-2222-2222-2222-222222222222"})
        m.post(backend.url_for("PublishAllXml"), status_code=204)
        result = CliRunner().invoke(cli, [
            "--stage-only", "--json", "metadata", "create-optionset",
            "--name", "new_priority", "--display", "Priority",
            "--option", "1:Low", "--option", "2:High",
        ])
    assert result.exit_code == 0, result.output
    assert _publish_hits(m, backend) == []
    env = json.loads(result.output)
    assert env["meta"]["staged"] is True
