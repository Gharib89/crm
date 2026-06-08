# crm/tests/test_ribbon_cmd.py
import base64
import io
import zipfile
import xml.etree.ElementTree as ET
from click.testing import CliRunner
import pytest
from crm.cli import cli
from crm.core import ribbon as ribbon_mod


def _compressed(diff_xml: str) -> str:
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w") as zf:
        zf.writestr("RibbonXml.xml", diff_xml)
        zf.writestr("[Content_Types].xml", "<Types/>")
    return base64.b64encode(buf.getvalue()).decode("ascii")


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


def test_ribbon_list_shows_custom_buttons(monkeypatch, tmp_path):
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
