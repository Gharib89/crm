"""Unit tests for crm.core.forms."""
# pyright: basic
from __future__ import annotations

import pytest
import requests_mock

from crm.utils.d365_backend import ConnectionProfile, D365Backend


@pytest.fixture
def profile() -> ConnectionProfile:
    return ConnectionProfile(
        name="testp", url="https://crm.contoso.local/contoso",
        domain="CONTOSO", username="alice", api_version="v9.2", verify_ssl=False,
    )


@pytest.fixture
def backend(profile):
    return D365Backend(profile, password="pw", dry_run=False)


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
