"""The transform registry for `crm inventory convert` and its transforms (ADR 0034).

A **transform** is named by a compatibility rule's optional ``transform`` id. It reads
one source file's syntax tree and returns a `Plan`: the byte edits it can prove
equivalent, and the **unproven** sites where the rule matches but the rewrite is not
provable. A transform never writes; the engine applies the edits, gates them and
emits the patch.

A later transform registers itself with `register` and names its rule's id in
``compatibility-rules.json``.
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
#: A method that moved (`getUserName` to `userSettings.userName`) is not here.
SAME_METHOD = frozenset(
    (
        "getClientUrl",
        "getOrgLcid",
        "getOrgUniqueName",
        "getQueryStringParameters",
        "getTimeZoneOffsetMinutes",
        "getUserId",
        "getUserLcid",
        "getUserRoles",
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
        elif method.text is None or method.text.decode() not in SAME_METHOD:
            name = method.text.decode() if method.text else "?"
            unproven.append(Unproven(line, f"{name} has no same-name method on getGlobalContext()"))
        else:
            edits.append(Edit(node.start_byte, node.end_byte, _GLOBAL_CONTEXT))
    edits.sort(key=lambda e: e.start)
    unproven.sort(key=lambda u: u.line)
    return Plan(tuple(edits), tuple(unproven))


register("global-context", Transform("javascript", (3,), _plan_global_context))
