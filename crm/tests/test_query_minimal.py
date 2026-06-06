"""Unit tests for --minimal: prune OData annotation keys from JSON output (#85)."""
# pyright: basic
from __future__ import annotations

import json

from click.testing import CliRunner

from crm.cli import CLIContext, cli
from crm.commands._helpers import _prune_annotations


def _annotated_record() -> dict:
    """A record carrying the three annotation shapes the rule must drop."""
    return {
        "@odata.etag": "W/\"123\"",
        "accountid": "00000000-0000-0000-0000-000000000001",
        "name": "Contoso Ltd",
        "statuscode@OData.Community.Display.V1.FormattedValue": "Active",
        "statuscode": 1,
        "_owner_value": "00000000-0000-0000-0000-000000000002",
        "_owner_value@Microsoft.Dynamics.CRM.lookuplogicalname": "systemuser",
    }


class TestPruneAnnotations:
    def test_drops_only_at_keys_keeps_lookup_guid(self):
        pruned = _prune_annotations(_annotated_record())
        # The three @-containing keys are gone (etag, *@FormattedValue, *@lookuplogicalname).
        assert "@odata.etag" not in pruned
        assert "statuscode@OData.Community.Display.V1.FormattedValue" not in pruned
        assert "_owner_value@Microsoft.Dynamics.CRM.lookuplogicalname" not in pruned
        # Business fields, primary id, and the bare lookup GUID are retained.
        assert set(pruned) == {"accountid", "name", "statuscode", "_owner_value"}
        assert pruned["_owner_value"] == "00000000-0000-0000-0000-000000000002"


def _stub_value_backend(monkeypatch):
    class StubBackend:
        def get(self, path, **kw):
            return {
                "@odata.context": "https://crm.contoso.local/contoso/api/data/v9.2/$metadata#accounts",
                "value": [_annotated_record()],
            }

    monkeypatch.setattr(CLIContext, "backend", lambda self: StubBackend())


class TestCLIQuery:
    def test_minimal_prunes_records(self, monkeypatch):
        _stub_value_backend(monkeypatch)
        runner = CliRunner()
        result = runner.invoke(
            cli, ["--json", "query", "odata", "accounts", "--minimal"]
        )
        assert result.exit_code == 0, result.output
        env = json.loads(result.output)
        rec = env["data"]["value"][0]
        assert all("@" not in k for k in rec), rec
        assert rec["_owner_value"] == "00000000-0000-0000-0000-000000000002"
        assert rec["accountid"] == "00000000-0000-0000-0000-000000000001"

    def test_default_retains_annotations(self, monkeypatch):
        _stub_value_backend(monkeypatch)
        runner = CliRunner()
        result = runner.invoke(cli, ["--json", "query", "odata", "accounts"])
        assert result.exit_code == 0, result.output
        env = json.loads(result.output)
        rec = env["data"]["value"][0]
        assert "@odata.etag" in rec
        assert "statuscode@OData.Community.Display.V1.FormattedValue" in rec


class TestCLIEntityGet:
    def test_minimal_prunes_single_record(self, monkeypatch):
        class StubBackend:
            def get(self, path, **kw):
                return _annotated_record()

        monkeypatch.setattr(CLIContext, "backend", lambda self: StubBackend())

        runner = CliRunner()
        result = runner.invoke(
            cli,
            ["--json", "entity", "get", "accounts",
             "00000000-0000-0000-0000-000000000001", "--minimal"],
        )
        assert result.exit_code == 0, result.output
        env = json.loads(result.output)
        rec = env["data"]
        assert all("@" not in k for k in rec), rec
        assert rec["_owner_value"] == "00000000-0000-0000-0000-000000000002"
