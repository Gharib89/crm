"""`$batch` honours the same environment defaults a single request does (#1294).

The platform reads MSCRMCallerID from the outer `$batch` request and
MSCRM.SuppressDuplicateDetection from the part, so each resolved header is sent
in both places. All HTTP is mocked.
"""
# pyright: basic

from __future__ import annotations

import pytest
import requests_mock

from crm.utils.d365_backend import D365Backend, D365Error
from crm.utils.d365_types import BatchOperation

CALLER = "11111111-2222-3333-4444-555555555555"
OBJECT_ID = "66666666-7777-8888-9999-aaaaaaaaaaaa"
RESP = (
    "--r\r\nContent-Type: application/http\r\nContent-Transfer-Encoding: binary\r\n\r\n"
    "HTTP/1.1 204 No Content\r\n\r\n--r--\r\n"
)
WRITE: BatchOperation = {"method": "POST", "url": "accounts", "body": {"name": "a"}}
GET: BatchOperation = {"method": "GET", "url": "accounts"}


@pytest.fixture(autouse=True)
def _clean_env(monkeypatch):
    for name in (
        "CRM_AS_USER",
        "CRM_AS_USER_OBJECT_ID",
        "CRM_BYPASS_PLUGINS",
        "CRM_SUPPRESS_DUP",
    ):
        monkeypatch.delenv(name, raising=False)


def _send(profile, ops, *, transactional=True):
    """Run a batch under the current env; return (outer headers, body text)."""
    backend = D365Backend(profile, password="pw", dry_run=False)
    with requests_mock.Mocker() as m:
        m.post(
            f"{profile.api_base}$batch",
            content=RESP.encode(),
            headers={"Content-Type": "multipart/mixed; boundary=r"},
        )
        backend.batch(ops, transactional=transactional)
    req = m.request_history[0]
    return req.headers, req.body.decode()


def _part_header_lines(body: str, name: str) -> list[str]:
    return [ln for ln in body.split("\r\n") if ln.lower().startswith(name.lower() + ":")]


def test_caller_id_on_outer_request_and_every_part(profile, monkeypatch):
    monkeypatch.setenv("CRM_AS_USER", CALLER)
    headers, body = _send(profile, [WRITE, WRITE, GET])
    assert headers["MSCRMCallerID"] == CALLER
    assert "CallerObjectId" not in headers
    assert _part_header_lines(body, "MSCRMCallerID") == [f"MSCRMCallerID: {CALLER}"] * 3
    assert _part_header_lines(body, "CallerObjectId") == []


def test_caller_object_id_in_both_places_and_no_caller_id(profile, monkeypatch):
    monkeypatch.setenv("CRM_AS_USER_OBJECT_ID", OBJECT_ID)
    headers, body = _send(profile, [WRITE, GET])
    assert headers["CallerObjectId"] == OBJECT_ID
    assert "MSCRMCallerID" not in headers
    assert _part_header_lines(body, "CallerObjectId") == [f"CallerObjectId: {OBJECT_ID}"] * 2
    assert _part_header_lines(body, "MSCRMCallerID") == []


def test_both_identities_fail_before_any_request(profile, monkeypatch):
    monkeypatch.setenv("CRM_AS_USER", CALLER)
    monkeypatch.setenv("CRM_AS_USER_OBJECT_ID", OBJECT_ID)
    backend = D365Backend(profile, password="pw", dry_run=False)
    with requests_mock.Mocker() as m:
        with pytest.raises(D365Error, match="both systemuserid"):
            backend.batch([WRITE])
    assert m.call_count == 0


def test_both_identities_fail_a_dry_run_batch_too(profile, monkeypatch):
    monkeypatch.setenv("CRM_AS_USER", CALLER)
    monkeypatch.setenv("CRM_AS_USER_OBJECT_ID", OBJECT_ID)
    backend = D365Backend(profile, password="pw", dry_run=True)
    with pytest.raises(D365Error, match="both systemuserid"):
        backend.batch([WRITE])


@pytest.mark.parametrize(
    ("env", "header"),
    [
        ("CRM_BYPASS_PLUGINS", "MSCRM.BypassCustomPluginExecution"),
        ("CRM_SUPPRESS_DUP", "MSCRM.SuppressDuplicateDetection"),
    ],
)
def test_flag_headers_on_outer_request_and_every_part(profile, monkeypatch, env, header):
    monkeypatch.setenv(env, "1")
    headers, body = _send(profile, [WRITE, GET, WRITE], transactional=False)
    assert headers[header] == "true"
    assert _part_header_lines(body, header) == [f"{header}: true"] * 3


def test_changeset_and_top_level_parts_both_carry_headers(profile, monkeypatch):
    monkeypatch.setenv("CRM_SUPPRESS_DUP", "1")
    _, body = _send(profile, [GET, WRITE, WRITE])
    # One top-level GET plus two writes inside a changeset.
    assert "multipart/mixed; boundary=changeset_" in body
    assert len(_part_header_lines(body, "MSCRM.SuppressDuplicateDetection")) == 3


def test_operation_header_wins_once_case_insensitively(profile, monkeypatch):
    monkeypatch.setenv("CRM_SUPPRESS_DUP", "1")
    monkeypatch.setenv("CRM_AS_USER", CALLER)
    op = {
        **WRITE,
        "headers": {
            "mscrm.suppressduplicatedetection": "false",
            "MSCRMCallerID": OBJECT_ID,
        },
    }
    _, body = _send(profile, [op])
    assert _part_header_lines(body, "MSCRM.SuppressDuplicateDetection") == [
        "mscrm.suppressduplicatedetection: false"
    ]
    assert _part_header_lines(body, "MSCRMCallerID") == [f"MSCRMCallerID: {OBJECT_ID}"]


def test_operation_caller_object_id_displaces_env_caller_id(profile, monkeypatch):
    monkeypatch.setenv("CRM_AS_USER", CALLER)
    op = {**WRITE, "headers": {"CallerObjectId": OBJECT_ID}}
    _, body = _send(profile, [op])
    assert _part_header_lines(body, "CallerObjectId") == [f"CallerObjectId: {OBJECT_ID}"]
    assert _part_header_lines(body, "MSCRMCallerID") == []


def test_no_env_adds_nothing(profile):
    headers, body = _send(profile, [WRITE, GET])
    for name in (
        "MSCRMCallerID",
        "CallerObjectId",
        "MSCRM.BypassCustomPluginExecution",
        "MSCRM.SuppressDuplicateDetection",
    ):
        assert name not in headers
        assert _part_header_lines(body, name) == []
