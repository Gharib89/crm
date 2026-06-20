"""Unit tests for `metadata changes` — the RetrieveMetadataChanges delta wrapper.

A real ``D365Backend`` driven by ``requests_mock`` so the exact function-call
path and the URL-encoded ``Query`` JSON are asserted at the wire. The wire shape
was verified live against a v9.2 org before these tests were written.
"""
# pyright: basic

from __future__ import annotations

import json
from urllib.parse import parse_qs, urlparse

import requests_mock
from click.testing import CliRunner

from crm.cli import CLIContext, cli
from crm.core import metadata as meta


def _query_param(request, alias="@p1"):
    """Pull a function parameter alias out of the request URL.

    ``request.qs`` lower-cases the whole query string, which corrupts the JSON
    in the ``Query`` value — parse the raw URL instead.
    """
    return parse_qs(urlparse(request.url).query)[alias][0]


def _resp(entities, *, stamp="111!06/20/2026 20:10:36", deleted_count=0):
    return {
        "ServerVersionStamp": stamp,
        "EntityMetadata": entities,
        "DeletedMetadata": {"Count": deleted_count},
    }


def _entity(logical, schema, *, label=None, has_changed=None, attributes=None):
    e = {"LogicalName": logical, "SchemaName": schema, "HasChanged": has_changed}
    if label is not None:
        e["DisplayName"] = {"UserLocalizedLabel": {"Label": label}}
    if attributes is not None:
        e["Attributes"] = attributes
    return e


def test_metadata_changes_baseline(backend):
    """Without --since: GET RetrieveMetadataChanges(Query=@p1), shaped entities + stamp."""
    resp = _resp([_entity("account", "Account", label="Account")])
    with requests_mock.Mocker() as m:
        m.get(backend.url_for("RetrieveMetadataChanges(Query=@p1)"), json=resp)
        out = meta.metadata_changes(backend)

    assert out["server_version_stamp"] == "111!06/20/2026 20:10:36"
    assert out["count"] == 1
    e = out["entities"][0]
    assert e["logical_name"] == "account"
    assert e["schema_name"] == "Account"
    assert e["display_name"] == "Account"

    # The Query param carries the EntityQueryExpression as raw JSON (not quoted).
    sent = json.loads(_query_param(m.last_request))
    assert sent["Properties"]["PropertyNames"] == ["LogicalName", "SchemaName", "DisplayName"]
    # baseline omits ClientVersionStamp + DeletedMetadataFilters entirely.
    assert "ClientVersionStamp" not in m.last_request.url
    assert "DeletedMetadataFilters" not in m.last_request.url


def test_metadata_changes_since_delta(backend):
    """With --since: delta path carries ClientVersionStamp + DeletedMetadataFilters,
    the stamp is sent as a quoted string literal, and deleted_count is surfaced."""
    stamp = "3513553!06/20/2026 20:10:36"
    resp = _resp([], stamp="9999!06/20/2026 21:00:00", deleted_count=2)
    path = (
        "RetrieveMetadataChanges(Query=@p1,ClientVersionStamp=@p2,"
        "DeletedMetadataFilters=Microsoft.Dynamics.CRM.DeletedMetadataFilters'All')"
    )
    with requests_mock.Mocker() as m:
        m.get(backend.url_for(path), json=resp)
        out = meta.metadata_changes(backend, since=stamp)

    assert out["server_version_stamp"] == "9999!06/20/2026 21:00:00"
    assert out["count"] == 0
    assert out["deleted_count"] == 2
    # ClientVersionStamp is a string literal: single-quoted, not raw.
    assert parse_qs(urlparse(m.last_request.url).query)["@p2"][0] == f"'{stamp}'"


def test_metadata_changes_scoped_to_entities(backend):
    """--entity adds an Or'd Equals-LogicalName Criteria for each entity."""
    resp = _resp([_entity("account", "Account"), _entity("contact", "Contact")])
    with requests_mock.Mocker() as m:
        m.get(backend.url_for("RetrieveMetadataChanges(Query=@p1)"), json=resp)
        out = meta.metadata_changes(backend, entities=["account", "contact"])

    assert {e["logical_name"] for e in out["entities"]} == {"account", "contact"}
    sent = json.loads(_query_param(m.last_request))
    crit = sent["Criteria"]
    assert crit["FilterOperator"] == "Or"
    values = [c["Value"]["Value"] for c in crit["Conditions"]]
    assert values == ["account", "contact"]
    assert all(c["PropertyName"] == "LogicalName" for c in crit["Conditions"])


def test_metadata_changes_with_attributes(backend):
    """--attributes requests Attributes + an AttributeQuery, and the columns are shaped."""
    attrs = [
        {"LogicalName": "name", "AttributeType": "String", "HasChanged": True},
        {"LogicalName": "createdon", "AttributeType": "DateTime", "HasChanged": None},
    ]
    resp = _resp([_entity("account", "Account", attributes=attrs)])
    with requests_mock.Mocker() as m:
        m.get(backend.url_for("RetrieveMetadataChanges(Query=@p1)"), json=resp)
        out = meta.metadata_changes(backend, attributes=True)

    sent = json.loads(_query_param(m.last_request))
    assert "Attributes" in sent["Properties"]["PropertyNames"]
    assert sent["AttributeQuery"]["Properties"]["PropertyNames"] == [
        "LogicalName", "AttributeType", "DisplayName"
    ]
    shaped_attrs = out["entities"][0]["attributes"]
    assert shaped_attrs[0] == {
        "logical_name": "name", "attribute_type": "String", "has_changed": True
    }


def test_metadata_changes_baseline_has_no_attributes_key(backend):
    """Without --attributes, entities carry no 'attributes' key (Attributes not requested)."""
    resp = _resp([_entity("account", "Account")])
    with requests_mock.Mocker() as m:
        m.get(backend.url_for("RetrieveMetadataChanges(Query=@p1)"), json=resp)
        out = meta.metadata_changes(backend)
    assert "attributes" not in out["entities"][0]


# ── CLI ──────────────────────────────────────────────────────────────────────
def _stub(monkeypatch, backend):
    monkeypatch.setattr(CLIContext, "backend", lambda self: backend)


def test_changes_cli_json_surfaces_stamp(monkeypatch, backend):
    """`--json metadata changes` returns the ok envelope, the entities under data,
    and the new ServerVersionStamp (the value to save) in data."""
    _stub(monkeypatch, backend)
    resp = _resp([_entity("account", "Account", label="Account")], stamp="42!ts")
    with requests_mock.Mocker() as m:
        m.get(backend.url_for("RetrieveMetadataChanges(Query=@p1)"), json=resp)
        result = CliRunner().invoke(cli, ["--json", "metadata", "changes"])
        assert {r.method for r in m.request_history} == {"GET"}
    assert result.exit_code == 0, result.output
    env = json.loads(result.output)
    assert env["ok"] is True
    assert env["data"]["server_version_stamp"] == "42!ts"
    assert env["data"]["entities"][0]["logical_name"] == "account"
    assert env["meta"]["count"] == 1


def test_changes_cli_since_and_entity_options(monkeypatch, backend):
    """--since + repeated --entity reach the delta path with both tables scoped."""
    _stub(monkeypatch, backend)
    path = (
        "RetrieveMetadataChanges(Query=@p1,ClientVersionStamp=@p2,"
        "DeletedMetadataFilters=Microsoft.Dynamics.CRM.DeletedMetadataFilters'All')"
    )
    with requests_mock.Mocker() as m:
        m.get(backend.url_for(path), json=_resp([], stamp="99!ts", deleted_count=1))
        result = CliRunner().invoke(
            cli,
            ["--json", "metadata", "changes", "--since", "42!ts",
             "--entity", "account", "--entity", "contact"],
        )
    assert result.exit_code == 0, result.output
    env = json.loads(result.output)
    assert env["data"]["deleted_count"] == 1
    sent = json.loads(_query_param(m.last_request))
    assert [c["Value"]["Value"] for c in sent["Criteria"]["Conditions"]] == ["account", "contact"]


def test_changes_cli_help_lists_command():
    result = CliRunner().invoke(cli, ["metadata", "changes", "--help"])
    assert result.exit_code == 0
    assert "RetrieveMetadataChanges" in result.output


def test_changes_cli_rejects_empty_since(monkeypatch, backend):
    """An explicit empty --since is rejected (would otherwise silently trigger an
    expensive baseline) — and the error fires before any backend call."""
    called = {"backend": False}

    def _boom(self):
        called["backend"] = True
        return backend

    monkeypatch.setattr(CLIContext, "backend", _boom)
    result = CliRunner().invoke(cli, ["--json", "metadata", "changes", "--since", "  "])
    assert result.exit_code != 0
    assert "must not be empty" in result.output
    assert called["backend"] is False  # validated before the backend was built


def test_changes_runs_live_under_dry_run(dry_backend):
    """`changes` is a read — the reads-execute rule means it runs live even in
    dry-run mode (no _dry_run preview, real GET issued)."""
    resp = _resp([_entity("account", "Account")], stamp="7!ts")
    with requests_mock.Mocker() as m:
        m.get(dry_backend.url_for("RetrieveMetadataChanges(Query=@p1)"), json=resp)
        out = meta.metadata_changes(dry_backend)
        assert {r.method for r in m.request_history} == {"GET"}
    assert "_dry_run" not in out
    assert out["server_version_stamp"] == "7!ts"
