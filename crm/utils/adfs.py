"""AD FS (IFD, claims-based) sign-in for on-prem orgs: WS-Trust to session cookies.

An IFD org fronted by AD FS answers NTLM on the Web API with a bare 500, but
accepts the passive WS-Federation sign-in a browser performs, driven here
without a browser (issue #978, measured on a live 9.1 org):

1. ``GET <org>/main.aspx`` unauthenticated: a 302 to ``<sts>/adfs/ls/?wa=wsignin1.0``
   whose query carries ``wctx``.
2. A WS-Trust 1.3 RST/Issue to ``<sts>/adfs/services/trust/13/usernamemixed``
   (Bearer key, ``AppliesTo`` the org realm, a WS-Security UsernameToken).
3. ``POST <org>/`` with ``wa``/``wresult``/``wctx``: a 302 setting the
   ``MSISAuth`` session cookies every later Web API call rides on.

This module is imported only when an ``adfs`` profile builds its backend, so it
may import ``requests`` at module level without slowing CLI startup (#247).
"""

from __future__ import annotations

import datetime as _dt
import re
import urllib.parse
import uuid
import xml.etree.ElementTree as ET
from collections.abc import Iterator
from typing import Any, cast
from xml.sax.saxutils import escape, quoteattr

import requests
from requests.auth import AuthBase

from crm.utils.d365_backend import D365Error
from crm.utils.safe_xml import fromstring

_TRUST_PATH = "/adfs/services/trust/13/usernamemixed"
_WS_TRUST = "http://docs.oasis-open.org/ws-sx/ws-trust/200512"
_RSTR = "RequestSecurityTokenResponse"
# A start tag named exactly RequestSecurityTokenResponse (not ...Collection),
# with or without a prefix.
_RSTR_START = re.compile(r"<((?:[A-Za-z_][\w.-]*:)?)" + _RSTR + r"(?=[\s/>])")

_RST_TEMPLATE = """\
<s:Envelope xmlns:s="http://www.w3.org/2003/05/soap-envelope" \
xmlns:a="http://www.w3.org/2005/08/addressing" \
xmlns:u="http://docs.oasis-open.org/wss/2004/01/oasis-200401-wss-wssecurity-utility-1.0.xsd">
<s:Header>
<a:Action s:mustUnderstand="1">{trust}/RST/Issue</a:Action>
<a:MessageID>urn:uuid:{message_id}</a:MessageID>
<a:ReplyTo><a:Address>http://www.w3.org/2005/08/addressing/anonymous</a:Address></a:ReplyTo>
<a:To s:mustUnderstand="1">{endpoint}</a:To>
<o:Security s:mustUnderstand="1" \
xmlns:o="http://docs.oasis-open.org/wss/2004/01/oasis-200401-wss-wssecurity-secext-1.0.xsd">
<u:Timestamp u:Id="_0"><u:Created>{created}</u:Created>\
<u:Expires>{expires}</u:Expires></u:Timestamp>
<o:UsernameToken u:Id="uuid-{token_id}"><o:Username>{username}</o:Username>\
<o:Password Type="http://docs.oasis-open.org/wss/2004/01/\
oasis-200401-wss-username-token-profile-1.0#PasswordText">{password}</o:Password></o:UsernameToken>
</o:Security>
</s:Header>
<s:Body>
<trust:RequestSecurityToken xmlns:trust="{trust}">
<wsp:AppliesTo xmlns:wsp="http://schemas.xmlsoap.org/ws/2004/09/policy">\
<a:EndpointReference><a:Address>{realm}</a:Address></a:EndpointReference></wsp:AppliesTo>
<trust:KeyType>{trust}/Bearer</trust:KeyType>
<trust:RequestType>{trust}/Issue</trust:RequestType>
</trust:RequestSecurityToken>
</s:Body>
</s:Envelope>"""


def build_rst(endpoint: str, realm: str, username: str, password: str) -> str:
    """The WS-Trust 1.3 RST/Issue envelope for a username/password Bearer token."""
    now = _dt.datetime.now(_dt.UTC)
    stamp = "%Y-%m-%dT%H:%M:%S.000Z"
    return _RST_TEMPLATE.format(
        trust=_WS_TRUST,
        message_id=uuid.uuid4(),
        token_id=uuid.uuid4(),
        endpoint=escape(endpoint),
        created=now.strftime(stamp),
        expires=(now + _dt.timedelta(minutes=5)).strftime(stamp),
        username=escape(username),
        password=escape(password),
        realm=escape(realm),
    )


def extract_wresult(response_xml: str) -> str:
    """Slice the RSTR out of an RSTR collection, keeping its namespaces bound.

    The org verifies the token only when ``wresult`` carries every namespace
    declaration the RSTR inherits from the collection and envelope (sliced
    without them it answers 401). The RSTR is cut from the raw text rather than
    re-serialized, because the token inside is encrypted for the org and must
    reach it byte for byte; the inherited declarations are added to its start tag.
    """
    fromstring(response_xml)  # well-formed, and no DTD/entities (defusedxml)
    match = _RSTR_START.search(response_xml)
    close_tag = None if match is None else re.compile(f"</{re.escape(match[1])}{_RSTR}\\s*>")
    close = (
        None if close_tag is None or match is None else close_tag.search(response_xml, match.end())
    )
    if match is None or close is None:
        raise D365Error("AD FS token response carried no RequestSecurityTokenResponse.", status=401)
    fragment = response_xml[match.start() : close.end()]
    decls = "".join(
        f" xmlns:{prefix}={quoteattr(uri)}" if prefix else f" xmlns={quoteattr(uri)}"
        for prefix, uri in _inherited_namespaces(response_xml).items()
    )
    return fragment[: match.end() - match.start()] + decls + fragment[match.end() - match.start() :]


def _inherited_namespaces(response_xml: str) -> dict[str, str]:
    """Namespaces in scope at the first RSTR that its own start tag does not declare.

    Safe with the stdlib parser: extract_wresult already refused any DTD.
    """
    parser: ET.XMLPullParser[ET.Element] = ET.XMLPullParser(events=("start-ns", "start", "end"))
    parser.feed(response_xml)
    frames: list[dict[str, str]] = []
    pending: dict[str, str] = {}
    events = cast(Iterator[tuple[str, Any]], parser.read_events())
    for event, item in events:
        if event == "start-ns":
            prefix, uri = cast(tuple[str, str], item)
            pending[prefix] = uri
        elif event == "start":
            if cast(ET.Element, item).tag.endswith("}" + _RSTR):
                scope: dict[str, str] = {}
                for frame in frames:
                    scope.update(frame)
                return {p: u for p, u in scope.items() if p not in pending}
            frames.append(pending)
            pending = {}
        else:
            frames.pop()
    return {}


def _fault_reason(body: bytes) -> str:
    """The SOAP fault's Reason text, or a fixed phrase when there is none."""
    try:
        root = fromstring(body)
    except ET.ParseError:
        return "no SOAP fault in the response"
    for el in root.iter():
        if el.tag.endswith("}Text") and el.text:
            return el.text.strip().rstrip(".")[:300]
    return "no fault reason given"


def is_sts_redirect(resp: requests.Response) -> bool:
    """Whether *resp* bounces the caller to a WS-Federation sign-in page."""
    query = urllib.parse.urlsplit(resp.headers.get("Location", "")).query
    return resp.is_redirect and urllib.parse.parse_qs(query).get("wa") == ["wsignin1.0"]


def discover(http: requests.Session, org_url: str, timeout: float) -> tuple[str, str]:
    """``(sts_origin, wctx)`` from the org's unauthenticated main.aspx redirect."""
    main = f"{org_url}/main.aspx"
    resp = http.get(main, allow_redirects=False, timeout=timeout)
    if not is_sts_redirect(resp):
        raise D365Error(
            f"AD FS discovery failed: {main} answered HTTP {resp.status_code} without "
            "a redirect to an AD FS sign-in page. Check the org URL, and that this org "
            "signs in through AD FS (IFD).",
            status=401,
        )
    location = urllib.parse.urljoin(main, resp.headers["Location"])
    parts = urllib.parse.urlsplit(location)
    wctx = urllib.parse.parse_qs(parts.query).get("wctx", [""])[0]
    if not wctx:
        raise D365Error(
            f"AD FS discovery failed: the sign-in redirect from {main} carried no wctx.",
            status=401,
        )
    return f"{parts.scheme}://{parts.netloc}", wctx


def sign_in(
    http: requests.Session,
    org_url: str,
    username: str,
    password: str,
    *,
    sts_url: str | None,
    timeout: float,
) -> None:
    """Run the three-step sign-in; the org's session cookies land in ``http.cookies``."""
    org = urllib.parse.urlsplit(org_url)
    root = f"{org.scheme}://{org.netloc}/"
    discovered, wctx = discover(http, org_url, timeout)
    endpoint = (sts_url or discovered).rstrip("/") + _TRUST_PATH
    if urllib.parse.urlsplit(endpoint).scheme != "https":
        raise D365Error(
            f"AD FS token request refused: {endpoint} is not https, and the request "
            "carries the password.",
            status=401,
        )
    resp = http.post(
        endpoint,
        data=build_rst(endpoint, root, username, password).encode("utf-8"),
        headers={"Content-Type": "application/soap+xml; charset=utf-8"},
        timeout=timeout,
    )
    if resp.status_code != 200:
        raise D365Error(
            f"AD FS token request to {endpoint} failed (HTTP {resp.status_code}): "
            f"{_fault_reason(resp.content)}. Check the username and password; AD FS "
            "servers differ in the username form they accept (DOMAIN\\user, UPN or the "
            "bare name), so try another form.",
            status=401,
        )
    try:
        wresult = extract_wresult(resp.text)
    except ET.ParseError as exc:
        raise D365Error(
            f"AD FS token request to {endpoint} returned a response that is not XML.",
            status=401,
        ) from exc
    resp = http.post(
        root,
        data={"wa": "wsignin1.0", "wresult": wresult, "wctx": wctx},
        allow_redirects=False,
        timeout=timeout,
    )
    if "MSISAuth" not in resp.cookies:
        raise D365Error(
            f"AD FS sign-in to {root} failed (HTTP {resp.status_code}): the org did not "
            "issue a session cookie for the AD FS token.",
            status=401,
        )
    http.cookies.update(resp.cookies)


class AdfsCookieAuth(AuthBase):
    """Keeps a backend session signed in to an AD FS org through MSISAuth cookies.

    Signs in before the first request. A later 401, or a redirect to the STS
    once the org's session has expired, signs in again and re-sends that
    request once; a second rejection is returned to the caller (auth_failed).
    """

    def __init__(
        self,
        session: requests.Session,
        profile_url: str,
        username: str,
        password: str,
        *,
        sts_url: str | None,
        verify: bool,
        timeout: float,
    ) -> None:
        self._session = session
        self._url = profile_url
        self._username = username
        self._password = password
        self._sts_url = sts_url
        self._verify = verify
        self._timeout = timeout
        self._signed_in = False

    def __call__(self, r: requests.PreparedRequest) -> requests.PreparedRequest:
        if not self._signed_in:
            self._sign_in()
            _reset_cookies(r, self._session.cookies)
        r.register_hook("response", self._on_response)
        return r

    def _sign_in(self) -> None:
        # A separate session: the sign-in's own requests must not carry this
        # auth, and the discovery GET must go out without stale cookies.
        with requests.Session() as http:
            http.verify = self._verify
            sign_in(
                http,
                self._url,
                self._username,
                self._password,
                sts_url=self._sts_url,
                timeout=self._timeout,
            )
            # The token is chunked over MSISAuth, MSISAuth1, ...; a re-sign-in can
            # issue fewer chunks, and a stale one left behind corrupts the token.
            jar = self._session.cookies
            for c in [c for c in jar if c.name.startswith("MSISAuth")]:
                jar.clear(c.domain, c.path, c.name)
            jar.update(http.cookies)
        self._signed_in = True

    def _on_response(self, resp: requests.Response, **kwargs: Any) -> requests.Response:
        if resp.status_code != 401 and not is_sts_redirect(resp):
            return resp
        self._sign_in()
        resp.close()
        prep = resp.request.copy()
        _reset_cookies(prep, self._session.cookies)
        # Sent through the adapter, which runs no hooks: this is the one retry.
        retry: requests.Response = resp.connection.send(prep, **kwargs)  # pyright: ignore[reportUnknownMemberType]
        retry.history.append(resp)
        retry.request = prep
        if is_sts_redirect(retry):
            raise D365Error(
                "AD FS session was rejected: the org redirected to sign-in again "
                "right after re-authenticating.",
                status=401,
            )
        return retry


def _reset_cookies(r: requests.PreparedRequest, jar: Any) -> None:
    r.headers.pop("Cookie", None)
    r.prepare_cookies(jar)


def sts_hint(org_url: str, verify: bool, timeout: float) -> str | None:
    """The STS origin when *org_url* signs in through AD FS, else ``None``.

    Best effort, for pointing a failed NTLM profile at ``--auth-scheme adfs``.
    """
    try:
        with requests.Session() as http:
            http.verify = verify
            return discover(http, org_url, timeout)[0]
    except (D365Error, requests.RequestException):
        return None
