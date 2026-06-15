"""Curated --json output contract (ADR 0008 / #304, folds #303).

`data` is a CLI-owned shape: list verbs emit a bare array, OData paging moves to
`meta`, `@odata.*` protocol keys are stripped, and the affected record's GUID is
surfaced under the normalized `_entity_id` key on write verbs + single-record get.
"""
# pyright: basic
from __future__ import annotations

import json

import pytest
from click.testing import CliRunner

from crm.cli import cli

# Several behaviors resolve the entity's primary attributes through the
# read-through metadata cache; isolate CRM_HOME so no test touches a real cache.
pytestmark = pytest.mark.usefixtures("isolated_home")

GUID = "00000000-0000-0000-0000-000000000001"


def _collection(*records: dict, **envelope: object) -> dict:
    return {
        "@odata.context": "https://crm.contoso.local/contoso/api/data/v9.2/$metadata#accounts",
        "value": list(records),
        **envelope,
    }


def _row() -> dict:
    return {
        "@odata.etag": 'W/"123"',
        "accountid": GUID,
        "name": "Contoso Ltd",
        "statuscode@OData.Community.Display.V1.FormattedValue": "Active",
        "statuscode": 1,
    }


class TestListPayloadBareArray:
    def test_query_odata_data_is_bare_array(self, make_fake_backend, inject_backend):
        inject_backend(make_fake_backend(responses={"get": _collection(_row())}))
        result = CliRunner().invoke(cli, ["--json", "query", "odata", "accounts"])
        assert result.exit_code == 0, result.output
        env = json.loads(result.output)
        # data is the bare array — data[0] is the first row, no OData envelope.
        assert isinstance(env["data"], list)
        assert env["data"][0]["accountid"] == GUID
        assert "@odata.context" not in env["data"]  # not a dict key; envelope gone

    def test_per_row_odata_protocol_keys_stripped(self, make_fake_backend, inject_backend):
        inject_backend(make_fake_backend(responses={"get": _collection(_row())}))
        result = CliRunner().invoke(cli, ["--json", "query", "odata", "accounts"])
        env = json.loads(result.output)
        row = env["data"][0]
        assert "@odata.etag" not in row
        # Opted-in annotations (capital @OData.Community / @Microsoft.*) survive.
        assert "statuscode@OData.Community.Display.V1.FormattedValue" in row

    def test_paging_relocated_to_meta(self, make_fake_backend, inject_backend):
        env_resp = _collection(_row(), **{
            "@odata.count": 42,
            "@odata.nextLink": "https://crm.contoso.local/contoso/api/data/v9.2/accounts?$skiptoken=x",
        })
        inject_backend(make_fake_backend(responses={"get": env_resp}))
        result = CliRunner().invoke(cli, ["--json", "query", "odata", "accounts"])
        env = json.loads(result.output)
        assert env["meta"]["count"] == 42
        assert env["meta"]["next_link"].endswith("$skiptoken=x")
        # No paging keys left in data rows.
        assert all("@odata" not in k for k in env["data"][0])


class TestSingleRecordGet:
    def _single(self) -> dict:
        return {
            "@odata.context": "https://crm.contoso.local/contoso/api/data/v9.2/$metadata#accounts/$entity",
            "@odata.etag": 'W/"123"',
            **_row(),
        }

    def test_single_record_strips_odata_keeps_annotations(self, make_fake_backend, inject_backend):
        inject_backend(make_fake_backend(responses={"get": self._single()}))
        result = CliRunner().invoke(cli, ["--json", "entity", "get", "accounts", GUID])
        assert result.exit_code == 0, result.output
        rec = json.loads(result.output)["data"]
        assert "@odata.context" not in rec
        assert "@odata.etag" not in rec
        assert rec["name"] == "Contoso Ltd"
        assert "statuscode@OData.Community.Display.V1.FormattedValue" in rec

    def test_entity_id_injected(self, make_fake_backend, inject_backend):
        inject_backend(make_fake_backend(responses={"get": self._single()}))
        result = CliRunner().invoke(cli, ["--json", "entity", "get", "accounts", GUID])
        rec = json.loads(result.output)["data"]
        assert rec["_entity_id"] == GUID
        assert rec["_entity_id_url"].endswith(f"accounts({GUID})")
        # The genuine PK attribute is still present alongside the synthetic key.
        assert rec["accountid"] == GUID


class TestHumanPrimaryNameColumn:
    def test_infer_columns_hoists_primary_name_past_cap(self):
        from crm.commands._helpers import _infer_columns
        row: dict[str, object] = {f"sys{i}": i for i in range(10)}
        row["name"] = "X"
        cols = _infer_columns([row], primary_name="name")
        assert cols[0] == "name"  # hoisted first
        assert len(cols) == 8  # still capped

    def test_infer_columns_no_primary_name_unchanged(self):
        from crm.commands._helpers import _infer_columns
        assert _infer_columns([{"a": 1, "b": 2}]) == ["a", "b"]

    def test_query_human_surfaces_name_when_cached(self, make_fake_backend, inject_backend):
        # name would fall past the 8-column cap behind the address* columns;
        # the cached primary-name hoist pulls it to the front so it shows.
        row = {f"address{i}": f"v{i}" for i in range(10)}
        row.update({"name": "Contoso Ltd", "accountid": GUID})
        be = make_fake_backend(responses={"get": _collection(row)})
        inject_backend(be)
        from crm.core import entity_names
        entity_names.load_name_map(be, refresh=True)  # warm the cache (no GET on read)
        result = CliRunner().invoke(cli, ["query", "odata", "accounts"])
        assert result.exit_code == 0, result.output
        assert "Contoso Ltd" in result.output


class TestCreateNormalizedId:
    def test_create_injects_entity_id_keeps_full_record(self, make_fake_backend, inject_backend):
        body = {"@odata.etag": 'W/"1"', "accountid": GUID, "name": "New Co", "statuscode": 1}
        inject_backend(make_fake_backend(responses={"post": body}))
        result = CliRunner().invoke(
            cli, ["--json", "entity", "create", "accounts", "--data", '{"name":"New Co"}']
        )
        assert result.exit_code == 0, result.output
        data = json.loads(result.output)["data"]
        # Full record preserved (minus stripped @odata.*), plus the normalized id.
        assert data["name"] == "New Co"
        assert data["accountid"] == GUID
        assert data["_entity_id"] == GUID
        assert data["_entity_id_url"].endswith(f"accounts({GUID})")
        assert "@odata.etag" not in data

    def test_create_no_return_emits_entity_id_not_id(self, make_fake_backend, inject_backend):
        url = f"https://crm.contoso.local/contoso/api/data/v9.2/accounts({GUID})"
        # The 204 no-return path surfaces the GUID from the OData-EntityId header.
        inject_backend(make_fake_backend(
            responses={"post": {"_entity_id": GUID, "_entity_id_url": url}}))
        result = CliRunner().invoke(
            cli, ["--json", "entity", "create", "accounts", "--data", '{"name":"x"}', "--no-return"]
        )
        assert result.exit_code == 0, result.output
        data = json.loads(result.output)["data"]
        assert data["_entity_id"] == GUID
        assert data["_entity_id_url"] == url
        assert "id" not in data  # no bare id — one extraction rule everywhere


class TestDryRunPreviewNotCurated:
    def test_dry_run_preview_keeps_request_shape_keys(self):
        # A dry-run mutation preview echoes the request that WOULD be sent; the
        # central @odata curation must not touch it (else @odata.bind disappears
        # and the preview no longer matches the wire payload).
        import io
        import contextlib
        from crm.cli import CLIContext
        ctx = CLIContext()
        ctx.json_mode = True
        ctx.dry_run = True
        buf = io.StringIO()
        with contextlib.redirect_stdout(buf):
            ctx.emit(True, data={
                "_dry_run": True,
                "would_create": {"body": {"name": "x",
                                          "ownerid@odata.bind": "/systemusers(1)"}},
            })
        env = json.loads(buf.getvalue())
        assert env["data"]["would_create"]["body"]["ownerid@odata.bind"] == "/systemusers(1)"
        assert env["meta"]["dry_run"] is True


class TestUpdateNormalizedId:
    def test_update_no_return_fallback_emits_entity_id_not_id(self, make_fake_backend, inject_backend):
        # An empty 204 (no OData-EntityId header) drives the fallback envelope.
        inject_backend(make_fake_backend(responses={"patch": None}))
        result = CliRunner().invoke(
            cli, ["--json", "entity", "update", "accounts", GUID, "--data", '{"name":"x"}']
        )
        assert result.exit_code == 0, result.output
        data = json.loads(result.output)["data"]
        assert data["updated"] is True
        assert data["_entity_id"] == GUID
        assert data["_entity_id_url"].endswith(f"accounts({GUID})")
        assert "id" not in data


class TestNameMapPrimaryAttrs:
    def test_primary_lookup_by_set_or_logical(self):
        from crm.core.entity_names import NameMap
        nm = NameMap(
            logical_to_set={"account": "accounts"},
            set_to_logical={"accounts": "account"},
            primary_id={"account": "accountid"},
            primary_name={"account": "name"},
        )
        assert nm.primary_id_for("accounts") == "accountid"
        assert nm.primary_id_for("account") == "accountid"
        assert nm.primary_name_for("accounts") == "name"
        assert nm.primary_id_for("unknown") is None

    def test_load_name_map_carries_primary_attrs(self, make_fake_backend, tmp_path, monkeypatch):
        monkeypatch.setenv("CRM_HOME", str(tmp_path))
        from crm.core.entity_names import load_name_map
        be = make_fake_backend(responses={"get_collection": [
            {"LogicalName": "account", "EntitySetName": "accounts",
             "PrimaryIdAttribute": "accountid", "PrimaryNameAttribute": "name"},
        ]})
        nm = load_name_map(be, refresh=True)
        assert nm.primary_id_for("accounts") == "accountid"
        assert nm.primary_name_for("accounts") == "name"


class TestCacheSchemaVersion:
    def test_legacy_cache_without_schema_is_a_miss(self, profile, tmp_path, monkeypatch):
        import json as _json
        monkeypatch.setenv("CRM_HOME", str(tmp_path))
        from crm.core import metadata_cache as mc
        path = mc.cache_file(profile)
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(_json.dumps({
            "url": profile.url.rstrip("/"), "api_version": profile.api_version,
            "cached_at": 10_000.0,
            "definitions": [{"logical": "account", "set_name": "accounts"}],
        }))
        assert mc.read_definitions(profile, now=10_001.0) is None

    def test_new_cache_with_primary_attrs_roundtrips(self, profile, tmp_path, monkeypatch):
        monkeypatch.setenv("CRM_HOME", str(tmp_path))
        from crm.core import metadata_cache as mc
        defs = [{"logical": "account", "set_name": "accounts",
                 "primary_id": "accountid", "primary_name": "name"}]
        mc.write_definitions(profile, defs, now=10_000.0)
        assert mc.read_definitions(profile, now=10_001.0) == defs


class TestDeleteNormalizedId:
    def test_delete_emits_entity_id_not_id(self, make_fake_backend, inject_backend):
        inject_backend(make_fake_backend(responses={"delete": None}))
        result = CliRunner().invoke(
            cli, ["--json", "entity", "delete", "accounts", GUID, "--yes"]
        )
        assert result.exit_code == 0, result.output
        data = json.loads(result.output)["data"]
        assert data["deleted"] is True
        assert data["_entity_id"] == GUID
        assert data["_entity_id_url"].endswith(f"accounts({GUID})")
        # The old bare `id` key is gone.
        assert "id" not in data
