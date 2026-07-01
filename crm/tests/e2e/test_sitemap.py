# pyright: basic
"""E2E tests for the live SiteMap editors: add-area / add-group / add-subarea /
remove-node (the GET → mutate → PATCH read-modify-write path).

The lifecycle test creates a throwaway, app-unassociated ``sitemaps`` row (a
seed Area/Group/SubArea — a sitemap must ship a non-empty Group), then splices
and removes nodes against it **with --publish**, so the editors' publish-gated
T3 read-back is exercised on the live target (on-prem v9.x returns the *stale*
published layer on a pre-publish GET — see the safe-xml-editors feasibility
§8.1; a cloud-only run would mask that). The row is deleted at the end.
"""
from __future__ import annotations

import json
import xml.etree.ElementTree as ET

import pytest

from crm.tests.e2e.coverage import covers


def _data(result):
    assert result.returncode == 0, f"failed:\n{result.stderr}\n{result.stdout}"
    env = json.loads(result.stdout)
    assert env["ok"], env
    return env["data"]


def _sitemap_raw(cli, sitemap_id: str) -> str:
    """Re-GET the live sitemapxml string (the T3 read-back surface)."""
    got = _data(cli(["--json", "query", "odata",
                     f"sitemaps({sitemap_id})", "--select", "sitemapxml"]))
    return got["sitemapxml"]


def _sitemapxml(cli, sitemap_id: str) -> ET.Element:
    return ET.fromstring(_sitemap_raw(cli, sitemap_id))


def _has(root: ET.Element, tag: str, node_id: str) -> bool:
    return any(el.get("Id") == node_id for el in root.iter(tag))


@covers("sitemap add-area", "sitemap add-group", "sitemap add-subarea",
        "sitemap move-node", "sitemap remove-node")
@pytest.mark.slow
def test_sitemap_live_edit_lifecycle(cli, unique, ephemeral_solution):
    """Build a throwaway sitemap, then add/remove nav nodes over the live RMW
    path and assert each edit lands on the published layer (T3)."""
    name = f"E2E SiteMap {unique}"
    uniq = f"cwx_e2e_{unique}"
    created = _data(cli([
        "--json", "app", "build-sitemap", name,
        "--area", "cwxarea:CWX Area",
        "--group", "cwxarea/cwxgrp:CWX Group",
        "--subarea", "cwxarea/cwxgrp:entity=account:Accounts",
        "--unique-name", uniq, "--no-publish", "--solution", ephemeral_solution]))
    sitemap_id = created["sitemapid"]
    assert sitemap_id, created

    try:
        # add-area (publish → T3 read-back inside the verb)
        _data(cli(["--json", "sitemap", "add-area", sitemap_id,
                   "--id", "cwx_ops", "--title", "Operations", "--publish",
                   "--solution", ephemeral_solution]))
        # add-group under the new area
        _data(cli(["--json", "sitemap", "add-group", sitemap_id,
                   "--area", "cwx_ops", "--id", "cwx_opsgrp",
                   "--title", "Ops Group", "--publish",
                   "--solution", ephemeral_solution]))
        # add-subarea binding a real table (entity validated to exist live)
        _data(cli(["--json", "sitemap", "add-subarea", sitemap_id,
                   "--area", "cwx_ops", "--group", "cwx_opsgrp",
                   "--id", "cwx_opscontacts", "--entity", "contact",
                   "--title", "Contacts", "--publish",
                   "--solution", ephemeral_solution]))
        # a second SubArea, so move-node has a sibling to reorder against
        _data(cli(["--json", "sitemap", "add-subarea", sitemap_id,
                   "--area", "cwx_ops", "--group", "cwx_opsgrp",
                   "--id", "cwx_opsaccts", "--entity", "account",
                   "--title", "Accounts", "--publish",
                   "--solution", ephemeral_solution]))

        root = _sitemapxml(cli, sitemap_id)
        assert _has(root, "Area", "cwx_ops"), ET.tostring(root, "unicode")
        assert _has(root, "Group", "cwx_opsgrp")
        assert _has(root, "SubArea", "cwx_opscontacts")
        # the entity bind landed and no spurious WebResource attribute was emitted
        sub = next(s for s in root.iter("SubArea")
                   if s.get("Id") == "cwx_opscontacts")
        assert sub.get("Entity") == "contact"
        assert sub.get("WebResource") is None

        # add order under the group is [contacts, accts]
        grp = next(g for g in root.iter("Group") if g.get("Id") == "cwx_opsgrp")
        order = [s.get("Id") for s in grp if s.tag == "SubArea"]
        assert order == ["cwx_opscontacts", "cwx_opsaccts"], order

        # move-node (publish → T3 asserts the new order on the published layer):
        # reorder contacts after accts.
        _data(cli(["--json", "sitemap", "move-node", sitemap_id,
                   "--id", "cwx_opscontacts", "--after", "cwx_opsaccts",
                   "--publish", "--solution", ephemeral_solution]))
        root = _sitemapxml(cli, sitemap_id)
        grp = next(g for g in root.iter("Group") if g.get("Id") == "cwx_opsgrp")
        order = [s.get("Id") for s in grp if s.tag == "SubArea"]
        assert order == ["cwx_opsaccts", "cwx_opscontacts"], order
        # the moved node kept its attributes (pure permutation)
        sub = next(s for s in grp if s.get("Id") == "cwx_opscontacts")
        assert sub.get("Entity") == "contact"

        # tidy the extra SubArea before tearing down the rest
        _data(cli(["--json", "sitemap", "remove-node", sitemap_id,
                   "--id", "cwx_opsaccts", "--publish",
                   "--solution", ephemeral_solution]))

        # remove-node (publish → T3 asserts absence on the published layer)
        _data(cli(["--json", "sitemap", "remove-node", sitemap_id,
                   "--id", "cwx_opscontacts", "--publish",
                   "--solution", ephemeral_solution]))
        root = _sitemapxml(cli, sitemap_id)
        assert not _has(root, "SubArea", "cwx_opscontacts")
        assert _has(root, "Group", "cwx_opsgrp")  # parent survives

        # remove-node --comment-out (publish → soft-delete must stay well-formed)
        _data(cli(["--json", "sitemap", "remove-node", sitemap_id,
                   "--id", "cwx_opsgrp", "--comment-out", "--publish",
                   "--solution", ephemeral_solution]))
        raw = _sitemap_raw(cli, sitemap_id)
        ET.fromstring(raw)  # the commented node round-trips (well-formed)
        assert not _has(ET.fromstring(raw), "Group", "cwx_opsgrp")  # not live
        assert "cwx_opsgrp" in raw  # survives only inside the XML comment
    finally:
        deleted = _data(cli(["--json", "entity", "delete", "sitemaps",
                             sitemap_id, "--yes"]))
        assert deleted["deleted"] is True


# Not @requires_cloud / @requires_onprem — localized <Titles>/<Descriptions>
# editing is pure SiteMapXml surgery over the publish-gated RMW seam, identical
# on both targets; CI runs it on each leg (union coverage).
@covers("sitemap set-title", "sitemap set-description")
@pytest.mark.slow
def test_sitemap_set_localized_title_description(cli, unique, ephemeral_solution):
    """Set a localized <Title>/<Description> on a nav node over the live RMW
    path and assert the published layer carries them in schema-valid element
    order (Titles → Descriptions → child nodes), one entry per LCID."""
    name = f"E2E SiteMap L10n {unique}"
    uniq = f"cwx_l10n_{unique}"
    created = _data(cli([
        "--json", "app", "build-sitemap", name,
        "--area", "cwxarea:CWX Area",
        "--group", "cwxarea/cwxgrp:CWX Group",
        "--subarea", "cwxarea/cwxgrp:entity=account:Accounts",
        "--unique-name", uniq, "--no-publish", "--solution", ephemeral_solution]))
    sitemap_id = created["sitemapid"]
    assert sitemap_id, created

    try:
        # 1033 (en-US) is the base language on the test orgs; set a localized
        # title + description on the Area (which has a Group child).
        _data(cli(["--json", "sitemap", "set-title", sitemap_id,
                   "--id", "cwxarea", "--lcid", "1033", "--title", "Sales L10n",
                   "--publish", "--solution", ephemeral_solution]))
        _data(cli(["--json", "sitemap", "set-description", sitemap_id,
                   "--id", "cwxarea", "--lcid", "1033",
                   "--description", "Sales area", "--publish",
                   "--solution", ephemeral_solution]))

        area = next(a for a in _sitemapxml(cli, sitemap_id).iter("Area")
                    if a.get("Id") == "cwxarea")
        # T3: containers spliced ahead of the child node, in schema order
        tags = [c.tag for c in area if isinstance(c.tag, str)]
        assert tags == ["Titles", "Descriptions", "Group"], tags
        title = area.find("Titles/Title")
        assert title is not None
        assert title.get("LCID") == "1033" and title.get("Title") == "Sales L10n"
        desc = area.find("Descriptions/Description")
        assert desc is not None
        assert desc.get("LCID") == "1033" and desc.get("Description") == "Sales area"

        # re-setting the same LCID updates in place — no duplicate <Title>
        _data(cli(["--json", "sitemap", "set-title", sitemap_id,
                   "--id", "cwxarea", "--lcid", "1033", "--title", "Selling",
                   "--publish", "--solution", ephemeral_solution]))
        area = next(a for a in _sitemapxml(cli, sitemap_id).iter("Area")
                    if a.get("Id") == "cwxarea")
        assert [t.get("Title") for t in area.findall("Titles/Title")] == [
            "Selling"], ET.tostring(area, "unicode")
    finally:
        deleted = _data(cli(["--json", "entity", "delete", "sitemaps",
                             sitemap_id, "--yes"]))
        assert deleted["deleted"] is True
