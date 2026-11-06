"""The transform registry for `crm inventory convert` and its transforms (ADR 0034).

A **transform** is named by a compatibility rule's optional ``transform`` id. It reads
one source file's syntax tree and returns a `Plan`: the byte edits it can prove
equivalent, and the **unproven** sites where the rule matches but the rewrite is not
provable. A transform never writes; the engine applies the edits, gates them and
emits the patch.

A transform registers itself with `register` and names its rule's id in
``compatibility-rules.json``. A file whose findings name several transforms runs them
one after another on the text the one before left, so a transform never sees a site an
earlier one rewrote, and each owns the sites its rule describes.
"""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from tree_sitter import Node


@dataclass(frozen=True)
class Edit:
    """Replace the bytes ``[start, end)`` of the source with *text*."""

    start: int
    end: int
    text: bytes


@dataclass(frozen=True)
class Unproven:
    """A site (1-based *line*) the rule matches but the transform will not rewrite, and why."""

    line: int
    reason: str


@dataclass(frozen=True)
class Plan:
    edits: tuple[Edit, ...]
    unproven: tuple[Unproven, ...]


@dataclass(frozen=True)
class Transform:
    """A registered transform: its grammar, the web resource types it reads, its planner."""

    language: str
    web_resource_types: tuple[int, ...]
    plan: Callable[[Node], Plan]


#: Registered transforms by id. Tests swap entries in to exercise the engine.
TRANSFORMS: dict[str, Transform] = {}


def register(transform_id: str, transform: Transform) -> None:
    if transform_id in TRANSFORMS:
        raise ValueError(f"transform {transform_id!r} is already registered")
    TRANSFORMS[transform_id] = transform


# ── global-context ──────────────────────────────────────────────────────────

#: Methods `Xrm.Page.context` and `Xrm.Utility.getGlobalContext()` both carry under one name.
#: A method that moved to a settings property (`getUserName` to `userSettings.userName`) is
#: not here: the `context-settings` transform owns it, see `CONTEXT_SETTINGS`.
SAME_METHOD = frozenset(
    (
        "getClientUrl",
        "getQueryStringParameters",
        "getVersion",
        "isOutlookClient",
        "isOutlookOnline",
        "prependOrgName",
    )
)
_GLOBAL_CONTEXT = b"Xrm.Utility.getGlobalContext()"


def _is_xrm_page_context(node: Node) -> bool:
    """True for the syntax node of exactly ``Xrm.Page.context``, nothing wrapped around it."""
    if node.type != "member_expression":
        return False
    obj, prop = node.child_by_field_name("object"), node.child_by_field_name("property")
    if obj is None or prop is None or prop.text != b"context" or obj.type != "member_expression":
        return False
    root, page = obj.child_by_field_name("object"), obj.child_by_field_name("property")
    return root is not None and page is not None and _is_xrm(root) and page.text == b"Page"


def _is_xrm(root: Node) -> bool:
    """True for ``Xrm`` or a qualified ``<owner>.Xrm`` (``window.Xrm``, ``parent.Xrm``)."""
    if root.type == "member_expression":
        prop = root.child_by_field_name("property")
        return prop is not None and prop.text == b"Xrm"
    return root.type == "identifier" and root.text == b"Xrm"


_DECLARES = frozenset(
    (
        "variable_declarator",
        "function_declaration",
        "function_expression",
        "generator_function_declaration",
        "class_declaration",
    )
)
_PATTERNS = frozenset(
    (
        "formal_parameters",
        "rest_pattern",
        "array_pattern",
        "object_pattern",
        "pair_pattern",
        "import_clause",
        "import_specifier",
        "namespace_import",
    )
)
_ASSIGNS = frozenset(
    (
        "assignment_expression",
        "augmented_assignment_expression",
        "assignment_pattern",
        "object_assignment_pattern",
        "for_in_statement",
    )
)


def _binds_xrm(node: Node) -> bool:
    """True when *node* is an ``Xrm`` identifier that declares or assigns a local name."""
    if node.type not in ("identifier", "shorthand_property_identifier_pattern"):
        return False
    if node.text != b"Xrm" or node.parent is None:
        return False
    parent = node.parent
    if parent.type in _DECLARES:
        return parent.child_by_field_name("name") == node
    if parent.type in _ASSIGNS:
        return parent.child_by_field_name("left") == node
    if parent.type == "arrow_function":
        return parent.child_by_field_name("parameter") == node
    if parent.type == "catch_clause":
        return parent.child_by_field_name("parameter") == node
    return parent.type in _PATTERNS


def _in_optional_chain(*nodes: Node | None) -> bool:
    return any(n is not None and any(c.type == "optional_chain" for c in n.children) for n in nodes)


def _plan_global_context(root: Node) -> Plan:
    """Rewrite ``Xrm.Page.context.<m>(...)`` to ``Xrm.Utility.getGlobalContext().<m>(...)``.

    Only a direct call of a `SAME_METHOD` is rewritten. Any other use of the context
    (an alias, an argument, a moved method) is unproven. Strings and comments are
    not syntax nodes of that shape, so they are never matched.
    """
    edits: list[Edit] = []
    unproven: list[Unproven] = []
    shadowed = False
    sites: list[Node] = []
    stack = [root]
    while stack:
        node = stack.pop()
        stack.extend(node.children)
        shadowed = shadowed or _binds_xrm(node)
        if _is_xrm_page_context(node):
            sites.append(node)
    for node in sites:
        line = node.start_point.row + 1
        parent = node.parent
        method = (
            parent.child_by_field_name("property")
            if parent is not None
            and parent.type == "member_expression"
            and parent.child_by_field_name("object") == node
            else None
        )
        call = parent.parent if parent is not None else None
        called = (
            call is not None
            and call.type == "call_expression"
            and call.child_by_field_name("function") == parent
        )
        page = node.child_by_field_name("object")
        xrm = page.child_by_field_name("object") if page is not None else None
        if xrm is not None and xrm.type == "member_expression":
            unproven.append(
                Unproven(line, "a qualified Xrm (window.Xrm, parent.Xrm) may be another frame")
            )
        elif shadowed:
            unproven.append(
                Unproven(line, "Xrm is bound in this file, so it may not be the global")
            )
        elif _in_optional_chain(node, node.child_by_field_name("object"), parent, call):
            unproven.append(
                Unproven(line, "an optional chain: the rewrite would drop its short-circuit")
            )
        elif method is None or not called:
            unproven.append(Unproven(line, "Xrm.Page.context is used other than as a direct call"))
        elif method.text is not None and method.text.decode() in CONTEXT_SETTINGS:
            continue
        elif method.text is None or method.text.decode() not in SAME_METHOD:
            name = method.text.decode() if method.text else "?"
            unproven.append(Unproven(line, f"{name} has no same-name method on getGlobalContext()"))
        else:
            edits.append(Edit(node.start_byte, node.end_byte, _GLOBAL_CONTEXT))
    edits.sort(key=lambda e: e.start)
    unproven.sort(key=lambda u: u.line)
    return Plan(tuple(edits), tuple(unproven))


register("global-context", Transform("javascript", (3,), _plan_global_context))


# ── shared helpers for the deprecation-table transforms ─────────────────────


def _walk(root: Node) -> list[Node]:
    out: list[Node] = []
    stack = [root]
    while stack:
        node = stack.pop()
        out.append(node)
        stack.extend(node.children)
    return out


def _prop(node: Node | None) -> bytes | None:
    """The property name of a member expression, else None."""
    if node is None or node.type != "member_expression":
        return None
    prop = node.child_by_field_name("property")
    return prop.text if prop is not None else None


def _no_args(call: Node) -> bool:
    args = call.child_by_field_name("arguments")
    return args is not None and args.named_child_count == 0


def _called(member: Node) -> Node | None:
    """The call expression whose function is exactly *member*, else None."""
    call = member.parent
    if call is None or call.type != "call_expression":
        return None
    return call if call.child_by_field_name("function") == member else None


def _xrm_problem(xrm: Node, shadowed: bool) -> str | None:
    if xrm.type == "member_expression":
        return "a qualified Xrm (window.Xrm, parent.Xrm) may be another frame"
    if shadowed:
        return "Xrm is bound in this file, so it may not be the global"
    return None


def _finish(edits: list[Edit], unproven: list[Unproven]) -> Plan:
    edits.sort(key=lambda e: e.start)
    unproven.sort(key=lambda u: u.line)
    return Plan(tuple(edits), tuple(unproven))


# ── context-settings: moved Xrm.Page.context methods ────────────────────────

#: Each method of the global context that moved to a settings property or method, with the
#: text that replaces ``<method>()`` after ``Xrm.Utility.getGlobalContext()``.
CONTEXT_SETTINGS = {
    "getIsAutoSaveEnabled": b"organizationSettings.isAutoSaveEnabled",
    "getOrgLcid": b"organizationSettings.languageId",
    "getOrgUniqueName": b"organizationSettings.uniqueName",
    "getTimeZoneOffsetMinutes": b"userSettings.getTimeZoneOffsetMinutes()",
    "getUserId": b"userSettings.userId",
    "getUserLcid": b"userSettings.languageId",
    "getUserName": b"userSettings.userName",
    "getUserRoles": b"userSettings.roles",
}


def _context_receiver(node: Node) -> tuple[Node, bool] | None:
    """For ``Xrm.Page.context`` or ``Xrm.Utility.getGlobalContext()`` the ``Xrm`` node and
    whether it is the first form, else None.
    """
    if _is_xrm_page_context(node):
        page = node.child_by_field_name("object")
        xrm = page.child_by_field_name("object") if page is not None else None
        return (xrm, True) if xrm is not None else None
    if node.type == "call_expression" and _no_args(node):
        fn = node.child_by_field_name("function")
        util = fn.child_by_field_name("object") if fn is not None else None
        if _prop(fn) == b"getGlobalContext" and _prop(util) == b"Utility" and util is not None:
            xrm = util.child_by_field_name("object")
            if xrm is not None and _is_xrm(xrm):
                return xrm, False
    return None


def _plan_context_settings(root: Node) -> Plan:
    """Rewrite ``<context>.getUserName()`` and the other moved methods to the property.

    The context is ``Xrm.Page.context`` or ``Xrm.Utility.getGlobalContext()``. Only a
    direct no-argument call is rewritten. ``getUserRoles()`` holds GUID strings and
    ``userSettings.roles`` holds lookup objects, which a callee can read as strings
    whatever the call site shows, so it is always unproven.
    """
    nodes = _walk(root)
    shadowed = any(_binds_xrm(n) for n in nodes)
    edits: list[Edit] = []
    unproven: list[Unproven] = []
    for m in nodes:
        name = _prop(m)
        recv = m.child_by_field_name("object") if name is not None else None
        found = _context_receiver(recv) if recv is not None else None
        if name is None or recv is None or found is None or name.decode() not in CONTEXT_SETTINGS:
            continue
        xrm, direct = found
        line = m.start_point.row + 1
        call = _called(m)
        fn_node = recv.child_by_field_name("function") if not direct else None
        reason: str | None = _xrm_problem(xrm, shadowed)
        if reason is None and _in_optional_chain(
            recv, m, call, fn_node, recv.child_by_field_name("object")
        ):
            reason = "an optional chain: the rewrite would drop its short-circuit"
        elif reason is None and (call is None or not _no_args(call)):
            reason = f"{name.decode()} is used other than as a direct no-argument call"
        elif reason is None and call is not None:
            tail = call.text[len(m.text) :] if call.text and m.text else b""
            prop_node = m.child_by_field_name("property")
            dropped = (recv.text if direct and recv.text else b"") + tail
            dropped += prop_node.text if prop_node is not None and prop_node.text else b""
            if b"\n" in dropped:
                reason = "the call spans lines"
            elif name == b"getUserRoles":
                reason = (
                    "getUserRoles() returned GUID strings and userSettings.roles holds"
                    " {id, name} lookups: every read of the list needs review"
                )
        if reason is not None or call is None or recv.text is None or m.text is None:
            unproven.append(Unproven(line, reason or "not a direct call"))
            continue
        gap = m.text[len(recv.text) : len(m.text) - len(name)]
        head = _GLOBAL_CONTEXT if direct else recv.text
        edits.append(
            Edit(recv.start_byte, call.end_byte, head + gap + CONTEXT_SETTINGS[name.decode()])
        )
    return _finish(edits, unproven)


register("context-settings", Transform("javascript", (3,), _plan_context_settings))


# ── grid-row: GridRow.getData() and GridRowData.getEntity() ─────────────────


def _plan_grid_row(root: Node) -> Plan:
    """Rewrite ``<row>.getData().getEntity()`` to ``<row>.data.entity``.

    Only GridRowData has ``getEntity()``, so a ``getEntity()`` on a no-argument
    ``getData()`` is the grid chain whatever the receiver. A ``getData()`` or
    ``getEntity()`` alone has a receiver the file does not prove, so it is unproven.
    """
    nodes = _walk(root)
    edits: list[Edit] = []
    unproven: list[Unproven] = []
    in_chain: set[int] = set()
    calls = [n for n in nodes if n.type == "call_expression"]
    for call in calls:
        fn = call.child_by_field_name("function")
        inner = fn.child_by_field_name("object") if fn is not None else None
        data_fn = inner.child_by_field_name("function") if inner is not None else None
        if (
            _prop(fn) != b"getEntity"
            or not _no_args(call)
            or inner is None
            or inner.type != "call_expression"
            or _prop(data_fn) != b"getData"
            or not _no_args(inner)
            or fn is None
            or data_fn is None
        ):
            continue
        in_chain.add(inner.start_byte)
        line = call.start_point.row + 1
        entity, data = fn.child_by_field_name("property"), data_fn.child_by_field_name("property")
        if entity is None or data is None or fn.text is None or inner.text is None:
            continue
        gap = fn.text[len(inner.text) : len(fn.text) - len(entity.text or b"")]
        tail = inner.text[len(data_fn.text or b"") :]
        dropped = (data.text or b"") + tail + (entity.text or b"")
        dropped += call.text[len(fn.text) :] if call.text else b""
        if _in_optional_chain(call, fn, inner, data_fn):
            unproven.append(
                Unproven(line, "an optional chain: the rewrite would drop its short-circuit")
            )
        elif b"\n" in dropped:
            unproven.append(Unproven(line, "the chain spans lines"))
        else:
            edits.append(Edit(data.start_byte, call.end_byte, b"data" + gap + b"entity"))
    for call in calls:
        fn = call.child_by_field_name("function")
        name = _prop(fn)
        if name not in (b"getData", b"getEntity") or not _no_args(call):
            continue
        if call.start_byte in in_chain or (fn is not None and _is_chain_member(call, fn)):
            continue
        who = "GridRow" if name == b"getData" else "GridRowData"
        unproven.append(
            Unproven(
                call.start_point.row + 1,
                f"{name.decode()}() on a receiver the file does not prove is a {who}",
            )
        )
    return _finish(edits, unproven)


def _is_chain_member(call: Node, fn: Node) -> bool:
    """True for a ``getEntity()`` whose receiver is a no-argument ``getData()`` (a chain)."""
    inner = fn.child_by_field_name("object")
    return (
        _prop(fn) == b"getEntity"
        and inner is not None
        and inner.type == "call_expression"
        and _no_args(inner)
        and _prop(inner.child_by_field_name("function")) == b"getData"
    )


register("grid-row", Transform("javascript", (3,), _plan_grid_row))


# ── mobile-offline: Xrm.Mobile.offline to Xrm.WebApi.offline ────────────────

_OFFLINE_SAME = frozenset(
    ("createRecord", "deleteRecord", "retrieveMultipleRecords", "retrieveRecord", "updateRecord")
)


def _plan_mobile_offline(root: Node) -> Plan:
    """Rewrite a direct call ``Xrm.Mobile.offline.<m>(...)`` to ``Xrm.WebApi.offline.<m>(...)``.

    Only the five record methods keep their name and arguments. ``isOfflineEnabled`` has no
    namesake (``isAvailableOffline`` asks whether the table is in the user's offline
    profile), and any other use of the namespace is unproven.
    """
    nodes = _walk(root)
    shadowed = any(_binds_xrm(n) for n in nodes)
    edits: list[Edit] = []
    unproven: list[Unproven] = []
    for off in nodes:
        mobile = off.child_by_field_name("object") if _prop(off) == b"offline" else None
        xrm = mobile.child_by_field_name("object") if mobile is not None else None
        if mobile is None or xrm is None or _prop(mobile) != b"Mobile" or not _is_xrm(xrm):
            continue
        line = off.start_point.row + 1
        parent = off.parent
        member = (
            parent
            if parent is not None
            and parent.type == "member_expression"
            and parent.child_by_field_name("object") == off
            else None
        )
        call = _called(member) if member is not None else None
        name = (_prop(member) or b"").decode()
        reason = _xrm_problem(xrm, shadowed)
        if reason is None and _in_optional_chain(off, mobile, member, call):
            reason = "an optional chain: the rewrite would drop its short-circuit"
        elif reason is None and name == "isOfflineEnabled":
            reason = (
                "isOfflineEnabled has no namesake: isAvailableOffline asks a different question"
            )
        elif reason is None and (call is None or name not in _OFFLINE_SAME):
            reason = "Xrm.Mobile.offline is used other than as a direct call of a record method"
        word = mobile.child_by_field_name("property")
        if reason is not None or word is None:
            unproven.append(Unproven(line, reason or "not a direct call"))
        else:
            edits.append(Edit(word.start_byte, word.end_byte, b"WebApi"))
    return _finish(edits, unproven)


register("mobile-offline", Transform("javascript", (3,), _plan_mobile_offline))


# ── entity-save: formContext.data.entity.save() to formContext.data.save() ──


def _member_object(node: Node | None) -> Node | None:
    return node.child_by_field_name("object") if node is not None else None


def _plan_entity_save(root: Node) -> Plan:
    """Rewrite a no-argument ``<x>.data.entity.save()`` to ``<x>.data.save()``.

    ``entity.save(saveOption)`` takes a string and ``data.save(saveOptions)`` an object, so
    a call with an argument is unproven.
    """
    edits: list[Edit] = []
    unproven: list[Unproven] = []
    nodes = _walk(root)
    shadowed = any(_binds_xrm(n) for n in nodes)
    for call in nodes:
        if call.type == "member_expression":
            parent = call.parent
            if (
                _prop(call) == b"save"
                and _prop(call.child_by_field_name("object")) == b"entity"
                and _prop(_member_object(call.child_by_field_name("object"))) == b"data"
                and not (
                    parent is not None
                    and parent.type == "call_expression"
                    and parent.child_by_field_name("function") == call
                )
            ):
                unproven.append(
                    Unproven(call.start_point.row + 1, "entity.save is used other than as a call")
                )
            continue
        if call.type != "call_expression":
            continue
        save = call.child_by_field_name("function")
        entity = save.child_by_field_name("object") if save is not None else None
        data = entity.child_by_field_name("object") if entity is not None else None
        if _prop(save) != b"save" or _prop(entity) != b"entity" or _prop(data) != b"data":
            continue
        if save is None or entity is None:
            continue
        line = call.start_point.row + 1
        entity_word = entity.child_by_field_name("property")
        save_word = save.child_by_field_name("property")
        if entity_word is None or save_word is None or save.text is None or entity.text is None:
            continue
        between = save.text[len(entity.text) : len(save.text) - len(save_word.text or b"")]
        if _in_optional_chain(call, save, entity, data):
            unproven.append(
                Unproven(line, "an optional chain: the rewrite would drop its short-circuit")
            )
        elif (
            _prop(_member_object(data)) == b"Page"
            and (xrm := _member_object(_member_object(data))) is not None
            and (problem := _xrm_problem(xrm, shadowed)) is not None
        ):
            unproven.append(Unproven(line, problem))
        elif not _no_args(call):
            unproven.append(
                Unproven(
                    line, "save(saveOption) takes a string; data.save takes a saveOptions object"
                )
            )
        elif between.strip(b" \t") != b".":
            unproven.append(Unproven(line, "a comment or line break sits inside entity.save"))
        else:
            edits.append(Edit(entity_word.start_byte, save_word.start_byte, b""))
    return _finish(edits, unproven)


register("entity-save", Transform("javascript", (3,), _plan_entity_save))


# ── review-only transforms: rows whose signature or sync behaviour changes ──

_UTILITY_REVIEW = {
    "alertDialog": "openAlertDialog takes text and options objects and is asynchronous",
    "confirmDialog": "openConfirmDialog takes text and options objects and is asynchronous",
    "isActivityType": "isActivityType is synchronous, its replacement getEntityMetadata is not",
    "openEntityForm": "Xrm.Navigation.openForm takes entityFormOptions and is asynchronous",
    "openQuickCreate": "Xrm.Navigation.openForm takes entityFormOptions with createFromEntity",
    "openWebResource": "Xrm.Navigation.openWebResource takes (name, windowOptions, data)",
}


def _plan_xrm_utility_review(root: Node) -> Plan:
    """List each ``Xrm.Utility.<method>`` use of the methods that moved to Xrm.Navigation."""
    nodes = _walk(root)
    shadowed = any(_binds_xrm(n) for n in nodes)
    unproven: list[Unproven] = []
    for n in nodes:
        name = _prop(n)
        util = n.child_by_field_name("object") if name is not None else None
        xrm = util.child_by_field_name("object") if util is not None else None
        if name is None or xrm is None or _prop(util) != b"Utility" or not _is_xrm(xrm):
            continue
        if name.decode() in _UTILITY_REVIEW:
            reason = _xrm_problem(xrm, shadowed) or _UTILITY_REVIEW[name.decode()]
            unproven.append(Unproven(n.start_point.row + 1, reason))
    return _finish([], unproven)


register("xrm-utility-review", Transform("javascript", (3,), _plan_xrm_utility_review))

_KEYPRESS = frozenset(
    ("addOnKeyPress", "fireOnKeyPress", "hideAutoComplete", "removeOnKeyPress", "showAutoComplete")
)


def _plan_keypress_review(root: Node) -> Plan:
    """List each key-press or auto-complete call: the replacement is a custom control."""
    unproven = [
        Unproven(
            call.start_point.row + 1,
            f"{(_prop(fn) or b'').decode()} has no API replacement: use a custom control (PCF)",
        )
        for call in _walk(root)
        if call.type == "call_expression"
        and (fn := call.child_by_field_name("function")) is not None
        and (_prop(fn) or b"").decode() in _KEYPRESS
    ]
    return _finish([], unproven)


register("keypress-review", Transform("javascript", (3,), _plan_keypress_review))
