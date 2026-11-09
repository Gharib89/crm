"""Unit tests for crm.core.solution.delete_and_promote's lost-response handling (#1178).

A large DeleteAndPromote outlives the read timeout while the server keeps running
it, and a POST is never re-sent after one. After a read timeout the verb waits
until a solution row with the base's unique name carries a new solutionid (the
holding solution's name is replaced by the base's, so the row's existence is not
the signal). All HTTP is mocked via requests_mock; no live D365 server.
"""

# pyright: basic
from __future__ import annotations

import json
import time

import pytest
import requests
import requests_mock
from click.testing import CliRunner

from crm.cli import cli
from crm.core import solution as sol_mod
from crm.utils.d365_backend import D365Error

_OLD_ID = "11111111-1111-1111-1111-111111111111"
_NEW_ID = "22222222-2222-2222-2222-222222222222"
_TIMEOUT = requests.exceptions.ReadTimeout


@pytest.fixture(autouse=True)
def _no_sleep(monkeypatch):
    monkeypatch.setattr(time, "sleep", lambda s: None)


def _rows(*ids):
    """One GET answer per id; an id of None is an empty page (no row for the name)."""
    return [
        {"json": {"value": [] if i is None else [{"solutionid": i, "uniquename": "Pilot"}]}}
        for i in ids
    ]


def _posts(m):
    return [r for r in m.request_history if r.method == "POST"]


def _promote_mock(backend, m, *gets, post=None):
    m.get(backend.url_for("solutions"), list(gets))
    m.post(backend.url_for("DeleteAndPromote"), **(post or {"exc": _TIMEOUT}))


class TestLostResponse:
    def test_read_timeout_then_new_id_returns_envelope_with_one_post(self, backend):
        with requests_mock.Mocker() as m:
            # base read before the POST, then two polls: still the old id, then the new one
            _promote_mock(backend, m, *_rows(_OLD_ID, _OLD_ID, _NEW_ID))
            out = sol_mod.delete_and_promote(backend, "Pilot")
        assert out == {"promoted": True, "solution": "Pilot", "solutionid": _NEW_ID}
        assert len(_posts(m)) == 1

    def test_zero_rows_for_the_name_keeps_waiting(self, backend):
        with requests_mock.Mocker() as m:
            _promote_mock(backend, m, *_rows(_OLD_ID, None, None, _NEW_ID))
            out = sol_mod.delete_and_promote(backend, "Pilot")
        assert out["solutionid"] == _NEW_ID
        assert len(_posts(m)) == 1

    def test_old_id_at_the_deadline_raises_may_still_be_running(self, backend):
        with requests_mock.Mocker() as m:
            _promote_mock(backend, m, *_rows(_OLD_ID))
            with pytest.raises(D365Error, match="may still be running") as exc_info:
                sol_mod.delete_and_promote(backend, "Pilot", timeout=0)
        assert exc_info.value.code == "PromoteStillRunning"
        assert len(_posts(m)) == 1

    def test_negative_timeout_is_rejected_before_any_request(self, backend):
        with requests_mock.Mocker() as m:
            with pytest.raises(D365Error, match="timeout"):
                sol_mod.delete_and_promote(backend, "Pilot", timeout=-1)
        assert m.request_history == []

    def test_missing_base_solution_fails_fast_without_a_post(self, backend):
        with requests_mock.Mocker() as m:
            m.get(backend.url_for("solutions"), json={"value": []})
            with pytest.raises(D365Error, match="Solution not found"):
                sol_mod.delete_and_promote(backend, "Pilot")
        assert _posts(m) == []

    def test_a_normal_response_sends_no_extra_post_and_no_poll(self, backend):
        with requests_mock.Mocker() as m:
            _promote_mock(backend, m, *_rows(_OLD_ID), post={"json": {"SolutionId": _NEW_ID}})
            out = sol_mod.delete_and_promote(backend, "Pilot")
        assert out == {"promoted": True, "solution": "Pilot", "solutionid": _NEW_ID}
        assert len(_posts(m)) == 1
        assert len([r for r in m.request_history if r.method == "GET"]) == 1

    def test_dry_run_reads_the_base_but_sends_no_post(self, dry_backend):
        with requests_mock.Mocker() as m:
            m.get(dry_backend.url_for("solutions"), **_rows(_OLD_ID)[0])
            out = sol_mod.delete_and_promote(dry_backend, "Pilot")
        assert out["_dry_run"] is True
        assert _posts(m) == []


class TestTimeoutWiring:
    def test_apply_upgrade_forwards_timeout(self, monkeypatch):
        seen = {}
        monkeypatch.setattr(
            "crm.core.solution.delete_and_promote",
            lambda backend, name, **kw: seen.update(kw) or {"promoted": True, "solution": name},
        )
        monkeypatch.setattr("crm.cli.CLIContext.backend", lambda self: object())
        result = CliRunner().invoke(
            cli, ["--json", "solution", "apply-upgrade", "Pilot", "--timeout", "45", "--yes"]
        )
        assert result.exit_code == 0, result.output
        assert seen == {"timeout": 45}

    def test_apply_upgrade_rejects_a_negative_timeout(self, monkeypatch):
        monkeypatch.setattr("crm.cli.CLIContext.backend", lambda self: object())
        result = CliRunner().invoke(
            cli, ["--json", "solution", "apply-upgrade", "Pilot", "--timeout", "-1", "--yes"]
        )
        assert result.exit_code == 2, result.output

    def test_stage_and_upgrade_forwards_timeout_to_the_promote_wait(self, monkeypatch, tmp_path):
        zip_path = tmp_path / "up.zip"
        zip_path.write_bytes(b"PK\x03\x04stub")
        seen = {}
        monkeypatch.setattr(
            "crm.core.solution_transfer.import_solution",
            lambda backend, path, **kw: (
                seen.update(import_timeout=kw["timeout"]) or {"status": "succeeded"}
            ),
        )
        monkeypatch.setattr(
            "crm.core.solution.delete_and_promote",
            lambda backend, name, **kw: (
                seen.update(promote_timeout=kw["timeout"]) or {"promoted": True, "solution": name}
            ),
        )
        monkeypatch.setattr("crm.cli.CLIContext.backend", lambda self: object())
        result = CliRunner().invoke(
            cli,
            [
                "--json",
                "solution",
                "stage-and-upgrade",
                str(zip_path),
                "--promote",
                "--solution",
                "Pilot",
                "--timeout",
                "77",
                "--yes",
            ],
        )
        assert result.exit_code == 0, result.output
        assert json.loads(result.output)["data"]["promote"]["promoted"] is True
        assert seen == {"import_timeout": 77, "promote_timeout": 77}
