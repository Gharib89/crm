"""Command-layer tests for `crm sitemap` — the bits that live in the Click
wrapper, not the core: exactly-one-of usage errors (exit 2) and routing the
cascade advisory onto the warnings channel rather than the `data` payload."""
# pyright: basic
from __future__ import annotations

import json

import pytest
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


class TestPassParams:
    def test_pass_params_with_non_url_is_usage_error(self, backend, monkeypatch):
        _use_backend(monkeypatch, backend)
        result = CliRunner().invoke(cli, [
            "--json", "sitemap", "add-subarea", _SID,
            "--area", "SFA", "--group", "SFA_Grp", "--id", "x",
            "--dashboard", "12345678-1234-1234-1234-1234567890ab",
            "--pass-params"])
        # --pass-params only applies to --url → CLI usage error (exit 2)
        assert result.exit_code == 2, result.output
        assert "pass-params" in result.output


class TestSetTitlePairing:
    def test_mismatched_lcid_title_counts_is_usage_error(self, backend, monkeypatch):
        _use_backend(monkeypatch, backend)
        result = CliRunner().invoke(cli, [
            "--json", "sitemap", "set-title", _SID, "--id", "SFA",
            "--lcid", "1033", "--lcid", "1031", "--title", "Only one"])
        # one --title for two --lcid is a CLI usage error (exit 2)
        assert result.exit_code == 2, result.output
        assert "one --title per --lcid" in result.output

    def test_duplicate_lcid_is_usage_error(self, backend, monkeypatch):
        # Untrusted input is validated at the command layer before ctx.backend()
        # (house rule), so a repeated --lcid is a Click usage error (exit 2) and
        # makes no live call.
        _use_backend(monkeypatch, backend)
        with rm_module.Mocker() as m:
            result = CliRunner().invoke(cli, [
                "--json", "sitemap", "set-title", _SID, "--id", "SFA",
                "--lcid", "1033", "--title", "A",
                "--lcid", "1033", "--title", "B", "--no-publish"])
            assert m.request_history == []  # nothing hit the backend
        assert result.exit_code == 2, result.output
        assert "duplicate --lcid 1033" in result.output

    @pytest.mark.parametrize("args,needle", [
        (["--id", "  ", "--lcid", "1033", "--title", "X"], "--id must not be empty"),
        (["--id", "SFA", "--lcid", "99", "--title", "X"], "4-digit locale ID"),
        (["--id", "SFA", "--lcid", "1033", "--title", "  "], "must not be empty"),
    ])
    def test_bad_input_is_usage_error_before_backend(self, backend, monkeypatch,
                                                     args, needle):
        _use_backend(monkeypatch, backend)
        with rm_module.Mocker() as m:
            result = CliRunner().invoke(
                cli, ["--json", "sitemap", "set-title", _SID, *args])
            assert m.request_history == []
        assert result.exit_code == 2, result.output
        assert needle in result.output

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

    def test_duplicate_lcid_is_usage_error(self, backend, monkeypatch):
        _use_backend(monkeypatch, backend)
        with rm_module.Mocker() as m:
            result = CliRunner().invoke(cli, [
                "--json", "sitemap", "set-description", _SID, "--id", "SFA",
                "--lcid", "1033", "--description", "A",
                "--lcid", "1033", "--description", "B", "--no-publish"])
            assert m.request_history == []
        assert result.exit_code == 2, result.output
        assert "duplicate --lcid 1033" in result.output


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
