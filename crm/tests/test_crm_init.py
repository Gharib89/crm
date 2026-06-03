"""Unit tests for `crm init`."""
# pyright: basic
from __future__ import annotations

import json
from pathlib import Path

import pytest
from click.testing import CliRunner

from crm.cli import cli


class TestTemplateMode:
    def test_template_writes_env_example(self, tmp_path, monkeypatch):
        monkeypatch.chdir(tmp_path)
        runner = CliRunner()
        result = runner.invoke(cli, ["init", "--template"])
        assert result.exit_code == 0, result.output
        env_file = tmp_path / ".env.example"
        assert env_file.exists()
        content = env_file.read_text(encoding="utf-8")
        assert "CRM_URL=" in content
        assert "CRM_USERNAME=" in content
        assert "CRM_PASSWORD=" in content
        assert "CRM_AUTH=ntlm" in content
        assert "CRM_LOG_LEVEL=" in content

    def test_template_includes_oauth_block(self, tmp_path, monkeypatch):
        monkeypatch.chdir(tmp_path)
        runner = CliRunner()
        result = runner.invoke(cli, ["init", "--template"])
        assert result.exit_code == 0, result.output
        content = (tmp_path / ".env.example").read_text(encoding="utf-8")
        assert "CRM_AUTH=oauth" in content
        assert "CRM_TENANT_ID=" in content
        assert "CRM_CLIENT_ID=" in content
        assert "CRM_CLIENT_SECRET=" in content

    def test_template_refuses_to_overwrite(self, tmp_path, monkeypatch):
        monkeypatch.chdir(tmp_path)
        (tmp_path / ".env.example").write_text("existing", encoding="utf-8")
        runner = CliRunner()
        result = runner.invoke(cli, ["init", "--template"])
        assert result.exit_code != 0
        assert "already exists" in result.output.lower()
        assert (tmp_path / ".env.example").read_text(encoding="utf-8") == "existing"


class TestInteractiveWizard:
    def test_wizard_writes_profile(self, tmp_path, monkeypatch):
        monkeypatch.setenv("CRM_HOME", str(tmp_path / ".crm"))
        runner = CliRunner()
        inputs = "\n".join([
            "https://crm.contoso.local/contoso",
            "ntlm",
            "alice",
            "pw1234",
            "CONTOSO",
            "myprofile",
            "y",
        ]) + "\n"
        result = runner.invoke(cli, ["init"], input=inputs)
        assert result.exit_code == 0, result.output
        profile_file = tmp_path / ".crm" / "profiles" / "myprofile.json"
        assert profile_file.exists()
        data = json.loads(profile_file.read_text(encoding="utf-8"))
        assert data["url"].startswith("https://crm.contoso.local")
        assert data["username"] == "alice"
        assert data["domain"] == "CONTOSO"
        assert data["auth_scheme"] == "ntlm"

    def test_wizard_writes_oauth_profile(self, tmp_path, monkeypatch):
        monkeypatch.setenv("CRM_HOME", str(tmp_path / ".crm"))
        runner = CliRunner()
        inputs = "\n".join([
            "https://contoso.crm.dynamics.com",
            "oauth",
            "11111111-1111-1111-1111-111111111111",  # tenant
            "22222222-2222-2222-2222-222222222222",  # client
            "the-secret",                             # client secret (not persisted)
            "oauthprofile",
        ]) + "\n"
        result = runner.invoke(cli, ["init"], input=inputs)
        assert result.exit_code == 0, result.output
        data = json.loads((tmp_path / ".crm" / "profiles" / "oauthprofile.json").read_text(encoding="utf-8"))
        assert data["auth_scheme"] == "oauth"
        assert data["tenant_id"] == "11111111-1111-1111-1111-111111111111"
        assert data["client_id"] == "22222222-2222-2222-2222-222222222222"
        assert data["username"] == ""
        assert "the-secret" not in json.dumps(data)  # secret never persisted
