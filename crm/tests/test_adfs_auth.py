"""AD FS (IFD) auth: WS-Trust usernamemixed sign-in to MSISAuth session cookies.

The STS and the org are mocked with response shapes recorded against a live
IFD org (issue #978): main.aspx 302 to the STS, an RSTR collection from the
usernamemixed endpoint, a 302 plus MSISAuth cookies from the org root.
"""

# pyright: basic
from __future__ import annotations

import json
import logging
import urllib.parse
from typing import Any

import pytest
import requests_mock
from click.testing import CliRunner

from crm.cli import cli
from crm.core import session as session_mod
from crm.utils import adfs
from crm.utils.d365_backend import ConnectionProfile, D365Backend, D365Error
from crm.utils.safe_xml import fromstring

ORG = "https://org.contoso.com"
STS = "https://sts.contoso.com"
TRUST = f"{STS}/adfs/services/trust/13/usernamemixed"
WHOAMI = f"{ORG}/api/data/v9.1/WhoAmI"
WCTX = "rm=0&id=passive&ru=%2fmain.aspx"
PASSWORD = "S3cr<et>&pw"
_WHOAMI_BODY = {
    "UserId": "00000000-0000-0000-0000-000000000001",
    "BusinessUnitId": "00000000-0000-0000-0000-0000000000bb",
    "OrganizationId": "00000000-0000-0000-0000-0000000000cc",
}

# The RSTR uses the `trust:` prefix declared on the collection and the `u:`
# prefix declared on the envelope; sliced out without them it is not XML.
RSTR = (
    "<trust:RequestSecurityTokenResponse>"
    "<trust:Lifetime><u:Created>2026-09-29T10:00:00.000Z</u:Created>"
    "<u:Expires>2026-09-29T10:05:00.000Z</u:Expires></trust:Lifetime>"
    '<wsp:AppliesTo xmlns:wsp="http://schemas.xmlsoap.org/ws/2004/09/policy">'
    "<a:EndpointReference><a:Address>https://org.contoso.com/</a:Address>"
    "</a:EndpointReference></wsp:AppliesTo>"
    "<trust:RequestedSecurityToken>"
    '<xenc:EncryptedData Type="http://www.w3.org/2001/04/xmlenc#Element" '
    'xmlns:xenc="http://www.w3.org/2001/04/xmlenc#"><xenc:CipherData>'
    "<xenc:CipherValue>T1BBUVVFK1RPS0VO/w==</xenc:CipherValue>"
    "</xenc:CipherData></xenc:EncryptedData></trust:RequestedSecurityToken>"
    "<trust:TokenType>urn:oasis:names:tc:SAML:1.0:assertion</trust:TokenType>"
    "<trust:RequestType>http://docs.oasis-open.org/ws-sx/ws-trust/200512/Issue</trust:RequestType>"
    "<trust:KeyType>http://docs.oasis-open.org/ws-sx/ws-trust/200512/Bearer</trust:KeyType>"
    "</trust:RequestSecurityTokenResponse>"
)
RSTRC = (
    '<s:Envelope xmlns:s="http://www.w3.org/2003/05/soap-envelope" '
    'xmlns:a="http://www.w3.org/2005/08/addressing" '
    'xmlns:u="http://docs.oasis-open.org/wss/2004/01/oasis-200401-wss-wssecurity-utility-1.0.xsd">'
    "<s:Header>"
    '<a:Action s:mustUnderstand="1">'
    "http://docs.oasis-open.org/ws-sx/ws-trust/200512/RSTRC/IssueFinal</a:Action>"
    '<o:Security s:mustUnderstand="1" xmlns:o="http://docs.oasis-open.org/wss/2004/01/'
    'oasis-200401-wss-wssecurity-secext-1.0.xsd"><u:Timestamp u:Id="_0">'
    "<u:Created>2026-09-29T10:00:00.000Z</u:Created></u:Timestamp></o:Security>"
    "</s:Header><s:Body>"
    '<trust:RequestSecurityTokenResponseCollection xmlns:trust="http://docs.oasis-open.org/ws-sx/ws-trust/200512">'
    f"{RSTR}"
    "</trust:RequestSecurityTokenResponseCollection></s:Body></s:Envelope>"
)
FAULT = (
    '<s:Envelope xmlns:s="http://www.w3.org/2003/05/soap-envelope">'
    "<s:Body><s:Fault><s:Code><s:Value>s:Sender</s:Value></s:Code>"
    '<s:Reason><s:Text xml:lang="en-US">ID3242: The security token could not be '
    "authenticated or authorized.</s:Text></s:Reason></s:Fault></s:Body></s:Envelope>"
)


def _profile(**kw) -> ConnectionProfile:
    fields: dict[str, Any] = {
        "name": "ifd",
        "url": ORG,
        "domain": "",
        "username": "alice",
        "api_version": "v9.1",
        "auth_scheme": "adfs",
    }
    fields.update(kw)
    return ConnectionProfile(**fields)


def _discovery_location(sts: str = STS) -> str:
    query = urllib.parse.urlencode(
        {"wa": "wsignin1.0", "wtrealm": f"{ORG}/", "wctx": WCTX, "wct": "2026-09-29T10:00:00Z"}
    )
    return f"{sts}/adfs/ls/?{query}"


def _mock_sign_in(m: requests_mock.Mocker, *, sts: str = STS) -> None:
    m.get(f"{ORG}/main.aspx", status_code=302, headers={"Location": _discovery_location(sts)})
    m.post(f"{sts}/adfs/services/trust/13/usernamemixed", text=RSTRC)
    m.post(
        f"{ORG}/",
        status_code=302,
        headers={"Location": "/main.aspx"},
        cookies={"MSISAuth": "cookie-a", "MSISAuth1": "cookie-b"},
    )


def _user_id(b: D365Backend) -> str:
    result = b.get("WhoAmI")
    assert isinstance(result, dict)
    return result["UserId"]


def _calls(m: requests_mock.Mocker, method: str, url: str) -> list:
    return [r for r in m.request_history if r.method == method and r.url == url]


class TestWresult:
    def test_rstr_is_sliced_with_the_inherited_namespaces(self):
        wresult = adfs.extract_wresult(RSTRC)
        root = fromstring(wresult)  # well-formed on its own: prefixes are bound
        assert (
            root.tag
            == "{http://docs.oasis-open.org/ws-sx/ws-trust/200512}RequestSecurityTokenResponse"
        )
        assert 'xmlns:trust="http://docs.oasis-open.org/ws-sx/ws-trust/200512"' in wresult
        assert (
            'xmlns:u="http://docs.oasis-open.org/wss/2004/01/oasis-200401-wss-wssecurity-utility-1.0.xsd"'
            in wresult
        )
        # The encrypted token rides through byte for byte.
        assert wresult.endswith(RSTR[len("<trust:RequestSecurityTokenResponse>") :])
        assert "RequestSecurityTokenResponseCollection" not in wresult

    def test_response_without_an_rstr_is_an_error(self):
        with pytest.raises(D365Error, match="no RequestSecurityTokenResponse"):
            adfs.extract_wresult(FAULT)


class TestSignIn:
    def test_discovers_sts_and_establishes_cookie_session(self):
        b = D365Backend(_profile(), password=PASSWORD)
        with requests_mock.Mocker() as m:
            _mock_sign_in(m)
            m.get(WHOAMI, json=_WHOAMI_BODY)
            assert _user_id(b) == _WHOAMI_BODY["UserId"]

        (discovery,) = _calls(m, "GET", f"{ORG}/main.aspx")
        assert "Cookie" not in discovery.headers
        (sign_in,) = _calls(m, "POST", f"{ORG}/")
        form = urllib.parse.parse_qs(sign_in.text)
        assert form["wa"] == ["wsignin1.0"]
        assert form["wctx"] == [WCTX]
        assert form["wresult"] == [adfs.extract_wresult(RSTRC)]
        (whoami,) = _calls(m, "GET", WHOAMI)
        assert "MSISAuth=cookie-a" in whoami.headers["Cookie"]
        assert "MSISAuth1=cookie-b" in whoami.headers["Cookie"]

    def test_rst_carries_username_realm_and_escaped_password(self):
        b = D365Backend(_profile(), password=PASSWORD)
        with requests_mock.Mocker() as m:
            _mock_sign_in(m)
            m.get(WHOAMI, json=_WHOAMI_BODY)
            b.get("WhoAmI")
        (rst,) = _calls(m, "POST", TRUST)
        assert rst.headers["Content-Type"].startswith("application/soap+xml")
        root = fromstring(rst.body)
        texts = {el.tag.rsplit("}", 1)[-1]: el.text for el in root.iter()}
        assert texts["Username"] == "alice"
        assert texts["Password"] == PASSWORD  # escaped on the wire, intact once parsed
        assert texts["Address"] == f"{ORG}/"
        assert texts["KeyType"] == "http://docs.oasis-open.org/ws-sx/ws-trust/200512/Bearer"
        assert texts["Action"] == "http://docs.oasis-open.org/ws-sx/ws-trust/200512/RST/Issue"
        assert texts["To"] == TRUST

    def test_domain_kept_by_a_scheme_override_joins_the_username(self):
        # `crm --auth-scheme adfs` on an ntlm profile keeps its separate domain.
        b = D365Backend(_profile(domain="CONTOSO"), password=PASSWORD)
        with requests_mock.Mocker() as m:
            _mock_sign_in(m)
            m.get(WHOAMI, json=_WHOAMI_BODY)
            b.get("WhoAmI")
        (rst,) = _calls(m, "POST", TRUST)
        texts = {el.tag.rsplit("}", 1)[-1]: el.text for el in fromstring(rst.body).iter()}
        assert texts["Username"] == "CONTOSO\\alice"

    def test_adfs_url_overrides_the_discovered_sts(self):
        other = "https://adfs.internal.contoso.com"
        b = D365Backend(_profile(adfs_url=other + "/"), password=PASSWORD)
        with requests_mock.Mocker() as m:
            _mock_sign_in(m)
            m.post(f"{other}/adfs/services/trust/13/usernamemixed", text=RSTRC)
            m.get(WHOAMI, json=_WHOAMI_BODY)
            b.get("WhoAmI")
        assert _calls(m, "POST", f"{other}/adfs/services/trust/13/usernamemixed")
        assert not _calls(m, "POST", TRUST)

    def test_org_without_sts_redirect_names_the_discovery_step(self):
        b = D365Backend(_profile(), password=PASSWORD)
        with requests_mock.Mocker() as m:
            m.get(f"{ORG}/main.aspx", status_code=200, text="<html/>")
            with pytest.raises(D365Error, match="AD FS discovery") as ei:
                b.get("WhoAmI")
        assert ei.value.status == 401

    def test_sts_fault_names_the_token_step_without_xml_or_password(self, caplog):
        caplog.set_level(logging.DEBUG)
        b = D365Backend(_profile(), password=PASSWORD)
        with requests_mock.Mocker() as m:
            _mock_sign_in(m)
            m.post(TRUST, status_code=500, text=FAULT)
            with pytest.raises(D365Error) as ei:
                b.get("WhoAmI")
        msg = str(ei.value)
        assert ei.value.status == 401
        assert "AD FS token request" in msg and TRUST in msg
        assert "ID3242" in msg and "authorized.." not in msg
        assert "<s:" not in msg
        assert PASSWORD not in msg
        assert PASSWORD not in caplog.text

    def test_org_refusing_the_token_names_the_sign_in_step(self):
        b = D365Backend(_profile(), password=PASSWORD)
        with requests_mock.Mocker() as m:
            _mock_sign_in(m)
            m.post(f"{ORG}/", status_code=401)
            with pytest.raises(D365Error, match="session cookie") as ei:
                b.get("WhoAmI")
        assert ei.value.status == 401

    def test_missing_password_is_an_error(self):
        with pytest.raises(D365Error, match="password"):
            D365Backend(_profile(), password="")


class TestReauth:
    def test_401_signs_in_again_once_and_retries(self):
        b = D365Backend(_profile(), password=PASSWORD)
        with requests_mock.Mocker() as m:
            _mock_sign_in(m)
            m.get(WHOAMI, [{"status_code": 401}, {"json": _WHOAMI_BODY}])
            assert _user_id(b) == _WHOAMI_BODY["UserId"]
        assert len(_calls(m, "POST", TRUST)) == 2
        assert len(_calls(m, "GET", WHOAMI)) == 2

    def test_redirect_to_sts_signs_in_again_once_and_retries(self):
        b = D365Backend(_profile(), password=PASSWORD)
        with requests_mock.Mocker() as m:
            _mock_sign_in(m)
            m.get(
                WHOAMI,
                [
                    {"status_code": 302, "headers": {"Location": _discovery_location()}},
                    {"json": _WHOAMI_BODY},
                ],
            )
            assert _user_id(b) == _WHOAMI_BODY["UserId"]
        assert len(_calls(m, "POST", TRUST)) == 2
        assert not _calls(m, "GET", _discovery_location())  # the STS page is never followed

    def test_second_401_surfaces_auth_failed(self):
        b = D365Backend(_profile(), password=PASSWORD)
        with requests_mock.Mocker() as m:
            _mock_sign_in(m)
            m.get(WHOAMI, status_code=401)
            with pytest.raises(D365Error) as ei:
                b.get("WhoAmI")
        assert ei.value.status == 401
        assert len(_calls(m, "POST", TRUST)) == 2
        assert len(_calls(m, "GET", WHOAMI)) == 2


class TestProfile:
    def test_adfs_scheme_and_url_round_trip(self):
        p = _profile(adfs_url="https://sts.contoso.com")
        again = ConnectionProfile.from_dict(json.loads(json.dumps(p.to_dict())))
        assert again.auth_scheme == "adfs"
        assert again.adfs_url == "https://sts.contoso.com"

    def test_profile_json_without_adfs_url_still_loads(self):
        p = ConnectionProfile.from_dict(
            {"name": "old", "url": ORG, "domain": "D", "username": "u", "auth_scheme": "ntlm"}
        )
        assert p.adfs_url is None


@pytest.fixture
def crm_home(tmp_path, monkeypatch):
    monkeypatch.setenv("CRM_HOME", str(tmp_path / ".crm"))
    import crm.core.keyring_store as ks

    monkeypatch.setattr(ks, "is_available", lambda: False)
    return tmp_path


def _add(*extra: str) -> list[str]:
    return [
        "--json",
        "profile",
        "add",
        "--url",
        ORG,
        "--username",
        "alice",
        "--password",
        PASSWORD,
        "--name",
        "ifd",
        "--api-version",
        "v9.1",
        "--yes",
        *extra,
    ]


class TestProfileAdd:
    def test_add_adfs_tests_whoami_then_saves(self, crm_home):
        with requests_mock.Mocker() as m:
            _mock_sign_in(m)
            m.get(WHOAMI, json=_WHOAMI_BODY)
            result = CliRunner().invoke(cli, _add("--auth-scheme", "adfs", "--adfs-url", STS))
        assert result.exit_code == 0, result.output
        assert json.loads(result.output)["data"]["auth_scheme"] == "adfs"
        p = session_mod.load_profile("ifd")
        assert p.auth_scheme == "adfs" and p.adfs_url == STS
        assert session_mod.load_profile_secret("ifd") == PASSWORD
        assert _calls(m, "GET", WHOAMI)

    def test_add_adfs_wrong_password_never_echoes_it(self, crm_home):
        with requests_mock.Mocker() as m:
            _mock_sign_in(m)
            m.post(TRUST, status_code=500, text=FAULT)
            result = CliRunner().invoke(cli, _add("--auth-scheme", "adfs"))
        assert result.exit_code != 0
        assert "AD FS token request" in result.output
        assert PASSWORD not in result.output
        assert "ifd" not in session_mod.list_profiles()

    def test_ntlm_failure_on_an_ifd_org_points_at_adfs(self, crm_home):
        with requests_mock.Mocker() as m:
            m.get(WHOAMI, status_code=500, json={"_error": "An error has occurred"})
            m.get(f"{ORG}/main.aspx", status_code=302, headers={"Location": _discovery_location()})
            result = CliRunner().invoke(cli, _add("--auth-scheme", "ntlm"))
        assert result.exit_code != 0
        assert "--auth-scheme adfs" in result.output

    def test_wizard_offers_adfs(self, crm_home, monkeypatch):
        monkeypatch.setattr("crm.commands.profile._stdin_is_tty", lambda: True)
        seen = {}

        def _fake_select(title, items, default=None):
            seen["values"] = [v for v, _ in items]
            return "adfs"

        monkeypatch.setattr("crm.commands.profile.select_one", _fake_select)
        with requests_mock.Mocker() as m:
            _mock_sign_in(m)
            m.get(WHOAMI, json=_WHOAMI_BODY)
            args = [a for a in _add() if a != "--json"]
            # blank AD FS URL (discover), blank publisher prefix, read-only N
            result = CliRunner().invoke(cli, args, input="\n\n\n")
        assert result.exit_code == 0, result.output
        assert "adfs" in seen["values"]
        p = session_mod.load_profile("ifd")
        assert p.auth_scheme == "adfs" and p.adfs_url is None


class TestProfileEdit:
    def test_edit_sets_adfs_url(self, crm_home):
        session_mod.save_profile(_profile())
        result = CliRunner().invoke(cli, ["--json", "profile", "edit", "ifd", "--adfs-url", STS])
        assert result.exit_code == 0, result.output
        assert session_mod.load_profile("ifd").adfs_url == STS


class TestReviewHardening:
    def test_default_namespace_rstr_keeps_its_namespace(self):
        doc = (
            '<s:Envelope xmlns:s="http://www.w3.org/2003/05/soap-envelope"><s:Body>'
            '<RequestSecurityTokenResponseCollection xmlns="http://docs.oasis-open.org/ws-sx/ws-trust/200512">'
            "<RequestSecurityTokenResponse><TokenType>t</TokenType></RequestSecurityTokenResponse >"
            "</RequestSecurityTokenResponseCollection></s:Body></s:Envelope>"
        )
        wresult = adfs.extract_wresult(doc)
        start_tag = wresult.split(">", 1)[0]
        assert ' xmlns="http://docs.oasis-open.org/ws-sx/ws-trust/200512"' in start_tag
        assert wresult.endswith("</RequestSecurityTokenResponse >")
        assert fromstring(wresult).tag.endswith("}RequestSecurityTokenResponse")

    def test_non_https_sts_never_receives_the_password(self):
        b = D365Backend(_profile(adfs_url="http://sts.contoso.com"), password=PASSWORD)
        with requests_mock.Mocker() as m:
            _mock_sign_in(m)
            with pytest.raises(D365Error, match="https") as ei:
                b.get("WhoAmI")
        assert ei.value.status == 401
        assert not [r for r in m.request_history if r.method == "POST"]

    def test_wsignin_in_another_parameter_is_not_an_sts_redirect(self):
        b = D365Backend(_profile(), password=PASSWORD)
        with requests_mock.Mocker() as m:
            m.get(
                f"{ORG}/main.aspx",
                status_code=302,
                headers={"Location": f"{STS}/adfs/ls/?wa=wsignout1.0&ru={ORG}/?wa=wsignin1.0"},
            )
            with pytest.raises(D365Error, match="without a redirect"):
                b.get("WhoAmI")

    def test_redirect_after_reauth_is_an_auth_error(self):
        b = D365Backend(_profile(), password=PASSWORD)
        with requests_mock.Mocker() as m:
            _mock_sign_in(m)
            m.get(WHOAMI, status_code=302, headers={"Location": _discovery_location()})
            with pytest.raises(D365Error, match="rejected") as ei:
                b.get("WhoAmI")
        assert ei.value.status == 401
        assert len(_calls(m, "POST", TRUST)) == 2

    def test_token_fault_hint_names_every_username_form(self):
        b = D365Backend(_profile(), password=PASSWORD)
        with requests_mock.Mocker() as m:
            _mock_sign_in(m)
            m.post(TRUST, status_code=500, text=FAULT)
            with pytest.raises(D365Error) as ei:
                b.get("WhoAmI")
        assert "DOMAIN\\user, UPN or the bare name" in str(ei.value)


class TestReviewCli:
    def test_add_adfs_url_with_another_scheme_is_a_usage_error(self, crm_home):
        result = CliRunner().invoke(cli, _add("--auth-scheme", "ntlm", "--adfs-url", STS))
        assert result.exit_code == 2
        assert "--adfs-url" in result.output

    def test_add_domain_with_adfs_is_a_usage_error(self, crm_home):
        with requests_mock.Mocker():
            result = CliRunner().invoke(cli, _add("--auth-scheme", "adfs", "--domain", "CONTOSO"))
        assert result.exit_code == 2
        assert "--domain" in result.output

    def test_edit_blank_adfs_url_restores_discovery(self, crm_home):
        session_mod.save_profile(_profile(adfs_url=STS))
        result = CliRunner().invoke(cli, ["--json", "profile", "edit", "ifd", "--adfs-url", ""])
        assert result.exit_code == 0, result.output
        assert session_mod.load_profile("ifd").adfs_url is None

    def test_edit_adfs_url_on_another_scheme_is_a_usage_error(self, crm_home):
        session_mod.save_profile(_profile(auth_scheme="ntlm"))
        result = CliRunner().invoke(cli, ["--json", "profile", "edit", "ifd", "--adfs-url", STS])
        assert result.exit_code == 2
        assert session_mod.load_profile("ifd").adfs_url is None

    def test_ntlm_transport_failure_skips_the_adfs_probe(self, crm_home, monkeypatch):
        import requests

        monkeypatch.setattr("crm.utils.d365_backend.time.sleep", lambda _s: None)

        with requests_mock.Mocker() as m:
            m.get(WHOAMI, exc=requests.exceptions.ConnectTimeout)
            m.get(f"{ORG}/main.aspx", status_code=302, headers={"Location": _discovery_location()})
            result = CliRunner().invoke(cli, _add("--auth-scheme", "ntlm"))
        assert result.exit_code != 0
        assert not _calls(m, "GET", f"{ORG}/main.aspx")

    def test_doctor_hint_on_adfs_sign_in_failure(self):
        from crm.core.connection import connection_doctor

        b = D365Backend(_profile(), password=PASSWORD)
        with requests_mock.Mocker() as m:
            _mock_sign_in(m)
            m.post(TRUST, status_code=500, text=FAULT)
            import socket
            from unittest import mock

            with mock.patch.object(socket, "create_connection"):
                report = connection_doctor(b)
        tls = next(c for c in report["checks"] if c["check"] == "tls")
        assert not tls["ok"]
        assert "AD FS" in tls["hint"] and "OAuth" not in tls["hint"]
