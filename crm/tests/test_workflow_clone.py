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


import requests_mock
from crm.utils.d365_backend import ConnectionProfile, D365Backend, D365Error


@pytest.fixture
def profile() -> ConnectionProfile:
    return ConnectionProfile(
        name="testp", url="https://crm.contoso.local/contoso",
        domain="CONTOSO", username="alice", api_version="v9.2", verify_ssl=False,
    )


@pytest.fixture
def backend(profile):
    return D365Backend(profile, password="pw", dry_run=False)


class TestGetWorkflow:
    def test_returns_definition(self, backend):
        from crm.core import workflow
        wf_url = backend.url_for(f"workflows({_SRC_ID})")
        with requests_mock.Mocker() as m:
            m.get(wf_url, json={
                "workflowid": _SRC_ID, "name": "Update request", "category": 0,
                "primaryentity": "cwx_ticket", "type": 1, "xaml": _XAML,
                "mode": 0, "scope": 4, "ondemand": True, "subprocess": False,
                "languagecode": 1033,
            })
            wf = workflow.get_workflow(backend, _SRC_ID)
        assert wf["primaryentity"] == "cwx_ticket"
        assert wf["xaml"] == _XAML

    def test_rejects_activation_copy(self, backend):
        from crm.core import workflow
        wf_url = backend.url_for(f"workflows({_SRC_ID})")
        with requests_mock.Mocker() as m:
            m.get(wf_url, json={"workflowid": _SRC_ID, "type": 2, "name": "X"})
            with pytest.raises(D365Error, match="definition"):
                workflow.get_workflow(backend, _SRC_ID)


def _patches(m):
    return [r for r in m.request_history if r.method == "PATCH"]


class TestCloneWorkflow:
    def _src(self, category=0):
        return {
            "workflowid": _SRC_ID, "name": "Update request", "category": category,
            "primaryentity": "cwx_ticket", "type": 1, "xaml": _XAML,
            "mode": 0, "scope": 4, "ondemand": True, "subprocess": False,
            "languagecode": 1033,
        }

    def test_clones_classic_workflow_as_draft(self, backend):
        from crm.core import workflow
        with requests_mock.Mocker() as m:
            m.get(backend.url_for(f"workflows({_SRC_ID})"), json=self._src())
            m.patch(requests_mock.ANY, status_code=204)
            out = workflow.clone_workflow_to_entity(
                backend, _SRC_ID, "cwx_ticketclone", activate=False,
            )
        body = _patches(m)[0].json()
        assert body["primaryentity"] == "cwx_ticketclone"
        assert 'Entity="cwx_ticketclone"' in body["xaml"]
        assert body["name"] == "Update request (Clone)"
        assert body["category"] == 0
        assert out["activated"] is False
        assert out["workflow_id"] == out["workflow_id"]  # a real GUID string
        # only the upsert PATCH happened, no activation PATCH
        assert len(_patches(m)) == 1

    def test_activate_true_compiles_after_create(self, backend):
        from crm.core import workflow
        with requests_mock.Mocker() as m:
            m.get(backend.url_for(f"workflows({_SRC_ID})"), json=self._src())
            m.patch(requests_mock.ANY, status_code=204)
            out = workflow.clone_workflow_to_entity(
                backend, _SRC_ID, "cwx_ticketclone", activate=True,
            )
        # two PATCHes: upsert (draft) then activation
        assert len(_patches(m)) == 2
        activation = _patches(m)[1].json()
        assert activation == {"statecode": 1, "statuscode": 2}
        assert out["activated"] is True

    def test_custom_name_override(self, backend):
        from crm.core import workflow
        with requests_mock.Mocker() as m:
            m.get(backend.url_for(f"workflows({_SRC_ID})"), json=self._src())
            m.patch(requests_mock.ANY, status_code=204)
            workflow.clone_workflow_to_entity(
                backend, _SRC_ID, "cwx_ticketclone", name="My Clone", activate=False,
            )
            assert _patches(m)[0].json()["name"] == "My Clone"

    def test_business_rule_supported(self, backend):
        from crm.core import workflow
        with requests_mock.Mocker() as m:
            m.get(backend.url_for(f"workflows({_SRC_ID})"), json=self._src(category=2))
            m.patch(requests_mock.ANY, status_code=204)
            out = workflow.clone_workflow_to_entity(
                backend, _SRC_ID, "cwx_ticketclone", activate=False,
            )
        assert out["category"] == 2

    @pytest.mark.parametrize("category", [3, 4])
    def test_action_and_bpf_fail_loudly(self, backend, category):
        from crm.core import workflow
        with requests_mock.Mocker() as m:
            m.get(backend.url_for(f"workflows({_SRC_ID})"), json=self._src(category=category))
            with pytest.raises(D365Error, match="not yet supported"):
                workflow.clone_workflow_to_entity(backend, _SRC_ID, "cwx_ticketclone")
        # nothing was written
        assert not _patches(m)

    @pytest.mark.parametrize("category", [1, 5])
    def test_dialog_and_modern_flow_out_of_scope(self, backend, category):
        from crm.core import workflow
        with requests_mock.Mocker() as m:
            m.get(backend.url_for(f"workflows({_SRC_ID})"), json=self._src(category=category))
            with pytest.raises(D365Error, match="not supported"):
                workflow.clone_workflow_to_entity(backend, _SRC_ID, "cwx_ticketclone")
