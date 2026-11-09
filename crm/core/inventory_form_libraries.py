"""Form XML half of the `form-odata-adapter` transform (ADR 0034).

A form's ``<formLibraries>`` lists the script web resources it loads, in load order. A patched
library calls the generated OData v2 adapter, so the adapter must load first. This module maps
each ``<Library>`` to its bytes in the FormXml text and plans the one insert that lists the
adapter ahead of the first patched library, leaving the rest of the file byte for byte as it was.
"""

from __future__ import annotations

import re
import uuid
from collections.abc import Collection
from dataclasses import dataclass
from html import unescape
from xml.etree.ElementTree import ParseError

from crm.core.inventory_transforms import Edit
from crm.utils.safe_xml import fromstring

_TAG = re.compile(rb"<Library\b(?:\"[^\"]*\"|'[^']*'|[^>\"'])*>")
_NAME = re.compile(rb"\bname\s*=\s*(\"[^\"]*\"|'[^']*')")


@dataclass(frozen=True)
class LibraryTag:
    """One ``<Library>`` of a form: the web resource it names and where its tag starts."""

    name: str
    start: int


def form_library_tags(text: bytes) -> list[LibraryTag] | None:
    """The libraries of a FormXml in load order, or None when they cannot be mapped to the text.

    The text and a real XML parse must agree on every library's name and on how many there are, so
    a tag in a comment or an unreadable file is refused rather than guessed at.
    """
    try:
        parsed = fromstring(text.decode("utf-8"))
    except (ParseError, ValueError):
        return None
    out: list[LibraryTag] = []
    for m in _TAG.finditer(text):
        name = _NAME.search(m.group(0))
        out.append(
            LibraryTag(unescape(name.group(1)[1:-1].decode("utf-8")) if name else "", m.start())
        )
    listed = [lib.get("name") or "" for lib in parsed.findall("./formLibraries/Library")]
    if len(parsed.findall(".//Library")) != len(out) or listed != [t.name for t in out]:
        return None
    return out


def library_id(form_key: str, adapter: str) -> str:
    """A brace-wrapped ``libraryUniqueId`` that depends only on the form and the adapter's name,
    so a rerun writes the same patch.
    """
    seed = f"crm:inventory-convert:form-odata-adapter:{form_key}:{adapter.lower()}"
    return "{" + str(uuid.uuid5(uuid.NAMESPACE_URL, seed)).upper() + "}"


def plan_adapter_library(
    tags: list[LibraryTag], adapter: str, rewritten: Collection[str], unique_id: str
) -> Edit | str | None:
    """The insert that lists *adapter* just before the first library named in *rewritten*.

    *rewritten* holds lowercase library names. Returns None when the adapter is already listed
    ahead of that library (the form needs nothing), and else the reason it cannot be inserted.
    """
    names = [t.name.lower() for t in tags]
    first = next((i for i, n in enumerate(names) if n in rewritten), None)
    if first is None:
        return "no library the form lists had its OData v2 calls rewritten in this run"
    listed = adapter.lower() in names
    if listed and names.index(adapter.lower()) < first:
        return None
    if listed:
        return (
            f"the adapter {adapter} is already listed after {tags[first].name}:"
            " move it ahead of that library"
        )
    name = adapter.replace("&", "&amp;").replace("<", "&lt;").replace('"', "&quot;")
    element = f'<Library name="{name}" libraryUniqueId="{unique_id}" />'
    at = tags[first].start
    return Edit(at, at, element.encode("utf-8"))


def adapter_gate(
    original: bytes, patched: bytes, adapter: str, rewritten: Collection[str]
) -> str | None:
    """The emit gate for a library patch: None when the patch may be written, else why not.

    The library list must still map to the text and differ from the original by exactly one
    inserted adapter, ahead of every library in *rewritten* that the original listed.
    """
    before, after = form_library_tags(original), form_library_tags(patched)
    if before is None or after is None:
        return "the patched form's library list can no longer be mapped to its text"
    old, new = [t.name for t in before], [t.name for t in after]
    at = next((i for i, n in enumerate(new) if n.lower() == adapter.lower()), None)
    if at is None or new[:at] + new[at + 1 :] != old:
        return "the patch does not insert the adapter into the library list"
    if any(n.lower() in rewritten for n in new[:at]):
        return "the adapter is still listed after a library it serves"
    return None
