"""Live read-modify-write editors for an existing SiteMap's navigation tree.

``app build-sitemap`` / ``app set-sitemap`` POST a *whole* new ``SiteMapXml``;
this module is the complementary in-place editor — surgically splice an Area,
Group or SubArea into a **live** sitemap, or remove a node, without re-authoring
the whole document. It is the first user of the read-modify-write seam every
later SiteMap slice (node move, title/description) reuses: GET
``sitemaps({id})?$select=sitemapxml`` → mutate the parsed tree → PATCH the column
back through the shared :mod:`crm.core.xml_edit` commit, then publish and read
the result back (T3).

SiteMap-specific safety rules (verified live, on-prem v9.1 + cloud):

- A new node ``Id`` matches ``[a-zA-Z0-9_]+`` and is unique across the whole
  document (every Area / Group / SubArea Id) — matching ``build_sitemapxml`` and
  keeping ``remove-node``'s by-Id targeting unambiguous.
- ``ResourceId`` (the localized-label pointer) and ``IntroducedVersion`` (the
  solution-version stamp) are platform-owned: new nodes carry neither, so the
  editors never write or mutate them (the "never touch" rule holds by
  construction — a new node's visible label comes from a plain ``Title``).
- A SubArea's content is **exactly one** of ``Entity`` (a table — its logical
  name is validated to exist, since a dangling ``Entity=`` silently hides the
  node), ``Url`` (any link, including an HTML web resource — there is **no**
  SubArea ``WebResource`` attribute; ``$webresource:`` is the *Icon* directive
  only), or ``DefaultDashboard`` (a dashboard GUID).
- No internal GUIDs are minted, so the #275 form-clone collision class is absent
  by construction; a ``--dashboard`` reference is an external, caller-supplied id.

The T3 read-back (only meaningful after publish — a Web API GET returns the
published layer) asserts the post-edit node-Id set equals the pre-edit set plus
the added Id (or minus the removed subtree), and that the spliced node landed
under its named parent / the removed node is gone.
"""

from __future__ import annotations

import re
import xml.etree.ElementTree as ET
from typing import Any, Callable

from crm.core import xml_edit
from crm.core.entity_names import resolve_logical_name
from crm.utils.d365_backend import D365Backend, D365Error, as_dict, normalize_guid

_SITEMAP_SET = "sitemaps"
_SITEMAP_COLUMN = "sitemapxml"
_NODE_TAGS = ("Area", "Group", "SubArea")

# The SiteMap nav-id grammar (matches stock ids like ``HLP``, ``HLP_GRP``,
# ``nav_template``). A publisher prefix is recommended but not enforced.
_NODE_ID_RE = re.compile(r"^[a-zA-Z0-9_]+$")


class _Mutation:
    """The outcome of a tree edit, carrying everything the T3 read-back needs.

    ``added`` / ``removed`` are the node-Id deltas (``removed`` includes a removed
    subtree's descendants); ``warning`` is an optional advisory (e.g. a cascade);
    ``verify`` runs the node-present / node-absent assertion against the
    re-parsed, published tree.
    """

    def __init__(
        self,
        *,
        added: set[str],
        removed: set[str],
        verify: Callable[[ET.Element], None],
        warning: str | None = None,
    ) -> None:
        self.added = added
        self.removed = removed
        self.verify = verify
        self.warning = warning


# --- tree helpers ---------------------------------------------------------------


def _node_ids(root: ET.Element) -> set[str]:
    """Every Area / Group / SubArea ``Id`` in ``root`` (live nodes only — a
    commented-out node is not matched by ``iter``)."""
    ids: set[str] = set()
    for tag in _NODE_TAGS:
        for el in root.iter(tag):
            nid = el.get("Id")
            if nid:
                ids.add(nid)
    return ids


def _find(root: ET.Element, tag: str, node_id: str) -> ET.Element | None:
    for el in root.iter(tag):
        if el.get("Id") == node_id:
            return el
    return None


def _require_unique(root: ET.Element, node_id: str) -> None:
    """Reject a new node Id that collides with **any** existing node.

    Node Ids are unique across the whole document (not just within their type or
    parent), matching ``build_sitemapxml`` and — crucially — keeping
    ``remove-node``'s by-Id targeting unambiguous: two nodes sharing an Id would
    make a removal pick one arbitrarily.
    """
    if node_id in _node_ids(root):
        raise D365Error(f"node id {node_id!r} already exists in the sitemap.")


def _validate_node_id(node_id: str, *, kind: str) -> str:
    nid = node_id.strip()
    if not nid:
        raise D365Error(f"{kind} id must not be empty.")
    if not _NODE_ID_RE.match(nid):
        raise D365Error(
            f"{kind} id {nid!r} is invalid: ids must match [a-zA-Z0-9_]+ "
            "(a publisher prefix is recommended).")
    return nid


def _safe_comment(text: str) -> str:
    """Make ``text`` safe inside an XML comment: no ``--`` run and no trailing
    ``-`` (both make ``<!-- ... -->`` malformed). A commented-out node only needs
    to round-trip as text, so spacing the dashes is harmless."""
    text = re.sub(r"-(?=-)", "- ", text)
    return text + " " if text.endswith("-") else text


def _load(backend: D365Backend, sitemap_id: str) -> tuple[str, str]:
    """Resolve ``sitemap_id`` to a GUID and GET its current ``sitemapxml``."""
    rid = normalize_guid(sitemap_id)
    if rid is None:
        raise D365Error(f"Invalid sitemap id (expected GUID): {sitemap_id!r}")
    row = as_dict(backend.get(
        f"{_SITEMAP_SET}({rid})", params={"$select": _SITEMAP_COLUMN}))
    xml = str(row.get(_SITEMAP_COLUMN) or "")
    if not xml.strip():
        raise D365Error(f"sitemap {rid} has no {_SITEMAP_COLUMN} to edit.")
    return rid, xml


def _edit(
    backend: D365Backend,
    sitemap_id: str,
    *,
    action: str,
    extra: dict[str, Any],
    mutate: Callable[[ET.Element], _Mutation],
    publish: bool,
    solution: str | None,
) -> dict[str, Any]:
    """The shared SiteMap read-modify-write: GET → parse → ``mutate`` → PATCH.

    Reuses :func:`xml_edit.commit_xml_patch` for the dry-run / PATCH / publish
    mechanics and wires a T3 read-back (node-Id-set delta + node present/absent)
    when — and only when — publishing, since a pre-publish GET returns the stale
    published layer and would false-negative.
    """
    rid, before_xml = _load(backend, sitemap_id)
    root = xml_edit.parse_xml(before_xml, label="SiteMapXml")
    before_ids = _node_ids(root)
    mut = mutate(root)
    after_xml = xml_edit.serialize_xml(root)

    result: dict[str, Any] = {"sitemapid": rid, "action": action}
    result.update({k: v for k, v in extra.items() if v is not None})
    if mut.warning:
        result["cascade_warning"] = mut.warning
    if backend.dry_run:
        result["sitemapxml"] = after_xml

    def _read_back(returned: str) -> None:
        rb = xml_edit.parse_xml(returned, label="returned SiteMapXml")
        expected = (before_ids | mut.added) - mut.removed
        if _node_ids(rb) != expected:
            raise D365Error(
                "SiteMap read-back (T3) failed: the published node-Id set does "
                "not match the expected set after the edit.")
        mut.verify(rb)

    return xml_edit.commit_xml_patch(
        backend, entity_set=_SITEMAP_SET, record_id=rid, column=_SITEMAP_COLUMN,
        new_xml=after_xml, result=result, dry_run_flag="would_edit",
        publish=publish, solution=solution,
        read_back=_read_back if publish else None)


# --- add-area -------------------------------------------------------------------


def add_area(
    backend: D365Backend,
    sitemap_id: str,
    *,
    area_id: str,
    title: str,
    icon: str | None = None,
    show_groups: bool = False,
    publish: bool = False,
    solution: str | None = None,
) -> dict[str, Any]:
    """Splice a new ``<Area>`` (with a plain ``Title``) into the sitemap."""
    aid = _validate_node_id(area_id, kind="Area")

    def mutate(root: ET.Element) -> _Mutation:
        _require_unique(root, aid)
        attrs = {"Id": aid, "Title": title}
        if show_groups:
            attrs["ShowGroups"] = "true"
        if icon:
            attrs["Icon"] = icon
        ET.SubElement(root, "Area", attrs)

        def verify(rb: ET.Element) -> None:
            if not xml_edit.node_present(rb, "Area", Id=aid):
                raise D365Error(f"read-back: Area {aid!r} absent after publish.")

        return _Mutation(added={aid}, removed=set(), verify=verify)

    return _edit(backend, sitemap_id, action="add-area",
                 extra={"area_id": aid, "title": title},
                 mutate=mutate, publish=publish, solution=solution)


# --- add-group ------------------------------------------------------------------


def add_group(
    backend: D365Backend,
    sitemap_id: str,
    *,
    area_id: str,
    group_id: str,
    title: str,
    publish: bool = False,
    solution: str | None = None,
) -> dict[str, Any]:
    """Splice a new ``<Group>`` under an Area (Id unique across the document)."""
    aid = area_id.strip()
    gid = _validate_node_id(group_id, kind="Group")

    def mutate(root: ET.Element) -> _Mutation:
        area = _find(root, "Area", aid)
        if area is None:
            raise D365Error(f"parent Area {aid!r} not found in the sitemap.")
        _require_unique(root, gid)
        ET.SubElement(area, "Group", {"Id": gid, "Title": title})

        def verify(rb: ET.Element) -> None:
            # parent-aware: the new Group must land *under the named Area*, not
            # merely exist somewhere in the document.
            area_rb = _find(rb, "Area", aid)
            if area_rb is None or _find(area_rb, "Group", gid) is None:
                raise D365Error(
                    f"read-back: Group {gid!r} not under Area {aid!r} after "
                    "publish.")

        return _Mutation(added={gid}, removed=set(), verify=verify)

    return _edit(backend, sitemap_id, action="add-group",
                 extra={"area_id": aid, "group_id": gid, "title": title},
                 mutate=mutate, publish=publish, solution=solution)


# --- add-subarea ----------------------------------------------------------------


def _subarea_content(
    backend: D365Backend,
    *,
    entity: str | None,
    url: str | None,
    dashboard: str | None,
) -> tuple[str, str, str]:
    """Resolve the exactly-one-of content mode to ``(cli_key, attribute, value)``.

    ``cli_key`` is the CLI-facing flag name (``entity`` / ``url`` / ``dashboard``)
    the result echoes back; ``attribute`` is the SiteMap XML attribute it maps to
    (``Entity`` / ``Url`` / ``DefaultDashboard``). ``--entity`` is validated to
    exist (a dangling ``Entity=`` silently hides the node); ``--dashboard`` is
    validated as a GUID. Raises if zero or more than one mode is supplied.
    """
    chosen = [(flag, val) for flag, val in
              (("entity", entity), ("url", url), ("dashboard", dashboard))
              if val]
    if len(chosen) != 1:
        raise D365Error(
            "add-subarea needs exactly one of --entity, --url or --dashboard "
            f"(got {len(chosen)}).")
    flag, value = chosen[0]
    if flag == "entity":
        # Resolve raises D365Error (with a close-match suggestion) on a miss.
        return "entity", "Entity", resolve_logical_name(backend, value)
    if flag == "url":
        return "url", "Url", value
    guid = normalize_guid(value)
    if guid is None:
        raise D365Error(f"--dashboard must be a dashboard GUID: {value!r}")
    return "dashboard", "DefaultDashboard", guid


def add_subarea(
    backend: D365Backend,
    sitemap_id: str,
    *,
    area_id: str,
    group_id: str,
    sub_id: str,
    entity: str | None = None,
    url: str | None = None,
    dashboard: str | None = None,
    title: str | None = None,
    icon: str | None = None,
    publish: bool = False,
    solution: str | None = None,
) -> dict[str, Any]:
    """Splice a new ``<SubArea>`` under a Group (exactly-one-of content mode)."""
    sid = _validate_node_id(sub_id, kind="SubArea")
    aid = area_id.strip()
    gid = group_id.strip()
    cli_key, content_attr, content_val = _subarea_content(
        backend, entity=entity, url=url, dashboard=dashboard)

    def mutate(root: ET.Element) -> _Mutation:
        area = _find(root, "Area", aid)
        if area is None:
            raise D365Error(f"parent Area {aid!r} not found in the sitemap.")
        group = _find(area, "Group", gid)
        if group is None:
            raise D365Error(
                f"parent Group {gid!r} not found in Area {aid!r}.")
        _require_unique(root, sid)
        attrs = {"Id": sid, content_attr: content_val}
        if title:
            attrs["Title"] = title
        if icon:
            attrs["Icon"] = icon
        ET.SubElement(group, "SubArea", attrs)

        def verify(rb: ET.Element) -> None:
            # parent-aware: the new SubArea must land under the named Group.
            area_rb = _find(rb, "Area", aid)
            group_rb = _find(area_rb, "Group", gid) if area_rb is not None else None
            if group_rb is None or _find(group_rb, "SubArea", sid) is None:
                raise D365Error(
                    f"read-back: SubArea {sid!r} not under {aid}/{gid} after "
                    "publish.")

        return _Mutation(added={sid}, removed=set(), verify=verify)

    return _edit(backend, sitemap_id, action="add-subarea",
                 extra={"area_id": aid, "group_id": gid, "sub_id": sid,
                        cli_key: content_val},
                 mutate=mutate, publish=publish, solution=solution)


# --- remove-node ----------------------------------------------------------------


def remove_node(
    backend: D365Backend,
    sitemap_id: str,
    *,
    node_id: str,
    comment_out: bool = False,
    publish: bool = False,
    solution: str | None = None,
) -> dict[str, Any]:
    """Remove (or comment out) the Area / Group / SubArea with ``node_id``.

    Removing an Area or Group cascades to its descendants — a warning names how
    many. With ``comment_out`` the node is replaced by a (well-formed) XML comment
    of itself rather than deleted outright.
    """
    nid = node_id.strip()
    if not nid:
        raise D365Error("remove-node --id must not be empty.")

    def mutate(root: ET.Element) -> _Mutation:
        parents = {child: parent for parent in root.iter() for child in parent}
        target: ET.Element | None = None
        tag = ""
        for candidate in _NODE_TAGS:
            target = _find(root, candidate, nid)
            if target is not None:
                tag = candidate
                break
        if target is None:
            raise D365Error(
                f"no Area, Group or SubArea with Id {nid!r} in the sitemap.")
        parent = parents.get(target)
        if parent is None:  # unreachable: a node Id never sits on the root
            raise D365Error("cannot remove the SiteMap root element.")

        removed = _node_ids(target)
        warning: str | None = None
        descendants = len(removed) - 1
        if tag in ("Area", "Group") and descendants:
            warning = (
                f"removing {tag} {nid!r} also removes {descendants} descendant "
                "node(s).")

        index = list(parent).index(target)
        parent.remove(target)
        if comment_out:
            parent.insert(
                index, ET.Comment(_safe_comment(xml_edit.serialize_xml(target))))

        def verify(rb: ET.Element) -> None:
            if nid in _node_ids(rb):
                raise D365Error(
                    f"read-back: node {nid!r} still present after publish.")

        return _Mutation(added=set(), removed=removed, verify=verify,
                         warning=warning)

    return _edit(backend, sitemap_id, action="remove-node",
                 extra={"node_id": nid, "comment_out": comment_out},
                 mutate=mutate, publish=publish, solution=solution)
