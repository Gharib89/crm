"""Entity ribbon (command-bar) read + edit logic — issue #142.

Reads via RetrieveEntityRibbon (decode the zipped CompressedEntityXml) and edits
the entity's RibbonDiffXml inside a user-supplied solution, applying through the
export -> validate -> import -> publish path.
"""
from __future__ import annotations

import base64
import io
import zipfile
import xml.etree.ElementTree as ET
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from crm.utils.d365_backend import D365Backend

_RIBBON_MEMBER = "RibbonXml.xml"


def decode_compressed_ribbon(compressed_b64: str) -> ET.Element:
    """Decode a RetrieveEntityRibbon ``CompressedEntityXml`` value.

    The value is base64 over a ZIP archive (PK header — NOT gzip) whose
    ``RibbonXml.xml`` member is the ribbon document. Returns its root element.
    """
    raw = base64.b64decode(compressed_b64)
    if raw[:2] != b"PK":
        raise ValueError("CompressedEntityXml is not a ZIP archive (no PK header)")
    with zipfile.ZipFile(io.BytesIO(raw)) as zf:
        names = zf.namelist()
        member = _RIBBON_MEMBER if _RIBBON_MEMBER in names else next(
            (n for n in names if n.lower().endswith(".xml")
             and not n.startswith("[")), names[0])
        xml_bytes = zf.read(member)
    return ET.fromstring(xml_bytes)


def retrieve_entity_ribbon(backend: "D365Backend", entity: str) -> ET.Element:
    """Call RetrieveEntityRibbon for ``entity`` and return the decoded ribbon root.

    The enum filter MUST be an inline OData string literal (``'All'``); parameter
    aliases are rejected by the server (verified live). Returns the full composed
    ribbon (system + custom).
    """
    safe = entity.replace("'", "''")
    path = (f"RetrieveEntityRibbon(EntityName='{safe}',"
            f"RibbonLocationFilter='All')")
    resp = backend.get(path)
    if not isinstance(resp, dict) or "CompressedEntityXml" not in resp:
        raise ValueError(
            f"RetrieveEntityRibbon returned no CompressedEntityXml for {entity!r}")
    return decode_compressed_ribbon(str(resp["CompressedEntityXml"]))
