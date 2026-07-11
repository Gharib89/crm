"""Entity-safe XML parsing for org-supplied documents (#838).

Org-supplied XML — solution/customization archives, Web-API-returned
formxml/fetchxml/sitemapxml/CSDL, workflow XAML, and CLI/local-file inputs —
must be parsed here rather than with stdlib ``xml.etree.ElementTree``. Stdlib
ElementTree expands internal entities, so a hostile document crossing the org
boundary can carry an entity-expansion bomb (billion-laughs DoS). ``defusedxml``
refuses ``<!ENTITY>`` declarations (internal *and* external) before expansion or
resolution; the predefined ``&amp;``/``&lt;`` references and numeric character
references that real D365 XML depends on are untouched, so ordinary documents
round-trip byte-for-byte unchanged.

``defusedxml`` reports a rejection as ``DefusedXmlException`` — a ``ValueError``
subclass, *not* an ``ElementTree.ParseError``. :func:`fromstring` normalizes it
into ``ParseError`` so every existing ``except ParseError`` boundary in the
codebase — typed CLI errors, validation findings, best-effort empty results,
advisory sniffs — absorbs a hostile document exactly as it already absorbs
malformed XML, with no per-caller change. The distinction between an attack and a
typo is not actionable at the CLI boundary: both mean "this document cannot be
safely used".

Kept in ``crm/utils`` (basic pyright mode) so the untyped ``defusedxml`` import
stays out of the strict ``crm/core`` surface while callers still see the typed
``ET.Element`` signature.
"""
# pyright: basic
from __future__ import annotations

import xml.etree.ElementTree as ET

from defusedxml.ElementTree import fromstring as _defused_fromstring
from defusedxml.common import DefusedXmlException

__all__ = ["fromstring"]


def fromstring(text: "str | bytes") -> "ET.Element":
    """Parse an XML document from a string/bytes, rejecting entity attacks.

    A drop-in for ``xml.etree.ElementTree.fromstring`` on org-supplied input.
    Raises ``ElementTree.ParseError`` both for malformed XML (unchanged) and for
    a document that declares an internal or external entity (normalized from
    ``defusedxml``'s ``DefusedXmlException``).
    """
    try:
        return _defused_fromstring(text)
    except DefusedXmlException as exc:
        raise ET.ParseError(f"unsafe XML rejected: {exc}") from exc
