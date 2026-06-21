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
from crm.core.ribbon import retrieve_provisioned_languages
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


# --- move-node ------------------------------------------------------------------


def _locate(root: ET.Element, node_id: str) -> tuple[ET.Element | None, str]:
    """Find the Area / Group / SubArea with ``node_id`` (Ids are document-unique)."""
    for tag in _NODE_TAGS:
        el = _find(root, tag, node_id)
        if el is not None:
            return el, tag
    return None, ""


def move_node(
    backend: D365Backend,
    sitemap_id: str,
    *,
    node_id: str,
    before: str | None = None,
    after: str | None = None,
    index: int | None = None,
    publish: bool = False,
    solution: str | None = None,
) -> dict[str, Any]:
    """Reorder the Area / Group / SubArea ``node_id`` within its parent.

    Exactly one destination is given: ``before`` / ``after`` an anchor sibling, or
    a 0-based ``index`` among the parent's same-type children. The anchor must
    share the moved node's parent **and** node type; ``index`` must be in range.
    This is a pure permutation — the moved node's attributes and children are
    never touched, only its position among its siblings.
    """
    nid = node_id.strip()
    if not nid:
        raise D365Error("move-node --id must not be empty.")
    chosen = [k for k, v in (("before", before), ("after", after),
                             ("index", index)) if v is not None]
    if len(chosen) != 1:
        raise D365Error(
            "move-node needs exactly one of --before, --after or --index "
            f"(got {len(chosen)}).")

    def mutate(root: ET.Element) -> _Mutation:
        parents = {child: parent for parent in root.iter() for child in parent}
        target, tag = _locate(root, nid)
        if target is None:
            raise D365Error(
                f"no Area, Group or SubArea with Id {nid!r} in the sitemap.")
        parent = parents.get(target)
        if parent is None:  # unreachable: a node Id never sits on the root
            raise D365Error("cannot move the SiteMap root element.")

        if index is not None:
            count = sum(1 for c in parent if c.tag == tag)
            if not 0 <= index < count:
                raise D365Error(
                    f"--index {index} is out of range for {tag} {nid!r}: the "
                    f"parent has {count} {tag} child(ren) (valid 0..{count - 1}).")
            parent.remove(target)
            remaining = [c for c in parent if c.tag == tag]
            if index < len(remaining):
                parent.insert(list(parent).index(remaining[index]), target)
            elif remaining:
                parent.insert(list(parent).index(remaining[-1]) + 1, target)
            else:
                parent.append(target)
        else:
            flag = "before" if before is not None else "after"
            anchor_id = (before if before is not None else after or "").strip()
            anchor, anchor_tag = _locate(root, anchor_id)
            if anchor is None:
                raise D365Error(
                    f"anchor node {anchor_id!r} not found in the sitemap.")
            if anchor is target:
                raise D365Error(
                    f"--{flag} {anchor_id!r} cannot be the node being moved.")
            if anchor_tag != tag:
                raise D365Error(
                    f"anchor {anchor_id!r} is a {anchor_tag}, not a {tag}; move "
                    f"requires an anchor of the same node type as {nid!r}.")
            if parents.get(anchor) is not parent:
                raise D365Error(
                    f"anchor {anchor_id!r} is not a sibling of {nid!r} (they "
                    "must share the same parent).")
            parent.remove(target)
            pos = list(parent).index(anchor)
            parent.insert(pos if flag == "before" else pos + 1, target)

        # Siblings of a node are always its own type — the SiteMap hierarchy is
        # strict (SiteMap→Area→Group→SubArea), so a parent's direct children are
        # homogeneous. Filtering by ``tag`` keeps the order check to same-type
        # siblings, matching the move semantics.
        expected = [c.get("Id") for c in parent if c.tag == tag and c.get("Id")]
        parent_tag, parent_id = parent.tag, parent.get("Id")

        def verify(rb: ET.Element) -> None:
            # parent is the SiteMap root (moving an Area) → it carries no Id.
            parent_rb = rb if parent_id is None else _find(rb, parent_tag, parent_id)
            if parent_rb is None:
                raise D365Error(
                    f"read-back: parent {parent_tag} {parent_id!r} missing after "
                    "publish.")
            order = [c.get("Id") for c in parent_rb if c.tag == tag and c.get("Id")]
            if order != expected:
                raise D365Error(
                    f"read-back: {tag} {nid!r} is not at the requested position "
                    "after publish.")

        return _Mutation(added=set(), removed=set(), verify=verify)

    extra: dict[str, Any] = {"node_id": nid}
    if index is not None:
        extra["index"] = index
    elif before is not None:
        extra["before"] = before
    else:
        extra["after"] = after
    return _edit(backend, sitemap_id, action="move-node", extra=extra,
                 mutate=mutate, publish=publish, solution=solution)


# --- set-title / set-description (localized) ------------------------------------

# A nav node's children follow a strict sequence in the SiteMap XSD:
# ``Titles`` → ``Descriptions`` → child nodes (Group / SubArea / Privilege). A
# naive append of a localized container *after* the child nodes is a
# schema-invalid import, so a new container is spliced at the rank this map
# dictates (anything not listed — a child node — ranks last).
_CONTAINER_RANK = {"Titles": 0, "Descriptions": 1}


def _container_index(node: ET.Element, container_tag: str) -> int:
    """Index at which a ``Titles`` / ``Descriptions`` container belongs in ``node``.

    Returns the first position whose existing child ranks at or after the new
    container, so ``Titles`` lands first and ``Descriptions`` lands right after an
    existing ``Titles`` — both ahead of the node's child nodes, honoring the
    strict child sequence. A comment node (e.g. a ``--comment-out`` soft-delete)
    has a non-string tag that matches no rank key, so it falls to the child-node
    rank — a container is spliced ahead of it, which is schema-correct."""
    rank = _CONTAINER_RANK[container_tag]
    for i, child in enumerate(node):
        if _CONTAINER_RANK.get(child.tag, 2) >= rank:
            return i
    return len(node)


# Element children that follow the containers in the strict sequence.
_CHILD_NODE_TAGS = frozenset((*_NODE_TAGS, "Privilege"))


def _child_order_ok(node: ET.Element) -> bool:
    """True if ``node``'s child *elements* are in schema order (Titles →
    Descriptions → child nodes).

    Only the schema's known element tags are ranked; comment / PI nodes (whose
    tag is not one of these strings) are ignored, so a ``--comment-out``
    soft-deleted child never trips the order check wherever it sits."""
    ranks: list[int] = []
    for child in node:
        if child.tag == "Titles":
            ranks.append(0)
        elif child.tag == "Descriptions":
            ranks.append(1)
        elif child.tag in _CHILD_NODE_TAGS:
            ranks.append(2)
    return ranks == sorted(ranks)


def _normalize_entries(
    entries: list[tuple[int, str]], *, item_tag: str
) -> list[tuple[int, str]]:
    """Validate the ``(LCID, text)`` pairs for a localized set, enforcing one per
    LCID.

    Rejects an empty set, a blank value, and a repeated LCID: the XSD permits
    duplicate ``<Title>`` / ``<Description>`` elements per language, but a node
    surfaces only one, so the CLI enforces one-per-LCID rather than let a typo
    strand a shadow entry. The text is preserved verbatim (only blank is
    rejected)."""
    label = item_tag.lower()
    if not entries:
        raise D365Error(f"set-{label} needs at least one --lcid/--{label} pair.")
    seen: set[int] = set()
    norm: list[tuple[int, str]] = []
    for lcid, text in entries:
        if not 1000 <= lcid <= 9999:
            raise D365Error(
                f"--lcid {lcid} must be a 4-digit locale ID (e.g. 1033).")
        if not (text or "").strip():
            raise D365Error(f"{item_tag} for LCID {lcid} must not be empty.")
        if lcid in seen:
            raise D365Error(
                f"duplicate --lcid {lcid}: one {label} per language.")
        seen.add(lcid)
        norm.append((lcid, text))
    return norm


def _require_installed_lcids(backend: D365Backend, lcids: list[int]) -> None:
    """Reject any LCID not provisioned in the org (cross-checked live).

    A ``<Title>`` / ``<Description>`` for an un-provisioned language is silently
    ignored by the platform, so the editor refuses it up front rather than write
    dead text."""
    try:
        installed = set(retrieve_provisioned_languages(backend))
    except ValueError as exc:  # malformed RetrieveProvisionedLanguages response
        # Re-raise as D365Error so it surfaces through the CLI's error envelope
        # (d365_errors only traps D365Error), not as an unhandled traceback.
        raise D365Error(f"could not read installed languages: {exc}") from exc
    bad = [lcid for lcid in lcids if lcid not in installed]
    if bad:
        listed = ", ".join(str(x) for x in sorted(installed))
        raise D365Error(
            f"LCID(s) {', '.join(str(b) for b in bad)} are not installed "
            f"languages (installed: {listed}).")


def _set_localized(
    backend: D365Backend,
    sitemap_id: str,
    *,
    node_id: str,
    entries: list[tuple[int, str]],
    container_tag: str,
    item_tag: str,
    action: str,
    publish: bool,
    solution: str | None,
) -> dict[str, Any]:
    """Shared read-modify-write for ``set-title`` / ``set-description``.

    Finds (or creates, in schema order) the ``container_tag`` element on the node,
    then upserts one ``item_tag`` element per LCID — updating an existing entry
    for that language in place so no duplicate is ever minted. ``ResourceId`` and
    every other attribute on the node are left untouched."""
    nid = node_id.strip()
    if not nid:
        raise D365Error(f"{action} --id must not be empty.")
    norm = _normalize_entries(entries, item_tag=item_tag)
    _require_installed_lcids(backend, [lcid for lcid, _ in norm])

    def mutate(root: ET.Element) -> _Mutation:
        node, _tag = _locate(root, nid)
        if node is None:
            raise D365Error(
                f"no Area, Group or SubArea with Id {nid!r} in the sitemap.")
        container = node.find(container_tag)
        if container is None:
            container = ET.Element(container_tag)
            node.insert(_container_index(node, container_tag), container)
        for lcid, text in norm:
            existing = next(
                (c for c in container.findall(item_tag)
                 if c.get("LCID") == str(lcid)), None)
            if existing is None:
                ET.SubElement(
                    container, item_tag, {"LCID": str(lcid), item_tag: text})
            else:  # one element per LCID — update in place, never duplicate
                existing.set(item_tag, text)

        def verify(rb: ET.Element) -> None:
            rb_node, _ = _locate(rb, nid)
            rb_container = (
                rb_node.find(container_tag) if rb_node is not None else None)
            if rb_node is None or rb_container is None:
                raise D365Error(
                    f"read-back: {container_tag} absent on node {nid!r} after "
                    "publish.")
            for lcid, text in norm:
                if not any(
                    c.get("LCID") == str(lcid) and c.get(item_tag) == text
                    for c in rb_container.findall(item_tag)):
                    raise D365Error(
                        f"read-back: {item_tag} LCID={lcid} absent on node "
                        f"{nid!r} after publish.")
            if not _child_order_ok(rb_node):
                raise D365Error(
                    f"read-back: node {nid!r} children are out of schema order "
                    "(Titles → Descriptions → child nodes) after publish.")

        return _Mutation(added=set(), removed=set(), verify=verify)

    extra: dict[str, Any] = {
        "node_id": nid,
        container_tag.lower(): [
            {"lcid": lcid, item_tag.lower(): text} for lcid, text in norm],
    }
    return _edit(backend, sitemap_id, action=action, extra=extra,
                 mutate=mutate, publish=publish, solution=solution)


def set_title(
    backend: D365Backend,
    sitemap_id: str,
    *,
    node_id: str,
    titles: list[tuple[int, str]],
    publish: bool = False,
    solution: str | None = None,
) -> dict[str, Any]:
    """Set localized ``<Title>`` text (one per LCID) on a nav node's ``<Titles>``."""
    return _set_localized(
        backend, sitemap_id, node_id=node_id, entries=titles,
        container_tag="Titles", item_tag="Title", action="set-title",
        publish=publish, solution=solution)


def set_description(
    backend: D365Backend,
    sitemap_id: str,
    *,
    node_id: str,
    descriptions: list[tuple[int, str]],
    publish: bool = False,
    solution: str | None = None,
) -> dict[str, Any]:
    """Set localized ``<Description>`` text (one per LCID) on a node's
    ``<Descriptions>``."""
    return _set_localized(
        backend, sitemap_id, node_id=node_id, entries=descriptions,
        container_tag="Descriptions", item_tag="Description",
        action="set-description", publish=publish, solution=solution)
