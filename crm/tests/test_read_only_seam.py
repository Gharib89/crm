"""Backend read-only seam: reads execute, mutations are refused (#665).

A read-only profile refuses every non-GET Web API call — an operational failure
(D365Error, exit 1) — minus a small read-safe export allowlist; GETs execute
normally. `$batch` is blocked unconditionally. The refusal never masquerades as a
dry-run preview: `--dry-run` runs first, so a read-only + dry-run backend still
returns previews (dry-run never hits the wire; strictly safer).

`read_only` is a constructor-only property (no setter), mirroring `dry_run`.

All HTTP is mocked via `requests_mock`. No live D365 server needed.
"""
# pyright: basic

from __future__ import annotations

import pytest
import requests_mock

from crm.utils.d365_backend import (
    BatchOperation, ConnectionProfile, D365Backend, D365Error,
)


def _profile(read_only: bool = True) -> ConnectionProfile:
    return ConnectionProfile(
        name="ro", url="https://crm.contoso.local/contoso",
        domain="CONTOSO", username="alice", api_version="v9.2",
        verify_ssl=False, read_only=read_only,
    )


@pytest.fixture
def ro_backend() -> D365Backend:
    """A read-only backend (no network at construction)."""
    return D365Backend(_profile(read_only=True), password="pw")


class TestReadsExecute:
    def test_get_executes_under_read_only(self, ro_backend: D365Backend):
        """A GET runs for real under read_only and returns live data."""
        with requests_mock.Mocker() as m:
            m.get(ro_backend.url_for("accounts"), json={"value": [{"name": "Acme"}]})
            result = ro_backend.get("accounts")
        assert result == {"value": [{"name": "Acme"}]}
        assert [r.method for r in m.request_history] == ["GET"]


class TestMutationsRefused:
    @pytest.mark.parametrize("verb", ["post", "patch", "put", "delete"])
    def test_mutations_raise_and_issue_no_http(self, ro_backend: D365Backend, verb: str):
        """Each write verb is refused with a loud D365Error and hits no wire."""
        call = getattr(ro_backend, verb)
        args = ("accounts",) if verb == "delete" else ("accounts", {"name": "Acme"})
        with requests_mock.Mocker() as m:
            with pytest.raises(D365Error) as exc:
                call(*args)
        assert m.request_history == []
        msg = str(exc.value)
        assert "read-only" in msg
        assert "ro" in msg  # names the profile
        assert "--no-read-only" in msg  # names the fix

    def test_batch_blocked_unconditionally(self, ro_backend: D365Backend):
        """$batch is a bulk-write path — refused under read_only even if all GETs."""
        ops: list[BatchOperation] = [{"method": "GET", "url": "accounts"}]
        with requests_mock.Mocker() as m:
            with pytest.raises(D365Error) as exc:
                ro_backend.batch(ops)
        assert m.request_history == []
        assert "read-only" in str(exc.value)


class TestReadSafeActions:
    @pytest.mark.parametrize("path", [
        "ExportSolution",
        "ExportSolutionAsync",
        "DownloadSolutionExportData",
        "solutions/Microsoft.Dynamics.CRM.ExportTranslation",
    ])
    def test_read_safe_post_actions_execute(self, ro_backend: D365Backend, path: str):
        """Export actions extract rather than mutate — exempt from the refusal."""
        with requests_mock.Mocker() as m:
            m.post(ro_backend.url_for(path), json={"ok": True})
            result = ro_backend.post(path, json_body={})
        assert result == {"ok": True}
        assert [r.method for r in m.request_history] == ["POST"]


class TestDryRunWins:
    def test_read_only_plus_dry_run_previews_not_refuses(self):
        """--dry-run runs first: a read-only+dry-run mutation previews (ok), no raise."""
        backend = D365Backend(_profile(read_only=True), password="pw", dry_run=True)
        with requests_mock.Mocker() as m:
            result = backend.post("accounts", {"name": "Acme"})
        assert isinstance(result, dict)
        assert result["_dry_run"] is True
        assert m.request_history == []

    def test_batch_dry_run_previews_under_read_only(self):
        """Dry-run batch under read_only returns the dry-run echo, never raises."""
        backend = D365Backend(_profile(read_only=True), password="pw", dry_run=True)
        ops: list[BatchOperation] = [{"method": "POST", "url": "accounts", "body": {}}]
        with requests_mock.Mocker() as m:
            results = backend.batch(ops)
        assert results[0]["error"] == "dry-run"
        assert m.request_history == []


class TestReadOnlyIsConstructorOnly:
    def test_assignment_raises(self, ro_backend: D365Backend):
        """read_only is constructor-only — no setter (mirrors dry_run)."""
        assert ro_backend.read_only is True
        with pytest.raises(AttributeError):
            ro_backend.read_only = False  # type: ignore[misc]
        assert ro_backend.read_only is True

    def test_default_is_writable(self, backend: D365Backend):
        """A profile without the flag is writable (read_only False)."""
        assert backend.read_only is False


class TestProfilePersistence:
    def test_from_dict_defaults_false_when_absent(self):
        """A flag-less profile JSON (pre-#665) loads as writable — back-compat."""
        d = {"name": "old", "url": "https://crm.contoso.local/c",
             "domain": "D", "username": "u"}
        assert ConnectionProfile.from_dict(d).read_only is False

    def test_round_trips_through_dict(self):
        """read_only survives to_dict → from_dict."""
        p = _profile(read_only=True)
        assert p.to_dict()["read_only"] is True
        assert ConnectionProfile.from_dict(p.to_dict()).read_only is True
