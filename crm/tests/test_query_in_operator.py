# pyright: basic
"""Detect the unsupported OData 4.01 `in` operator client-side and redirect.

The bare `in` keyword is OData 4.01; the Dataverse Web API is OData 4.0 and
rejects it with a generic 500. `odata_query` raises a status-less `D365Error`
(→ classified `validation`, not retryable) BEFORE touching the network, while
leaving the native `Microsoft.Dynamics.CRM.In(...)` function and any quoted
literal / column name containing the substring `in` untouched.
"""
from __future__ import annotations

import json
from typing import cast

import pytest
from click.testing import CliRunner

from crm.cli import cli
from crm.core.query import odata_query
from crm.utils.d365_backend import D365Backend, D365Error


_RAISING_FILTERS = [
    "workflowid in ('a','b')",
    "workflowid in ['a','b']",
    "statecode  in  (0,1)",
]

_PASSTHROUGH_FILTERS = [
    "name eq 'stand in (queue)'",  # ` in (` inside a quoted literal
    "Microsoft.Dynamics.CRM.In(PropertyName='workflowid',PropertyValues=['a','b'])",  # native fn
    "createdon gt 2020-01-01",  # ordinary column, no bare `in` operator
    "_in_value eq 5",  # column name contains the substring 'in'
    "min_value eq 3",  # ditto
    "contains(name,'foo')",  # OData contains function
]


@pytest.mark.parametrize("filter_", _RAISING_FILTERS)
def test_bare_in_operator_raises_before_network(filter_, make_fake_backend):
    with pytest.raises(D365Error) as excinfo:
        odata_query(cast(D365Backend, make_fake_backend(forbid=("get",))), "workflows", filter_=filter_)
    assert "in" in str(excinfo.value).lower()
    # Status-less → classified validation / not retryable.
    assert excinfo.value.status is None


@pytest.mark.parametrize("filter_", _PASSTHROUGH_FILTERS)
def test_legitimate_filters_pass_through(filter_, make_fake_backend):
    backend = make_fake_backend()
    result = odata_query(cast(D365Backend, backend), "workflows", filter_=filter_)
    assert backend.called, "legitimate filter should reach backend.get"
    assert result == {"value": []}


def test_envelope_classifies_in_operator_as_validation(make_fake_backend, inject_backend):
    """Full CLI path: `in` operator surfaces as a non-retryable validation error
    and never reaches the (asserting) backend."""
    inject_backend(make_fake_backend(forbid=("get",)))
    result = CliRunner().invoke(
        cli,
        ["--json", "query", "odata", "workflows", "--filter", "workflowid in ('a','b')"],
    )
    assert result.exit_code == 1, result.output
    meta = json.loads(result.output)["meta"]
    assert meta["category"] == "validation"
    assert meta["retryable"] is False
