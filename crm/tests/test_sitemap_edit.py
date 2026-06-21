"""Unit tests for crm.core.sitemap (live read-modify-write nav-node editors).

The transport is exercised through ``requests_mock``: the initial GET returns a
seed ``sitemapxml``, the PATCH body is captured and parsed to assert the spliced
tree, and (for the publish path) a second GET returns the "published" layer so
the T3 read-back can be driven both green and red.
"""
# pyright: basic
from __future__ import annotations

import xml.etree.ElementTree as ET

import pytest
import requests_mock

from crm.core import sitemap as sm
from crm.utils.d365_backend import D365Error

_SID = "aaaa1111-2222-3333-4444-555566667777"

# Seed sitemap: two Areas; the first has a Group with one SubArea. Stock nodes
# carry ResourceId / IntroducedVersion (platform-owned) — the editors must never
# touch them. Node ids: SFA, SFA_Grp, nav_accts, HLP, HLP_Grp.
_SEED = (
    '<SiteMap IntroducedVersion="7.0.0.0">'
    '<Area Id="SFA" ResourceId="Area_Sales" IntroducedVersion="7.0.0.0">'
    '<Group Id="SFA_Grp" ResourceId="Group_Sales">'
    '<SubArea Id="nav_accts" Entity="account" />'
    '</Group></Area>'
    '<Area Id="HLP" ResourceId="Area_Help"><Group Id="HLP_Grp" /></Area>'
    '</SiteMap>'
)


def _url(backend) -> str:
    return backend.url_for(f"sitemaps({_SID})")


def _patched_xml(m: requests_mock.Mocker) -> str:
    """The sitemapxml from the captured PATCH body."""
    patch = next(r for r in m.request_history if r.method == "PATCH")
    return patch.json()["sitemapxml"]


def _patched_root(m: requests_mock.Mocker) -> ET.Element:
    return ET.fromstring(_patched_xml(m))


def _with_seed(m: requests_mock.Mocker, backend, *, patch: bool = True) -> None:
    m.get(_url(backend), json={"sitemapxml": _SEED})
    if patch:
        m.patch(_url(backend), status_code=204)


# ── add-area ──────────────────────────────────────────────────────────────────


class TestAddArea:
    def test_dry_run_previews_without_writing(self, dry_backend):
        with requests_mock.Mocker() as m:
            m.get(_url(dry_backend), json={"sitemapxml": _SEED})
            out = sm.add_area(dry_backend, _SID, area_id="cwx_new", title="New")
            # only the load GET — no PATCH, no publish
            assert [r.method for r in m.request_history] == ["GET"]
        assert out["_dry_run"] is True and out["would_edit"] is True
        root = ET.fromstring(out["sitemapxml"])
        assert sm._find(root, "Area", "cwx_new") is not None

    def test_splices_area_with_plain_title_no_protected_attrs(self, backend):
        with requests_mock.Mocker() as m:
            _with_seed(m, backend)
            sm.add_area(backend, _SID, area_id="cwx_ops", title="Operations",
                        show_groups=True, icon="$webresource:cwx_icon")
            area = sm._find(_patched_root(m), "Area", "cwx_ops")
        assert area is not None
        assert area.get("Title") == "Operations"
        assert area.get("ShowGroups") == "true"
        assert area.get("Icon") == "$webresource:cwx_icon"
        # platform-owned attrs are never minted onto a new node
        assert area.get("ResourceId") is None
        assert area.get("IntroducedVersion") is None

    def test_duplicate_area_is_rejected(self, backend):
        with requests_mock.Mocker() as m:
            m.get(_url(backend), json={"sitemapxml": _SEED})
            with pytest.raises(D365Error, match="Area 'SFA' already exists"):
                sm.add_area(backend, _SID, area_id="SFA", title="dup")
            assert not any(r.method == "PATCH" for r in m.request_history)

    @pytest.mark.parametrize("bad", ["has space", "bad-dash", "dot.id", ""])
    def test_invalid_id_grammar_is_rejected(self, backend, bad):
        with pytest.raises(D365Error, match="invalid|must not be empty"):
            sm.add_area(backend, _SID, area_id=bad, title="x")

    def test_publish_runs_t3_read_back_in_order(self, backend):
        published = _SEED.replace(
            "</SiteMap>", '<Area Id="cwx_ops" Title="Operations" /></SiteMap>')
        with requests_mock.Mocker() as m:
            m.get(_url(backend), [
                {"json": {"sitemapxml": _SEED}},        # initial load
                {"json": {"sitemapxml": published}}])   # post-publish read-back
            m.patch(_url(backend), status_code=204)
            m.post(backend.url_for("PublishAllXml"), status_code=204)
            out = sm.add_area(backend, _SID, area_id="cwx_ops", title="Operations",
                              publish=True)
            assert [r.method for r in m.request_history] == [
                "GET", "PATCH", "POST", "GET"]
        assert out["updated"] is True and out["published"] is True

    def test_t3_read_back_fails_when_node_absent(self, backend):
        # Published layer still missing the new Area → T3 must raise.
        with requests_mock.Mocker() as m:
            m.get(_url(backend), [
                {"json": {"sitemapxml": _SEED}},
                {"json": {"sitemapxml": _SEED}}])
            m.patch(_url(backend), status_code=204)
            m.post(backend.url_for("PublishAllXml"), status_code=204)
            with pytest.raises(D365Error, match="read-back"):
                sm.add_area(backend, _SID, area_id="cwx_ops", title="Operations",
                            publish=True)


# ── add-group ───────────────────────────────────────────────────────────────


class TestAddGroup:
    def test_splices_group_under_parent_area(self, backend):
        with requests_mock.Mocker() as m:
            _with_seed(m, backend)
            sm.add_group(backend, _SID, area_id="SFA", group_id="cwx_grp",
                         title="My Group")
            area = sm._find(_patched_root(m), "Area", "SFA")
        assert area is not None
        grp = sm._find(area, "Group", "cwx_grp")
        assert grp is not None and grp.get("Title") == "My Group"

    def test_missing_parent_area_is_rejected(self, backend):
        with requests_mock.Mocker() as m:
            m.get(_url(backend), json={"sitemapxml": _SEED})
            with pytest.raises(D365Error, match="parent Area 'NOPE' not found"):
                sm.add_group(backend, _SID, area_id="NOPE", group_id="g",
                             title="t")

    def test_duplicate_group_within_area_is_rejected(self, backend):
        with requests_mock.Mocker() as m:
            m.get(_url(backend), json={"sitemapxml": _SEED})
            with pytest.raises(D365Error, match="Group 'SFA_Grp' already exists"):
                sm.add_group(backend, _SID, area_id="SFA", group_id="SFA_Grp",
                             title="dup")


# ── add-subarea ─────────────────────────────────────────────────────────────


class TestAddSubarea:
    def _seeded(self, backend, m):
        _with_seed(m, backend)

    def test_url_mode_emits_url_attr(self, backend):
        with requests_mock.Mocker() as m:
            self._seeded(backend, m)
            sm.add_subarea(backend, _SID, area_id="SFA", group_id="SFA_Grp",
                           sub_id="cwx_link", url="https://example.com")
            sub = sm._find(_patched_root(m), "SubArea", "cwx_link")
        assert sub is not None and sub.get("Url") == "https://example.com"
        # there is no SubArea WebResource attribute — a web resource is a Url
        assert sub.get("WebResource") is None

    def test_entity_mode_validated_and_emits_entity_attr(self, backend, monkeypatch):
        monkeypatch.setattr(sm, "resolve_logical_name",
                            lambda _b, name: name.lower())
        with requests_mock.Mocker() as m:
            self._seeded(backend, m)
            sm.add_subarea(backend, _SID, area_id="SFA", group_id="SFA_Grp",
                           sub_id="cwx_contacts", entity="Contact", title="People")
            sub = sm._find(_patched_root(m), "SubArea", "cwx_contacts")
        assert sub is not None
        assert sub.get("Entity") == "contact" and sub.get("Title") == "People"

    def test_entity_validation_failure_propagates(self, backend, monkeypatch):
        def _boom(_b, _name):
            raise D365Error("no such entity 'bogus'")
        monkeypatch.setattr(sm, "resolve_logical_name", _boom)
        with requests_mock.Mocker() as m:
            m.get(_url(backend), json={"sitemapxml": _SEED})
            with pytest.raises(D365Error, match="no such entity"):
                sm.add_subarea(backend, _SID, area_id="SFA", group_id="SFA_Grp",
                               sub_id="cwx_x", entity="bogus")
            assert not any(r.method == "PATCH" for r in m.request_history)

    def test_dashboard_mode_emits_normalized_guid(self, backend):
        guid = "{12345678-1234-1234-1234-1234567890ab}"
        with requests_mock.Mocker() as m:
            self._seeded(backend, m)
            sm.add_subarea(backend, _SID, area_id="SFA", group_id="SFA_Grp",
                           sub_id="cwx_dash", dashboard=guid)
            sub = sm._find(_patched_root(m), "SubArea", "cwx_dash")
        assert sub is not None
        assert sub.get("DefaultDashboard") == "12345678-1234-1234-1234-1234567890ab"

    def test_bad_dashboard_guid_is_rejected(self, backend):
        with requests_mock.Mocker() as m:
            m.get(_url(backend), json={"sitemapxml": _SEED})
            with pytest.raises(D365Error, match="must be a dashboard GUID"):
                sm.add_subarea(backend, _SID, area_id="SFA", group_id="SFA_Grp",
                               sub_id="cwx_dash", dashboard="not-a-guid")

    @pytest.mark.parametrize("kwargs", [
        {},                                              # zero modes
        {"entity": "account", "url": "https://x"},       # two modes
    ])
    def test_exactly_one_content_mode_enforced(self, backend, monkeypatch, kwargs):
        monkeypatch.setattr(sm, "resolve_logical_name", lambda _b, n: n)
        with pytest.raises(D365Error, match="exactly one of"):
            sm.add_subarea(backend, _SID, area_id="SFA", group_id="SFA_Grp",
                           sub_id="cwx_x", **kwargs)

    def test_missing_parent_group_is_rejected(self, backend):
        with requests_mock.Mocker() as m:
            m.get(_url(backend), json={"sitemapxml": _SEED})
            with pytest.raises(D365Error, match="parent Group 'NOPE' not found"):
                sm.add_subarea(backend, _SID, area_id="SFA", group_id="NOPE",
                               sub_id="cwx_x", url="https://x")

    def test_duplicate_subarea_id_is_rejected(self, backend):
        with requests_mock.Mocker() as m:
            m.get(_url(backend), json={"sitemapxml": _SEED})
            with pytest.raises(D365Error, match="SubArea id 'nav_accts'"):
                sm.add_subarea(backend, _SID, area_id="SFA", group_id="SFA_Grp",
                               sub_id="nav_accts", url="https://x")


# ── remove-node ─────────────────────────────────────────────────────────────


class TestRemoveNode:
    def test_removes_leaf_subarea(self, backend):
        with requests_mock.Mocker() as m:
            _with_seed(m, backend)
            out = sm.remove_node(backend, _SID, node_id="nav_accts")
            root = _patched_root(m)
        assert sm._find(root, "SubArea", "nav_accts") is None
        assert "cascade_warning" not in out

    def test_removing_area_with_children_warns_cascade(self, backend):
        with requests_mock.Mocker() as m:
            _with_seed(m, backend)
            out = sm.remove_node(backend, _SID, node_id="SFA")
            root = _patched_root(m)
        assert sm._find(root, "Area", "SFA") is None
        # SFA had a Group and a SubArea → 2 descendants
        assert "2 descendant" in out["cascade_warning"]

    def test_comment_out_keeps_node_as_wellformed_comment(self, backend):
        with requests_mock.Mocker() as m:
            _with_seed(m, backend)
            sm.remove_node(backend, _SID, node_id="nav_accts", comment_out=True)
            xml = _patched_xml(m)
        # round-trips (well-formed) and the node survives only inside a comment
        root = ET.fromstring(xml)
        assert sm._find(root, "SubArea", "nav_accts") is None
        assert "<!--" in xml and "nav_accts" in xml

    def test_comment_out_sanitizes_double_dash(self, backend):
        seed = (
            '<SiteMap><Area Id="A"><Group Id="G">'
            '<SubArea Id="s1" Url="https://x/a--b--c" /></Group></Area></SiteMap>')
        with requests_mock.Mocker() as m:
            m.get(_url(backend), json={"sitemapxml": seed})
            m.patch(_url(backend), status_code=204)
            sm.remove_node(backend, _SID, node_id="s1", comment_out=True)
            xml = _patched_xml(m)
        # no raw '--' run survives inside the comment text (would be malformed)
        assert "--b--c" not in xml
        ET.fromstring(xml)  # must still parse

    def test_unknown_node_is_rejected(self, backend):
        with requests_mock.Mocker() as m:
            m.get(_url(backend), json={"sitemapxml": _SEED})
            with pytest.raises(D365Error, match="no Area, Group or SubArea"):
                sm.remove_node(backend, _SID, node_id="ghost")


# ── shared RMW seam ───────────────────────────────────────────────────────────


class TestLoad:
    def test_invalid_sitemap_id_rejected(self, backend):
        with pytest.raises(D365Error, match="Invalid sitemap id"):
            sm.add_area(backend, "not-a-guid", area_id="a", title="t")

    def test_empty_sitemapxml_rejected(self, backend):
        with requests_mock.Mocker() as m:
            m.get(_url(backend), json={"sitemapxml": ""})
            with pytest.raises(D365Error, match="no sitemapxml"):
                sm.add_area(backend, _SID, area_id="a", title="t")
