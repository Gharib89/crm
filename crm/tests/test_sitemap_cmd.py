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


class TestSetTitlePairing:
    def test_mismatched_lcid_title_counts_is_usage_error(self, backend, monkeypatch):
        _use_backend(monkeypatch, backend)
        result = CliRunner().invoke(cli, [
            "--json", "sitemap", "set-title", _SID, "--id", "SFA",
            "--lcid", "1033", "--lcid", "1031", "--title", "Only one"])
        # one --title for two --lcid is a CLI usage error (exit 2)
        assert result.exit_code == 2, result.output
        assert "one --title per --lcid" in result.output

    def test_duplicate_lcid_is_in_command_error_exit_1(self, backend, monkeypatch):
        # A repeated --lcid is in-command validation, not a malformed invocation:
        # per ADR 0001 it exits 1 (the ok:false envelope), like add-area's
        # id-grammar checks — distinct from the exit-2 pair-count usage error.
        _use_backend(monkeypatch, backend)
        with rm_module.Mocker() as m:
            m.get(backend.url_for("RetrieveProvisionedLanguages()"),
                  json={"RetrieveProvisionedLanguages": [1033]})
            result = CliRunner().invoke(cli, [
                "--json", "sitemap", "set-title", _SID, "--id", "SFA",
                "--lcid", "1033", "--title", "A",
                "--lcid", "1033", "--title", "B", "--no-publish"])
        assert result.exit_code == 1, result.output
        env = json.loads(result.output)
        assert env["ok"] is False and "duplicate --lcid 1033" in env["error"]

    def test_repeatable_pairs_reach_core(self, backend, monkeypatch):
        _use_backend(monkeypatch, backend)
        with rm_module.Mocker() as m:
            m.get(backend.url_for("RetrieveProvisionedLanguages()"),
                  json={"RetrieveProvisionedLanguages": [1033, 1031]})
            m.get(_sitemaps_url(backend), json={"sitemapxml": _SEED})
            m.patch(_sitemaps_url(backend), status_code=204)
            result = CliRunner().invoke(cli, [
                "--json", "sitemap", "set-title", _SID, "--id", "SFA",
                "--lcid", "1033", "--title", "Sales",
                "--lcid", "1031", "--title", "Vertrieb", "--no-publish"])
        assert result.exit_code == 0, result.output
        env = json.loads(result.output)
        assert env["data"]["action"] == "set-title"
        assert env["data"]["titles"] == [
            {"lcid": 1033, "title": "Sales"}, {"lcid": 1031, "title": "Vertrieb"}]


class TestSetDescriptionPairing:
    def test_mismatched_counts_is_usage_error(self, backend, monkeypatch):
        _use_backend(monkeypatch, backend)
        result = CliRunner().invoke(cli, [
            "--json", "sitemap", "set-description", _SID, "--id", "SFA",
            "--lcid", "1033", "--lcid", "1031", "--description", "Only one"])
        assert result.exit_code == 2, result.output
        assert "one --description per --lcid" in result.output

    def test_duplicate_lcid_is_in_command_error_exit_1(self, backend, monkeypatch):
        _use_backend(monkeypatch, backend)
        with rm_module.Mocker() as m:
            m.get(backend.url_for("RetrieveProvisionedLanguages()"),
                  json={"RetrieveProvisionedLanguages": [1033]})
            result = CliRunner().invoke(cli, [
                "--json", "sitemap", "set-description", _SID, "--id", "SFA",
                "--lcid", "1033", "--description", "A",
                "--lcid", "1033", "--description", "B", "--no-publish"])
        assert result.exit_code == 1, result.output
        env = json.loads(result.output)
        assert env["ok"] is False and "duplicate --lcid 1033" in env["error"]


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
