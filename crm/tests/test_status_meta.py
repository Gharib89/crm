"""Unit tests for crm.core.status_meta (InsertStatusValue / UpdateStateValue /
custom state-model transitions)."""
# pyright: basic

from __future__ import annotations

import pytest
import requests_mock

from crm.utils.d365_backend import D365Backend, D365Error
from crm.core import status_meta as sm


class TestAddStatusValue:
    def test_inserts_status_tied_to_state(self, backend):
        with requests_mock.Mocker() as m:
            m.post(backend.url_for("InsertStatusValue"), json={"NewOptionValue": 727000003})
            out = sm.add_status_value(
                backend, "new_widget", state_code=0, label_text="Pending",
            )
        assert out["added"] is True
        assert out["value"] == 727000003
        body = m.request_history[0].json()
        assert body["EntityLogicalName"] == "new_widget"
        assert body["AttributeLogicalName"] == "statuscode"
        assert body["StateCode"] == 0
        assert body["Label"]["LocalizedLabels"][0]["Label"] == "Pending"
        assert "Value" not in body  # omitted → server assigns

    def test_explicit_value_and_solution_header(self, backend):
        with requests_mock.Mocker() as m:
            m.post(backend.url_for("InsertStatusValue"), json={"NewOptionValue": 5})
            sm.add_status_value(
                backend, "new_widget", state_code=1, label_text="Archived",
                value=5, solution="mysol",
            )
        req = m.request_history[0]
        assert req.json()["Value"] == 5
        assert req.headers["MSCRM.SolutionUniqueName"] == "mysol"

    def test_dry_run_does_not_post(self, dry_backend):
        with requests_mock.Mocker() as m:
            m.post(dry_backend.url_for("InsertStatusValue"), json={})
            out = sm.add_status_value(
                dry_backend, "new_widget", state_code=0, label_text="Pending",
            )
        assert out["_dry_run"] is True
        assert out["would_add_status"] is True
        assert [r for r in m.request_history if r.method == "POST"] == []

    def test_label_required(self, backend):
        with pytest.raises(D365Error, match="label is required"):
            sm.add_status_value(backend, "new_widget", state_code=0, label_text="")


class TestRelabelStateValue:
    def test_updates_state_label(self, backend):
        with requests_mock.Mocker() as m:
            m.post(backend.url_for("UpdateStateValue"), status_code=204, json={})
            out = sm.relabel_state_value(
                backend, "new_widget", value=1, label_text="Dormant",
                merge_labels=True,
            )
        assert out["updated"] is True
        body = m.request_history[0].json()
        assert body["AttributeLogicalName"] == "statecode"
        assert body["Value"] == 1
        assert body["MergeLabels"] is True
        assert body["Label"]["LocalizedLabels"][0]["Label"] == "Dormant"

    def test_dry_run_does_not_post(self, dry_backend):
        with requests_mock.Mocker() as m:
            m.post(dry_backend.url_for("UpdateStateValue"), status_code=204, json={})
            out = sm.relabel_state_value(
                dry_backend, "new_widget", value=1, label_text="Dormant",
            )
        assert out["_dry_run"] is True
        assert out["would_relabel_state"] is True
        assert [r for r in m.request_history if r.method == "POST"] == []


_STATUS_CAST_PATH = (
    "EntityDefinitions(LogicalName='new_widget')"
    "/Attributes(LogicalName='statuscode')/Microsoft.Dynamics.CRM.StatusAttributeMetadata"
)

_STATUS_OPTIONS = {
    "OptionSet": {
        "Options": [
            {"Value": 1, "State": 0, "TransitionData": None},
            {"Value": 2, "State": 1, "TransitionData": None},
            {"Value": 100000000, "State": 0, "TransitionData": None},
        ]
    }
}


class TestSetStatusTransitions:
    def test_writes_transition_xml_on_source_option(self, backend):
        with requests_mock.Mocker() as m:
            m.get(backend.url_for(_STATUS_CAST_PATH), json=dict(_STATUS_OPTIONS))
            m.put(backend.url_for(_STATUS_CAST_PATH), status_code=204, json={})
            out = sm.set_status_transitions(
                backend, "new_widget", transitions=[(1, 2), (1, 100000000)],
            )
        assert out["updated"] is True
        assert out["transitions_set"] == [1]
        put_req = [r for r in m.request_history if r.method == "PUT"][0]
        sent = put_req.json()
        opt1 = next(o for o in sent["OptionSet"]["Options"] if o["Value"] == 1)
        td = opt1["TransitionData"]
        assert 'sourcestatusid="1"' in td
        assert 'tostatusid="2"' in td
        assert 'tostatusid="100000000"' in td
        # Untouched source keeps its data.
        opt2 = next(o for o in sent["OptionSet"]["Options"] if o["Value"] == 2)
        assert opt2["TransitionData"] is None

    def test_rejects_unknown_status_value(self, backend):
        with requests_mock.Mocker() as m:
            m.get(backend.url_for(_STATUS_CAST_PATH), json=dict(_STATUS_OPTIONS))
            with pytest.raises(D365Error, match="999 is not an option"):
                sm.set_status_transitions(
                    backend, "new_widget", transitions=[(1, 999)],
                )

    def test_dry_run_gets_but_does_not_put(self, dry_backend):
        with requests_mock.Mocker() as m:
            m.get(dry_backend.url_for(_STATUS_CAST_PATH), json=dict(_STATUS_OPTIONS))
            m.put(dry_backend.url_for(_STATUS_CAST_PATH), status_code=204, json={})
            out = sm.set_status_transitions(
                dry_backend, "new_widget", transitions=[(1, 2)],
            )
        assert out["_dry_run"] is True
        assert out["would_set_transitions"] is True
        assert [r for r in m.request_history if r.method == "GET"]
        assert [r for r in m.request_history if r.method == "PUT"] == []

    def test_requires_transitions(self, backend):
        with pytest.raises(D365Error, match="at least one"):
            sm.set_status_transitions(backend, "new_widget", transitions=[])
