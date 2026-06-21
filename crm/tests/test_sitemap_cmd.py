"""Command-layer tests for `crm sitemap` — the bits that live in the Click
wrapper, not the core: exactly-one-of usage errors (exit 2) and routing the
cascade advisory onto the warnings channel rather than the `data` payload."""
# pyright: basic
from __future__ import annotations

import json

import requests_mock as rm_module
from click.testing import CliRunner

from crm.cli import cli
from crm.utils.d365_backend import D365Backend

_SID = "aaaa1111-2222-3333-4444-555566667777"
_SEED = (
    '<SiteMap><Area Id="SFA"><Group Id="SFA_Grp">'
    '<SubArea Id="nav_accts" Entity="account" /></Group></Area></SiteMap>')


def _use_backend(monkeypatch, backend):
    monkeypatch.setattr("crm.cli.CLIContext.backend", lambda self: backend)


def _sitemaps_url(backend: D365Backend) -> str:
    return backend.url_for(f"sitemaps({_SID})")


class TestExactlyOneContentMode:
    def test_zero_modes_is_usage_error(self, backend, monkeypatch):
        _use_backend(monkeypatch, backend)
        result = CliRunner().invoke(cli, [
            "--json", "sitemap", "add-subarea", _SID,
            "--area", "SFA", "--group", "SFA_Grp", "--id", "x"])
        # mutually-exclusive/required flag combos are CLI usage errors (exit 2)
        assert result.exit_code == 2, result.output
        assert "exactly one of" in result.output

    def test_two_modes_is_usage_error(self, backend, monkeypatch):
        _use_backend(monkeypatch, backend)
        result = CliRunner().invoke(cli, [
            "--json", "sitemap", "add-subarea", _SID,
            "--area", "SFA", "--group", "SFA_Grp", "--id", "x",
            "--url", "https://x", "--dashboard",
            "12345678-1234-1234-1234-1234567890ab"])
        assert result.exit_code == 2, result.output


class TestCascadeWarningRouting:
    def test_cascade_goes_to_warnings_not_data(self, backend, monkeypatch):
        _use_backend(monkeypatch, backend)
        with rm_module.Mocker() as m:
            m.get(_sitemaps_url(backend), json={"sitemapxml": _SEED})
            m.patch(_sitemaps_url(backend), status_code=204)
            result = CliRunner().invoke(cli, [
                "--json", "sitemap", "remove-node", _SID, "--id", "SFA",
                "--no-publish"])
        assert result.exit_code == 0, result.output
        env = json.loads(result.output)
        # advisory surfaces on meta.warnings, and is NOT left in data
        assert any("descendant" in w for w in env["meta"]["warnings"]), env
        assert "cascade_warning" not in env["data"], env
