# pyright: basic
"""Validates the entity-set positional contract for `query odata` (#185, #237).

Accepted forms for the positional arg:
  - bare entity-set name:  `solutions`, `contacts`
  - bound-function path:   `RetrieveAppComponents(AppModuleId=<guid>)`
  - metadata path:         `EntityDefinitions(LogicalName='account')/Keys`

OData query options must go through `--select`/`--filter`/etc., never inline.
A `?` or `$` in the arg (e.g. `solutions?$select=uniquename`) is rejected
client-side — the server would return a bare HTTP 400 with no recovery signal.
"""
from __future__ import annotations

import json
from typing import cast

import pytest
from click.testing import CliRunner

from crm.cli import cli
from crm.core.query import odata_query
from crm.utils.d365_backend import D365Backend, D365Error


_RAISING_SETS = [
    "solutions?$select=uniquename",
    "solutions?$filter=ismanaged eq false",
    "accounts?$top=5",
    "$metadata",
]


@pytest.mark.parametrize("entity_set", _RAISING_SETS)
def test_odata_param_in_entity_set_raises_before_network(entity_set, make_fake_backend):
    with pytest.raises(D365Error) as excinfo:
        odata_query(cast(D365Backend, make_fake_backend(forbid=("get",))), entity_set)
    assert excinfo.value.status is None
    assert excinfo.value.code is not None
    assert "bare" in str(excinfo.value).lower()


def test_bare_entity_set_passes_through(make_fake_backend):
    backend = make_fake_backend()
    result = odata_query(
        cast(D365Backend, backend), "solutions", select=["uniquename"]
    )
    assert backend.called, "a bare entity set should reach backend.get"
    assert backend.last_path == "solutions"
    assert result == {"value": []}


def test_envelope_classifies_malformed_set_as_validation(make_fake_backend, inject_backend):
    """Full CLI path: a malformed entity set surfaces as a validation error with a
    non-null code and a recovery hint, never reaching the (asserting) backend."""
    inject_backend(make_fake_backend(forbid=("get",)))
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


# ── Path-shaped args pass through verbatim (#237) ───────────────────────


_PATH_SHAPED = [
    "solutions",                                                                   # bare entity set
    "EntityDefinitions(LogicalName='account')/Keys",                               # metadata path
    "RetrieveAppComponents(AppModuleId=00000000-0000-0000-0000-000000000001)",     # bound-function
]


@pytest.mark.parametrize("entity_set", _PATH_SHAPED)
def test_path_shaped_arg_passes_through_verbatim(entity_set, make_fake_backend):
    """All three accepted forms (bare entity set, metadata path, bound-function) pass through verbatim."""
    backend = make_fake_backend()
    odata_query(cast(D365Backend, backend), entity_set)
    assert backend.called, "path-shaped arg must reach backend.get"
    assert backend.last_path == entity_set, (
        f"path must be forwarded verbatim; got {backend.last_path!r}"
    )


def test_cli_metadata_path_passes_through(make_fake_backend, inject_backend):
    """CLI-level: metadata path reaches backend without validation error."""
    b = inject_backend(make_fake_backend())
    result = CliRunner().invoke(
        cli,
        ["--json", "query", "odata", "EntityDefinitions(LogicalName='account')/Keys"],
    )
    assert result.exit_code == 0, result.output
    assert b.called
    assert b.last_path == "EntityDefinitions(LogicalName='account')/Keys"


def test_rejection_error_hints_flags(make_fake_backend):
    """The error message for ?/$ tells the user to use the flags."""
    with pytest.raises(D365Error) as excinfo:
        odata_query(
            cast(D365Backend, make_fake_backend(forbid=("get",))),
            "solutions?$select=uniquename",
        )
    msg = str(excinfo.value).lower()
    assert "--select" in msg and "--filter" in msg
