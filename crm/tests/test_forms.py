"""Unit tests for crm.core.forms."""
# pyright: basic
from __future__ import annotations

import re
import uuid

import pytest
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

    def test_explicit_form_types_widen_the_filter(self, backend):
        from crm.core import forms
        with requests_mock.Mocker() as m:
            m.get(_forms_url(backend), json={"value": []})
            forms.read_entity_forms(backend, "new_project", form_types=(7,))
        url = m.last_request.url
        assert "type+eq+7" in url or "type%20eq%207" in url

    def test_none_form_types_omits_the_type_filter(self, backend):
        """``form_types=None`` lists every form type — only the entity is filtered."""
        from crm.core import forms
        with requests_mock.Mocker() as m:
            m.get(_forms_url(backend), json={"value": []})
            forms.read_entity_forms(backend, "new_project", form_types=None)
        url = m.last_request.url
        assert "objecttypecode" in url
        assert "type+eq" not in url and "type%20eq" not in url

    def test_empty_form_types_is_rejected(self, backend):
        from crm.core import forms
        from crm.utils.d365_backend import D365Error
        with requests_mock.Mocker() as m:
            m.get(_forms_url(backend), json={"value": []})
            with pytest.raises(D365Error):
                forms.read_entity_forms(backend, "new_project", form_types=())


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


class TestClassidForAttributeType:
    def test_maps_common_types(self):
        from crm.core import forms
        assert forms.classid_for_attribute_type("String") == \
            "{4273EDBD-AC1D-40D3-9FB2-095C621B552D}"
        assert forms.classid_for_attribute_type("Lookup") == \
            "{270BD3DB-D9AF-4782-9025-509E298DEC0A}"
        # Customer/Owner share the lookup control
        assert forms.classid_for_attribute_type("Owner") == \
            forms.classid_for_attribute_type("Lookup")

    def test_unmapped_type_raises_clear_error(self):
        from crm.core import forms
        from crm.utils.d365_backend import D365Error
        with pytest.raises(D365Error) as exc:
            forms.classid_for_attribute_type("MultiSelectPicklist")
        assert "MultiSelectPicklist" in str(exc.value)


# A realistic single-line main-form FormXml: two tabs, each with one section
# carrying one bound field. Includes an external `classid` (the existing control)
# and a <Role Id> security-role ref to assert the add/remove/move transforms
# never disturb external GUIDs.
_MAIN_FORMXML = (
    '<form>'
    '<tabs>'
    '<tab name="general" id="{aaaaaaaa-0000-0000-0000-000000000001}">'
    '<labels><label description="General" languagecode="1033" /></labels>'
    '<columns><column width="100%"><sections>'
    '<section name="summary" id="{bbbbbbbb-0000-0000-0000-000000000002}" showlabel="true">'
    '<labels><label description="Summary" languagecode="1033" /></labels>'
    '<rows><row><cell id="{cccccccc-0000-0000-0000-000000000003}">'
    '<labels><label description="Name" languagecode="1033" /></labels>'
    '<control id="new_name" classid="{4273EDBD-AC1D-40D3-9FB2-095C621B552D}" '
    'datafieldname="new_name" /></cell></row></rows>'
    '</section></sections></column></columns>'
    '</tab>'
    '<tab name="details" id="{dddddddd-0000-0000-0000-000000000004}">'
    '<labels><label description="Details" languagecode="1033" /></labels>'
    '<columns><column width="100%"><sections>'
    '<section name="extra" id="{eeeeeeee-0000-0000-0000-000000000005}" showlabel="true">'
    '<labels><label description="Extra" languagecode="1033" /></labels>'
    '<rows></rows>'
    '</section></sections></column></columns>'
    '</tab>'
    '</tabs>'
    '<roles><role><Role Id="{ffffffff-0000-0000-0000-000000000006}" /></role></roles>'
    '</form>'
)

_LOOKUP_CLASSID = "{270BD3DB-D9AF-4782-9025-509E298DEC0A}"


def _controls(formxml):
    """Parse out (datafieldname -> classid) for every bound control."""
    out = {}
    for m in re.finditer(r"<control\b[^>]*>", formxml):
        tag = m.group(0)
        df = re.search(r'datafieldname="([^"]+)"', tag)
        cid = re.search(r'classid="([^"]+)"', tag)
        if df:
            out[df.group(1)] = cid.group(1) if cid else None
    return out


class TestAddFieldToFormxml:
    def test_adds_control_with_classid_and_datafieldname(self):
        from crm.core import forms
        out = forms.add_field_to_formxml(
            _MAIN_FORMXML, datafieldname="new_owner",
            classid=_LOOKUP_CLASSID, label="Owner")
        ctrls = _controls(out)
        assert ctrls["new_owner"] == _LOOKUP_CLASSID
        # existing field untouched
        assert ctrls["new_name"] == "{4273EDBD-AC1D-40D3-9FB2-095C621B552D}"

    def test_preserves_external_guids(self):
        from crm.core import forms
        out = forms.add_field_to_formxml(
            _MAIN_FORMXML, datafieldname="new_owner",
            classid=_LOOKUP_CLASSID, label="Owner")
        # the security-role ref and the existing control's classid survive
        assert "{ffffffff-0000-0000-0000-000000000006}" in out
        assert "{4273EDBD-AC1D-40D3-9FB2-095C621B552D}" in out

    def test_fresh_cell_id_is_unique(self):
        from crm.core import forms
        out = forms.add_field_to_formxml(
            _MAIN_FORMXML, datafieldname="new_owner",
            classid=_LOOKUP_CLASSID, label="Owner")
        cell_ids = re.findall(r'<cell id="(\{[^"]+\})"', out)
        assert len(cell_ids) == len(set(cell_ids))  # no duplicate cell ids
        # the new cell's id is not the existing one
        assert "{cccccccc-0000-0000-0000-000000000003}" in cell_ids
        assert len(cell_ids) == 2

    def test_duplicate_field_raises(self):
        from crm.core import forms
        from crm.utils.d365_backend import D365Error
        with pytest.raises(D365Error):
            forms.add_field_to_formxml(
                _MAIN_FORMXML, datafieldname="new_name",
                classid=_LOOKUP_CLASSID, label="Name")

    def test_default_target_is_first_section(self):
        from crm.core import forms
        out = forms.add_field_to_formxml(
            _MAIN_FORMXML, datafieldname="new_owner",
            classid=_LOOKUP_CLASSID, label="Owner")
        # new control lands in the "summary" section (first), before "details" tab
        assert out.index("new_owner") < out.index('name="details"')

    def test_target_section_by_name(self):
        from crm.core import forms
        out = forms.add_field_to_formxml(
            _MAIN_FORMXML, datafieldname="new_owner",
            classid=_LOOKUP_CLASSID, label="Owner",
            tab="details", section="extra")
        # control lands after the details tab opening
        assert out.index('name="details"') < out.index("new_owner")

    def test_unknown_tab_raises(self):
        from crm.core import forms
        from crm.utils.d365_backend import D365Error
        with pytest.raises(D365Error):
            forms.add_field_to_formxml(
                _MAIN_FORMXML, datafieldname="new_owner",
                classid=_LOOKUP_CLASSID, label="Owner", tab="nope")


class TestRemoveFieldFromFormxml:
    def test_removes_targeted_field_only(self):
        from crm.core import forms
        added = forms.add_field_to_formxml(
            _MAIN_FORMXML, datafieldname="new_owner",
            classid=_LOOKUP_CLASSID, label="Owner")
        out = forms.remove_field_from_formxml(added, datafieldname="new_owner")
        ctrls = _controls(out)
        assert "new_owner" not in ctrls
        assert "new_name" in ctrls  # the other field survives

    def test_tidies_emptied_row(self):
        from crm.core import forms
        # new_name is the only cell in its row; removing it should drop the row
        out = forms.remove_field_from_formxml(_MAIN_FORMXML, datafieldname="new_name")
        assert "<row>" not in out or out.count("<cell") == 0
        assert "new_name" not in _controls(out)

    def test_preserves_external_guids(self):
        from crm.core import forms
        out = forms.remove_field_from_formxml(_MAIN_FORMXML, datafieldname="new_name")
        assert "{ffffffff-0000-0000-0000-000000000006}" in out  # role ref

    def test_absent_field_raises(self):
        from crm.core import forms
        from crm.utils.d365_backend import D365Error
        with pytest.raises(D365Error):
            forms.remove_field_from_formxml(_MAIN_FORMXML, datafieldname="nope")


class TestMoveFieldInFormxml:
    def test_moves_field_to_target_section(self):
        from crm.core import forms
        out = forms.move_field_in_formxml(
            _MAIN_FORMXML, datafieldname="new_name", tab="details", section="extra")
        # new_name now lands after the details tab opening, and only once
        assert out.index('name="details"') < out.index("new_name")
        assert list(_controls(out)).count("new_name") == 1

    def test_preserves_cell_id_and_classid(self):
        from crm.core import forms
        out = forms.move_field_in_formxml(
            _MAIN_FORMXML, datafieldname="new_name", tab="details", section="extra")
        assert "{cccccccc-0000-0000-0000-000000000003}" in out  # original cell id
        assert _controls(out)["new_name"] == "{4273EDBD-AC1D-40D3-9FB2-095C621B552D}"

    def test_absent_field_raises(self):
        from crm.core import forms
        from crm.utils.d365_backend import D365Error
        with pytest.raises(D365Error):
            forms.move_field_in_formxml(
                _MAIN_FORMXML, datafieldname="nope", tab="details")


class TestSelectForm:
    _A = {"formid": "11111111-1111-1111-1111-111111111111", "name": "Main",
          "type": 2, "formxml": "<form/>", "isdefault": False}
    _B = {"formid": "22222222-2222-2222-2222-222222222222", "name": "Default",
          "type": 2, "formxml": "<form/>", "isdefault": True}

    def test_sole_form_used_without_flag(self):
        from crm.core import forms
        assert forms._select_form([self._A], None)["formid"] == self._A["formid"]

    def test_prefers_sole_default_among_many(self):
        from crm.core import forms
        # multiple main forms but exactly one isdefault -> primary is unambiguous
        assert forms._select_form([self._A, self._B], None)["formid"] == self._B["formid"]

    def test_ambiguous_without_default_requires_flag(self):
        from crm.core import forms
        from crm.utils.d365_backend import D365Error
        a2 = dict(self._A, formid="33333333-3333-3333-3333-333333333333")
        with pytest.raises(D365Error) as exc:
            forms._select_form([self._A, a2], None)
        assert "--form" in str(exc.value)

    def test_form_flag_matches_by_name_or_id(self):
        from crm.core import forms
        assert forms._select_form([self._A, self._B], "Main")["formid"] == self._A["formid"]
        assert forms._select_form(
            [self._A, self._B], self._B["formid"])["formid"] == self._B["formid"]


class TestMalformedFormxml:
    def test_add_raises_d365error_on_unparseable_xml(self):
        from crm.core import forms
        from crm.utils.d365_backend import D365Error
        with pytest.raises(D365Error):
            forms.add_field_to_formxml(
                "<form><tabs><tab", datafieldname="new_owner",
                classid=_LOOKUP_CLASSID, label="Owner")

    def test_remove_raises_d365error_on_unparseable_xml(self):
        from crm.core import forms
        from crm.utils.d365_backend import D365Error
        with pytest.raises(D365Error):
            forms.remove_field_from_formxml("<form><<>", datafieldname="x")


# --- event-handler & library wiring (issue #459) --------------------------------

import xml.etree.ElementTree as _ET  # noqa: E402


def _events(formxml):
    """Parse the form and return its <events> element (or None)."""
    return _ET.fromstring(formxml).find("events")


def _node(formxml, path):
    """The etree node at ``path``, asserting it exists (narrows away Optional)."""
    node = _ET.fromstring(formxml).find(path)
    assert node is not None, f"node {path!r} not found in form"
    return node


def _event_nodes(formxml):
    """All <event> elements under <events> (empty list when there are none)."""
    return _ET.fromstring(formxml).findall("events/event")


def _handlers(formxml, event, *, field=None):
    """Return the <Handler> dicts under the named event's <Handlers>."""
    from crm.core import forms
    return [h for h in forms.list_handlers_in_formxml(formxml)
            if h["event"] == event and (field is None or h["field"] == field)]


class TestAddLibraryToFormxml:
    def test_registers_library_with_fresh_unique_id(self):
        from crm.core import forms
        out = forms.add_library_to_formxml(_MAIN_FORMXML, library_name="new_lib.js")
        libs = _ET.fromstring(out).findall("formLibraries/Library")
        assert [l.get("name") for l in libs] == ["new_lib.js"]
        assert libs[0].get("libraryUniqueId"), "library got no unique id"

    def test_is_idempotent_no_duplicate(self):
        from crm.core import forms
        once = forms.add_library_to_formxml(_MAIN_FORMXML, library_name="new_lib.js")
        twice = forms.add_library_to_formxml(once, library_name="new_lib.js")
        libs = _ET.fromstring(twice).findall("formLibraries/Library")
        assert len(libs) == 1

    def test_preserves_existing_classids(self):
        from crm.core import forms
        out = forms.add_library_to_formxml(_MAIN_FORMXML, library_name="new_lib.js")
        assert "{4273EDBD-AC1D-40D3-9FB2-095C621B552D}" in out


class TestAddHandlerToFormxml:
    def test_wires_handler_under_handlers_not_internal(self):
        from crm.core import forms
        out = forms.add_handler_to_formxml(
            _MAIN_FORMXML, event="onload", function="App.onLoad",
            library_name="new_lib.js")
        ev = _node(out, "events/event")
        assert ev.get("name") == "onload"
        assert ev.find("Handlers") is not None
        assert ev.find("InternalHandlers") is None
        h = _node(out, "events/event/Handlers/Handler")
        assert h.get("functionName") == "App.onLoad"
        assert h.get("libraryName") == "new_lib.js"
        assert h.get("handlerUniqueId")
        assert h.get("enabled") == "true"
        assert h.get("passExecutionContext") == "true"

    def test_also_registers_the_library(self):
        from crm.core import forms
        out = forms.add_handler_to_formxml(
            _MAIN_FORMXML, event="onload", function="App.onLoad",
            library_name="new_lib.js")
        names = [l.get("name") for l in _ET.fromstring(out).findall(
            "formLibraries/Library")]
        assert names == ["new_lib.js"]

    def test_merges_into_existing_event_preserving_order(self):
        from crm.core import forms
        first = forms.add_handler_to_formxml(
            _MAIN_FORMXML, event="onload", function="App.first",
            library_name="new_lib.js")
        second = forms.add_handler_to_formxml(
            first, event="onload", function="App.second", library_name="new_lib.js")
        events = _event_nodes(second)
        assert len(events) == 1, "merged into one <event>, not a duplicate"
        fns = [h.get("functionName")
               for h in events[0].findall("Handlers/Handler")]
        assert fns == ["App.first", "App.second"], "existing order not preserved"

    def test_no_pass_context_and_disabled_flags(self):
        from crm.core import forms
        out = forms.add_handler_to_formxml(
            _MAIN_FORMXML, event="onsave", function="App.onSave",
            library_name="new_lib.js", pass_context=False, enabled=False)
        h = _node(out, "events/event/Handlers/Handler")
        assert h.get("enabled") == "false"
        assert h.get("passExecutionContext") == "false"

    def test_params_joined_comma_separated(self):
        from crm.core import forms
        out = forms.add_handler_to_formxml(
            _MAIN_FORMXML, event="onload", function="App.onLoad",
            library_name="new_lib.js", params=("a", "b", "c"))
        h = _node(out, "events/event/Handlers/Handler")
        assert h.get("parameters") == "a,b,c"

    def test_onchange_targets_field_attribute(self):
        from crm.core import forms
        out = forms.add_handler_to_formxml(
            _MAIN_FORMXML, event="onchange", function="App.onChange",
            library_name="new_lib.js", field="new_name")
        ev = _node(out, "events/event")
        assert ev.get("name") == "onchange"
        assert ev.get("attribute") == "new_name"

    def test_onchange_requires_field(self):
        from crm.core import forms
        from crm.utils.d365_backend import D365Error
        with pytest.raises(D365Error, match="onchange"):
            forms.add_handler_to_formxml(
                _MAIN_FORMXML, event="onchange", function="App.onChange",
                library_name="new_lib.js")

    def test_onchange_field_must_be_on_form(self):
        from crm.core import forms
        from crm.utils.d365_backend import D365Error
        with pytest.raises(D365Error, match="not on the form"):
            forms.add_handler_to_formxml(
                _MAIN_FORMXML, event="onchange", function="App.onChange",
                library_name="new_lib.js", field="not_a_field")

    def test_field_rejected_for_non_onchange(self):
        from crm.core import forms
        from crm.utils.d365_backend import D365Error
        with pytest.raises(D365Error, match="onchange"):
            forms.add_handler_to_formxml(
                _MAIN_FORMXML, event="onload", function="App.onLoad",
                library_name="new_lib.js", field="new_name")

    def test_unsupported_event_rejected(self):
        from crm.core import forms
        from crm.utils.d365_backend import D365Error
        with pytest.raises(D365Error, match="Unsupported event"):
            forms.add_handler_to_formxml(
                _MAIN_FORMXML, event="onbogus", function="App.x",
                library_name="new_lib.js")

    def test_duplicate_handler_refused(self):
        from crm.core import forms
        from crm.utils.d365_backend import D365Error
        once = forms.add_handler_to_formxml(
            _MAIN_FORMXML, event="onload", function="App.onLoad",
            library_name="new_lib.js")
        with pytest.raises(D365Error, match="already wired"):
            forms.add_handler_to_formxml(
                once, event="onload", function="App.onLoad",
                library_name="new_lib.js")

    def test_separate_onchange_events_per_field(self):
        from crm.core import forms
        out = forms.add_handler_to_formxml(
            _MAIN_FORMXML, event="onchange", function="App.a",
            library_name="new_lib.js", field="new_name")
        out = forms.add_handler_to_formxml(
            out, event="onchange", function="App.b",
            library_name="new_lib.js", field="new_name")
        # same field → merged into one event
        evs = [e for e in _event_nodes(out) if e.get("name") == "onchange"]
        assert len(evs) == 1
        assert len(evs[0].findall("Handlers/Handler")) == 2

    def test_preserves_classids(self):
        from crm.core import forms
        out = forms.add_handler_to_formxml(
            _MAIN_FORMXML, event="onload", function="App.onLoad",
            library_name="new_lib.js")
        assert "{4273EDBD-AC1D-40D3-9FB2-095C621B552D}" in out


class TestRemoveHandlerFromFormxml:
    def _wired(self):
        from crm.core import forms
        return forms.add_handler_to_formxml(
            _MAIN_FORMXML, event="onload", function="App.onLoad",
            library_name="new_lib.js")

    def test_removes_the_handler_and_tidies_empty_containers(self):
        from crm.core import forms
        out = forms.remove_handler_from_formxml(
            self._wired(), event="onload", function="App.onLoad")
        # the only handler is gone → no leftover empty <events>
        assert _events(out) is None

    def test_keeps_sibling_handler(self):
        from crm.core import forms
        two = forms.add_handler_to_formxml(
            self._wired(), event="onload", function="App.other",
            library_name="new_lib.js")
        out = forms.remove_handler_from_formxml(
            two, event="onload", function="App.onLoad")
        fns = [h["function"] for h in _handlers(out, "onload")]
        assert fns == ["App.other"]

    def test_absent_handler_errors(self):
        from crm.core import forms
        from crm.utils.d365_backend import D365Error
        with pytest.raises(D365Error, match="No handler"):
            forms.remove_handler_from_formxml(
                _MAIN_FORMXML, event="onload", function="App.nope")

    def test_onchange_remove_requires_field(self):
        from crm.core import forms
        from crm.utils.d365_backend import D365Error
        with pytest.raises(D365Error, match="requires --field"):
            forms.remove_handler_from_formxml(
                _MAIN_FORMXML, event="onchange", function="App.c")

    def test_onchange_removed_by_field(self):
        from crm.core import forms
        wired = forms.add_handler_to_formxml(
            _MAIN_FORMXML, event="onchange", function="App.c",
            library_name="new_lib.js", field="new_name")
        out = forms.remove_handler_from_formxml(
            wired, event="onchange", function="App.c", field="new_name")
        assert _handlers(out, "onchange", field="new_name") == []


class TestListHandlersInFormxml:
    def test_empty_when_no_events(self):
        from crm.core import forms
        assert forms.list_handlers_in_formxml(_MAIN_FORMXML) == []

    def test_reports_wired_handlers(self):
        from crm.core import forms
        wired = forms.add_handler_to_formxml(
            _MAIN_FORMXML, event="onload", function="App.onLoad",
            library_name="new_lib.js")
        rows = forms.list_handlers_in_formxml(wired)
        assert len(rows) == 1
        r = rows[0]
        assert r["event"] == "onload"
        assert r["function"] == "App.onLoad"
        assert r["library"] == "new_lib.js"
        assert r["enabled"] is True
        assert r["pass_context"] is True
        assert r["field"] is None


def _tabs(formxml):
    """Tab logical names in document order."""
    import xml.etree.ElementTree as ET
    return [t.get("name") for t in ET.fromstring(formxml).findall("./tabs/tab")]


def _sections(formxml, tab_name):
    """Section names in document order within the named tab."""
    import xml.etree.ElementTree as ET
    root = ET.fromstring(formxml)
    tab = next(t for t in root.findall("./tabs/tab") if t.get("name") == tab_name)
    return [s.get("name")
            for s in tab.findall("./columns/column/sections/section")]


# The external GUIDs that must survive every tab/section transform untouched.
_EXTERNAL_GUIDS = (
    "{4273edbd-ac1d-40d3-9fb2-095c621b552d}",  # existing control classid
    "{ffffffff-0000-0000-0000-000000000006}",  # <Role Id> security-role ref
)


def _assert_external_guids_survive(out):
    low = out.lower()
    for g in _EXTERNAL_GUIDS:
        assert g in low, f"external GUID {g} not preserved: {out}"


class TestAddTabToFormxml:
    def test_appends_tab_with_label_and_userdefined(self):
        from crm.core import forms
        out = forms.add_tab_to_formxml(_MAIN_FORMXML, name="new_tab", label="New Tab")
        assert _tabs(out) == ["general", "details", "new_tab"]
        assert 'description="New Tab"' in out
        assert 'IsUserDefined="1"' in out

    def test_new_tab_carries_nonempty_section_skeleton(self):
        from crm.core import forms
        out = forms.add_tab_to_formxml(_MAIN_FORMXML, name="new_tab", label="New Tab")
        # the tab is non-empty: it has a starter section (an empty tab renders broken)
        assert _sections(out, "new_tab"), "new tab has no section skeleton"

    def test_new_tab_id_is_fresh_and_braced(self):
        from crm.core import forms
        out = forms.add_tab_to_formxml(_MAIN_FORMXML, name="new_tab", label="New Tab")
        tab_ids = re.findall(r'<tab\b[^>]*\bid="(\{[^"]+\})"', out)
        assert len(tab_ids) == len(set(tab_ids))  # all braced, all unique
        # the new tab's id is not any sibling's id
        assert "{aaaaaaaa-0000-0000-0000-000000000001}" in tab_ids

    def test_preserves_external_guids(self):
        from crm.core import forms
        _assert_external_guids_survive(
            forms.add_tab_to_formxml(_MAIN_FORMXML, name="new_tab", label="x"))

    def test_after_inserts_following_named_tab(self):
        from crm.core import forms
        out = forms.add_tab_to_formxml(
            _MAIN_FORMXML, name="new_tab", label="x", after="general")
        assert _tabs(out) == ["general", "new_tab", "details"]

    def test_duplicate_name_raises(self):
        from crm.core import forms
        from crm.utils.d365_backend import D365Error
        with pytest.raises(D365Error):
            forms.add_tab_to_formxml(_MAIN_FORMXML, name="general", label="x")

    def test_columns_out_of_range_raises(self):
        from crm.core import forms
        from crm.utils.d365_backend import D365Error
        with pytest.raises(D365Error):
            forms.add_tab_to_formxml(_MAIN_FORMXML, name="t", label="x", columns=5)
        with pytest.raises(D365Error):
            forms.add_tab_to_formxml(_MAIN_FORMXML, name="t", label="x", columns=0)

    def test_columns_emit_layout_columns(self):
        from crm.core import forms
        import xml.etree.ElementTree as ET
        out = forms.add_tab_to_formxml(
            _MAIN_FORMXML, name="new_tab", label="x", columns=3)
        root = ET.fromstring(out)
        tab = next(t for t in root.findall("./tabs/tab") if t.get("name") == "new_tab")
        assert len(tab.findall("./columns/column")) == 3


class TestRemoveTabFromFormxml:
    def test_removes_named_tab_only(self):
        from crm.core import forms
        out = forms.remove_tab_from_formxml(_MAIN_FORMXML, tab="details")
        assert _tabs(out) == ["general"]

    def test_refuses_removing_the_only_tab(self):
        from crm.core import forms
        from crm.utils.d365_backend import D365Error
        one_tab = forms.remove_tab_from_formxml(_MAIN_FORMXML, tab="details")
        with pytest.raises(D365Error, match="only tab"):
            forms.remove_tab_from_formxml(one_tab, tab="general")

    def test_refuses_orphaning_remove_without_force(self):
        from crm.core import forms
        from crm.utils.d365_backend import D365Error
        # the "general" tab holds the bound new_name control
        with pytest.raises(D365Error, match="new_name"):
            forms.remove_tab_from_formxml(_MAIN_FORMXML, tab="general")

    def test_force_removes_tab_with_bound_fields(self):
        from crm.core import forms
        out = forms.remove_tab_from_formxml(_MAIN_FORMXML, tab="general", force=True)
        assert _tabs(out) == ["details"]
        assert "new_name" not in out

    def test_preserves_external_guids(self):
        from crm.core import forms
        # removing the empty details tab keeps the role ref + control classid
        _assert_external_guids_survive(
            forms.remove_tab_from_formxml(_MAIN_FORMXML, tab="details"))


class TestRenameTabInFormxml:
    def test_sets_label_keeps_name(self):
        from crm.core import forms
        out = forms.rename_tab_in_formxml(
            _MAIN_FORMXML, tab="general", label="Overview")
        assert _tabs(out) == ["general", "details"]  # logical name unchanged
        assert 'description="Overview"' in out

    def test_preserves_all_guids(self):
        from crm.core import forms
        import xml.etree.ElementTree as ET
        before = sorted(re.findall(r"\{[^}]+\}", _MAIN_FORMXML))
        out = forms.rename_tab_in_formxml(_MAIN_FORMXML, tab="general", label="X")
        assert sorted(re.findall(r"\{[^}]+\}", out)) == before
        ET.fromstring(out)  # still well-formed


class TestMoveTabInFormxml:
    def test_moves_to_front_by_default(self):
        from crm.core import forms
        out = forms.move_tab_in_formxml(_MAIN_FORMXML, tab="details")
        assert _tabs(out) == ["details", "general"]

    def test_after_reorders_following_named_tab(self):
        from crm.core import forms
        out = forms.move_tab_in_formxml(_MAIN_FORMXML, tab="general", after="details")
        assert _tabs(out) == ["details", "general"]

    def test_preserves_all_guids(self):
        from crm.core import forms
        before = sorted(re.findall(r"\{[^}]+\}", _MAIN_FORMXML))
        out = forms.move_tab_in_formxml(_MAIN_FORMXML, tab="details")
        assert sorted(re.findall(r"\{[^}]+\}", out)) == before


class TestAddSectionToFormxml:
    def test_appends_section_to_target_tab(self):
        from crm.core import forms
        out = forms.add_section_to_formxml(
            _MAIN_FORMXML, name="new_sec", label="New", tab="details")
        assert _sections(out, "details") == ["extra", "new_sec"]
        assert 'IsUserDefined="1"' in out

    def test_defaults_to_first_tab(self):
        from crm.core import forms
        out = forms.add_section_to_formxml(_MAIN_FORMXML, name="new_sec", label="N")
        assert "new_sec" in _sections(out, "general")

    def test_after_inserts_following_named_section(self):
        from crm.core import forms
        out = forms.add_section_to_formxml(
            _MAIN_FORMXML, name="new_sec", label="N", tab="general", after="summary")
        assert _sections(out, "general") == ["summary", "new_sec"]

    def test_duplicate_name_in_tab_raises(self):
        from crm.core import forms
        from crm.utils.d365_backend import D365Error
        with pytest.raises(D365Error):
            forms.add_section_to_formxml(
                _MAIN_FORMXML, name="summary", label="x", tab="general")

    def test_columns_out_of_range_raises(self):
        from crm.core import forms
        from crm.utils.d365_backend import D365Error
        with pytest.raises(D365Error):
            forms.add_section_to_formxml(
                _MAIN_FORMXML, name="s", label="x", columns=9)

    def test_preserves_external_guids(self):
        from crm.core import forms
        _assert_external_guids_survive(
            forms.add_section_to_formxml(_MAIN_FORMXML, name="s", label="x"))


class TestRemoveSectionFromFormxml:
    def test_removes_named_section(self):
        from crm.core import forms
        out = forms.remove_section_from_formxml(
            _MAIN_FORMXML, section="extra", tab="details")
        assert _sections(out, "details") == []

    def test_refuses_orphaning_remove_without_force(self):
        from crm.core import forms
        from crm.utils.d365_backend import D365Error
        with pytest.raises(D365Error, match="new_name"):
            forms.remove_section_from_formxml(
                _MAIN_FORMXML, section="summary", tab="general")

    def test_force_removes_section_with_bound_fields(self):
        from crm.core import forms
        out = forms.remove_section_from_formxml(
            _MAIN_FORMXML, section="summary", tab="general", force=True)
        assert "new_name" not in out
        assert _sections(out, "general") == []

    def test_preserves_external_guids(self):
        from crm.core import forms
        # removing the empty "extra" section keeps the role ref + control classid
        _assert_external_guids_survive(
            forms.remove_section_from_formxml(
                _MAIN_FORMXML, section="extra", tab="details"))


class TestRenameSectionInFormxml:
    def test_sets_label_keeps_name(self):
        from crm.core import forms
        out = forms.rename_section_in_formxml(
            _MAIN_FORMXML, section="summary", label="Highlights", tab="general")
        assert _sections(out, "general") == ["summary"]
        assert 'description="Highlights"' in out


class TestMoveSectionInFormxml:
    def test_reorders_section_after_sibling(self):
        from crm.core import forms
        # add a second section to "general", then move it ahead of "summary"
        two = forms.add_section_to_formxml(
            _MAIN_FORMXML, name="new_sec", label="N", tab="general")
        assert _sections(two, "general") == ["summary", "new_sec"]
        out = forms.move_section_in_formxml(two, section="new_sec", tab="general")
        assert _sections(out, "general") == ["new_sec", "summary"]

    def test_preserves_all_guids(self):
        from crm.core import forms
        two = forms.add_section_to_formxml(
            _MAIN_FORMXML, name="new_sec", label="N", tab="general")
        before = sorted(re.findall(r"\{[^}]+\}", two))
        out = forms.move_section_in_formxml(two, section="new_sec", tab="general")
        assert sorted(re.findall(r"\{[^}]+\}", out)) == before
