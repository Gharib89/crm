"""Backend dry-run seam: reads execute, only mutations preview.

Under dry_run, GETs hit the wire for real (the reads-execute rule); POST/PATCH/
PUT/DELETE return the preview echo; batch + async-poll keep their own
short-circuits; and dry_run is a constructor-only read-only property.

All HTTP is mocked via `requests_mock`. No live D365 server needed.
"""
# pyright: basic

from __future__ import annotations

import pytest
import requests_mock

from crm.utils.d365_backend import ConnectionProfile, D365Backend


class TestReadsExecute:
    def test_get_executes_under_dry_run(self, dry_backend: D365Backend):
        """A GET runs for real under dry_run and returns live data, not the echo."""
        with requests_mock.Mocker() as m:
            m.get(dry_backend.url_for("accounts"), json={"value": [{"name": "Acme"}]})
            result = dry_backend.get("accounts")
        assert result == {"value": [{"name": "Acme"}]}
        assert [r.method for r in m.request_history] == ["GET"]

    @pytest.mark.parametrize("verb", ["post", "patch", "put", "delete"])
    def test_mutations_preview_under_dry_run(self, dry_backend: D365Backend, verb: str):
        """Each write verb returns the preview echo and issues no HTTP."""
        call = getattr(dry_backend, verb)
        args = ("accounts",) if verb == "delete" else ("accounts", {"name": "Acme"})
        with requests_mock.Mocker() as m:
            result = call(*args)
        assert isinstance(result, dict)
        assert result["_dry_run"] is True
        assert result["method"].upper() == verb.upper()
        assert m.request_history == []


class TestDryRunIsReadOnly:
    def test_assignment_raises(self, dry_backend: D365Backend):
        """dry_run is constructor-only — the toggle dance is impossible."""
        assert dry_backend.dry_run is True
        with pytest.raises(AttributeError):
            dry_backend.dry_run = False  # type: ignore[misc]
        assert dry_backend.dry_run is True

    def test_constructor_sets_flag(self, profile: ConnectionProfile):
        assert D365Backend(profile, password="pw", dry_run=True).dry_run is True
        assert D365Backend(profile, password="pw", dry_run=False).dry_run is False
        assert D365Backend(profile, password="pw").dry_run is False
