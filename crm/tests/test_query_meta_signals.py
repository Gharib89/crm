# pyright: basic
"""A capped single server page is self-describing in `meta` (#626, folds #625).

A default (non-`--all`) `query` read returns one server page. When the server
supplied an `@odata.nextLink` cursor, more rows genuinely exist — so the envelope
now carries an explicit `meta.has_more: true` plus a truncation warning, and a
clamped `--count` (the value equals the 5000 server ceiling *and* a cursor is
present) is flagged as a lower bound. A read that fits one page, and any
`--all`/`--max-records` run, get none of these.
"""
from __future__ import annotations

import json

from click.testing import CliRunner

from crm.cli import cli

CTX = "https://crm.contoso.local/contoso/api/data/v9.2/$metadata#accounts"
NEXT = "https://crm.contoso.local/contoso/api/data/v9.2/accounts?$skiptoken=p2"


def _run(make_fake_backend, inject_backend, envelope, *args):
    inject_backend(make_fake_backend(responses={"get": envelope}))
    result = CliRunner().invoke(cli, ["--json", "query", "odata", "accounts", *args])
    assert result.exit_code == 0, result.output
    return json.loads(result.output)


def test_single_page_with_more_sets_has_more_and_warns(make_fake_backend, inject_backend):
    env = _run(make_fake_backend, inject_backend,
               {"@odata.context": CTX, "value": [{"accountid": "1"}], "@odata.nextLink": NEXT})
    assert env["meta"]["has_more"] is True
    assert any("more rows exist" in w for w in env["meta"]["warnings"])


def test_complete_single_page_has_no_signals(make_fake_backend, inject_backend):
    # No @odata.nextLink → the page is the whole result set; stays silent.
    env = _run(make_fake_backend, inject_backend,
               {"@odata.context": CTX, "value": [{"accountid": "1"}]})
    assert "has_more" not in env["meta"]
    assert "warnings" not in env["meta"]


def test_clamped_count_is_flagged_as_lower_bound(make_fake_backend, inject_backend):
    # Count == 5000 ceiling *and* a cursor present → the count is clamped.
    env = _run(make_fake_backend, inject_backend,
               {"@odata.context": CTX, "@odata.count": 5000,
                "value": [{"accountid": "1"}], "@odata.nextLink": NEXT},
               "--count")
    assert env["meta"]["count"] == 5000
    assert env["meta"]["has_more"] is True
    assert any("clamped at 5000" in w for w in env["meta"]["warnings"])


def test_honest_small_count_is_not_flagged(make_fake_backend, inject_backend):
    # Genuine count on a set that fits one page → count unchanged, no warnings.
    env = _run(make_fake_backend, inject_backend,
               {"@odata.context": CTX, "@odata.count": 42, "value": [{"accountid": "1"}]},
               "--count")
    assert env["meta"]["count"] == 42
    assert "has_more" not in env["meta"]
    assert "warnings" not in env["meta"]


def test_count_below_ceiling_with_more_pages_warns_truncation_only(
        make_fake_backend, inject_backend):
    # A small page size can leave a cursor while the count is honest (< ceiling):
    # signal truncation, but never a false clamp warning.
    env = _run(make_fake_backend, inject_backend,
               {"@odata.context": CTX, "@odata.count": 42,
                "value": [{"accountid": "1"}], "@odata.nextLink": NEXT},
               "--count")
    assert env["meta"]["count"] == 42
    assert env["meta"]["has_more"] is True
    assert any("more rows exist" in w for w in env["meta"]["warnings"])
    assert not any("clamped" in w for w in env["meta"]["warnings"])


def test_all_run_has_no_has_more_or_warnings(make_fake_backend, inject_backend):
    # --all follows and drops the cursor (sets its own meta.truncated) → silent here.
    from crm.tests.test_query_paging import _paged
    inject_backend(make_fake_backend(responses={"get": _paged(
        {"@odata.context": CTX, "value": [{"accountid": "1"}], "@odata.nextLink": NEXT},
        {"@odata.context": CTX, "value": [{"accountid": "2"}]},
    )}))
    result = CliRunner().invoke(cli, ["--json", "query", "odata", "accounts", "--all"])
    assert result.exit_code == 0, result.output
    env = json.loads(result.output)
    assert "has_more" not in env["meta"]
    assert "warnings" not in env["meta"]


def test_human_mode_surfaces_warning_via_skin(make_fake_backend, inject_backend):
    # skin.warning writes to stderr (Click 8.2+ separates streams).
    inject_backend(make_fake_backend(responses={"get":
        {"@odata.context": CTX, "value": [{"accountid": "1"}], "@odata.nextLink": NEXT}}))
    result = CliRunner().invoke(cli, ["query", "odata", "accounts"])
    assert result.exit_code == 0, result.output
    assert "more rows exist" in result.stderr
