"""Unit tests for `crm org brief` — the one-call agent-first org inventory (#790).

Mirrors the mocked-backend pattern of the write-readiness brief
(`test_metadata_describe.py`): a real ``D365Backend`` driven by ``requests_mock``
so the exact GET round-trips are asserted. The brief's whole point is a *bounded*
request count independent of org size, so ``test_request_count_is_bounded`` pins
that budget — and because ``requests_mock`` raises ``NoMockAddress`` for any
endpoint a test does not register, the brief provably issues no write and no
stray fat-list read.
"""
# pyright: basic

from __future__ import annotations

import json
import re

import requests_mock
from click.testing import CliRunner

from crm.cli import CLIContext, cli


# ── Canned org: small but non-empty in every section ────────────────────────
_WHOAMI = {
    "UserId": "11111111-1111-1111-1111-111111111111",
    "BusinessUnitId": "22222222-2222-2222-2222-222222222222",
    "OrganizationId": "33333333-3333-3333-3333-333333333333",
}
_ORG = {"name": "Contoso"}
_VERSION = {"Version": "9.2.24091.00196"}

_SOLUTIONS = {"value": [
    {"uniquename": "Default", "friendlyname": "Default Solution", "ismanaged": False},
    {"uniquename": "Active", "friendlyname": "Active", "ismanaged": False},
    {"uniquename": "msdynce_Sales", "friendlyname": "Sales", "ismanaged": True},
    {"uniquename": "AcmeCore", "friendlyname": "Acme Core", "ismanaged": False},
    {"uniquename": "AcmeExt", "friendlyname": "Acme Ext", "ismanaged": False},
]}
_PUBLISHERS = {"value": [
    {"uniquename": "acme", "friendlyname": "Acme", "customizationprefix": "acme"},
    {"uniquename": "new", "friendlyname": "Default Publisher", "customizationprefix": "new"},
]}
_CUSTOM_ENTITIES = {"value": [
    {"LogicalName": "acme_widget", "EntitySetName": "acme_widgets"},
    {"LogicalName": "acme_gadget", "EntitySetName": "acme_gadgets"},
]}
_OPTIONSETS = {"value": [
    {"Name": "acme_color", "IsCustomOptionSet": True},
    {"Name": "acme_size", "IsCustomOptionSet": True},
    {"Name": "statuscode_shared", "IsCustomOptionSet": False},
]}
_APPS = {"@odata.count": 2, "value": [
    {"name": "Sales Hub", "uniquename": "acme_saleshub"},
    {"name": "Acme Ops", "uniquename": "acme_ops"},
]}
# workflows: type=1 definitions across categories/states.
_WORKFLOWS = {"value": [
    {"category": 0, "statecode": 1},   # workflow, activated
    {"category": 0, "statecode": 0},   # workflow, draft
    {"category": 2, "statecode": 1},   # business_rule, activated
    {"category": 4, "statecode": 1},   # bpf, activated
    {"category": 5, "statecode": 0},   # modern_flow, draft
]}

# webresourceset is hit once for the total and once per notable type; roles and
# the plain counts each return an @odata.count envelope.
_WEBRESOURCE_COUNTS = {None: 40, "1": 5, "2": 3, "3": 20}  # total, html, css, script


def _count_env(n: int) -> dict:
    return {"@odata.count": n, "value": []}


def _register(m: requests_mock.Mocker, backend) -> None:
    def u(path: str) -> str:
        return backend.url_for(path)

    m.get(u("WhoAmI"), json=_WHOAMI)
    m.get(u(f"organizations({_WHOAMI['OrganizationId']})"), json=_ORG)
    m.get(u("RetrieveVersion()"), json=_VERSION)
    m.get(u("solutions"), json=_SOLUTIONS)
    m.get(u("publishers"), json={**_PUBLISHERS, "@odata.count": 2})
    m.get(u("EntityDefinitions"), json=_CUSTOM_ENTITIES)
    m.get(u("GlobalOptionSetDefinitions"), json=_OPTIONSETS)
    m.get(u("appmodules"), json=_APPS)
    m.get(u("pluginassemblies"), json=_count_env(4))
    m.get(u("sdkmessageprocessingsteps"), json=_count_env(12))
    m.get(u("workflows"), json=_WORKFLOWS)
    m.get(u("slas"), json=_count_env(1))
    m.get(u("duplicaterules"), json=_count_env(7))
    m.get(u("roles"), json=_count_env(6))

    # webresourceset: dispatch on the $filter querystring so each type-count and
    # the unfiltered total return distinct values from a single matcher.
    def _webresource(request, context):
        context.status_code = 200
        flt = request.qs.get("$filter", [None])[0]
        wtype = None
        if flt:
            mo = re.search(r"webresourcetype eq (\d+)", flt)
            wtype = mo.group(1) if mo else None
        return _count_env(_WEBRESOURCE_COUNTS[wtype])

    m.get(u("webresourceset"), json=_webresource)


class TestComposer:
    def test_brief_has_all_sections_with_expected_counts(self, backend):
        from crm.core import org

        with requests_mock.Mocker() as m:
            _register(m, backend)
            brief = org.org_brief(backend)

        ident = brief["identity"]
        assert ident["org_name"] == "Contoso"
        assert ident["version"] == "9.2.24091.00196"
        assert ident["organization_id"] == _WHOAMI["OrganizationId"]

        sol = brief["solutions"]
        assert sol["managed"] == 1
        assert sol["unmanaged"] == 4  # Default, Active, AcmeCore, AcmeExt
        # Default/Active are excluded from the candidate --solution target list.
        assert set(sol["unmanaged_names"]) == {"AcmeCore", "AcmeExt"}

        assert {p["prefix"] for p in brief["publishers"]["items"]} == {"acme", "new"}

        schema = brief["schema"]
        assert schema["custom_entities"] == 2
        assert set(schema["custom_entity_names"]) == {"acme_widget", "acme_gadget"}
        assert schema["global_optionsets"] == 3

        assert brief["apps"]["count"] == 2
        assert set(brief["apps"]["names"]) == {"Sales Hub", "Acme Ops"}

        auto = brief["automation"]
        assert auto["plugin_assemblies"] == 4
        assert auto["plugin_steps"] == 12
        assert auto["slas"] == 1
        by_cat = auto["workflows"]["by_category"]
        assert by_cat["workflow"] == {"total": 2, "activated": 1}
        assert by_cat["business_rule"] == {"total": 1, "activated": 1}
        assert by_cat["bpf"] == {"total": 1, "activated": 1}
        assert by_cat["modern_flow"] == {"total": 1, "activated": 0}

        comp = brief["components"]
        assert comp["webresources"]["total"] == 40
        assert comp["webresources"]["by_type"] == {"html": 5, "css": 3, "script": 20}
        assert comp["security_roles_custom"] == 6
        assert comp["duplicate_rules"] == 7

    def test_request_count_is_bounded(self, backend):
        """The bounded request budget IS the feature. Pin it so a regression that
        reintroduces a fat per-row sweep (making the count scale with org size)
        fails loudly. requests_mock counts every HTTP call the composer makes."""
        from crm.core import org

        with requests_mock.Mocker() as m:
            _register(m, backend)
            org.org_brief(backend)
            assert m.call_count == org.EXPECTED_REQUESTS

    def test_brief_issues_only_gets(self, backend):
        """Read-only: every recorded request is a GET (no write verb)."""
        from crm.core import org

        with requests_mock.Mocker() as m:
            _register(m, backend)
            org.org_brief(backend)
            assert {r.method for r in m.request_history} == {"GET"}


class TestCli:
    def _stub(self, monkeypatch, backend):
        # Mirror the real `backend()` side effects so emit's self-identifying
        # meta (#624) fires: it is gated on connection_resolved + _backend.
        def _fake(self: CLIContext):
            self._backend = backend
            self.connection_resolved = True
            return backend

        monkeypatch.setattr(CLIContext, "backend", _fake)

    def test_json_envelope(self, monkeypatch, backend):
        self._stub(monkeypatch, backend)
        with requests_mock.Mocker() as m:
            _register(m, backend)
            r = CliRunner().invoke(cli, ["--json", "org", "brief"])
        assert r.exit_code == 0, r.output
        env = json.loads(r.output)
        assert env["ok"]
        data = env["data"]
        for section in ("identity", "solutions", "publishers", "schema", "apps",
                        "automation", "components"):
            assert section in data
        # Self-identifying envelope (#624): meta carries profile + url.
        assert env["meta"]["profile"] == backend.profile.name

    def test_human_mode_renders(self, monkeypatch, backend):
        self._stub(monkeypatch, backend)
        with requests_mock.Mocker() as m:
            _register(m, backend)
            r = CliRunner().invoke(cli, ["org", "brief"])
        assert r.exit_code == 0, r.output
        assert "Contoso" in r.output

    def test_help_lists_org_group(self):
        r = CliRunner().invoke(cli, ["org", "--help"])
        assert r.exit_code == 0
        assert "brief" in r.output
