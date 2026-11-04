"""Offline tests for the read-only CRM 2011 SOAP transport (`crm.core.legacy`).

Fixtures under ``fixtures/legacy/`` are real dev-org responses (CRM 2011 UR15,
5.0.9690.3557) with GUIDs replaced and no host or user name.
"""

# pyright: basic
from __future__ import annotations

from pathlib import Path

import pytest
import requests
import requests_mock

from crm.core.legacy import READ_ONLY_REQUESTS, LegacyClient
from crm.utils.d365_backend import ConnectionProfile, D365Error, classify_d365_error

_FIXTURES = Path(__file__).parent / "fixtures" / "legacy"
_ENDPOINT = "https://crm.contoso.local/contoso/XRMServices/2011/Organization.svc/web"
_ACTION = "http://schemas.microsoft.com/xrm/2011/Contracts/Services/IOrganizationService/"


def _fixture(name: str) -> str:
    return (_FIXTURES / name).read_text()


@pytest.fixture
def client() -> LegacyClient:
    profile = ConnectionProfile(
        name="legacy",
        url="https://crm.contoso.local/contoso",
        domain="CONTOSO",
        username="alice",
        platform="crm2011",
    )
    return LegacyClient(profile, "pw")


def _soap(m: requests_mock.Mocker, name: str, status: int = 200) -> None:
    m.post(
        _ENDPOINT,
        text=_fixture(name),
        status_code=status,
        headers={"Content-Type": "text/xml; charset=utf-8"},
    )


class TestEnvelope:
    @pytest.mark.parametrize("request_name", ["WhoAmI", "RetrieveVersion"])
    def test_execute_posts_soap11_envelope_naming_the_request(self, client, request_name):
        with requests_mock.Mocker() as m:
            _soap(m, "whoami.xml" if request_name == "WhoAmI" else "retrieve_version.xml")
            client.execute(request_name)
        sent = m.request_history[0]
        assert sent.method == "POST"
        assert sent.url == _ENDPOINT
        assert sent.headers["Content-Type"] == "text/xml; charset=utf-8"
        assert sent.headers["SOAPAction"] == _ACTION + "Execute"
        body = sent.body.decode()
        assert body.startswith('<s:Envelope xmlns:s="http://schemas.xmlsoap.org/soap/envelope/">')
        assert f"<a:RequestName>{request_name}</a:RequestName>" in body


class TestResponseParsing:
    def test_whoami_results(self, client):
        with requests_mock.Mocker() as m:
            _soap(m, "whoami.xml")
            results = client.execute("WhoAmI")
        assert results == {
            "UserId": "11111111-1111-1111-1111-111111111111",
            "BusinessUnitId": "22222222-2222-2222-2222-222222222222",
            "OrganizationId": "33333333-3333-3333-3333-333333333333",
        }

    def test_retrieve_version_results(self, client):
        with requests_mock.Mocker() as m:
            _soap(m, "retrieve_version.xml")
            assert client.execute("RetrieveVersion") == {"Version": "5.0.9690.3557"}


class TestFaults:
    def test_mixed_case_soap_media_type_is_parsed(self, client):
        with requests_mock.Mocker() as m:
            m.post(
                _ENDPOINT,
                text=_fixture("retrieve_version.xml"),
                headers={"Content-Type": "Text/XML; charset=utf-8"},
            )
            assert client.execute("RetrieveVersion")["Version"] == "5.0.9690.3557"

    def test_organization_service_fault_carries_hex_error_code(self, client):
        with requests_mock.Mocker() as m:
            _soap(m, "fault_not_found.xml", status=500)
            with pytest.raises(D365Error) as info:
                client.execute("WhoAmI")
        exc = info.value
        assert exc.status == 400  # a Client fault, though the wire status is 500
        assert exc.code == "0x80040217"
        assert str(exc) == "account With Id = 44444444-4444-4444-4444-444444444444 Does Not Exist"
        assert classify_d365_error(exc.status, exc.code, str(exc))[0] == "not_found"

    def test_request_not_supported_fault(self, client):
        with requests_mock.Mocker() as m:
            _soap(m, "fault_request_not_supported.xml", status=500)
            with pytest.raises(D365Error) as info:
                client.execute("RetrieveVersion")
        assert info.value.code == "0x80040315"
        assert "Request not supported" in str(info.value)
        exc = info.value
        assert classify_d365_error(exc.status, exc.code, str(exc)) == ("validation", False)

    def test_a_server_fault_keeps_the_wire_status(self, client):
        body = _fixture("fault_request_not_supported.xml").replace("s:Client", "s:Server")
        with requests_mock.Mocker() as m:
            m.post(_ENDPOINT, text=body, status_code=500, headers={"Content-Type": "text/xml"})
            with pytest.raises(D365Error) as info:
                client.execute("RetrieveVersion")
        assert info.value.status == 500

    def test_401_plain_text_is_classified_not_parsed(self, client):
        with requests_mock.Mocker() as m:
            m.post(
                _ENDPOINT,
                text=_fixture("unauthorized_401.txt"),
                status_code=401,
                headers={"Content-Type": "text/plain"},
            )
            with pytest.raises(D365Error) as info:
                client.execute("WhoAmI")
        assert info.value.status == 401
        assert classify_d365_error(info.value.status, info.value.code, str(info.value))[0] == (
            "auth_failed"
        )

    def test_html_error_page_is_classified_not_parsed(self, client):
        with requests_mock.Mocker() as m:
            m.post(
                _ENDPOINT,
                text=_fixture("iis_404.html"),
                status_code=404,
                headers={"Content-Type": "text/html"},
            )
            with pytest.raises(D365Error) as info:
                client.execute("WhoAmI")
        assert info.value.status == 404
        assert "text/html" in str(info.value)

    @pytest.mark.parametrize("raw", ["--5", "\u00b2", "abc", ""])
    def test_an_unreadable_error_code_keeps_the_fault_without_a_code(self, client, raw):
        body = _fixture("fault_not_found.xml").replace(
            "<ErrorCode>-2147220969</ErrorCode>", f"<ErrorCode>{raw}</ErrorCode>", 1
        )
        with requests_mock.Mocker() as m:
            m.post(_ENDPOINT, text=body, status_code=500, headers={"Content-Type": "text/xml"})
            with pytest.raises(D365Error) as info:
                client.execute("WhoAmI")
        assert info.value.code is None
        assert "Does Not Exist" in str(info.value)


class TestConnectRetries:
    def test_retries_after_connect_timeout(self, client, no_sleep):
        with requests_mock.Mocker() as m:
            m.post(
                _ENDPOINT,
                [
                    {"exc": requests.exceptions.ConnectTimeout},
                    {
                        "text": _fixture("retrieve_version.xml"),
                        "headers": {"Content-Type": "text/xml; charset=utf-8"},
                    },
                ],
            )
            assert client.execute("RetrieveVersion") == {"Version": "5.0.9690.3557"}
        assert m.call_count == 2

    def test_retries_a_tls_handshake_read_timeout(self, client, no_sleep):
        # Measured on the dev org: the flaky VPN connect surfaces as a ReadTimeout
        # raised during the TLS handshake, before any request is sent.
        with requests_mock.Mocker() as m:
            m.post(
                _ENDPOINT,
                [
                    {"exc": requests.exceptions.ReadTimeout},
                    {
                        "text": _fixture("whoami.xml"),
                        "headers": {"Content-Type": "text/xml; charset=utf-8"},
                    },
                ],
            )
            client.execute("WhoAmI")
        assert m.call_count == 2

    def test_retries_are_capped(self, client, no_sleep):
        with requests_mock.Mocker() as m:
            m.post(_ENDPOINT, exc=requests.exceptions.ConnectionError)
            with pytest.raises(D365Error) as info:
                client.execute("WhoAmI")
        assert m.call_count == 4
        category, _ = classify_d365_error(info.value.status, info.value.code, str(info.value))
        assert category == "transport_error"

    def test_other_requests_failures_are_wrapped_without_retry(self, client, no_sleep):
        with requests_mock.Mocker() as m:
            m.post(_ENDPOINT, exc=requests.exceptions.MissingSchema)
            with pytest.raises(D365Error) as info:
                client.execute("WhoAmI")
        assert m.call_count == 1
        category, _ = classify_d365_error(info.value.status, info.value.code, str(info.value))
        assert category == "transport_error"

    def test_no_retry_after_a_500(self, client, no_sleep):
        with requests_mock.Mocker() as m:
            _soap(m, "fault_not_found.xml", status=500)
            with pytest.raises(D365Error):
                client.execute("WhoAmI")
        assert m.call_count == 1


class TestAllowlist:
    def test_allowlist_is_one_frozen_set(self):
        assert READ_ONLY_REQUESTS == frozenset({"WhoAmI", "RetrieveVersion"})

    @pytest.mark.parametrize(
        "operation", ["Create", "Update", "Delete", "Associate", "Disassociate"]
    )
    def test_mutating_operation_refused_before_any_io(self, client, operation):
        with requests_mock.Mocker() as m:
            with pytest.raises(D365Error, match=operation):
                client._send(operation, "<x/>")
        assert m.call_count == 0

    @pytest.mark.parametrize("request_name", ["Create", "SetState", "Assign", "NoSuchRequest"])
    def test_unlisted_request_name_refused_before_any_io(self, client, request_name):
        with requests_mock.Mocker() as m:
            with pytest.raises(D365Error, match=request_name):
                client.execute(request_name)
        assert m.call_count == 0

    def test_execute_without_request_name_refused(self, client):
        with requests_mock.Mocker() as m:
            with pytest.raises(D365Error):
                client._send("Execute", "<x/>")
        assert m.call_count == 0
