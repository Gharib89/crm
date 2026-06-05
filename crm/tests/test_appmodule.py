"""Unit tests for crm.core.appmodule.

The AddAppComponents payload shape and the appmodule `webresourceid` requirement
were both verified live against D365 CE on-prem 9.1 (walkthrough §11): the action
takes typed entity references, and `appmodules` rejects a null `webresourceid`.
"""
# pyright: basic
from __future__ import annotations

from typing import Any

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
_SITEMAP_ID = "88888888-8888-8888-8888-888888888888"


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

    def test_create_app_dry_run_probes_for_real_and_reports_would_skip(self, profile):
        from crm.core import appmodule
        dry = D365Backend(profile, password="pw", dry_run=True)
        with requests_mock.Mocker() as m:
            m.get(dry.url_for("appmodules"),
                  json={"value": [{"appmoduleid": _APP_ID, "uniquename": "cwx_crmworx"}]})
            out = appmodule.create_app(dry, name="CRMWorx",
                                       unique_name="cwx_crmworx", if_exists="skip")
        assert out["_dry_run"] is True
        assert out["_exists"] is True
        assert out["would_skip"] is True
        assert any(r.method == "GET" for r in m.request_history)
        assert not any(r.method == "POST" for r in m.request_history)

    def test_create_app_publishes_before_readback(self, backend):
        from crm.core import appmodule
        with requests_mock.Mocker() as m:
            m.get(backend.url_for("appmodules"), json={"value": []})  # guard
            app_url = backend.url_for(f"appmodules({_APP_ID})")
            m.post(backend.url_for("appmodules"), status_code=204,
                   headers={"OData-EntityId": app_url})
            m.post(backend.url_for("PublishAllXml"), status_code=204)
            m.get(app_url, json={"appmoduleid": _APP_ID, "name": "CRMWorx"})
            appmodule.create_app(backend, name="CRMWorx",
                                 unique_name="cwx_crmworx", publish=True)
        # PublishAllXml must precede the read-back GET of the new appmodule
        kinds = [(r.method, r.url) for r in m.request_history]
        publish_i = next(i for i, (mth, u) in enumerate(kinds)
                         if mth == "POST" and "PublishAllXml" in u)
        readback_i = next(i for i, (mth, u) in enumerate(kinds)
                          if mth == "GET" and f"appmodules({_APP_ID})" in u)
        assert publish_i < readback_i

    def test_create_app_unparseable_id_sets_lookup_error(self, backend):
        from crm.core import appmodule
        with requests_mock.Mocker() as m:
            m.get(backend.url_for("appmodules"), json={"value": []})
            m.post(backend.url_for("appmodules"), status_code=204,
                   headers={"OData-EntityId": "https://x/appmodules(bogus)"})
            out = appmodule.create_app(backend, name="CRMWorx",
                                       unique_name="cwx_crmworx")
        assert out["created"] is True
        assert out["appmoduleid"] is None
        assert "app_lookup_error" in out

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

    def test_add_components_dry_run_previews(self, profile):
        from crm.core import appmodule
        dry = D365Backend(profile, password="pw", dry_run=True)
        out = appmodule.add_app_components(
            dry, app_id=_APP_ID, components=[("view", "bbbb"), ("chart", "cccc")])
        # dry-run surfaces the preview instead of a fake "added" count
        assert out["_dry_run"] is True
        assert out["components"] == 2
        assert out["app_id"] == _APP_ID
        assert "added" not in out


class TestSetSitemap:
    def test_set_sitemap_posts_and_reads_id(self, backend):
        from crm.core import appmodule
        sm_url = backend.url_for(f"sitemaps({_SITEMAP_ID})")
        with requests_mock.Mocker() as m:
            m.post(backend.url_for("sitemaps"), status_code=204,
                   headers={"OData-EntityId": sm_url})
            out = appmodule.set_sitemap(
                backend, sitemap_name="CRMWorx SiteMap",
                sitemap_xml="<SiteMap><Area Id='cwx' /></SiteMap>",
                unique_name="cwx_crmworx", solution="cwx_sol",
            )
        assert out["created"] is True
        assert out["sitemapid"] == _SITEMAP_ID
        post = _posts(m)[0]
        body = post.json()
        assert body["sitemapname"] == "CRMWorx SiteMap"
        assert body["sitemapxml"].startswith("<SiteMap")
        assert body["sitemapnameunique"] == "cwx_crmworx"
        # solution routes through the MSCRM.SolutionUniqueName header
        assert post.headers["MSCRM.SolutionUniqueName"] == "cwx_sol"

    def test_set_sitemap_unparseable_id_sets_lookup_error(self, backend):
        from crm.core import appmodule
        with requests_mock.Mocker() as m:
            m.post(backend.url_for("sitemaps"), status_code=204,
                   headers={"OData-EntityId": "https://x/sitemaps(bogus)"})
            out = appmodule.set_sitemap(backend, sitemap_name="X",
                                        sitemap_xml="<SiteMap/>")
        assert out["created"] is True
        assert out["sitemapid"] is None
        assert "sitemap_lookup_error" in out

    def test_set_sitemap_rejects_empty_xml(self, backend):
        from crm.core import appmodule
        with pytest.raises(D365Error, match="must not be empty"):
            appmodule.set_sitemap(backend, sitemap_name="x", sitemap_xml="   ")

    def test_app_set_sitemap_command_wires_core(self, monkeypatch):
        from click.testing import CliRunner
        from crm.cli import cli
        captured = {}

        def fake_set_sitemap(backend, **kw):
            captured.update(kw)
            return {"created": True, "sitemapid": _SITEMAP_ID,
                    "sitemapname": kw["sitemap_name"]}

        monkeypatch.setattr("crm.core.appmodule.set_sitemap", fake_set_sitemap)
        monkeypatch.setattr("crm.cli.CLIContext.backend", lambda self: object())
        runner = CliRunner()
        with runner.isolated_filesystem():
            with open("sm.xml", "w", encoding="utf-8") as fh:
                fh.write("<SiteMap><Area Id='cwx' /></SiteMap>")
            result = runner.invoke(cli, [
                "--json", "app", "set-sitemap", "CRMWorx SiteMap",
                "--xml-file", "sm.xml", "--unique-name", "cwx_crmworx",
            ])
        assert result.exit_code == 0, result.output
        assert captured["sitemap_name"] == "CRMWorx SiteMap"
        assert captured["unique_name"] == "cwx_crmworx"
        assert captured["sitemap_xml"].startswith("<SiteMap")


class TestBuildSitemapXml:
    def test_nests_areas_groups_subareas_in_order(self):
        from crm.core import appmodule
        xml = appmodule.build_sitemapxml(
            areas=[("area1", "Sales"), ("area2", "Service")],
            groups=[("area1", "g1", "Accounts"), ("area2", "g2", "Cases")],
            subareas=[
                ("area1", "g1", "account", "Accounts"),
                ("area2", "g2", "incident", "Cases"),
            ],
        )
        assert xml == (
            '<SiteMap>'
            '<Area Id="area1" Title="Sales">'
            '<Group Id="g1" Title="Accounts">'
            '<SubArea Id="account" Entity="account" Title="Accounts" />'
            '</Group></Area>'
            '<Area Id="area2" Title="Service">'
            '<Group Id="g2" Title="Cases">'
            '<SubArea Id="incident" Entity="incident" Title="Cases" />'
            '</Group></Area>'
            '</SiteMap>'
        )

    def test_subarea_omits_title_when_none(self):
        from crm.core import appmodule
        xml = appmodule.build_sitemapxml(
            areas=[("a", "A")], groups=[("a", "g", "G")],
            subareas=[("a", "g", "account", None)],
        )
        assert '<SubArea Id="account" Entity="account" />' in xml
        assert 'SubArea Id="account" Entity="account" Title=' not in xml

    def test_subarea_omits_title_when_empty(self):
        from crm.core import appmodule
        xml = appmodule.build_sitemapxml(
            areas=[("a", "A")], groups=[("a", "g", "G")],
            subareas=[("a", "g", "account", "   ")],
        )
        assert '<SubArea Id="account" Entity="account" />' in xml

    def test_empty_area_group_title_defaults_to_id(self):
        from crm.core import appmodule
        xml = appmodule.build_sitemapxml(
            areas=[("area1", "  ")], groups=[("area1", "grp1", "")],
            subareas=[],
        )
        assert '<Area Id="area1" Title="area1">' in xml
        assert '<Group Id="grp1" Title="grp1">' in xml

    def test_attribute_values_are_quoteattr_escaped(self):
        from crm.core import appmodule
        xml = appmodule.build_sitemapxml(
            areas=[("a", 'Tom & "Jerry" <x>')],
            groups=[("a", "g", "G")],
            subareas=[("a", "g", "account", 'Acc & "B" <c>')],
        )
        # quoteattr escapes & and <; double quotes inside force single-quote wrapping
        assert "&amp;" in xml
        assert "&lt;" in xml
        assert 'Title=\'Tom &amp; "Jerry" &lt;x&gt;\'' in xml
        assert 'Title=\'Acc &amp; "B" &lt;c&gt;\'' in xml

    def test_duplicate_subarea_entity_gets_distinct_ids(self):
        from crm.core import appmodule
        xml = appmodule.build_sitemapxml(
            areas=[("a", "A")], groups=[("a", "g", "G")],
            subareas=[
                ("a", "g", "account", None),
                ("a", "g", "account", None),
                ("a", "g", "account", None),
            ],
        )
        assert '<SubArea Id="account" Entity="account" />' in xml
        assert '<SubArea Id="account_2" Entity="account" />' in xml
        assert '<SubArea Id="account_3" Entity="account" />' in xml

    def test_subarea_ids_unique_across_whole_document(self):
        from crm.core import appmodule
        xml = appmodule.build_sitemapxml(
            areas=[("a1", "A1"), ("a2", "A2")],
            groups=[("a1", "g1", "G1"), ("a2", "g2", "G2")],
            subareas=[
                ("a1", "g1", "account", None),
                ("a2", "g2", "account", None),
            ],
        )
        assert '<SubArea Id="account" Entity="account" />' in xml
        assert '<SubArea Id="account_2" Entity="account" />' in xml

    def test_empty_areas_raises(self):
        from crm.core import appmodule
        with pytest.raises(D365Error, match="at least one area"):
            appmodule.build_sitemapxml(areas=[], groups=[], subareas=[])

    def test_duplicate_area_id_raises(self):
        from crm.core import appmodule
        with pytest.raises(D365Error, match="duplicate area"):
            appmodule.build_sitemapxml(
                areas=[("a", "A"), ("a", "B")], groups=[], subareas=[])

    def test_duplicate_group_id_raises(self):
        from crm.core import appmodule
        with pytest.raises(D365Error, match="duplicate group"):
            appmodule.build_sitemapxml(
                areas=[("a", "A")],
                groups=[("a", "g", "G1"), ("a", "g", "G2")],
                subareas=[],
            )

    def test_group_unknown_area_raises(self):
        from crm.core import appmodule
        with pytest.raises(D365Error, match="unknown area"):
            appmodule.build_sitemapxml(
                areas=[("a", "A")], groups=[("nope", "g", "G")], subareas=[])

    def test_subarea_unknown_group_raises(self):
        from crm.core import appmodule
        with pytest.raises(D365Error, match="does not reference"):
            appmodule.build_sitemapxml(
                areas=[("a", "A")], groups=[("a", "g", "G")],
                subareas=[("a", "nope", "account", None)],
            )

    def test_subarea_group_in_wrong_area_raises(self):
        from crm.core import appmodule
        with pytest.raises(D365Error, match="does not reference"):
            appmodule.build_sitemapxml(
                areas=[("a1", "A1"), ("a2", "A2")],
                groups=[("a1", "g1", "G1")],
                subareas=[("a2", "g1", "account", None)],
            )

    def test_empty_entity_raises(self):
        from crm.core import appmodule
        with pytest.raises(D365Error, match="entity"):
            appmodule.build_sitemapxml(
                areas=[("a", "A")], groups=[("a", "g", "G")],
                subareas=[("a", "g", "", None)],
            )

    def test_empty_area_id_raises(self):
        from crm.core import appmodule
        with pytest.raises(D365Error, match="area"):
            appmodule.build_sitemapxml(
                areas=[("", "A")], groups=[], subareas=[])

    def test_empty_group_id_raises(self):
        from crm.core import appmodule
        with pytest.raises(D365Error, match="group"):
            appmodule.build_sitemapxml(
                areas=[("a", "A")], groups=[("a", "", "G")], subareas=[])

    def test_whitespace_only_ids_are_stripped_and_rejected(self):
        # Whitespace-only identifiers are treated as empty in core too (not just
        # the CLI), keeping programmatic callers safe.
        from crm.core import appmodule
        with pytest.raises(D365Error, match="area"):
            appmodule.build_sitemapxml(
                areas=[("   ", "A")], groups=[], subareas=[])
        with pytest.raises(D365Error, match="entity"):
            appmodule.build_sitemapxml(
                areas=[("a", "A")], groups=[("a", "g", "G")],
                subareas=[("a", "g", "   ", None)])

    def test_ids_are_stripped_in_output(self):
        # Surrounding whitespace on Ids/entity is stripped before emission, so
        # references still resolve and the XML stays clean.
        from crm.core import appmodule
        xml = appmodule.build_sitemapxml(
            areas=[("  sales  ", "Sales")],
            groups=[("sales", "  accts  ", "Accounts")],
            subareas=[("sales", "accts", "  account  ", None)])
        assert 'Id="sales"' in xml
        assert 'Id="accts"' in xml
        assert 'Entity="account"' in xml


class TestBuildSitemap:
    def _args(self) -> dict[str, Any]:
        return dict(
            sitemap_name="CRMWorx SiteMap",
            areas=[("a", "Sales")],
            groups=[("a", "g", "Accounts")],
            subareas=[("a", "g", "account", "Accounts")],
        )

    def test_dry_run_returns_xml_without_posting(self, profile):
        from crm.core import appmodule
        dry = D365Backend(profile, password="pw", dry_run=True)
        with requests_mock.Mocker() as m:
            out = appmodule.build_sitemap(dry, **self._args())
        assert out["_dry_run"] is True
        assert out["sitemapname"] == "CRMWorx SiteMap"
        assert out["sitemapxml"].startswith("<SiteMap")
        assert "<SubArea" in out["sitemapxml"]
        assert not any(r.method == "POST" for r in m.request_history)

    def test_empty_name_raises(self, backend):
        from crm.core import appmodule
        args = self._args()
        args["sitemap_name"] = "   "
        with pytest.raises(D365Error, match="sitemap_name"):
            appmodule.build_sitemap(backend, **args)

    def test_posts_via_set_sitemap_body_unchanged(self, backend):
        from crm.core import appmodule
        a = self._args()
        expected_xml = appmodule.build_sitemapxml(
            areas=a["areas"], groups=a["groups"], subareas=a["subareas"],
        )
        sm_url = backend.url_for(f"sitemaps({_SITEMAP_ID})")
        with requests_mock.Mocker() as m:
            m.post(backend.url_for("sitemaps"), status_code=204,
                   headers={"OData-EntityId": sm_url})
            out = appmodule.build_sitemap(backend, **self._args())
        assert out["created"] is True
        assert out["sitemapid"] == _SITEMAP_ID
        posts = _posts(m)
        assert len(posts) == 1
        # Byte-identical to what set_sitemap would build (no sitemapnameunique here)
        assert posts[0].json() == {
            "sitemapname": "CRMWorx SiteMap", "sitemapxml": expected_xml,
        }

    def test_posts_with_unique_name_adds_sitemapnameunique(self, backend):
        from crm.core import appmodule
        sm_url = backend.url_for(f"sitemaps({_SITEMAP_ID})")
        with requests_mock.Mocker() as m:
            m.post(backend.url_for("sitemaps"), status_code=204,
                   headers={"OData-EntityId": sm_url})
            appmodule.build_sitemap(backend, unique_name="cwx_crmworx", **self._args())
        body = _posts(m)[0].json()
        assert body["sitemapnameunique"] == "cwx_crmworx"

    def test_publish_runs_when_requested(self, backend):
        from crm.core import appmodule
        sm_url = backend.url_for(f"sitemaps({_SITEMAP_ID})")
        with requests_mock.Mocker() as m:
            m.post(backend.url_for("sitemaps"), status_code=204,
                   headers={"OData-EntityId": sm_url})
            m.post(backend.url_for("PublishAllXml"), status_code=204)
            out = appmodule.build_sitemap(backend, publish=True, **self._args())
        assert out["published"] is True
        assert any(r.method == "POST" and "PublishAllXml" in r.url
                   for r in m.request_history)


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

    def test_app_create_icon_webresource_guid_passthrough(self, monkeypatch):
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
            "--icon-webresource", "11111111-1111-1111-1111-111111111111",
        ])
        assert result.exit_code == 0, result.output
        assert captured["web_resource_id"] == "11111111-1111-1111-1111-111111111111"

    def test_app_create_icon_webresource_resolves_name(self, monkeypatch):
        from click.testing import CliRunner
        from crm.cli import cli
        captured = {}
        seen = {}

        def fake_resolve(backend, name_or_guid):
            seen["name_or_guid"] = name_or_guid
            return "99999999-9999-9999-9999-999999999999"

        def fake_create_app(backend, **kw):
            captured.update(kw)
            return {"created": True, "appmoduleid": _APP_ID, "uniquename": kw["unique_name"]}

        monkeypatch.setattr("crm.core.webresource.resolve_webresource_id", fake_resolve)
        monkeypatch.setattr("crm.core.appmodule.create_app", fake_create_app)
        monkeypatch.setattr("crm.cli.CLIContext.backend", lambda self: object())
        monkeypatch.setattr("crm.core.solution.publish_all", lambda b: {"ok": True})
        result = CliRunner().invoke(cli, [
            "--json", "app", "create", "--name", "CRMWorx",
            "--unique-name", "cwx_crmworx", "--no-publish",
            "--icon-webresource", "cwx_/icons/app.svg",
        ])
        assert result.exit_code == 0, result.output
        assert seen["name_or_guid"] == "cwx_/icons/app.svg"
        assert captured["web_resource_id"] == "99999999-9999-9999-9999-999999999999"

    def test_app_create_defaults_icon_when_omitted(self, monkeypatch):
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
        assert captured["web_resource_id"] == _DEFAULT_ICON

    def test_app_build_sitemap_command_wires_core(self, monkeypatch):
        from click.testing import CliRunner
        from crm.cli import cli
        captured: dict[str, Any] = {}

        def fake_build_sitemap(backend, **kw):
            captured.update(kw)
            return {"created": True, "sitemapid": _SITEMAP_ID,
                    "sitemapname": kw["sitemap_name"]}

        monkeypatch.setattr("crm.core.appmodule.build_sitemap", fake_build_sitemap)
        monkeypatch.setattr("crm.cli.CLIContext.backend", lambda self: object())
        result = CliRunner().invoke(cli, [
            "--json", "app", "build-sitemap", "CRMWorx SiteMap",
            "--area", "sales:Sales",
            "--group", "sales/accts:Accounts",
            "--subarea", "sales/accts:entity=account:Accounts",
            "--subarea", "sales/accts:entity=contact",
            "--unique-name", "cwx_crmworx", "--no-publish",
        ])
        assert result.exit_code == 0, result.output
        assert captured["sitemap_name"] == "CRMWorx SiteMap"
        assert captured["areas"] == [("sales", "Sales")]
        assert captured["groups"] == [("sales", "accts", "Accounts")]
        assert captured["subareas"] == [
            ("sales", "accts", "account", "Accounts"),
            ("sales", "accts", "contact", None),
        ]
        assert captured["unique_name"] == "cwx_crmworx"
        assert captured["publish"] is False

    def test_app_build_sitemap_dry_run_emits_xml_without_post(self, monkeypatch):
        from click.testing import CliRunner
        from crm.cli import cli

        def fake_build_sitemap(backend, **kw):
            assert backend.dry_run is True
            xml = appmodule.build_sitemapxml(
                kw["areas"], kw["groups"], kw["subareas"])
            return {"_dry_run": True, "sitemapname": kw["sitemap_name"],
                    "sitemapxml": xml}

        from crm.core import appmodule
        monkeypatch.setattr("crm.core.appmodule.build_sitemap", fake_build_sitemap)

        class _DryBackend:
            dry_run = True

        monkeypatch.setattr("crm.cli.CLIContext.backend", lambda self: _DryBackend())
        result = CliRunner().invoke(cli, [
            "--json", "--dry-run", "app", "build-sitemap", "CRMWorx SiteMap",
            "--area", "sales:Sales",
            "--group", "sales/accts:Accounts",
            "--subarea", "sales/accts:entity=account",
        ])
        assert result.exit_code == 0, result.output
        assert "<SiteMap" in result.output
        assert "<SubArea" in result.output

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

    def test_app_add_components_strips_whitespace(self, monkeypatch):
        from click.testing import CliRunner
        from crm.cli import cli
        captured = {}
        monkeypatch.setattr(
            "crm.core.appmodule.add_app_components",
            lambda backend, **kw: captured.update(kw) or {"added": len(kw["components"])})
        monkeypatch.setattr("crm.cli.CLIContext.backend", lambda self: object())
        result = CliRunner().invoke(cli, [
            "--json", "app", "add-components", _APP_ID, "--component", " view : bbbb ",
        ])
        assert result.exit_code == 0, result.output
        assert captured["components"] == [("view", "bbbb")]


class TestSitemapParsers:
    def test_parse_area_with_title(self):
        from crm.commands.app import _parse_area
        assert _parse_area(" sales : Sales ") == ("sales", "Sales")

    def test_parse_area_empty_title_ok(self):
        from crm.commands.app import _parse_area
        assert _parse_area("sales") == ("sales", "")

    def test_parse_area_empty_id_rejected(self):
        import click
        from crm.commands.app import _parse_area
        with pytest.raises(click.BadParameter):
            _parse_area(" :Sales")

    def test_parse_group_valid(self):
        from crm.commands.app import _parse_group
        assert _parse_group(" sales / accts : Accounts ") == (
            "sales", "accts", "Accounts")

    def test_parse_group_no_slash_rejected(self):
        import click
        from crm.commands.app import _parse_group
        with pytest.raises(click.BadParameter):
            _parse_group("sales:Accounts")

    def test_parse_group_empty_id_rejected(self):
        import click
        from crm.commands.app import _parse_group
        with pytest.raises(click.BadParameter):
            _parse_group("sales/:Accounts")

    def test_parse_subarea_with_title(self):
        from crm.commands.app import _parse_subarea
        assert _parse_subarea("sales/accts:entity=account:Accounts") == (
            "sales", "accts", "account", "Accounts")

    def test_parse_subarea_without_title(self):
        from crm.commands.app import _parse_subarea
        assert _parse_subarea("sales/accts:entity=account") == (
            "sales", "accts", "account", None)

    def test_parse_subarea_blank_title_is_none(self):
        from crm.commands.app import _parse_subarea
        assert _parse_subarea("sales/accts:entity=account:   ") == (
            "sales", "accts", "account", None)

    def test_parse_subarea_no_colon_rejected(self):
        import click
        from crm.commands.app import _parse_subarea
        with pytest.raises(click.BadParameter):
            _parse_subarea("sales/accts")

    def test_parse_subarea_missing_entity_prefix_rejected(self):
        import click
        from crm.commands.app import _parse_subarea
        with pytest.raises(click.BadParameter):
            _parse_subarea("sales/accts:account")

    def test_parse_subarea_empty_entity_rejected(self):
        import click
        from crm.commands.app import _parse_subarea
        with pytest.raises(click.BadParameter):
            _parse_subarea("sales/accts:entity=")

    def test_parse_subarea_bad_ref_rejected(self):
        import click
        from crm.commands.app import _parse_subarea
        with pytest.raises(click.BadParameter):
            _parse_subarea("sales:entity=account")
