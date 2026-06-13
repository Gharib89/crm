"""Unit tests for crm.core.forms."""
# pyright: basic
from __future__ import annotations

import re
import uuid

import requests_mock

_FORM_ROW = {
    "formid": "11112222-3333-4444-5555-666677778888",
    "name": "Information",
    "objecttypecode": "new_project",
    "type": 2,
    "formxml": "<form><tab><control id='new_code' datafieldname='new_code' /></tab></form>",
    "description": "Main form",
    "isdefault": True,
}


def _forms_url(backend) -> str:
    return backend.url_for("systemforms")


class TestReadEntityForms:
    def test_reads_main_forms(self, backend):
        from crm.core import forms
        with requests_mock.Mocker() as m:
            m.get(_forms_url(backend), json={"value": [_FORM_ROW]})
            result = forms.read_entity_forms(backend, "new_project")
        assert len(result) == 1
        f = result[0]
        assert f["formid"] == _FORM_ROW["formid"]
        assert f["name"] == "Information"
        assert f["objecttypecode"] == "new_project"
        assert f["type"] == 2
        assert "<form>" in f["formxml"]

    def test_filters_by_objecttypecode_in_request(self, backend):
        from crm.core import forms
        with requests_mock.Mocker() as m:
            m.get(_forms_url(backend), json={"value": []})
            forms.read_entity_forms(backend, "new_project")
        assert "objecttypecode" in m.last_request.url and "new_project" in m.last_request.url

    def test_default_restricts_to_main_form_type(self, backend):
        from crm.core import forms
        with requests_mock.Mocker() as m:
            m.get(_forms_url(backend), json={"value": []})
            forms.read_entity_forms(backend, "new_project")
        assert "type" in m.last_request.url and "2" in m.last_request.url

    def test_escapes_single_quote_in_entity_name(self, backend):
        from crm.core import forms
        with requests_mock.Mocker() as m:
            m.get(_forms_url(backend), json={"value": []})
            forms.read_entity_forms(backend, "it's_table")
        assert "it%27%27s_table" in m.last_request.url


class TestRetargetFormxml:
    def test_rewrites_whole_word_entity_refs(self):
        from crm.core.forms import retarget_formxml
        xml = ('<form><control entityname="new_project" /></form>')
        out = retarget_formxml(xml, src_entity="new_project", dst_entity="cwx_ticketclone")
        assert 'entityname="cwx_ticketclone"' in out

    def test_protects_attribute_datafieldnames(self):
        from crm.core.forms import retarget_formxml
        xml = ('<cell><control id="new_projectid" datafieldname="new_projectid" />'
               '<control datafieldname="new_project_code" /></cell>')
        out = retarget_formxml(xml, src_entity="new_project", dst_entity="cwx_ticketclone")
        assert 'datafieldname="new_projectid"' in out
        assert 'datafieldname="new_project_code"' in out
        assert "cwx_ticketclone" not in out

    def test_noop_when_entity_absent(self):
        from crm.core.forms import retarget_formxml
        out = retarget_formxml("<form/>", src_entity="new_project", dst_entity="cwx_ticketclone")
        assert out == "<form/>"


class TestCloneFormToEntity:
    def test_posts_retargeted_form(self, backend):
        from crm.core import forms
        form = {
            "formid": "old", "name": "Information", "objecttypecode": "new_project",
            "type": 2,
            "formxml": '<form><control entityname="new_project" /></form>',
            "description": "Main form", "isdefault": True,
        }
        with requests_mock.Mocker() as m:
            m.post(backend.url_for("systemforms"), status_code=204, headers={
                "OData-EntityId":
                    backend.url_for("systemforms(99998888-7777-6666-5555-444433332222)"),
            })
            out = forms.clone_form_to_entity(backend, form, "cwx_ticketclone")
        body = m.last_request.json()
        assert body["objecttypecode"] == "cwx_ticketclone"
        assert 'entityname="cwx_ticketclone"' in body["formxml"]
        assert body["name"] == "Information"
        assert body["type"] == 2
        assert out["created"] is True
        assert out["formid"] == "99998888-7777-6666-5555-444433332222"

    def test_adds_solution_header_when_given(self, backend):
        from crm.core import forms
        form = {"formid": "old", "name": "F", "objecttypecode": "new_project",
                "type": 2, "formxml": "<form/>", "description": None, "isdefault": False}
        with requests_mock.Mocker() as m:
            m.post(backend.url_for("systemforms"), status_code=204, headers={
                "OData-EntityId": backend.url_for("systemforms(99998888-7777-6666-5555-444433332222)"),
            })
            forms.clone_form_to_entity(backend, form, "cwx_ticketclone", solution="MySol")
        assert m.last_request.headers.get("MSCRM.SolutionUniqueName") == "MySol"


# Issue #268: cloning the SAME source form twice collides on on-prem v9.x because
# the form's internal registration GUIDs (labelid / layout id / uniqueid / handler-
# & library-UniqueId) are reused verbatim and must be org-unique (0x8004f658).
# Each clone must POST FormXML whose internal ids are freshly regenerated, while
# GUIDs that REFERENCE external objects (classid control types, <Role Id> security
# roles, <ViewId>/<QuickFormId> lookups) are preserved untouched.

_G = "[0-9a-fA-F]{8}-[0-9a-fA-F]{4}-[0-9a-fA-F]{4}-[0-9a-fA-F]{4}-[0-9a-fA-F]{12}"

# Source-form GUIDs that MUST be regenerated (form-internal registrations).
_SRC_TAB_ID = "11111111-1111-1111-1111-111111111111"
_SRC_TAB_LABEL = "22222222-2222-2222-2222-222222222222"
_SRC_SEC_ID = "33333333-3333-3333-3333-333333333333"
_SRC_CELL_LABEL = "44444444-4444-4444-4444-444444444444"
_SRC_UNIQUEID = "55555555-5555-5555-5555-555555555555"
_SRC_HANDLER = "66666666-6666-6666-6666-666666666666"
_SRC_LIBRARY = "77777777-7777-7777-7777-777777777777"
_REGEN_SRC_GUIDS = {
    _SRC_TAB_ID, _SRC_TAB_LABEL, _SRC_SEC_ID, _SRC_CELL_LABEL,
    _SRC_UNIQUEID, _SRC_HANDLER, _SRC_LIBRARY,
}
# Source-form GUIDs that MUST be preserved (references to external objects).
# Obvious placeholder GUIDs — never real control-class / role / view / quick-form
# identifiers (this is a public repo); they only need to be distinct + GUID-shaped.
_KEEP_CLASSID = "CCCCCCCC-CCCC-CCCC-CCCC-CCCCCCCCCCCC"
_KEEP_ROLE = "DDDDDDDD-DDDD-DDDD-DDDD-DDDDDDDDDDDD"
_KEEP_VIEW = "EEEEEEEE-EEEE-EEEE-EEEE-EEEEEEEEEEEE"
_KEEP_QUICKFORM = "FFFFFFFF-FFFF-FFFF-FFFF-FFFFFFFFFFFF"
_KEEP_SRC_GUIDS = {_KEEP_CLASSID, _KEEP_ROLE, _KEEP_VIEW, _KEEP_QUICKFORM}

_SOURCE_FORMXML = (
    "<form><tabs>"
    f'<tab name="general" id="{{{_SRC_TAB_ID}}}" labelid="{{{_SRC_TAB_LABEL}}}">'
    '<labels><label description="General" languagecode="1033" /></labels>'
    "<columns><column width=\"100%\"><sections>"
    f'<section name="s1" id="{{{_SRC_SEC_ID}}}"><rows><row>'
    f'<cell labelid="{{{_SRC_CELL_LABEL}}}">'
    f'<control id="new_code" classid="{{{_KEEP_CLASSID}}}" datafieldname="new_code" '
    f'uniqueid="{{{_SRC_UNIQUEID}}}" /></cell>'
    f'<cell><control id="sub1" handlerUniqueId="{{{_SRC_HANDLER}}}"><parameters>'
    f"<ViewId>{{{_KEEP_VIEW}}}</ViewId>"
    f'<QuickFormId entityname="contact">{{{_KEEP_QUICKFORM}}}</QuickFormId>'
    "</parameters></control></cell></row></rows></section></sections></column></columns>"
    "</tab></tabs>"
    f'<formLibraries><Library libraryUniqueId="{{{_SRC_LIBRARY}}}" /></formLibraries>'
    f'<controlDescriptions><controlDescription><Roles><Role Id="{{{_KEEP_ROLE}}}" />'
    "</Roles></controlDescription></controlDescriptions></form>"
)
_SOURCE_FORM = {
    "formid": "old", "name": "Information", "objecttypecode": "new_project",
    "type": 2, "formxml": _SOURCE_FORMXML, "description": "Main form",
    "isdefault": True,
}


def _all_guids(xml: str) -> set[str]:
    return {g.upper() for g in re.findall(_G, xml)}


class TestRegenerateFormCloneIds:
    def test_regenerates_internal_ids_and_preserves_external_refs(self):
        from crm.core.forms import regenerate_form_clone_ids
        out = regenerate_form_clone_ids(_SOURCE_FORMXML)
        present = _all_guids(out)
        # Every form-internal registration GUID is gone (replaced).
        assert _REGEN_SRC_GUIDS.isdisjoint(present), (
            f"internal ids not regenerated: {_REGEN_SRC_GUIDS & present}")
        # Every external-reference GUID survives byte-identical.
        assert _KEEP_SRC_GUIDS <= present, (
            f"external refs lost: {_KEEP_SRC_GUIDS - present}")
        # classid / Role Id / ViewId / QuickFormId remain exactly as written.
        assert f'classid="{{{_KEEP_CLASSID}}}"' in out
        assert f'Id="{{{_KEEP_ROLE}}}"' in out
        assert f"<ViewId>{{{_KEEP_VIEW}}}</ViewId>" in out
        assert f"{{{_KEEP_QUICKFORM}}}</QuickFormId>" in out
        # Non-GUID ids are untouched.
        assert 'id="new_code"' in out and 'id="sub1"' in out

    def test_consistent_mapping_same_source_guid_one_new_value(self):
        from crm.core.forms import regenerate_form_clone_ids
        shared = "abababab-abab-abab-abab-abababababab"
        xml = f'<form><tab id="{{{shared}}}" labelid="{{{shared}}}" /></form>'
        out = regenerate_form_clone_ids(xml)
        vals = re.findall(r'(?:id|labelid)="\{(' + _G + r')\}"', out)
        assert len(vals) == 2 and vals[0] == vals[1], (
            f"shared source GUID mapped inconsistently: {vals}")
        assert vals[0].lower() != shared

    def test_regenerated_values_are_canonical_uuids(self):
        from crm.core.forms import regenerate_form_clone_ids
        out = regenerate_form_clone_ids(_SOURCE_FORMXML)
        # The regenerated labelid is a canonical lowercase hyphenated uuid.
        m = re.search(r'labelid="\{(' + _G + r')\}"', out)
        assert m, "no labelid in regenerated formxml"
        new_label = m.group(1)
        assert str(uuid.UUID(new_label)) == new_label, f"not canonical: {new_label!r}"

    def test_noop_on_empty(self):
        from crm.core.forms import regenerate_form_clone_ids
        assert regenerate_form_clone_ids("") == ""


class TestCloneRegeneratesIds:
    def _post_clone_twice(self, backend, forms):
        bodies = []
        with requests_mock.Mocker() as m:
            m.post(backend.url_for("systemforms"), status_code=204, headers={
                "OData-EntityId":
                    backend.url_for("systemforms(99998888-7777-6666-5555-444433332222)"),
            })
            for _ in range(2):
                forms.clone_form_to_entity(backend, _SOURCE_FORM, "cwx_ticketclone")
                bodies.append(m.last_request.json())
        return bodies

    def test_repeat_clones_carry_distinct_ids_neither_source(self, backend):
        from crm.core import forms
        b1, b2 = self._post_clone_twice(backend, forms)
        labels1 = set(re.findall(r'labelid="\{(' + _G + r')\}"', b1["formxml"]))
        labels2 = set(re.findall(r'labelid="\{(' + _G + r')\}"', b2["formxml"]))
        assert labels1 and labels2
        assert labels1.isdisjoint(labels2), "two clones reused labelids"
        src = {_SRC_TAB_LABEL.lower(), _SRC_CELL_LABEL.lower()}
        assert {x.lower() for x in labels1}.isdisjoint(src)
        assert {x.lower() for x in labels2}.isdisjoint(src)

    def test_no_top_level_formid_sent(self, backend):
        from crm.core import forms
        b1, _ = self._post_clone_twice(backend, forms)
        assert "formid" not in b1, f"top-level formid must not be sent: {b1}"
