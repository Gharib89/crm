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


class _FakeBackend:
    """Minimal D365Backend stand-in: records the GET path, returns canned JSON."""
    def __init__(self, compressed_b64: str) -> None:
        self.compressed_b64 = compressed_b64
        self.last_path: str | None = None

    def get(self, path: str, **kw: object) -> dict[str, object]:
        self.last_path = path
        return {"CompressedEntityXml": self.compressed_b64}


def test_retrieve_entity_ribbon_uses_inline_string_literals():
    be = _FakeBackend(FIXTURE.read_text())
    root = ribbon.retrieve_entity_ribbon(be, "cwx_ticket")  # type: ignore[arg-type]
    assert root.tag == "RibbonDiffXml"
    # Verified live: inline literals, NOT parameter aliases.
    assert be.last_path == (
        "RetrieveEntityRibbon(EntityName='cwx_ticket',RibbonLocationFilter='All')")


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
