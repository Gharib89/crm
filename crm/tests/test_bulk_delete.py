"""Unit tests for the server-side BulkDelete core (`crm data delete`)."""
# pyright: basic

from __future__ import annotations

import pytest
import requests_mock

from crm.core import bulk_delete
from crm.utils.d365_backend import D365Error

_FETCH = (
    '<fetch returntotalrecordcount="true"><entity name="contact">'
    '<filter><condition attribute="firstname" operator="eq" value="Bob"/></filter>'
    "</entity></fetch>"
)
_QUERY = {"EntityName": "contact", "Criteria": {"FilterOperator": "And", "Conditions": []}}
_JOB_ID = "11111111-2222-3333-4444-555555555555"


def _mock_convert(m, base):
    m.get(f"{base}FetchXmlToQueryExpression(FetchXml=@p1)", json={"Query": dict(_QUERY)})


def _mock_preview(m, base, count=3):
    m.get(f"{base}contacts",
          json={"@odata.context": f"{base}$metadata#contacts", "value": [], "@odata.count": count})


class TestSubmit:
    def test_submit_returns_job_id(self, backend, profile):
        base = profile.api_base
        with requests_mock.Mocker() as m:
            _mock_convert(m, base)
            _mock_preview(m, base, count=3)
            m.post(f"{base}BulkDelete", json={"JobId": _JOB_ID})
            result = bulk_delete.bulk_delete(backend, "contacts", _FETCH)
        assert result["job_id"] == _JOB_ID
        assert result["status"] == "submitted"
        assert result["match_count"] == 3

    def test_wait_polls_and_reports_counts(self, backend, profile):
        base = profile.api_base
        with requests_mock.Mocker() as m:
            _mock_convert(m, base)
            _mock_preview(m, base, count=5)
            m.post(f"{base}BulkDelete", json={"JobId": _JOB_ID})
            m.get(f"{base}asyncoperations({_JOB_ID})",
                  json={"asyncoperationid": _JOB_ID, "statecode": 3, "statuscode": 30})
            m.get(f"{base}bulkdeleteoperations",
                  json={"value": [{"successcount": 5, "failurecount": 0}]})
            result = bulk_delete.bulk_delete(backend, "contacts", _FETCH, wait=True)
        assert result["status"] == "completed"
        assert result["succeeded"] == 5
        assert result["failed"] == 0

    def test_dry_run_previews_without_submitting(self, dry_backend, profile):
        base = profile.api_base
        with requests_mock.Mocker() as m:
            _mock_convert(m, base)
            _mock_preview(m, base, count=7)
            # No BulkDelete POST is mocked: under dry-run it must never hit the wire.
            result = bulk_delete.bulk_delete(dry_backend, "contacts", _FETCH)
        assert result["_dry_run"] is True
        assert result["match_count"] == 7
        assert "job_id" not in result

    def test_conversion_without_query_raises(self, backend, profile):
        base = profile.api_base
        with requests_mock.Mocker() as m:
            m.get(f"{base}FetchXmlToQueryExpression(FetchXml=@p1)", json={})
            with pytest.raises(D365Error, match="no Query"):
                bulk_delete.bulk_delete(backend, "contacts", _FETCH)

    def test_malformed_fetchxml_raises_before_any_call(self, backend, profile):
        with requests_mock.Mocker() as m:
            with pytest.raises(D365Error, match="well-formed"):
                bulk_delete.bulk_delete(backend, "contacts", "<fetch><entity")
            assert m.call_count == 0
