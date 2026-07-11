"""Unit tests for the server-side BulkDetectDuplicates core (`crm dup bulk-detect`)."""
# pyright: basic

from __future__ import annotations

import pytest
import requests_mock

from crm.core import dup
from crm.utils.d365_backend import D365Error

_JOB_ID = "11111111-2222-3333-4444-555555555555"
_FETCH = (
    '<fetch><entity name="account">'
    '<filter><condition attribute="statecode" operator="eq" value="0"/></filter>'
    "</entity></fetch>"
)
_QUERY = {"EntityName": "account", "Criteria": {"FilterOperator": "And", "Conditions": []}}


def _mock_convert(m, base):
    m.get(f"{base}FetchXmlToQueryExpression(FetchXml=@p1)", json={"Query": dict(_QUERY)})


def _mock_submit(m, base):
    m.post(f"{base}BulkDetectDuplicates", json={"JobId": _JOB_ID})


class TestSubmit:
    def test_bare_entity_sweep_returns_job_id(self, backend, profile):
        base = profile.api_base
        with requests_mock.Mocker() as m:
            _mock_submit(m, base)
            result = dup.bulk_detect(backend, "account")
        assert result["job_id"] == _JOB_ID
        assert result["status"] == "submitted"
        assert result["entity"] == "account"
        # A bare sweep submits a QueryExpression carrying only the entity name.
        body = m.last_request.json()
        assert body["Query"]["EntityName"] == "account"
        assert body["Query"]["@odata.type"] == "Microsoft.Dynamics.CRM.QueryExpression"
        assert body["SendEmailNotification"] is False
        # TemplateId is a required action parameter even with no mail sent — the
        # server rejects the request without it (0x80048d19).
        assert body["TemplateId"] == "00000000-0000-0000-0000-000000000000"

    def test_fetchxml_scope_is_converted_before_submit(self, backend, profile):
        base = profile.api_base
        with requests_mock.Mocker() as m:
            _mock_convert(m, base)
            _mock_submit(m, base)
            result = dup.bulk_detect(backend, "account", fetch_xml=_FETCH)
        assert result["job_id"] == _JOB_ID
        # The converted QueryExpression (with its criteria) is what gets submitted.
        body = m.last_request.json()
        assert body["Query"]["EntityName"] == "account"
        assert body["Query"]["Criteria"] == _QUERY["Criteria"]

    def test_fetch_entity_mismatch_raises_before_submit(self, backend, profile):
        base = profile.api_base
        with requests_mock.Mocker() as m:
            _mock_convert(m, base)  # the fetch's <entity> is "account"
            with pytest.raises(D365Error, match="must match the swept table"):
                dup.bulk_detect(backend, "contact", fetch_xml=_FETCH)
            # The mismatch is caught client-side — no BulkDetectDuplicates POST.
            assert not any(r.url.endswith("BulkDetectDuplicates") for r in m.request_history)

    def test_wait_polls_and_reports_duplicates(self, backend, profile):
        base = profile.api_base
        # The async job flags each record as a base record; the matched-counterpart
        # lookup is left empty, so a row carries only its base ref + the log PK.
        dupes = [
            {"_baserecordid_value": "a1", "duplicateid": "row1"},
            {"_baserecordid_value": "a2", "duplicateid": "row2"},
        ]
        with requests_mock.Mocker() as m:
            _mock_submit(m, base)
            m.get(
                f"{base}asyncoperations({_JOB_ID})",
                json={"asyncoperationid": _JOB_ID, "statecode": 3, "statuscode": 30},
            )
            m.get(f"{base}duplicaterecords", json={"value": dupes})
            result = dup.bulk_detect(backend, "account", wait=True)
        assert result["status"] == "completed"
        assert result["count"] == 2
        assert result["duplicates"] == dupes

    def test_dry_run_previews_without_submitting(self, dry_backend, profile):
        with requests_mock.Mocker() as m:
            # No BulkDetectDuplicates POST is mocked: under dry-run the POST is
            # previewed, never submitted (GET reads would still execute, but a
            # bare-entity sweep issues none).
            result = dup.bulk_detect(dry_backend, "account")
        assert result["_dry_run"] is True
        assert result["would_submit"] == "BulkDetectDuplicates"
        assert result["entity"] == "account"
        assert "job_id" not in result

    def test_missing_entity_raises(self, backend):
        with pytest.raises(D365Error, match="entity is required"):
            dup.bulk_detect(backend, "")

    def test_malformed_fetchxml_raises_before_any_call(self, backend):
        with requests_mock.Mocker() as m:
            with pytest.raises(D365Error, match="well-formed"):
                dup.bulk_detect(backend, "account", fetch_xml="<fetch><entity")
            # Fails fast locally — no FetchXmlToQueryExpression round-trip.
            assert m.call_count == 0

    def test_submit_without_job_id_raises(self, backend, profile):
        base = profile.api_base
        with requests_mock.Mocker() as m:
            m.post(f"{base}BulkDetectDuplicates", json={})
            with pytest.raises(D365Error, match="no JobId"):
                dup.bulk_detect(backend, "account")
