"""Unit tests for crm.core.xml_edit (shared XML-edit safety primitives)."""
# pyright: basic
from __future__ import annotations

import re
import uuid

import pytest
import requests_mock

from crm.core import xml_edit
from crm.utils.d365_backend import D365Error

_G = r"[0-9a-fA-F]{8}-[0-9a-fA-F]{4}-[0-9a-fA-F]{4}-[0-9a-fA-F]{4}-[0-9a-fA-F]{12}"

# An internal-id attribute pattern in the shape regenerate_guids requires.
_ID_RE = re.compile(
    r"""(?P<attr>(?<![\w])id|labelid)(?P<eq>\s*=\s*)(?P<q>["'])"""
    r"""(?P<brace>\{)?(?P<guid>""" + _G + r""")(?(brace)\})(?P=q)""",
    re.VERBOSE,
)


class TestParseSerialize:
    def test_parse_then_serialize_round_trips(self):
        root = xml_edit.parse_xml("<form><tab id='1' /></form>")
        assert root.tag == "form"
        assert "<tab" in xml_edit.serialize_xml(root)

    def test_malformed_xml_raises_typed_error(self):
        with pytest.raises(D365Error, match="Could not parse the FormXml"):
            xml_edit.parse_xml("<form><tab>", label="FormXml")


class TestFreshGuid:
    def test_braced_by_default(self):
        g = xml_edit.fresh_guid()
        assert g.startswith("{") and g.endswith("}")
        assert str(uuid.UUID(g.strip("{}"))) == g.strip("{}")

    def test_unbraced(self):
        g = xml_edit.fresh_guid(braced=False)
        assert "{" not in g and str(uuid.UUID(g)) == g


class TestRegenerateGuids:
    def test_consistent_mapping_one_value_per_source(self):
        shared = "abababab-abab-abab-abab-abababababab"
        xml = f'<x id="{{{shared}}}" labelid="{{{shared}}}" />'
        out, mapping = xml_edit.regenerate_guids(xml, _ID_RE)
        vals = re.findall(r'(?:id|labelid)="\{(' + _G + r')\}"', out)
        assert len(vals) == 2 and vals[0] == vals[1], vals
        assert vals[0].lower() != shared
        assert mapping == {shared: vals[0]}

    def test_preserves_braces_and_quote_style(self):
        xml = "<x id='11111111-1111-1111-1111-111111111111' />"
        out, _ = xml_edit.regenerate_guids(xml, _ID_RE)
        # single-quoted, unbraced source stays single-quoted, unbraced
        assert re.fullmatch(r"<x id='" + _G + r"' />", out)


class TestExternalGuidGuard:
    def test_non_mutating_edit_passes(self):
        before = '<x classid="{cccccccc-cccc-cccc-cccc-cccccccccccc}" id="1" />'
        after = '<x classid="{cccccccc-cccc-cccc-cccc-cccccccccccc}" id="1" foo="bar" />'
        # No GUID changed → no raise.
        xml_edit.assert_external_guids_intact(before, after)

    def test_mutating_external_guid_raises(self):
        # A changed classid is the canonical external-ref mutation the guard exists
        # to catch (#275-class corruption): well-formed, XSD-valid, silently broken.
        before = '<x classid="{cccccccc-cccc-cccc-cccc-cccccccccccc}" />'
        after = '<x classid="{dddddddd-dddd-dddd-dddd-dddddddddddd}" />'
        with pytest.raises(D365Error, match="non-target external GUID"):
            xml_edit.assert_external_guids_intact(before, after)

    def test_regenerated_ids_are_excused(self):
        before = '<x id="{aaaaaaaa-aaaa-aaaa-aaaa-aaaaaaaaaaaa}" />'
        out, mapping = xml_edit.regenerate_guids(before, _ID_RE)
        # The id we deliberately regenerated is excused; guard must not fire.
        xml_edit.assert_external_guids_intact(before, out, regenerated=mapping)

    def test_custom_message(self):
        with pytest.raises(D365Error, match="custom boom"):
            xml_edit.assert_external_guids_intact(
                '<x a="11111111-1111-1111-1111-111111111111" />',
                '<x a="22222222-2222-2222-2222-222222222222" />',
                message="custom boom")


class TestClassidsIntact:
    def test_unchanged_classids_pass(self):
        xml = '<c classid="{cccccccc-cccc-cccc-cccc-cccccccccccc}" />'
        xml_edit.assert_classids_intact(xml, xml.replace("/>", 'x="1" />'))

    def test_changed_classid_raises(self):
        before = '<c classid="{cccccccc-cccc-cccc-cccc-cccccccccccc}" />'
        after = '<c classid="{dddddddd-dddd-dddd-dddd-dddddddddddd}" />'
        with pytest.raises(D365Error, match="changed a control classid"):
            xml_edit.assert_classids_intact(before, after)


class TestGuidSetAndNodePresent:
    def test_guid_set_lowercased(self):
        xml = '<x a="AAAAAAAA-AAAA-AAAA-AAAA-AAAAAAAAAAAA" />'
        assert xml_edit.guid_set(xml) == {"aaaaaaaa-aaaa-aaaa-aaaa-aaaaaaaaaaaa"}

    def test_node_present_matches_attrs(self):
        root = xml_edit.parse_xml(
            '<form><tabs><tab name="general" /></tabs></form>')
        assert xml_edit.node_present(root, "tab", name="general")
        assert not xml_edit.node_present(root, "tab", name="details")
        assert not xml_edit.node_present(root, "section")


def _systemforms_id(backend, formid):
    return backend.url_for(f"systemforms({formid})")


class TestCommitXmlPatch:
    _FORMID = "11112222-3333-4444-5555-666677778888"

    def _base_result(self):
        return {"formid": self._FORMID, "action": "add-field"}

    def test_dry_run_issues_zero_http(self, dry_backend):
        with requests_mock.Mocker() as m:
            out = xml_edit.commit_xml_patch(
                dry_backend, entity_set="systemforms", record_id=self._FORMID,
                column="formxml", new_xml="<form/>", result=self._base_result(),
                dry_run_flag="would_add", publish=True)
            assert m.call_count == 0
        assert out["_dry_run"] is True
        assert out["would_add"] is True
        assert "updated" not in out

    def test_patch_then_publish_then_read_back_in_order(self, backend):
        seen: list[str] = []
        read_xml: list[str] = []
        with requests_mock.Mocker() as m:
            m.patch(_systemforms_id(backend, self._FORMID), status_code=204,
                    additional_matcher=lambda r: seen.append("patch") is None or True)
            m.post(backend.url_for("PublishAllXml"), status_code=204,
                   additional_matcher=lambda r: seen.append("publish") is None or True)
            m.get(_systemforms_id(backend, self._FORMID),
                  json={"formxml": "<form published='1'/>"},
                  additional_matcher=lambda r: seen.append("get") is None or True)
            out = xml_edit.commit_xml_patch(
                backend, entity_set="systemforms", record_id=self._FORMID,
                column="formxml", new_xml="<form/>", result=self._base_result(),
                dry_run_flag="would_add", publish=True,
                read_back=lambda xml: read_xml.append(xml))
        assert out["updated"] is True
        assert out["published"] is True
        # publish-before-read-back ordering is load-bearing.
        assert seen == ["patch", "publish", "get"]
        assert read_xml == ["<form published='1'/>"]

    def test_no_read_back_skips_get(self, backend):
        with requests_mock.Mocker() as m:
            m.patch(_systemforms_id(backend, self._FORMID), status_code=204)
            out = xml_edit.commit_xml_patch(
                backend, entity_set="systemforms", record_id=self._FORMID,
                column="formxml", new_xml="<form/>", result=self._base_result(),
                dry_run_flag="would_add", publish=False)
            # PATCH only — no publish, no read-back GET.
            assert m.call_count == 1
            assert m.last_request.method == "PATCH"
        assert out["updated"] is True

    def test_solution_header_sent(self, backend):
        with requests_mock.Mocker() as m:
            m.patch(_systemforms_id(backend, self._FORMID), status_code=204)
            xml_edit.commit_xml_patch(
                backend, entity_set="systemforms", record_id=self._FORMID,
                column="formxml", new_xml="<form/>", result=self._base_result(),
                dry_run_flag="would_add", publish=False, solution="mysol")
            assert m.last_request.headers.get("MSCRM.SolutionUniqueName") == "mysol"
