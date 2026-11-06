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

import re
from collections.abc import Callable, Mapping
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
    """What a transform proved. *done* names the registered functions a handler transform
    rewrote. *cleared* is how many sites of the rule's detection the edits remove, where that
    is not one per edit. *threaded*, *unreachable* and *boundary* count the helpers it gave a
    ``formContext``, the functions no handler reaches and the helpers a caller outside the
    file keeps from being rewritten.
    """

    edits: tuple[Edit, ...]
    unproven: tuple[Unproven, ...]
    done: tuple[str, ...] = ()
    cleared: int | None = None
    threaded: int = 0
    unreachable: int = 0
    boundary: int = 0


@dataclass(frozen=True)
class Transform:
    """A registered transform: its grammar, the web resource types it reads, its planner.

    A transform with *needs_handlers* is also given the form handlers that call the file:
    each registered function name, mapped to whether any registration passes it positional
    ``parameters``.
    """

    language: str
    web_resource_types: tuple[int, ...]
    plan: Callable[..., Plan]
    needs_handlers: bool = False


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


# ── form-handler-context: registered handlers take the execution context ────

#: The id the form registration rule names. Its JS half is `_plan_handler_context`, its
#: form XML half `inventory_form_handlers.plan_form_handlers`.
HANDLER_TRANSFORM = "form-handler-context"

#: A first parameter that already reads as the execution context.
_CONTEXT_PARAM = re.compile(r"(?i)(e|ex|exec|execution)?_?(context|ctx)")
_FUNCTIONS = frozenset(("function_declaration", "function_expression"))
_NESTED = frozenset(
    (
        "function_declaration",
        "function_expression",
        "generator_function_declaration",
        "generator_function",
        "arrow_function",
        "method_definition",
    )
)
_NAMES = frozenset(("identifier", "property_identifier", "shorthand_property_identifier"))


def _definitions(nodes: list[Node]) -> dict[bytes, list[tuple[Node, Node]]]:
    """Each named function of the file by name: ``(function node, node naming it)`` pairs."""
    found: dict[bytes, list[tuple[Node, Node]]] = {}
    for n in nodes:
        if n.type == "function_declaration":
            ident = n.child_by_field_name("name")
            if ident is not None and ident.text is not None:
                found.setdefault(ident.text, []).append((n, ident))
        elif n.type in ("assignment_expression", "variable_declarator"):
            assign = n.type == "assignment_expression"
            left = n.child_by_field_name("left" if assign else "name")
            right = n.child_by_field_name("right" if assign else "value")
            if (
                left is not None
                and left.text is not None
                and right is not None
                and right.type == "function_expression"
            ):
                found.setdefault(left.text, []).append(
                    (right, left.child_by_field_name("property") or left)
                )
    return found


def _context_sites(
    fn: Node, body: Node, inside: list[Node], shadowed: bool
) -> tuple[list[Edit], list[Unproven]]:
    """The ``Xrm.Page`` sites of function *fn* that become ``formContext``, and those that stay."""
    sites: list[Edit] = []
    problems: list[Unproven] = []
    for page in inside:
        xrm = page.child_by_field_name("object") if _prop(page) == b"Page" else None
        if xrm is None or not _is_xrm(xrm):
            continue
        use = page.parent
        at = page.start_point.row + 1
        member = _prop(use) if use is not None and _member_object(use) == page else None
        nested, up = False, page.parent
        while up is not None and up != fn:
            nested = nested or up.type in _NESTED
            up = up.parent
        reason = _xrm_problem(xrm, shadowed)
        if reason is None and page.start_byte < body.start_byte:
            reason = "Xrm.Page is in a parameter default: it runs before the body's formContext"
        elif reason is None and _in_optional_chain(page, xrm, use):
            reason = "an optional chain: the rewrite would drop its short-circuit"
        elif reason is None and member is None:
            reason = "Xrm.Page is used other than as a member access"
        elif reason is None and member == b"context":
            continue
        elif reason is None and nested:
            reason = "Xrm.Page is in a nested function: the context is valid only during the event"
        elif reason is None and b"\n" in (page.text or b""):
            reason = "Xrm.Page spans lines"
        if reason is not None:
            problems.append(Unproven(at, reason))
        else:
            sites.append(Edit(page.start_byte, page.end_byte, b"formContext"))
    return sites, problems


def _call_name(call: Node) -> tuple[bytes, Node] | None:
    """The dotted name *call* invokes and the node holding its last part, else None."""
    fn = call.child_by_field_name("function")
    if fn is None or fn.text is None:
        return None
    if fn.type == "identifier":
        return fn.text, fn
    prop = fn.child_by_field_name("property") if fn.type == "member_expression" else None
    return (fn.text, prop) if prop is not None else None


def _owner(node: Node, defined: Mapping[int, bytes]) -> tuple[bytes | None, bool]:
    """The named function holding *node*, and whether an unnamed function sits between."""
    nested, up = False, node.parent
    while up is not None:
        if up.id in defined:
            return defined[up.id], nested
        nested = nested or up.type in _NESTED
        up = up.parent
    return None, nested


def _thread_helpers(
    nodes: list[Node],
    table: Mapping[bytes, list[tuple[Node, Node]]],
    functions: Mapping[str, bool],
    ready: set[str],
    shadowed: bool,
    outside: frozenset[str] | None,
) -> Plan:
    """Pass ``formContext`` down the file's call graph from the rewritten handlers.

    *ready* names the handlers that can take a ``formContext``; the plan's *done* names those of
    them that call a threaded helper, which a handler with no site of its own needs.

    A helper is threaded when it (or a helper it calls) reads ``Xrm.Page`` and every use of its
    name is a direct, plain call from a rewritten handler or an already threaded helper: it
    gains a first ``formContext`` parameter and each call passes it, so a signature never
    changes without its callers. Anything else leaves the helper whole: a name bound twice or
    used other than as a call (a callback, ``.call``, a property), a call from a nested
    function, top-level code or a function nothing threads, a recursive cycle, and a name the
    rest of the inventory names (another script, a ribbon, a form registration): that is a
    caller this run cannot update. A function (or top-level site) no registered handler reaches
    counts as unreachable, a helper kept back by a name used outside the file as a boundary,
    and each is a manual-review entry.
    """
    handlers = {n.encode() for n in functions}
    live = {n.encode() for n in ready}
    defined = {fn.id: name for name, defs in table.items() for fn, _ in defs}
    accounted = {ident.id for defs in table.values() for _, ident in defs}
    calls: dict[bytes, list[tuple[Node, bytes | None, bool]]] = {}
    for n in nodes:
        named = _call_name(n) if n.type == "call_expression" else None
        if named is not None and named[0] in table:
            owner, nested = _owner(n, defined)
            calls.setdefault(named[0], []).append((n, owner, nested))
            accounted.add(named[1].id)
    by_last: dict[bytes, list[bytes]] = {}
    for name in table:
        by_last.setdefault(name.rsplit(b".", 1)[-1], []).append(name)
    refs: dict[bytes, list[Node]] = {}
    for n in nodes:
        if n.type in _NAMES and n.text in by_last and n.id not in accounted:
            for name in by_last[n.text or b""]:
                refs.setdefault(name, []).append(n)
    callees: dict[bytes | None, set[bytes]] = {}
    for callee, found in calls.items():
        for _, owner, _ in found:
            callees.setdefault(owner, set()).add(callee)

    units: dict[bytes, tuple[Node, Node, str | None, list[Edit], list[Unproven]]] = {}
    for name, defs in table.items():
        fn = defs[0][0]
        if name in handlers or _owner(fn, defined)[0] is not None:
            continue
        params, body = fn.child_by_field_name("parameters"), fn.child_by_field_name("body")
        inside = _walk(fn)
        sites, problems = _context_sites(fn, body or fn, inside, shadowed)
        reason: str | None = None
        text = name.decode()
        if len(defs) > 1:
            reason = f"{text} is defined more than once in this file"
            for extra, _ in defs[1:]:
                more, left = _context_sites(extra, extra, _walk(extra), shadowed)
                if more or left:
                    problems.append(Unproven(extra.start_point.row + 1, reason))
        elif params is None or body is None or body.type != "statement_block":
            reason = f"{text} has no block body to take the formContext"
        elif fn.type == "function_expression" and fn.child_by_field_name("name") is not None:
            reason = f"{text} is a named function expression: its inner name is not tracked"
        elif any(c.type == "async" for c in fn.children):
            reason = f"{text} is async: the caller's context is not valid after an await"
        elif any(n.type == "identifier" and n.text == b"arguments" for n in inside):
            reason = f"{text} reads arguments, whose positions formContext would shift"
        elif any(n.type in _NAMES and n.text == b"formContext" for n in inside):
            reason = f"formContext is already used in {text}"
        units[name] = (fn, body or fn, reason, sites, problems)

    def mentioned(name: bytes) -> bool:
        return outside is None or name.rsplit(b".", 1)[-1].decode() in outside

    def blocked(name: bytes, at: set[bytes]) -> str | None:
        """Why *name* cannot be threaded given the functions in *at* hold a formContext."""
        text, reason = name.decode(), units[name][2]
        if reason is not None:
            return reason
        if mentioned(name):
            return f"{text} is also named outside this file: a caller or registration there stays"
        if name in refs:
            line = refs[name][0].start_point.row + 1
            return f"{text} is also referenced at line {line}, other than as a direct call"
        if name not in calls:
            return f"nothing in this file calls {text}"
        for call, owner, nested in calls[name]:
            line = call.start_point.row + 1
            args = call.child_by_field_name("arguments")
            if owner is None:
                return f"{text} is called outside any function at line {line}"
            if nested:
                return f"{text} is called from a nested function at line {line}"
            if owner == name:
                return f"{text} is recursive"
            if owner not in at:
                return f"{text} is called at line {line} by {owner.decode()}, which gets none"
            if args is None or args.type != "arguments":
                return f"{text} is called at line {line} other than by a plain call"
            if _in_optional_chain(call, call.child_by_field_name("function")):
                return f"an optional chain at line {line}: the rewrite would drop its short-circuit"
            caller_body = table[owner][0][0].child_by_field_name("body")
            if caller_body is not None and call.start_byte < caller_body.start_byte:
                return f"{text} is called from a parameter default at line {line}"
        return None

    threaded: set[bytes] = set()
    changed = True
    while changed:
        changed = False
        for name in sorted(units):
            if name not in threaded and blocked(name, live | threaded) is None:
                threaded.add(name)
                changed = True
    changed = True
    while changed:
        changed = False
        for name in sorted(threaded):
            if not units[name][3] and not callees.get(name, set()) & threaded:
                threaded.discard(name)
                changed = True

    reached: set[bytes] = (handlers & set(table)) | callees.get(None, set())
    queue = list(reached)
    while queue:
        for nxt in callees.get(queue.pop(), set()) - reached:
            reached.add(nxt)
            queue.append(nxt)

    edits: list[Edit] = []
    unproven: list[Unproven] = []
    unreachable = boundary = cleared = 0
    for name, (fn, _body, _, sites, problems) in sorted(units.items()):
        line = fn.start_point.row + 1
        if name in threaded:
            params = fn.child_by_field_name("parameters")
            first = (
                params.named_children[0] if params is not None and params.named_children else None
            )
            lead = (
                first.start_byte if first is not None else (params.start_byte + 1 if params else 0)
            )
            edits.append(Edit(lead, lead, b"formContext" + (b", " if first is not None else b"")))
            edits.extend(sites)
            cleared += len(sites)
            for call, _, _ in calls[name]:
                args = call.child_by_field_name("arguments")
                if args is not None:
                    pad = b", " if args.named_child_count else b""
                    edits.append(
                        Edit(args.start_byte + 1, args.start_byte + 1, b"formContext" + pad)
                    )
            unproven.extend(problems)
        elif sites or problems:
            reason = blocked(name, live | threaded) or f"{name.decode()} gets no formContext"
            if mentioned(name):
                boundary += 1
            elif name not in reached:
                unreachable += 1
                reason = f"no registered handler reaches {name.decode()}: {reason}"
            unproven.append(Unproven(line, reason))
    for page in nodes:
        xrm = page.child_by_field_name("object") if _prop(page) == b"Page" else None
        if xrm is None or not _is_xrm(xrm) or _owner(page, defined)[0] is not None:
            continue
        if _prop(page.parent) == b"context" and _member_object(page.parent) == page:
            continue
        unreachable += 1
        unproven.append(
            Unproven(
                page.start_point.row + 1,
                "Xrm.Page is outside any named function: no handler reaches it",
            )
        )
    used = tuple(
        sorted(
            owner.decode()
            for name in threaded
            for _, owner, _ in calls[name]
            if owner is not None and owner in live
        )
    )
    return Plan(tuple(edits), tuple(unproven), used, cleared, len(threaded), unreachable, boundary)


def _plan_handler_context(
    root: Node, functions: Mapping[str, bool], outside: frozenset[str] | None = frozenset()
) -> Plan:
    """Give each registered handler in *functions* an ``executionContext`` and a ``formContext``.

    A handler gets the parameter in first position (the platform passes the context before
    any form ``parameters``, so those keep their order behind it), a
    ``var formContext = <context>.getFormContext();`` declaration after its opening brace
    (on the same line, so no line moves), and each ``Xrm.Page.<member>`` in its own body
    becomes ``formContext.<member>``. The Learn page on the execution context says it is
    valid only during the event, so a site in a nested function or in an async handler is
    unproven. So is a function the file defines twice or refers to elsewhere (a caller would
    pass the old positions), one that reads ``arguments`` or already uses ``formContext``,
    and ``Xrm.Page.context``, which `global-context` owns. A function is rewritten only when
    at least one of its sites is.

    A helper the handlers reach is threaded too, see `_thread_helpers`; *outside* holds the
    names the rest of the inventory mentions, None where a script could not be read.
    """
    nodes = _walk(root)
    table = _definitions(nodes)
    shadowed = any(_binds_xrm(n) for n in nodes)
    edits: list[Edit] = []
    unproven: list[Unproven] = []
    done: list[str] = []
    ready: dict[str, tuple[list[Edit], list[Edit]]] = {}
    cleared = 0
    for name, has_parameters in sorted(functions.items()):
        defs = table.get(name.encode(), [])
        if not defs:
            continue
        fn = defs[0][0]
        line = fn.start_point.row + 1
        params, body = fn.child_by_field_name("parameters"), fn.child_by_field_name("body")
        last = name.rsplit(".", 1)[-1].encode()
        inside = _walk(fn)
        taken = {n.start_byte for _, n in defs}
        other = [
            n for n in nodes if n.type in _NAMES and n.text == last and n.start_byte not in taken
        ]
        first = params.named_children[0] if params is not None and params.named_children else None
        has_context = (
            first is not None
            and first.type == "identifier"
            and _CONTEXT_PARAM.fullmatch((first.text or b"").decode()) is not None
        )
        context = (
            (first.text or b"").decode()
            if has_context and first is not None
            else "executionContext"
        )
        reason: str | None = None
        if len(defs) > 1:
            reason = f"{name} is defined more than once in this file"
        elif other:
            at = other[0].start_point.row + 1
            reason = f"{name} is also referenced at line {at}: its callers pass the old positions"
        elif params is None or body is None or body.type != "statement_block":
            reason = f"{name} has no block body to take the context"
        elif fn.type == "function_expression" and fn.child_by_field_name("name") is not None:
            reason = f"{name} is a named function expression: its inner name is not tracked"
        elif any(c.type == "async" for c in fn.children):
            reason = f"{name} is async: the context is not valid after an await"
        elif any(n.type == "identifier" and n.text == b"arguments" for n in inside):
            reason = f"{name} reads arguments, whose positions the context would shift"
        elif any(n.type in _NAMES and n.text == b"formContext" for n in inside):
            reason = f"formContext is already used in {name}"
        elif not has_context and any(
            n.type in _NAMES and n.text == b"executionContext" for n in inside
        ):
            reason = f"executionContext is already used in {name}"
        elif has_context and has_parameters:
            reason = (
                f"{context} may be a positional parameter: {name} is registered with parameters"
            )
        if reason is not None:
            unproven.append(Unproven(line, reason))
            continue
        if params is None or body is None:
            continue
        sites, problems = _context_sites(fn, body, inside, shadowed)
        unproven.extend(problems)
        own: list[Edit] = []
        if not has_context:
            lead = first.start_byte if first is not None else params.start_byte + 1
            tail = b", " if first is not None else b""
            own.append(Edit(lead, lead, b"executionContext" + tail))
        brace, glue = body.start_byte + 1, ""
        for stmt in body.named_children:
            if stmt.type == "comment":
                continue
            lead_string = stmt.named_children[0] if stmt.named_children else None
            if stmt.type != "expression_statement" or lead_string is None:
                break
            if lead_string.type != "string":
                break
            brace = stmt.end_byte
            glue = "" if (stmt.text or b"").endswith(b";") else ";"
        own.append(
            Edit(brace, brace, f"{glue} var formContext = {context}.getFormContext();".encode())
        )
        ready[name] = (sites, own)
    graph = _thread_helpers(nodes, table, functions, set(ready), shadowed, outside)
    for name, (sites, own) in ready.items():
        if sites or name in graph.done:
            edits.extend(own + sites)
            done.append(name)
            cleared += len(sites)
    edits.extend(graph.edits)
    unproven.extend(graph.unproven)
    edits.sort(key=lambda e: (e.start, e.end))
    unproven.sort(key=lambda u: u.line)
    return Plan(
        tuple(edits),
        tuple(unproven),
        tuple(done),
        cleared + (graph.cleared or 0),
        graph.threaded,
        graph.unreachable,
        graph.boundary,
    )


register(HANDLER_TRANSFORM, Transform("javascript", (3,), _plan_handler_context, True))
