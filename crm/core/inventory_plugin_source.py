"""Find where C# source creates or accesses a listed type (ADR 0034).

`type_uses` is pure: source bytes in, the (line, column) of each use of a listed type out. A
**use** is an object creation or a member access whose type resolves, through the file's own
``using`` directives, ``using`` aliases, ``using static`` or a fully qualified name, to a listed
full type name. A ``using`` alone, a comment and a string literal are not uses, and a type of the
same simple name declared in the same file wins over an import. Only the syntax tree is read, so
nothing is compiled and no type is looked up in another file.
"""

from __future__ import annotations

from collections.abc import Collection
from typing import TYPE_CHECKING

from crm.core.code_parse import error_count, parse

if TYPE_CHECKING:
    from tree_sitter import Node

_TYPE_DECLARATIONS = frozenset(
    (
        "class_declaration",
        "struct_declaration",
        "interface_declaration",
        "enum_declaration",
        "record_declaration",
        "record_struct_declaration",
        "delegate_declaration",
    )
)
_METHOD_DECLARATIONS = frozenset(("method_declaration", "local_function_statement"))


def _dotted(node: Node | None) -> str | None:
    """The dotted name *node* spells, or None when it is anything but names and dots."""
    if node is None:
        return None
    if node.type == "identifier":
        return (node.text or b"").decode()
    if node.type == "alias_qualified_name":
        # `global::X` is X; another alias qualifier is not resolved.
        qualifier, name = node.named_children[0], node.named_children[-1]
        return _dotted(name) if (qualifier.text or b"") == b"global" else None
    if node.type in ("qualified_name", "member_access_expression"):
        names = node.named_children
        left, right = _dotted(names[0]), _dotted(names[-1])
        return None if left is None or right is None else f"{left}.{right}"
    return None


class _Imports:
    """What a file's ``using`` directives and declarations make of a name."""

    def __init__(self, root: Node, listed: Collection[str]) -> None:
        self.listed = listed
        self.namespaces: list[str] = []
        self.aliases: dict[str, str] = {}
        self.static: list[str] = []
        self.types: set[str] = set()
        self.methods: set[str] = set()
        stack = [root]
        while stack:
            node = stack.pop()
            stack.extend(node.children)
            if node.type == "using_directive":
                self._using(node)
            elif node.type in _TYPE_DECLARATIONS or node.type in _METHOD_DECLARATIONS:
                name = node.child_by_field_name("name")
                if name is not None:
                    target = self.types if node.type in _TYPE_DECLARATIONS else self.methods
                    target.add((name.text or b"").decode())

    def _using(self, node: Node) -> None:
        names = node.named_children
        if not names:
            return
        target = _dotted(names[-1])
        if target is None:
            return
        if any(c.type == "static" for c in node.children):
            self.static.append(target)
        elif any(c.type == "=" for c in node.children):
            alias = _dotted(names[0])
            if alias is not None:
                self.aliases[alias] = target
        else:
            self.namespaces.append(target)

    def resolve(self, name: str) -> str | None:
        """The listed full type name *name* stands for here, or None."""
        first, _, rest = name.partition(".")
        if first in self.types:
            return None
        if first in self.aliases:
            candidates = [self.aliases[first] + (f".{rest}" if rest else "")]
        else:
            candidates = [name, *(f"{ns}.{name}" for ns in self.namespaces)]
        return next((c for c in candidates if c in self.listed), None)


def _position(source: bytes, node: Node) -> tuple[int, int]:
    """The 1-based (line, column) of *node*, the column in characters, not bytes."""
    start = node.start_byte
    line_start = source.rfind(b"\n", 0, start) + 1
    return node.start_point.row + 1, len(source[line_start:start].decode("utf-8")) + 1


def type_uses(source: bytes, listed: Collection[str]) -> dict[str, list[tuple[int, int]]] | None:
    """Each listed full type name *source* uses, with the 1-based (line, column) of every use.

    *source* must be valid UTF-8. A bare call under ``using static`` is attributed to the first
    listed type imported that way, unless the file declares a method of that name: the syntax
    tree cannot say which type declares the method. Returns None when the file has syntax errors,
    since a tree recovered from them proves nothing.
    """
    root = parse("c_sharp", source)
    if error_count(root):
        return None
    imports = _Imports(root, listed)
    static = next((s for s in imports.static if s in listed), None)
    found: dict[str, list[tuple[int, int]]] = {}

    def add(full: str | None, node: Node) -> None:
        if full is not None:
            found.setdefault(full, []).append(_position(source, node))

    stack = [root]
    while stack:
        node = stack.pop()
        stack.extend(reversed(node.children))
        if node.type == "object_creation_expression":
            kind = node.child_by_field_name("type")
            name = _dotted(kind)
            if kind is not None and name is not None:
                add(imports.resolve(name), kind)
        elif node.type == "member_access_expression":
            target = node.child_by_field_name("expression")
            name = _dotted(target)
            if target is not None and name is not None:
                add(imports.resolve(name), target)
        elif node.type == "invocation_expression" and static is not None:
            function = node.child_by_field_name("function")
            if (
                function is not None
                and function.type == "identifier"
                and (function.text or b"").decode() not in imports.methods
            ):
                add(static, function)
    return {name: sorted(sites) for name, sites in found.items()}
