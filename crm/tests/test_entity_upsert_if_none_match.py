# pyright: basic
"""`entity upsert --if-none-match` — create-only conditional upsert (#368).

Sends `If-None-Match: *` so a PATCH-upsert succeeds only when the record does
NOT already exist; the server returns a 412 precondition failure otherwise. The
create-only complement to `entity update --if-match`.
"""
from __future__ import annotations

import json
from typing import cast

from click.testing import CliRunner

from crm.cli import cli
from crm.core.entity import upsert, upsert_by_key
from crm.utils.d365_backend import D365Backend

GUID = "12345678-1234-1234-1234-123456789abc"


def _last_headers(backend):
    return backend.calls[-1][2].get("extra_headers")


def test_upsert_if_none_match_sets_header(make_fake_backend):
    backend = make_fake_backend()
    upsert(cast(D365Backend, backend), "accounts", GUID, {"name": "X"},
           if_none_match=True)
    assert _last_headers(backend)["If-None-Match"] == "*"


def test_upsert_without_if_none_match_sends_no_header(make_fake_backend):
    backend = make_fake_backend()
    upsert(cast(D365Backend, backend), "accounts", GUID, {"name": "X"})
    headers = _last_headers(backend) or {}
    assert "If-None-Match" not in headers


def test_upsert_by_key_if_none_match_sets_header(make_fake_backend):
    backend = make_fake_backend()
    upsert_by_key(cast(D365Backend, backend), "contacts",
                  {"emailaddress1": "a@b.com"}, {"emailaddress1": "a@b.com",
                                                 "firstname": "A"},
                  if_none_match=True)
    assert _last_headers(backend)["If-None-Match"] == "*"


def test_cli_upsert_if_none_match_threads_header(make_fake_backend, inject_backend):
    backend = inject_backend(make_fake_backend())
    result = CliRunner().invoke(cli, [
        "--json", "entity", "upsert", "accounts", GUID,
        "--data", json.dumps({"name": "X"}), "--if-none-match",
    ])
    assert result.exit_code == 0, result.output
    assert _last_headers(backend)["If-None-Match"] == "*"
