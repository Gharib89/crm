"""Core-layer tests for `crm dashboard` (systemform type=0 verbs)."""
# pyright: basic
from __future__ import annotations

import pytest
import requests_mock

from crm.core import dashboard
from crm.utils.d365_backend import D365Error

_DASH_ID = "11112222-3333-4444-5555-666677778888"
_NEW_ID = "99998888-7777-6666-5555-444433332222"
_DASH_ROW = {
    "formid": _DASH_ID,
    "name": "Sales Overview",
    "objecttypecode": "none",
    "description": "Org sales dashboard",
    "isdefault": False,
    "type": 0,
    "formxml": "<form><tabs/></form>",
}


def _forms_url(backend) -> str:
    return backend.url_for("systemforms")


class TestListDashboards:
    def test_scopes_to_type_0(self, backend):
        with requests_mock.Mocker() as m:
            m.get(_forms_url(backend), json={"value": [_DASH_ROW]})
            result = dashboard.list_dashboards(backend)
        # the read is scoped to dashboards only (systemforms holds every form type)
        assert m.last_request.qs.get("$filter") == ["type eq 0"]
        assert len(result) == 1
        assert result[0]["formid"] == _DASH_ID

    def test_list_omits_formxml(self, backend):
        with requests_mock.Mocker() as m:
            m.get(_forms_url(backend), json={"value": [_DASH_ROW]})
            result = dashboard.list_dashboards(backend)
        # list returns list columns only — formxml is fetched via `dashboard get`
        assert "formxml" not in result[0]
        # the $select must not request the heavy formxml column
        assert "formxml" not in m.last_request.qs.get("$select", [""])[0]


class TestGetDashboard:
    def test_includes_formxml(self, backend):
        with requests_mock.Mocker() as m:
            m.get(backend.url_for(f"systemforms({_DASH_ID})"), json=_DASH_ROW)
            result = dashboard.get_dashboard(backend, _DASH_ID)
        assert result["formxml"] == "<form><tabs/></form>"
        assert result["name"] == "Sales Overview"

    def test_invalid_id_raises(self, backend):
        with pytest.raises(D365Error):
            dashboard.get_dashboard(backend, "not-a-guid")

    def test_rejects_non_dashboard_form(self, backend):
        # systemforms is shared — a main-form id (type 2) must not project as one
        with requests_mock.Mocker() as m:
            m.get(backend.url_for(f"systemforms({_DASH_ID})"),
                  json={**_DASH_ROW, "type": 2})
            with pytest.raises(D365Error, match="not a dashboard"):
                dashboard.get_dashboard(backend, _DASH_ID)


class TestDeleteDashboard:
    def test_delete(self, backend):
        with requests_mock.Mocker() as m:
            # pre-flight type check, then the delete
            m.get(backend.url_for(f"systemforms({_DASH_ID})"),
                  json={"formid": _DASH_ID, "type": 0})
            m.delete(backend.url_for(f"systemforms({_DASH_ID})"), status_code=204)
            result = dashboard.delete_dashboard(backend, _DASH_ID)
        assert result == {"deleted": True, "formid": _DASH_ID}

    def test_delete_dry_run_previews(self, dry_backend):
        # the pre-flight GET runs even under dry-run (reads-execute); the DELETE
        # is short-circuited by the backend.
        with requests_mock.Mocker() as m:
            m.get(dry_backend.url_for(f"systemforms({_DASH_ID})"),
                  json={"formid": _DASH_ID, "type": 0})
            result = dashboard.delete_dashboard(dry_backend, _DASH_ID)
        assert result == {"_dry_run": True, "would_delete": True, "formid": _DASH_ID}

    def test_delete_refuses_non_dashboard_form(self, backend):
        with requests_mock.Mocker() as m:
            m.get(backend.url_for(f"systemforms({_DASH_ID})"),
                  json={"formid": _DASH_ID, "type": 2})
            with pytest.raises(D365Error, match="not a dashboard"):
                dashboard.delete_dashboard(backend, _DASH_ID)
        # the destructive DELETE was never issued
        assert all(r.method != "DELETE" for r in m.request_history)


class TestCreateDashboard:
    _NEW_ID_URL = f"systemforms({_NEW_ID})"

    def _post_mock(self, m, backend):
        m.post(_forms_url(backend), status_code=204,
               headers={"OData-EntityId": backend.url_for(self._NEW_ID_URL)})

    def test_posts_type_0_org_dashboard(self, backend):
        with requests_mock.Mocker() as m:
            self._post_mock(m, backend)
            out = dashboard.create_dashboard(
                backend, name="Sales", formxml="<form/>")
        body = m.last_request.json()
        assert body["type"] == 0
        assert body["name"] == "Sales"
        assert body["formxml"] == "<form/>"
        assert body["objecttypecode"] == "none"
        assert out["created"] is True
        assert out["formid"] == _NEW_ID

    def test_create_dry_run_previews_resolved_body(self, dry_backend):
        out = dashboard.create_dashboard(
            dry_backend, name="Sales", formxml="<form/>", description="d")
        assert out["_dry_run"] is True
        assert out["would_create"]["entity_set"] == "systemforms"
        assert out["would_create"]["body"]["type"] == 0
        assert out["would_create"]["body"]["description"] == "d"

    def test_adds_solution_header(self, backend):
        with requests_mock.Mocker() as m:
            self._post_mock(m, backend)
            dashboard.create_dashboard(
                backend, name="Sales", formxml="<form/>", solution="MySol")
        assert m.last_request.headers.get("MSCRM.SolutionUniqueName") == "MySol"

    def test_publish_runs_publishallxml(self, backend, monkeypatch):
        called = {}
        monkeypatch.setattr("crm.core.solution.publish_all",
                            lambda b: called.setdefault("published", True))
        with requests_mock.Mocker() as m:
            self._post_mock(m, backend)
            out = dashboard.create_dashboard(
                backend, name="Sales", formxml="<form/>", publish=True)
        assert called.get("published") is True
        assert out["published"] is True
