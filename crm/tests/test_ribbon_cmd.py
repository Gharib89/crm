# pyright: basic
import json
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


def test_ribbon_export_application_prints_xml(monkeypatch):
    xml = "<RibbonDiffXml><CustomActions/></RibbonDiffXml>"
    called = {}

    def _fake_app(backend):
        called["app"] = True
        return ET.fromstring(xml)

    monkeypatch.setattr(ribbon_mod, "retrieve_application_ribbon", _fake_app)
    monkeypatch.setattr("crm.cli.CLIContext.backend", lambda self: object())
    res = CliRunner().invoke(cli, ["--json", "ribbon", "export", "--application"])
    assert res.exit_code == 0, res.output
    assert called.get("app") is True
    data = json.loads(res.output)
    assert data["data"]["application"] is True
    assert "RibbonDiffXml" in data["data"]["ribbonxml"]


def test_ribbon_export_requires_entity_or_application(monkeypatch):
    monkeypatch.setattr("crm.cli.CLIContext.backend", lambda self: object())
    res = CliRunner().invoke(cli, ["--json", "ribbon", "export"])
    # Invalid arg combination is a usage error (exit 2, ADR 0001).
    assert res.exit_code == 2
    assert "application" in res.output.lower() or "entity" in res.output.lower()


def test_ribbon_export_rejects_entity_with_application(monkeypatch):
    monkeypatch.setattr("crm.cli.CLIContext.backend", lambda self: object())
    res = CliRunner().invoke(
        cli, ["--json", "ribbon", "export", "cwx_ticket", "--application"])
    assert res.exit_code == 2


def test_ribbon_export_json_no_output_emits_envelope(monkeypatch):
    xml = "<RibbonDiffXml><CustomActions/></RibbonDiffXml>"
    monkeypatch.setattr(
        ribbon_mod, "retrieve_entity_ribbon",
        lambda backend, entity: ET.fromstring(xml))
    monkeypatch.setattr("crm.cli.CLIContext.backend", lambda self: object())
    res = CliRunner().invoke(cli, ["--json", "ribbon", "export", "cwx_ticket"])
    assert res.exit_code == 0, res.output
    data = json.loads(res.output)
    assert data["ok"] is True
    assert data["data"]["entity"] == "cwx_ticket"
    assert "RibbonDiffXml" in data["data"]["ribbonxml"]


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


_COMPOSED = (
    "<RibbonDefinitions><RibbonDefinition><Tabs><Tab><Groups><Group><Controls>"
    "<Button Id='Mscrm.HomepageGrid.account.Deactivate' "
    "Command='Mscrm.HomepageGrid.Deactivate' TemplateAlias='o2'/>"
    "</Controls></Group></Groups></Tab></Tabs></RibbonDefinition></RibbonDefinitions>")


def _patch_apply_capturing(monkeypatch, captured):
    def fake_apply(backend, *, solution, entity, mutate, publish=True, **kw):
        root = ET.fromstring(
            "<ImportExportXml><Entities><Entity><Name>account</Name>"
            "</Entity></Entities></ImportExportXml>")
        mutate(root)
        captured["root"] = root
        captured["solution"] = solution
        captured["publish"] = publish
        return {"status": "succeeded"}
    monkeypatch.setattr(ribbon_mod, "apply_ribbon_change", fake_apply)
    monkeypatch.setattr(ribbon_mod, "retrieve_entity_ribbon",
                        lambda backend, entity: ET.fromstring(_COMPOSED))
    monkeypatch.setattr("crm.cli.CLIContext.backend", lambda self: object())


def test_ribbon_hide_button_display_rule_overrides_command(monkeypatch):
    captured: dict[str, object] = {}
    _patch_apply_capturing(monkeypatch, captured)
    res = CliRunner().invoke(cli, [
        "--json", "ribbon", "hide-button", "account", "--solution", "MySol",
        "--target-id", "Mscrm.HomepageGrid.account.Deactivate"])
    assert res.exit_code == 0, res.output
    root = captured["root"]
    assert isinstance(root, ET.Element)
    cdef = root.find(".//CommandDefinition[@Id='Mscrm.HomepageGrid.Deactivate']")
    assert cdef is not None
    rule_ids = [r.get("Id") for r in cdef.findall("DisplayRules/DisplayRule")]
    assert rule_ids == ["Mscrm.HideOnModern", "Mscrm.ShowOnlyOnModern"]
    data = json.loads(res.output)
    assert data["ok"] is True
    # unsupported-OOB-reuse warning is emitted
    warnings = data.get("meta", {}).get("warnings") or []
    assert any("unsupported" in w.lower() for w in warnings)


def test_ribbon_hide_button_rejects_unresolved_target(monkeypatch):
    captured: dict[str, object] = {}
    _patch_apply_capturing(monkeypatch, captured)
    res = CliRunner().invoke(cli, [
        "--json", "ribbon", "hide-button", "account", "--solution", "MySol",
        "--target-id", "Mscrm.Typo.NotARealButton"])
    assert res.exit_code == 1
    assert "not found" in res.output.lower() or "resolve" in res.output.lower()
    # never reached apply (no silent no-op)
    assert "root" not in captured


def test_ribbon_hide_button_hide_action_requires_confirm(monkeypatch):
    captured: dict[str, object] = {}
    _patch_apply_capturing(monkeypatch, captured)
    res = CliRunner().invoke(cli, [
        "--json", "ribbon", "hide-button", "account", "--solution", "MySol",
        "--target-id", "Mscrm.HomepageGrid.account.Deactivate",
        "--method", "hide-action"], input="n\n")
    assert res.exit_code == 1
    assert "root" not in captured  # aborted before mutating


def test_ribbon_hide_button_hide_action_emits_hidecustomaction(monkeypatch):
    captured: dict[str, object] = {}
    _patch_apply_capturing(monkeypatch, captured)
    res = CliRunner().invoke(cli, [
        "ribbon", "hide-button", "account", "--solution", "MySol",
        "--target-id", "Mscrm.HomepageGrid.account.Deactivate",
        "--method", "hide-action", "--yes"])
    assert res.exit_code == 0, res.output
    root = captured["root"]
    assert isinstance(root, ET.Element)
    hide = root.find(".//HideCustomAction")
    assert hide is not None
    assert hide.get("Location") == "Mscrm.HomepageGrid.account.Deactivate"


def test_ribbon_hide_button_no_publish_passes_through(monkeypatch):
    captured: dict[str, object] = {}
    _patch_apply_capturing(monkeypatch, captured)
    res = CliRunner().invoke(cli, [
        "ribbon", "hide-button", "account", "--solution", "MySol",
        "--target-id", "Mscrm.HomepageGrid.account.Deactivate", "--no-publish"])
    assert res.exit_code == 0, res.output
    assert captured["publish"] is False


def test_ribbon_hide_button_dry_run_does_not_import(monkeypatch):
    """--dry-run validates the target and previews via the export short-circuit in
    apply_ribbon_change, without importing/publishing (same as add-button/remove)."""
    imported: list[str] = []

    def fake_export(backend, name, output_path, **kw):
        return {"_dry_run": True, "would_export": name}

    monkeypatch.setattr(ribbon_mod, "export_solution", fake_export)
    monkeypatch.setattr(ribbon_mod, "import_solution",
                        lambda *a, **k: imported.append("x"))
    monkeypatch.setattr(ribbon_mod, "publish_all", lambda *a, **k: None)
    monkeypatch.setattr(ribbon_mod, "retrieve_entity_ribbon",
                        lambda backend, entity: ET.fromstring(_COMPOSED))
    monkeypatch.setattr("crm.cli.CLIContext.backend", lambda self: object())
    res = CliRunner().invoke(cli, [
        "--json", "--dry-run", "ribbon", "hide-button", "account",
        "--solution", "MySol",
        "--target-id", "Mscrm.HomepageGrid.account.Deactivate"])
    assert res.exit_code == 0, res.output
    assert imported == []  # never imported under --dry-run
_CUST_WITH_COMMAND = (
    "<ImportExportXml><Entities><Entity><Name>cwx_ticket</Name>"
    "<RibbonDiffXml><CustomActions/><CommandDefinitions>"
    "<CommandDefinition Id='cwx_ticket.form.Validate.Command'>"
    "<EnableRules/><DisplayRules/><Actions/></CommandDefinition>"
    "</CommandDefinitions><RuleDefinitions/></RibbonDiffXml>"
    "</Entity></Entities></ImportExportXml>")


def test_ribbon_set_rules_applies(monkeypatch):
    captured: dict[str, object] = {}

    def fake_apply(backend, *, solution, entity, mutate, **kw):
        root = ET.fromstring(_CUST_WITH_COMMAND)
        mutate(root)
        cdef = root.find(".//CommandDefinition[@Id='cwx_ticket.form.Validate.Command']")
        assert cdef is not None
        captured["enable"] = [e.get("Id") for e in cdef.findall("EnableRules/EnableRule")]
        return {"status": "succeeded"}

    monkeypatch.setattr(ribbon_mod, "apply_ribbon_change", fake_apply)
    monkeypatch.setattr("crm.cli.CLIContext.backend", lambda self: object())
    res = CliRunner().invoke(cli, [
        "--json", "ribbon", "set-rules", "cwx_ticket", "--solution", "MySol",
        "--command-id", "cwx_ticket.form.Validate.Command",
        "--enable-rule", "Mscrm.SelectionCountExactlyOne",
        "--enable-rule", "Mscrm.ShowOnGrid"])
    assert res.exit_code == 0, res.output
    assert captured["enable"] == ["Mscrm.SelectionCountExactlyOne", "Mscrm.ShowOnGrid"]


def test_ribbon_set_rules_requires_a_rule(monkeypatch):
    monkeypatch.setattr("crm.cli.CLIContext.backend", lambda self: object())
    res = CliRunner().invoke(cli, [
        "--json", "ribbon", "set-rules", "cwx_ticket", "--solution", "MySol",
        "--command-id", "cwx_ticket.form.Validate.Command"])
    assert res.exit_code == 2  # usage error
    assert "enable-rule" in res.output or "display-rule" in res.output


def test_ribbon_set_rules_rejects_unknown_platform_id(monkeypatch):
    monkeypatch.setattr("crm.cli.CLIContext.backend", lambda self: object())
    res = CliRunner().invoke(cli, [
        "--json", "ribbon", "set-rules", "cwx_ticket", "--solution", "MySol",
        "--command-id", "cwx_ticket.form.Validate.Command",
        "--enable-rule", "Mscrm.Typooo"])
    assert res.exit_code == 1
    assert "not a recognized platform rule" in res.output


def test_ribbon_set_rules_warns_on_oob_command(monkeypatch):
    monkeypatch.setattr(ribbon_mod, "apply_ribbon_change",
                        lambda *a, **k: {"status": "succeeded"})
    monkeypatch.setattr("crm.cli.CLIContext.backend", lambda self: object())
    res = CliRunner().invoke(cli, [
        "--json", "ribbon", "set-rules", "cwx_ticket", "--solution", "MySol",
        "--command-id", "Mscrm.SavePrimary",
        "--display-rule", "Mscrm.HideOnModern"])
    assert res.exit_code == 0, res.output
    data = json.loads(res.output)
    assert any("unsupported" in w.lower()
               for w in (data.get("meta", {}).get("warnings") or []))


def test_ribbon_add_custom_rule_applies(monkeypatch):
    captured: dict[str, object] = {}

    def fake_apply(backend, *, solution, entity, mutate, **kw):
        root = ET.fromstring(_CUST_WITH_COMMAND)
        mutate(root)
        rule = root.find(".//RuleDefinitions/EnableRules/EnableRule/CustomRule")
        captured["library"] = rule.get("Library") if rule is not None else None
        return {"status": "succeeded"}

    monkeypatch.setattr(ribbon_mod, "apply_ribbon_change", fake_apply)
    monkeypatch.setattr(ribbon_mod, "resolve_webresource_id",
                        lambda backend, name: "guid-1")
    monkeypatch.setattr("crm.cli.CLIContext.backend", lambda self: object())
    res = CliRunner().invoke(cli, [
        "--json", "ribbon", "add-custom-rule", "cwx_ticket", "--solution", "MySol",
        "--command-id", "cwx_ticket.form.Validate.Command",
        "--webresource", "cwx_/scripts/x.js", "--function", "ns.canRun"])
    assert res.exit_code == 0, res.output
    assert captured["library"] == "$webresource:cwx_/scripts/x.js"
    data = json.loads(res.output)
    assert data["data"]["rule_id"] == \
        "cwx_ticket.form.Validate.Command.nscanRun.EnableRule"


def test_ribbon_add_custom_rule_rejects_missing_webresource(monkeypatch):
    def boom(backend, name):
        raise ValueError(f"web resource {name!r} not found")
    monkeypatch.setattr(ribbon_mod, "resolve_webresource_id", boom)
    monkeypatch.setattr("crm.cli.CLIContext.backend", lambda self: object())
    res = CliRunner().invoke(cli, [
        "--json", "ribbon", "add-custom-rule", "cwx_ticket", "--solution", "MySol",
        "--command-id", "cwx_ticket.form.Validate.Command",
        "--webresource", "cwx_/missing.js", "--function", "ns.canRun"])
    assert res.exit_code == 1
    assert "not found" in res.output


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
