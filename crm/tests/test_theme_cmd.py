"""Command-layer tests for `crm theme` (list / get / create / update / publish)."""
# pyright: basic
from __future__ import annotations

import json

import requests_mock as rm_module

from click.testing import CliRunner
from crm.cli import cli
from crm.utils.d365_backend import D365Backend


_THEME = {
    "themeid": "11112222-3333-4444-5555-666677778888",
    "name": "Corporate Blue",
    "type": True,
    "isdefaulttheme": False,
    "maincolor": "#0066cc",
}
_NEW_ID = "99998888-7777-6666-5555-444433332222"


def _themes_url(backend: D365Backend) -> str:
    return backend.url_for("themes")


def _use_backend(monkeypatch, backend):
    monkeypatch.setattr("crm.cli.CLIContext.backend", lambda self: backend)


class TestThemeList:
    def test_list_themes(self, backend, monkeypatch):
        _use_backend(monkeypatch, backend)
        with rm_module.Mocker() as m:
            m.get(_themes_url(backend), json={"value": [_THEME]})
            result = CliRunner().invoke(cli, ["--json", "theme", "list"])
        assert result.exit_code == 0, result.output
        env = json.loads(result.output)
        assert env["ok"] is True
        row = env["data"][0]
        assert row["themeid"] == _THEME["themeid"]
        # list returns summary columns only
        assert "maincolor" not in row


class TestThemeGet:
    def test_get_includes_branding(self, backend, monkeypatch):
        _use_backend(monkeypatch, backend)
        tid = _THEME["themeid"]
        with rm_module.Mocker() as m:
            m.get(backend.url_for(f"themes({tid})"), json=_THEME)
            result = CliRunner().invoke(cli, ["--json", "theme", "get", tid])
        assert result.exit_code == 0, result.output
        env = json.loads(result.output)
        assert env["data"]["maincolor"] == "#0066cc"


class TestThemeCreate:
    def test_create_with_set_pairs(self, backend, monkeypatch):
        _use_backend(monkeypatch, backend)
        with rm_module.Mocker() as m:
            m.post(_themes_url(backend), status_code=204,
                   headers={"OData-EntityId": backend.url_for(f"themes({_NEW_ID})")})
            result = CliRunner().invoke(cli, [
                "--json", "theme", "create",
                "--name", "Corporate Blue",
                "--set", "maincolor=#0066cc",
                "--set", "navbarbackgroundcolor=#002050",
            ])
        assert result.exit_code == 0, result.output
        body = m.last_request.json()
        assert body["name"] == "Corporate Blue"
        assert body["maincolor"] == "#0066cc"
        assert body["navbarbackgroundcolor"] == "#002050"
        env = json.loads(result.output)
        assert env["data"]["created"] is True
        assert env["data"]["themeid"] == _NEW_ID

    def test_create_rejects_malformed_set(self, backend, monkeypatch):
        _use_backend(monkeypatch, backend)
        result = CliRunner().invoke(cli, [
            "--json", "theme", "create", "--name", "T", "--set", "novalue",
        ])
        # usage error, no round-trip
        assert result.exit_code == 2, result.output

    def test_create_dry_run_previews(self, dry_backend, monkeypatch):
        _use_backend(monkeypatch, dry_backend)
        result = CliRunner().invoke(cli, [
            "--json", "--dry-run", "theme", "create",
            "--name", "T", "--set", "maincolor=#fff",
        ])
        assert result.exit_code == 0, result.output
        env = json.loads(result.output)
        assert env["meta"]["dry_run"] is True
        assert env["data"]["would_create"]["body"]["maincolor"] == "#fff"


class TestThemeUpdate:
    def test_update_patches(self, backend, monkeypatch):
        _use_backend(monkeypatch, backend)
        tid = _THEME["themeid"]
        with rm_module.Mocker() as m:
            m.patch(backend.url_for(f"themes({tid})"), status_code=204)
            result = CliRunner().invoke(cli, [
                "--json", "theme", "update", tid, "--set", "maincolor=#ff0000",
            ])
        assert result.exit_code == 0, result.output
        assert m.last_request.json()["maincolor"] == "#ff0000"
        assert json.loads(result.output)["data"]["updated"] is True

    def test_update_requires_a_field(self, backend, monkeypatch):
        _use_backend(monkeypatch, backend)
        tid = _THEME["themeid"]
        result = CliRunner().invoke(cli, ["--json", "theme", "update", tid])
        assert result.exit_code == 1, result.output
        assert json.loads(result.output)["ok"] is False


class TestThemePublish:
    def test_publish_calls_action(self, backend, monkeypatch):
        _use_backend(monkeypatch, backend)
        tid = _THEME["themeid"]
        action_url = backend.url_for(
            f"themes({tid})/Microsoft.Dynamics.CRM.PublishTheme")
        with rm_module.Mocker() as m:
            m.post(action_url, status_code=204)
            result = CliRunner().invoke(cli, ["--json", "theme", "publish", tid])
        assert result.exit_code == 0, result.output
        env = json.loads(result.output)
        assert env["data"]["published"] is True
        assert env["data"]["themeid"] == tid
