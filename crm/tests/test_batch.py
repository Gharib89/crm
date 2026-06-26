"""Unit tests for Spec C $batch helper + multipart codec."""
# pyright: basic

from __future__ import annotations

import re
from typing import Any

import pytest
import requests_mock

from crm.utils.d365_backend import (
    D365Backend,
    D365Error,
    _assemble_batch_body,
    _extract_batch_error,
    _parse_batch_response,
)


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


class TestExtractBatchError:
    def test_invalid_utf8_in_part_does_not_raise(self):
        # Error-path robustness: malformed bytes in a part must not raise
        # UnicodeDecodeError and mask the original $batch failure.
        body = (
            b"--bx\r\n"
            b"Content-Type: application/htt\xffp\r\n"  # invalid byte in the decoded ctype value
            b"\r\n"
            b"HTTP/1.1 404 Not Found\r\n"
            b"Content-Type: application/json\r\n"
            b"\r\n"
            b'{"error":{"code":"0x1","message":"boom \xff"}}\r\n'
            b"--bx--\r\n"
        )
        # Must return cleanly (not raise); message decoded with replacement.
        msg, code = _extract_batch_error(body, "multipart/mixed; boundary=bx")
        assert code == "0x1"
        assert msg is not None and "boom" in msg


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

    def test_batch_whole_failure_extracts_aspnet_message(self, backend, profile):
        # Whole-$batch non-2xx with a flat multipart body carrying the ASP.NET
        # routing shape {"Message": ...}: the raised error must carry the inner
        # message, never the raw --batchresponse_ boundary blob.
        resp_body = (
            "--batchresponse_x\r\n"
            "Content-Type: application/http\r\n"
            "Content-Transfer-Encoding: binary\r\n"
            "\r\n"
            "HTTP/1.1 404 Not Found\r\n"
            "Content-Type: application/json\r\n"
            "\r\n"
            '{"Message":"No HTTP resource was found that matches the request URI '
            "'http://host/contacts?$top=1'.\"}\r\n"
            "--batchresponse_x--\r\n"
        )
        with requests_mock.Mocker() as m:
            m.post(
                f"{profile.api_base}$batch",
                content=resp_body.encode("utf-8"),
                headers={"Content-Type": "multipart/mixed; boundary=batchresponse_x"},
                status_code=404,
            )
            with pytest.raises(D365Error) as exc:
                backend.batch([{"method": "GET", "url": "contacts?$top=1"}])
        assert "No HTTP resource was found" in str(exc.value)
        assert "--batchresponse" not in str(exc.value)
        # Full raw body preserved for debugging.
        assert "--batchresponse_x" in (exc.value.response_body or "")

    def test_batch_whole_failure_extracts_odata_message_and_code(self, backend, profile):
        # Bare-URL DELETE on a nonexistent GUID in a transactional batch rolls
        # back the whole $batch → non-2xx with a nested changesetresponse
        # carrying the OData {"error":{"code","message"}} shape. The code must
        # propagate to D365Error.code (→ meta.code in the envelope).
        resp_body = (
            "--batchresponse_x\r\n"
            "Content-Type: multipart/mixed; boundary=changesetresponse_y\r\n"
            "\r\n"
            "--changesetresponse_y\r\n"
            "Content-Type: application/http\r\n"
            "Content-Transfer-Encoding: binary\r\n"
            "\r\n"
            "HTTP/1.1 404 Not Found\r\n"
            "Content-Type: application/json\r\n"
            "\r\n"
            '{"error":{"code":"0x80040217","message":"contact With Id = '
            '00000000-0000-0000-0000-000000000099 Does Not Exist"}}\r\n'
            "--changesetresponse_y--\r\n"
            "--batchresponse_x--\r\n"
        )
        with requests_mock.Mocker() as m:
            m.post(
                f"{profile.api_base}$batch",
                content=resp_body.encode("utf-8"),
                headers={"Content-Type": "multipart/mixed; boundary=batchresponse_x"},
                status_code=404,
            )
            with pytest.raises(D365Error) as exc:
                backend.batch([
                    {"method": "DELETE",
                     "url": "contacts(00000000-0000-0000-0000-000000000099)"},
                ])
        assert "Does Not Exist" in str(exc.value)
        assert "--batchresponse" not in str(exc.value)
        assert exc.value.code == "0x80040217"

    def test_batch_whole_failure_code_without_message(self, backend, profile):
        # Inner error carries a code but an empty message: the code must still
        # reach D365Error.code, and the raw boundary blob must not leak.
        resp_body = (
            "--batchresponse_x\r\n"
            "Content-Type: multipart/mixed; boundary=changesetresponse_y\r\n"
            "\r\n"
            "--changesetresponse_y\r\n"
            "Content-Type: application/http\r\n"
            "Content-Transfer-Encoding: binary\r\n"
            "\r\n"
            "HTTP/1.1 400 Bad Request\r\n"
            "Content-Type: application/json\r\n"
            "\r\n"
            '{"error":{"code":"0x80040203","message":""}}\r\n'
            "--changesetresponse_y--\r\n"
            "--batchresponse_x--\r\n"
        )
        with requests_mock.Mocker() as m:
            m.post(
                f"{profile.api_base}$batch",
                content=resp_body.encode("utf-8"),
                headers={"Content-Type": "multipart/mixed; boundary=batchresponse_x"},
                status_code=400,
            )
            with pytest.raises(D365Error) as exc:
                backend.batch([{"method": "POST", "url": "accounts", "body": {"x": 1}}])
        assert exc.value.code == "0x80040203"
        assert "--batchresponse" not in str(exc.value)

    def test_batch_whole_failure_unparseable_body_falls_back(self, backend, profile):
        # No parseable inner error → readable truncated body text, not a crash.
        with requests_mock.Mocker() as m:
            m.post(
                f"{profile.api_base}$batch",
                text="500 Internal Server Error (plain text, not multipart)",
                headers={"Content-Type": "text/plain"},
                status_code=500,
            )
            with pytest.raises(D365Error) as exc:
                backend.batch([{"method": "GET", "url": "contacts"}])
        assert "Internal Server Error" in str(exc.value)
        assert exc.value.code is None

    def test_batch_whole_failure_inner_message_not_truncated(self, backend, profile):
        # The inner message must be parsed in full, never amputated by the
        # 500-char raw-body cap that bit the old code.
        long_msg = "Record not found: " + "x" * 700
        resp_body = (
            "--batchresponse_x\r\n"
            "Content-Type: multipart/mixed; boundary=changesetresponse_y\r\n"
            "\r\n"
            "--changesetresponse_y\r\n"
            "Content-Type: application/http\r\n"
            "Content-Transfer-Encoding: binary\r\n"
            "\r\n"
            "HTTP/1.1 404 Not Found\r\n"
            "Content-Type: application/json\r\n"
            "\r\n"
            '{"error":{"code":"0x80040217","message":"' + long_msg + '"}}\r\n'
            "--changesetresponse_y--\r\n"
            "--batchresponse_x--\r\n"
        )
        with requests_mock.Mocker() as m:
            m.post(
                f"{profile.api_base}$batch",
                content=resp_body.encode("utf-8"),
                headers={"Content-Type": "multipart/mixed; boundary=batchresponse_x"},
                status_code=404,
            )
            with pytest.raises(D365Error) as exc:
                backend.batch([
                    {"method": "DELETE",
                     "url": "contacts(00000000-0000-0000-0000-000000000099)"},
                ])
        assert long_msg in str(exc.value)

    def test_batch_validates_method(self, backend):
        with pytest.raises(D365Error, match="method"):
            backend.batch([{"method": "POKE", "url": "accounts"}])

    def test_batch_requires_url(self, backend):
        with pytest.raises(D365Error, match="url"):
            backend.batch([{"method": "GET"}])

    def test_batch_rejects_non_str_url(self, backend):
        # TypedDicts aren't enforced at runtime; a non-str url must fail as a
        # clear D365Error, not crash with AttributeError on .startswith.
        with pytest.raises(D365Error, match="url"):
            backend.batch([{"method": "GET", "url": 123}])

    def test_batch_rejects_leading_slash_url(self, backend):
        # Leading-slash URLs resolve against the host root (404), not the
        # service root — reject client-side with a bare-relative-path hint.
        with pytest.raises(D365Error, match="relative"):
            backend.batch([{"method": "GET", "url": "/contacts(00000000-0000-0000-0000-000000000001)"}])

    def test_batch_rejects_leading_slash_url_in_dry_run(self, profile):
        # Validation runs before the dry-run branch, so the check fires there too.
        b = D365Backend(profile, password="pw", dry_run=True)
        with pytest.raises(D365Error, match="relative"):
            b.batch([{"method": "GET", "url": "/contacts"}])

    def test_batch_rejects_body_on_get(self, backend):
        with pytest.raises(D365Error, match="body"):
            backend.batch([{"method": "GET", "url": "accounts", "body": {"x": 1}}])

    def test_batch_rejects_body_on_delete(self, backend):
        with pytest.raises(D365Error, match="body"):
            backend.batch([{"method": "DELETE", "url": "accounts(x)", "body": {"x": 1}}])

    def test_batch_retries_on_429(self, backend, profile, fixed_boundaries, no_sleep):
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

    @pytest.mark.parametrize("method,url", [("POST", "accounts"), ("PATCH", "accounts(x)")])
    def test_batch_rejects_missing_body(self, backend, method, url):
        with pytest.raises(D365Error, match="body required"):
            backend.batch([{"method": method, "url": url}])

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
        ])
        assert result.exit_code != 0
        assert "continue-on-error" in result.output.lower() or "transaction" in result.output.lower()

    def test_batch_cli_accepts_output_flags(self, backend, monkeypatch, tmp_path):
        monkeypatch.setattr("crm.cli.CLIContext.backend", lambda self: backend)
        from click.testing import CliRunner
        from crm import cli as crm_cli
        runner = CliRunner()
        p = tmp_path / "b.json"
        p.write_text('[{"method": "GET", "url": "accounts"}]', encoding="utf-8")

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

        # Test -o
        out_o = tmp_path / "out_o.json"
        with requests_mock.Mocker() as m:
            m.post(
                backend.url_for("$batch"),
                content=resp_body.encode("utf-8"),
                headers={"Content-Type": "multipart/mixed; boundary=batchresp"},
                status_code=200,
            )
            result = runner.invoke(crm_cli.cli, [
                "--json", "batch", str(p), "-o", str(out_o),
            ])
        assert result.exit_code == 0, result.output
        assert out_o.exists()

        # Test --output
        out_output = tmp_path / "out_output.json"
        with requests_mock.Mocker() as m:
            m.post(
                backend.url_for("$batch"),
                content=resp_body.encode("utf-8"),
                headers={"Content-Type": "multipart/mixed; boundary=batchresp"},
                status_code=200,
            )
            result = runner.invoke(crm_cli.cli, [
                "--json", "batch", str(p), "--output", str(out_output),
            ])
        assert result.exit_code == 0, result.output
        assert out_output.exists()


class TestRenderBatchSummary:
    def test_empty_results(self):
        from crm.core.batch import render_batch_summary
        assert render_batch_summary([]) == {"total": 0, "success": 0, "failed": 0}

    def test_mixed_2xx_and_failure(self):
        from crm.core.batch import render_batch_summary
        results = [
            {"status": 200},
            {"status": 204},
            {"status": 299},
            {"status": 400},
            {"status": 500},
            {"status": 0},
        ]
        assert render_batch_summary(results) == {"total": 6, "success": 3, "failed": 3}

    def test_status_300_counts_as_failed(self):
        from crm.core.batch import render_batch_summary
        assert render_batch_summary([{"status": 300}]) == {
            "total": 1, "success": 0, "failed": 1,
        }


class TestParseBatchFileRequiresBody:
    @pytest.mark.parametrize("op", [
        '{"method": "POST", "url": "accounts"}',
        '{"method": "PATCH", "url": "accounts(1)"}',
    ])
    def test_write_without_body_rejected(self, tmp_path, op):
        from crm.core.batch import parse_batch_file
        p = tmp_path / "b.json"
        p.write_text(f"[{op}]", encoding="utf-8")
        with pytest.raises(D365Error, match="requires a JSON object 'body'"):
            parse_batch_file(p)


class TestParseBatchFileIOError:
    def test_missing_file_raises_d365error(self, tmp_path):
        from crm.core.batch import parse_batch_file
        with pytest.raises(D365Error, match="Could not read"):
            parse_batch_file(tmp_path / "missing.json")
