# pyright: basic
"""How `import_records` reports failure (#1283): the validating dry run, the
input row named in a failed transactional chunk, and the dropped-lookup count.
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any
from unittest.mock import MagicMock

import pytest
import requests_mock as requests_mock_lib

from crm.core import entity_names
from crm.core.data_import import import_records
from crm.utils.d365_backend import D365Backend, D365Error

_ATTRS_URL_PART = "EntityDefinitions(LogicalName='account')/Attributes"
_M2O_URL_PART = "EntityDefinitions(LogicalName='account')/ManyToOneRelationships"

_ATTRS = [
    {
        "LogicalName": "accountid",
        "AttributeType": "Uniqueidentifier",
        "IsValidForCreate": True,
        "IsValidForUpdate": False,
    },
    {
        "LogicalName": "name",
        "AttributeType": "String",
        "IsValidForCreate": True,
        "IsValidForUpdate": True,
    },
    {
        "LogicalName": "createdon",
        "AttributeType": "DateTime",
        "IsValidForCreate": False,
        "IsValidForUpdate": False,
    },
    {
        "LogicalName": "accountnumber",
        "AttributeType": "String",
        "IsValidForCreate": True,
        "IsValidForUpdate": False,
    },
    {
        "LogicalName": "ownerid",
        "AttributeType": "Owner",
        "IsValidForCreate": True,
        "IsValidForUpdate": True,
    },
    {
        "LogicalName": "primarycontactid",
        "AttributeType": "Lookup",
        "IsValidForCreate": True,
        "IsValidForUpdate": True,
    },
]

_M2O = [
    {
        "ReferencingAttribute": "ownerid",
        "ReferencedEntity": "owner",
        "ReferencingEntityNavigationPropertyName": "ownerid",
    },
    {
        "ReferencingAttribute": "primarycontactid",
        "ReferencedEntity": "contact",
        "ReferencingEntityNavigationPropertyName": "primarycontactid",
    },
]


@pytest.fixture(autouse=True)
def _name_map(monkeypatch: pytest.MonkeyPatch) -> None:
    nm = entity_names.NameMap(
        logical_to_set={
            "account": "accounts",
            "contact": "contacts",
            "systemuser": "systemusers",
            "team": "teams",
        },
        set_to_logical={
            "accounts": "account",
            "contacts": "contact",
            "systemusers": "systemuser",
            "teams": "team",
        },
        primary_id={"account": "accountid"},
    )
    monkeypatch.setattr(entity_names, "load_name_map", lambda backend, **_kw: nm)


def _jsonl(tmp_path: Path, rows: list[dict[str, Any]]) -> Path:
    p = tmp_path / "rows.jsonl"
    p.write_text("".join(json.dumps(r) + "\n" for r in rows), encoding="utf-8")
    return p


def _mock_metadata(m: Any, backend: D365Backend) -> None:
    m.get(backend.url_for(_ATTRS_URL_PART), json={"value": _ATTRS})
    m.get(backend.url_for(_M2O_URL_PART), json={"value": _M2O})


# ── dry run validates columns ─────────────────────────────────────────────────


class TestDryRunValidation:
    def test_unknown_column_is_a_failure_and_nothing_is_written(
        self, tmp_path: Path, dry_backend: D365Backend
    ) -> None:
        p = _jsonl(tmp_path, [{"name": "A"}, {"name": "B", "nosuchcol": 1}, {"name": "C"}])
        with requests_mock_lib.Mocker() as m:
            _mock_metadata(m, dry_backend)
            result = import_records(dry_backend, "accounts", p)
            methods = {r.method for r in m.request_history}
        assert methods == {"GET"}
        assert result["would_import"] == 2
        assert result["failed"] == 1
        assert result["imported"] == 0
        [failure] = result["failures"]
        assert failure["index"] == 2
        assert failure["status"] is None
        assert "nosuchcol" in failure["error"]

    def test_column_not_valid_for_create(self, tmp_path: Path, dry_backend: D365Backend) -> None:
        p = _jsonl(tmp_path, [{"name": "A", "createdon": "2020-01-01"}])
        with requests_mock_lib.Mocker() as m:
            _mock_metadata(m, dry_backend)
            result = import_records(dry_backend, "accounts", p, mode="create")
        assert result["failed"] == 1
        assert "createdon" in result["failures"][0]["error"]
        assert "create" in result["failures"][0]["error"]

    def test_column_not_valid_for_update_under_upsert(
        self, tmp_path: Path, dry_backend: D365Backend
    ) -> None:
        guid = "11111111-1111-1111-1111-111111111111"
        p = _jsonl(tmp_path, [{"accountid": guid, "name": "A", "accountnumber": "X1"}])
        with requests_mock_lib.Mocker() as m:
            _mock_metadata(m, dry_backend)
            result = import_records(
                dry_backend, "accounts", p, mode="upsert", id_column="accountid"
            )
        # accountid is the id column (stripped from the body), so it is not
        # flagged though it is not valid for update; accountnumber is.
        [failure] = result["failures"]
        assert "accountnumber" in failure["error"]
        assert "accountid" not in failure["error"]
        assert "update" in failure["error"]

    def test_lookup_and_annotation_keys_are_not_flagged(
        self, tmp_path: Path, dry_backend: D365Backend
    ) -> None:
        guid = "22222222-2222-2222-2222-222222222222"
        row = {
            "name": "A",
            "primarycontactid@odata.bind": f"/contacts({guid})",
            "_primarycontactid_value": guid,
            "_ownerid_value": guid,
            "_ownerid_value@Microsoft.Dynamics.CRM.lookuplogicalname": "systemuser",
            "@odata.etag": 'W/"1"',
        }
        p = _jsonl(tmp_path, [row])
        with requests_mock_lib.Mocker() as m:
            _mock_metadata(m, dry_backend)
            result = import_records(dry_backend, "accounts", p)
            paths = [r.path for r in m.request_history]
        assert result["failures"] == []
        assert result["would_import"] == 1
        # Only metadata reads: no request checks a lookup target exists.
        assert all("entitydefinitions" in path for path in paths), paths

    def test_clean_file(self, tmp_path: Path, dry_backend: D365Backend) -> None:
        p = _jsonl(tmp_path, [{"name": "A"}, {"name": "B"}])
        with requests_mock_lib.Mocker() as m:
            _mock_metadata(m, dry_backend)
            result = import_records(dry_backend, "accounts", p)
        assert result["would_import"] == 2
        assert result["failed"] == 0

    def test_real_run_has_no_would_import(self, tmp_path: Path) -> None:
        backend = MagicMock(spec=D365Backend)
        backend.dry_run = False
        backend.batch.return_value = [
            {
                "method": "POST",
                "url": "accounts",
                "status": 204,
                "headers": {},
                "body": None,
                "error": None,
            }
        ]
        result = import_records(backend, "accounts", _jsonl(tmp_path, [{"name": "A"}]))
        assert "would_import" not in result


# ── transactional failure names the input row ─────────────────────────────────


class TestTransactionalRow:
    def _backend(self, exc: D365Error) -> Any:
        backend = MagicMock(spec=D365Backend)
        backend.dry_run = False
        ok = [
            {
                "method": "POST",
                "url": "accounts",
                "status": 204,
                "headers": {},
                "body": None,
                "error": None,
            }
        ] * 5
        backend.batch.side_effect = [ok, exc]
        return backend

    def test_second_chunk_third_op_names_row(self, tmp_path: Path) -> None:
        exc = D365Error("$batch failed: HTTP 400: bad value", status=400, code="0x1")
        exc.content_id = "3"
        p = _jsonl(tmp_path, [{"name": str(i)} for i in range(10)])
        with pytest.raises(D365Error) as caught:
            import_records(self._backend(exc), "accounts", p, chunk_size=5)
        assert "row 8" in str(caught.value)
        assert "bad value" in str(caught.value)
        assert caught.value.status == 400
        assert caught.value.code == "0x1"

    def test_no_content_id_keeps_todays_error(self, tmp_path: Path) -> None:
        exc = D365Error("$batch failed: HTTP 400: bad value", status=400)
        p = _jsonl(tmp_path, [{"name": str(i)} for i in range(10)])
        with pytest.raises(D365Error) as caught:
            import_records(self._backend(exc), "accounts", p, chunk_size=5)
        assert caught.value is exc


# ── dropped polymorphic lookup is counted ─────────────────────────────────────


class TestDroppedLookup:
    def _run(self, tmp_path: Path, backend: D365Backend, row: dict[str, Any]) -> dict[str, Any]:
        p = _jsonl(tmp_path, [row, {"name": "plain"}])
        with requests_mock_lib.Mocker() as m:
            _mock_metadata(m, backend)
            backend.batch = MagicMock(  # type: ignore[method-assign]
                return_value=[
                    {
                        "method": "POST",
                        "url": "accounts",
                        "status": 204,
                        "headers": {},
                        "body": None,
                        "error": None,
                    }
                ]
                * 2
            )
            return import_records(backend, "accounts", p)

    def test_missing_annotation_counts_the_drop(self, tmp_path: Path, backend: D365Backend) -> None:
        guid = "33333333-3333-3333-3333-333333333333"
        result = self._run(tmp_path, backend, {"name": "A", "_ownerid_value": guid})
        assert result["lookups_dropped"] == {"ownerid": 1}

    def test_annotated_lookup_binds_without_a_drop(
        self, tmp_path: Path, backend: D365Backend
    ) -> None:
        guid = "33333333-3333-3333-3333-333333333333"
        row = {
            "name": "A",
            "_ownerid_value": guid,
            "_ownerid_value@Microsoft.Dynamics.CRM.lookuplogicalname": "systemuser",
        }
        result = self._run(tmp_path, backend, row)
        assert "lookups_dropped" not in result
        [ops] = [c.args[0] for c in backend.batch.call_args_list]  # type: ignore[attr-defined]
        assert ops[0]["body"]["ownerid@odata.bind"] == f"/systemusers({guid})"
