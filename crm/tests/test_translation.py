"""Unit tests for crm.core.translation (ExportTranslation / ImportTranslation)."""
# pyright: basic

from __future__ import annotations

import base64

import pytest
import requests_mock

from crm.utils.d365_backend import ConnectionProfile, D365Backend, D365Error


@pytest.fixture
def backend():
    profile = ConnectionProfile(
        name="testp",
        url="https://crm.contoso.local/contoso",
        domain="CONTOSO",
        username="alice",
        api_version="v9.2",
        verify_ssl=False,
    )
    return D365Backend(profile, password="pw", dry_run=False)


_ZIP_BYTES = b"PK\x03\x04 fake translations zip"


class TestExportTranslation:
    def test_export_writes_zip_and_returns_envelope(self, backend, tmp_path):
        from crm.core import translation
        out = tmp_path / "labels.zip"
        encoded = base64.b64encode(_ZIP_BYTES).decode("ascii")
        with requests_mock.Mocker() as m:
            m.post(
                backend.url_for("solutions/Microsoft.Dynamics.CRM.ExportTranslation"),
                json={"ExportTranslationFile": encoded},
            )
            info = translation.export_translation(backend, "CRMWorx", out)
            body = m.request_history[0].json()
        assert body == {"SolutionName": "CRMWorx"}
        assert out.read_bytes() == _ZIP_BYTES
        assert info["output"] == str(out)
        assert info["bytes"] == len(_ZIP_BYTES)
        assert info["solution"] == "CRMWorx"
        assert info["action"] == "ExportTranslation"

    def test_export_missing_payload_raises(self, backend, tmp_path):
        from crm.core import translation
        with requests_mock.Mocker() as m:
            m.post(
                backend.url_for("solutions/Microsoft.Dynamics.CRM.ExportTranslation"),
                json={},
            )
            with pytest.raises(D365Error, match="ExportTranslationFile"):
                translation.export_translation(backend, "CRMWorx", tmp_path / "x.zip")

    def test_export_dry_run_previews_without_writing(self, tmp_path):
        from crm.core import translation
        profile = ConnectionProfile(
            name="testp", url="https://crm.contoso.local/contoso",
            domain="CONTOSO", username="alice", api_version="v9.2", verify_ssl=False,
        )
        dry = D365Backend(profile, password="pw", dry_run=True)
        out = tmp_path / "labels.zip"
        info = translation.export_translation(dry, "CRMWorx", out)
        assert info["_dry_run"] is True
        assert info["action"] == "ExportTranslation"
        assert not out.exists()


def _write_translations_zip(path):
    import zipfile
    with zipfile.ZipFile(path, "w") as zf:
        zf.writestr("CrmTranslations.xml", "<root/>")
        zf.writestr("[Content_Types].xml", "<Types/>")
    return path


class TestImportTranslation:
    def test_import_posts_zip_and_returns_job_id(self, backend, tmp_path):
        from crm.core import translation
        src = _write_translations_zip(tmp_path / "labels.zip")
        with requests_mock.Mocker() as m:
            m.post(backend.url_for("ImportTranslation"), status_code=204)
            info = translation.import_translation(backend, src)
            body = m.request_history[0].json()
        assert base64.b64decode(body["TranslationFile"]) == src.read_bytes()
        assert body["ImportJobId"] == info["import_job_id"]
        assert info["status"] == "succeeded"
        assert info["action"] == "ImportTranslation"

    def test_import_rejects_non_zip_before_any_http(self, backend, tmp_path):
        from crm.core import translation
        src = tmp_path / "CrmTranslations.xml"
        src.write_text("<root/>", encoding="utf-8")
        with requests_mock.Mocker() as m:
            with pytest.raises(D365Error, match="not a zip"):
                translation.import_translation(backend, src)
            assert m.request_history == []

    def test_import_missing_file_raises(self, backend, tmp_path):
        from crm.core import translation
        with pytest.raises(D365Error, match="not found"):
            translation.import_translation(backend, tmp_path / "nope.zip")

    def test_import_dry_run_previews(self, tmp_path):
        from crm.core import translation
        profile = ConnectionProfile(
            name="testp", url="https://crm.contoso.local/contoso",
            domain="CONTOSO", username="alice", api_version="v9.2", verify_ssl=False,
        )
        dry = D365Backend(profile, password="pw", dry_run=True)
        src = _write_translations_zip(tmp_path / "labels.zip")
        info = translation.import_translation(dry, src)
        assert info["_dry_run"] is True
        assert info["action"] == "ImportTranslation"
        assert info["import_job_id"]


# ── CLI commands ────────────────────────────────────────────────────────────

from click.testing import CliRunner  # noqa: E402


def _seed_profile(tmp_path, monkeypatch):
    """Isolate CRM_HOME and seed an NTLM profile + plaintext secret named 't'."""
    monkeypatch.setenv("CRM_HOME", str(tmp_path / ".crm"))
    monkeypatch.setenv("CRM_DOTENV", str(tmp_path / "noop.env"))
    from crm.core import session as session_mod
    session_mod.save_profile(ConnectionProfile(
        name="t", url="https://crm.contoso.local/contoso",
        domain="CONTOSO", username="alice"))
    session_mod.save_profile_secret_plaintext("t", "pw")


class TestTranslationCommands:
    def test_export_command(self, monkeypatch, tmp_path):
        _seed_profile(tmp_path, monkeypatch)
        from crm.commands import translation as tr_cmd
        captured = {}
        monkeypatch.setattr(
            tr_cmd.translation_mod, "export_translation",
            lambda backend, solution, output, **kw:
                captured.update(solution=solution, output=output, **kw)
                or {"output": str(output), "bytes": 1, "solution": solution,
                    "action": "ExportTranslation"},
        )
        from crm.cli import cli
        result = CliRunner().invoke(cli, [
            "--profile", "t", "translation", "export",
            "--solution", "CRMWorx", "-o", str(tmp_path / "labels.zip"),
        ])
        assert result.exit_code == 0, result.output
        assert captured["solution"] == "CRMWorx"

    def test_import_command_requires_confirmation(self, monkeypatch, tmp_path):
        _seed_profile(tmp_path, monkeypatch)
        src = _write_translations_zip(tmp_path / "labels.zip")
        from crm.cli import cli
        result = CliRunner().invoke(
            cli, ["--profile", "t", "translation", "import", str(src)],
        )
        assert result.exit_code == 1
        assert "aborted by user" in result.output

    def test_import_command_with_yes_runs_and_hints_publish(self, monkeypatch, tmp_path):
        _seed_profile(tmp_path, monkeypatch)
        src = _write_translations_zip(tmp_path / "labels.zip")
        from crm.commands import translation as tr_cmd
        captured = {}
        monkeypatch.setattr(
            tr_cmd.translation_mod, "import_translation",
            lambda backend, zip_path, **kw:
                captured.update(zip_path=zip_path, **kw)
                or {"import_job_id": "11111111-2222-3333-4444-555555555555",
                    "status": "succeeded", "action": "ImportTranslation"},
        )
        from crm.cli import cli
        result = CliRunner().invoke(cli, [
            "--profile", "t", "--json", "translation", "import", str(src), "--yes",
        ])
        assert result.exit_code == 0, result.output
        assert captured["zip_path"] == str(src)
        import json
        envelope = json.loads(result.stdout)
        assert envelope["ok"] is True
        assert envelope["data"]["import_job_id"] == "11111111-2222-3333-4444-555555555555"
        warnings = " ".join(envelope["meta"]["warnings"])
        assert "publish" in warnings.lower()
