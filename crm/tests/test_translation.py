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

    def test_export_invalid_base64_raises(self, backend, tmp_path):
        from crm.core import translation

        with requests_mock.Mocker() as m:
            m.post(
                backend.url_for("solutions/Microsoft.Dynamics.CRM.ExportTranslation"),
                json={"ExportTranslationFile": "AAAA!!!!"},
            )
            with pytest.raises(D365Error, match="not valid base64"):
                translation.export_translation(backend, "CRMWorx", tmp_path / "x.zip")

    def test_export_dry_run_previews_without_writing(self, tmp_path):
        from crm.core import translation

        profile = ConnectionProfile(
            name="testp",
            url="https://crm.contoso.local/contoso",
            domain="CONTOSO",
            username="alice",
            api_version="v9.2",
            verify_ssl=False,
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
            name="testp",
            url="https://crm.contoso.local/contoso",
            domain="CONTOSO",
            username="alice",
            api_version="v9.2",
            verify_ssl=False,
        )
        dry = D365Backend(profile, password="pw", dry_run=True)
        src = _write_translations_zip(tmp_path / "labels.zip")
        info = translation.import_translation(dry, src)
        assert info["_dry_run"] is True
        assert info["action"] == "ImportTranslation"
        assert info["import_job_id"]


# ── ExportTranslation bytes + CrmTranslations.xml parsing (issue #942) ────────

# A CrmTranslations.xml "Localized Labels" worksheet mirroring the real
# SpreadsheetML ExportTranslation emits: header row `Entity name | Object ID |
# Object Column Name | <LCID>...`, then one row per (object, column) with a text
# cell per language column. Form element labels are the lowercase `displayname`
# rows; attribute labels use capital `DisplayName`. Bilingual here (1033 + 1036)
# to prove the per-language map. Structure captured from a live agent-cloud export.
_LOCALIZED_LABELS_XML = """<?xml version="1.0"?>
<Workbook xmlns="urn:schemas-microsoft-com:office:spreadsheet"
          xmlns:ss="urn:schemas-microsoft-com:office:spreadsheet">
 <Worksheet ss:Name="Information"><Table>
  <Row><Cell><Data ss:Type="String">Base language ID:</Data></Cell>
       <Cell><Data ss:Type="Number">1033</Data></Cell></Row>
 </Table></Worksheet>
 <Worksheet ss:Name="Localized Labels"><Table>
  <Row>
   <Cell><Data ss:Type="String">Entity name</Data></Cell>
   <Cell><Data ss:Type="String">Object ID</Data></Cell>
   <Cell><Data ss:Type="String">Object Column Name</Data></Cell>
   <Cell><Data ss:Type="Number">1033</Data></Cell>
   <Cell><Data ss:Type="Number">1036</Data></Cell>
  </Row>
  <Row>
   <Cell><Data ss:Type="String">new_project</Data></Cell>
   <Cell><Data ss:Type="String">aaaa1111-0000-0000-0000-000000000001</Data></Cell>
   <Cell><Data ss:Type="String">displayname</Data></Cell>
   <Cell><Data ss:Type="String">General</Data></Cell>
   <Cell><Data ss:Type="String">Général</Data></Cell>
  </Row>
  <Row>
   <Cell><Data ss:Type="String">new_project</Data></Cell>
   <Cell><Data ss:Type="String">bbbb2222-0000-0000-0000-000000000002</Data></Cell>
   <Cell><Data ss:Type="String">displayname</Data></Cell>
   <Cell><Data ss:Type="String">Details</Data></Cell>
   <Cell><Data ss:Type="String"></Data></Cell>
  </Row>
  <Row>
   <Cell><Data ss:Type="String">new_project</Data></Cell>
   <Cell><Data ss:Type="String">cccc3333-0000-0000-0000-000000000003</Data></Cell>
   <Cell><Data ss:Type="String">DisplayName</Data></Cell>
   <Cell><Data ss:Type="String">Name</Data></Cell>
   <Cell><Data ss:Type="String">Nom</Data></Cell>
  </Row>
 </Table></Worksheet>
</Workbook>"""


def _labels_zip_bytes(xml: str = _LOCALIZED_LABELS_XML) -> bytes:
    import io
    import zipfile

    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w") as zf:
        zf.writestr("CrmTranslations.xml", xml)
        zf.writestr("[Content_Types].xml", "<Types/>")
    return buf.getvalue()


class TestExportTranslationBytes:
    def test_returns_decoded_zip_without_writing(self, backend):
        from crm.core import translation

        encoded = base64.b64encode(_ZIP_BYTES).decode("ascii")
        with requests_mock.Mocker() as m:
            m.post(
                backend.url_for("solutions/Microsoft.Dynamics.CRM.ExportTranslation"),
                json={"ExportTranslationFile": encoded},
            )
            data = translation.export_translation_bytes(backend, "CRMWorx")
            body = m.request_history[0].json()
        assert body == {"SolutionName": "CRMWorx"}
        assert data == _ZIP_BYTES

    def test_missing_payload_raises(self, backend):
        from crm.core import translation

        with requests_mock.Mocker() as m:
            m.post(
                backend.url_for("solutions/Microsoft.Dynamics.CRM.ExportTranslation"),
                json={},
            )
            with pytest.raises(D365Error, match="ExportTranslationFile"):
                translation.export_translation_bytes(backend, "CRMWorx")

    def test_non_string_payload_raises_d365error(self, backend):
        # A non-str/bytes payload makes base64.b64decode raise TypeError; it must
        # still surface as the typed envelope, not an uncaught traceback.
        from crm.core import translation

        with requests_mock.Mocker() as m:
            m.post(
                backend.url_for("solutions/Microsoft.Dynamics.CRM.ExportTranslation"),
                json={"ExportTranslationFile": 12345},
            )
            with pytest.raises(D365Error, match="not valid base64"):
                translation.export_translation_bytes(backend, "CRMWorx")


class TestParseLocalizedLabels:
    def test_language_codes_from_header(self):
        from crm.core import translation

        languages, _ = translation.parse_localized_labels(_labels_zip_bytes())
        assert languages == [1033, 1036]

    def test_maps_displayname_rows_by_object_id_across_languages(self):
        from crm.core import translation

        _, by_id = translation.parse_localized_labels(_labels_zip_bytes())
        # keyed by lowercased, brace-stripped object id
        assert by_id["aaaa1111-0000-0000-0000-000000000001"] == {
            "1033": "General",
            "1036": "Général",
        }

    def test_empty_language_cell_omitted_not_stored_blank(self):
        from crm.core import translation

        _, by_id = translation.parse_localized_labels(_labels_zip_bytes())
        # 'Details' has no French text → 1036 absent, not stored as ""
        assert by_id["bbbb2222-0000-0000-0000-000000000002"] == {"1033": "Details"}

    def test_capital_displayname_attribute_rows_also_captured(self):
        from crm.core import translation

        # case-insensitive on the column name, so attribute DisplayName rows are
        # captured too; their object ids never collide with form-label ids.
        _, by_id = translation.parse_localized_labels(_labels_zip_bytes())
        assert by_id["cccc3333-0000-0000-0000-000000000003"] == {"1033": "Name", "1036": "Nom"}

    def test_not_a_zip_raises_d365error(self):
        from crm.core import translation

        with pytest.raises(D365Error, match="not a valid zip"):
            translation.parse_localized_labels(b"this is not a zip")

    def test_missing_member_raises_d365error(self):
        import io
        import zipfile

        from crm.core import translation

        buf = io.BytesIO()
        with zipfile.ZipFile(buf, "w") as zf:
            zf.writestr("[Content_Types].xml", "<Types/>")
        with pytest.raises(D365Error, match="no CrmTranslations.xml"):
            translation.parse_localized_labels(buf.getvalue())

    def test_malformed_xml_raises_d365error_not_parseerror(self):
        # A malformed CrmTranslations.xml must surface as the typed envelope, not a
        # raw ElementTree.ParseError (which d365_errors would not absorb).
        from crm.core import translation

        with pytest.raises(D365Error, match="Could not parse CrmTranslations.xml"):
            translation.parse_localized_labels(_labels_zip_bytes("<Workbook><unclosed>"))


# ── CLI commands ────────────────────────────────────────────────────────────

from click.testing import CliRunner  # noqa: E402


def _seed_profile(tmp_path, monkeypatch):
    """Isolate CRM_HOME and seed an NTLM profile + plaintext secret named 't'."""
    monkeypatch.setenv("CRM_HOME", str(tmp_path / ".crm"))
    monkeypatch.setenv("CRM_DOTENV", str(tmp_path / "noop.env"))
    from crm.core import session as session_mod

    session_mod.save_profile(
        ConnectionProfile(
            name="t", url="https://crm.contoso.local/contoso", domain="CONTOSO", username="alice"
        )
    )
    session_mod.save_profile_secret_plaintext("t", "pw")


class TestTranslationCommands:
    def test_export_command(self, monkeypatch, tmp_path):
        _seed_profile(tmp_path, monkeypatch)
        from crm.commands import translation as tr_cmd

        captured = {}
        monkeypatch.setattr(
            tr_cmd.translation_mod,
            "export_translation",
            lambda backend, solution, output, **kw: (
                captured.update(solution=solution, output=output, **kw)
                or {
                    "output": str(output),
                    "bytes": 1,
                    "solution": solution,
                    "action": "ExportTranslation",
                }
            ),
        )
        from crm.cli import cli

        result = CliRunner().invoke(
            cli,
            [
                "--profile",
                "t",
                "translation",
                "export",
                "--solution",
                "CRMWorx",
                "-o",
                str(tmp_path / "labels.zip"),
            ],
        )
        assert result.exit_code == 0, result.output
        assert captured["solution"] == "CRMWorx"

    def test_export_command_oserror_emits_clean_envelope(self, monkeypatch, tmp_path):
        _seed_profile(tmp_path, monkeypatch)
        from crm.commands import translation as tr_cmd

        def _boom(backend, solution, output, **kw):
            raise OSError("disk full")

        monkeypatch.setattr(tr_cmd.translation_mod, "export_translation", _boom)
        from crm.cli import cli

        result = CliRunner().invoke(
            cli,
            [
                "--profile",
                "t",
                "--json",
                "translation",
                "export",
                "--solution",
                "CRMWorx",
                "-o",
                str(tmp_path / "labels.zip"),
            ],
        )
        assert result.exit_code == 1
        import json

        envelope = json.loads(result.stdout)
        assert envelope["ok"] is False
        assert "disk full" in envelope["error"]

    def test_import_command_requires_confirmation(self, monkeypatch, tmp_path):
        _seed_profile(tmp_path, monkeypatch)
        src = _write_translations_zip(tmp_path / "labels.zip")
        from crm.cli import cli

        # No TTY (CliRunner), no --yes → fail fast naming --yes rather than
        # blocking on a prompt (human-mode non-interactive path).
        result = CliRunner().invoke(
            cli,
            ["--profile", "t", "translation", "import", str(src)],
        )
        assert result.exit_code == 1
        assert "Pass --yes to continue" in result.output

    def test_import_command_with_publish_flag_calls_publish_all(self, monkeypatch, tmp_path):
        _seed_profile(tmp_path, monkeypatch)
        src = _write_translations_zip(tmp_path / "labels.zip")
        from crm.commands import translation as tr_cmd

        monkeypatch.setattr(
            tr_cmd.translation_mod,
            "import_translation",
            lambda backend, zip_path, **kw: {
                "import_job_id": "11111111-2222-3333-4444-555555555555",
                "status": "succeeded",
                "action": "ImportTranslation",
            },
        )
        published = {}
        import crm.core.solution as sol_core

        monkeypatch.setattr(
            sol_core,
            "publish_all",
            lambda backend: (
                published.update(called=True) or {"published": True, "action": "PublishAllXml"}
            ),
        )
        from crm.cli import cli

        result = CliRunner().invoke(
            cli,
            [
                "--profile",
                "t",
                "--json",
                "translation",
                "import",
                str(src),
                "--yes",
                "--publish",
            ],
        )
        assert result.exit_code == 0, result.output
        import json

        envelope = json.loads(result.stdout)
        assert envelope["ok"] is True
        assert envelope["data"]["publish"]["published"] is True
        assert published.get("called") is True
        warnings = envelope.get("meta", {}).get("warnings", [])
        assert not any("publish-all" in w for w in warnings)

    def test_import_command_with_publish_flag_dry_run_skips_publish(self, monkeypatch, tmp_path):
        _seed_profile(tmp_path, monkeypatch)
        src = _write_translations_zip(tmp_path / "labels.zip")
        from crm.commands import translation as tr_cmd

        monkeypatch.setattr(
            tr_cmd.translation_mod,
            "import_translation",
            lambda backend, zip_path, **kw: {
                "_dry_run": True,
                "import_job_id": "11111111-2222-3333-4444-555555555555",
                "action": "ImportTranslation",
            },
        )
        publish_called = []
        import crm.core.solution as sol_core

        monkeypatch.setattr(
            sol_core, "publish_all", lambda backend: publish_called.append(True) or {}
        )
        from crm.cli import cli

        result = CliRunner().invoke(
            cli,
            [
                "--profile",
                "t",
                "--json",
                "translation",
                "import",
                str(src),
                "--yes",
                "--publish",
            ],
        )
        assert result.exit_code == 0, result.output
        assert not publish_called, "publish_all must not be called under dry-run"
        import json

        envelope = json.loads(result.stdout)
        warnings = " ".join(envelope.get("meta", {}).get("warnings", []))
        assert "publish" in warnings.lower(), (
            "warning must still appear when --publish skipped due to dry-run"
        )

    def test_import_command_with_yes_runs_and_hints_publish(self, monkeypatch, tmp_path):
        _seed_profile(tmp_path, monkeypatch)
        src = _write_translations_zip(tmp_path / "labels.zip")
        from crm.commands import translation as tr_cmd

        captured = {}
        monkeypatch.setattr(
            tr_cmd.translation_mod,
            "import_translation",
            lambda backend, zip_path, **kw: (
                captured.update(zip_path=zip_path, **kw)
                or {
                    "import_job_id": "11111111-2222-3333-4444-555555555555",
                    "status": "succeeded",
                    "action": "ImportTranslation",
                }
            ),
        )
        from crm.cli import cli

        result = CliRunner().invoke(
            cli,
            [
                "--profile",
                "t",
                "--json",
                "translation",
                "import",
                str(src),
                "--yes",
            ],
        )
        assert result.exit_code == 0, result.output
        assert captured["zip_path"] == str(src)
        import json

        envelope = json.loads(result.stdout)
        assert envelope["ok"] is True
        assert envelope["data"]["import_job_id"] == "11111111-2222-3333-4444-555555555555"
        warnings = " ".join(envelope["meta"]["warnings"])
        assert "publish" in warnings.lower()
