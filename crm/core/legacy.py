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

import dataclasses
import time
import xml.etree.ElementTree as ET
from collections.abc import Iterator
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
READ_ONLY_REQUESTS: frozenset[str] = frozenset(
    {
        "WhoAmI",
        "RetrieveVersion",
        "RetrieveAllEntities",
        "RetrieveEntity",
        "RetrieveAllOptionSets",
        "RetrieveDependentComponents",
        "RetrieveRequiredComponents",
        "RetrieveDependenciesForDelete",
        # Exports the solution as stored; publishing first (PublishXml,
        # PublishAllXml) would write, so neither is here.
        "ExportSolution",
        "RetrieveApplicationRibbon",
        "RetrieveEntityRibbon",
    }
)

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
_SOAP_BODY_NS = (
    'xmlns="http://schemas.microsoft.com/xrm/2011/Contracts/Services" '
    'xmlns:i="http://www.w3.org/2001/XMLSchema-instance"'
)


def parameter(key: str, xsi_type: str, type_ns: str, value: str) -> str:
    """One ``Execute`` request parameter, rendered for :meth:`LegacyClient.execute_results`."""
    return (
        "<a:KeyValuePairOfstringanyType>"
        f"<b:key>{escape(key)}</b:key>"
        f'<b:value i:type="c:{xsi_type}" xmlns:c="{type_ns}">{escape(value)}</b:value>'
        "</a:KeyValuePairOfstringanyType>"
    )


def _refuse(what: str) -> D365Error:
    return D365Error(
        f"refused: {what} is not an allowlisted read; a CRM 2011 profile is read-only"
        " and `crm legacy` sends only allowlisted reads"
    )


def require_crm2011(profile: ConnectionProfile) -> None:
    """Refuse a profile `crm legacy` cannot serve: not crm2011, or not NTLM."""
    if profile.platform != PLATFORM_CRM2011:
        raise D365Error(
            f"Profile {profile.name!r} is not a CRM 2011 profile; "
            "`crm legacy` runs only on a crm2011 profile."
        )
    if profile.auth_scheme != "ntlm":
        raise D365Error(
            f"Profile {profile.name!r} uses {profile.auth_scheme!r}; `crm legacy` supports "
            "only HTTP NTLM."
        )


@dataclasses.dataclass(frozen=True)
class Page:
    """One RetrieveMultiple page: its records, and the cookie that continues it.

    *cookie* is None on the last page, and on a page that reports *more*
    records but carries no cookie, which cannot be continued.
    """

    entity: str
    number: int
    records: list[dict[str, object]]
    more: bool
    cookie: str | None


class LegacyClient:
    """Read-only SOAP client for one CRM 2011 org, authenticated with HTTP NTLM."""

    def __init__(self, profile: ConnectionProfile, password: str):
        require_crm2011(profile)
        import requests
        from requests_ntlm import HttpNtlmAuth

        self.profile = profile
        self.endpoint = profile.url + ENDPOINT_PATH
        principal = f"{profile.domain}\\{profile.username}" if profile.domain else profile.username
        self._session: requests.Session = requests.Session()
        self._session.auth = HttpNtlmAuth(principal, password)
        self._session.verify = profile.verify_ssl

    def execute(self, request_name: str) -> dict[str, str | None]:
        """Run an allowlisted parameterless ``Execute`` request; return its Results as text."""
        return {key: _result_text(node) for key, node in self.execute_results(request_name).items()}

    def execute_results(self, request_name: str, parameters: str = "") -> dict[str, ET.Element]:
        """Run an allowlisted ``Execute`` request; return each Result's value element.

        *parameters* is pre-rendered :func:`parameter` XML.
        """
        body = (
            f"<Execute {_SOAP_BODY_NS}>"
            '<request xmlns:a="http://schemas.microsoft.com/xrm/2011/Contracts">'
            '<a:Parameters xmlns:b="http://schemas.datacontract.org/2004/07/System.Collections.Generic">'
            f"{parameters}</a:Parameters>"
            '<a:RequestId i:nil="true"/>'
            f"<a:RequestName>{escape(request_name)}</a:RequestName>"
            "</request></Execute>"
        )
        root = self._send("Execute", body, request_name=request_name)
        results = root.find("s:Body/svc:ExecuteResponse/svc:ExecuteResult/a:Results", _NS)
        if results is None:
            raise D365Error(f"{request_name}: the SOAP response carries no ExecuteResult")
        pairs = results.findall("a:KeyValuePairOfstringanyType", _NS)
        return {
            pair.findtext("b:key", "", _NS): value
            for pair in pairs
            if (value := pair.find("b:value", _NS)) is not None
        }

    def retrieve_multiple(
        self, fetch_xml: str, *, page_size: int = 5000
    ) -> Iterator[list[dict[str, object]]]:
        """Page a FetchXML query to its last page, yielding each page's records.

        Follows the SOAP paging cookie. A page that reports more records but
        carries no cookie cannot be continued; it is yielded, then raises, so the
        rows already read stay with the caller.
        """
        for page in self.pages(fetch_xml, page_size=page_size):
            yield page.records
            if page.more and page.cookie is None:
                raise D365Error(
                    f"paging stopped early: page {page.number} of {page.entity} reports more"
                    " records but carries no paging cookie"
                )

    def pages(
        self, fetch_xml: str, *, page_size: int = 5000, start: Page | None = None
    ) -> Iterator[Page]:
        """Page a FetchXML query, yielding each `Page` with what continues it.

        With *start*, the last page an earlier run finished, paging resumes at
        the page after it from its cookie. Stops after the last page, or after
        a page that reports more records but carries no cookie (the caller
        reads that as truncation).
        """
        fetch = fromstring(fetch_xml)
        entity = fetch.find("entity")
        name = entity.get("name", "") if entity is not None else ""
        fetch.set("count", str(page_size))
        number = 1
        if start is not None:
            if not start.more or start.cookie is None:
                return
            number = start.number + 1
            fetch.set("paging-cookie", start.cookie)
        while True:
            fetch.set("page", str(number))
            query = escape(ET.tostring(fetch, encoding="unicode"))
            body = (
                f"<RetrieveMultiple {_SOAP_BODY_NS}>"
                '<query i:type="a:FetchExpression" xmlns:a="http://schemas.microsoft.com/xrm/2011/Contracts">'
                f"<a:Query>{query}</a:Query></query></RetrieveMultiple>"
            )
            root = self._send("RetrieveMultiple", body)
            result = root.find(
                "s:Body/svc:RetrieveMultipleResponse/svc:RetrieveMultipleResult", _NS
            )
            if result is None:
                raise D365Error("RetrieveMultiple: the SOAP response carries no result")
            more = result.findtext("a:MoreRecords", "", _NS) == "true"
            cookie = result.findtext("a:PagingCookie", "", _NS) or None
            page = Page(name, number, entity_records(result), more, cookie if more else None)
            yield page
            if page.more is False or page.cookie is None:
                return
            fetch.set("paging-cookie", page.cookie)
            number += 1

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
            except requests.exceptions.SSLError as exc:
                # A certificate failure (a ConnectionError subclass) fails the
                # same way every time; the measured handshake stall is a
                # ReadTimeout, retried below.
                raise D365Error(f"{_TRANSPORT_FAILURE_PREFIX}: {exc}") from exc
            except (requests.ConnectionError, requests.Timeout) as exc:
                # No HTTP response arrived (connect refused or timed out, or the
                # TLS handshake stalled, which requests reports as ReadTimeout).
                # Every allowlisted call is a read, so a re-send is safe.
                if attempt + 1 >= _CONNECT_ATTEMPTS:
                    raise D365Error(f"{_TRANSPORT_FAILURE_PREFIX}: {exc}") from exc
                time.sleep(_BACKOFF_SECONDS[attempt])
                attempt += 1
            except requests.RequestException as exc:
                # A request that cannot be sent at all (a malformed URL, say):
                # re-sending would fail the same way.
                raise D365Error(f"{_TRANSPORT_FAILURE_PREFIX}: {exc}") from exc
        return _parse(resp)


def detect_crm2011(
    profile: ConnectionProfile, password: str, web_api_error: D365Error
) -> ConnectionProfile | None:
    """Return *profile* as a crm2011 profile if the org is CRM 2011, else None.

    ``crm profile add`` calls this when the Web API test failed. Only a 404 on an
    NTLM profile qualifies; the org must then answer ``RetrieveVersion`` over
    /web with a 5.x build, which tells a CRM 2011 org apart from a wrong URL.
    """
    if web_api_error.status != 404 or profile.auth_scheme != "ntlm":
        return None
    candidate = dataclasses.replace(profile, platform=PLATFORM_CRM2011)
    try:
        version = LegacyClient(candidate, password).execute("RetrieveVersion").get("Version")
    except D365Error:
        return None
    return candidate if version and version.startswith("5.") else None


def _result_text(node: ET.Element | None) -> str | None:
    if node is None or node.get(f"{{{_NS['i']}}}nil") == "true":
        return None
    return node.text


def _value(node: ET.Element) -> object:
    """Convert one SOAP ``b:value`` by its ``i:type``; unknown types stay text."""
    if node.get(f"{{{_NS['i']}}}nil") == "true":
        return None
    kind = node.get(f"{{{_NS['i']}}}type", "").rpartition(":")[2]
    if kind == "boolean":
        return node.text == "true"
    if kind in ("int", "long"):
        return int(node.text or 0)
    if kind == "OptionSetValue":
        return int(node.findtext("a:Value", "0", _NS))
    if kind == "AliasedValue":
        # A link-entity column: the value sits one level down.
        inner = node.find("a:Value", _NS)
        return None if inner is None else _value(inner)
    if kind == "EntityReference":
        return {
            "id": node.findtext("a:Id", None, _NS),
            "logical_name": node.findtext("a:LogicalName", None, _NS),
            "name": node.findtext("a:Name", None, _NS),
        }
    return node.text


def entity_records(collection: ET.Element) -> list[dict[str, object]]:
    """The records of an ``EntityCollection`` (a RetrieveMultiple result or an Execute value)."""
    return [_record(e) for e in collection.findall("a:Entities/a:Entity", _NS)]


def _record(entity: ET.Element) -> dict[str, object]:
    record: dict[str, object] = {"id": entity.findtext("a:Id", None, _NS)}
    for pair in entity.findall("a:Attributes/a:KeyValuePairOfstringanyType", _NS):
        value = pair.find("b:value", _NS)
        if value is not None:
            record[pair.findtext("b:key", "", _NS)] = _value(value)
    formatted = {
        pair.findtext("b:key", "", _NS): pair.findtext("b:value", None, _NS)
        for pair in entity.findall("a:FormattedValues/a:KeyValuePairOfstringstring", _NS)
    }
    if formatted:
        record["formatted"] = formatted
    return record


def _parse(resp: requests.Response) -> ET.Element:
    """Parse a SOAP response, or raise the D365Error a fault or non-SOAP body maps to."""
    content_type = resp.headers.get("Content-Type", "")
    if "xml" not in content_type.lower():  # media types are case-insensitive
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
    # SOAP 1.1 sends every fault as HTTP 500, so the wire status says nothing
    # about whose fault it is; the faultcode does. A Client fault (the request
    # was at fault) is reported as 400 so it does not classify as a retryable
    # server error.
    faultcode = (fault.findtext("faultcode") or "").rpartition(":")[2]
    status = 400 if faultcode.partition(".")[0] == "Client" else resp.status_code
    org_fault = fault.find(".//a:OrganizationServiceFault", _NS)
    code: str | None = None
    message = fault.findtext("faultstring") or "SOAP fault"
    if org_fault is not None:
        raw = org_fault.findtext("a:ErrorCode", None, _NS)
        if raw:
            try:
                code = f"0x{int(raw) & 0xFFFFFFFF:08x}"
            except ValueError:
                pass  # an unreadable ErrorCode leaves the fault uncoded
        message = org_fault.findtext("a:Message", None, _NS) or message
    raise D365Error(message, status=status, code=code)
