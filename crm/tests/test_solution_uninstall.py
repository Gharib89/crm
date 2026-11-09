"""Unit tests for crm.core.solution.uninstall_solution's lost-response handling (#1172).

A long uninstall outlives the read timeout while the server keeps running it, so
re-sending the DELETE collides with the CLI's own operation. After a read timeout
the DELETE is not re-sent: the verb polls solutions(<id>) until it is gone. All
HTTP is mocked via requests_mock; no live D365 server.
"""

# pyright: basic
from __future__ import annotations

import time

import pytest
import requests
import requests_mock

from crm.core import solution as sol_mod
from crm.utils.d365_backend import D365Error

_SOL_ID = "33333333-3333-3333-3333-333333333333"
_NOT_FOUND = {"status_code": 404, "json": {"error": {"code": "0x80040217", "message": "gone"}}}
_LOCK = {
    "status_code": 429,
    "json": {"error": {"code": "0x80071151", "message": "another [Uninstall] is running"}},
}


@pytest.fixture(autouse=True)
def _no_sleep(monkeypatch):
    monkeypatch.setattr(time, "sleep", lambda s: None)


def _deletes(m):
    return [r for r in m.request_history if r.method == "DELETE"]


def _mock_org(backend, m, *, history=None):
    """Mock the solution lookup and, where given, the history rows (a list per GET)."""
    m.get(
        backend.url_for("solutions"),
        json={"value": [{"solutionid": _SOL_ID, "uniquename": "Pilot", "ismanaged": True}]},
    )
    if history is None:
        m.get(backend.url_for("msdyn_solutionhistories"), **_NOT_FOUND)
    else:
        m.get(
            backend.url_for("msdyn_solutionhistories"),
            [{"json": {"value": rows}} for rows in history],
        )


def _uninstall(backend, **kw):
    return sol_mod.uninstall_solution(backend, "Pilot", force=True, **kw)


class TestLostResponse:
    def test_read_timeout_then_gone_succeeds_with_one_delete(self, backend):
        with requests_mock.Mocker() as m:
            _mock_org(backend, m)
            m.delete(backend.url_for(f"solutions({_SOL_ID})"), exc=requests.exceptions.ReadTimeout)
            m.get(backend.url_for(f"solutions({_SOL_ID})"), [{"json": {}}, _NOT_FOUND])
            out = _uninstall(backend)
        assert out == {"uninstalled": True, "solution": "Pilot", "solutionid": _SOL_ID}
        assert len(_deletes(m)) == 1

    def test_still_present_at_timeout_raises_may_still_be_running(self, backend):
        with requests_mock.Mocker() as m:
            _mock_org(backend, m)
            m.delete(backend.url_for(f"solutions({_SOL_ID})"), exc=requests.exceptions.ReadTimeout)
            m.get(backend.url_for(f"solutions({_SOL_ID})"), json={"solutionid": _SOL_ID})
            with pytest.raises(D365Error, match="may still be running") as exc_info:
                _uninstall(backend, timeout=0)
        assert exc_info.value.code == "UninstallStillRunning"
        assert len(_deletes(m)) == 1

    def test_failed_history_row_ends_the_wait_with_its_message(self, backend):
        failed = {
            "msdyn_solutionhistoryid": "row-1",
            "msdyn_exceptionmessage": "Solution uninstall failed: component in use",
        }
        with requests_mock.Mocker() as m:
            _mock_org(backend, m, history=[[], [failed]])
            m.delete(backend.url_for(f"solutions({_SOL_ID})"), exc=requests.exceptions.ReadTimeout)
            m.get(backend.url_for(f"solutions({_SOL_ID})"), json={"solutionid": _SOL_ID})
            with pytest.raises(D365Error, match="component in use"):
                _uninstall(backend, timeout=60)
        assert len(_deletes(m)) == 1

    def test_history_filter_reads_finished_failed_rows_only(self, backend):
        # A still-running Uninstall row can read result=false too; only a
        # Completed (msdyn_status 1) row may end the wait (#1179 review).
        from urllib.parse import unquote_plus

        with requests_mock.Mocker() as m:
            _mock_org(backend, m, history=[[], []])
            m.delete(backend.url_for(f"solutions({_SOL_ID})"), exc=requests.exceptions.ReadTimeout)
            m.get(backend.url_for(f"solutions({_SOL_ID})"), [{"json": {}}, _NOT_FOUND])
            _uninstall(backend, timeout=60)
        history = [r for r in m.request_history if "msdyn_solutionhistories" in r.url]
        assert history
        flt = unquote_plus(history[0].url)
        assert "msdyn_status eq 1" in flt and "msdyn_result eq false" in flt

    def test_failed_row_from_before_the_delete_is_not_this_uninstall(self, backend):
        old = {"msdyn_solutionhistoryid": "row-0", "msdyn_exceptionmessage": "an older failure"}
        with requests_mock.Mocker() as m:
            _mock_org(backend, m, history=[[old], [old]])
            m.delete(backend.url_for(f"solutions({_SOL_ID})"), exc=requests.exceptions.ReadTimeout)
            m.get(backend.url_for(f"solutions({_SOL_ID})"), [{"json": {}}, _NOT_FOUND])
            out = _uninstall(backend, timeout=60)
        assert out["uninstalled"] is True

    def test_negative_timeout_is_refused_before_any_http(self, backend):
        with requests_mock.Mocker() as m:
            with pytest.raises(D365Error, match=">= 0"):
                _uninstall(backend, timeout=-1)
        assert m.request_history == []

    def test_connect_timeout_keeps_the_retry(self, backend):
        with requests_mock.Mocker() as m:
            _mock_org(backend, m)
            m.delete(
                backend.url_for(f"solutions({_SOL_ID})"),
                [{"exc": requests.exceptions.ConnectTimeout}, {"status_code": 204}],
            )
            out = _uninstall(backend)
        assert out["uninstalled"] is True
        assert len(_deletes(m)) == 2

    def test_foreign_lock_collision_still_retries(self, backend):
        with requests_mock.Mocker() as m:
            _mock_org(backend, m)
            m.delete(backend.url_for(f"solutions({_SOL_ID})"), [_LOCK, {"status_code": 204}])
            out = _uninstall(backend)
        assert out["uninstalled"] is True
        assert len(_deletes(m)) == 2
