# pyright: basic
"""`query odata --track-changes` / `--delta-token` change-tracking delta queries (#364).

`--track-changes` emits `Prefer: odata.track-changes` so the server returns an
`@odata.deltaLink`; the envelope surfaces it as `meta.delta_link` plus the bare
`meta.delta_token` extracted from it. `--delta-token <tok>` resumes by sending
`$deltatoken=<tok>`, returning only rows changed since (deletes arrive as rows
carrying a `$deletedEntity` context, stripped to `{id, reason}`). The Dataverse
Web API rejects `$filter/$orderby/$expand/$top` with change tracking, so those
combinations (and `--max-records`) are rejected client-side. `--all` is allowed:
the platform sends `@odata.deltaLink` on the last page only, so the page-following
merge carries it from there (#1289).
"""

from __future__ import annotations

import json
from typing import cast

import pytest
from click.testing import CliRunner

from crm.cli import cli
from crm.core.query import odata_query
from crm.utils.d365_backend import D365Backend, D365Error

CTX = "https://crm.contoso.local/contoso/api/data/v9.2/$metadata#accounts"
DELTA = (
    "https://crm.contoso.local/contoso/api/data/v9.2/accounts"
    "?$select=name&$deltatoken=919042%2108%2f22%2f2017%2008%3a10%3a44"
)
TOKEN = "919042!08/22/2017 08:10:44"


def _last_kwargs(backend):
    """The kwargs dict (params, extra_headers) of the backend's last get()."""
    return backend.calls[-1][2]


def test_track_changes_sends_prefer_header(make_fake_backend):
    backend = make_fake_backend(responses={"get": {"@odata.context": CTX, "value": []}})
    odata_query(cast(D365Backend, backend), "accounts", track_changes=True)
    assert _last_kwargs(backend)["extra_headers"]["Prefer"] == "odata.track-changes"


def test_delta_token_sends_deltatoken_param(make_fake_backend):
    backend = make_fake_backend(responses={"get": {"@odata.context": CTX, "value": []}})
    odata_query(cast(D365Backend, backend), "accounts", delta_token=TOKEN)
    assert _last_kwargs(backend)["params"]["$deltatoken"] == TOKEN
    # Resume sends no track-changes Prefer (the token alone identifies the round).
    assert _last_kwargs(backend)["extra_headers"] is None


@pytest.mark.parametrize(
    "kwargs",
    [
        {"filter_": "name eq 'x'"},
        {"orderby": "name"},
        {"expand": ["primarycontactid"]},
        {"top": 5},
        {"max_records": 10},
    ],
)
def test_track_changes_rejects_unsupported_options(make_fake_backend, kwargs):
    backend = make_fake_backend()
    with pytest.raises(D365Error):
        odata_query(cast(D365Backend, backend), "accounts", track_changes=True, **kwargs)
    # Rejected client-side — never reaches the server.
    assert not backend.called


def test_delta_token_rejects_unsupported_options(make_fake_backend):
    backend = make_fake_backend()
    with pytest.raises(D365Error):
        odata_query(
            cast(D365Backend, backend), "accounts", delta_token=TOKEN, filter_="name eq 'x'"
        )
    assert not backend.called


def test_track_changes_and_delta_token_are_mutually_exclusive(make_fake_backend):
    backend = make_fake_backend()
    with pytest.raises(D365Error):
        odata_query(cast(D365Backend, backend), "accounts", track_changes=True, delta_token=TOKEN)
    assert not backend.called


def test_cli_track_changes_surfaces_delta_link_and_token(make_fake_backend, inject_backend):
    inject_backend(
        make_fake_backend(
            responses={
                "get": {
                    "@odata.context": CTX,
                    "@odata.deltaLink": DELTA,
                    "value": [{"accountid": "1", "name": "Monte"}],
                }
            }
        )
    )
    result = CliRunner().invoke(
        cli, ["--json", "query", "odata", "accounts", "--track-changes", "--select", "name"]
    )
    assert result.exit_code == 0, result.output
    env = json.loads(result.output)
    assert [r["accountid"] for r in env["data"]] == ["1"]
    # Full link surfaced for opaque round-tripping; bare token decoded for --delta-token.
    assert env["meta"]["delta_link"] == DELTA
    assert env["meta"]["delta_token"] == TOKEN
    # Protocol key never leaks into the data payload.
    assert all("@odata" not in k for r in env["data"] for k in r)


def test_cli_delta_token_passes_through_deletes(make_fake_backend, inject_backend):
    backend = inject_backend(
        make_fake_backend(
            responses={
                "get": {
                    "@odata.context": CTX + "/$delta",
                    "@odata.deltaLink": DELTA,
                    "value": [
                        {"accountid": "1", "name": "Monte"},
                        {"@odata.context": CTX + "/$deletedEntity", "id": "2", "reason": "deleted"},
                    ],
                }
            }
        )
    )
    result = CliRunner().invoke(
        cli, ["--json", "query", "odata", "accounts", "--delta-token", TOKEN]
    )
    assert result.exit_code == 0, result.output
    env = json.loads(result.output)
    # The resume sent the token as $deltatoken.
    assert backend.calls[-1][2]["params"]["$deltatoken"] == TOKEN
    # The deleted row survives as a clean {id, reason} pair (its @odata.context stripped).
    deleted = [r for r in env["data"] if r.get("reason") == "deleted"]
    assert deleted == [{"id": "2", "reason": "deleted"}]
    # Each round surfaces the next delta link to chain from.
    assert env["meta"]["delta_token"] == TOKEN


NEXT = "https://crm.contoso.local/contoso/api/data/v9.2/accounts?$skiptoken=p2"
PAGE_LINK = DELTA.replace("919042%2108", "777%2108")


def _two_pages(path):
    """Page 1 holds the cursor only; the delta link rides on the last page."""
    if path == "accounts":
        return {"@odata.context": CTX, "@odata.nextLink": NEXT, "value": [{"accountid": "1"}]}
    return {"@odata.context": CTX, "@odata.deltaLink": PAGE_LINK, "value": [{"accountid": "2"}]}


@pytest.mark.parametrize("kwargs", [{"track_changes": True}, {"delta_token": TOKEN}])
def test_all_follows_pages_and_keeps_last_pages_delta_link(make_fake_backend, kwargs):
    backend = make_fake_backend(responses={"get": _two_pages})
    result = odata_query(cast(D365Backend, backend), "accounts", all_pages=True, **kwargs)
    assert [r["accountid"] for r in result["value"]] == ["1", "2"]
    assert result["@odata.deltaLink"] == PAGE_LINK
    assert "@odata.nextLink" not in result
    assert backend.last_path == NEXT


def test_track_changes_all_repeats_the_prefer_header_on_later_pages(make_fake_backend):
    backend = make_fake_backend(responses={"get": _two_pages})
    odata_query(cast(D365Backend, backend), "accounts", all_pages=True, track_changes=True)
    assert _last_kwargs(backend)["extra_headers"]["Prefer"] == "odata.track-changes"


def test_cli_track_changes_all_returns_every_row_and_the_last_pages_token(
    make_fake_backend, inject_backend
):
    inject_backend(make_fake_backend(responses={"get": _two_pages}))
    result = CliRunner().invoke(
        cli, ["--json", "query", "odata", "accounts", "--track-changes", "--all"]
    )
    assert result.exit_code == 0, result.output
    env = json.loads(result.output)
    assert [r["accountid"] for r in env["data"]] == ["1", "2"]
    assert env["meta"]["delta_token"] == "777!08/22/2017 08:10:44"
    assert "has_more" not in env["meta"]


def test_cli_track_changes_max_records_is_refused_with_the_reason(
    make_fake_backend, inject_backend
):
    backend = inject_backend(make_fake_backend())
    result = CliRunner().invoke(
        cli, ["--json", "query", "odata", "accounts", "--track-changes", "--max-records", "1"]
    )
    assert result.exit_code == 1, result.output
    assert "last page" in json.loads(result.output)["error"]
    assert not backend.called


@pytest.mark.parametrize("flag", [["--track-changes"], ["--delta-token", TOKEN]])
def test_cli_tracked_page_without_all_warns_the_token_arrives_on_the_last_page(
    make_fake_backend, inject_backend, flag
):
    inject_backend(make_fake_backend(responses={"get": _two_pages}))
    result = CliRunner().invoke(cli, ["--json", "query", "odata", "accounts", *flag])
    assert result.exit_code == 0, result.output
    env = json.loads(result.output)
    assert env["meta"]["has_more"] is True
    assert "delta_token" not in env["meta"]
    (warning,) = env["meta"]["warnings"]
    assert "last page" in warning and "--all" in warning
