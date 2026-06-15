"""Concise human single-record render for `entity get` / `entity create` (#302).

Human mode dumps a single record as one key/value status line per attribute,
which for a typical account is ~190 lines led by `@odata.context`/`@odata.etag`
with the primary name and the new record's id buried among nulls. The default
human render is now concise — `@odata.*` plumbing and null/empty fields dropped,
`_entity_id` (then the primary name, if metadata is already cached) hoisted
first — with a `--full` escape hatch restoring the raw record. JSON is untouched.
"""
# pyright: basic

from __future__ import annotations

import json

import requests_mock
from click.testing import CliRunner

from crm.cli import CLIContext, cli
from crm.commands._helpers import _concise_record
from crm.core import entity as entity_mod


_GUID = "11111111-1111-1111-1111-111111111111"


class TestConciseRecordHelper:
    def test_drops_odata_and_nulls_hoists_id_first(self):
        record = {
            "@odata.context": "https://…/$metadata#accounts/$entity",
            "@odata.etag": 'W/"123"',
            "accountid": _GUID,
            "name": "Contoso",
            "telephone1": None,
            "fax": "",
            "_entity_id": _GUID,
        }
        out = _concise_record(record)
        # OData plumbing and null/empty fields gone.
        assert not any("@odata." in k for k in out)
        assert "telephone1" not in out
        assert "fax" not in out
        # Populated business fields kept.
        assert out["name"] == "Contoso"
        # `_entity_id` leads the render.
        assert next(iter(out)) == "_entity_id"

    def test_primary_name_hoisted_after_id(self):
        record = {"createdon": "2024-01-01", "name": "Contoso", "_entity_id": _GUID}
        out = _concise_record(record, primary_name="name")
        assert list(out)[:2] == ["_entity_id", "name"]

    def test_cold_cache_skips_name_hoist(self):
        # primary_name=None (cold metadata cache) leaves the name in place.
        record = {"createdon": "2024-01-01", "name": "Contoso", "_entity_id": _GUID}
        out = _concise_record(record, primary_name=None)
        assert list(out) == ["_entity_id", "createdon", "name"]


# Full record as a v9.x account GET returns it: OData plumbing first, the name
# buried among many nulls.
_FULL_RECORD = {
    "@odata.context": "https://crm.contoso.local/contoso/api/data/v9.2/$metadata#accounts/$entity",
    "@odata.etag": 'W/"1234567"',
    "accountnumber": None,
    "name": "Contoso",
    "telephone1": None,
    "fax": None,
    "websiteurl": None,
    "accountid": _GUID,
}


def _stub(monkeypatch, backend) -> None:
    monkeypatch.setattr(CLIContext, "backend", lambda self: backend)


def _get_url(backend) -> str:
    return backend.url_for(entity_mod.build_record_path("accounts", _GUID))


class TestEntityGetHumanRender:
    def test_default_is_concise(self, monkeypatch, backend, isolated_home):
        _stub(monkeypatch, backend)
        with requests_mock.Mocker() as m:
            # ONLY the record GET is mocked; a metadata round-trip for the
            # primary-name hoist would raise NoMockAddress (proves AC4).
            m.get(_get_url(backend), json=_FULL_RECORD)
            result = CliRunner().invoke(cli, ["entity", "get", "accounts", _GUID])
        assert result.exit_code == 0, result.output
        out = result.output
        assert "@odata.context" not in out
        assert "@odata.etag" not in out
        assert "telephone1" not in out  # null field suppressed
        assert "Contoso" in out  # populated business field kept
        # `_entity_id` is rendered before the business fields.
        assert out.index("_entity_id") < out.index("name")

    def test_full_flag_shows_everything(self, monkeypatch, backend, isolated_home):
        _stub(monkeypatch, backend)
        with requests_mock.Mocker() as m:
            m.get(_get_url(backend), json=_FULL_RECORD)
            result = CliRunner().invoke(
                cli, ["entity", "get", "accounts", _GUID, "--full"]
            )
        assert result.exit_code == 0, result.output
        out = result.output
        assert "@odata.context" in out
        assert "telephone1" in out  # nulls shown under --full

    def test_json_output_unchanged_by_full_flag(self, monkeypatch, backend, isolated_home):
        # --full is a human-mode concept; JSON keeps the curated full record.
        _stub(monkeypatch, backend)
        with requests_mock.Mocker() as m:
            m.get(_get_url(backend), json=_FULL_RECORD)
            result = CliRunner().invoke(cli, ["--json", "entity", "get", "accounts", _GUID])
        assert result.exit_code == 0, result.output
        rec = json.loads(result.output)["data"]
        # JSON default still carries the full curated record incl. null fields.
        assert "telephone1" in rec
        assert rec["_entity_id"] == _GUID


_DEFS = {"value": [{
    "LogicalName": "account", "EntitySetName": "accounts",
    "PrimaryIdAttribute": "accountid", "PrimaryNameAttribute": "name",
}]}


class TestEntityCreateHumanRender:
    def test_create_leads_with_entity_id(self, monkeypatch, backend, isolated_home):
        _stub(monkeypatch, backend)
        created = {
            "@odata.context": "https://crm.contoso.local/contoso/api/data/v9.2/$metadata#accounts/$entity",
            "@odata.etag": 'W/"1"',
            "accountnumber": None,
            "name": "Contoso",
            "accountid": _GUID,
        }
        with requests_mock.Mocker() as m:
            m.post(backend.url_for("accounts"), json=created)
            m.get(backend.url_for("EntityDefinitions"), json=_DEFS)
            result = CliRunner().invoke(
                cli, ["entity", "create", "accounts", "--data", json.dumps({"name": "Contoso"})]
            )
        assert result.exit_code == 0, result.output
        out = result.output
        assert "@odata.context" not in out
        assert "accountnumber" not in out  # null suppressed
        # The new id leads, not buried among the record's fields.
        assert out.index("_entity_id") < out.index("Contoso")
