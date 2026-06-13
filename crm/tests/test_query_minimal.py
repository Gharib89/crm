"""Unit tests for --minimal: prune OData annotation keys from JSON output (#85)."""
# pyright: basic
from __future__ import annotations

import json

from click.testing import CliRunner

from crm.cli import cli
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


def _stub_value_backend(make_fake_backend, inject_backend):
    inject_backend(make_fake_backend(responses={"get": {
        "@odata.context": "https://crm.contoso.local/contoso/api/data/v9.2/$metadata#accounts",
        "value": [_annotated_record()],
    }}))


class TestCLIQuery:
    def test_minimal_prunes_records(self, make_fake_backend, inject_backend):
        _stub_value_backend(make_fake_backend, inject_backend)
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

    def test_default_retains_annotations(self, make_fake_backend, inject_backend):
        _stub_value_backend(make_fake_backend, inject_backend)
        runner = CliRunner()
        result = runner.invoke(cli, ["--json", "query", "odata", "accounts"])
        assert result.exit_code == 0, result.output
        env = json.loads(result.output)
        rec = env["data"]["value"][0]
        assert "@odata.etag" in rec
        assert "statuscode@OData.Community.Display.V1.FormattedValue" in rec

    def test_minimal_preserves_top_level_envelope(self, make_fake_backend, inject_backend):
        """The `{**result, "value": [...]}` rebuild must keep top-level envelope
        keys (e.g. `@odata.count` from `--count`) while still pruning per-record
        annotations."""
        inject_backend(make_fake_backend(responses={"get": {
            "@odata.context": "https://crm.contoso.local/contoso/api/data/v9.2/$metadata#accounts",
            "@odata.count": 7,
            "value": [_annotated_record()],
        }}))
        runner = CliRunner()
        result = runner.invoke(
            cli, ["--json", "query", "odata", "accounts", "--minimal", "--count"]
        )
        assert result.exit_code == 0, result.output
        env = json.loads(result.output)
        # Top-level envelope survives the rebuild: both the surfaced meta and the
        # raw `@odata.count` key in the data payload.
        assert env["meta"]["odata_count"] == 7
        assert env["data"]["@odata.count"] == 7
        # Per-record annotations are still stripped.
        rec = env["data"]["value"][0]
        assert "@odata.etag" not in rec
        assert all("@" not in k for k in rec), rec


class TestCLIEntityGet:
    def test_minimal_prunes_single_record(self, make_fake_backend, inject_backend):
        inject_backend(make_fake_backend(responses={"get": _annotated_record()}))

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
