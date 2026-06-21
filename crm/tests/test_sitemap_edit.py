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
            with pytest.raises(D365Error, match="node id 'SFA' already exists"):
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

    def test_duplicate_group_in_same_area_is_rejected(self, backend):
        with requests_mock.Mocker() as m:
            m.get(_url(backend), json={"sitemapxml": _SEED})
            with pytest.raises(D365Error, match="node id 'SFA_Grp' already exists"):
                sm.add_group(backend, _SID, area_id="SFA", group_id="SFA_Grp",
                             title="dup")

    def test_duplicate_group_across_areas_is_rejected(self, backend):
        # node ids are unique across the whole document — a group id already used
        # in another Area cannot be reused (keeps remove-node by-id unambiguous).
        with requests_mock.Mocker() as m:
            m.get(_url(backend), json={"sitemapxml": _SEED})
            with pytest.raises(D365Error, match="node id 'SFA_Grp' already exists"):
                sm.add_group(backend, _SID, area_id="HLP", group_id="SFA_Grp",
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
            with pytest.raises(D365Error, match="node id 'nav_accts' already exists"):
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


# ── move-node ─────────────────────────────────────────────────────────────────

# A sitemap with three SubAreas under one Group (G1) — enough to reorder — and a
# lone SubArea under a second Group (G2) in a second Area, to test the
# same-parent / same-type rails.
_MOVE_SEED = (
    '<SiteMap IntroducedVersion="7.0.0.0">'
    '<Area Id="A1"><Group Id="G1">'
    '<SubArea Id="s1" Entity="account" Title="Accounts" />'
    '<SubArea Id="s2" Entity="contact" />'
    '<SubArea Id="s3" Entity="lead" />'
    '</Group></Area>'
    '<Area Id="A2"><Group Id="G2"><SubArea Id="s4" Url="https://x" /></Group></Area>'
    '</SiteMap>'
)


def _order(parent: ET.Element, tag: str) -> list[str]:
    """Ordered Ids of ``parent``'s direct ``tag`` children."""
    return [c.get("Id") or "" for c in parent if c.tag == tag]


def _move_seeded(m: requests_mock.Mocker, backend, *, patch: bool = True) -> None:
    m.get(_url(backend), json={"sitemapxml": _MOVE_SEED})
    if patch:
        m.patch(_url(backend), status_code=204)


class TestMoveNode:
    def test_move_before_reorders_within_parent(self, backend):
        with requests_mock.Mocker() as m:
            _move_seeded(m, backend)
            sm.move_node(backend, _SID, node_id="s3", before="s1")
            g1 = sm._find(_patched_root(m), "Group", "G1")
        assert g1 is not None
        assert _order(g1, "SubArea") == ["s3", "s1", "s2"]

    def test_move_after_reorders_within_parent(self, backend):
        with requests_mock.Mocker() as m:
            _move_seeded(m, backend)
            sm.move_node(backend, _SID, node_id="s1", after="s3")
            g1 = sm._find(_patched_root(m), "Group", "G1")
        assert g1 is not None
        assert _order(g1, "SubArea") == ["s2", "s3", "s1"]

    def test_move_to_index_reorders_within_parent(self, backend):
        with requests_mock.Mocker() as m:
            _move_seeded(m, backend)
            sm.move_node(backend, _SID, node_id="s1", index=2)
            g1 = sm._find(_patched_root(m), "Group", "G1")
        assert g1 is not None
        assert _order(g1, "SubArea") == ["s2", "s3", "s1"]

    def test_move_to_index_zero(self, backend):
        with requests_mock.Mocker() as m:
            _move_seeded(m, backend)
            sm.move_node(backend, _SID, node_id="s3", index=0)
            g1 = sm._find(_patched_root(m), "Group", "G1")
        assert g1 is not None
        assert _order(g1, "SubArea") == ["s3", "s1", "s2"]

    def test_move_is_pure_permutation_attrs_untouched(self, backend):
        with requests_mock.Mocker() as m:
            _move_seeded(m, backend)
            sm.move_node(backend, _SID, node_id="s1", index=2)
            g1 = sm._find(_patched_root(m), "Group", "G1")
        assert g1 is not None
        moved = sm._find(g1, "SubArea", "s1")
        assert moved is not None
        # only its position changed — its attributes are carried verbatim
        assert moved.get("Entity") == "account"
        assert moved.get("Title") == "Accounts"

    def test_index_out_of_range_is_rejected(self, backend):
        with requests_mock.Mocker() as m:
            m.get(_url(backend), json={"sitemapxml": _MOVE_SEED})
            with pytest.raises(D365Error, match="out of range"):
                sm.move_node(backend, _SID, node_id="s1", index=5)
            assert not any(r.method == "PATCH" for r in m.request_history)

    def test_anchor_in_other_parent_is_rejected(self, backend):
        # s4 lives under G2, not G1 → not a sibling of s1.
        with requests_mock.Mocker() as m:
            m.get(_url(backend), json={"sitemapxml": _MOVE_SEED})
            with pytest.raises(D365Error, match="not a sibling"):
                sm.move_node(backend, _SID, node_id="s1", after="s4")

    def test_anchor_of_other_type_is_rejected(self, backend):
        # A1 is an Area; s1 is a SubArea → different node type.
        with requests_mock.Mocker() as m:
            m.get(_url(backend), json={"sitemapxml": _MOVE_SEED})
            with pytest.raises(D365Error, match="same node type"):
                sm.move_node(backend, _SID, node_id="s1", before="A1")

    def test_anchor_equal_to_node_is_rejected(self, backend):
        with requests_mock.Mocker() as m:
            m.get(_url(backend), json={"sitemapxml": _MOVE_SEED})
            with pytest.raises(D365Error, match="cannot be the node being moved"):
                sm.move_node(backend, _SID, node_id="s1", before="s1")

    def test_unknown_node_is_rejected(self, backend):
        with requests_mock.Mocker() as m:
            m.get(_url(backend), json={"sitemapxml": _MOVE_SEED})
            with pytest.raises(D365Error, match="no Area, Group or SubArea"):
                sm.move_node(backend, _SID, node_id="ghost", index=0)

    def test_unknown_anchor_is_rejected(self, backend):
        with requests_mock.Mocker() as m:
            m.get(_url(backend), json={"sitemapxml": _MOVE_SEED})
            with pytest.raises(D365Error, match="anchor node 'ghost' not found"):
                sm.move_node(backend, _SID, node_id="s1", before="ghost")

    @pytest.mark.parametrize("kwargs", [
        {},                               # zero modes
        {"before": "s2", "index": 0},     # two modes
    ])
    def test_exactly_one_destination_enforced(self, backend, kwargs):
        with pytest.raises(D365Error, match="exactly one of"):
            sm.move_node(backend, _SID, node_id="s1", **kwargs)

    def test_dry_run_previews_without_writing(self, dry_backend):
        with requests_mock.Mocker() as m:
            m.get(_url(dry_backend), json={"sitemapxml": _MOVE_SEED})
            out = sm.move_node(dry_backend, _SID, node_id="s1", index=2)
            assert [r.method for r in m.request_history] == ["GET"]
        assert out["_dry_run"] is True and out["would_edit"] is True
        g1 = sm._find(ET.fromstring(out["sitemapxml"]), "Group", "G1")
        assert g1 is not None
        assert _order(g1, "SubArea") == ["s2", "s3", "s1"]

    def test_publish_t3_verifies_new_position(self, backend):
        published = _MOVE_SEED.replace(
            '<SubArea Id="s1" Entity="account" Title="Accounts" />'
            '<SubArea Id="s2" Entity="contact" />'
            '<SubArea Id="s3" Entity="lead" />',
            '<SubArea Id="s2" Entity="contact" />'
            '<SubArea Id="s3" Entity="lead" />'
            '<SubArea Id="s1" Entity="account" Title="Accounts" />')
        with requests_mock.Mocker() as m:
            m.get(_url(backend), [
                {"json": {"sitemapxml": _MOVE_SEED}},
                {"json": {"sitemapxml": published}}])
            m.patch(_url(backend), status_code=204)
            m.post(backend.url_for("PublishAllXml"), status_code=204)
            out = sm.move_node(backend, _SID, node_id="s1", index=2, publish=True)
            assert [r.method for r in m.request_history] == [
                "GET", "PATCH", "POST", "GET"]
        assert out["updated"] is True and out["published"] is True

    def test_t3_read_back_fails_when_order_unchanged(self, backend):
        # Published layer still in the original order → T3 must raise.
        with requests_mock.Mocker() as m:
            m.get(_url(backend), [
                {"json": {"sitemapxml": _MOVE_SEED}},
                {"json": {"sitemapxml": _MOVE_SEED}}])
            m.patch(_url(backend), status_code=204)
            m.post(backend.url_for("PublishAllXml"), status_code=204)
            with pytest.raises(D365Error, match="requested position"):
                sm.move_node(backend, _SID, node_id="s1", index=2, publish=True)


# ── set-title / set-description (localized) ───────────────────────────────────

# A node that already carries a localized <Titles> and a child Group, so the
# in-place update and the Descriptions-ordering paths can be exercised.
_TITLED_SEED = (
    '<SiteMap>'
    '<Area Id="SFA" ResourceId="Area_Sales">'
    '<Titles><Title LCID="1033" Title="Sales" /></Titles>'
    '<Group Id="SFA_Grp"><SubArea Id="nav_accts" Entity="account" /></Group>'
    '</Area></SiteMap>'
)


def _langs_url(backend) -> str:
    return backend.url_for("RetrieveProvisionedLanguages()")


def _with_langs(m: requests_mock.Mocker, backend, *lcids: int) -> None:
    """Stub the live installed-languages probe (defaults to en-US + de-DE)."""
    m.get(_langs_url(backend),
          json={"RetrieveProvisionedLanguages": list(lcids or (1033, 1031))})


def _titles(node: ET.Element) -> list[tuple[str, str]]:
    """(LCID, Title) pairs of a node's <Titles>/<Title> children."""
    container = node.find("Titles")
    return [] if container is None else [
        (t.get("LCID") or "", t.get("Title") or "") for t in container]


class TestSetTitle:
    def test_creates_titles_container_before_child_nodes(self, backend):
        # SFA already has a <Titles> and a <Group>; add a second language and
        # assert <Titles> stays the first child (ahead of <Group>).
        with requests_mock.Mocker() as m:
            _with_langs(m, backend)
            m.get(_url(backend), json={"sitemapxml": _TITLED_SEED})
            m.patch(_url(backend), status_code=204)
            sm.set_title(backend, _SID, node_id="SFA", titles=[(1031, "Vertrieb")])
            area = sm._find(_patched_root(m), "Area", "SFA")
        assert area is not None
        assert area[0].tag == "Titles"  # container leads the child sequence
        assert ("1031", "Vertrieb") in _titles(area)
        # the pre-existing language is preserved
        assert ("1033", "Sales") in _titles(area)

    def test_creates_titles_on_node_without_one(self, backend):
        # nav_accts (a SubArea) has no <Titles> yet → one is created at index 0.
        with requests_mock.Mocker() as m:
            _with_langs(m, backend)
            m.get(_url(backend), json={"sitemapxml": _TITLED_SEED})
            m.patch(_url(backend), status_code=204)
            sm.set_title(backend, _SID, node_id="nav_accts",
                         titles=[(1033, "Accounts")])
            sub = sm._find(_patched_root(m), "SubArea", "nav_accts")
        assert sub is not None and sub[0].tag == "Titles"
        assert _titles(sub) == [("1033", "Accounts")]

    def test_updates_existing_lcid_in_place_no_duplicate(self, backend):
        with requests_mock.Mocker() as m:
            _with_langs(m, backend)
            m.get(_url(backend), json={"sitemapxml": _TITLED_SEED})
            m.patch(_url(backend), status_code=204)
            sm.set_title(backend, _SID, node_id="SFA", titles=[(1033, "Selling")])
            area = sm._find(_patched_root(m), "Area", "SFA")
        assert area is not None
        # exactly one <Title> for 1033, with the new text (no shadow sibling)
        assert _titles(area) == [("1033", "Selling")]

    def test_two_lcids_in_one_call_become_siblings(self, backend):
        with requests_mock.Mocker() as m:
            _with_langs(m, backend, 1033, 1031)
            m.get(_url(backend), json={"sitemapxml": _SEED})
            m.patch(_url(backend), status_code=204)
            sm.set_title(backend, _SID, node_id="HLP",
                         titles=[(1033, "Help"), (1031, "Hilfe")])
            area = sm._find(_patched_root(m), "Area", "HLP")
        assert area is not None
        assert set(_titles(area)) == {("1033", "Help"), ("1031", "Hilfe")}

    def test_resourceid_untouched(self, backend):
        with requests_mock.Mocker() as m:
            _with_langs(m, backend)
            m.get(_url(backend), json={"sitemapxml": _TITLED_SEED})
            m.patch(_url(backend), status_code=204)
            sm.set_title(backend, _SID, node_id="SFA", titles=[(1033, "x")])
            area = sm._find(_patched_root(m), "Area", "SFA")
        assert area is not None and area.get("ResourceId") == "Area_Sales"

    def test_duplicate_lcid_in_one_call_rejected(self, backend):
        with requests_mock.Mocker() as m:
            _with_langs(m, backend)
            m.get(_url(backend), json={"sitemapxml": _SEED})
            with pytest.raises(D365Error, match="duplicate --lcid 1033"):
                sm.set_title(backend, _SID, node_id="HLP",
                             titles=[(1033, "Help"), (1033, "Again")])
            assert not any(r.method == "PATCH" for r in m.request_history)

    def test_uninstalled_lcid_rejected(self, backend):
        with requests_mock.Mocker() as m:
            _with_langs(m, backend, 1033)  # only en-US installed
            m.get(_url(backend), json={"sitemapxml": _SEED})
            with pytest.raises(D365Error, match="not installed languages"):
                sm.set_title(backend, _SID, node_id="HLP", titles=[(1031, "Hilfe")])
            assert not any(r.method == "PATCH" for r in m.request_history)

    def test_empty_title_rejected(self, backend):
        with requests_mock.Mocker() as m:
            _with_langs(m, backend)
            with pytest.raises(D365Error, match="must not be empty"):
                sm.set_title(backend, _SID, node_id="HLP", titles=[(1033, "   ")])

    @pytest.mark.parametrize("bad_lcid", [99, 999, 10000, 0])
    def test_non_four_digit_lcid_rejected(self, backend, bad_lcid):
        # Rejected up front (no live probe needed) — the spec requires a 4-digit
        # locale ID; a clearer error than a downstream "not installed".
        with pytest.raises(D365Error, match="4-digit locale ID"):
            sm.set_title(backend, _SID, node_id="HLP", titles=[(bad_lcid, "x")])

    def test_titles_splice_ahead_of_commented_out_child(self, backend):
        # A node whose only child is a --comment-out soft-delete: a new <Titles>
        # must still land first and the order check must ignore the comment.
        seed = (
            '<SiteMap><Area Id="X"><Group Id="G">'
            '<!--<SubArea Id="old" Entity="account" />--></Group></Area></SiteMap>')
        with requests_mock.Mocker() as m:
            _with_langs(m, backend)
            m.get(_url(backend), json={"sitemapxml": seed})
            m.patch(_url(backend), status_code=204)
            sm.set_title(backend, _SID, node_id="G", titles=[(1033, "Grp")])
            grp = sm._find(_patched_root(m), "Group", "G")
        assert grp is not None and grp[0].tag == "Titles"
        assert sm._child_order_ok(grp)

    def test_no_entries_rejected(self, backend):
        with requests_mock.Mocker() as m:
            _with_langs(m, backend)
            with pytest.raises(D365Error, match="at least one"):
                sm.set_title(backend, _SID, node_id="HLP", titles=[])

    def test_unknown_node_rejected(self, backend):
        with requests_mock.Mocker() as m:
            _with_langs(m, backend)
            m.get(_url(backend), json={"sitemapxml": _SEED})
            with pytest.raises(D365Error, match="no Area, Group or SubArea"):
                sm.set_title(backend, _SID, node_id="ghost", titles=[(1033, "x")])

    def test_dry_run_previews_without_writing(self, dry_backend):
        with requests_mock.Mocker() as m:
            _with_langs(m, dry_backend)
            m.get(_url(dry_backend), json={"sitemapxml": _SEED})
            out = sm.set_title(dry_backend, _SID, node_id="HLP",
                               titles=[(1033, "Help")])
            assert not any(r.method == "PATCH" for r in m.request_history)
        assert out["_dry_run"] is True and out["would_edit"] is True
        area = sm._find(ET.fromstring(out["sitemapxml"]), "Area", "HLP")
        assert area is not None and _titles(area) == [("1033", "Help")]

    def test_publish_runs_t3_read_back(self, backend):
        published = _SEED.replace(
            '<Area Id="HLP" ResourceId="Area_Help">',
            '<Area Id="HLP" ResourceId="Area_Help">'
            '<Titles><Title LCID="1033" Title="Help" /></Titles>')
        with requests_mock.Mocker() as m:
            _with_langs(m, backend)
            m.get(_url(backend), [
                {"json": {"sitemapxml": _SEED}},
                {"json": {"sitemapxml": published}}])
            m.patch(_url(backend), status_code=204)
            m.post(backend.url_for("PublishAllXml"), status_code=204)
            out = sm.set_title(backend, _SID, node_id="HLP",
                               titles=[(1033, "Help")], publish=True)
        assert out["updated"] is True and out["published"] is True

    def test_t3_read_back_fails_when_title_absent(self, backend):
        with requests_mock.Mocker() as m:
            _with_langs(m, backend)
            m.get(_url(backend), [
                {"json": {"sitemapxml": _SEED}},
                {"json": {"sitemapxml": _SEED}}])  # published layer still untitled
            m.patch(_url(backend), status_code=204)
            m.post(backend.url_for("PublishAllXml"), status_code=204)
            with pytest.raises(D365Error, match="read-back"):
                sm.set_title(backend, _SID, node_id="HLP",
                             titles=[(1033, "Help")], publish=True)


class TestSetDescription:
    def test_description_lands_after_titles_before_child_nodes(self, backend):
        # SFA: <Titles> then <Group>. A new <Descriptions> must splice between
        # them (Titles → Descriptions → child nodes).
        with requests_mock.Mocker() as m:
            _with_langs(m, backend)
            m.get(_url(backend), json={"sitemapxml": _TITLED_SEED})
            m.patch(_url(backend), status_code=204)
            sm.set_description(backend, _SID, node_id="SFA",
                               descriptions=[(1033, "Sales area")])
            area = sm._find(_patched_root(m), "Area", "SFA")
        assert area is not None
        tags = [c.tag for c in area]
        assert tags == ["Titles", "Descriptions", "Group"]
        desc = area.find("Descriptions")
        assert desc is not None
        assert [(d.get("LCID"), d.get("Description")) for d in desc] == [
            ("1033", "Sales area")]

    def test_creates_descriptions_when_no_titles(self, backend):
        # HLP has neither <Titles> nor <Descriptions> but has a <Group>; the new
        # <Descriptions> still lands ahead of the child node.
        with requests_mock.Mocker() as m:
            _with_langs(m, backend)
            m.get(_url(backend), json={"sitemapxml": _SEED})
            m.patch(_url(backend), status_code=204)
            sm.set_description(backend, _SID, node_id="HLP",
                               descriptions=[(1033, "Help area")])
            area = sm._find(_patched_root(m), "Area", "HLP")
        assert area is not None
        assert [c.tag for c in area] == ["Descriptions", "Group"]


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
