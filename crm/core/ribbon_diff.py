"""Read the ribbon diffs out of an exported solution (#1100).

A solution's ``customizations.xml`` holds one ``RibbonDiffXml`` for the application
(a root-level child) and one per entity (``Entities/Entity/RibbonDiffXml``): the
customizations the solution layers over the stock ribbon. Most are empty skeletons
and an entity that takes no ribbon carries ``<RibbonNotSupported/>``, so only a diff
with content is kept. The file is streamed, never held as a tree: an org-wide export
of a large org carries a ``customizations.xml`` of tens of MB.
"""

from __future__ import annotations

import xml.etree.ElementTree as ET
import zipfile
from pathlib import Path

from crm.utils.safe_xml import iterparse

CUSTOMIZATIONS = "customizations.xml"
# Children that hold the content directly, and those that hold it one level down.
_FLAT = ("CustomActions", "CommandDefinitions", "LocLabels")
_NESTED = ("RuleDefinitions", "Templates")


def _has_content(diff: ET.Element) -> bool:
    for child in diff:
        if child.tag in _FLAT and len(child):
            return True
        if child.tag in _NESTED and any(len(grandchild) for grandchild in child):
            return True
    return False


def read_ribbon_diffs(zip_path: Path) -> dict[str | None, bytes]:
    """The raw ``RibbonDiffXml`` of the application (key ``None``) and of each entity
    (its lower-cased logical name) that holds content, from the solution zip at *zip_path*.

    Raises:
        KeyError: the zip has no ``customizations.xml``.
        zipfile.BadZipFile: *zip_path* is not a zip.
        xml.etree.ElementTree.ParseError: the document is malformed or declares an entity.
    """
    diffs: dict[str | None, bytes] = {}
    path: list[str] = []
    entity: str | None = None
    with zipfile.ZipFile(zip_path) as archive, archive.open(CUSTOMIZATIONS) as member:
        for event, elem in iterparse(member, ("start", "end")):
            if event == "start":
                path.append(elem.tag)
                continue
            path.pop()
            if "RibbonDiffXml" in path:
                continue  # inside a diff: kept whole until the diff itself ends
            # Depth counts ancestors: 1 is a child of the document root (the application's
            # diff), 2 an `Entities/Entity`, 3 a child of an entity (its diff and its `Name`).
            depth = len(path)
            if elem.tag == "Name" and path[-2:] == ["Entities", "Entity"]:
                entity = (elem.text or "").strip().lower()
            elif elem.tag == "RibbonDiffXml" and depth in (1, 3):
                key = None if depth == 1 else entity
                if _has_content(elem) and (depth == 1 or key):
                    diffs.setdefault(key, ET.tostring(elem, encoding="utf-8"))
                elem.clear()
            elif elem.tag == "Entity" and depth == 2:
                entity = None
                elem.clear()
            elif depth >= 3 or (depth == 1 and elem.tag != "Entities"):
                elem.clear()
    return diffs
