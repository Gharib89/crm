"""#1189: a write scoped by --solution is refused client-side when that solution is managed.

The platform silently ignores MSCRM.SolutionUniqueName for a managed target and
creates the component unmanaged in Default, so the backend guards the one seam
every solution-scoped write passes through. All HTTP is mocked.
"""

# pyright: basic
from __future__ import annotations

import pytest
import requests_mock

from crm.core import entity as entity_mod
from crm.utils.d365_backend import D365Error

pytestmark = pytest.mark.managed_guard

_SOL_ID = "22222222-2222-2222-2222-222222222222"


def _mock_solutions(m, backend, *, managed: bool | None):
    rows = [] if managed is None else [{"solutionid": _SOL_ID, "ismanaged": managed}]
    return m.get(backend.url_for("solutions"), json={"value": rows})


def _writes(m, suffix):
    return [r for r in m.request_history if r.method == "POST" and r.url.endswith(suffix)]


def test_managed_target_refused_before_send(backend):
    with requests_mock.Mocker() as m:
        _mock_solutions(m, backend, managed=True)
        m.post(backend.url_for("roles"), status_code=204)
        with pytest.raises(D365Error) as exc:
            entity_mod.create(backend, "roles", {"name": "r"}, solution="Pilot")
        assert exc.value.code == "CannotModifyManagedSolution"
        assert "Pilot" in str(exc.value)
        assert _writes(m, "/roles") == []


def test_other_write_path_refused(backend):
    with requests_mock.Mocker() as m:
        _mock_solutions(m, backend, managed=True)
        m.post(backend.url_for("EntityDefinitions"), status_code=204)
        with pytest.raises(D365Error) as exc:
            backend.post("EntityDefinitions", json_body={}, solution="Pilot")
        assert exc.value.code == "CannotModifyManagedSolution"
        assert _writes(m, "/EntityDefinitions") == []


def test_dry_run_reports_the_same_refusal(dry_backend):
    with requests_mock.Mocker() as m:
        _mock_solutions(m, dry_backend, managed=True)
        with pytest.raises(D365Error) as exc:
            dry_backend.post("roles", json_body={}, solution="Pilot")
        assert exc.value.code == "CannotModifyManagedSolution"


def test_unmanaged_target_sends_header(backend):
    with requests_mock.Mocker() as m:
        _mock_solutions(m, backend, managed=False)
        m.post(backend.url_for("roles"), status_code=204)
        entity_mod.create(backend, "roles", {"name": "r"}, solution="Pilot")
        sent = _writes(m, "/roles")
        assert len(sent) == 1
        assert sent[0].headers["MSCRM.SolutionUniqueName"] == "Pilot"


def test_several_writes_one_lookup(backend):
    with requests_mock.Mocker() as m:
        sols = _mock_solutions(m, backend, managed=False)
        m.post(backend.url_for("roles"), status_code=204)
        for _ in range(3):
            entity_mod.create(backend, "roles", {"name": "r"}, solution="Pilot")
        assert sols.call_count == 1


def test_unknown_solution_keeps_todays_behaviour(backend):
    with requests_mock.Mocker() as m:
        _mock_solutions(m, backend, managed=None)
        m.post(backend.url_for("roles"), status_code=204)
        entity_mod.create(backend, "roles", {"name": "r"}, solution="Nope")
        assert len(_writes(m, "/roles")) == 1


def test_read_with_solution_is_not_guarded(backend):
    with requests_mock.Mocker() as m:
        sols = _mock_solutions(m, backend, managed=True)
        m.get(backend.url_for("roles"), json={"value": []})
        backend.get("roles", solution="Pilot")
        assert sols.call_count == 0
