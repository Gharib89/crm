"""Unit tests for Spec C $batch helper + multipart codec."""
# pyright: basic

from __future__ import annotations

import re
from typing import Any

import pytest
import requests_mock

from crm.utils.d365_backend import (
    ConnectionProfile,
    D365Backend,
    D365Error,
    _assemble_batch_body,
    _parse_batch_response,
)


@pytest.fixture
def profile() -> ConnectionProfile:
    return ConnectionProfile(
        name="testp",
        url="https://crm.contoso.local/contoso",
        domain="CONTOSO",
        username="alice",
        api_version="v9.2",
        verify_ssl=False,
    )


@pytest.fixture
def backend(profile) -> D365Backend:
    return D365Backend(profile, password="pw", dry_run=False)


@pytest.fixture
def fixed_boundaries(monkeypatch):
    """Return deterministic uuid.hex values so multipart bodies are byte-stable."""
    counter = {"n": 0}
    names = ["batchXX", "csetXX"]

    class _U:
        @property
        def hex(self) -> str:
            i = counter["n"]
            counter["n"] = (i + 1) % len(names)
            return names[i]

    monkeypatch.setattr("crm.utils.d365_backend.uuid.uuid4", lambda: _U())


class TestAssembly:
    def test_all_get(self, profile, fixed_boundaries):
        ops = [
            {"method": "GET", "url": "accounts?$select=name"},
            {"method": "GET", "url": "contacts(00000000-0000-0000-0000-000000000001)"},
        ]
        body, content_type = _assemble_batch_body(
            ops, transactional=True,
        )
        assert content_type == "multipart/mixed; boundary=batch_batchXX"
        # GET parts only; no changeset wrapper.
        assert body.count("--batch_batchXX") == 3   # 2 parts + closing
        assert "GET accounts?$select=name HTTP/1.1" in body
        assert "multipart/mixed; boundary=changeset" not in body

    def test_single_changeset(self, profile, fixed_boundaries):
        ops = [
            {"method": "POST", "url": "accounts", "body": {"name": "a"}},
            {"method": "PATCH", "url": "accounts(00000000-0000-0000-0000-000000000001)",
             "body": {"name": "b"}},
        ]
        body, _ = _assemble_batch_body(ops, transactional=True)
        assert "multipart/mixed; boundary=changeset_csetXX" in body
        # Two write sub-parts inside the changeset.
        assert body.count("--changeset_csetXX") == 3  # 2 parts + closing
        assert "Content-ID: 1" in body
        assert "Content-ID: 2" in body
        assert "POST accounts HTTP/1.1" in body
        assert "PATCH accounts(00000000-0000-0000-0000-000000000001) HTTP/1.1" in body

    def test_mixed_get_then_writes(self, profile, fixed_boundaries):
        ops = [
            {"method": "GET", "url": "accounts"},
            {"method": "POST", "url": "accounts", "body": {"name": "a"}},
            {"method": "PATCH", "url": "accounts(00000000-0000-0000-0000-000000000001)",
             "body": {"name": "b"}},
        ]
        body, _ = _assemble_batch_body(ops, transactional=True)
        # 1 GET part + 1 changeset part = 2 top-level parts.
        assert body.count("--batch_batchXX") == 3   # 2 + closing
        assert "multipart/mixed; boundary=changeset_csetXX" in body
        assert "GET accounts HTTP/1.1" in body
        assert "Content-ID: 1" in body
        assert "Content-ID: 2" in body

    def test_changeset_honors_caller_content_id(self, profile, fixed_boundaries):
        ops = [
            {"method": "POST", "url": "accounts", "body": {"name": "a"}, "content_id": "acct1"},
            {"method": "PATCH", "url": "$acct1", "body": {"name": "b"}},
        ]
        body, _ = _assemble_batch_body(ops, transactional=True)
        assert "Content-ID: acct1" in body
        assert "Content-ID: 2" in body  # second op falls back to sequence
        assert "PATCH $acct1 HTTP/1.1" in body

    def test_changeset_rejects_duplicate_content_id(self, profile, fixed_boundaries):
        ops = [
            {"method": "POST", "url": "accounts", "body": {"name": "a"}, "content_id": "dup"},
            {"method": "PATCH", "url": "$dup", "body": {"name": "b"}, "content_id": "dup"},
        ]
        with pytest.raises(D365Error, match="duplicate content_id"):
            _assemble_batch_body(ops, transactional=True)

    def test_non_transactional_flattens(self, profile, fixed_boundaries):
        ops = [
            {"method": "POST", "url": "accounts", "body": {"name": "a"}},
            {"method": "PATCH", "url": "accounts(00000000-0000-0000-0000-000000000001)",
             "body": {"name": "b"}},
        ]
        body, _ = _assemble_batch_body(ops, transactional=False)
        assert "boundary=changeset" not in body
        # Two top-level parts directly.
        assert body.count("--batch_batchXX") == 3
        assert "POST accounts HTTP/1.1" in body
        assert "PATCH accounts(00000000-0000-0000-0000-000000000001) HTTP/1.1" in body


class TestParseResponse:
    def _build_response_body(self, parts: list[str], boundary: str) -> bytes:
        text = f"--{boundary}\r\n" + f"\r\n--{boundary}\r\n".join(parts) + f"\r\n--{boundary}--\r\n"
        return text.encode("utf-8")

    def test_parses_two_top_level_gets(self):
        body = self._build_response_body([
            "Content-Type: application/http\r\n"
            "Content-Transfer-Encoding: binary\r\n"
            "\r\n"
            "HTTP/1.1 200 OK\r\n"
            "Content-Type: application/json\r\n"
            "\r\n"
            '{"value": [{"name": "a"}]}',

            "Content-Type: application/http\r\n"
            "Content-Transfer-Encoding: binary\r\n"
            "\r\n"
            "HTTP/1.1 200 OK\r\n"
            "Content-Type: application/json\r\n"
            "\r\n"
            '{"value": [{"name": "b"}]}',
        ], boundary="batchresp")
        ops = [
            {"method": "GET", "url": "accounts"},
            {"method": "GET", "url": "contacts"},
        ]
        results = _parse_batch_response(body, "multipart/mixed; boundary=batchresp", ops)
        assert len(results) == 2
        assert results[0]["status"] == 200
        assert results[0]["body"] == {"value": [{"name": "a"}]}
        assert results[1]["body"] == {"value": [{"name": "b"}]}
        assert all(r["error"] is None for r in results)

    def test_parses_changeset_with_content_id(self):
        cs_part = (
            "Content-Type: multipart/mixed; boundary=cs1\r\n"
            "\r\n"
            "--cs1\r\n"
            "Content-Type: application/http\r\n"
            "Content-Transfer-Encoding: binary\r\n"
            "Content-ID: 1\r\n"
            "\r\n"
            "HTTP/1.1 204 No Content\r\n"
            "OData-EntityId: https://crm.x/api/data/v9.2/accounts(00000000-0000-0000-0000-000000000001)\r\n"
            "\r\n"
            "--cs1\r\n"
            "Content-Type: application/http\r\n"
            "Content-Transfer-Encoding: binary\r\n"
            "Content-ID: 2\r\n"
            "\r\n"
            "HTTP/1.1 204 No Content\r\n"
            "\r\n"
            "--cs1--"
        )
        body = self._build_response_body([cs_part], boundary="batchresp")
        ops = [
            {"method": "POST", "url": "accounts", "body": {"name": "a"}},
            {"method": "PATCH", "url": "accounts(00000000-0000-0000-0000-000000000001)",
             "body": {"name": "b"}},
        ]
        results = _parse_batch_response(body, "multipart/mixed; boundary=batchresp", ops)
        assert len(results) == 2
        assert results[0]["status"] == 204
        assert results[1]["status"] == 204
        assert results[0]["headers"].get("OData-EntityId", "").endswith(
            "accounts(00000000-0000-0000-0000-000000000001)"
        )

    def test_parses_non_transactional_writes_at_top_level(self):
        body = self._build_response_body([
            "Content-Type: application/http\r\n"
            "Content-Transfer-Encoding: binary\r\n"
            "\r\n"
            "HTTP/1.1 204 No Content\r\n"
            "OData-EntityId: https://x/accounts(11111111-1111-1111-1111-111111111111)\r\n"
            "\r\n",
            "Content-Type: application/http\r\n"
            "Content-Transfer-Encoding: binary\r\n"
            "\r\n"
            "HTTP/1.1 204 No Content\r\n"
            "OData-EntityId: https://x/contacts(22222222-2222-2222-2222-222222222222)\r\n"
            "\r\n",
        ], boundary="batchresp")
        ops = [
            {"method": "POST", "url": "accounts", "body": {"name": "a"}},
            {"method": "POST", "url": "contacts", "body": {"firstname": "c"}},
        ]
        results = _parse_batch_response(body, "multipart/mixed; boundary=batchresp", ops, transactional=False)
        assert len(results) == 2
        assert results[0]["status"] == 204
        assert results[1]["status"] == 204
        assert results[0]["error"] is None
        assert results[1]["error"] is None

    def test_parses_changeset_with_string_content_id(self):
        cs_part = (
            "Content-Type: multipart/mixed; boundary=cs1\r\n"
            "\r\n"
            "--cs1\r\n"
            "Content-Type: application/http\r\n"
            "Content-Transfer-Encoding: binary\r\n"
            "Content-ID: acct1\r\n"
            "\r\n"
            "HTTP/1.1 204 No Content\r\n"
            "OData-EntityId: https://x/accounts(11111111-1111-1111-1111-111111111111)\r\n"
            "\r\n"
            "--cs1--"
        )
        body = self._build_response_body([cs_part], boundary="batchresp")
        ops = [
            {"method": "POST", "url": "accounts", "body": {"name": "a"}, "content_id": "acct1"},
        ]
        results = _parse_batch_response(
            body, "multipart/mixed; boundary=batchresp", ops, transactional=True,
        )
        assert results[0]["status"] == 204
        assert "accounts(11111111-1111-1111-1111-111111111111)" in (
            results[0]["headers"].get("OData-EntityId", "")
        )

    def test_parses_changeset_out_of_order_via_string_ids(self):
        # Response delivers cs parts in reverse order; cid mapping must restore alignment.
        cs_part = (
            "Content-Type: multipart/mixed; boundary=cs1\r\n"
            "\r\n"
            "--cs1\r\n"
            "Content-Type: application/http\r\n"
            "Content-Transfer-Encoding: binary\r\n"
            "Content-ID: b\r\n"
            "\r\n"
            "HTTP/1.1 204 No Content\r\n"
            "OData-EntityId: https://x/contacts(22222222-2222-2222-2222-222222222222)\r\n"
            "\r\n"
            "--cs1\r\n"
            "Content-Type: application/http\r\n"
            "Content-Transfer-Encoding: binary\r\n"
            "Content-ID: a\r\n"
            "\r\n"
            "HTTP/1.1 204 No Content\r\n"
            "OData-EntityId: https://x/accounts(11111111-1111-1111-1111-111111111111)\r\n"
            "\r\n"
            "--cs1--"
        )
        body = self._build_response_body([cs_part], boundary="batchresp")
        ops = [
            {"method": "POST", "url": "accounts", "body": {"name": "x"}, "content_id": "a"},
            {"method": "POST", "url": "contacts", "body": {"name": "y"}, "content_id": "b"},
        ]
        results = _parse_batch_response(
            body, "multipart/mixed; boundary=batchresp", ops, transactional=True,
        )
        # Despite the response coming in cs-part order [b, a], results align to input ops by cid.
        assert "accounts(11111111-1111-1111-1111-111111111111)" in (
            results[0]["headers"].get("OData-EntityId", "")
        )
        assert "contacts(22222222-2222-2222-2222-222222222222)" in (
            results[1]["headers"].get("OData-EntityId", "")
        )

    def test_error_populated_on_non_2xx_subpart(self):
        body = self._build_response_body([
            "Content-Type: application/http\r\n"
            "Content-Transfer-Encoding: binary\r\n"
            "\r\n"
            "HTTP/1.1 404 Not Found\r\n"
            "Content-Type: application/json\r\n"
            "\r\n"
            '{"error":{"code":"0x80040217","message":"Record not found"}}',
        ], boundary="batchresp")
        ops = [{"method": "GET", "url": "accounts(00000000-0000-0000-0000-000000000099)"}]
        results = _parse_batch_response(body, "multipart/mixed; boundary=batchresp", ops)
        assert results[0]["status"] == 404
        assert "Record not found" in (results[0]["error"] or "")


class TestBatchMethod:
    def test_batch_round_trip_writes_only(self, backend, profile, fixed_boundaries):
        ops = [
            {"method": "POST", "url": "accounts", "body": {"name": "a"}},
            {"method": "POST", "url": "contacts", "body": {"firstname": "c"}},
        ]
        resp_body = (
            "--batchresp\r\n"
            "Content-Type: multipart/mixed; boundary=cs1\r\n"
            "\r\n"
            "--cs1\r\n"
            "Content-Type: application/http\r\n"
            "Content-Transfer-Encoding: binary\r\n"
            "Content-ID: 1\r\n"
            "\r\n"
            "HTTP/1.1 204 No Content\r\n"
            "OData-EntityId: https://x/accounts(11111111-1111-1111-1111-111111111111)\r\n"
            "\r\n"
            "--cs1\r\n"
            "Content-Type: application/http\r\n"
            "Content-Transfer-Encoding: binary\r\n"
            "Content-ID: 2\r\n"
            "\r\n"
            "HTTP/1.1 204 No Content\r\n"
            "OData-EntityId: https://x/contacts(22222222-2222-2222-2222-222222222222)\r\n"
            "\r\n"
            "--cs1--\r\n"
            "--batchresp--\r\n"
        )
        with requests_mock.Mocker() as m:
            m.post(
                f"{profile.api_base}$batch",
                content=resp_body.encode("utf-8"),
                headers={"Content-Type": "multipart/mixed; boundary=batchresp"},
                status_code=200,
            )
            results = backend.batch(ops)
        assert len(results) == 2
        assert results[0]["status"] == 204
        assert results[1]["status"] == 204
        assert "accounts(11111111-1111-1111-1111-111111111111)" in (
            results[0]["headers"].get("OData-EntityId", "")
        )

    def test_batch_validates_method(self, backend):
        with pytest.raises(D365Error, match="method"):
            backend.batch([{"method": "POKE", "url": "accounts"}])

    def test_batch_requires_url(self, backend):
        with pytest.raises(D365Error, match="url"):
            backend.batch([{"method": "GET"}])

    def test_batch_rejects_body_on_get(self, backend):
        with pytest.raises(D365Error, match="body"):
            backend.batch([{"method": "GET", "url": "accounts", "body": {"x": 1}}])

    def test_batch_rejects_body_on_delete(self, backend):
        with pytest.raises(D365Error, match="body"):
            backend.batch([{"method": "DELETE", "url": "accounts(x)", "body": {"x": 1}}])

    def test_batch_retries_on_429(self, backend, profile, fixed_boundaries, monkeypatch):
        monkeypatch.setattr("crm.utils.d365_backend.time.sleep", lambda *_: None)
        ops = [{"method": "GET", "url": "accounts"}]
        resp_body = (
            "--batchresp\r\n"
            "Content-Type: application/http\r\n"
            "Content-Transfer-Encoding: binary\r\n"
            "\r\n"
            "HTTP/1.1 200 OK\r\n"
            "Content-Type: application/json\r\n"
            "\r\n"
            '{"value": []}\r\n'
            "--batchresp--\r\n"
        )
        with requests_mock.Mocker() as m:
            m.post(
                f"{profile.api_base}$batch",
                [
                    {"status_code": 429, "headers": {"Retry-After": "0"}, "text": ""},
                    {"status_code": 200,
                     "headers": {"Content-Type": "multipart/mixed; boundary=batchresp"},
                     "content": resp_body.encode("utf-8")},
                ],
            )
            results = backend.batch(ops)
        assert m.call_count == 2
        assert results[0]["status"] == 200

    def test_batch_rejects_missing_body_on_post(self, backend):
        with pytest.raises(D365Error, match="body required"):
            backend.batch([{"method": "POST", "url": "accounts"}])

    def test_batch_rejects_missing_body_on_patch(self, backend):
        with pytest.raises(D365Error, match="body required"):
            backend.batch([{"method": "PATCH", "url": "accounts(x)"}])

    def test_batch_rejects_empty_string_content_id(self, backend):
        with pytest.raises(D365Error, match="content_id"):
            backend.batch([{"method": "POST", "url": "accounts", "body": {}, "content_id": ""}])

    def test_batch_rejects_non_str_int_content_id(self, backend):
        with pytest.raises(D365Error, match="content_id"):
            backend.batch([{"method": "POST", "url": "accounts", "body": {}, "content_id": 1.5}])

    def test_batch_rejects_bool_content_id(self, backend):
        with pytest.raises(D365Error, match="content_id"):
            backend.batch([{"method": "POST", "url": "accounts", "body": {}, "content_id": True}])

    def test_batch_dry_run_returns_preview_without_http(self, profile, fixed_boundaries):
        b = D365Backend(profile, password="pw", dry_run=True)
        with requests_mock.Mocker() as m:
            preview = b.batch([{"method": "GET", "url": "accounts"}])
            assert m.call_count == 0
        assert isinstance(preview, list)
        assert len(preview) == 1
        assert preview[0]["status"] == 0
        assert preview[0]["error"] is None or preview[0]["error"] == "dry-run"



class TestParseBatchFile:
    def test_parses_valid_list(self, tmp_path):
        from crm.core.batch import parse_batch_file
        p = tmp_path / "batch.json"
        p.write_text(
            '[{"method": "GET", "url": "accounts"}, '
            '{"method": "POST", "url": "accounts", "body": {"name": "a"}}]',
            encoding="utf-8",
        )
        ops = parse_batch_file(p)
        assert len(ops) == 2
        assert ops[0]["method"] == "GET"
        assert ops[1]["body"] == {"name": "a"}

    def test_rejects_non_list_root(self, tmp_path):
        from crm.core.batch import parse_batch_file
        p = tmp_path / "batch.json"
        p.write_text('{"method": "GET", "url": "x"}', encoding="utf-8")
        with pytest.raises(D365Error, match="list"):
            parse_batch_file(p)

    def test_rejects_invalid_method(self, tmp_path):
        from crm.core.batch import parse_batch_file
        p = tmp_path / "batch.json"
        p.write_text('[{"method": "POKE", "url": "x"}]', encoding="utf-8")
        with pytest.raises(D365Error, match="method"):
            parse_batch_file(p)

    def test_rejects_missing_url(self, tmp_path):
        from crm.core.batch import parse_batch_file
        p = tmp_path / "batch.json"
        p.write_text('[{"method": "GET"}]', encoding="utf-8")
        with pytest.raises(D365Error, match="url"):
            parse_batch_file(p)

    def test_rejects_body_on_get(self, tmp_path):
        from crm.core.batch import parse_batch_file
        p = tmp_path / "batch.json"
        p.write_text('[{"method": "GET", "url": "x", "body": {"a": 1}}]', encoding="utf-8")
        with pytest.raises(D365Error, match="body"):
            parse_batch_file(p)


class TestBatchCLI:
    def test_continue_on_error_rejected_in_transactional_mode(self, tmp_path):
        from click.testing import CliRunner
        from crm import cli as crm_cli
        runner = CliRunner()
        p = tmp_path / "b.json"
        p.write_text('[{"method": "GET", "url": "accounts"}]', encoding="utf-8")
        result = runner.invoke(crm_cli.cli, [
            "batch", str(p), "--continue-on-error",
        ], env={"D365_URL": "https://x/y", "D365_USER": "u",
                "D365_PASSWORD": "p", "D365_DOMAIN": "d"})
        assert result.exit_code != 0
        assert "continue-on-error" in result.output.lower() or "transaction" in result.output.lower()
