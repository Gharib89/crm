# pyright: basic
import json
import xml.etree.ElementTree as ET
from click.testing import CliRunner
import pytest
from crm.cli import cli
from crm.core import ribbon as ribbon_mod
from crm.utils.d365_backend import D365Error


def _apply_mutate(mutate, root, entity):
    """Mirror apply_ribbon_change's contract in a fake: hand the callback the
    entity's RibbonDiffXml element (the find/get-or-create scoping now lives in
    apply_ribbon_change), not the raw customizations root."""
    node = ribbon_mod.find_entity_node(root, entity)
    mutate(ribbon_mod.get_or_create_ribbon_diff(node))


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
        _apply_mutate(mutate, root, entity)
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
        raise D365Error(f"web resource {name!r} not found")
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
        _apply_mutate(mutate, root, entity)
        captured["remaining"] = len(root.findall(".//CustomAction"))
        return {"status": "succeeded"}

    monkeypatch.setattr(ribbon_mod, "apply_ribbon_change", fake_apply)
    monkeypatch.setattr("crm.cli.CLIContext.backend", lambda self: object())
    res = CliRunner().invoke(cli, [
        "ribbon", "remove", "cwx_ticket", "--solution", "MySol",
        "--button-id", "cwx_ticket.form.Validate.CustomAction", "--yes"])
    assert res.exit_code == 0, res.output
    assert captured["remaining"] == 0


def _patch_add_button_capturing(monkeypatch, captured):
    def fake_apply(backend, *, solution, entity, mutate, publish=True, **kw):
        captured["publish"] = publish
        return {"status": "succeeded"}
    monkeypatch.setattr(ribbon_mod, "apply_ribbon_change", fake_apply)
    monkeypatch.setattr(ribbon_mod, "resolve_webresource_id",
                        lambda backend, name: "guid-1")
    monkeypatch.setattr("crm.cli.CLIContext.backend", lambda self: object())


def _run_add_button(*extra):
    return CliRunner().invoke(cli, [
        "ribbon", "add-button", "cwx_ticket", "--solution", "MySol",
        "--label", "Validate", "--location", "form",
        "--webresource", "cwx_/scripts/x.js", "--function", "ns.fn",
        "--param", "PrimaryControl", *extra])


def test_ribbon_add_button_stages_by_default(monkeypatch):
    captured: dict[str, object] = {}
    _patch_add_button_capturing(monkeypatch, captured)
    res = _run_add_button()
    assert res.exit_code == 0, res.output
    assert captured["publish"] is False


def test_ribbon_add_button_publish_flag_publishes(monkeypatch):
    captured: dict[str, object] = {}
    _patch_add_button_capturing(monkeypatch, captured)
    res = _run_add_button("--publish")
    assert res.exit_code == 0, res.output
    assert captured["publish"] is True


def _patch_remove_capturing(monkeypatch, captured):
    def fake_apply(backend, *, solution, entity, mutate, publish=True, **kw):
        captured["publish"] = publish
        return {"status": "succeeded"}
    monkeypatch.setattr(ribbon_mod, "apply_ribbon_change", fake_apply)
    monkeypatch.setattr("crm.cli.CLIContext.backend", lambda self: object())


def _run_remove(*extra):
    return CliRunner().invoke(cli, [
        "ribbon", "remove", "cwx_ticket", "--solution", "MySol",
        "--button-id", "cwx_ticket.form.Validate.CustomAction", "--yes", *extra])


def test_ribbon_remove_stages_by_default(monkeypatch):
    captured: dict[str, object] = {}
    _patch_remove_capturing(monkeypatch, captured)
    res = _run_remove()
    assert res.exit_code == 0, res.output
    assert captured["publish"] is False


def test_ribbon_remove_publish_flag_publishes(monkeypatch):
    captured: dict[str, object] = {}
    _patch_remove_capturing(monkeypatch, captured)
    res = _run_remove("--publish")
    assert res.exit_code == 0, res.output
    assert captured["publish"] is True


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
        _apply_mutate(mutate, root, entity)
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
        _apply_mutate(mutate, root, entity)
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
        _apply_mutate(mutate, root, entity)
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
        raise D365Error(f"web resource {name!r} not found")
    monkeypatch.setattr(ribbon_mod, "resolve_webresource_id", boom)
    monkeypatch.setattr("crm.cli.CLIContext.backend", lambda self: object())
    res = CliRunner().invoke(cli, [
        "--json", "ribbon", "add-custom-rule", "cwx_ticket", "--solution", "MySol",
        "--command-id", "cwx_ticket.form.Validate.Command",
        "--webresource", "cwx_/missing.js", "--function", "ns.canRun"])
    assert res.exit_code == 1
    assert "not found" in res.output


_CUST_WITH_BUTTON = (
    "<ImportExportXml><Entities><Entity><Name>cwx_ticket</Name>"
    "<RibbonDiffXml><CustomActions>"
    "<CustomAction Id='cwx_ticket.form.Validate.CustomAction'>"
    "<CommandUIDefinition><Button Id='cwx_ticket.form.Validate.Button' "
    "Command='cwx_ticket.form.Validate.Command' TemplateAlias='o1' "
    "Sequence='50' LabelText='Validate'/>"
    "</CommandUIDefinition></CustomAction></CustomActions>"
    "<CommandDefinitions/><LocLabels/></RibbonDiffXml>"
    "</Entity></Entities></ImportExportXml>")
_BID = "cwx_ticket.form.Validate.CustomAction"


def _patch_set_label_apply(monkeypatch, captured):
    def fake_apply(backend, *, solution, entity, mutate, publish=True, **kw):
        root = ET.fromstring(_CUST_WITH_BUTTON)
        _apply_mutate(mutate, root, entity)
        captured["root"] = root
        captured["solution"] = solution
        captured["publish"] = publish
        return {"status": "succeeded"}
    monkeypatch.setattr(ribbon_mod, "apply_ribbon_change", fake_apply)
    monkeypatch.setattr("crm.cli.CLIContext.backend", lambda self: object())


def test_ribbon_set_label_inline_sets_labeltext(monkeypatch):
    captured: dict[str, object] = {}
    _patch_set_label_apply(monkeypatch, captured)
    res = CliRunner().invoke(cli, [
        "--json", "ribbon", "set-label", "cwx_ticket", "--solution", "MySol",
        "--button-id", _BID, "--label", "Check"])
    assert res.exit_code == 0, res.output
    root = captured["root"]
    assert isinstance(root, ET.Element)
    btn = root.find(".//Button")
    assert btn is not None and btn.get("LabelText") == "Check"
    # protected attributes untouched
    assert btn.get("Command") == "cwx_ticket.form.Validate.Command"
    assert btn.get("TemplateAlias") == "o1"
    data = json.loads(res.output)
    assert data["ok"] is True
    assert data["data"]["button_id"] == _BID


def test_ribbon_set_label_requires_a_field(monkeypatch):
    captured: dict[str, object] = {}
    _patch_set_label_apply(monkeypatch, captured)
    res = CliRunner().invoke(cli, [
        "--json", "ribbon", "set-label", "cwx_ticket", "--solution", "MySol",
        "--button-id", _BID])
    assert res.exit_code == 2  # usage error
    assert "root" not in captured  # never reached apply


def test_ribbon_set_label_lcid_validated_against_provisioned(monkeypatch):
    captured: dict[str, object] = {}
    _patch_set_label_apply(monkeypatch, captured)
    monkeypatch.setattr(ribbon_mod, "retrieve_provisioned_languages",
                        lambda backend: [1033])
    res = CliRunner().invoke(cli, [
        "--json", "ribbon", "set-label", "cwx_ticket", "--solution", "MySol",
        "--button-id", _BID, "--label", "Valider", "--lcid", "9999"])
    assert res.exit_code == 1
    assert "provisioned" in res.output.lower()
    assert "root" not in captured  # never mutated when lcid invalid


def test_ribbon_set_label_lcid_uses_loclabels(monkeypatch):
    captured: dict[str, object] = {}
    _patch_set_label_apply(monkeypatch, captured)
    monkeypatch.setattr(ribbon_mod, "retrieve_provisioned_languages",
                        lambda backend: [1033, 1036])
    res = CliRunner().invoke(cli, [
        "ribbon", "set-label", "cwx_ticket", "--solution", "MySol",
        "--button-id", _BID, "--label", "Valider", "--lcid", "1036"])
    assert res.exit_code == 0, res.output
    root = captured["root"]
    assert isinstance(root, ET.Element)
    btn = root.find(".//Button")
    assert btn is not None
    assert btn.get("LabelText", "").startswith("$LocLabels:")
    title = root.find(".//LocLabels/LocLabel/Titles/Title[@languagecode='1036']")
    assert title is not None and title.get("description") == "Valider"


def test_ribbon_set_label_no_publish_passes_through(monkeypatch):
    captured: dict[str, object] = {}
    _patch_set_label_apply(monkeypatch, captured)
    res = CliRunner().invoke(cli, [
        "ribbon", "set-label", "cwx_ticket", "--solution", "MySol",
        "--button-id", _BID, "--label", "Check", "--no-publish"])
    assert res.exit_code == 0, res.output
    assert captured["publish"] is False


def test_ribbon_set_label_unknown_button_errors(monkeypatch):
    captured: dict[str, object] = {}
    _patch_set_label_apply(monkeypatch, captured)
    res = CliRunner().invoke(cli, [
        "--json", "ribbon", "set-label", "cwx_ticket", "--solution", "MySol",
        "--button-id", "does.not.exist", "--label", "Check"])
    assert res.exit_code == 1
    assert "not found" in res.output


def test_ribbon_remove_unknown_button_errors(monkeypatch):
    cust = ("<ImportExportXml><Entities><Entity><Name>cwx_ticket</Name>"
            "<RibbonDiffXml><CustomActions/><CommandDefinitions/></RibbonDiffXml>"
            "</Entity></Entities></ImportExportXml>")

    def fake_apply(backend, *, solution, entity, mutate, **kw):
        _apply_mutate(mutate, ET.fromstring(cust), entity)  # mutate raises -> propagate
        return {"status": "succeeded"}

    monkeypatch.setattr(ribbon_mod, "apply_ribbon_change", fake_apply)
    monkeypatch.setattr("crm.cli.CLIContext.backend", lambda self: object())
    res = CliRunner().invoke(cli, [
        "--json", "ribbon", "remove", "cwx_ticket", "--solution", "MySol",
        "--button-id", "does.not.exist", "--yes"])
    assert res.exit_code == 1
    assert "not found" in res.output


# ---------------------------------------------------------------------------
# ribbon export — dry-run, D365Error, --output
# ---------------------------------------------------------------------------

def test_ribbon_export_dry_run_emits_preview(monkeypatch):
    class _FakeBackend:
        def get(self, path, **kw):
            return {"_dry_run": True, "path": path}

    monkeypatch.setattr("crm.cli.CLIContext.backend", lambda self: _FakeBackend())
    res = CliRunner().invoke(cli, [
        "--json", "--dry-run", "ribbon", "export", "cwx_ticket"])
    assert res.exit_code == 0, res.output
    data = json.loads(res.output)
    assert data["ok"] is True


def test_ribbon_export_validation_error_emits_failure(monkeypatch):
    monkeypatch.setattr(
        ribbon_mod, "retrieve_entity_ribbon",
        lambda backend, entity: (_ for _ in ()).throw(D365Error("bad entity")))
    monkeypatch.setattr("crm.cli.CLIContext.backend", lambda self: object())
    res = CliRunner().invoke(cli, ["--json", "ribbon", "export", "bad_entity"])
    assert res.exit_code == 1
    data = json.loads(res.output)
    assert data["ok"] is False
    assert "bad entity" in data.get("error", "")


def test_ribbon_export_d365error_emits_failure(monkeypatch):
    from crm.utils.d365_backend import D365Error
    monkeypatch.setattr(
        ribbon_mod, "retrieve_entity_ribbon",
        lambda backend, entity: (_ for _ in ()).throw(D365Error("api error", status=503)))
    monkeypatch.setattr("crm.cli.CLIContext.backend", lambda self: object())
    res = CliRunner().invoke(cli, ["--json", "ribbon", "export", "cwx_ticket"])
    assert res.exit_code == 1
    data = json.loads(res.output)
    assert data["ok"] is False


def test_ribbon_export_writes_output_file(monkeypatch, tmp_path):
    xml = "<RibbonDiffXml><CustomActions/></RibbonDiffXml>"
    monkeypatch.setattr(
        ribbon_mod, "retrieve_entity_ribbon",
        lambda backend, entity: ET.fromstring(xml))
    monkeypatch.setattr("crm.cli.CLIContext.backend", lambda self: object())
    out = tmp_path / "ribbon.xml"
    res = CliRunner().invoke(cli, [
        "--json", "ribbon", "export", "cwx_ticket", "--output", str(out)])
    assert res.exit_code == 0, res.output
    data = json.loads(res.output)
    assert data["ok"] is True
    assert data["data"]["output"] == str(out)
    assert out.exists()
    assert "RibbonDiffXml" in out.read_text()


def test_ribbon_export_output_oserror_emits_failure(monkeypatch, tmp_path):
    """Writing to a non-writable path emits ok=False with the OS error message."""
    xml = "<RibbonDiffXml><CustomActions/></RibbonDiffXml>"
    monkeypatch.setattr(
        ribbon_mod, "retrieve_entity_ribbon",
        lambda backend, entity: ET.fromstring(xml))
    monkeypatch.setattr("crm.cli.CLIContext.backend", lambda self: object())
    bad_dir = tmp_path / "no_such_dir" / "ribbon.xml"
    res = CliRunner().invoke(cli, [
        "--json", "ribbon", "export", "cwx_ticket", "--output", str(bad_dir)])
    assert res.exit_code == 1
    data = json.loads(res.output)
    assert data["ok"] is False
    assert "Could not write" in data.get("error", "")


# ---------------------------------------------------------------------------
# ribbon list — dry-run, D365Error
# ---------------------------------------------------------------------------

def test_ribbon_list_dry_run_emits_preview(monkeypatch):
    def fake_export(backend, name, output_path, **kw):
        return {"_dry_run": True, "would_export": name}

    monkeypatch.setattr(ribbon_mod, "export_solution", fake_export)
    monkeypatch.setattr("crm.cli.CLIContext.backend", lambda self: object())
    res = CliRunner().invoke(cli, [
        "--json", "--dry-run", "ribbon", "list", "cwx_ticket", "--solution", "MySol"])
    assert res.exit_code == 0, res.output
    data = json.loads(res.output)
    assert data["ok"] is True
    assert data["data"]["_dry_run"] is True


def test_ribbon_list_d365error_emits_failure(monkeypatch):
    from crm.utils.d365_backend import D365Error

    def boom_export(backend, name, output_path, **kw):
        raise D365Error("export failed", status=500)

    monkeypatch.setattr(ribbon_mod, "export_solution", boom_export)
    monkeypatch.setattr("crm.cli.CLIContext.backend", lambda self: object())
    res = CliRunner().invoke(cli, [
        "--json", "ribbon", "list", "cwx_ticket", "--solution", "MySol"])
    assert res.exit_code == 1
    data = json.loads(res.output)
    assert data["ok"] is False


def test_ribbon_list_validation_error_emits_failure(monkeypatch):
    """ribbon_list D365Error (validation) path: export succeeds but entity not found."""
    import zipfile as zf

    def fake_export_empty(backend, name, output_path, **kw):
        cust = "<ImportExportXml><Entities/></ImportExportXml>"
        with zf.ZipFile(output_path, "w") as z:
            z.writestr("customizations.xml", cust)
        return {"output": str(output_path)}

    monkeypatch.setattr(ribbon_mod, "export_solution", fake_export_empty)
    monkeypatch.setattr("crm.cli.CLIContext.backend", lambda self: object())
    res = CliRunner().invoke(cli, [
        "--json", "ribbon", "list", "cwx_missing", "--solution", "MySol"])
    assert res.exit_code == 1
    data = json.loads(res.output)
    assert data["ok"] is False


# ---------------------------------------------------------------------------
# ribbon add-button — D365Error paths in resolve_webresource and apply
# ---------------------------------------------------------------------------

def test_ribbon_add_button_d365error_in_resolve_webresource(monkeypatch):
    from crm.utils.d365_backend import D365Error

    def boom(backend, name):
        raise D365Error("webresource lookup failed", status=500)

    monkeypatch.setattr(ribbon_mod, "resolve_webresource_id", boom)
    monkeypatch.setattr("crm.cli.CLIContext.backend", lambda self: object())
    res = CliRunner().invoke(cli, [
        "--json", "ribbon", "add-button", "cwx_ticket", "--solution", "MySol",
        "--label", "Validate", "--location", "form",
        "--webresource", "cwx_/x.js", "--function", "ns.fn",
        "--param", "PrimaryControl"])
    assert res.exit_code == 1
    data = json.loads(res.output)
    assert data["ok"] is False


def test_ribbon_add_button_d365error_in_apply(monkeypatch):
    from crm.utils.d365_backend import D365Error

    monkeypatch.setattr(ribbon_mod, "resolve_webresource_id",
                        lambda backend, name: "guid-1")
    monkeypatch.setattr(ribbon_mod, "apply_ribbon_change",
                        lambda *a, **k: (_ for _ in ()).throw(D365Error("apply failed", status=500)))
    monkeypatch.setattr("crm.cli.CLIContext.backend", lambda self: object())
    res = CliRunner().invoke(cli, [
        "--json", "ribbon", "add-button", "cwx_ticket", "--solution", "MySol",
        "--label", "Validate", "--location", "form",
        "--webresource", "cwx_/x.js", "--function", "ns.fn",
        "--param", "PrimaryControl"])
    assert res.exit_code == 1
    data = json.loads(res.output)
    assert data["ok"] is False


def test_ribbon_add_button_validation_error_in_apply(monkeypatch):
    monkeypatch.setattr(ribbon_mod, "resolve_webresource_id",
                        lambda backend, name: "guid-1")
    monkeypatch.setattr(ribbon_mod, "apply_ribbon_change",
                        lambda *a, **k: (_ for _ in ()).throw(D365Error("mutate failed")))
    monkeypatch.setattr("crm.cli.CLIContext.backend", lambda self: object())
    res = CliRunner().invoke(cli, [
        "--json", "ribbon", "add-button", "cwx_ticket", "--solution", "MySol",
        "--label", "Validate", "--location", "form",
        "--webresource", "cwx_/x.js", "--function", "ns.fn",
        "--param", "PrimaryControl"])
    assert res.exit_code == 1
    assert "mutate failed" in res.output


def test_ribbon_validation_error_json_envelope_has_reserved_meta(monkeypatch):
    """#644: a status-less (client-side validation) D365Error now surfaces with the
    standard reserved error meta ({status, code, category, retryable}) under --json,
    since core ribbon validation raises D365Error rather than a bare ValueError."""
    monkeypatch.setattr(ribbon_mod, "resolve_webresource_id",
                        lambda backend, name: "guid-1")
    monkeypatch.setattr(ribbon_mod, "apply_ribbon_change",
                        lambda *a, **k: (_ for _ in ()).throw(D365Error("ribbon id collides")))
    monkeypatch.setattr("crm.cli.CLIContext.backend", lambda self: object())
    res = CliRunner().invoke(cli, [
        "--json", "ribbon", "add-button", "cwx_ticket", "--solution", "MySol",
        "--label", "Validate", "--location", "form",
        "--webresource", "cwx_/x.js", "--function", "ns.fn",
        "--param", "PrimaryControl"])
    assert res.exit_code == 1
    data = json.loads(res.output)
    assert data["ok"] is False
    meta = data["meta"]
    assert meta["status"] is None
    assert meta["code"] is None
    assert meta["category"] == "validation"
    assert meta["retryable"] is False


# ---------------------------------------------------------------------------
# ribbon remove — D365Error in apply
# ---------------------------------------------------------------------------

def test_ribbon_remove_d365error_in_apply(monkeypatch):
    from crm.utils.d365_backend import D365Error

    monkeypatch.setattr(ribbon_mod, "apply_ribbon_change",
                        lambda *a, **k: (_ for _ in ()).throw(D365Error("apply failed", status=500)))
    monkeypatch.setattr("crm.cli.CLIContext.backend", lambda self: object())
    res = CliRunner().invoke(cli, [
        "--json", "ribbon", "remove", "cwx_ticket", "--solution", "MySol",
        "--button-id", "some.button", "--yes"])
    assert res.exit_code == 1
    data = json.loads(res.output)
    assert data["ok"] is False


def test_ribbon_remove_validation_error_in_apply(monkeypatch):
    monkeypatch.setattr(ribbon_mod, "apply_ribbon_change",
                        lambda *a, **k: (_ for _ in ()).throw(D365Error("entity not found")))
    monkeypatch.setattr("crm.cli.CLIContext.backend", lambda self: object())
    res = CliRunner().invoke(cli, [
        "--json", "ribbon", "remove", "cwx_ticket", "--solution", "MySol",
        "--button-id", "some.button", "--yes"])
    assert res.exit_code == 1
    assert "entity not found" in res.output


# ---------------------------------------------------------------------------
# ribbon set-label — D365Error in retrieve_provisioned_languages, D365Error in apply
# ---------------------------------------------------------------------------

def test_ribbon_set_label_d365error_in_retrieve_provisioned_languages(monkeypatch):
    from crm.utils.d365_backend import D365Error

    monkeypatch.setattr(ribbon_mod, "retrieve_provisioned_languages",
                        lambda backend: (_ for _ in ()).throw(D365Error("lang lookup failed", status=500)))
    monkeypatch.setattr("crm.cli.CLIContext.backend", lambda self: object())
    res = CliRunner().invoke(cli, [
        "--json", "ribbon", "set-label", "cwx_ticket", "--solution", "MySol",
        "--button-id", _BID, "--label", "Check", "--lcid", "1036"])
    assert res.exit_code == 1
    data = json.loads(res.output)
    assert data["ok"] is False


def test_ribbon_set_label_validation_error_in_retrieve_provisioned_languages(monkeypatch):
    monkeypatch.setattr(ribbon_mod, "retrieve_provisioned_languages",
                        lambda backend: (_ for _ in ()).throw(D365Error("bad provisioned call")))
    monkeypatch.setattr("crm.cli.CLIContext.backend", lambda self: object())
    res = CliRunner().invoke(cli, [
        "--json", "ribbon", "set-label", "cwx_ticket", "--solution", "MySol",
        "--button-id", _BID, "--label", "Check", "--lcid", "1036"])
    assert res.exit_code == 1
    assert "bad provisioned call" in res.output


def test_ribbon_set_label_d365error_in_apply(monkeypatch):
    from crm.utils.d365_backend import D365Error

    monkeypatch.setattr(ribbon_mod, "apply_ribbon_change",
                        lambda *a, **k: (_ for _ in ()).throw(D365Error("apply failed", status=500)))
    monkeypatch.setattr("crm.cli.CLIContext.backend", lambda self: object())
    res = CliRunner().invoke(cli, [
        "--json", "ribbon", "set-label", "cwx_ticket", "--solution", "MySol",
        "--button-id", _BID, "--label", "Check"])
    assert res.exit_code == 1
    data = json.loads(res.output)
    assert data["ok"] is False


def test_ribbon_set_label_validation_error_in_apply(monkeypatch):
    monkeypatch.setattr(ribbon_mod, "apply_ribbon_change",
                        lambda *a, **k: (_ for _ in ()).throw(D365Error("button not found")))
    monkeypatch.setattr("crm.cli.CLIContext.backend", lambda self: object())
    res = CliRunner().invoke(cli, [
        "--json", "ribbon", "set-label", "cwx_ticket", "--solution", "MySol",
        "--button-id", _BID, "--label", "Check"])
    assert res.exit_code == 1
    assert "button not found" in res.output


# ---------------------------------------------------------------------------
# ribbon hide-button — D365Error in retrieve_entity_ribbon,
#                     target has no Command, D365Error in apply
# ---------------------------------------------------------------------------

def test_ribbon_hide_button_d365error_in_retrieve(monkeypatch):
    from crm.utils.d365_backend import D365Error

    monkeypatch.setattr(ribbon_mod, "retrieve_entity_ribbon",
                        lambda backend, entity: (_ for _ in ()).throw(D365Error("ribbon fetch failed", status=500)))
    monkeypatch.setattr("crm.cli.CLIContext.backend", lambda self: object())
    res = CliRunner().invoke(cli, [
        "--json", "ribbon", "hide-button", "account", "--solution", "MySol",
        "--target-id", "Mscrm.HomepageGrid.account.Deactivate"])
    assert res.exit_code == 1
    data = json.loads(res.output)
    assert data["ok"] is False


def test_ribbon_hide_button_validation_error_in_retrieve(monkeypatch):
    monkeypatch.setattr(ribbon_mod, "retrieve_entity_ribbon",
                        lambda backend, entity: (_ for _ in ()).throw(D365Error("entity invalid")))
    monkeypatch.setattr("crm.cli.CLIContext.backend", lambda self: object())
    res = CliRunner().invoke(cli, [
        "--json", "ribbon", "hide-button", "account", "--solution", "MySol",
        "--target-id", "Mscrm.HomepageGrid.account.Deactivate"])
    assert res.exit_code == 1
    assert "entity invalid" in res.output


_COMPOSED_NO_CMD = (
    "<RibbonDefinitions><RibbonDefinition><Tabs><Tab><Groups><Group><Controls>"
    "<Button Id='Mscrm.NoCommand.Button' TemplateAlias='o2'/>"
    "</Controls></Group></Groups></Tab></Tabs></RibbonDefinition></RibbonDefinitions>")


def test_ribbon_hide_button_target_has_no_command(monkeypatch):
    """display-rule method requires a Command attribute; element without one errors."""
    monkeypatch.setattr(ribbon_mod, "retrieve_entity_ribbon",
                        lambda backend, entity: ET.fromstring(_COMPOSED_NO_CMD))
    monkeypatch.setattr("crm.cli.CLIContext.backend", lambda self: object())
    res = CliRunner().invoke(cli, [
        "--json", "ribbon", "hide-button", "account", "--solution", "MySol",
        "--target-id", "Mscrm.NoCommand.Button"])
    assert res.exit_code == 1
    assert "no command" in res.output.lower() or "hide-action" in res.output.lower()


def test_ribbon_hide_button_d365error_in_apply(monkeypatch):
    from crm.utils.d365_backend import D365Error

    monkeypatch.setattr(ribbon_mod, "retrieve_entity_ribbon",
                        lambda backend, entity: ET.fromstring(_COMPOSED))
    monkeypatch.setattr(ribbon_mod, "apply_ribbon_change",
                        lambda *a, **k: (_ for _ in ()).throw(D365Error("apply failed", status=500)))
    monkeypatch.setattr("crm.cli.CLIContext.backend", lambda self: object())
    res = CliRunner().invoke(cli, [
        "--json", "ribbon", "hide-button", "account", "--solution", "MySol",
        "--target-id", "Mscrm.HomepageGrid.account.Deactivate"])
    assert res.exit_code == 1
    data = json.loads(res.output)
    assert data["ok"] is False


def test_ribbon_hide_button_validation_error_in_apply(monkeypatch):
    monkeypatch.setattr(ribbon_mod, "retrieve_entity_ribbon",
                        lambda backend, entity: ET.fromstring(_COMPOSED))
    monkeypatch.setattr(ribbon_mod, "apply_ribbon_change",
                        lambda *a, **k: (_ for _ in ()).throw(D365Error("mutate failed")))
    monkeypatch.setattr("crm.cli.CLIContext.backend", lambda self: object())
    res = CliRunner().invoke(cli, [
        "--json", "ribbon", "hide-button", "account", "--solution", "MySol",
        "--target-id", "Mscrm.HomepageGrid.account.Deactivate"])
    assert res.exit_code == 1
    assert "mutate failed" in res.output


# ---------------------------------------------------------------------------
# ribbon set-rules — D365Error in apply
# ---------------------------------------------------------------------------

def test_ribbon_set_rules_d365error_in_apply(monkeypatch):
    from crm.utils.d365_backend import D365Error

    monkeypatch.setattr(ribbon_mod, "apply_ribbon_change",
                        lambda *a, **k: (_ for _ in ()).throw(D365Error("apply failed", status=500)))
    monkeypatch.setattr("crm.cli.CLIContext.backend", lambda self: object())
    res = CliRunner().invoke(cli, [
        "--json", "ribbon", "set-rules", "cwx_ticket", "--solution", "MySol",
        "--command-id", "cwx_ticket.form.Validate.Command",
        "--enable-rule", "Mscrm.SelectionCountExactlyOne"])
    assert res.exit_code == 1
    data = json.loads(res.output)
    assert data["ok"] is False


def test_ribbon_set_rules_validation_error_in_apply(monkeypatch):
    monkeypatch.setattr(ribbon_mod, "apply_ribbon_change",
                        lambda *a, **k: (_ for _ in ()).throw(D365Error("command not found")))
    monkeypatch.setattr("crm.cli.CLIContext.backend", lambda self: object())
    res = CliRunner().invoke(cli, [
        "--json", "ribbon", "set-rules", "cwx_ticket", "--solution", "MySol",
        "--command-id", "cwx_ticket.form.Validate.Command",
        "--enable-rule", "Mscrm.SelectionCountExactlyOne"])
    assert res.exit_code == 1
    assert "command not found" in res.output


# ---------------------------------------------------------------------------
# ribbon add-custom-rule — D365Error in resolve_webresource, OOB warning,
#                          D365Error in apply
# ---------------------------------------------------------------------------

def test_ribbon_add_custom_rule_d365error_in_resolve_webresource(monkeypatch):
    from crm.utils.d365_backend import D365Error

    monkeypatch.setattr(ribbon_mod, "resolve_webresource_id",
                        lambda backend, name: (_ for _ in ()).throw(D365Error("wr lookup failed", status=500)))
    monkeypatch.setattr("crm.cli.CLIContext.backend", lambda self: object())
    res = CliRunner().invoke(cli, [
        "--json", "ribbon", "add-custom-rule", "cwx_ticket", "--solution", "MySol",
        "--command-id", "cwx_ticket.form.Validate.Command",
        "--webresource", "cwx_/x.js", "--function", "ns.canRun"])
    assert res.exit_code == 1
    data = json.loads(res.output)
    assert data["ok"] is False


def test_ribbon_add_custom_rule_oob_warning(monkeypatch):
    """OOB command triggers the unsupported-reuse warning in add-custom-rule."""
    monkeypatch.setattr(ribbon_mod, "resolve_webresource_id",
                        lambda backend, name: "guid-1")
    monkeypatch.setattr(ribbon_mod, "apply_ribbon_change",
                        lambda *a, **k: {"status": "succeeded"})
    monkeypatch.setattr("crm.cli.CLIContext.backend", lambda self: object())
    res = CliRunner().invoke(cli, [
        "--json", "ribbon", "add-custom-rule", "cwx_ticket", "--solution", "MySol",
        "--command-id", "Mscrm.SavePrimary",
        "--webresource", "cwx_/x.js", "--function", "ns.canRun"])
    assert res.exit_code == 0, res.output
    data = json.loads(res.output)
    assert any("unsupported" in w.lower()
               for w in (data.get("meta", {}).get("warnings") or []))


def test_ribbon_add_custom_rule_d365error_in_apply(monkeypatch):
    from crm.utils.d365_backend import D365Error

    monkeypatch.setattr(ribbon_mod, "resolve_webresource_id",
                        lambda backend, name: "guid-1")
    monkeypatch.setattr(ribbon_mod, "apply_ribbon_change",
                        lambda *a, **k: (_ for _ in ()).throw(D365Error("apply failed", status=500)))
    monkeypatch.setattr("crm.cli.CLIContext.backend", lambda self: object())
    res = CliRunner().invoke(cli, [
        "--json", "ribbon", "add-custom-rule", "cwx_ticket", "--solution", "MySol",
        "--command-id", "cwx_ticket.form.Validate.Command",
        "--webresource", "cwx_/x.js", "--function", "ns.canRun"])
    assert res.exit_code == 1
    data = json.loads(res.output)
    assert data["ok"] is False


def test_ribbon_add_custom_rule_validation_error_in_apply(monkeypatch):
    monkeypatch.setattr(ribbon_mod, "resolve_webresource_id",
                        lambda backend, name: "guid-1")
    monkeypatch.setattr(ribbon_mod, "apply_ribbon_change",
                        lambda *a, **k: (_ for _ in ()).throw(D365Error("entity mismatch")))
    monkeypatch.setattr("crm.cli.CLIContext.backend", lambda self: object())
    res = CliRunner().invoke(cli, [
        "--json", "ribbon", "add-custom-rule", "cwx_ticket", "--solution", "MySol",
        "--command-id", "cwx_ticket.form.Validate.Command",
        "--webresource", "cwx_/x.js", "--function", "ns.canRun"])
    assert res.exit_code == 1
    assert "entity mismatch" in res.output


# ---------------------------------------------------------------------------
# ribbon add-button — icon flags (issue #679)
# ---------------------------------------------------------------------------

def test_ribbon_add_button_writes_icon_attributes(monkeypatch):
    captured: dict[str, object] = {}

    def fake_apply(backend, *, solution, entity, mutate, **kw):
        root = ET.fromstring(
            "<ImportExportXml><Entities><Entity><Name>cwx_ticket</Name>"
            "</Entity></Entities></ImportExportXml>")
        _apply_mutate(mutate, root, entity)
        captured["root"] = root
        return {"status": "succeeded"}

    monkeypatch.setattr(ribbon_mod, "apply_ribbon_change", fake_apply)
    monkeypatch.setattr(ribbon_mod, "resolve_webresource_id",
                        lambda backend, name: "guid-1")
    monkeypatch.setattr(ribbon_mod, "validate_icon_webresource",
                        lambda backend, *, slot, name: None)
    monkeypatch.setattr("crm.cli.CLIContext.backend", lambda self: object())
    res = CliRunner().invoke(cli, [
        "ribbon", "add-button", "cwx_ticket", "--solution", "MySol",
        "--label", "Validate", "--location", "form",
        "--webresource", "cwx_/scripts/x.js", "--function", "ns.fn",
        "--param", "PrimaryControl",
        "--modern-image", "cwx_/i.svg", "--image16", "cwx_/i16.png"])
    assert res.exit_code == 0, res.output
    root = captured["root"]
    assert isinstance(root, ET.Element)
    btn = root.find(".//Button")
    assert btn is not None
    assert btn.get("ModernImage") == "$webresource:cwx_/i.svg"
    assert btn.get("Image16by16") == "$webresource:cwx_/i16.png"
    assert btn.get("Image32by32") is None


def test_ribbon_add_button_rejects_wrong_type_icon(monkeypatch):
    """A wrong-type icon web resource errors (exit 1, ADR 0001 in-command
    validation) before any solution round-trip."""
    def bad_icon(backend, *, slot, name):
        raise D365Error(f"web resource {name!r} is not valid for --{slot}")
    applied: list[str] = []
    monkeypatch.setattr(ribbon_mod, "resolve_webresource_id",
                        lambda backend, name: "guid-1")
    monkeypatch.setattr(ribbon_mod, "validate_icon_webresource", bad_icon)
    monkeypatch.setattr(ribbon_mod, "apply_ribbon_change",
                        lambda *a, **k: applied.append("x") or {"status": "ok"})
    monkeypatch.setattr("crm.cli.CLIContext.backend", lambda self: object())
    res = CliRunner().invoke(cli, [
        "--json", "ribbon", "add-button", "cwx_ticket", "--solution", "MySol",
        "--label", "Validate", "--location", "form",
        "--webresource", "cwx_/scripts/x.js", "--function", "ns.fn",
        "--param", "PrimaryControl", "--modern-image", "cwx_/i.png"])
    assert res.exit_code == 1
    assert "not valid for" in res.output
    assert applied == []  # never reached the export/import round-trip


def test_ribbon_add_button_no_icons_omits_attributes(monkeypatch):
    """Without any icon flag the button carries no image attribute (byte-for-byte
    parity with the pre-#679 behavior)."""
    captured: dict[str, object] = {}

    def fake_apply(backend, *, solution, entity, mutate, **kw):
        root = ET.fromstring(
            "<ImportExportXml><Entities><Entity><Name>cwx_ticket</Name>"
            "</Entity></Entities></ImportExportXml>")
        _apply_mutate(mutate, root, entity)
        captured["root"] = root
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
    btn = captured["root"].find(".//Button")  # type: ignore[union-attr]
    assert btn is not None
    for attr in ("ModernImage", "Image16by16", "Image32by32"):
        assert attr not in btn.attrib


# ---------------------------------------------------------------------------
# ribbon set-icon (issue #679)
# ---------------------------------------------------------------------------

def _patch_set_icon_apply(monkeypatch, captured):
    def fake_apply(backend, *, solution, entity, mutate, publish=True, **kw):
        root = ET.fromstring(_CUST_WITH_BUTTON)
        _apply_mutate(mutate, root, entity)
        captured["root"] = root
        captured["solution"] = solution
        captured["publish"] = publish
        return {"status": "succeeded"}
    monkeypatch.setattr(ribbon_mod, "apply_ribbon_change", fake_apply)
    monkeypatch.setattr(ribbon_mod, "validate_icon_webresource",
                        lambda backend, *, slot, name: None)
    monkeypatch.setattr("crm.cli.CLIContext.backend", lambda self: object())


def test_ribbon_set_icon_sets_attributes(monkeypatch):
    captured: dict[str, object] = {}
    _patch_set_icon_apply(monkeypatch, captured)
    res = CliRunner().invoke(cli, [
        "--json", "ribbon", "set-icon", "cwx_ticket", "--solution", "MySol",
        "--button-id", _BID, "--modern-image", "cwx_/i.svg",
        "--image16", "cwx_/i16.png", "--image32", "cwx_/i32.png"])
    assert res.exit_code == 0, res.output
    root = captured["root"]
    assert isinstance(root, ET.Element)
    btn = root.find(".//Button")
    assert btn is not None
    assert btn.get("ModernImage") == "$webresource:cwx_/i.svg"
    assert btn.get("Image16by16") == "$webresource:cwx_/i16.png"
    assert btn.get("Image32by32") == "$webresource:cwx_/i32.png"
    # protected attributes untouched
    assert btn.get("Command") == "cwx_ticket.form.Validate.Command"
    assert btn.get("LabelText") == "Validate"
    assert btn.get("TemplateAlias") == "o1"
    data = json.loads(res.output)
    assert data["ok"] is True
    assert data["data"]["button_id"] == _BID


def test_ribbon_set_icon_requires_a_slot(monkeypatch):
    captured: dict[str, object] = {}
    _patch_set_icon_apply(monkeypatch, captured)
    res = CliRunner().invoke(cli, [
        "--json", "ribbon", "set-icon", "cwx_ticket", "--solution", "MySol",
        "--button-id", _BID])
    assert res.exit_code == 2  # usage error (ADR 0001)
    assert "root" not in captured  # never reached apply


def test_ribbon_set_icon_missing_webresource_errors(monkeypatch):
    """A missing/wrong-type icon web resource errors (exit 1) before apply."""
    def boom(backend, *, slot, name):
        raise D365Error(f"Web resource not found: {name}")
    applied: list[str] = []
    monkeypatch.setattr(ribbon_mod, "validate_icon_webresource", boom)
    monkeypatch.setattr(ribbon_mod, "apply_ribbon_change",
                        lambda *a, **k: applied.append("x") or {"status": "ok"})
    monkeypatch.setattr("crm.cli.CLIContext.backend", lambda self: object())
    res = CliRunner().invoke(cli, [
        "--json", "ribbon", "set-icon", "cwx_ticket", "--solution", "MySol",
        "--button-id", _BID, "--modern-image", "cwx_/gone.svg"])
    assert res.exit_code == 1
    assert "not found" in res.output
    assert applied == []


def test_ribbon_set_icon_unknown_button_errors(monkeypatch):
    captured: dict[str, object] = {}
    _patch_set_icon_apply(monkeypatch, captured)
    res = CliRunner().invoke(cli, [
        "--json", "ribbon", "set-icon", "cwx_ticket", "--solution", "MySol",
        "--button-id", "does.not.exist", "--modern-image", "cwx_/i.svg"])
    assert res.exit_code == 1
    assert "not found" in res.output


def test_ribbon_set_icon_no_publish_passes_through(monkeypatch):
    captured: dict[str, object] = {}
    _patch_set_icon_apply(monkeypatch, captured)
    res = CliRunner().invoke(cli, [
        "ribbon", "set-icon", "cwx_ticket", "--solution", "MySol",
        "--button-id", _BID, "--modern-image", "cwx_/i.svg", "--no-publish"])
    assert res.exit_code == 0, res.output
    assert captured["publish"] is False


def test_ribbon_set_icon_dry_run_does_not_import(monkeypatch):
    """--dry-run previews via the export short-circuit without importing/publishing."""
    imported: list[str] = []

    def fake_export(backend, name, output_path, **kw):
        return {"_dry_run": True, "would_export": name}

    monkeypatch.setattr(ribbon_mod, "export_solution", fake_export)
    monkeypatch.setattr(ribbon_mod, "import_solution",
                        lambda *a, **k: imported.append("x"))
    monkeypatch.setattr(ribbon_mod, "publish_all", lambda *a, **k: None)
    monkeypatch.setattr(ribbon_mod, "validate_icon_webresource",
                        lambda backend, *, slot, name: None)
    monkeypatch.setattr("crm.cli.CLIContext.backend", lambda self: object())
    res = CliRunner().invoke(cli, [
        "--json", "--dry-run", "ribbon", "set-icon", "cwx_ticket",
        "--solution", "MySol", "--button-id", _BID, "--modern-image", "cwx_/i.svg"])
    assert res.exit_code == 0, res.output
    assert imported == []


def test_ribbon_set_icon_d365error_in_apply(monkeypatch):
    monkeypatch.setattr(ribbon_mod, "validate_icon_webresource",
                        lambda backend, *, slot, name: None)
    monkeypatch.setattr(ribbon_mod, "apply_ribbon_change",
                        lambda *a, **k: (_ for _ in ()).throw(D365Error("apply failed", status=500)))
    monkeypatch.setattr("crm.cli.CLIContext.backend", lambda self: object())
    res = CliRunner().invoke(cli, [
        "--json", "ribbon", "set-icon", "cwx_ticket", "--solution", "MySol",
        "--button-id", _BID, "--modern-image", "cwx_/i.svg"])
    assert res.exit_code == 1
    data = json.loads(res.output)
    assert data["ok"] is False
