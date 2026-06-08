# pyright: basic
import xml.etree.ElementTree as ET
from click.testing import CliRunner
import pytest
from crm.cli import cli
from crm.core import ribbon as ribbon_mod


def test_ribbon_export_prints_xml(monkeypatch):
    xml = "<RibbonDiffXml><CustomActions/></RibbonDiffXml>"
    monkeypatch.setattr(
        ribbon_mod, "retrieve_entity_ribbon",
        lambda backend, entity: ET.fromstring(xml))
    # avoid building a real backend
    monkeypatch.setattr("crm.cli.CLIContext.backend", lambda self: object())
    res = CliRunner().invoke(cli, ["ribbon", "export", "cwx_ticket"])
    assert res.exit_code == 0, res.output
    assert "RibbonDiffXml" in res.output


def test_ribbon_list_shows_custom_buttons(monkeypatch):
    cust = ("<ImportExportXml><Entities><Entity><Name>cwx_ticket</Name>"
            "<RibbonDiffXml><CustomActions>"
            "<CustomAction Id='cwx_ticket.form.Validate.CustomAction' "
            "Location='Mscrm.Form.cwx_ticket.MainTab.Save.Controls._children'>"
            "<CommandUIDefinition><Button Id='b' "
            "Command='cwx_ticket.form.Validate.Command' LabelText='Validate'/>"
            "</CommandUIDefinition></CustomAction></CustomActions>"
            "<CommandDefinitions><CommandDefinition "
            "Id='cwx_ticket.form.Validate.Command'><Actions>"
            "<JavaScriptFunction Library='$webresource:cwx_/x.js' FunctionName='ns.fn'/>"
            "</Actions></CommandDefinition></CommandDefinitions>"
            "</RibbonDiffXml></Entity></Entities></ImportExportXml>")

    def fake_export(backend, name, output_path, **kw):
        import zipfile as zf
        with zf.ZipFile(output_path, "w") as z:
            z.writestr("customizations.xml", cust)
        return {"output": str(output_path)}

    monkeypatch.setattr(ribbon_mod, "export_solution", fake_export)
    monkeypatch.setattr("crm.cli.CLIContext.backend", lambda self: object())
    res = CliRunner().invoke(
        cli, ["--json", "ribbon", "list", "cwx_ticket", "--solution", "MySol"])
    assert res.exit_code == 0, res.output
    assert "cwx_ticket.form.Validate.CustomAction" in res.output
    assert "Validate" in res.output


def test_ribbon_add_button_applies(monkeypatch):
    calls: dict[str, object] = {}

    def fake_apply(backend, *, solution, entity, mutate, **kw):
        # exercise the mutate callback against a minimal solution root
        root = ET.fromstring(
            "<ImportExportXml><Entities><Entity><Name>cwx_ticket</Name>"
            "</Entity></Entities></ImportExportXml>")
        mutate(root)
        calls["solution"] = solution
        calls["entity"] = entity
        calls["has_button"] = (
            root.find(".//Button[@LabelText='Validate']") is not None)
        return {"status": "succeeded"}

    monkeypatch.setattr(ribbon_mod, "apply_ribbon_change", fake_apply)
    monkeypatch.setattr(ribbon_mod, "resolve_webresource_id",
                        lambda backend, name: "guid-1")
    monkeypatch.setattr("crm.cli.CLIContext.backend", lambda self: object())
    res = CliRunner().invoke(cli, [
        "ribbon", "add-button", "cwx_ticket", "--solution", "MySol",
        "--label", "Validate", "--location", "form",
        "--webresource", "cwx_/scripts/x.js", "--function", "ns.fn",
        "--param", "PrimaryControl"])
    assert res.exit_code == 0, res.output
    assert calls["solution"] == "MySol"
    assert calls["has_button"] is True


def test_ribbon_add_button_rejects_missing_webresource(monkeypatch):
    def boom(backend, name):
        raise ValueError(f"web resource {name!r} not found")
    monkeypatch.setattr(ribbon_mod, "resolve_webresource_id", boom)
    monkeypatch.setattr("crm.cli.CLIContext.backend", lambda self: object())
    res = CliRunner().invoke(cli, [
        "--json", "ribbon", "add-button", "cwx_ticket", "--solution", "MySol",
        "--label", "Validate", "--location", "form",
        "--webresource", "cwx_/missing.js", "--function", "ns.fn",
        "--param", "PrimaryControl"])
    assert res.exit_code == 1
    assert "not found" in res.output


def test_ribbon_remove_deletes_button(monkeypatch):
    cust = ("<ImportExportXml><Entities><Entity><Name>cwx_ticket</Name>"
            "<RibbonDiffXml><CustomActions>"
            "<CustomAction Id='cwx_ticket.form.Validate.CustomAction'>"
            "<CommandUIDefinition><Button Id='b' "
            "Command='cwx_ticket.form.Validate.Command' LabelText='Validate'/>"
            "</CommandUIDefinition></CustomAction></CustomActions>"
            "<CommandDefinitions/></RibbonDiffXml></Entity></Entities></ImportExportXml>")
    captured: dict[str, object] = {}

    def fake_apply(backend, *, solution, entity, mutate, **kw):
        root = ET.fromstring(cust)
        mutate(root)
        captured["remaining"] = len(root.findall(".//CustomAction"))
        return {"status": "succeeded"}

    monkeypatch.setattr(ribbon_mod, "apply_ribbon_change", fake_apply)
    monkeypatch.setattr("crm.cli.CLIContext.backend", lambda self: object())
    res = CliRunner().invoke(cli, [
        "ribbon", "remove", "cwx_ticket", "--solution", "MySol",
        "--button-id", "cwx_ticket.form.Validate.CustomAction", "--yes"])
    assert res.exit_code == 0, res.output
    assert captured["remaining"] == 0


def test_ribbon_remove_unknown_button_errors(monkeypatch):
    cust = ("<ImportExportXml><Entities><Entity><Name>cwx_ticket</Name>"
            "<RibbonDiffXml><CustomActions/><CommandDefinitions/></RibbonDiffXml>"
            "</Entity></Entities></ImportExportXml>")

    def fake_apply(backend, *, solution, entity, mutate, **kw):
        mutate(ET.fromstring(cust))  # mutate raises -> propagate
        return {"status": "succeeded"}

    monkeypatch.setattr(ribbon_mod, "apply_ribbon_change", fake_apply)
    monkeypatch.setattr("crm.cli.CLIContext.backend", lambda self: object())
    res = CliRunner().invoke(cli, [
        "--json", "ribbon", "remove", "cwx_ticket", "--solution", "MySol",
        "--button-id", "does.not.exist", "--yes"])
    assert res.exit_code == 1
    assert "not found" in res.output
