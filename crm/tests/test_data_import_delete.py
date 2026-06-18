# pyright: basic
"""`data import --mode delete` — batch DELETE by GUID or alternate key (#368).

Mirrors upsert's record resolution (``--id-column`` or ``--key``) but emits
DELETE operations with no body instead of PATCH.
"""
from __future__ import annotations

import json
from pathlib import Path
from typing import Any

import pytest

from crm.utils.d365_backend import D365Error
from crm.utils.d365_types import BatchResult

from crm.tests.test_data_import import _make_stub_backend, _import_records

GUID = "12345678-1234-1234-1234-123456789abc"


def _delete_results(n: int) -> list[BatchResult]:
    return [{
        "method": "DELETE", "url": "accounts", "status": 204,
        "headers": {}, "body": None, "error": None,
    } for _ in range(n)]


def test_delete_by_id_column_builds_delete_op(tmp_path: Path) -> None:
    record = {"accountid": GUID, "name": "ignored"}
    p = tmp_path / "data.jsonl"
    p.write_text(json.dumps(record) + "\n", encoding="utf-8")
    backend = _make_stub_backend([_delete_results(1)])
    result = _import_records(backend, "accounts", p, mode="delete",
                             id_column="accountid")

    ops = backend.batch.call_args[0][0]
    assert ops[0]["method"] == "DELETE"
    assert ops[0]["url"] == f"accounts({GUID})"
    assert ops[0].get("body") is None
    assert result["imported"] == 1
    assert result["mode"] == "delete"


def test_delete_by_alternate_key_builds_delete_op(tmp_path: Path) -> None:
    record = {"emailaddress1": "joe@x.com"}
    p = tmp_path / "data.jsonl"
    p.write_text(json.dumps(record) + "\n", encoding="utf-8")
    backend = _make_stub_backend([_delete_results(1)])
    _import_records(backend, "contacts", p, mode="delete",
                    alt_key=["emailaddress1"])

    ops = backend.batch.call_args[0][0]
    assert ops[0]["method"] == "DELETE"
    assert ops[0]["url"] == "contacts(emailaddress1='joe%40x.com')"
    assert ops[0].get("body") is None


def test_delete_without_id_column_or_key_raises(tmp_path: Path) -> None:
    p = tmp_path / "data.jsonl"
    p.write_text('{"name": "x"}\n', encoding="utf-8")
    backend = _make_stub_backend([])
    with pytest.raises(D365Error, match="id_column"):
        _import_records(backend, "accounts", p, mode="delete")


def test_delete_record_missing_id_raises(tmp_path: Path) -> None:
    p = tmp_path / "data.jsonl"
    p.write_text('{"name": "NoId"}\n', encoding="utf-8")
    backend = _make_stub_backend([])
    with pytest.raises(D365Error, match="row"):
        _import_records(backend, "accounts", p, mode="delete",
                        id_column="accountid")
