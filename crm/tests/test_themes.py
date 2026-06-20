"""Unit tests for crm.core.themes (wire-level, requests_mock)."""
# pyright: basic
from __future__ import annotations

import pytest
import requests_mock

from crm.utils.d365_backend import D365Error


_THEME_ROW = {
    "themeid": "11112222-3333-4444-5555-666677778888",
    "name": "Corporate Blue",
    "type": True,
    "isdefaulttheme": False,
    "maincolor": "#0066cc",
    "navbarbackgroundcolor": "#002050",
}
_NEW_ID = "99998888-7777-6666-5555-444433332222"


def _themes_url(backend) -> str:
    return backend.url_for("themes")


class TestListThemes:
    def test_lists_themes(self, backend):
        from crm.core import themes
        with requests_mock.Mocker() as m:
            m.get(_themes_url(backend), json={"value": [_THEME_ROW]})
            result = themes.list_themes(backend)
        assert len(result) == 1
        t = result[0]
        assert t["themeid"] == _THEME_ROW["themeid"]
        assert t["name"] == "Corporate Blue"
        assert t["type"] is True
        assert t["isdefaulttheme"] is False
        # list projects to summary columns only — no color XML/strings
        assert "maincolor" not in t

    def test_list_select_is_summary(self, backend):
        from crm.core import themes
        with requests_mock.Mocker() as m:
            m.get(_themes_url(backend), json={"value": []})
            themes.list_themes(backend)
        assert "themeid" in m.last_request.qs["$select"][0]
        assert "maincolor" not in m.last_request.qs["$select"][0]


class TestGetTheme:
    def test_gets_theme_by_id(self, backend):
        from crm.core import themes
        tid = _THEME_ROW["themeid"]
        with requests_mock.Mocker() as m:
            m.get(backend.url_for(f"themes({tid})"), json=_THEME_ROW)
            out = themes.get_theme(backend, tid)
        assert out["themeid"] == tid
        assert out["name"] == "Corporate Blue"
        assert out["maincolor"] == "#0066cc"

    def test_rejects_non_guid_id(self, backend):
        from crm.core import themes
        with pytest.raises(D365Error):
            themes.get_theme(backend, "not-a-guid")


class TestCreateTheme:
    def test_creates_theme_and_posts_body(self, backend):
        from crm.core import themes
        with requests_mock.Mocker() as m:
            m.post(_themes_url(backend), status_code=204,
                   headers={"OData-EntityId": backend.url_for(f"themes({_NEW_ID})")})
            out = themes.create_theme(
                backend, name="Corporate Blue",
                attributes={"maincolor": "#0066cc"},
            )
        body = m.last_request.json()
        assert body["name"] == "Corporate Blue"
        assert body["maincolor"] == "#0066cc"
        assert out["created"] is True
        assert out["themeid"] == _NEW_ID
        assert out["name"] == "Corporate Blue"

    def test_logo_resolves_name_and_binds(self, backend):
        from crm.core import themes
        wr_id = "dddddddd-0000-0000-0000-000000000001"
        with requests_mock.Mocker() as m:
            m.get(backend.url_for("webresourceset"), json={
                "value": [{"webresourceid": wr_id, "name": "new_logo"}]})
            m.post(_themes_url(backend), status_code=204,
                   headers={"OData-EntityId": backend.url_for(f"themes({_NEW_ID})")})
            out = themes.create_theme(backend, name="T", logo="new_logo")
        body = m.last_request.json()
        assert body["logoimage@odata.bind"] == f"/webresourceset({wr_id})"
        assert out["created"] is True

    def test_logo_accepts_guid_without_lookup(self, backend):
        from crm.core import themes
        wr_id = "dddddddd-0000-0000-0000-000000000002"
        with requests_mock.Mocker() as m:
            m.post(_themes_url(backend), status_code=204,
                   headers={"OData-EntityId": backend.url_for(f"themes({_NEW_ID})")})
            themes.create_theme(backend, name="T", logo=wr_id)
        body = m.last_request.json()
        assert body["logoimage@odata.bind"] == f"/webresourceset({wr_id})"

    def test_logo_not_found_raises(self, backend):
        from crm.core import themes
        with requests_mock.Mocker() as m:
            m.get(backend.url_for("webresourceset"), json={"value": []})
            with pytest.raises(D365Error):
                themes.create_theme(backend, name="T", logo="missing_wr")

    def test_create_dry_run_previews_resolved_body(self, dry_backend):
        from crm.core import themes
        out = themes.create_theme(
            dry_backend, name="Corporate Blue",
            attributes={"maincolor": "#0066cc"},
        )
        assert out["_dry_run"] is True
        assert out["would_create"]["entity_set"] == "themes"
        assert out["would_create"]["body"]["name"] == "Corporate Blue"
        assert out["would_create"]["body"]["maincolor"] == "#0066cc"


class TestUpdateTheme:
    def test_patches_attributes(self, backend):
        from crm.core import themes
        tid = _THEME_ROW["themeid"]
        with requests_mock.Mocker() as m:
            m.patch(backend.url_for(f"themes({tid})"), status_code=204)
            out = themes.update_theme(
                backend, tid, name="Renamed", attributes={"maincolor": "#ff0000"})
        body = m.last_request.json()
        assert body["name"] == "Renamed"
        assert body["maincolor"] == "#ff0000"
        assert out["updated"] is True
        assert out["themeid"] == tid

    def test_update_requires_some_field(self, backend):
        from crm.core import themes
        tid = _THEME_ROW["themeid"]
        with pytest.raises(D365Error):
            themes.update_theme(backend, tid)

    def test_update_rejects_non_guid_id(self, backend):
        from crm.core import themes
        with pytest.raises(D365Error):
            themes.update_theme(backend, "not-a-guid", name="X")

    def test_update_dry_run_previews(self, dry_backend):
        from crm.core import themes
        tid = _THEME_ROW["themeid"]
        out = themes.update_theme(dry_backend, tid, attributes={"maincolor": "#ff0000"})
        assert out["_dry_run"] is True
        assert out["would_update"]["entity_set"] == "themes"
        assert out["would_update"]["themeid"] == tid
        assert out["would_update"]["body"]["maincolor"] == "#ff0000"


class TestPublishTheme:
    def test_publishes_via_bound_action(self, backend):
        from crm.core import themes
        tid = _THEME_ROW["themeid"]
        action_url = backend.url_for(
            f"themes({tid})/Microsoft.Dynamics.CRM.PublishTheme")
        with requests_mock.Mocker() as m:
            m.post(action_url, status_code=204)
            out = themes.publish_theme(backend, tid)
        assert out["published"] is True
        assert out["themeid"] == tid

    def test_publish_rejects_non_guid_id(self, backend):
        from crm.core import themes
        with pytest.raises(D365Error):
            themes.publish_theme(backend, "not-a-guid")

    def test_publish_dry_run_previews(self, dry_backend):
        from crm.core import themes
        tid = _THEME_ROW["themeid"]
        out = themes.publish_theme(dry_backend, tid)
        assert out["_dry_run"] is True
        assert out["would_publish"]["themeid"] == tid
