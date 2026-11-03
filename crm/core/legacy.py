"""Read-only SOAP transport for a legacy CRM 2011 org (ADR 0031).

A CRM 2011 org has no Web API, so ``crm legacy`` reaches it over SOAP 1.1 at
``<org url>/XRMServices/2011/Organization.svc/web`` with plain HTTP NTLM. The
main ``Organization.svc`` endpoint demands WS-Trust SPNEGO message security and
is out of scope. ``/web`` is documented for web resources, not external
clients, so whether it answers is checked per org (tested on CRM 2011 UR15,
5.0.9690.3557).

The transport is read-only by construction: every call passes one guard that
admits only the read operations and, for ``Execute``, a request named in
:data:`READ_ONLY_REQUESTS`, before any network I/O. An HTTP-method guard would
not do, since every SOAP call, read or write, is a POST.

This module is deliberately separate from ``D365Backend``: the OData URL
building, response parsing and v9 commands do not carry over.
"""

from __future__ import annotations

import time
import xml.etree.ElementTree as ET
from typing import TYPE_CHECKING
from xml.sax.saxutils import escape

from crm.utils.d365_backend import (
    _TRANSPORT_FAILURE_PREFIX,  # pyright: ignore[reportPrivateUsage]  # shared so classify_d365_error reads it as transport_error
    PLATFORM_CRM2011,
    ConnectionProfile,
    D365Error,
)
from crm.utils.safe_xml import fromstring

if TYPE_CHECKING:
    import requests

# The one allowlist of Execute request names. Later slices extend this set; a
# request outside it is refused without opening a connection.
READ_ONLY_REQUESTS: frozenset[str] = frozenset({"WhoAmI", "RetrieveVersion"})

# IOrganizationService operations that only read. Create, Update, Delete,
# Associate and Disassociate are refused.
_READ_OPERATIONS: frozenset[str] = frozenset({"Retrieve", "RetrieveMultiple", "Execute"})

ENDPOINT_PATH = "/XRMServices/2011/Organization.svc/web"
_SOAP_ACTION = "http://schemas.microsoft.com/xrm/2011/Contracts/Services/IOrganizationService/"

# Connect-failure retries: total attempts and the backoff between them. Only a
# failure that got no HTTP response is retried; any response, a 500 included,
# returns at once.
_CONNECT_ATTEMPTS = 4
_BACKOFF_SECONDS = (1.0, 2.0, 4.0)

_NS = {
    "s": "http://schemas.xmlsoap.org/soap/envelope/",
    "svc": "http://schemas.microsoft.com/xrm/2011/Contracts/Services",
    "a": "http://schemas.microsoft.com/xrm/2011/Contracts",
    "b": "http://schemas.datacontract.org/2004/07/System.Collections.Generic",
    "i": "http://www.w3.org/2001/XMLSchema-instance",
}


def _refuse(what: str) -> D365Error:
    return D365Error(
        f"refused: {what} is not an allowlisted read; a CRM 2011 (legacy) profile is "
        "read-only and `crm legacy` sends only allowlisted reads"
    )


class LegacyClient:
    """Read-only SOAP client for one CRM 2011 org, authenticated with HTTP NTLM."""

    def __init__(self, profile: ConnectionProfile, password: str):
        if profile.platform != PLATFORM_CRM2011:
            raise D365Error(
                f"Profile {profile.name!r} is not a CRM 2011 (legacy) profile; "
                "`crm legacy` runs only on a crm2011 profile."
            )
        import requests
        from requests_ntlm import HttpNtlmAuth

        self.profile = profile
        self.endpoint = profile.url + ENDPOINT_PATH
        principal = f"{profile.domain}\\{profile.username}" if profile.domain else profile.username
        self._session: requests.Session = requests.Session()
        self._session.auth = HttpNtlmAuth(principal, password)
        self._session.verify = profile.verify_ssl

    def execute(self, request_name: str) -> dict[str, str | None]:
        """Run an allowlisted parameterless ``Execute`` request; return its Results."""
        body = (
            '<Execute xmlns="http://schemas.microsoft.com/xrm/2011/Contracts/Services" '
            'xmlns:i="http://www.w3.org/2001/XMLSchema-instance">'
            '<request xmlns:a="http://schemas.microsoft.com/xrm/2011/Contracts">'
            '<a:Parameters xmlns:b="http://schemas.datacontract.org/2004/07/System.Collections.Generic"/>'
            '<a:RequestId i:nil="true"/>'
            f"<a:RequestName>{escape(request_name)}</a:RequestName>"
            "</request></Execute>"
        )
        root = self._send("Execute", body, request_name=request_name)
        results = root.find("s:Body/svc:ExecuteResponse/svc:ExecuteResult/a:Results", _NS)
        if results is None:
            raise D365Error(f"{request_name}: the SOAP response carries no ExecuteResult")
        return {
            pair.findtext("b:key", "", _NS): _value(pair.find("b:value", _NS))
            for pair in results.findall("a:KeyValuePairOfstringanyType", _NS)
        }

    def _send(self, operation: str, body: str, *, request_name: str | None = None) -> ET.Element:
        """POST one SOAP operation, the only path to the wire; the allowlist runs first."""
        if operation not in _READ_OPERATIONS:
            raise _refuse(f"operation {operation!r}")
        if operation == "Execute" and request_name not in READ_ONLY_REQUESTS:
            raise _refuse(f"request {request_name!r}")
        import requests

        envelope = (
            '<s:Envelope xmlns:s="http://schemas.xmlsoap.org/soap/envelope/">'
            f"<s:Body>{body}</s:Body></s:Envelope>"
        )
        headers = {
            "Content-Type": "text/xml; charset=utf-8",
            "SOAPAction": _SOAP_ACTION + operation,
        }
        attempt = 0
        while True:
            try:
                resp = self._session.post(
                    self.endpoint,
                    data=envelope.encode("utf-8"),
                    headers=headers,
                    timeout=self.profile.timeout,
                )
                break
            except (requests.ConnectionError, requests.Timeout) as exc:
                # No HTTP response arrived (connect refused or timed out, or the
                # TLS handshake stalled, which requests reports as ReadTimeout).
                # Every allowlisted call is a read, so a re-send is safe.
                if attempt + 1 >= _CONNECT_ATTEMPTS:
                    raise D365Error(f"{_TRANSPORT_FAILURE_PREFIX}: {exc}") from exc
                time.sleep(_BACKOFF_SECONDS[attempt])
                attempt += 1
        return _parse(resp)


def _value(node: ET.Element | None) -> str | None:
    if node is None or node.get(f"{{{_NS['i']}}}nil") == "true":
        return None
    return node.text


def _parse(resp: requests.Response) -> ET.Element:
    """Parse a SOAP response, or raise the D365Error a fault or non-SOAP body maps to."""
    content_type = resp.headers.get("Content-Type", "")
    if "xml" not in content_type:
        raise D365Error(
            f"HTTP {resp.status_code} from the CRM 2011 endpoint, a non-SOAP "
            f"{content_type or 'untyped'} response",
            status=resp.status_code,
        )
    try:
        root = fromstring(resp.content)
    except ET.ParseError as exc:
        raise D365Error(
            f"HTTP {resp.status_code} from the CRM 2011 endpoint, unreadable XML: {exc}",
            status=resp.status_code,
        ) from exc
    fault = root.find("s:Body/s:Fault", _NS)
    if fault is None:
        if resp.status_code >= 400:
            raise D365Error(
                f"HTTP {resp.status_code} from the CRM 2011 endpoint", status=resp.status_code
            )
        return root
    org_fault = fault.find(".//a:OrganizationServiceFault", _NS)
    code: str | None = None
    message = fault.findtext("faultstring") or "SOAP fault"
    if org_fault is not None:
        raw = org_fault.findtext("a:ErrorCode", None, _NS)
        if raw and raw.lstrip("-").isdigit():
            code = f"0x{int(raw) & 0xFFFFFFFF:08x}"
        message = org_fault.findtext("a:Message", None, _NS) or message
    raise D365Error(message, status=resp.status_code, code=code)
