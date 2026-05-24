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
            ops, profile.api_base, transactional=True,
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
        body, _ = _assemble_batch_body(ops, profile.api_base, transactional=True)
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
        body, _ = _assemble_batch_body(ops, profile.api_base, transactional=True)
        # 1 GET part + 1 changeset part = 2 top-level parts.
        assert body.count("--batch_batchXX") == 3   # 2 + closing
        assert "multipart/mixed; boundary=changeset_csetXX" in body
        assert "GET accounts HTTP/1.1" in body
        assert "Content-ID: 1" in body
        assert "Content-ID: 2" in body

    def test_non_transactional_flattens(self, profile, fixed_boundaries):
        ops = [
            {"method": "POST", "url": "accounts", "body": {"name": "a"}},
            {"method": "PATCH", "url": "accounts(00000000-0000-0000-0000-000000000001)",
             "body": {"name": "b"}},
        ]
        body, _ = _assemble_batch_body(ops, profile.api_base, transactional=False)
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
