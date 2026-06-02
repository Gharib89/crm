"""Unit tests for crm.core.appmodule.

The AddAppComponents payload shape and the appmodule `webresourceid` requirement
were both verified live against D365 CE on-prem 9.1 (walkthrough §11): the action
takes typed entity references, and `appmodules` rejects a null `webresourceid`.
"""
# pyright: basic
from __future__ import annotations

import pytest
import requests_mock

from crm.utils.d365_backend import ConnectionProfile, D365Backend, D365Error


@pytest.fixture
def profile() -> ConnectionProfile:
    return ConnectionProfile(
        name="testp", url="https://crm.contoso.local/contoso",
        domain="CONTOSO", username="alice", api_version="v9.2", verify_ssl=False,
    )


@pytest.fixture
def backend(profile):
    return D365Backend(profile, password="pw", dry_run=False)


_APP_ID = "77777777-7777-7777-7777-777777777777"
_DEFAULT_ICON = "953b9fac-1e5e-e611-80d6-00155ded156f"


def _posts(m):
    return [r for r in m.request_history if r.method == "POST"]


class TestCreateApp:
    def test_create_app_posts_appmodule_and_reads_back(self, backend):
        from crm.core import appmodule
        with requests_mock.Mocker() as m:
            m.get(backend.url_for("appmodules"), json={"value": []})  # guard
            app_url = backend.url_for(f"appmodules({_APP_ID})")
            m.post(backend.url_for("appmodules"), status_code=204,
                   headers={"OData-EntityId": app_url})
            m.get(app_url, json={"appmoduleid": _APP_ID, "name": "CRMWorx",
                                 "uniquename": "cwx_crmworx"})
            out = appmodule.create_app(
                backend, name="CRMWorx", unique_name="cwx_crmworx",
                description="IT ticketing",
            )
        assert out["created"] is True
        assert out["appmoduleid"] == _APP_ID
        body = _posts(m)[0].json()
        assert body["uniquename"] == "cwx_crmworx"
        assert body["name"] == "CRMWorx"
        # appmodule create on 9.1 rejects a null webresourceid → the default icon is set
        assert body["webresourceid"] == _DEFAULT_ICON

    def test_create_app_skips_when_exists(self, backend):
        from crm.core import appmodule
        with requests_mock.Mocker() as m:
            m.get(backend.url_for("appmodules"),
                  json={"value": [{"appmoduleid": _APP_ID, "uniquename": "cwx_crmworx"}]})
            out = appmodule.create_app(backend, name="CRMWorx",
                                       unique_name="cwx_crmworx", if_exists="skip")
        assert out["skipped"] is True
        assert not _posts(m)


class TestAddComponents:
    def test_add_components_builds_typed_references(self, backend):
        from crm.core import appmodule
        with requests_mock.Mocker() as m:
            m.post(backend.url_for("AddAppComponents"), status_code=204)
            out = appmodule.add_app_components(
                backend, app_id=_APP_ID,
                components=[("view", "bbbb"), ("chart", "cccc"), ("sitemap", "dddd")],
            )
        assert out["added"] == 3
        body = _posts(m)[0].json()
        assert body["AppId"] == _APP_ID
        # 9.1 wants typed entity references, not {Type, Id}
        types = {c["@odata.type"] for c in body["Components"]}
        assert types == {
            "Microsoft.Dynamics.CRM.savedquery",
            "Microsoft.Dynamics.CRM.savedqueryvisualization",
            "Microsoft.Dynamics.CRM.sitemap",
        }
        view = next(c for c in body["Components"]
                    if c["@odata.type"].endswith(".savedquery"))
        assert view["savedqueryid"] == "bbbb"

    def test_add_components_rejects_unknown_kind(self, backend):
        from crm.core import appmodule
        with pytest.raises(D365Error, match="unknown component kind"):
            appmodule.add_app_components(backend, app_id=_APP_ID,
                                         components=[("widget", "xxxx")])


class TestAppCommands:
    def test_app_create_command_wires_core(self, monkeypatch):
        from click.testing import CliRunner
        from crm.cli import cli
        captured = {}

        def fake_create_app(backend, **kw):
            captured.update(kw)
            return {"created": True, "appmoduleid": _APP_ID, "uniquename": kw["unique_name"]}

        monkeypatch.setattr("crm.core.appmodule.create_app", fake_create_app)
        monkeypatch.setattr("crm.cli.CLIContext.backend", lambda self: object())
        monkeypatch.setattr("crm.core.solution.publish_all", lambda b: {"ok": True})
        result = CliRunner().invoke(cli, [
            "--json", "app", "create", "--name", "CRMWorx",
            "--unique-name", "cwx_crmworx", "--no-publish",
        ])
        assert result.exit_code == 0, result.output
        assert captured["unique_name"] == "cwx_crmworx"

    def test_app_add_components_command(self, monkeypatch):
        from click.testing import CliRunner
        from crm.cli import cli
        captured = {}

        def fake_add(backend, **kw):
            captured.update(kw)
            return {"added": len(kw["components"]), "app_id": kw["app_id"]}

        monkeypatch.setattr("crm.core.appmodule.add_app_components", fake_add)
        monkeypatch.setattr("crm.cli.CLIContext.backend", lambda self: object())
        result = CliRunner().invoke(cli, [
            "--json", "app", "add-components", _APP_ID,
            "--component", "view:bbbb", "--component", "chart:cccc",
        ])
        assert result.exit_code == 0, result.output
        assert captured["components"] == [("view", "bbbb"), ("chart", "cccc")]
