# pyright: basic
"""`query odata --all/--max-records` follow @odata.nextLink across pages (#351).

Default behavior (no flag) is a single server page with `@odata.nextLink`
preserved in the envelope. `--all` follows the cursor to exhaustion and merges
every page's `value` into one array; `--max-records N` caps the total and stops
issuing page requests once N is reached. Both reuse the `backend.get(next_link)`
paging pattern already used by the export path.
"""
from __future__ import annotations

import json
from typing import Any, cast

from click.testing import CliRunner

from crm.cli import cli
from crm.core.query import odata_query
from crm.utils.d365_backend import D365Backend

NEXT = "https://crm.contoso.local/contoso/api/data/v9.2/accounts?$skiptoken=p2"
NEXT2 = "https://crm.contoso.local/contoso/api/data/v9.2/accounts?$skiptoken=p3"
CTX = "https://crm.contoso.local/contoso/api/data/v9.2/$metadata#accounts"


def _paged(*pages: dict[str, Any]):
    """Return a `responses['get']` callable that serves successive pages keyed by
    request path: the bare entity set yields page 0, each `@odata.nextLink` the
    next page. `pages[i]` is the raw envelope returned for that hop."""
    by_path = {"accounts": pages[0]}
    for i, link in enumerate((NEXT, NEXT2)[: len(pages) - 1], start=1):
        by_path[link] = pages[i]

    def _serve(path: Any) -> dict[str, Any]:
        return by_path[path]

    return _serve


def test_all_follows_next_link_and_merges(make_fake_backend):
    backend = make_fake_backend(responses={"get": _paged(
        {"@odata.context": CTX, "value": [{"accountid": "1"}], "@odata.nextLink": NEXT},
        {"@odata.context": CTX, "value": [{"accountid": "2"}], "@odata.nextLink": NEXT2},
        {"@odata.context": CTX, "value": [{"accountid": "3"}]},
    )})
    result = odata_query(cast(D365Backend, backend), "accounts", all_pages=True)
    assert [r["accountid"] for r in result["value"]] == ["1", "2", "3"]
    # Paging complete → no dangling cursor in the merged envelope.
    assert "@odata.nextLink" not in result
    assert backend.count("get") == 3


def test_max_records_caps_and_stops_early(make_fake_backend):
    backend = make_fake_backend(responses={"get": _paged(
        {"@odata.context": CTX, "value": [{"accountid": "1"}, {"accountid": "2"}],
         "@odata.nextLink": NEXT},
        {"@odata.context": CTX, "value": [{"accountid": "3"}, {"accountid": "4"}],
         "@odata.nextLink": NEXT2},
        {"@odata.context": CTX, "value": [{"accountid": "5"}]},
    )})
    result = odata_query(cast(D365Backend, backend), "accounts", max_records=3)
    assert [r["accountid"] for r in result["value"]] == ["1", "2", "3"]
    # Stopped after the second page (4 rows ≥ 3) — third page never requested.
    assert backend.count("get") == 2
    # More rows existed than returned → cap-hit marker set.
    assert result.get("@crm.truncated") is True


def test_max_records_not_truncated_when_fewer_rows_exist(make_fake_backend):
    backend = make_fake_backend(responses={"get": _paged(
        {"@odata.context": CTX, "value": [{"accountid": "1"}, {"accountid": "2"}]},
    )})
    result = odata_query(cast(D365Backend, backend), "accounts", max_records=5)
    assert [r["accountid"] for r in result["value"]] == ["1", "2"]
    # Cap never bit — no truncation marker.
    assert "@crm.truncated" not in result


def test_all_with_max_records_is_bounded_by_max(make_fake_backend):
    backend = make_fake_backend(responses={"get": _paged(
        {"@odata.context": CTX, "value": [{"accountid": "1"}], "@odata.nextLink": NEXT},
        {"@odata.context": CTX, "value": [{"accountid": "2"}], "@odata.nextLink": NEXT2},
        {"@odata.context": CTX, "value": [{"accountid": "3"}]},
    )})
    result = odata_query(cast(D365Backend, backend), "accounts", all_pages=True, max_records=2)
    assert [r["accountid"] for r in result["value"]] == ["1", "2"]


def test_default_is_single_page_unchanged(make_fake_backend):
    page0 = {"@odata.context": CTX, "value": [{"accountid": "1"}], "@odata.nextLink": NEXT}
    backend = make_fake_backend(responses={"get": _paged(page0)})
    result = odata_query(cast(D365Backend, backend), "accounts")
    # Byte-identical to the raw first page; cursor preserved, no extra requests.
    assert result == page0
    assert backend.count("get") == 1


def test_cli_all_merges_and_drops_next_link(make_fake_backend, inject_backend):
    inject_backend(make_fake_backend(responses={"get": _paged(
        {"@odata.context": CTX, "value": [{"accountid": "1"}], "@odata.nextLink": NEXT},
        {"@odata.context": CTX, "value": [{"accountid": "2"}]},
    )}))
    result = CliRunner().invoke(cli, ["--json", "query", "odata", "accounts", "--all"])
    assert result.exit_code == 0, result.output
    env = json.loads(result.output)
    assert [r["accountid"] for r in env["data"]] == ["1", "2"]
    assert "next_link" not in env["meta"]
    assert "truncated" not in env["meta"]


def test_cli_max_records_reports_truncated_in_meta(make_fake_backend, inject_backend):
    inject_backend(make_fake_backend(responses={"get": _paged(
        {"@odata.context": CTX, "value": [{"accountid": "1"}, {"accountid": "2"}],
         "@odata.nextLink": NEXT},
        {"@odata.context": CTX, "value": [{"accountid": "3"}]},
    )}))
    result = CliRunner().invoke(
        cli, ["--json", "query", "odata", "accounts", "--max-records", "1"])
    assert result.exit_code == 0, result.output
    env = json.loads(result.output)
    assert [r["accountid"] for r in env["data"]] == ["1"]
    assert env["meta"]["truncated"] is True
    # Internal marker must never leak into the data payload.
    assert all("@crm" not in k for k in env["data"][0])
