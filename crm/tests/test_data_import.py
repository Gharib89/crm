# pyright: basic
"""Unit tests for crm.core.data_import — bulk import engine."""

from __future__ import annotations

import json
import math
from pathlib import Path
from typing import Any
from unittest.mock import MagicMock

import pytest
import requests_mock as requests_mock_lib

from crm.utils.d365_backend import ConnectionProfile, D365Backend, D365Error
from crm.utils.d365_types import BatchOperation, BatchResult


# ── helpers ─────────────────────────────────────────────────────────────────


def _make_2xx_results(n: int) -> list[BatchResult]:
    return [
        {
            "method": "POST",
            "url": "accounts",
            "status": 204,
            "headers": {},
            "body": None,
            "error": None,
        }
        for _ in range(n)
    ]


def _make_stub_backend(results_per_chunk: list[list[BatchResult]]) -> Any:
    """Return a MagicMock backend whose .batch() returns successive chunks."""
    backend = MagicMock(spec=D365Backend)
    backend.dry_run = False
    results_iter = iter(results_per_chunk)

    def _batch(_ops: list[BatchOperation], **kwargs: Any) -> list[BatchResult]:
        return next(results_iter)

    backend.batch.side_effect = _batch
    return backend


# ── import ───────────────────────────────────────────────────────────────────


def _import_records(*args: Any, **kwargs: Any) -> dict[str, Any]:
    from crm.core.data_import import import_records
    return import_records(*args, **kwargs)


# ── format inference ─────────────────────────────────────────────────────────


class TestFormatInference:
    def test_csv_suffix_inferred(self, tmp_path: Path) -> None:
        p = tmp_path / "records.csv"
        p.write_text("name\nAlpha\n", encoding="utf-8")
        backend = _make_stub_backend([_make_2xx_results(1)])
        result = _import_records(backend, "accounts", p)
        assert result["format"] == "csv"

    def test_other_suffix_inferred_as_jsonl(self, tmp_path: Path) -> None:
        p = tmp_path / "records.jsonl"
        p.write_text('{"name": "Alpha"}\n', encoding="utf-8")
        backend = _make_stub_backend([_make_2xx_results(1)])
        result = _import_records(backend, "accounts", p)
        assert result["format"] == "jsonl"

    def test_explicit_jsonl_accepted(self, tmp_path: Path) -> None:
        p = tmp_path / "records.txt"
        p.write_text('{"name": "Alpha"}\n', encoding="utf-8")
        backend = _make_stub_backend([_make_2xx_results(1)])
        result = _import_records(backend, "accounts", p, fmt="jsonl")
        assert result["format"] == "jsonl"

    def test_explicit_csv_accepted(self, tmp_path: Path) -> None:
        p = tmp_path / "records.txt"
        p.write_text("name\nAlpha\n", encoding="utf-8")
        backend = _make_stub_backend([_make_2xx_results(1)])
        result = _import_records(backend, "accounts", p, fmt="csv")
        assert result["format"] == "csv"

    def test_unknown_fmt_raises(self, tmp_path: Path) -> None:
        p = tmp_path / "records.txt"
        p.write_text("{}\n", encoding="utf-8")
        backend = _make_stub_backend([])
        with pytest.raises(D365Error, match="Unsupported"):
            _import_records(backend, "accounts", p, fmt="xml")


# ── chunking ─────────────────────────────────────────────────────────────────


class TestChunking:
    def test_n_records_k_chunk_size(self, tmp_path: Path) -> None:
        n, k = 7, 3
        records = "\n".join(json.dumps({"name": f"R{i}"}) for i in range(n))
        p = tmp_path / "data.jsonl"
        p.write_text(records + "\n", encoding="utf-8")

        expected_chunks = math.ceil(n / k)
        # Build result lists for each chunk
        chunk_results = []
        remaining = n
        for _ in range(expected_chunks):
            count = min(k, remaining)
            chunk_results.append(_make_2xx_results(count))
            remaining -= count

        backend = _make_stub_backend(chunk_results)
        result = _import_records(backend, "accounts", p, chunk_size=k)

        assert result["chunks"] == expected_chunks
        assert backend.batch.call_count == expected_chunks

    def test_chunk_size_1(self, tmp_path: Path) -> None:
        n = 3
        records = "\n".join(json.dumps({"name": f"R{i}"}) for i in range(n))
        p = tmp_path / "data.jsonl"
        p.write_text(records + "\n", encoding="utf-8")
        backend = _make_stub_backend([_make_2xx_results(1)] * n)
        result = _import_records(backend, "accounts", p, chunk_size=1)
        assert result["chunks"] == n
        assert backend.batch.call_count == n

    def test_invalid_chunk_size_raises(self, tmp_path: Path) -> None:
        p = tmp_path / "data.jsonl"
        p.write_text('{"name": "x"}\n', encoding="utf-8")
        backend = _make_stub_backend([])
        with pytest.raises(D365Error, match="chunk_size"):
            _import_records(backend, "accounts", p, chunk_size=0)

    def test_batch_called_with_correct_ops(self, tmp_path: Path) -> None:
        """Verify the actual ops passed to batch() have the right structure."""
        records = [{"name": "A"}, {"name": "B"}]
        p = tmp_path / "data.jsonl"
        p.write_text("\n".join(json.dumps(r) for r in records) + "\n", encoding="utf-8")
        backend = _make_stub_backend([_make_2xx_results(2)])
        _import_records(backend, "accounts", p, chunk_size=10)

        assert backend.batch.call_count == 1
        ops = backend.batch.call_args[0][0]
        assert len(ops) == 2
        assert ops[0]["method"] == "POST"
        assert ops[0]["url"] == "accounts"
        assert ops[0]["body"] == {"name": "A"}


# ── JSONL typed passthrough ───────────────────────────────────────────────────


class TestJsonlPassthrough:
    def test_odata_bind_preserved(self, tmp_path: Path) -> None:
        record = {
            "name": "Contoso",
            "primarycontactid@odata.bind": "/contacts(12345678-1234-1234-1234-123456789abc)",
        }
        p = tmp_path / "data.jsonl"
        p.write_text(json.dumps(record) + "\n", encoding="utf-8")
        backend = _make_stub_backend([_make_2xx_results(1)])
        _import_records(backend, "accounts", p)

        ops = backend.batch.call_args[0][0]
        assert ops[0]["body"]["primarycontactid@odata.bind"] == (
            "/contacts(12345678-1234-1234-1234-123456789abc)"
        )

    def test_numeric_field_stays_int(self, tmp_path: Path) -> None:
        record = {"name": "Test", "numberofemployees": 42}
        p = tmp_path / "data.jsonl"
        p.write_text(json.dumps(record) + "\n", encoding="utf-8")
        backend = _make_stub_backend([_make_2xx_results(1)])
        _import_records(backend, "accounts", p)
        ops = backend.batch.call_args[0][0]
        assert ops[0]["body"]["numberofemployees"] == 42
        assert isinstance(ops[0]["body"]["numberofemployees"], int)

    def test_bool_field_stays_bool(self, tmp_path: Path) -> None:
        record = {"name": "Test", "donotbulkemail": True}
        p = tmp_path / "data.jsonl"
        p.write_text(json.dumps(record) + "\n", encoding="utf-8")
        backend = _make_stub_backend([_make_2xx_results(1)])
        _import_records(backend, "accounts", p)
        ops = backend.batch.call_args[0][0]
        assert ops[0]["body"]["donotbulkemail"] is True

    def test_blank_lines_skipped(self, tmp_path: Path) -> None:
        content = '{"name": "A"}\n\n\n{"name": "B"}\n'
        p = tmp_path / "data.jsonl"
        p.write_text(content, encoding="utf-8")
        backend = _make_stub_backend([_make_2xx_results(2)])
        result = _import_records(backend, "accounts", p)
        assert result["imported"] == 2
        ops = backend.batch.call_args[0][0]
        assert len(ops) == 2


# ── CSV coercion ──────────────────────────────────────────────────────────────


class TestCsvCoercion:
    def test_empty_string_becomes_none(self, tmp_path: Path) -> None:
        p = tmp_path / "data.csv"
        p.write_text("name,revenue\nAlpha,\n", encoding="utf-8")
        backend = _make_stub_backend([_make_2xx_results(1)])
        _import_records(backend, "accounts", p)
        ops = backend.batch.call_args[0][0]
        assert ops[0]["body"]["revenue"] is None

    def test_true_false_string_becomes_bool(self, tmp_path: Path) -> None:
        p = tmp_path / "data.csv"
        p.write_text("name,active,archived\nAlpha,true,FALSE\n", encoding="utf-8")
        backend = _make_stub_backend([_make_2xx_results(1)])
        _import_records(backend, "accounts", p)
        ops = backend.batch.call_args[0][0]
        assert ops[0]["body"]["active"] is True
        assert ops[0]["body"]["archived"] is False

    def test_integer_string_becomes_int(self, tmp_path: Path) -> None:
        p = tmp_path / "data.csv"
        p.write_text("name,employees\nAlpha,100\n", encoding="utf-8")
        backend = _make_stub_backend([_make_2xx_results(1)])
        _import_records(backend, "accounts", p)
        ops = backend.batch.call_args[0][0]
        assert ops[0]["body"]["employees"] == 100
        assert isinstance(ops[0]["body"]["employees"], int)

    def test_float_string_becomes_float(self, tmp_path: Path) -> None:
        p = tmp_path / "data.csv"
        p.write_text("name,revenue\nAlpha,1.5\n", encoding="utf-8")
        backend = _make_stub_backend([_make_2xx_results(1)])
        _import_records(backend, "accounts", p)
        ops = backend.batch.call_args[0][0]
        assert ops[0]["body"]["revenue"] == 1.5
        assert isinstance(ops[0]["body"]["revenue"], float)

    def test_str_stays_str(self, tmp_path: Path) -> None:
        p = tmp_path / "data.csv"
        p.write_text("name,city\nAlpha,London\n", encoding="utf-8")
        backend = _make_stub_backend([_make_2xx_results(1)])
        _import_records(backend, "accounts", p)
        ops = backend.batch.call_args[0][0]
        assert ops[0]["body"]["city"] == "London"
        assert isinstance(ops[0]["body"]["city"], str)

    def test_coercion_order_1_is_int(self, tmp_path: Path) -> None:
        """'1' → int 1, not float."""
        p = tmp_path / "data.csv"
        p.write_text("val\n1\n", encoding="utf-8")
        backend = _make_stub_backend([_make_2xx_results(1)])
        _import_records(backend, "accounts", p)
        ops = backend.batch.call_args[0][0]
        assert ops[0]["body"]["val"] == 1
        assert isinstance(ops[0]["body"]["val"], int)

    def test_coercion_order_1_0_is_float(self, tmp_path: Path) -> None:
        """'1.0' → float 1.0, not int."""
        p = tmp_path / "data.csv"
        p.write_text("val\n1.0\n", encoding="utf-8")
        backend = _make_stub_backend([_make_2xx_results(1)])
        _import_records(backend, "accounts", p)
        ops = backend.batch.call_args[0][0]
        assert ops[0]["body"]["val"] == 1.0
        assert isinstance(ops[0]["body"]["val"], float)

    def test_non_finite_floats_stay_string(self, tmp_path: Path) -> None:
        """'NaN', 'inf', '-inf', 'Infinity' must not produce non-JSON-safe floats."""
        p = tmp_path / "data.csv"
        p.write_text("a,b,c,d\nNaN,inf,-inf,Infinity\n", encoding="utf-8")
        backend = _make_stub_backend([_make_2xx_results(1)])
        _import_records(backend, "accounts", p)
        ops = backend.batch.call_args[0][0]
        body = ops[0]["body"]
        assert body["a"] == "NaN" and isinstance(body["a"], str)
        assert body["b"] == "inf" and isinstance(body["b"], str)
        assert body["c"] == "-inf" and isinstance(body["c"], str)
        assert body["d"] == "Infinity" and isinstance(body["d"], str)

    def test_short_row_missing_column_becomes_none(self, tmp_path: Path) -> None:
        """A row with fewer cells than the header → missing column coerces to None."""
        p = tmp_path / "data.csv"
        p.write_text("name,revenue\nAlpha\n", encoding="utf-8")
        backend = _make_stub_backend([_make_2xx_results(1)])
        _import_records(backend, "accounts", p)
        ops = backend.batch.call_args[0][0]
        assert ops[0]["body"]["name"] == "Alpha"
        assert ops[0]["body"]["revenue"] is None

    def test_extra_columns_row_raises(self, tmp_path: Path) -> None:
        """A row with more cells than the header is rejected with a line number."""
        p = tmp_path / "data.csv"
        p.write_text("name,revenue\nAlpha,100,extra\n", encoding="utf-8")
        backend = _make_stub_backend([_make_2xx_results(1)])
        with pytest.raises(D365Error, match="line 2: more columns"):
            _import_records(backend, "accounts", p)


# ── dry-run ───────────────────────────────────────────────────────────────────


class TestDryRun:
    def test_dry_run_zero_writes(self, tmp_path: Path, profile: ConnectionProfile) -> None:
        """Real D365Backend(dry_run=True): no HTTP attempted, imported=0, failed=0."""
        records = [{"name": "A"}, {"name": "B"}]
        p = tmp_path / "data.jsonl"
        p.write_text("\n".join(json.dumps(r) for r in records) + "\n", encoding="utf-8")

        backend = D365Backend(profile, password="pw", dry_run=True)

        # requests_mock with no registered routes raises NoMockAddress if any
        # HTTP is attempted — this guards against accidental real requests.
        with requests_mock_lib.Mocker():
            result = _import_records(backend, "accounts", p)

        assert result["imported"] == 0
        assert result["failed"] == 0
        assert result["dry_run"] is True
        assert result["chunks"] == 1


# ── continue_on_error guard ───────────────────────────────────────────────────


class TestGuards:
    def test_continue_on_error_with_transactional_raises(self, tmp_path: Path) -> None:
        p = tmp_path / "data.jsonl"
        p.write_text('{"name": "x"}\n', encoding="utf-8")
        backend = _make_stub_backend([])
        with pytest.raises(D365Error, match="continue_on_error"):
            _import_records(
                backend, "accounts", p,
                continue_on_error=True, transactional=True,
            )


# ── upsert mode ───────────────────────────────────────────────────────────────


class TestUpsertMode:
    def test_upsert_builds_patch_op(self, tmp_path: Path) -> None:
        guid = "12345678-1234-1234-1234-123456789abc"
        record = {"accountid": guid, "name": "Contoso"}
        p = tmp_path / "data.jsonl"
        p.write_text(json.dumps(record) + "\n", encoding="utf-8")

        patch_results: list[BatchResult] = [{
            "method": "PATCH",
            "url": f"accounts({guid})",
            "status": 204,
            "headers": {},
            "body": None,
            "error": None,
        }]
        backend = _make_stub_backend([patch_results])
        result = _import_records(
            backend, "accounts", p, mode="upsert", id_column="accountid",
        )

        ops = backend.batch.call_args[0][0]
        assert ops[0]["method"] == "PATCH"
        assert ops[0]["url"] == f"accounts({guid})"
        # id_column removed from body
        assert "accountid" not in ops[0]["body"]
        assert ops[0]["body"]["name"] == "Contoso"
        assert result["imported"] == 1

    def test_upsert_without_id_column_raises(self, tmp_path: Path) -> None:
        p = tmp_path / "data.jsonl"
        p.write_text('{"name": "x"}\n', encoding="utf-8")
        backend = _make_stub_backend([])
        with pytest.raises(D365Error, match="id_column"):
            _import_records(backend, "accounts", p, mode="upsert")

    def test_upsert_record_missing_id_key_raises(self, tmp_path: Path) -> None:
        record = {"name": "NoId"}  # missing 'accountid'
        p = tmp_path / "data.jsonl"
        p.write_text(json.dumps(record) + "\n", encoding="utf-8")
        backend = _make_stub_backend([])
        with pytest.raises(D365Error, match="row"):
            _import_records(
                backend, "accounts", p, mode="upsert", id_column="accountid",
            )

    def test_upsert_by_alternate_key_builds_patch_op(self, tmp_path: Path) -> None:
        record = {"emailaddress1": "joe@x.com", "firstname": "Joe"}
        p = tmp_path / "data.jsonl"
        p.write_text(json.dumps(record) + "\n", encoding="utf-8")

        patch_results: list[BatchResult] = [{
            "method": "PATCH", "url": "contacts(emailaddress1='joe%40x.com')",
            "status": 204, "headers": {}, "body": None, "error": None,
        }]
        backend = _make_stub_backend([patch_results])
        result = _import_records(
            backend, "contacts", p, mode="upsert", alt_key=["emailaddress1"],
        )

        ops = backend.batch.call_args[0][0]
        assert ops[0]["method"] == "PATCH"
        assert ops[0]["url"] == "contacts(emailaddress1='joe%40x.com')"
        # The alternate-key attribute is stripped from the body (URL identifies it).
        assert "emailaddress1" not in ops[0]["body"]
        assert ops[0]["body"]["firstname"] == "Joe"
        assert result["imported"] == 1

    def test_upsert_by_composite_alternate_key(self, tmp_path: Path) -> None:
        record = {"sample_key1": 1, "sample_key2": 2, "name": "1:2"}
        p = tmp_path / "data.jsonl"
        p.write_text(json.dumps(record) + "\n", encoding="utf-8")
        backend = _make_stub_backend([_make_2xx_results(1)])
        _import_records(
            backend, "samples", p, mode="upsert",
            alt_key=["sample_key1", "sample_key2"],
        )
        ops = backend.batch.call_args[0][0]
        assert ops[0]["url"] == "samples(sample_key1=1,sample_key2=2)"
        assert ops[0]["body"] == {"name": "1:2"}

    def test_upsert_alt_key_missing_column_raises(self, tmp_path: Path) -> None:
        record = {"firstname": "Joe"}  # missing 'emailaddress1'
        p = tmp_path / "data.jsonl"
        p.write_text(json.dumps(record) + "\n", encoding="utf-8")
        backend = _make_stub_backend([])
        with pytest.raises(D365Error, match="row 1"):
            _import_records(
                backend, "contacts", p, mode="upsert", alt_key=["emailaddress1"],
            )

    def test_upsert_id_column_and_alt_key_mutually_exclusive(self, tmp_path: Path) -> None:
        p = tmp_path / "data.jsonl"
        p.write_text('{"emailaddress1": "a@b.com"}\n', encoding="utf-8")
        backend = _make_stub_backend([])
        with pytest.raises(D365Error, match="mutually exclusive"):
            _import_records(
                backend, "contacts", p, mode="upsert",
                id_column="contactid", alt_key=["emailaddress1"],
            )

    def test_alt_key_requires_upsert_mode(self, tmp_path: Path) -> None:
        p = tmp_path / "data.jsonl"
        p.write_text('{"emailaddress1": "a@b.com"}\n', encoding="utf-8")
        backend = _make_stub_backend([])
        with pytest.raises(D365Error, match="alt_key is only valid"):
            _import_records(
                backend, "contacts", p, mode="create", alt_key=["emailaddress1"],
            )

    def test_upsert_failure_includes_alt_key_segment(self, tmp_path: Path) -> None:
        record = {"emailaddress1": "joe@x.com", "firstname": "Joe"}
        p = tmp_path / "data.jsonl"
        p.write_text(json.dumps(record) + "\n", encoding="utf-8")
        fail_results: list[BatchResult] = [{
            "method": "PATCH", "url": "contacts(emailaddress1='joe%40x.com')",
            "status": 400, "headers": {}, "body": None, "error": "Bad request",
        }]
        backend = _make_stub_backend([fail_results])
        result = _import_records(
            backend, "contacts", p, mode="upsert", alt_key=["emailaddress1"],
        )
        assert result["failed"] == 1
        assert result["failures"][0]["id"] == "emailaddress1='joe%40x.com'"


# ── output counts ─────────────────────────────────────────────────────────────


class TestOutputCounts:
    def test_empty_file_zero_chunks(self, tmp_path: Path) -> None:
        p = tmp_path / "data.jsonl"
        p.write_text("", encoding="utf-8")
        backend = _make_stub_backend([])
        result = _import_records(backend, "accounts", p)
        assert result["imported"] == 0
        assert result["failed"] == 0
        assert result["chunks"] == 0
        assert backend.batch.call_count == 0

    def test_failed_counted_correctly(self, tmp_path: Path) -> None:
        """Non-2xx results not marked 'dry-run' count as failed."""
        records = "\n".join(json.dumps({"name": f"R{i}"}) for i in range(3))
        p = tmp_path / "data.jsonl"
        p.write_text(records + "\n", encoding="utf-8")

        mixed_results: list[BatchResult] = [
            {"method": "POST", "url": "accounts", "status": 204, "headers": {},
             "body": None, "error": None},
            {"method": "POST", "url": "accounts", "status": 400, "headers": {},
             "body": None, "error": "Bad request"},
            {"method": "POST", "url": "accounts", "status": 204, "headers": {},
             "body": None, "error": None},
        ]
        backend = _make_stub_backend([mixed_results])
        result = _import_records(backend, "accounts", p, chunk_size=10)
        assert result["imported"] == 2
        assert result["failed"] == 1

    def test_result_includes_required_keys(self, tmp_path: Path) -> None:
        p = tmp_path / "data.jsonl"
        p.write_text('{"name": "x"}\n', encoding="utf-8")
        backend = _make_stub_backend([_make_2xx_results(1)])
        result = _import_records(backend, "accounts", p)
        for key in ("imported", "failed", "chunks", "entity_set", "mode", "dry_run"):
            assert key in result, f"missing key: {key}"


# ── per-record failure detail (#332) ──────────────────────────────────────────


class TestFailuresDetail:
    def test_failures_capture_index_status_error(self, tmp_path: Path) -> None:
        """Each failed row yields a {index, status, error} entry, in input order."""
        records = "\n".join(json.dumps({"name": f"R{i}"}) for i in range(3))
        p = tmp_path / "data.jsonl"
        p.write_text(records + "\n", encoding="utf-8")

        mixed: list[BatchResult] = [
            {"method": "POST", "url": "accounts", "status": 204, "headers": {},
             "body": None, "error": None},
            {"method": "POST", "url": "accounts", "status": 400, "headers": {},
             "body": None, "error": "Bad request"},
            {"method": "POST", "url": "accounts", "status": 403, "headers": {},
             "body": None, "error": "Forbidden"},
        ]
        backend = _make_stub_backend([mixed])
        result = _import_records(backend, "accounts", p, chunk_size=10)

        assert result["failures"] == [
            {"index": 2, "status": 400, "error": "Bad request"},
            {"index": 3, "status": 403, "error": "Forbidden"},
        ]

    def test_no_failures_returns_empty_list(self, tmp_path: Path) -> None:
        """Zero-failure import still carries failures as [] (clone's convention)."""
        p = tmp_path / "data.jsonl"
        p.write_text('{"name": "x"}\n', encoding="utf-8")
        backend = _make_stub_backend([_make_2xx_results(1)])
        result = _import_records(backend, "accounts", p)
        assert result["failures"] == []

    def test_indices_span_chunks(self, tmp_path: Path) -> None:
        """Row index is the 1-based position in the input, not within the chunk."""
        records = "\n".join(json.dumps({"name": f"R{i}"}) for i in range(4))
        p = tmp_path / "data.jsonl"
        p.write_text(records + "\n", encoding="utf-8")

        ok: BatchResult = {"method": "POST", "url": "accounts", "status": 204,
                           "headers": {}, "body": None, "error": None}
        bad: BatchResult = {"method": "POST", "url": "accounts", "status": 400,
                            "headers": {}, "body": None, "error": "Bad request"}
        # chunk_size=2 → rows 1,2 then rows 3,4; row 4 fails.
        backend = _make_stub_backend([[ok, ok], [ok, bad]])
        result = _import_records(backend, "accounts", p, chunk_size=2)
        assert result["failures"] == [{"index": 4, "status": 400, "error": "Bad request"}]

    def test_upsert_failure_includes_id(self, tmp_path: Path) -> None:
        """Upsert rows carry the record GUID in the failure entry."""
        guid = "12345678-1234-1234-1234-123456789abc"
        record = {"accountid": guid, "name": "Contoso"}
        p = tmp_path / "data.jsonl"
        p.write_text(json.dumps(record) + "\n", encoding="utf-8")

        fail: list[BatchResult] = [{
            "method": "PATCH", "url": f"accounts({guid})", "status": 400,
            "headers": {}, "body": None, "error": "Bad request",
        }]
        backend = _make_stub_backend([fail])
        result = _import_records(
            backend, "accounts", p, mode="upsert", id_column="accountid",
        )
        assert result["failures"] == [
            {"index": 1, "id": guid, "status": 400, "error": "Bad request"},
        ]

    def test_failure_falls_back_to_http_status_when_error_blank(self, tmp_path: Path) -> None:
        """A non-2xx result with no error string still reports a non-empty message."""
        p = tmp_path / "data.jsonl"
        p.write_text('{"name": "x"}\n', encoding="utf-8")
        backend = _make_stub_backend([[{
            "method": "POST", "url": "accounts", "status": 500,
            "headers": {}, "body": None, "error": None,
        }]])
        result = _import_records(backend, "accounts", p)
        assert result["failures"] == [{"index": 1, "status": 500, "error": "HTTP 500"}]


# ── batch flag forwarding ─────────────────────────────────────────────────────


class TestBatchFlagForwarding:
    def test_non_default_flags_forwarded(self, tmp_path: Path) -> None:
        """transactional=False and continue_on_error=True must reach backend.batch()."""
        p = tmp_path / "data.jsonl"
        p.write_text('{"name": "x"}\n', encoding="utf-8")
        backend = MagicMock(spec=D365Backend)
        backend.dry_run = False
        backend.batch.return_value = _make_2xx_results(1)
        _import_records(
            backend, "accounts", p,
            transactional=False, continue_on_error=True,
        )
        backend.batch.assert_called_once()
        _args, kwargs = backend.batch.call_args
        assert kwargs["transactional"] is False
        assert kwargs["continue_on_error"] is True

    def test_default_flags_forwarded(self, tmp_path: Path) -> None:
        """Default transactional=True and continue_on_error=False must reach backend.batch()."""
        p = tmp_path / "data.jsonl"
        p.write_text('{"name": "x"}\n', encoding="utf-8")
        backend = MagicMock(spec=D365Backend)
        backend.dry_run = False
        backend.batch.return_value = _make_2xx_results(1)
        _import_records(backend, "accounts", p)
        backend.batch.assert_called_once()
        _args, kwargs = backend.batch.call_args
        assert kwargs["transactional"] is True
        assert kwargs["continue_on_error"] is False
