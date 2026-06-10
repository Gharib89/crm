# pyright: basic
"""Reject OData parameters baked into the entity-set arg client-side (#185).

`query odata` takes a bare entity-set *name* (e.g. `solutions`); OData options
go through `--select`/`--filter`/etc. Passing a full OData URL fragment like
`solutions?$select=uniquename` would hit the server and bounce back a bare
`HTTP 400` with `code: null` — no recovery signal for an agent. `odata_query`
catches a `?` or `$` in the entity-set arg and raises a status-less,
non-null-code `D365Error` (→ classified `validation`) BEFORE the network.
"""
from __future__ import annotations

import json
from typing import cast

import pytest
from click.testing import CliRunner

from crm.cli import CLIContext, cli
from crm.core.query import odata_query
from crm.utils.d365_backend import D365Backend, D365Error


class _NoNetworkBackend:
    """`.get` must never be reached — the raise happens before the request."""

    def get(self, *_args, **_kw):
        raise AssertionError("backend.get must not be called for a malformed entity set")


class _RecordingBackend:
    def __init__(self):
        self.called = False

    def get(self, *_args, **_kw):
        self.called = True
        return {"value": []}


_RAISING_SETS = [
    "solutions?$select=uniquename",
    "solutions?$filter=ismanaged eq false",
    "accounts?$top=5",
    "$metadata",
]


@pytest.mark.parametrize("entity_set", _RAISING_SETS)
def test_odata_param_in_entity_set_raises_before_network(entity_set):
    with pytest.raises(D365Error) as excinfo:
        odata_query(cast(D365Backend, _NoNetworkBackend()), entity_set)
    assert excinfo.value.status is None
    assert excinfo.value.code is not None
    assert "bare set name" in str(excinfo.value).lower()


def test_bare_entity_set_passes_through():
    backend = _RecordingBackend()
    result = odata_query(
        cast(D365Backend, backend), "solutions", select=["uniquename"]
    )
    assert backend.called, "a bare set name should reach backend.get"
    assert result == {"value": []}


def test_envelope_classifies_malformed_set_as_validation(monkeypatch):
    """Full CLI path: a malformed entity set surfaces as a validation error with a
    non-null code and a recovery hint, never reaching the (asserting) backend."""
    monkeypatch.setattr(CLIContext, "backend", lambda self: _NoNetworkBackend())
    result = CliRunner().invoke(
        cli, ["--json", "query", "odata", "solutions?$select=uniquename"]
    )
    assert result.exit_code == 1, result.output
    payload = json.loads(result.output)
    assert payload["ok"] is False
    meta = payload["meta"]
    assert meta["category"] == "validation"
    assert meta["code"] is not None
    assert meta["retryable"] is False
