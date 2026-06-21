# pyright: basic
from __future__ import annotations
from pathlib import Path
import xml.etree.ElementTree as ET
import pytest
from crm.core import ribbon

FIXTURE = Path(__file__).parent / "fixtures" / "ribbon_account.b64"


def test_decode_compressed_ribbon_returns_ribbondiff_root():
    b64 = FIXTURE.read_text()
    root = ribbon.decode_compressed_ribbon(b64)
    assert isinstance(root, ET.Element)
    assert root.tag == "RibbonDiffXml"


def test_decode_compressed_ribbon_rejects_non_zip():
    import base64
    not_a_zip = base64.b64encode(b"plain text, not PK").decode("ascii")
    with pytest.raises(ValueError, match="not a ZIP"):
        ribbon.decode_compressed_ribbon(not_a_zip)


def test_retrieve_entity_ribbon_uses_inline_string_literals(make_fake_backend, inject_backend):
    b64 = FIXTURE.read_text()
    be = inject_backend(make_fake_backend(responses={"get": {"CompressedEntityXml": b64}}))
    root = ribbon.retrieve_entity_ribbon(be, "cwx_ticket")  # type: ignore[arg-type]
    assert root.tag == "RibbonDiffXml"
    # Verified live: inline literals, NOT parameter aliases.
    assert be.last_path == (
        "RetrieveEntityRibbon(EntityName='cwx_ticket',RibbonLocationFilter='All')")


def test_retrieve_application_ribbon_calls_parameterless_function(make_fake_backend, inject_backend):
    b64 = FIXTURE.read_text()
    # The application ribbon response uses a different key than the entity one.
    be = inject_backend(make_fake_backend(
        responses={"get": {"CompressedApplicationRibbonXml": b64}}))
    root = ribbon.retrieve_application_ribbon(be)  # type: ignore[arg-type]
    assert root.tag == "RibbonDiffXml"
    # Verified live: the function takes no parameters.
    assert be.last_path == "RetrieveApplicationRibbon()"


def test_retrieve_application_ribbon_missing_key_raises(make_fake_backend, inject_backend):
    be = inject_backend(make_fake_backend(responses={"get": {}}))
    with pytest.raises(ValueError, match="CompressedApplicationRibbonXml"):
        ribbon.retrieve_application_ribbon(be)  # type: ignore[arg-type]


SAMPLE_DIFF = """
<RibbonDiffXml>
  <CustomActions>
    <CustomAction Id="cwx_ticket.form.Validate.CustomAction"
                  Location="Mscrm.Form.cwx_ticket.MainTab.Save.Controls._children"
                  Sequence="50">
      <CommandUIDefinition>
        <Button Id="cwx_ticket.form.Validate.Button"
                Command="cwx_ticket.form.Validate.Command" LabelText="Validate"/>
      </CommandUIDefinition>
    </CustomAction>
  </CustomActions>
  <CommandDefinitions>
    <CommandDefinition Id="cwx_ticket.form.Validate.Command">
      <Actions>
        <JavaScriptFunction Library="$webresource:cwx_/scripts/x.js" FunctionName="ns.fn">
          <CrmParameter Value="PrimaryControl"/>
        </JavaScriptFunction>
      </Actions>
    </CommandDefinition>
  </CommandDefinitions>
</RibbonDiffXml>
"""


def test_list_custom_buttons_extracts_fields():
    diff = ET.fromstring(SAMPLE_DIFF)
    buttons = ribbon.list_custom_buttons(diff)
    assert len(buttons) == 1
    b = buttons[0]
    assert b.button_id == "cwx_ticket.form.Validate.CustomAction"
    assert b.label == "Validate"
    assert b.command == "cwx_ticket.form.Validate.Command"
    assert b.location == "Mscrm.Form.cwx_ticket.MainTab.Save.Controls._children"
    assert b.function == "ns.fn"
    assert b.library == "$webresource:cwx_/scripts/x.js"


def test_list_custom_buttons_empty_when_no_customactions():
    diff = ET.fromstring("<RibbonDiffXml><CustomActions/></RibbonDiffXml>")
    assert ribbon.list_custom_buttons(diff) == []


def test_slugify_label():
    assert ribbon.slugify("Validate Ticket!") == "ValidateTicket"
    assert ribbon.slugify("re-open") == "reopen"


def test_resolve_group_defaults_are_parametric():
    assert ribbon.resolve_group("form", "cwx_ticket", None) == \
        "Mscrm.Form.cwx_ticket.MainTab.Save"
    assert ribbon.resolve_group("homegrid", "cwx_ticket", None) == \
        "Mscrm.HomepageGrid.cwx_ticket.MainTab.Management"
    assert ribbon.resolve_group("subgrid", "cwx_ticket", None) == \
        "Mscrm.SubGrid.cwx_ticket.MainTab.Management"


def test_resolve_group_override_wins():
    assert ribbon.resolve_group("form", "cwx_ticket", "My.Custom.Group") == \
        "My.Custom.Group"


def test_build_button_ids():
    ids = ribbon.build_button_ids("cwx_ticket", "form", "Validate", None)
    assert ids.custom_action == "cwx_ticket.form.Validate.CustomAction"
    assert ids.button == "cwx_ticket.form.Validate.Button"
    assert ids.command == "cwx_ticket.form.Validate.Command"


def test_build_button_ids_with_override_base():
    ids = ribbon.build_button_ids("cwx_ticket", "form", "Validate", "my.base")
    assert ids.custom_action == "my.base.CustomAction"


def test_build_button_ids_empty_slug_raises():
    with pytest.raises(ValueError, match="empty slug"):
        ribbon.build_button_ids("cwx_ticket", "form", "!!!", None)


CUST_XML = """<ImportExportXml>
  <Entities>
    <Entity><Name>cwx_ticket</Name><RibbonDiffXml><CustomActions/></RibbonDiffXml></Entity>
    <Entity><Name>account</Name></Entity>
  </Entities>
</ImportExportXml>"""


def test_find_entity_node_case_insensitive():
    root = ET.fromstring(CUST_XML)
    node = ribbon.find_entity_node(root, "CWX_Ticket")
    assert node is not None
    assert node.findtext("Name") == "cwx_ticket"


def test_find_entity_node_missing_raises():
    root = ET.fromstring(CUST_XML)
    with pytest.raises(ValueError, match="not found in solution"):
        ribbon.find_entity_node(root, "cwx_missing")


def test_get_or_create_ribbon_diff_returns_existing():
    root = ET.fromstring(CUST_XML)
    entity = ribbon.find_entity_node(root, "cwx_ticket")
    diff = ribbon.get_or_create_ribbon_diff(entity)
    assert diff.tag == "RibbonDiffXml"
    assert diff.find("CustomActions") is not None


def test_get_or_create_ribbon_diff_creates_skeleton():
    root = ET.fromstring(CUST_XML)
    entity = ribbon.find_entity_node(root, "account")
    diff = ribbon.get_or_create_ribbon_diff(entity)
    assert diff.tag == "RibbonDiffXml"
    for child in ("CustomActions", "Templates", "CommandDefinitions",
                  "RuleDefinitions", "LocLabels"):
        assert diff.find(child) is not None
    # idempotent: second call returns the same element, no duplicate
    again = ribbon.get_or_create_ribbon_diff(entity)
    assert again is diff
    assert len(entity.findall("RibbonDiffXml")) == 1


def _empty_diff() -> ET.Element:
    root = ET.fromstring(
        "<ImportExportXml><Entities><Entity><Name>cwx_ticket</Name>"
        "</Entity></Entities></ImportExportXml>")
    return ribbon.get_or_create_ribbon_diff(ribbon.find_entity_node(root, "cwx_ticket"))


def test_add_custom_action_injects_three_nodes():
    diff = _empty_diff()
    ids = ribbon.build_button_ids("cwx_ticket", "form", "Validate", None)
    ribbon.add_custom_action(
        diff, ids=ids, group="Mscrm.Form.cwx_ticket.MainTab.Save",
        label="Validate", webresource="cwx_/scripts/x.js", function="ns.fn",
        param="PrimaryControl", sequence=50)
    buttons = ribbon.list_custom_buttons(diff)
    assert len(buttons) == 1
    b = buttons[0]
    assert b.button_id == ids.custom_action
    assert b.label == "Validate"
    assert b.function == "ns.fn"
    assert b.library == "$webresource:cwx_/scripts/x.js"
    action = diff.find(f".//CustomAction[@Id='{ids.custom_action}']")
    assert action is not None
    assert action.get("Location") == \
        "Mscrm.Form.cwx_ticket.MainTab.Save.Controls._children"
    crm_param = diff.find(".//JavaScriptFunction/CrmParameter")
    assert crm_param is not None and crm_param.get("Value") == "PrimaryControl"


def test_add_custom_action_rejects_id_collision():
    diff = _empty_diff()
    ids = ribbon.build_button_ids("cwx_ticket", "form", "Validate", None)
    ribbon.add_custom_action(
        diff, ids=ids, group="G", label="Validate",
        webresource="cwx_/scripts/x.js", function="ns.fn",
        param="PrimaryControl", sequence=50)
    with pytest.raises(ValueError, match="already exists"):
        ribbon.add_custom_action(
            diff, ids=ids, group="G", label="Validate",
            webresource="cwx_/scripts/x.js", function="ns.fn",
            param="PrimaryControl", sequence=50)


def test_remove_custom_action_deletes_action_and_command():
    diff = _empty_diff()
    ids = ribbon.build_button_ids("cwx_ticket", "form", "Validate", None)
    ribbon.add_custom_action(
        diff, ids=ids, group="G", label="Validate",
        webresource="cwx_/scripts/x.js", function="ns.fn",
        param="PrimaryControl", sequence=50)
    removed = ribbon.remove_custom_action(diff, ids.custom_action)
    assert removed is True
    assert ribbon.list_custom_buttons(diff) == []
    assert diff.find(f".//CommandDefinition[@Id='{ids.command}']") is None


def test_remove_custom_action_unknown_returns_false():
    diff = _empty_diff()
    assert ribbon.remove_custom_action(diff, "nope.CustomAction") is False


COMPOSED = """
<RibbonDefinitions>
  <RibbonDefinition>
    <Tabs><Tab><Groups><Group><Controls>
      <Button Id="Mscrm.HomepageGrid.account.Deactivate"
              Command="Mscrm.HomepageGrid.Deactivate"
              TemplateAlias="o2" LabelText="Deactivate"/>
      <Button Id="Mscrm.HomepageGrid.account.Delete"
              Command="Mscrm.DeleteSelectedRecord" LabelText="Delete"/>
      <Group Id="Mscrm.HomepageGrid.account.MainTab.Actions"/>
    </Controls></Group></Groups></Tab></Tabs>
  </RibbonDefinition>
</RibbonDefinitions>
"""


def test_find_composed_element_locates_button_by_id():
    root = ET.fromstring(COMPOSED)
    el = ribbon.find_composed_element(root, "Mscrm.HomepageGrid.account.Deactivate")
    assert el is not None
    assert el.tag == "Button"
    assert el.get("Command") == "Mscrm.HomepageGrid.Deactivate"


def test_find_composed_element_locates_group():
    root = ET.fromstring(COMPOSED)
    el = ribbon.find_composed_element(root, "Mscrm.HomepageGrid.account.MainTab.Actions")
    assert el is not None and el.tag == "Group"


def test_find_composed_element_missing_returns_none():
    root = ET.fromstring(COMPOSED)
    assert ribbon.find_composed_element(root, "Mscrm.Typo.NotHere") is None


def test_hide_button_display_rule_emits_two_false_rules():
    diff = _empty_diff()
    ribbon.hide_button_display_rule(diff, "Mscrm.HomepageGrid.Deactivate")
    cdef = diff.find(
        ".//CommandDefinition[@Id='Mscrm.HomepageGrid.Deactivate']")
    assert cdef is not None
    rule_ids = [r.get("Id") for r in cdef.findall("DisplayRules/DisplayRule")]
    assert rule_ids == ["Mscrm.HideOnModern", "Mscrm.ShowOnlyOnModern"]
    # empty EnableRules + Actions so only the always-false display rules govern it
    enable_rules = cdef.find("EnableRules")
    actions = cdef.find("Actions")
    assert enable_rules is not None and list(enable_rules) == []
    assert actions is not None and list(actions) == []


def test_hide_button_display_rule_rejects_duplicate_override():
    diff = _empty_diff()
    ribbon.hide_button_display_rule(diff, "Mscrm.HomepageGrid.Deactivate")
    with pytest.raises(ValueError, match="already overridden"):
        ribbon.hide_button_display_rule(diff, "Mscrm.HomepageGrid.Deactivate")


def test_hide_button_hide_action_emits_hidecustomaction():
    diff = _empty_diff()
    ribbon.hide_button_hide_action(diff, "Mscrm.HomepageGrid.account.Deactivate")
    actions = diff.find("CustomActions")
    assert actions is not None
    hide = actions.find("HideCustomAction")
    assert hide is not None
    assert hide.get("Location") == "Mscrm.HomepageGrid.account.Deactivate"
    assert hide.get("HideActionId")  # a non-empty unique id


def test_hide_button_hide_action_rejects_duplicate():
    diff = _empty_diff()
    ribbon.hide_button_hide_action(diff, "Mscrm.HomepageGrid.account.Deactivate")
    with pytest.raises(ValueError, match="already hidden"):
        ribbon.hide_button_hide_action(diff, "Mscrm.HomepageGrid.account.Deactivate")


def _make_solution_zip(path, cust_xml: str) -> None:
    import zipfile as zf
    with zf.ZipFile(path, "w") as z:
        z.writestr("solution.xml", "<ImportExportXml><SolutionManifest/></ImportExportXml>")
        z.writestr("customizations.xml", cust_xml)
        z.writestr("[Content_Types].xml", "<Types/>")


def test_apply_ribbon_change_rewrites_and_imports(monkeypatch, tmp_path):
    captured: dict[str, object] = {}

    def fake_export(backend, name, output_path, **kw):
        _make_solution_zip(output_path, CUST_XML)
        return {"output": str(output_path), "bytes": 1}

    def fake_validate(zip_path, *, backend=None, check_collisions=True):
        # read back the rewritten customizations so the test can assert on it
        captured["check_collisions"] = check_collisions
        import zipfile as zf
        with zf.ZipFile(zip_path) as z:
            captured["customizations"] = z.read("customizations.xml").decode()
        return {"valid": True, "findings": [], "checks_run": ["package"]}

    def fake_import(backend, zip_path, **kw):
        captured["imported"] = str(zip_path)
        return {"status": "succeeded"}

    def fake_publish(backend):
        captured["published"] = True
        return {"ok": True}

    monkeypatch.setattr(ribbon, "export_solution", fake_export)
    monkeypatch.setattr(ribbon, "validate_solution", fake_validate)
    monkeypatch.setattr(ribbon, "import_solution", fake_import)
    monkeypatch.setattr(ribbon, "publish_all", fake_publish)

    def mutate(cust_root: ET.Element) -> None:
        entity = ribbon.find_entity_node(cust_root, "cwx_ticket")
        diff = ribbon.get_or_create_ribbon_diff(entity)
        ids = ribbon.build_button_ids("cwx_ticket", "form", "Validate", None)
        ribbon.add_custom_action(
            diff, ids=ids, group="Mscrm.Form.cwx_ticket.MainTab.Save",
            label="Validate", webresource="cwx_/scripts/x.js", function="ns.fn",
            param="PrimaryControl", sequence=50)

    result = ribbon.apply_ribbon_change(
        object(), solution="MySol", entity="cwx_ticket", mutate=mutate)  # type: ignore[arg-type]

    assert result["status"] == "succeeded"
    assert captured["published"] is True
    assert "cwx_ticket.form.Validate.Button" in captured["customizations"]  # type: ignore[operator]
    # #269: a ribbon edit is a round-trip update-import; existing form GUIDs are
    # expected state, so the GUID-collision checks must be skipped (but backend is
    # still passed so the web-resource-ref check runs against the org).
    assert captured["check_collisions"] is False


def test_apply_ribbon_change_aborts_on_validation_error(monkeypatch, tmp_path):
    def fake_export(backend, name, output_path, **kw):
        _make_solution_zip(output_path, CUST_XML)
        return {"output": str(output_path)}

    def fake_validate(zip_path, *, backend=None, check_collisions=True):
        return {"valid": False,
                "findings": [{"severity": "error", "message": "bad ribbon"}],
                "checks_run": ["package"]}

    imported: list[str] = []
    monkeypatch.setattr(ribbon, "export_solution", fake_export)
    monkeypatch.setattr(ribbon, "validate_solution", fake_validate)
    monkeypatch.setattr(ribbon, "import_solution",
                        lambda *a, **k: imported.append("x"))
    monkeypatch.setattr(ribbon, "publish_all", lambda *a, **k: None)

    with pytest.raises(ValueError, match="validation failed"):
        ribbon.apply_ribbon_change(
            object(), solution="MySol", entity="cwx_ticket",  # type: ignore[arg-type]
            mutate=lambda r: None)
    assert imported == []  # never imported a failing package
