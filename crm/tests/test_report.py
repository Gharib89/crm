"""Core-layer tests for `crm report` (reports + reportcategory verbs)."""
# pyright: basic
from __future__ import annotations

import pytest
import requests_mock

from crm.core import report
from crm.utils.d365_backend import D365Error

_REPORT_ID = "11112222-3333-4444-5555-666677778888"
_NEW_ID = "99998888-7777-6666-5555-444433332222"
_RC_ID = "aaaabbbb-cccc-dddd-eeee-ffff00001111"
_ROW = {
    "reportid": _REPORT_ID,
    "name": "Quarterly Sales",
    "filename": "sales.rdl",
    "reporttypecode": 1,
    "ispersonal": True,
    "description": "Q sales",
    "bodyurl": None,
    "bodytext": "<Report/>",
}


def _reports_url(backend) -> str:
    return backend.url_for("reports")


def _categories_url(backend) -> str:
    return backend.url_for("reportcategories")


class TestListReports:
    def test_returns_summary_rows(self, backend):
        with requests_mock.Mocker() as m:
            m.get(_reports_url(backend), json={"value": [_ROW]})
            result = report.list_reports(backend)
        assert len(result) == 1
        assert result[0]["reportid"] == _REPORT_ID
        # list returns summary columns only — the RDL body is not fetched
        assert "bodytext" not in result[0]
        assert "bodytext" not in m.last_request.qs.get("$select", [""])[0]


class TestGetReport:
    def test_includes_body(self, backend):
        with requests_mock.Mocker() as m:
            m.get(backend.url_for(f"reports({_REPORT_ID})"), json=_ROW)
            result = report.get_report(backend, _REPORT_ID)
        assert result["bodytext"] == "<Report/>"
        assert result["name"] == "Quarterly Sales"

    def test_invalid_id_raises(self, backend):
        with pytest.raises(D365Error):
            report.get_report(backend, "not-a-guid")


class TestCreateReport:
    def _post_mock(self, m, backend):
        m.post(_reports_url(backend), status_code=204,
               headers={"OData-EntityId": backend.url_for(f"reports({_NEW_ID})")})

    def test_rdl_upload_sets_bodytext_and_type(self, backend):
        with requests_mock.Mocker() as m:
            self._post_mock(m, backend)
            out = report.create_report(
                backend, name="Sales", body="<Report/>", filename="sales.rdl")
        body = m.last_request.json()
        assert body["bodytext"] == "<Report/>"
        assert body["reporttypecode"] == report.RDL_REPORT
        assert body["filename"] == "sales.rdl"
        assert "bodyurl" not in body
        assert out["created"] is True
        assert out["reportid"] == _NEW_ID

    def test_link_sets_bodyurl_and_type(self, backend):
        with requests_mock.Mocker() as m:
            self._post_mock(m, backend)
            report.create_report(
                backend, name="Link", url="https://example.com/r")
        body = m.last_request.json()
        assert body["bodyurl"] == "https://example.com/r"
        assert body["reporttypecode"] == report.LINK_REPORT
        assert "bodytext" not in body

    def test_requires_exactly_one_source(self, backend):
        with pytest.raises(D365Error, match="exactly one"):
            report.create_report(backend, name="X")
        with pytest.raises(D365Error, match="exactly one"):
            report.create_report(
                backend, name="X", body="<Report/>", url="https://e.com/r")

    def test_org_sets_ispersonal_false(self, backend):
        with requests_mock.Mocker() as m:
            self._post_mock(m, backend)
            report.create_report(
                backend, name="Org", url="https://e.com/r", org=True)
        assert m.last_request.json()["ispersonal"] is False

    def test_personal_by_default_omits_ispersonal(self, backend):
        with requests_mock.Mocker() as m:
            self._post_mock(m, backend)
            report.create_report(backend, name="P", url="https://e.com/r")
        # personal is the server default; we don't write ispersonal unless --org
        assert "ispersonal" not in m.last_request.json()

    def test_adds_solution_header(self, backend):
        with requests_mock.Mocker() as m:
            self._post_mock(m, backend)
            report.create_report(
                backend, name="S", url="https://e.com/r", solution="MySol")
        assert m.last_request.headers.get("MSCRM.SolutionUniqueName") == "MySol"

    def test_dry_run_previews_resolved_body(self, dry_backend):
        out = report.create_report(
            dry_backend, name="Sales", body="<Report/>", org=True)
        assert out["_dry_run"] is True
        assert out["would_create"]["entity_set"] == "reports"
        assert out["would_create"]["body"]["ispersonal"] is False
        assert out["would_create"]["body"]["reporttypecode"] == report.RDL_REPORT


class TestSetCategory:
    def _post_mock(self, m, backend):
        m.post(_categories_url(backend), status_code=204,
               headers={"OData-EntityId":
                        backend.url_for(f"reportcategories({_RC_ID})")})

    def test_creates_bound_category_record(self, backend):
        with requests_mock.Mocker() as m:
            self._post_mock(m, backend)
            out = report.set_category(backend, _REPORT_ID, category="service")
        body = m.last_request.json()
        assert body["categorycode"] == 2
        # the report lookup binds through the live-verified `reportid` nav property
        assert body["reportid@odata.bind"] == f"/reports({_REPORT_ID})"
        assert out["categorycode"] == 2
        assert out["reportcategoryid"] == _RC_ID

    def test_unknown_category_raises(self, backend):
        with pytest.raises(D365Error, match="Unknown category"):
            report.set_category(backend, _REPORT_ID, category="finance")

    def test_invalid_report_id_raises(self, backend):
        with pytest.raises(D365Error):
            report.set_category(backend, "not-a-guid", category="sales")

    def test_dry_run_previews(self, dry_backend):
        out = report.set_category(dry_backend, _REPORT_ID, category="marketing")
        assert out["_dry_run"] is True
        assert out["would_create"]["entity_set"] == "reportcategories"
        assert out["would_create"]["body"]["categorycode"] == 3


class TestDeleteReport:
    def test_delete(self, backend):
        with requests_mock.Mocker() as m:
            m.delete(backend.url_for(f"reports({_REPORT_ID})"), status_code=204)
            result = report.delete_report(backend, _REPORT_ID)
        assert result == {"deleted": True, "reportid": _REPORT_ID}

    def test_delete_dry_run_previews(self, dry_backend):
        result = report.delete_report(dry_backend, _REPORT_ID)
        assert result == {
            "_dry_run": True, "would_delete": True, "reportid": _REPORT_ID}
