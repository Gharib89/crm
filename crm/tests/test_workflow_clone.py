"""Unit tests for crm.core.workflow clone helpers."""
# pyright: basic
from __future__ import annotations

import pytest

from crm.core.workflow import retarget_xaml

_SRC_ID = "8f9e7a6b-5c4d-3e2f-1a0b-9c8d7e6f5a4b"
_DST_ID = "11112222-3333-4444-5555-666677778888"

_XAML = (
    '<?xml version="1.0" encoding="utf-16"?>\n'
    '<Activity x:Class="XrmWorkflow8f9e7a6b5c4d3e2f1a0b9c8d7e6f5a4b" '
    'xmlns:this="clr-namespace:XrmWorkflow8f9e7a6b5c4d3e2f1a0b9c8d7e6f5a4b">\n'
    '  <mxsw:GetEntityProperty Attribute="cwx_name" Entity="cwx_ticket" EntityName="cwx_ticket" />\n'
    '  <Comment>lookup field cwx_ticketcategory stays on cwx_ticket</Comment>\n'
    '  <this:XrmWorkflow8f9e7a6b5c4d3e2f1a0b9c8d7e6f5a4b.Variables />\n'
    '</Activity>\n'
)


class TestRetargetXaml:
    def test_rewrites_entity_refs_with_word_boundary(self):
        out = retarget_xaml(_XAML, src_entity="cwx_ticket", dst_entity="cwx_ticketclone",
                            src_id=_SRC_ID, dst_id=_DST_ID)
        assert 'Entity="cwx_ticketclone"' in out
        assert 'EntityName="cwx_ticketclone"' in out
        # the trap token must NOT be corrupted into cwx_ticketclonecategory
        assert "cwx_ticketcategory" in out
        assert "cwx_ticketclonecategory" not in out

    def test_rewrites_xclass_and_element_tag_id_dash_stripped(self):
        out = retarget_xaml(_XAML, src_entity="cwx_ticket", dst_entity="cwx_ticketclone",
                            src_id=_SRC_ID, dst_id=_DST_ID)
        dst_stripped = "11112222333344445555666677778888"
        assert f"XrmWorkflow{dst_stripped}" in out
        assert "XrmWorkflow8f9e7a6b5c4d3e2f1a0b9c8d7e6f5a4b" not in out
        # both x:Class and the this: element tag are rewritten
        assert out.count(f"XrmWorkflow{dst_stripped}") == 3

    def test_leaves_unrelated_attribute_names_untouched(self):
        out = retarget_xaml(_XAML, src_entity="cwx_ticket", dst_entity="cwx_ticketclone",
                            src_id=_SRC_ID, dst_id=_DST_ID)
        assert 'Attribute="cwx_name"' in out

    def test_noop_when_nothing_matches(self):
        out = retarget_xaml("<Activity/>", src_entity="cwx_ticket",
                            dst_entity="cwx_ticketclone", src_id=_SRC_ID, dst_id=_DST_ID)
        assert out == "<Activity/>"
