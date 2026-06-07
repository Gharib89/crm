"""Command-level tests for `crm connection connect` api_version negotiation (#51)."""
# pyright: basic
from __future__ import annotations

import json
import os

import pytest
import requests_mock
from click.testing import CliRunner

from crm.cli import cli
from crm.core import keyring_store

_WHOAMI = {"UserId": "00000000-0000-0000-0000-000000000001"}


def _profile_json(home, name):
    return json.loads((home / ".crm" / "profiles" / f"{name}.json").read_text(encoding="utf-8"))


@pytest.fixture(autouse=True)
def _isolated_home(tmp_path):
    # Snapshot/restore os.environ around each test: connect/test →
    # resolve_credentials → load_dotenv() writes .env values straight into
    # os.environ (monkeypatch can't undo those), which would leak into later
    # tests (cf. #56). Default CRM_DOTENV to a noop path so the repo's real
    # ./.env is never autoloaded; tests may override it.
    saved = dict(os.environ)
    os.environ["CRM_HOME"] = str(tmp_path / ".crm")
    os.environ["CRM_DOTENV"] = str(tmp_path / "noop.env")
    try:
        yield
    finally:
        os.environ.clear()
        os.environ.update(saved)


class TestConnectCommandNegotiation:
    def test_connect_onprem_persists_v91(self, tmp_path):
        # AC: connect against an on-prem org without --api-version yields a
        # working profile whose saved api_version is v9.1.
        base = "https://internalcrm.contoso.local/Contoso"
        with requests_mock.Mocker() as m:
            m.get(f"{base}/api/data/v9.2/WhoAmI", status_code=501,
                  json={"error": {"code": "0x0", "message": "Not Implemented"}})
            m.get(f"{base}/api/data/v9.1/WhoAmI", json=_WHOAMI)
            result = CliRunner().invoke(cli, [
                "connection", "connect", "--url", base,
                "--username", "alice", "--domain", "CONTOSO",
                "--password", "pw", "--profile-name", "onprem",
            ])
        assert result.exit_code == 0, result.output
        assert _profile_json(tmp_path, "onprem")["api_version"] == "v9.1"

    def test_connect_cloud_keeps_v92(self, tmp_path):
        # AC: cloud/OAuth-style org keeps v9.2, no needless downgrade probe.
        base = "https://contoso.crm.dynamics.com"
        with requests_mock.Mocker() as m:
            m.get(f"{base}/api/data/v9.2/WhoAmI", json=_WHOAMI)
            result = CliRunner().invoke(cli, [
                "connection", "connect", "--url", base,
                "--username", "alice", "--password", "pw",
                "--profile-name", "cloud",
            ])
            assert all("/v9.1/" not in r.url for r in m.request_history)
        assert result.exit_code == 0, result.output
        assert _profile_json(tmp_path, "cloud")["api_version"] == "v9.2"

    def test_connect_explicit_version_not_downgraded(self):
        # AC: an explicitly-requested version 501s through, never auto-downgraded.
        base = "https://internalcrm.contoso.local/Contoso"
        with requests_mock.Mocker() as m:
            m.get(f"{base}/api/data/v9.2/WhoAmI", status_code=501,
                  json={"error": {"code": "0x0", "message": "Not Implemented"}})
            result = CliRunner().invoke(cli, [
                "connection", "connect", "--url", base,
                "--username", "alice", "--password", "pw",
                "--profile-name", "pinned", "--api-version", "v9.2",
            ])
            assert all("/v9.1/" not in r.url for r in m.request_history)
        assert result.exit_code != 0  # operational failure surfaced

    def test_test_respects_version_pinned_in_dotenv(self, tmp_path):
        # A version pinned only in .env must be honoured: `connection test`
        # must NOT negotiate/downgrade it, even on a 501. Regression for the
        # decide-before-dotenv-autoload bug (PR #57 review). The autouse
        # snapshot/restore fixture undoes the load_dotenv() env writes.
        base = "https://internalcrm.contoso.local/Contoso"
        env_file = tmp_path / "pinned.env"
        env_file.write_text(
            "\n".join([
                f"D365_URL={base}",
                "D365_USERNAME=alice",
                "D365_PASSWORD=pw",
                "D365_DOMAIN=CONTOSO",
                "D365_AUTH=ntlm",
                "D365_API_VERSION=v9.2",
            ]) + "\n",
            encoding="utf-8",
        )
        os.environ["CRM_DOTENV"] = str(env_file)  # override autouse noop
        for k in ("D365_API_VERSION", "CRM_API_VERSION"):
            os.environ.pop(k, None)  # ensure only the .env supplies it
        with requests_mock.Mocker() as m:
            m.get(f"{base}/api/data/v9.2/WhoAmI", status_code=501,
                  json={"error": {"code": "0x0", "message": "Not Implemented"}})
            result = CliRunner().invoke(cli, ["connection", "test"])
            assert all("/v9.1/" not in r.url for r in m.request_history)
        assert result.exit_code != 0  # 501 surfaces, no silent downgrade


class TestMissingProfileEnvelope:
    """crm --profile <missing> must emit the standard envelope, not a traceback (#109)."""

    def test_human_mode_clean_error(self):
        # AC: exit 1, error mentions profile name, no raw traceback.
        result = CliRunner().invoke(cli, ["--profile", "does_not_exist", "connection", "whoami"])
        assert result.exit_code == 1
        # Human-mode errors render on stderr; the JSON envelope (other test) on stdout.
        assert "does_not_exist" in result.stderr
        assert "Traceback" not in result.stderr
        assert "FileNotFoundError" not in result.stderr

    def test_json_mode_envelope(self):
        # AC: exit 1, parseable JSON envelope with ok=false and category=validation.
        result = CliRunner().invoke(cli, ["--json", "--profile", "does_not_exist", "connection", "whoami"])
        assert result.exit_code == 1
        payload = json.loads(result.stdout)
        assert payload["ok"] is False
        assert "does_not_exist" in payload["error"]
        assert payload["meta"]["category"] == "validation"


@pytest.fixture
def fake_keyring(monkeypatch):
    store = {}
    monkeypatch.setattr(keyring_store, "is_available", lambda: True)
    monkeypatch.setattr(keyring_store, "get_secret", lambda n: store.get(n))
    monkeypatch.setattr(keyring_store, "set_secret",
                        lambda n, s: store.__setitem__(n, s))
    monkeypatch.setattr(keyring_store, "has_secret", lambda n: n in store)
    monkeypatch.setattr(keyring_store, "delete_secret",
                        lambda n: store.pop(n, None) is not None)
    return store


class TestConnectStoreFlags:
    _BASE = "https://crm.contoso.local/Contoso"

    def test_store_password_writes_keyring(self, fake_keyring):
        with requests_mock.Mocker() as m:
            m.get(f"{self._BASE}/api/data/v9.2/WhoAmI", json=_WHOAMI)
            r = CliRunner().invoke(cli, [
                "connection", "connect", "--url", self._BASE, "--username", "alice",
                "--domain", "CONTOSO", "--password", "pw",
                "--profile-name", "prod", "--store-password",
            ])
        assert r.exit_code == 0, r.output
        assert fake_keyring["prod"] == "pw"

    def test_store_plaintext_writes_secret_with_warning(self, tmp_path):
        with requests_mock.Mocker() as m:
            m.get(f"{self._BASE}/api/data/v9.2/WhoAmI", json=_WHOAMI)
            r = CliRunner().invoke(cli, [
                "connection", "connect", "--url", self._BASE, "--username", "alice",
                "--domain", "CONTOSO", "--password", "pw",
                "--profile-name", "ci", "--store-password-plaintext",
            ])
        assert r.exit_code == 0, r.output
        assert _profile_json(tmp_path, "ci")["_secret"] == "pw"
        assert "plaintext" in r.stderr.lower()

    def test_both_flags_is_usage_error(self):
        r = CliRunner().invoke(cli, [
            "connection", "connect", "--url", self._BASE, "--username", "alice",
            "--password", "pw", "--profile-name", "x",
            "--store-password", "--store-password-plaintext",
        ])
        assert r.exit_code == 2  # click.UsageError
        assert "mutually exclusive" in (r.output + (r.stderr or "")).lower()

    def test_store_password_without_keyring_is_graceful(self, monkeypatch):
        monkeypatch.setattr(keyring_store, "is_available", lambda: False)
        def _raise(n, s):
            from crm.utils.d365_backend import D365Error
            raise D365Error("The optional 'keyring' dependency is not installed.")
        monkeypatch.setattr(keyring_store, "set_secret", _raise)
        with requests_mock.Mocker() as m:
            m.get(f"{self._BASE}/api/data/v9.2/WhoAmI", json=_WHOAMI)
            r = CliRunner().invoke(cli, [
                "connection", "connect", "--url", self._BASE, "--username", "alice",
                "--password", "pw", "--profile-name", "p", "--store-password",
            ])
        assert r.exit_code == 1               # graceful failure envelope
        assert "Traceback" not in (r.output + (r.stderr or ""))
        assert "keyring" in (r.output + (r.stderr or "")).lower()

    def test_reconnect_without_store_flag_clears_plaintext(self, fake_keyring, tmp_path):
        # Regression for the maintainer-approved fix (#130): save_profile now
        # preserves _secret across re-saves, so connect with NO store flag must
        # explicitly drop a previously stored plaintext secret.
        with requests_mock.Mocker() as m:
            m.get(f"{self._BASE}/api/data/v9.2/WhoAmI", json=_WHOAMI)
            runner = CliRunner()
            r1 = runner.invoke(cli, [
                "connection", "connect", "--url", self._BASE, "--username", "alice",
                "--password", "pw", "--profile-name", "prod",
                "--store-password-plaintext",
            ])
            assert r1.exit_code == 0, r1.output
            assert _profile_json(tmp_path, "prod")["_secret"] == "pw"
            r2 = runner.invoke(cli, [
                "connection", "connect", "--url", self._BASE, "--username", "alice",
                "--password", "pw", "--profile-name", "prod",
            ])
            assert r2.exit_code == 0, r2.output
        assert "_secret" not in _profile_json(tmp_path, "prod")


class TestDeletePassword:
    def _save_profile(self):
        from crm.utils.d365_backend import ConnectionProfile
        session_path = None  # noqa: F841
        from crm.core import session as session_mod
        session_mod.save_profile(ConnectionProfile(
            name="prod", url="https://crm.contoso.local/c", domain="C", username="a",
        ))

    def test_delete_removes_keyring_entry(self, fake_keyring):
        self._save_profile()
        fake_keyring["prod"] = "pw"
        r = CliRunner().invoke(cli, ["connection", "delete-password", "--profile", "prod"])
        assert r.exit_code == 0, r.output
        assert "prod" not in fake_keyring

    def test_delete_removes_plaintext_secret(self, fake_keyring, tmp_path):
        from crm.core import session as session_mod
        self._save_profile()
        session_mod.save_profile_secret_plaintext("prod", "pw")
        r = CliRunner().invoke(cli, ["connection", "delete-password", "--profile", "prod"])
        assert r.exit_code == 0, r.output
        assert session_mod.load_profile_secret("prod") is None

    def test_delete_nothing_stored_is_clear_noop(self, fake_keyring):
        self._save_profile()
        r = CliRunner().invoke(cli, ["--json", "connection", "delete-password", "--profile", "prod"])
        assert r.exit_code == 0, r.output
        payload = json.loads(r.stdout)
        assert payload["data"]["removed"] is False
