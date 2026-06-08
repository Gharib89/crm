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
