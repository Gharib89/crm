"""Unit tests for crm.core.forms."""

# pyright: basic
from __future__ import annotations

import base64
import re
import uuid

import pytest
import requests_mock

from crm.utils.d365_backend import D365Error

# Default the #940 caller-UI-language lookup to None for this module (see conftest);
# the #940 tests below override it with a concrete language.
pytestmark = pytest.mark.usefixtures("neutralize_caller_language")

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

        xml = '<form><control entityname="new_project" /></form>'
        out = retarget_formxml(xml, src_entity="new_project", dst_entity="cwx_ticketclone")
        assert 'entityname="cwx_ticketclone"' in out

    def test_protects_attribute_datafieldnames(self):
        from crm.core.forms import retarget_formxml

        xml = (
            '<cell><control id="new_projectid" datafieldname="new_projectid" />'
            '<control datafieldname="new_project_code" /></cell>'
        )
        out = retarget_formxml(xml, src_entity="new_project", dst_entity="cwx_ticketclone")
        assert 'datafieldname="new_projectid"' in out
        assert 'datafieldname="new_project_code"' in out
        assert "cwx_ticketclone" not in out

    def test_noop_when_entity_absent(self):
        from crm.core.forms import retarget_formxml

        out = retarget_formxml("<form/>", src_entity="new_project", dst_entity="cwx_ticketclone")
        assert out == "<form/>"

    def test_backslash_in_dst_entity_inserted_literally(self):
        # dst_entity is caller-controlled; a backslash must be inserted verbatim,
        # not interpreted as a regex replacement escape (\g / \1) that would
        # raise or corrupt the output XML.
        from crm.core.forms import retarget_formxml

        xml = '<control entityname="new_project" />'
        out = retarget_formxml(xml, src_entity="new_project", dst_entity=r"cwx_\1clone")
        assert r'entityname="cwx_\1clone"' in out


class TestCloneFormToEntity:
    def test_posts_retargeted_form(self, backend):
        from crm.core import forms

        form = {
            "formid": "old",
            "name": "Information",
            "objecttypecode": "new_project",
            "type": 2,
            "formxml": '<form><control entityname="new_project" /></form>',
            "description": "Main form",
            "isdefault": True,
        }
        with requests_mock.Mocker() as m:
            m.post(
                backend.url_for("systemforms"),
                status_code=204,
                headers={
                    "OData-EntityId": backend.url_for(
                        "systemforms(99998888-7777-6666-5555-444433332222)"
                    ),
                },
            )
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

        form = {
            "formid": "old",
            "name": "F",
            "objecttypecode": "new_project",
            "type": 2,
            "formxml": "<form/>",
            "description": None,
            "isdefault": False,
        }
        with requests_mock.Mocker() as m:
            m.post(
                backend.url_for("systemforms"),
                status_code=204,
                headers={
                    "OData-EntityId": backend.url_for(
                        "systemforms(99998888-7777-6666-5555-444433332222)"
                    ),
                },
            )
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
    _SRC_TAB_ID,
    _SRC_TAB_LABEL,
    _SRC_SEC_ID,
    _SRC_CELL_LABEL,
    _SRC_UNIQUEID,
    _SRC_HANDLER,
    _SRC_LIBRARY,
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
    '<columns><column width="100%"><sections>'
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
    "formid": "old",
    "name": "Information",
    "objecttypecode": "new_project",
    "type": 2,
    "formxml": _SOURCE_FORMXML,
    "description": "Main form",
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
            f"internal ids not regenerated: {_REGEN_SRC_GUIDS & present}"
        )
        # Every external-reference GUID survives byte-identical.
        assert _KEEP_SRC_GUIDS <= present, f"external refs lost: {_KEEP_SRC_GUIDS - present}"
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
            f"shared source GUID mapped inconsistently: {vals}"
        )
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

    def test_forcontrol_ref_regenerated_with_its_uniqueid(self):
        """A ``<controlDescription forControl="{uniqueid}">`` points at an on-form
        control's ``uniqueid`` — an intra-form reference. Regeneration must rewrite
        ``forControl`` in lock-step with the ``uniqueid`` it names, so the link
        survives and the external-reference guard does not trip (issue #785:
        account "Customer profile cases" subgrid + custom-control descriptor).
        """
        from crm.core.forms import regenerate_form_clone_ids

        # Obvious placeholder GUID (public-repo fixture convention) — the transform
        # is value-agnostic, so a real captured id would add no coverage.
        uid = "dddddddd-dddd-dddd-dddd-dddddddddddd"
        xml = (
            "<form><tabs><tab><columns><column><sections><section><rows><row><cell>"
            f'<control id="Recent_Cases" uniqueid="{{{uid}}}"><parameters />'
            "</control></cell></row></rows></section></sections></column></columns>"
            f"</tab></tabs><controlDescriptions><controlDescription "
            f'forControl="{{{uid}}}"><customControl id="cc"><parameters />'
            "</customControl></controlDescription></controlDescriptions></form>"
        )
        # Must not raise the external-reference guard.
        out = regenerate_form_clone_ids(xml)
        # The source uniqueid is gone from both carriers…
        assert uid.lower() not in out.lower(), f"source uniqueid survived: {out}"
        # …and uniqueid + forControl still share one fresh value (link intact).
        new_uid = re.search(r'uniqueid="\{(' + _G + r')\}"', out)
        new_ref = re.search(r'forControl="\{(' + _G + r')\}"', out)
        assert new_uid and new_ref, f"carriers missing after regen: {out}"
        assert new_uid.group(1) == new_ref.group(1), (
            f"forControl decoupled from its uniqueid: {new_uid.group(1)} != {new_ref.group(1)}"
        )

    def test_customcontrol_id_reference_is_preserved(self):
        """``<customControl id="{GUID}">`` references a registered custom control —
        an external object — even though it rides on the bare ``id`` attribute the
        layout ids use. Regenerating it points the clone at a control the org does
        not have, so the server rejects the POST with *"Custom control with Id …
        does not exist"* (#785). It must survive byte-identical, while the layout
        ``id`` on the same form is still regenerated.
        """
        from crm.core.forms import regenerate_form_clone_ids

        # Obvious placeholders (public-repo fixture convention): `cccc…` for the
        # external control reference to preserve, `aaaa…` for the layout id to regen.
        cc = "cccccccc-cccc-cccc-cccc-cccccccccccc"
        cell = "aaaaaaaa-1111-2222-3333-444444444444"
        xml = (
            "<form><tabs><tab><columns><column><sections><section><rows><row>"
            f'<cell id="{{{cell}}}"><control id="Recent_Cases">'
            "<parameters /></control></cell></row></rows></section></sections>"
            "</column></columns></tab></tabs><controlDescriptions>"
            f'<controlDescription><customControl id="{{{cc}}}"><parameters />'
            "</customControl></controlDescription></controlDescriptions></form>"
        )
        out = regenerate_form_clone_ids(xml)
        # The registered-control reference survives exactly.
        assert f'customControl id="{{{cc}}}"' in out, (
            f"customControl id reference was altered: {out}"
        )
        # The layout cell id on the same form is still regenerated.
        assert cell not in out.lower(), f"layout cell id not regenerated: {out}"


class TestCloneRegeneratesIds:
    def _post_clone_twice(self, backend, forms):
        bodies = []
        with requests_mock.Mocker() as m:
            m.post(
                backend.url_for("systemforms"),
                status_code=204,
                headers={
                    "OData-EntityId": backend.url_for(
                        "systemforms(99998888-7777-6666-5555-444433332222)"
                    ),
                },
            )
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

        assert (
            forms.classid_for_attribute_type("String") == "{4273EDBD-AC1D-40D3-9FB2-095C621B552D}"
        )
        assert (
            forms.classid_for_attribute_type("Lookup") == "{270BD3DB-D9AF-4782-9025-509E298DEC0A}"
        )
        # Customer/Owner share the lookup control
        assert forms.classid_for_attribute_type("Owner") == forms.classid_for_attribute_type(
            "Lookup"
        )

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
    "<form>"
    "<tabs>"
    '<tab name="general" id="{aaaaaaaa-0000-0000-0000-000000000001}">'
    '<labels><label description="General" languagecode="1033" /></labels>'
    '<columns><column width="100%"><sections>'
    '<section name="summary" id="{bbbbbbbb-0000-0000-0000-000000000002}" showlabel="true">'
    '<labels><label description="Summary" languagecode="1033" /></labels>'
    '<rows><row><cell id="{cccccccc-0000-0000-0000-000000000003}">'
    '<labels><label description="Name" languagecode="1033" /></labels>'
    '<control id="new_name" classid="{4273EDBD-AC1D-40D3-9FB2-095C621B552D}" '
    'datafieldname="new_name" /></cell></row></rows>'
    "</section></sections></column></columns>"
    "</tab>"
    '<tab name="details" id="{dddddddd-0000-0000-0000-000000000004}">'
    '<labels><label description="Details" languagecode="1033" /></labels>'
    '<columns><column width="100%"><sections>'
    '<section name="extra" id="{eeeeeeee-0000-0000-0000-000000000005}" showlabel="true">'
    '<labels><label description="Extra" languagecode="1033" /></labels>'
    "<rows></rows>"
    "</section></sections></column></columns>"
    "</tab>"
    "</tabs>"
    '<roles><role><Role Id="{ffffffff-0000-0000-0000-000000000006}" /></role></roles>'
    "</form>"
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
            _MAIN_FORMXML, datafieldname="new_owner", classid=_LOOKUP_CLASSID, label="Owner"
        )
        ctrls = _controls(out)
        assert ctrls["new_owner"] == _LOOKUP_CLASSID
        # existing field untouched
        assert ctrls["new_name"] == "{4273EDBD-AC1D-40D3-9FB2-095C621B552D}"

    def test_preserves_external_guids(self):
        from crm.core import forms

        out = forms.add_field_to_formxml(
            _MAIN_FORMXML, datafieldname="new_owner", classid=_LOOKUP_CLASSID, label="Owner"
        )
        # the security-role ref and the existing control's classid survive
        assert "{ffffffff-0000-0000-0000-000000000006}" in out
        assert "{4273EDBD-AC1D-40D3-9FB2-095C621B552D}" in out

    def test_fresh_cell_id_is_unique(self):
        from crm.core import forms

        out = forms.add_field_to_formxml(
            _MAIN_FORMXML, datafieldname="new_owner", classid=_LOOKUP_CLASSID, label="Owner"
        )
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
                _MAIN_FORMXML, datafieldname="new_name", classid=_LOOKUP_CLASSID, label="Name"
            )

    def test_default_target_is_first_section(self):
        from crm.core import forms

        out = forms.add_field_to_formxml(
            _MAIN_FORMXML, datafieldname="new_owner", classid=_LOOKUP_CLASSID, label="Owner"
        )
        # new control lands in the "summary" section (first), before "details" tab
        assert out.index("new_owner") < out.index('name="details"')

    def test_target_section_by_name(self):
        from crm.core import forms

        out = forms.add_field_to_formxml(
            _MAIN_FORMXML,
            datafieldname="new_owner",
            classid=_LOOKUP_CLASSID,
            label="Owner",
            tab="details",
            section="extra",
        )
        # control lands after the details tab opening
        assert out.index('name="details"') < out.index("new_owner")

    def test_unknown_tab_raises(self):
        from crm.core import forms
        from crm.utils.d365_backend import D365Error

        with pytest.raises(D365Error):
            forms.add_field_to_formxml(
                _MAIN_FORMXML,
                datafieldname="new_owner",
                classid=_LOOKUP_CLASSID,
                label="Owner",
                tab="nope",
            )


class TestRemoveFieldFromFormxml:
    def test_removes_targeted_field_only(self):
        from crm.core import forms

        added = forms.add_field_to_formxml(
            _MAIN_FORMXML, datafieldname="new_owner", classid=_LOOKUP_CLASSID, label="Owner"
        )
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
            _MAIN_FORMXML, datafieldname="new_name", tab="details", section="extra"
        )
        # new_name now lands after the details tab opening, and only once
        assert out.index('name="details"') < out.index("new_name")
        assert list(_controls(out)).count("new_name") == 1

    def test_preserves_cell_id_and_classid(self):
        from crm.core import forms

        out = forms.move_field_in_formxml(
            _MAIN_FORMXML, datafieldname="new_name", tab="details", section="extra"
        )
        assert "{cccccccc-0000-0000-0000-000000000003}" in out  # original cell id
        assert _controls(out)["new_name"] == "{4273EDBD-AC1D-40D3-9FB2-095C621B552D}"

    def test_absent_field_raises(self):
        from crm.core import forms
        from crm.utils.d365_backend import D365Error

        with pytest.raises(D365Error):
            forms.move_field_in_formxml(_MAIN_FORMXML, datafieldname="nope", tab="details")


def _control_attr(formxml, datafieldname, attr):
    """The value of ``attr`` on ``datafieldname``'s <control>, or None if unset."""
    for m in re.finditer(r"<control\b[^>]*>", formxml):
        tag = m.group(0)
        if re.search(rf'datafieldname="{re.escape(datafieldname)}"', tag):
            hit = re.search(rf'\b{attr}="([^"]*)"', tag)
            return hit.group(1) if hit else None
    return None


def _cell_attr(formxml, datafieldname, attr):
    """The value of ``attr`` on the <cell> wrapping ``datafieldname``'s control."""
    from crm.core import forms

    root = forms._parse_formxml(formxml)
    control = forms._find_field_control(root, datafieldname)
    assert control is not None, f"{datafieldname!r} control not found"
    cell = forms._parent_map(root).get(control)
    return cell.get(attr) if cell is not None else None


class TestSetFieldPropsInFormxml:
    def test_disabled_sets_control_attribute(self):
        from crm.core import forms

        out = forms.set_field_props_in_formxml(
            _MAIN_FORMXML, datafieldname="new_name", disabled=True
        )
        assert _control_attr(out, "new_name", "disabled") == "true"

    def test_enabled_sets_control_attribute_false(self):
        from crm.core import forms

        out = forms.set_field_props_in_formxml(
            _MAIN_FORMXML, datafieldname="new_name", disabled=False
        )
        assert _control_attr(out, "new_name", "disabled") == "false"

    def test_visible_and_hidden_on_cell(self):
        from crm.core import forms

        # visible is a <cell> attribute — the FormXml schema rejects it on a
        # <control> (verified live against Dataverse).
        hidden = forms.set_field_props_in_formxml(
            _MAIN_FORMXML, datafieldname="new_name", visible=False
        )
        assert _cell_attr(hidden, "new_name", "visible") == "false"
        shown = forms.set_field_props_in_formxml(
            _MAIN_FORMXML, datafieldname="new_name", visible=True
        )
        assert _cell_attr(shown, "new_name", "visible") == "true"

    def test_locked_sets_cell_locklevel_integer(self):
        from crm.core import forms

        locked = forms.set_field_props_in_formxml(
            _MAIN_FORMXML, datafieldname="new_name", locked=True
        )
        # locklevel is an integer flag in FormXml (1 = locked, 0 = unlocked),
        # not a "true"/"false" boolean like the other three.
        assert _cell_attr(locked, "new_name", "locklevel") == "1"
        unlocked = forms.set_field_props_in_formxml(
            _MAIN_FORMXML, datafieldname="new_name", locked=False
        )
        assert _cell_attr(unlocked, "new_name", "locklevel") == "0"

    def test_show_label_toggles_cell_showlabel(self):
        from crm.core import forms

        out = forms.set_field_props_in_formxml(
            _MAIN_FORMXML, datafieldname="new_name", show_label=False
        )
        assert _cell_attr(out, "new_name", "showlabel") == "false"

    def test_untouched_props_left_alone(self):
        from crm.core import forms

        # Only flip `disabled`; the other props the caller did not pass must stay
        # exactly as they were in the source (here: absent on the field's cell/control).
        out = forms.set_field_props_in_formxml(
            _MAIN_FORMXML, datafieldname="new_name", disabled=True
        )
        assert _cell_attr(out, "new_name", "showlabel") is None
        assert _cell_attr(out, "new_name", "visible") is None
        assert _cell_attr(out, "new_name", "locklevel") is None

    def test_multiple_props_in_one_call(self):
        from crm.core import forms

        out = forms.set_field_props_in_formxml(
            _MAIN_FORMXML,
            datafieldname="new_name",
            disabled=True,
            visible=False,
            locked=True,
            show_label=False,
        )
        assert _control_attr(out, "new_name", "disabled") == "true"
        assert _cell_attr(out, "new_name", "visible") == "false"
        assert _cell_attr(out, "new_name", "locklevel") == "1"
        assert _cell_attr(out, "new_name", "showlabel") == "false"

    def test_preserves_external_guids_and_classid(self):
        from crm.core import forms

        out = forms.set_field_props_in_formxml(
            _MAIN_FORMXML, datafieldname="new_name", disabled=True
        )
        assert "{ffffffff-0000-0000-0000-000000000006}" in out  # role ref
        assert _control_attr(out, "new_name", "classid") == "{4273EDBD-AC1D-40D3-9FB2-095C621B552D}"

    def test_absent_field_raises(self):
        from crm.core import forms
        from crm.utils.d365_backend import D365Error

        with pytest.raises(D365Error):
            forms.set_field_props_in_formxml(_MAIN_FORMXML, datafieldname="nope", disabled=True)


class TestSelectForm:
    _A = {
        "formid": "11111111-1111-1111-1111-111111111111",
        "name": "Main",
        "type": 2,
        "formxml": "<form/>",
        "isdefault": False,
    }
    _B = {
        "formid": "22222222-2222-2222-2222-222222222222",
        "name": "Default",
        "type": 2,
        "formxml": "<form/>",
        "isdefault": True,
    }

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
        assert (
            forms._select_form([self._A, self._B], self._B["formid"])["formid"] == self._B["formid"]
        )


class TestMalformedFormxml:
    def test_add_raises_d365error_on_unparseable_xml(self):
        from crm.core import forms
        from crm.utils.d365_backend import D365Error

        with pytest.raises(D365Error):
            forms.add_field_to_formxml(
                "<form><tabs><tab",
                datafieldname="new_owner",
                classid=_LOOKUP_CLASSID,
                label="Owner",
            )

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

    return [
        h
        for h in forms.list_handlers_in_formxml(formxml)
        if h["event"] == event and (field is None or h["field"] == field)
    ]


class TestAddLibraryToFormxml:
    def test_registers_library_with_fresh_unique_id(self):
        from crm.core import forms

        out = forms.add_library_to_formxml(_MAIN_FORMXML, library_name="new_lib.js")
        libs = _ET.fromstring(out).findall("formLibraries/Library")
        assert [lib.get("name") for lib in libs] == ["new_lib.js"]
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
            _MAIN_FORMXML, event="onload", function="App.onLoad", library_name="new_lib.js"
        )
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
            _MAIN_FORMXML, event="onload", function="App.onLoad", library_name="new_lib.js"
        )
        names = [lib.get("name") for lib in _ET.fromstring(out).findall("formLibraries/Library")]
        assert names == ["new_lib.js"]

    def test_merges_into_existing_event_preserving_order(self):
        from crm.core import forms

        first = forms.add_handler_to_formxml(
            _MAIN_FORMXML, event="onload", function="App.first", library_name="new_lib.js"
        )
        second = forms.add_handler_to_formxml(
            first, event="onload", function="App.second", library_name="new_lib.js"
        )
        events = _event_nodes(second)
        assert len(events) == 1, "merged into one <event>, not a duplicate"
        fns = [h.get("functionName") for h in events[0].findall("Handlers/Handler")]
        assert fns == ["App.first", "App.second"], "existing order not preserved"

    def test_no_pass_context_and_disabled_flags(self):
        from crm.core import forms

        out = forms.add_handler_to_formxml(
            _MAIN_FORMXML,
            event="onsave",
            function="App.onSave",
            library_name="new_lib.js",
            pass_context=False,
            enabled=False,
        )
        h = _node(out, "events/event/Handlers/Handler")
        assert h.get("enabled") == "false"
        assert h.get("passExecutionContext") == "false"

    def test_params_joined_comma_separated(self):
        from crm.core import forms

        out = forms.add_handler_to_formxml(
            _MAIN_FORMXML,
            event="onload",
            function="App.onLoad",
            library_name="new_lib.js",
            params=("a", "b", "c"),
        )
        h = _node(out, "events/event/Handlers/Handler")
        assert h.get("parameters") == "a,b,c"

    def test_onchange_targets_field_attribute(self):
        from crm.core import forms

        out = forms.add_handler_to_formxml(
            _MAIN_FORMXML,
            event="onchange",
            function="App.onChange",
            library_name="new_lib.js",
            field="new_name",
        )
        ev = _node(out, "events/event")
        assert ev.get("name") == "onchange"
        assert ev.get("attribute") == "new_name"

    def test_onchange_requires_field(self):
        from crm.core import forms
        from crm.utils.d365_backend import D365Error

        with pytest.raises(D365Error, match="onchange"):
            forms.add_handler_to_formxml(
                _MAIN_FORMXML, event="onchange", function="App.onChange", library_name="new_lib.js"
            )

    def test_onchange_field_must_be_on_form(self):
        from crm.core import forms
        from crm.utils.d365_backend import D365Error

        with pytest.raises(D365Error, match="not on the form"):
            forms.add_handler_to_formxml(
                _MAIN_FORMXML,
                event="onchange",
                function="App.onChange",
                library_name="new_lib.js",
                field="not_a_field",
            )

    def test_field_rejected_for_non_onchange(self):
        from crm.core import forms
        from crm.utils.d365_backend import D365Error

        with pytest.raises(D365Error, match="onchange"):
            forms.add_handler_to_formxml(
                _MAIN_FORMXML,
                event="onload",
                function="App.onLoad",
                library_name="new_lib.js",
                field="new_name",
            )

    def test_unsupported_event_rejected(self):
        from crm.core import forms
        from crm.utils.d365_backend import D365Error

        with pytest.raises(D365Error, match="Unsupported event"):
            forms.add_handler_to_formxml(
                _MAIN_FORMXML, event="onbogus", function="App.x", library_name="new_lib.js"
            )

    def test_duplicate_handler_refused(self):
        from crm.core import forms
        from crm.utils.d365_backend import D365Error

        once = forms.add_handler_to_formxml(
            _MAIN_FORMXML, event="onload", function="App.onLoad", library_name="new_lib.js"
        )
        with pytest.raises(D365Error, match="already wired"):
            forms.add_handler_to_formxml(
                once, event="onload", function="App.onLoad", library_name="new_lib.js"
            )

    def test_separate_onchange_events_per_field(self):
        from crm.core import forms

        out = forms.add_handler_to_formxml(
            _MAIN_FORMXML,
            event="onchange",
            function="App.a",
            library_name="new_lib.js",
            field="new_name",
        )
        out = forms.add_handler_to_formxml(
            out, event="onchange", function="App.b", library_name="new_lib.js", field="new_name"
        )
        # same field → merged into one event
        evs = [e for e in _event_nodes(out) if e.get("name") == "onchange"]
        assert len(evs) == 1
        assert len(evs[0].findall("Handlers/Handler")) == 2

    def test_preserves_classids(self):
        from crm.core import forms

        out = forms.add_handler_to_formxml(
            _MAIN_FORMXML, event="onload", function="App.onLoad", library_name="new_lib.js"
        )
        assert "{4273EDBD-AC1D-40D3-9FB2-095C621B552D}" in out


class TestRemoveHandlerFromFormxml:
    def _wired(self):
        from crm.core import forms

        return forms.add_handler_to_formxml(
            _MAIN_FORMXML, event="onload", function="App.onLoad", library_name="new_lib.js"
        )

    def test_removes_the_handler_and_tidies_empty_containers(self):
        from crm.core import forms

        out = forms.remove_handler_from_formxml(
            self._wired(), event="onload", function="App.onLoad"
        )
        # the only handler is gone → no leftover empty <events>
        assert _events(out) is None

    def test_keeps_sibling_handler(self):
        from crm.core import forms

        two = forms.add_handler_to_formxml(
            self._wired(), event="onload", function="App.other", library_name="new_lib.js"
        )
        out = forms.remove_handler_from_formxml(two, event="onload", function="App.onLoad")
        fns = [h["function"] for h in _handlers(out, "onload")]
        assert fns == ["App.other"]

    def test_absent_handler_errors(self):
        from crm.core import forms
        from crm.utils.d365_backend import D365Error

        with pytest.raises(D365Error, match="No handler"):
            forms.remove_handler_from_formxml(_MAIN_FORMXML, event="onload", function="App.nope")

    def test_onchange_remove_requires_field(self):
        from crm.core import forms
        from crm.utils.d365_backend import D365Error

        with pytest.raises(D365Error, match="requires --field"):
            forms.remove_handler_from_formxml(_MAIN_FORMXML, event="onchange", function="App.c")

    def test_onchange_removed_by_field(self):
        from crm.core import forms

        wired = forms.add_handler_to_formxml(
            _MAIN_FORMXML,
            event="onchange",
            function="App.c",
            library_name="new_lib.js",
            field="new_name",
        )
        out = forms.remove_handler_from_formxml(
            wired, event="onchange", function="App.c", field="new_name"
        )
        assert _handlers(out, "onchange", field="new_name") == []


class TestListHandlersInFormxml:
    def test_empty_when_no_events(self):
        from crm.core import forms

        assert forms.list_handlers_in_formxml(_MAIN_FORMXML) == []

    def test_reports_wired_handlers(self):
        from crm.core import forms

        wired = forms.add_handler_to_formxml(
            _MAIN_FORMXML, event="onload", function="App.onLoad", library_name="new_lib.js"
        )
        rows = forms.list_handlers_in_formxml(wired)
        assert len(rows) == 1
        r = rows[0]
        assert r["event"] == "onload"
        assert r["function"] == "App.onLoad"
        assert r["library"] == "new_lib.js"
        assert r["enabled"] is True
        assert r["pass_context"] is True
        assert r["field"] is None


class TestSetHandlerPropsInFormxml:
    """set_handler_props_in_formxml converges a wired handler's enabled /
    passExecutionContext flags in place (reconcile — ADR 0024, #793).
    """

    def _wired(self, **kw):
        from crm.core import forms

        return forms.add_handler_to_formxml(
            _MAIN_FORMXML, event="onload", function="App.onLoad", library_name="new_lib.js", **kw
        )

    def test_toggles_enabled_leaving_pass_context_untouched(self):
        from crm.core import forms

        out = forms.set_handler_props_in_formxml(
            self._wired(), event="onload", function="App.onLoad", enabled=False
        )
        h = _node(out, "events/event/Handlers/Handler")
        assert h.get("enabled") == "false"
        assert h.get("passExecutionContext") == "true"  # untouched

    def test_toggles_pass_context_only(self):
        from crm.core import forms

        out = forms.set_handler_props_in_formxml(
            self._wired(), event="onload", function="App.onLoad", pass_context=False
        )
        h = _node(out, "events/event/Handlers/Handler")
        assert h.get("passExecutionContext") == "false"
        assert h.get("enabled") == "true"  # untouched

    def test_none_flags_leave_the_handler_byte_identical(self):
        from crm.core import forms

        wired = self._wired()
        out = forms.set_handler_props_in_formxml(wired, event="onload", function="App.onLoad")
        assert out == wired

    def test_matches_onchange_by_field(self):
        from crm.core import forms

        wired = forms.add_handler_to_formxml(
            _MAIN_FORMXML,
            event="onchange",
            function="App.onChange",
            library_name="new_lib.js",
            field="new_name",
        )
        out = forms.set_handler_props_in_formxml(
            wired, event="onchange", function="App.onChange", field="new_name", enabled=False
        )
        h = _node(out, "events/event/Handlers/Handler")
        assert h.get("enabled") == "false"

    def test_absent_handler_raises(self):
        from crm.core import forms
        from crm.utils.d365_backend import D365Error

        with pytest.raises(D365Error, match="No handler"):
            forms.set_handler_props_in_formxml(
                _MAIN_FORMXML, event="onload", function="App.missing", enabled=False
            )

    def test_onchange_without_field_rejected(self):
        from crm.core import forms
        from crm.utils.d365_backend import D365Error

        with pytest.raises(D365Error, match="onchange handler requires a 'field'"):
            forms.set_handler_props_in_formxml(
                _MAIN_FORMXML, event="onchange", function="App.onChange", enabled=False
            )

    def test_field_on_non_onchange_rejected(self):
        from crm.core import forms
        from crm.utils.d365_backend import D365Error

        with pytest.raises(D365Error, match="only to onchange"):
            forms.set_handler_props_in_formxml(
                _MAIN_FORMXML,
                event="onload",
                function="App.onLoad",
                field="new_name",
                enabled=False,
            )

    def test_preserves_classids(self):
        from crm.core import forms

        out = forms.set_handler_props_in_formxml(
            self._wired(), event="onload", function="App.onLoad", enabled=False
        )
        assert "{4273EDBD-AC1D-40D3-9FB2-095C621B552D}" in out


def _tabs(formxml):
    """Tab logical names in document order."""
    import xml.etree.ElementTree as ET

    return [t.get("name") for t in ET.fromstring(formxml).findall("./tabs/tab")]


def _sections(formxml, tab_name):
    """Section names in document order within the named tab."""
    import xml.etree.ElementTree as ET

    root = ET.fromstring(formxml)
    tab = next(t for t in root.findall("./tabs/tab") if t.get("name") == tab_name)
    return [s.get("name") for s in tab.findall("./columns/column/sections/section")]


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
            forms.add_tab_to_formxml(_MAIN_FORMXML, name="new_tab", label="x")
        )

    def test_after_inserts_following_named_tab(self):
        from crm.core import forms

        out = forms.add_tab_to_formxml(_MAIN_FORMXML, name="new_tab", label="x", after="general")
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
        import xml.etree.ElementTree as ET

        from crm.core import forms

        out = forms.add_tab_to_formxml(_MAIN_FORMXML, name="new_tab", label="x", columns=3)
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
        _assert_external_guids_survive(forms.remove_tab_from_formxml(_MAIN_FORMXML, tab="details"))


class TestRenameTabInFormxml:
    def test_sets_label_keeps_name(self):
        from crm.core import forms

        out = forms.rename_tab_in_formxml(_MAIN_FORMXML, tab="general", label="Overview")
        assert _tabs(out) == ["general", "details"]  # logical name unchanged
        assert 'description="Overview"' in out

    def test_preserves_all_guids(self):
        import xml.etree.ElementTree as ET

        from crm.core import forms

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
            _MAIN_FORMXML, name="new_sec", label="New", tab="details"
        )
        assert _sections(out, "details") == ["extra", "new_sec"]
        assert 'IsUserDefined="1"' in out

    def test_defaults_to_first_tab(self):
        from crm.core import forms

        out = forms.add_section_to_formxml(_MAIN_FORMXML, name="new_sec", label="N")
        assert "new_sec" in _sections(out, "general")

    def test_after_inserts_following_named_section(self):
        from crm.core import forms

        out = forms.add_section_to_formxml(
            _MAIN_FORMXML, name="new_sec", label="N", tab="general", after="summary"
        )
        assert _sections(out, "general") == ["summary", "new_sec"]

    def test_duplicate_name_in_tab_raises(self):
        from crm.core import forms
        from crm.utils.d365_backend import D365Error

        with pytest.raises(D365Error):
            forms.add_section_to_formxml(_MAIN_FORMXML, name="summary", label="x", tab="general")

    def test_columns_out_of_range_raises(self):
        from crm.core import forms
        from crm.utils.d365_backend import D365Error

        with pytest.raises(D365Error):
            forms.add_section_to_formxml(_MAIN_FORMXML, name="s", label="x", columns=9)

    def test_preserves_external_guids(self):
        from crm.core import forms

        _assert_external_guids_survive(
            forms.add_section_to_formxml(_MAIN_FORMXML, name="s", label="x")
        )


class TestRemoveSectionFromFormxml:
    def test_removes_named_section(self):
        from crm.core import forms

        out = forms.remove_section_from_formxml(_MAIN_FORMXML, section="extra", tab="details")
        assert _sections(out, "details") == []

    def test_refuses_orphaning_remove_without_force(self):
        from crm.core import forms
        from crm.utils.d365_backend import D365Error

        with pytest.raises(D365Error, match="new_name"):
            forms.remove_section_from_formxml(_MAIN_FORMXML, section="summary", tab="general")

    def test_force_removes_section_with_bound_fields(self):
        from crm.core import forms

        out = forms.remove_section_from_formxml(
            _MAIN_FORMXML, section="summary", tab="general", force=True
        )
        assert "new_name" not in out
        assert _sections(out, "general") == []

    def test_preserves_external_guids(self):
        from crm.core import forms

        # removing the empty "extra" section keeps the role ref + control classid
        _assert_external_guids_survive(
            forms.remove_section_from_formxml(_MAIN_FORMXML, section="extra", tab="details")
        )


class TestRenameSectionInFormxml:
    def test_sets_label_keeps_name(self):
        from crm.core import forms

        out = forms.rename_section_in_formxml(
            _MAIN_FORMXML, section="summary", label="Highlights", tab="general"
        )
        assert _sections(out, "general") == ["summary"]
        assert 'description="Highlights"' in out


class TestMoveSectionInFormxml:
    def test_reorders_section_after_sibling(self):
        from crm.core import forms

        # add a second section to "general", then move it ahead of "summary"
        two = forms.add_section_to_formxml(_MAIN_FORMXML, name="new_sec", label="N", tab="general")
        assert _sections(two, "general") == ["summary", "new_sec"]
        out = forms.move_section_in_formxml(two, section="new_sec", tab="general")
        assert _sections(out, "general") == ["new_sec", "summary"]

    def test_preserves_all_guids(self):
        from crm.core import forms

        two = forms.add_section_to_formxml(_MAIN_FORMXML, name="new_sec", label="N", tab="general")
        before = sorted(re.findall(r"\{[^}]+\}", two))
        out = forms.move_section_in_formxml(two, section="new_sec", tab="general")
        assert sorted(re.findall(r"\{[^}]+\}", out)) == before

    def test_after_places_section_following_anchor(self):
        from crm.core import forms

        # start: [summary, new_sec] — move new_sec to after=summary (no-op order,
        # but the after= branch fires); then verify order is [summary, new_sec]
        two = forms.add_section_to_formxml(_MAIN_FORMXML, name="new_sec", label="N", tab="general")
        three = forms.add_section_to_formxml(two, name="third_sec", label="T", tab="general")
        # start: [summary, new_sec, third_sec]; move third_sec after summary
        out = forms.move_section_in_formxml(
            three, section="third_sec", tab="general", after="summary"
        )
        assert _sections(out, "general") == ["summary", "third_sec", "new_sec"]

    def test_after_anchor_not_found_raises(self):
        from crm.core import forms
        from crm.utils.d365_backend import D365Error

        two = forms.add_section_to_formxml(_MAIN_FORMXML, name="new_sec", label="N", tab="general")
        with pytest.raises(D365Error, match="no_such_sec"):
            forms.move_section_in_formxml(
                two, section="new_sec", tab="general", after="no_such_sec"
            )


def _attr_url(backend, attr):
    return backend.url_for(
        f"EntityDefinitions(LogicalName='new_project')/Attributes(LogicalName='{attr}')"
    )


_WR_LIB = {
    "value": [
        {
            "webresourceid": "99990000-0000-0000-0000-000000000001",
            "name": "new_lib.js",
            "webresourcetype": 3,
        }
    ]
}


class TestConvergeDeclaredForm:
    """converge_declared_form layers a declared forms: block onto a live form
    additively and idempotently (ADR 0024).
    """

    _FORM_ROW = {
        "formid": "aaaaaaaa-0000-0000-0000-000000000001",
        "name": "Information",
        "objecttypecode": "new_project",
        "type": 2,
        "formxml": _MAIN_FORMXML,
        "isdefault": True,
    }

    def _block(self):
        return {
            "tabs": [
                {
                    "name": "custom",
                    "label": "Custom",
                    "columns": 2,
                    "sections": [
                        {
                            "name": "info",
                            "label": "Info",
                            "fields": [{"name": "new_owner", "label": "Owner"}],
                        }
                    ],
                }
            ],
            "libraries": ["new_lib.js"],
            "handlers": [{"event": "onload", "function": "App.onLoad", "library": "new_lib.js"}],
        }

    def test_adds_all_declared_components(self, backend):
        from crm.core import forms

        with requests_mock.Mocker() as m:
            m.get(
                _attr_url(backend, "new_owner"),
                json={"AttributeType": "Lookup", "LogicalName": "new_owner"},
            )
            m.get(backend.url_for("webresourceset"), json=_WR_LIB)
            new_xml, added = forms.converge_declared_form(
                backend, "new_project", dict(self._FORM_ROW), self._block()
            )
        kinds = [(a["kind"], a["name"]) for a in added]
        assert kinds == [
            ("tab", "custom"),
            ("section", "info"),
            ("field", "new_owner"),
            ("library", "new_lib.js"),
            ("handler", "App.onLoad"),
        ]
        assert 'name="custom"' in new_xml
        assert 'name="info"' in new_xml
        assert 'datafieldname="new_owner"' in new_xml
        assert _LOOKUP_CLASSID in new_xml
        assert '<Library name="new_lib.js"' in new_xml
        assert 'functionName="App.onLoad"' in new_xml

    def test_explicit_null_columns_defaults_not_typeerror(self, backend):
        # `columns: null` in the spec passes validation (None is optional), so
        # convergence must treat it as absent → default 1, not `int(None)`.
        from crm.core import forms

        block = {
            "tabs": [
                {"name": "custom", "columns": None, "sections": [{"name": "info", "columns": None}]}
            ]
        }
        with requests_mock.Mocker():
            new_xml, added = forms.converge_declared_form(
                backend, "new_project", dict(self._FORM_ROW), block
            )
        assert [(a["kind"], a["name"]) for a in added] == [("tab", "custom"), ("section", "info")]
        assert 'name="custom"' in new_xml and 'name="info"' in new_xml

    def test_reapply_is_idempotent(self, backend):
        from crm.core import forms

        with requests_mock.Mocker() as m:
            m.get(
                _attr_url(backend, "new_owner"),
                json={"AttributeType": "Lookup", "LogicalName": "new_owner"},
            )
            m.get(backend.url_for("webresourceset"), json=_WR_LIB)
            once, _ = forms.converge_declared_form(
                backend, "new_project", dict(self._FORM_ROW), self._block()
            )
            row2 = {**self._FORM_ROW, "formxml": once}
            twice, added = forms.converge_declared_form(backend, "new_project", row2, self._block())
        assert added == []
        assert twice == once

    def test_existing_tab_gets_new_section_not_duplicate(self, backend):
        from crm.core import forms

        block = {
            "tabs": [
                {"name": "general", "label": "General", "sections": [{"name": "s2", "label": "S2"}]}
            ]
        }
        with requests_mock.Mocker():
            _, added = forms.converge_declared_form(
                backend, "new_project", dict(self._FORM_ROW), block
            )
        assert [(a["kind"], a["name"]) for a in added] == [("section", "s2")]

    def test_apply_form_spec_commits_and_reports(self, backend):
        from crm.core import forms

        with requests_mock.Mocker() as m:
            m.get(_forms_url(backend), json={"value": [self._FORM_ROW]})
            m.get(
                _attr_url(backend, "new_owner"),
                json={"AttributeType": "Lookup", "LogicalName": "new_owner"},
            )
            m.get(backend.url_for("webresourceset"), json=_WR_LIB)
            patched = m.patch(
                backend.url_for(f"systemforms({self._FORM_ROW['formid']})"), status_code=204
            )
            result = forms.apply_form_spec(
                backend,
                "new_project",
                self._block(),
                publish=False,
                solution="TestSol",
                dry_run=False,
            )
        assert result["committed"] is True
        assert len(result["components"]) == 5
        assert patched.called

    def test_apply_form_spec_dry_run_writes_nothing(self, dry_backend):
        from crm.core import forms

        with requests_mock.Mocker() as m:
            m.get(_forms_url(dry_backend), json={"value": [self._FORM_ROW]})
            m.get(
                _attr_url(dry_backend, "new_owner"),
                json={"AttributeType": "Lookup", "LogicalName": "new_owner"},
            )
            m.get(dry_backend.url_for("webresourceset"), json=_WR_LIB)
            patched = m.patch(
                dry_backend.url_for(f"systemforms({self._FORM_ROW['formid']})"), status_code=204
            )
            result = forms.apply_form_spec(
                dry_backend,
                "new_project",
                self._block(),
                publish=False,
                solution="TestSol",
                dry_run=True,
            )
        assert result["committed"] is False
        assert len(result["components"]) == 5
        assert not patched.called

    def test_apply_form_spec_unmaterialized_when_no_main_form(self, backend):
        from crm.core import forms

        with requests_mock.Mocker() as m:
            m.get(_forms_url(backend), json={"value": []})
            result = forms.apply_form_spec(
                backend,
                "new_project",
                self._block(),
                publish=False,
                solution="TestSol",
                dry_run=False,
            )
        assert result["unmaterialized"] is True
        assert result["components"] == []


class TestConvergeDeclaredFormReconcile:
    """converge_declared_form converges drift in components already present
    (reconcile slice — ADR 0024, #793): tab/section label, field re-placement,
    tab order, and handler flags each converge in place and report a diff; an
    unchanged declaration stays a no-op.
    """

    _FORM_ROW = {
        "formid": "aaaaaaaa-0000-0000-0000-000000000001",
        "name": "Information",
        "objecttypecode": "new_project",
        "type": 2,
        "formxml": _MAIN_FORMXML,
        "isdefault": True,
    }

    def _converge(self, backend, block):
        from crm.core import forms

        return forms.converge_declared_form(backend, "new_project", dict(self._FORM_ROW), block)

    def _by(self, changes, kind, name):
        return next(c for c in changes if c["kind"] == kind and c["name"] == name)

    def test_tab_label_renamed_in_place(self, backend):
        import xml.etree.ElementTree as ET

        with requests_mock.Mocker():
            new_xml, changes = self._converge(
                backend, {"tabs": [{"name": "general", "label": "Overview"}]}
            )
        c = self._by(changes, "tab", "general")
        assert c["change"] == "converged"
        assert c["diff"] == {"label": {"old": "General", "new": "Overview"}}
        tab = next(
            t for t in ET.fromstring(new_xml).findall("./tabs/tab") if t.get("name") == "general"
        )
        label_el = tab.find("labels/label")
        assert label_el is not None
        assert label_el.get("description") == "Overview"

    def test_matching_tab_label_is_a_no_op(self, backend):
        with requests_mock.Mocker():
            new_xml, changes = self._converge(
                backend, {"tabs": [{"name": "general", "label": "General"}]}
            )
        assert changes == []
        assert new_xml == _MAIN_FORMXML

    def test_section_label_renamed_in_place(self, backend):
        with requests_mock.Mocker():
            _, changes = self._converge(
                backend,
                {
                    "tabs": [
                        {"name": "general", "sections": [{"name": "summary", "label": "Key facts"}]}
                    ]
                },
            )
        c = self._by(changes, "section", "summary")
        assert c["change"] == "converged"
        assert c["diff"] == {"label": {"old": "Summary", "new": "Key facts"}}

    def test_field_re_placed_to_declared_section(self, backend):
        # new_name lives in general/summary; declaring it under details/extra
        # relocates the existing cell (no add).
        with requests_mock.Mocker():
            new_xml, changes = self._converge(
                backend,
                {
                    "tabs": [
                        {
                            "name": "details",
                            "sections": [{"name": "extra", "fields": [{"name": "new_name"}]}],
                        }
                    ]
                },
            )
        c = self._by(changes, "field", "new_name")
        assert c["change"] == "converged"
        assert c["diff"] == {
            "placement": {
                "old": {"tab": "general", "section": "summary"},
                "new": {"tab": "details", "section": "extra"},
            }
        }
        assert _sections(new_xml, "general") == ["summary"]
        # the field now resolves under details/extra
        assert 'datafieldname="new_name"' in new_xml
        assert _controls(new_xml)["new_name"] == "{4273EDBD-AC1D-40D3-9FB2-095C621B552D}"

    def test_field_already_in_place_is_a_no_op(self, backend):
        with requests_mock.Mocker():
            _, changes = self._converge(
                backend,
                {
                    "tabs": [
                        {
                            "name": "general",
                            "sections": [{"name": "summary", "fields": [{"name": "new_name"}]}],
                        }
                    ]
                },
            )
        assert changes == []

    def test_tab_order_converged_to_declared_sequence(self, backend):
        # live order is [general, details]; declaring [details, general] reorders.
        with requests_mock.Mocker():
            new_xml, changes = self._converge(
                backend, {"tabs": [{"name": "details"}, {"name": "general"}]}
            )
        assert _tabs(new_xml) == ["details", "general"]
        assert any(c["change"] == "converged" and c["kind"] == "tab-order" for c in changes)

    def test_tab_order_already_correct_is_a_no_op(self, backend):
        with requests_mock.Mocker():
            new_xml, changes = self._converge(
                backend, {"tabs": [{"name": "general"}, {"name": "details"}]}
            )
        assert changes == []
        assert new_xml == _MAIN_FORMXML

    _TWO_SECTION_FORM = (
        "<form><tabs>"
        '<tab name="general" id="{aaaaaaaa-0000-0000-0000-000000000001}">'
        '<labels><label description="General" languagecode="1033" /></labels>'
        '<columns><column width="100%"><sections>'
        '<section name="s1" id="{bbbbbbbb-0000-0000-0000-000000000002}">'
        '<labels><label description="S1" languagecode="1033" /></labels>'
        "<rows></rows></section>"
        '<section name="s2" id="{cccccccc-0000-0000-0000-000000000003}">'
        '<labels><label description="S2" languagecode="1033" /></labels>'
        "<rows></rows></section>"
        "</sections></column></columns></tab></tabs></form>"
    )

    def test_section_order_converged_within_tab(self, backend):
        # live section order is [s1, s2]; declaring [s2, s1] reorders in place.
        row = {**self._FORM_ROW, "formxml": self._TWO_SECTION_FORM}
        from crm.core import forms

        with requests_mock.Mocker():
            new_xml, changes = forms.converge_declared_form(
                backend,
                "new_project",
                row,
                {"tabs": [{"name": "general", "sections": [{"name": "s2"}, {"name": "s1"}]}]},
            )
        assert _sections(new_xml, "general") == ["s2", "s1"]
        assert any(c["change"] == "converged" and c["kind"] == "section-order" for c in changes)

    def test_handler_flags_converged_in_place(self, backend):
        from crm.core import forms

        live = forms.add_handler_to_formxml(
            _MAIN_FORMXML, event="onload", function="App.onLoad", library_name="new_lib.js"
        )  # enabled=true by default
        row = {**self._FORM_ROW, "formxml": live}
        with requests_mock.Mocker():
            _, changes = forms.converge_declared_form(
                backend,
                "new_project",
                row,
                {
                    "handlers": [
                        {
                            "event": "onload",
                            "function": "App.onLoad",
                            "library": "new_lib.js",
                            "enabled": False,
                        }
                    ]
                },
            )
        c = self._by(changes, "handler", "App.onLoad")
        assert c["change"] == "converged"
        assert c["diff"] == {"enabled": {"old": True, "new": False}}

    def test_present_handler_matching_flags_is_a_no_op(self, backend):
        from crm.core import forms

        live = forms.add_handler_to_formxml(
            _MAIN_FORMXML, event="onload", function="App.onLoad", library_name="new_lib.js"
        )
        row = {**self._FORM_ROW, "formxml": live}
        with requests_mock.Mocker():
            _, changes = forms.converge_declared_form(
                backend,
                "new_project",
                row,
                {
                    "handlers": [
                        {
                            "event": "onload",
                            "function": "App.onLoad",
                            "library": "new_lib.js",
                            "enabled": True,
                            "pass_context": True,
                        }
                    ]
                },
            )
        assert changes == []


class TestApplyFormSpecReconcile:
    """apply_form_spec drives the reconcile: it commits converged drift, reports a
    would-converge preview under dry-run, and refuses an identity/ownership
    divergence (a named form that is not an existing main form) with no write
    (ADR 0024, #793).
    """

    _FORM_ROW = {
        "formid": "aaaaaaaa-0000-0000-0000-000000000001",
        "name": "Information",
        "objecttypecode": "new_project",
        "type": 2,
        "formxml": _MAIN_FORMXML,
        "isdefault": True,
    }

    def test_commits_converged_drift(self, backend):
        from crm.core import forms

        with requests_mock.Mocker() as m:
            m.get(_forms_url(backend), json={"value": [self._FORM_ROW]})
            patched = m.patch(
                backend.url_for(f"systemforms({self._FORM_ROW['formid']})"), status_code=204
            )
            result = forms.apply_form_spec(
                backend,
                "new_project",
                {"tabs": [{"name": "general", "label": "Overview"}]},
                publish=False,
                solution="TestSol",
                dry_run=False,
            )
        assert result["committed"] is True
        assert not result.get("blocked")
        assert [c["change"] for c in result["components"]] == ["converged"]
        assert patched.called

    def test_dry_run_previews_convergence_without_writing(self, dry_backend):
        from crm.core import forms

        with requests_mock.Mocker() as m:
            m.get(_forms_url(dry_backend), json={"value": [self._FORM_ROW]})
            patched = m.patch(
                dry_backend.url_for(f"systemforms({self._FORM_ROW['formid']})"), status_code=204
            )
            result = forms.apply_form_spec(
                dry_backend,
                "new_project",
                {"tabs": [{"name": "general", "label": "Overview"}]},
                publish=False,
                solution="TestSol",
                dry_run=True,
            )
        assert result["committed"] is False
        assert result["components"][0]["change"] == "converged"
        assert not patched.called

    def test_unchanged_form_reports_no_components(self, backend):
        from crm.core import forms

        with requests_mock.Mocker() as m:
            m.get(_forms_url(backend), json={"value": [self._FORM_ROW]})
            patched = m.patch(
                backend.url_for(f"systemforms({self._FORM_ROW['formid']})"), status_code=204
            )
            result = forms.apply_form_spec(
                backend,
                "new_project",
                {"tabs": [{"name": "general", "label": "General"}]},
                publish=False,
                solution="TestSol",
                dry_run=False,
            )
        assert result["components"] == []
        assert result["committed"] is False
        assert not patched.called

    def test_unknown_named_form_is_blocked_not_written(self, backend):
        from crm.core import forms

        with requests_mock.Mocker() as m:
            m.get(_forms_url(backend), json={"value": [self._FORM_ROW]})
            patched = m.patch(
                backend.url_for(f"systemforms({self._FORM_ROW['formid']})"), status_code=204
            )
            result = forms.apply_form_spec(
                backend,
                "new_project",
                {"name": "Ghost Form", "tabs": [{"name": "general", "label": "X"}]},
                publish=False,
                solution="TestSol",
                dry_run=False,
            )
        assert result["committed"] is False
        assert result["components"] == []
        assert len(result["blocked"]) == 1
        blk = result["blocked"][0]
        assert blk["kind"] == "form" and blk["name"] == "Ghost Form"
        assert "Ghost Form" in blk["reason"]
        assert not patched.called


_BILINGUAL_XML = (
    "<form><tabs>"
    '<tab name="general" id="{aaaa1111-0000-0000-0000-000000000001}">'
    "<labels>"
    '<label description="General" languagecode="1033" />'
    '<label description="عام" languagecode="1025" />'
    "</labels>"
    '<columns><column width="100%"><sections>'
    '<section name="s" id="{bbbb2222-0000-0000-0000-000000000002}">'
    '<labels><label description="Summary" languagecode="1033" /></labels>'
    "<rows /></section></sections></column></columns></tab>"
    "</tabs></form>"
)


class TestLabelLanguageScan:
    def test_collects_distinct_languagecodes(self):
        from crm.core import forms

        assert forms.label_languages_in_formxml(_BILINGUAL_XML) == {1033, 1025}

    def test_empty_formxml_yields_empty_set(self):
        from crm.core import forms

        assert forms.label_languages_in_formxml("") == set()

    def test_ignores_non_numeric_languagecode(self):
        from crm.core import forms

        xml = '<form><tab><labels><label description="x" languagecode="en" /></labels></tab></form>'
        assert forms.label_languages_in_formxml(xml) == set()

    def test_ignores_oversized_digit_languagecode(self):
        """A digit-only languagecode over Python's int_max_str_digits passes
        ``isdigit()`` but raises ``ValueError`` in ``int()`` — skip it, don't crash.
        """
        import sys

        from crm.core import forms

        big = "1" * (sys.get_int_max_str_digits() + 1)
        xml = (
            f'<form><tab><labels><label description="x" languagecode="{big}" />'
            "</labels></tab></form>"
        )
        assert forms.label_languages_in_formxml(xml) == set()


class TestLabelLanguageWarning:
    def test_warns_naming_foreign_codes(self):
        from crm.core import forms

        warning = forms.label_language_warning(_BILINGUAL_XML, 1025)
        assert warning is not None
        # The foreign-code list names 1033 (to be discarded), not the caller's own 1025.
        assert "language(s) 1033 differ" in warning
        assert "translation import" in warning

    def test_silent_when_all_labels_match_caller(self):
        from crm.core import forms

        xml = (
            '<form><tab><labels><label description="G" languagecode="1033" /></labels></tab></form>'
        )
        assert forms.label_language_warning(xml, 1033) is None

    def test_silent_when_caller_language_unknown(self):
        from crm.core import forms

        assert forms.label_language_warning(_BILINGUAL_XML, None) is None


class TestLabelProjectionNote:
    def test_names_caller_language(self):
        from crm.core import forms

        note = forms.label_projection_note(1025)
        assert note is not None
        assert "1025" in note
        assert "translation export" in note

    def test_none_when_language_unknown(self):
        from crm.core import forms

        assert forms.label_projection_note(None) is None


_MONO_1033_XML = (
    "<form><tabs>"
    '<tab name="general" id="{aaaa1111-0000-0000-0000-000000000001}">'
    '<labels><label description="General" languagecode="1033" /></labels>'
    '<columns><column width="100%"><sections>'
    '<section name="s" id="{bbbb2222-0000-0000-0000-000000000002}">'
    '<labels><label description="Summary" languagecode="1033" /></labels>'
    "<rows /></section></sections></column></columns></tab>"
    "</tabs></form>"
)


class TestCommitFormChangeLabelWarning:
    """`_commit_form_change` stashes a `_warnings` advisory when the outgoing
    formxml carries labels in a language other than the caller's (#940). Uses
    add_form_field as the seam; the CLI authors labels in 1033.
    """

    _FORM = {
        "formid": "11112222-3333-4444-5555-666677778888",
        "name": "Information",
        "objecttypecode": "new_project",
        "type": 2,
        "formxml": _MONO_1033_XML,
        "description": "Main",
        "isdefault": True,
    }

    def _mock_add_field(self, m, backend):
        m.get(_forms_url(backend), json={"value": [self._FORM]})
        m.get(
            _attr_url(backend, "new_owner"),
            json={"AttributeType": "Lookup", "LogicalName": "new_owner"},
        )
        m.patch(backend.url_for(f"systemforms({self._FORM['formid']})"), status_code=204)

    def test_warns_when_caller_language_differs_from_authored_label(self, backend, monkeypatch):
        from crm.core import connection, forms

        monkeypatch.setattr(connection, "caller_ui_language_id", lambda b: 1025)
        with requests_mock.Mocker() as m:
            self._mock_add_field(m, backend)
            result = forms.add_form_field(backend, "new_project", "new_owner")
        assert "_warnings" in result
        assert any("1033" in w for w in result["_warnings"])

    def test_silent_when_caller_language_matches(self, backend, monkeypatch):
        from crm.core import connection, forms

        monkeypatch.setattr(connection, "caller_ui_language_id", lambda b: 1033)
        with requests_mock.Mocker() as m:
            self._mock_add_field(m, backend)
            result = forms.add_form_field(backend, "new_project", "new_owner")
        assert "_warnings" not in result


# ── form labels: multi-language label dump (issue #942) ──────────────────────

# A form with three label-bearing elements: a tab keyed by an explicit `labelid`
# (so the join must prefer it over `id`), a section keyed by `id` only (labelid
# absent → fallback), and a field cell whose label has no translation row (→
# formxml-projection fallback). Element ids double as the object-id join keys.
_LABELS_FORMXML = (
    "<form><tabs>"
    "<tab id='{TAB000-0000-0000-0000-000000000000}' name='general' "
    "labelid='{LBL000-0000-0000-0000-000000000000}'>"
    "<labels><label description='General' languagecode='1033'/></labels>"
    "<columns><column><sections>"
    "<section id='{SEC000-0000-0000-0000-000000000000}' name='details'>"
    "<labels><label description='Details' languagecode='1033'/></labels>"
    "<rows><row><cell id='{CEL000-0000-0000-0000-000000000000}'>"
    "<labels><label description='Owner' languagecode='1033'/></labels>"
    "<control id='new_owner' datafieldname='new_owner'/>"
    "</cell></row></rows>"
    "</section>"
    "</sections></column></columns>"
    "</tab>"
    "</tabs></form>"
)

# Translation rows keyed by the tab's labelid and the section's id (both lowercased,
# brace-stripped), bilingual; the cell's id is deliberately absent.
_LABELS_BY_ID = {
    "lbl000-0000-0000-0000-000000000000": {"1033": "General", "1036": "Général"},
    "sec000-0000-0000-0000-000000000000": {"1033": "Details", "1036": "Détails"},
}


def _find_node(tree, node_type) -> dict:
    """The first node of ``node_type`` in a label tree (depth-first); asserts one
    exists so callers can subscript the result directly.
    """

    def _search(nodes):
        for node in nodes:
            if node["type"] == node_type:
                return node
            for key in ("sections", "cells"):
                hit = _search(node.get(key, []))
                if hit is not None:
                    return hit
        return None

    node = _search(tree)
    assert node is not None, f"no {node_type!r} node found in {tree}"
    return node


class TestFormLabelTree:
    def test_tab_joined_by_labelid_not_id(self):
        from crm.core import forms

        tree, matched = forms.form_label_tree(
            _LABELS_FORMXML, _LABELS_BY_ID, caller_language_id=1033
        )
        assert matched is True
        tab = _find_node(tree, "tab")
        assert tab["name"] == "general"
        assert tab["source"] == "translation"
        assert tab["labels"] == {"1033": "General", "1036": "Général"}

    def test_section_joined_by_id_when_labelid_absent(self):
        from crm.core import forms

        tree, _ = forms.form_label_tree(_LABELS_FORMXML, _LABELS_BY_ID, caller_language_id=1033)
        section = _find_node(tree, "section")
        assert section["source"] == "translation"
        assert section["labels"] == {"1033": "Details", "1036": "Détails"}

    def test_unmatched_cell_falls_back_to_formxml_projection(self):
        from crm.core import forms

        tree, _ = forms.form_label_tree(_LABELS_FORMXML, _LABELS_BY_ID, caller_language_id=1033)
        cell = _find_node(tree, "cell")
        assert cell["datafieldname"] == "new_owner"
        assert cell["source"] == "formxml-projection"
        # the projected label text is surfaced under the caller's language
        assert cell["labels"] == {"1033": "Owner"}

    def test_nesting_is_tab_section_cell(self):
        from crm.core import forms

        tree, _ = forms.form_label_tree(_LABELS_FORMXML, _LABELS_BY_ID, caller_language_id=1033)
        assert [n["type"] for n in tree] == ["tab"]
        assert [n["type"] for n in tree[0]["sections"]] == ["section"]
        assert [n["type"] for n in tree[0]["sections"][0]["cells"]] == ["cell"]

    def test_no_matches_reports_matched_false(self):
        from crm.core import forms

        tree, matched = forms.form_label_tree(_LABELS_FORMXML, {}, caller_language_id=1033)
        assert matched is False
        # every node still present, all projection-sourced
        assert _find_node(tree, "tab")["source"] == "formxml-projection"


_FORM_BY_ID_ROW = {
    "formid": "98ae5881-b152-4eb9-916d-539c83ff69c7",
    "name": "Information",
    "objecttypecode": "new_project",
    "type": 2,
    "formxml": _LABELS_FORMXML,
    "description": None,
    "isdefault": False,
}


class TestReadFormById:
    def test_reads_single_form(self, backend):
        from crm.core import forms

        fid = _FORM_BY_ID_ROW["formid"]
        with requests_mock.Mocker() as m:
            m.get(backend.url_for(f"systemforms({fid})"), json=_FORM_BY_ID_ROW)
            row = forms.read_form_by_id(backend, fid)
        assert row["formid"] == fid
        assert row["name"] == "Information"
        assert "<form>" in row["formxml"]

    def test_missing_form_raises(self, backend):
        from crm.core import forms

        fid = "00000000-0000-0000-0000-000000000000"
        with requests_mock.Mocker() as m:
            m.get(backend.url_for(f"systemforms({fid})"), status_code=404, json={})
            with pytest.raises(D365Error):
                forms.read_form_by_id(backend, fid)


class TestFormLabels:
    def _mock(self, m, backend, *, zip_bytes):
        fid = _FORM_BY_ID_ROW["formid"]
        m.get(backend.url_for(f"systemforms({fid})"), json=_FORM_BY_ID_ROW)
        m.post(
            backend.url_for("solutions/Microsoft.Dynamics.CRM.ExportTranslation"),
            json={"ExportTranslationFile": base64.b64encode(zip_bytes).decode("ascii")},
        )

    def test_returns_envelope_with_languages_and_tree(self, backend, monkeypatch):
        from crm.core import connection, forms

        monkeypatch.setattr(connection, "caller_ui_language_id", lambda b: 1033)
        with requests_mock.Mocker() as m:
            self._mock(m, backend, zip_bytes=_labels_zip_for(_LABELS_BY_ID))
            info = forms.form_labels(backend, _FORM_BY_ID_ROW["formid"], solution="CRMWorx")
        assert info["form"] == "Information"
        assert info["solution"] == "CRMWorx"
        assert info["languages"] == [1033, 1036]
        assert _find_node(info["elements"], "tab")["labels"]["1036"] == "Général"

    def test_form_not_in_solution_raises_component_hint(self, backend, monkeypatch):
        from crm.core import connection, forms

        monkeypatch.setattr(connection, "caller_ui_language_id", lambda b: 1033)
        with requests_mock.Mocker() as m:
            # translations that match none of the form's element ids
            self._mock(m, backend, zip_bytes=_labels_zip_for({}))
            with pytest.raises(D365Error, match="component of that solution"):
                forms.form_labels(backend, _FORM_BY_ID_ROW["formid"], solution="CRMWorx")


def _labels_zip_for(by_id: dict[str, dict[str, str]]) -> bytes:
    """A CrmTranslations.xml zip whose Localized Labels sheet carries ``by_id``
    as bilingual (1033 + 1036) displayname rows — for exercising the join.
    """
    import io
    import zipfile

    rows = []
    for object_id, langs in by_id.items():
        rows.append(
            "<Row>"
            "<Cell><Data ss:Type='String'>new_project</Data></Cell>"
            f"<Cell><Data ss:Type='String'>{object_id}</Data></Cell>"
            "<Cell><Data ss:Type='String'>displayname</Data></Cell>"
            f"<Cell><Data ss:Type='String'>{langs.get('1033', '')}</Data></Cell>"
            f"<Cell><Data ss:Type='String'>{langs.get('1036', '')}</Data></Cell>"
            "</Row>"
        )
    xml = (
        "<?xml version='1.0'?>"
        "<Workbook xmlns='urn:schemas-microsoft-com:office:spreadsheet' "
        "xmlns:ss='urn:schemas-microsoft-com:office:spreadsheet'>"
        "<Worksheet ss:Name='Localized Labels'><Table>"
        "<Row><Cell><Data ss:Type='String'>Entity name</Data></Cell>"
        "<Cell><Data ss:Type='String'>Object ID</Data></Cell>"
        "<Cell><Data ss:Type='String'>Object Column Name</Data></Cell>"
        "<Cell><Data ss:Type='Number'>1033</Data></Cell>"
        "<Cell><Data ss:Type='Number'>1036</Data></Cell></Row>"
        + "".join(rows)
        + "</Table></Worksheet></Workbook>"
    )
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w") as zf:
        zf.writestr("CrmTranslations.xml", xml)
        zf.writestr("[Content_Types].xml", "<Types/>")
    return buf.getvalue()


# ── form labels CLI command (issue #942) ─────────────────────────────────────

from click.testing import CliRunner  # noqa: E402

from crm.utils.d365_backend import ConnectionProfile  # noqa: E402

_LABELS_ENVELOPE = {
    "formid": "98ae5881-b152-4eb9-916d-539c83ff69c7",
    "form": "Information",
    "solution": "CRMWorx",
    "languages": [1033, 1036],
    "elements": [
        {
            "type": "tab",
            "name": "general",
            "label_object_id": "lbl000-0000-0000-0000-000000000000",
            "source": "translation",
            "labels": {"1033": "General", "1036": "Général"},
            "sections": [
                {
                    "type": "section",
                    "name": "details",
                    "label_object_id": "sec000-0000-0000-0000-000000000000",
                    "source": "translation",
                    "labels": {"1033": "Details", "1036": "Détails"},
                    "cells": [
                        {
                            "type": "cell",
                            "name": "new_owner",
                            "datafieldname": "new_owner",
                            "label_object_id": None,
                            "source": "formxml-projection",
                            "labels": {"1033": "Owner"},
                        }
                    ],
                }
            ],
        }
    ],
}


def _seed_form_profile(tmp_path, monkeypatch):
    monkeypatch.setenv("CRM_HOME", str(tmp_path / ".crm"))
    monkeypatch.setenv("CRM_DOTENV", str(tmp_path / "noop.env"))
    from crm.core import session as session_mod

    session_mod.save_profile(
        ConnectionProfile(
            name="t", url="https://crm.contoso.local/contoso", domain="CONTOSO", username="alice"
        )
    )
    session_mod.save_profile_secret_plaintext("t", "pw")


class TestFormLabelsCommand:
    def test_json_emits_tree_and_passes_args(self, monkeypatch, tmp_path):
        _seed_form_profile(tmp_path, monkeypatch)
        from crm.commands import form as form_cmd

        captured = {}
        monkeypatch.setattr(
            form_cmd.forms_mod,
            "form_labels",
            lambda backend, formid, **kw: captured.update(formid=formid, **kw) or _LABELS_ENVELOPE,
        )
        from crm.cli import cli

        result = CliRunner().invoke(
            cli,
            [
                "--profile",
                "t",
                "--json",
                "form",
                "labels",
                _LABELS_ENVELOPE["formid"],
                "--solution",
                "CRMWorx",
            ],
        )
        assert result.exit_code == 0, result.output
        import json

        envelope = json.loads(result.stdout)
        assert captured["formid"] == _LABELS_ENVELOPE["formid"]
        assert captured["solution"] == "CRMWorx"
        assert envelope["data"]["languages"] == [1033, 1036]
        assert envelope["data"]["elements"][0]["labels"]["1036"] == "Général"

    def test_human_renders_labels(self, monkeypatch, tmp_path):
        _seed_form_profile(tmp_path, monkeypatch)
        from crm.commands import form as form_cmd

        monkeypatch.setattr(
            form_cmd.forms_mod, "form_labels", lambda backend, formid, **kw: _LABELS_ENVELOPE
        )
        from crm.cli import cli

        result = CliRunner().invoke(
            cli,
            [
                "--profile",
                "t",
                "form",
                "labels",
                _LABELS_ENVELOPE["formid"],
                "--solution",
                "CRMWorx",
            ],
        )
        assert result.exit_code == 0, result.output
        assert "Général" in result.output
        assert "new_owner" in result.output
