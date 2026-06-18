# pyright: basic
"""`query odata --apply` — OData `$apply` aggregation/groupby passthrough (#368).

The path validator rejects an inline `$`, so `$apply` aggregation can only reach
the server through this dedicated flag, which threads the expression into the
OData query as `$apply=<expr>`.
"""
from __future__ import annotations

from typing import cast

from crm.cli import cli
from crm.core.query import odata_query
from crm.utils.d365_backend import D365Backend

from click.testing import CliRunner

CTX = "https://crm.contoso.local/contoso/api/data/v9.2/$metadata#accounts"
APPLY = "groupby((industrycode),aggregate(revenue with sum as total))"


def _last_kwargs(backend):
    return backend.calls[-1][2]


def test_apply_threads_into_query_params(make_fake_backend):
    backend = make_fake_backend(responses={"get": {"@odata.context": CTX, "value": []}})
    odata_query(cast(D365Backend, backend), "accounts", apply=APPLY)
    assert _last_kwargs(backend)["params"]["$apply"] == APPLY


def test_apply_flag_reaches_core(make_fake_backend, inject_backend):
    backend = inject_backend(
        make_fake_backend(responses={"get": {"@odata.context": CTX, "value": []}})
    )
    result = CliRunner().invoke(
        cli, ["--json", "query", "odata", "accounts", "--apply", APPLY]
    )
    assert result.exit_code == 0, result.output
    assert _last_kwargs(backend)["params"]["$apply"] == APPLY
