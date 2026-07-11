"""Unit tests for crm.utils.safe_xml (entity-safe XML parsing, #838)."""
# pyright: basic
from __future__ import annotations

import xml.etree.ElementTree as ET

import pytest

from crm.utils import safe_xml

# A billion-laughs style internal-entity bomb: nested <!ENTITY> declarations that
# would expand exponentially if the parser honoured them.
BILLION_LAUGHS = (
    '<?xml version="1.0"?>'
    '<!DOCTYPE lolz ['
    '<!ENTITY lol "lol">'
    '<!ENTITY lol2 "&lol;&lol;&lol;&lol;">'
    '<!ENTITY lol3 "&lol2;&lol2;&lol2;&lol2;">'
    ']>'
    '<lolz>&lol3;</lolz>'
)

# An external general entity pointing at a local file (classic XXE probe).
EXTERNAL_ENTITY = (
    '<?xml version="1.0"?>'
    '<!DOCTYPE foo [<!ENTITY xxe SYSTEM "file:///etc/passwd">]>'
    '<foo>&xxe;</foo>'
)


class TestRejectsEntityAttacks:
    def test_internal_entity_declaration_is_rejected(self):
        # Rejected before expansion — never returns a parsed tree.
        with pytest.raises(ET.ParseError):
            safe_xml.fromstring(BILLION_LAUGHS)

    def test_external_entity_declaration_is_rejected(self):
        with pytest.raises(ET.ParseError):
            safe_xml.fromstring(EXTERNAL_ENTITY)

    def test_rejection_is_a_parse_error_not_a_bare_valueerror(self):
        # The whole normalization contract: entity rejection surfaces as
        # ElementTree.ParseError so every existing `except ParseError` boundary
        # absorbs it. DefusedXmlException (a ValueError subclass) must not escape.
        with pytest.raises(ET.ParseError):
            safe_xml.fromstring(BILLION_LAUGHS)


class TestParsesOrdinaryXml:
    def test_namespace_free_customization_xml_round_trips(self):
        root = safe_xml.fromstring("<form><tab id='1'/></form>")
        assert root.tag == "form"
        tab = root.find("tab")
        assert tab is not None
        assert tab.get("id") == "1"

    def test_predefined_entities_and_char_refs_still_decode(self):
        # forbid_entities blocks <!ENTITY> *declarations*, never the predefined
        # &amp;/&lt; references or numeric char refs that real D365 XML relies on.
        root = safe_xml.fromstring('<a x="a &amp; b">t &lt; u &#65;</a>')
        assert root.get("x") == "a & b"
        assert root.text == "t < u A"

    def test_namespaced_csdl_style_xml_parses(self):
        root = safe_xml.fromstring('<Schema xmlns="http://x"><E/></Schema>')
        assert root.tag == "{http://x}Schema"

    def test_bytes_input_is_accepted(self):
        # ribbon reads customizations.xml as bytes straight from the zip.
        root = safe_xml.fromstring(b"<fetch><entity name='account'/></fetch>")
        assert root.tag == "fetch"

    def test_malformed_xml_still_raises_parse_error(self):
        with pytest.raises(ET.ParseError):
            safe_xml.fromstring("<not-closed>")


class TestCallerContractsPreserved:
    """Each affected caller keeps its established failure contract when it meets a
    hostile document — a typed error, a validation finding, an empty/default
    result, or a non-blocking advisory — rather than leaking a raw traceback."""

    def test_shared_customization_parser_raises_typed_error(self):
        from crm.core import xml_edit
        from crm.utils.d365_backend import D365Error

        with pytest.raises(D365Error):
            xml_edit.parse_xml(BILLION_LAUGHS, label="form's FormXml")

    def test_fetchxml_preview_raises_typed_error(self):
        from crm.core import bulk_delete
        from crm.utils.d365_backend import D365Error

        with pytest.raises(D365Error):
            bulk_delete._with_total_record_count(BILLION_LAUGHS)

    def test_import_job_data_raises_typed_error(self):
        from crm.core import solution_transfer
        from crm.utils.d365_backend import D365Error

        with pytest.raises(D365Error):
            solution_transfer.parse_import_job_data(BILLION_LAUGHS)

    def test_fetchxml_entity_name_raises_usage_error(self):
        import click

        from crm.commands.query import _parse_entity_name_from_fetchxml

        with pytest.raises(click.UsageError):
            _parse_entity_name_from_fetchxml(BILLION_LAUGHS)

    def test_best_effort_layout_reader_returns_empty(self):
        from crm.core.views import parse_fetch_order_filter, parse_layout_columns

        assert parse_layout_columns(BILLION_LAUGHS) == []
        assert parse_fetch_order_filter(BILLION_LAUGHS) == (None, False, False)

    def test_workflow_validation_returns_finding_not_raise(self):
        from crm.core.workflow import validate_workflow_xaml

        warnings = validate_workflow_xaml(BILLION_LAUGHS, [])
        assert any("malformed XAML" in w for w in warnings)

    def test_solution_sniff_stays_non_blocking(self):
        import io
        import zipfile

        from crm.core.solution_transfer import _sniff_solution_managed

        buf = io.BytesIO()
        with zipfile.ZipFile(buf, "w") as zf:
            zf.writestr("solution.xml", BILLION_LAUGHS)
        buf.seek(0)
        # Advisory sniff must degrade to "unknown" (None), never raise or block.
        assert _sniff_solution_managed(buf) is None
