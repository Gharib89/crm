# pyright: basic
"""`query odata --track-changes` / `--delta-token` change-tracking delta queries (#364).

`--track-changes` emits `Prefer: odata.track-changes` so the server returns an
`@odata.deltaLink`; the envelope surfaces it as `meta.delta_link` plus the bare
`meta.delta_token` extracted from it. `--delta-token <tok>` resumes by sending
`$deltatoken=<tok>`, returning only rows changed since (deletes arrive as rows
carrying a `$deletedEntity` context, stripped to `{id, reason}`). The Dataverse
Web API rejects `$filter/$orderby/$expand/$top` with change tracking, so those
combinations (and page-following) are rejected client-side.
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
DELTA = ("https://crm.contoso.local/contoso/api/data/v9.2/accounts"
         "?$select=name&$deltatoken=919042%2108%2f22%2f2017%2008%3a10%3a44")
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


@pytest.mark.parametrize("kwargs", [
    {"filter_": "name eq 'x'"},
    {"orderby": "name"},
    {"expand": ["primarycontactid"]},
    {"top": 5},
    {"all_pages": True},
    {"max_records": 10},
])
def test_track_changes_rejects_unsupported_options(make_fake_backend, kwargs):
    backend = make_fake_backend()
    with pytest.raises(D365Error):
        odata_query(cast(D365Backend, backend), "accounts", track_changes=True, **kwargs)
    # Rejected client-side — never reaches the server.
    assert not backend.called


def test_delta_token_rejects_unsupported_options(make_fake_backend):
    backend = make_fake_backend()
    with pytest.raises(D365Error):
        odata_query(cast(D365Backend, backend), "accounts",
                    delta_token=TOKEN, filter_="name eq 'x'")
    assert not backend.called


def test_track_changes_and_delta_token_are_mutually_exclusive(make_fake_backend):
    backend = make_fake_backend()
    with pytest.raises(D365Error):
        odata_query(cast(D365Backend, backend), "accounts",
                    track_changes=True, delta_token=TOKEN)
    assert not backend.called


def test_cli_track_changes_surfaces_delta_link_and_token(make_fake_backend, inject_backend):
    inject_backend(make_fake_backend(responses={"get": {
        "@odata.context": CTX,
        "@odata.deltaLink": DELTA,
        "value": [{"accountid": "1", "name": "Monte"}],
    }}))
    result = CliRunner().invoke(
        cli, ["--json", "query", "odata", "accounts", "--track-changes", "--select", "name"])
    assert result.exit_code == 0, result.output
    env = json.loads(result.output)
    assert [r["accountid"] for r in env["data"]] == ["1"]
    # Full link surfaced for opaque round-tripping; bare token decoded for --delta-token.
    assert env["meta"]["delta_link"] == DELTA
    assert env["meta"]["delta_token"] == TOKEN
    # Protocol key never leaks into the data payload.
    assert all("@odata" not in k for r in env["data"] for k in r)


def test_cli_delta_token_passes_through_deletes(make_fake_backend, inject_backend):
    backend = inject_backend(make_fake_backend(responses={"get": {
        "@odata.context": CTX + "/$delta",
        "@odata.deltaLink": DELTA,
        "value": [
            {"accountid": "1", "name": "Monte"},
            {"@odata.context": CTX + "/$deletedEntity",
             "id": "2", "reason": "deleted"},
        ],
    }}))
    result = CliRunner().invoke(
        cli, ["--json", "query", "odata", "accounts", "--delta-token", TOKEN])
    assert result.exit_code == 0, result.output
    env = json.loads(result.output)
    # The resume sent the token as $deltatoken.
    assert backend.calls[-1][2]["params"]["$deltatoken"] == TOKEN
    # The deleted row survives as a clean {id, reason} pair (its @odata.context stripped).
    deleted = [r for r in env["data"] if r.get("reason") == "deleted"]
    assert deleted == [{"id": "2", "reason": "deleted"}]
    # Each round surfaces the next delta link to chain from.
    assert env["meta"]["delta_token"] == TOKEN
