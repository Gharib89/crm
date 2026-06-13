"""Command-level tests for `crm metadata export-spec` (#92).

Pattern mirrors `test_metadata_describe.py`: real D365Backend + requests_mock
for the underlying GETs, CliRunner for the command surface. Each test mocks ONLY
the endpoints its scenario touches (requests_mock raises NoMockAddress for any
unregistered endpoint, so over-fetching surfaces as a test failure).
"""
# pyright: basic

from __future__ import annotations

import json

import requests_mock
import yaml
from click.testing import CliRunner

from crm.cli import CLIContext, cli
from crm.core import apply as apply_mod


# ── URL helpers ────────────────────────────────────────────────────────────────

def _entity_url(backend) -> str:
    return backend.url_for("EntityDefinitions(LogicalName='new_project')")


def _attrs_url(backend) -> str:
    return backend.url_for("EntityDefinitions(LogicalName='new_project')/Attributes")


def _attr_url(backend, attr, entity="new_project") -> str:
    return backend.url_for(
        f"EntityDefinitions(LogicalName='{entity}')/Attributes(LogicalName='{attr}')"
    )


def _o2m_url(backend) -> str:
    return backend.url_for(
        "EntityDefinitions(LogicalName='new_project')/OneToManyRelationships"
    )


def _savedqueries_url(backend) -> str:
    return backend.url_for("savedqueries")


# ── fixture data ───────────────────────────────────────────────────────────────

def _label(text: str) -> dict:
    return {"UserLocalizedLabel": {"Label": text, "LanguageCode": 1033}}


def _shallow(logical: str, *, custom: bool = True) -> dict:
    return {"LogicalName": logical, "SchemaName": logical, "IsCustomAttribute": custom}


_ENTITY = {
    "LogicalName": "new_project",
    "SchemaName": "new_Project",
    "DisplayName": _label("Project"),
    "DisplayCollectionName": _label("Projects"),
    "OwnershipType": "UserOwned",
    "PrimaryNameAttribute": "new_name",
}


def _primary_info() -> dict:
    return {
        "SchemaName": "new_Name",
        "DisplayName": _label("Project Name"),
        "AttributeTypeName": {"Value": "StringType"},
        "RequiredLevel": {"Value": "ApplicationRequired"},
        "MaxLength": 200,
        "FormatName": {"Value": "Text"},
    }


def _string_info() -> dict:
    return {
        "SchemaName": "new_Code",
        "DisplayName": _label("Code"),
        "AttributeTypeName": {"Value": "StringType"},
        "RequiredLevel": {"Value": "None"},
        "MaxLength": 50,
        "FormatName": {"Value": "Text"},
    }


# ── helpers ────────────────────────────────────────────────────────────────────

def _stub(monkeypatch, backend) -> None:
    monkeypatch.setattr(CLIContext, "backend", lambda self: backend)


# ── tests ──────────────────────────────────────────────────────────────────────

class TestNoOutput:
    """Without -o: spec emitted under the standard JSON envelope."""

    def test_spec_under_data_with_entity(self, monkeypatch, backend):
        _stub(monkeypatch, backend)
        attrs = {"value": [_shallow("new_name"), _shallow("new_code")]}

        with requests_mock.Mocker() as m:
            m.get(_entity_url(backend), json=_ENTITY)
            m.get(_attrs_url(backend), json=attrs)
            m.get(_attr_url(backend, "new_name"), json=_primary_info())
            m.get(_attr_url(backend, "new_code"), json=_string_info())
            result = CliRunner().invoke(
                cli, ["--json", "metadata", "export-spec", "new_project"]
            )

        assert result.exit_code == 0, result.output
        env = json.loads(result.output)
        assert env["ok"] is True
        # Spec is under data — not wrapped in an extra envelope layer.
        spec = env["data"]
        assert "entities" in spec
        assert spec["entities"][0]["schema_name"] == "new_Project"
        assert spec["entities"][0]["display_name"] == "Project"

    def test_output_is_not_bare_spec(self, monkeypatch, backend):
        """Without -o, stdout is the full envelope (ok/data), NOT the bare spec."""
        _stub(monkeypatch, backend)
        attrs = {"value": [_shallow("new_name")]}

        with requests_mock.Mocker() as m:
            m.get(_entity_url(backend), json=_ENTITY)
            m.get(_attrs_url(backend), json=attrs)
            m.get(_attr_url(backend, "new_name"), json=_primary_info())
            result = CliRunner().invoke(
                cli, ["--json", "metadata", "export-spec", "new_project"]
            )

        assert result.exit_code == 0, result.output
        env = json.loads(result.output)
        # Top-level must be the envelope, not the spec.
        assert "ok" in env
        assert "data" in env
        # The bare spec is NOT at the top level.
        assert "entities" not in env


class TestOutputFile:
    """With -o: YAML written to file; summary emitted; file is apply-consumable."""

    def test_round_trip_validate_spec(self, monkeypatch, backend, tmp_path):
        """Acceptance test: written file passes validate_spec and is the bare spec."""
        _stub(monkeypatch, backend)
        attrs = {"value": [_shallow("new_name"), _shallow("new_code")]}
        out_file = tmp_path / "spec.yaml"

        with requests_mock.Mocker() as m:
            m.get(_entity_url(backend), json=_ENTITY)
            m.get(_attrs_url(backend), json=attrs)
            m.get(_attr_url(backend, "new_name"), json=_primary_info())
            m.get(_attr_url(backend, "new_code"), json=_string_info())
            result = CliRunner().invoke(
                cli, ["--json", "metadata", "export-spec", "new_project",
                      "-o", str(out_file)]
            )

        assert result.exit_code == 0, result.output
        env = json.loads(result.output)
        assert env["ok"] is True

        # The file must exist and be the BARE spec (not the envelope).
        assert out_file.exists()
        loaded = yaml.safe_load(out_file.read_text(encoding="utf-8"))
        assert "ok" not in loaded        # NOT the envelope
        assert "entities" in loaded      # bare spec
        assert loaded["entities"][0]["schema_name"] == "new_Project"

        # The key acceptance gate: validate_spec must not raise.
        apply_mod.validate_spec(loaded)

    def test_summary_counts(self, monkeypatch, backend, tmp_path):
        """Success summary carries entity/attribute/optionset counts."""
        _stub(monkeypatch, backend)
        attrs = {"value": [_shallow("new_name"), _shallow("new_code")]}
        out_file = tmp_path / "spec.yaml"

        with requests_mock.Mocker() as m:
            m.get(_entity_url(backend), json=_ENTITY)
            m.get(_attrs_url(backend), json=attrs)
            m.get(_attr_url(backend, "new_name"), json=_primary_info())
            m.get(_attr_url(backend, "new_code"), json=_string_info())
            result = CliRunner().invoke(
                cli, ["--json", "metadata", "export-spec", "new_project",
                      "-o", str(out_file)]
            )

        assert result.exit_code == 0, result.output
        env = json.loads(result.output)
        data = env["data"]
        assert data["path"] == str(out_file)
        assert data["entities"] == 1
        assert data["attributes"] == 1    # new_code only (new_name = primary_attr)
        assert data["relationships"] == 0
        assert data["views"] == 0
        assert data["optionsets"] == 0


class TestWithViewsAndRelationships:
    """--with-views / --with-relationships flags propagate to the core."""

    def test_with_views(self, monkeypatch, backend, tmp_path):
        _stub(monkeypatch, backend)
        attrs = {"value": [_shallow("new_name")]}
        layout = (
            '<grid name="resultset" object="10042" jump="new_name" select="1">'
            '<row name="result" id="new_projectid">'
            '<cell name="new_name" width="200" />'
            '</row></grid>'
        )
        fetch = (
            '<fetch version="1.0" output-format="xml-platform" mapping="logical">'
            '<entity name="new_project">'
            '<attribute name="new_projectid" />'
            '<attribute name="new_name" />'
            '<order attribute="new_name" descending="false" />'
            '</entity></fetch>'
        )
        savedqueries = {"value": [{
            "name": "Active Projects",
            "layoutxml": layout,
            "fetchxml": fetch,
            "isdefault": True,
        }]}
        out_file = tmp_path / "spec.yaml"

        with requests_mock.Mocker() as m:
            m.get(_entity_url(backend), json=_ENTITY)
            m.get(_attrs_url(backend), json=attrs)
            m.get(_attr_url(backend, "new_name"), json=_primary_info())
            m.get(_savedqueries_url(backend), json=savedqueries)
            result = CliRunner().invoke(
                cli, ["--json", "metadata", "export-spec", "new_project",
                      "--with-views", "-o", str(out_file)]
            )

        assert result.exit_code == 0, result.output
        env = json.loads(result.output)
        assert env["ok"] is True
        assert env["data"]["views"] == 1

        loaded = yaml.safe_load(out_file.read_text(encoding="utf-8"))
        assert "views" in loaded["entities"][0]
        assert loaded["entities"][0]["views"][0]["name"] == "Active Projects"
        apply_mod.validate_spec(loaded)

    def test_with_relationships(self, monkeypatch, backend, tmp_path):
        _stub(monkeypatch, backend)
        attrs = {"value": [_shallow("new_name")]}
        o2m = {"value": [{
            "SchemaName": "new_project_new_task",
            "ReferencedEntity": "new_project",
            "ReferencingEntity": "new_task",
            "ReferencingAttribute": "new_projectid",
            "IsCustomRelationship": True,
            "CascadeConfiguration": {"Assign": "NoCascade", "Delete": "RemoveLink"},
            "AssociatedMenuConfiguration": {"Behavior": "UseCollectionName"},
        }]}
        rel_attr = {
            "LogicalName": "new_projectid",
            "DisplayName": _label("Project"),
            "RequiredLevel": {"Value": "None"},
        }
        out_file = tmp_path / "spec.yaml"

        with requests_mock.Mocker() as m:
            m.get(_entity_url(backend), json=_ENTITY)
            m.get(_attrs_url(backend), json=attrs)
            m.get(_attr_url(backend, "new_name"), json=_primary_info())
            m.get(_o2m_url(backend), json=o2m)
            m.get(_attr_url(backend, "new_projectid", entity="new_task"), json=rel_attr)
            result = CliRunner().invoke(
                cli, ["--json", "metadata", "export-spec", "new_project",
                      "--with-relationships", "-o", str(out_file)]
            )

        assert result.exit_code == 0, result.output
        env = json.loads(result.output)
        assert env["ok"] is True
        assert env["data"]["relationships"] == 1

        loaded = yaml.safe_load(out_file.read_text(encoding="utf-8"))
        assert "relationships" in loaded["entities"][0]
        assert loaded["entities"][0]["relationships"][0]["schema_name"] == "new_project_new_task"
        apply_mod.validate_spec(loaded)


class TestFileWriteOSError:
    """OSError on file write → clean error envelope, not a traceback."""

    def test_unwritable_path_emits_error_envelope(self, monkeypatch, backend, tmp_path):
        _stub(monkeypatch, backend)
        attrs = {"value": [_shallow("new_name")]}
        # Point at a path whose parent directory does not exist.
        bad_path = str(tmp_path / "nonexistent_dir" / "spec.yaml")

        with requests_mock.Mocker() as m:
            m.get(_entity_url(backend), json=_ENTITY)
            m.get(_attrs_url(backend), json=attrs)
            m.get(_attr_url(backend, "new_name"), json=_primary_info())
            result = CliRunner().invoke(
                cli, ["--json", "metadata", "export-spec", "new_project",
                      "-o", bad_path]
            )

        assert result.exit_code == 1, result.output  # exit 1 (emit false, not crash)
        env = json.loads(result.output)
        assert env["ok"] is False
        assert "error" in env
        # No traceback in output.
        assert "Traceback" not in result.output

    def test_oserror_via_monkeypatch(self, monkeypatch, backend, tmp_path):
        """Simulate OSError by monkeypatching open to raise it."""
        _stub(monkeypatch, backend)
        attrs = {"value": [_shallow("new_name")]}
        out_file = tmp_path / "spec.yaml"

        _real_open = open  # noqa: WPS421

        def _raising_open(path, *args, **kwargs):
            if str(path) == str(out_file):
                raise OSError("disk full")
            return _real_open(path, *args, **kwargs)

        monkeypatch.setattr("builtins.open", _raising_open)

        with requests_mock.Mocker() as m:
            m.get(_entity_url(backend), json=_ENTITY)
            m.get(_attrs_url(backend), json=attrs)
            m.get(_attr_url(backend, "new_name"), json=_primary_info())
            result = CliRunner().invoke(
                cli, ["--json", "metadata", "export-spec", "new_project",
                      "-o", str(out_file)]
            )

        assert result.exit_code == 1, result.output
        env = json.loads(result.output)
        assert env["ok"] is False
        assert "disk full" in env.get("error", "")


class TestMissingEntity:
    """A 404 on entity_info bubbles up as a clean D365Error envelope."""

    def test_missing_entity_emits_error_envelope(self, monkeypatch, backend):
        _stub(monkeypatch, backend)

        with requests_mock.Mocker() as m:
            m.get(_entity_url(backend), status_code=404,
                  json={"error": {"code": "0x80040217", "message": "entity not found"}})
            result = CliRunner().invoke(
                cli, ["--json", "metadata", "export-spec", "new_project"]
            )

        assert result.exit_code == 1, result.output
        env = json.loads(result.output)
        assert env["ok"] is False
        assert "error" in env
        assert "Traceback" not in result.output


def _pick_cast_url(backend, attr, entity="new_project") -> str:
    return backend.url_for(
        f"EntityDefinitions(LogicalName='{entity}')/Attributes(LogicalName='{attr}')"
        "/Microsoft.Dynamics.CRM.PicklistAttributeMetadata"
    )


def _local_pick_info() -> dict:
    return {
        "SchemaName": "new_Priority",
        "DisplayName": _label("Priority"),
        "AttributeTypeName": {"Value": "PicklistType"},
        "RequiredLevel": {"Value": "None"},
    }


class TestExportSpecWarningsCommand:
    def test_export_spec_emits_meta_warnings_for_dropped_picklist(
        self, monkeypatch, backend
    ):
        # A custom picklist whose cast 403s -> dropped -> warning in meta.warnings.
        _stub(monkeypatch, backend)
        attrs = {"value": [_shallow("new_name"), _shallow("new_priority")]}
        runner = CliRunner()
        with requests_mock.Mocker() as m:
            m.get(_entity_url(backend), json=_ENTITY)
            m.get(_attrs_url(backend), json=attrs)
            m.get(_attr_url(backend, "new_name"), json=_primary_info())
            m.get(_attr_url(backend, "new_priority"), json=_local_pick_info())
            m.get(_pick_cast_url(backend, "new_priority"), status_code=403, json={
                "error": {"code": "0x0", "message": "forbidden"}
            })
            result = runner.invoke(cli, ["--json", "metadata", "export-spec", "new_project"])

        assert result.exit_code == 0
        payload = json.loads(result.output)
        warnings = payload["meta"]["warnings"]
        assert any("new_priority" in w for w in warnings)
