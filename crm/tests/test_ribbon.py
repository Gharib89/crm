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
