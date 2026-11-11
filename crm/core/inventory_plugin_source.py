"""Find where C# source creates or accesses a listed type (ADR 0034).

`type_uses` is pure: source bytes in, the (line, column) of each use of a listed type out. A
**use** is an object creation or a member access whose type resolves, through the file's own
``using`` directives, ``using`` aliases, ``using static`` or a fully qualified name, to a listed
full type name. A ``using`` alone, a comment and a string literal are not uses, and a type of the
same simple name declared in the same file (a type, a variable, a parameter, a field or a
property in scope) wins over an import. Only the syntax tree is read, so
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
#: Declarations whose name can hide a type of the same name inside their scope: a parameter, a
#: variable, a field or a property called `File` is the member, not `System.IO.File`. Variables
#: bound by tuple deconstruction and LINQ are not tracked.
_MEMBER_DECLARATIONS = frozenset(
    (
        "parameter",
        "variable_declarator",
        "property_declaration",
        "declaration_pattern",
        "declaration_expression",
        "catch_declaration",
        "implicit_parameter",
        "foreach_statement",
    )
)
#: The nodes a member declaration is visible in.
_SCOPES = frozenset(
    (
        *_TYPE_DECLARATIONS,
        "block",
        "method_declaration",
        "constructor_declaration",
        "local_function_statement",
        "lambda_expression",
        "anonymous_method_expression",
        "accessor_declaration",
        "operator_declaration",
        "for_statement",
        "foreach_statement",
        "using_statement",
        "fixed_statement",
    )
)


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
        #: Types this file declares: they win over an import everywhere in the file.
        self.types: set[str] = set()
        #: Variables and members that win over an import, by name, inside the scope nodes (ids).
        self.scoped: dict[str, set[int]] = {}
        self.methods: set[str] = set()
        stack = [root]
        while stack:
            node = stack.pop()
            stack.extend(node.children)
            if node.type == "using_directive":
                self._using(node)
            elif node.type in _METHOD_DECLARATIONS:
                self._declared(node, self.methods)
            elif node.type in _TYPE_DECLARATIONS:
                self._declared(node, self.types)
            elif node.type in _MEMBER_DECLARATIONS:
                self._scoped(node)

    @staticmethod
    def _declared(node: Node, into: set[str]) -> None:
        name = node.child_by_field_name("name")
        if name is not None:
            into.add((name.text or b"").decode())

    def _scoped(self, node: Node) -> None:
        # An implicit lambda parameter is its own name; a foreach variable is its `left` field.
        name = node if node.type == "implicit_parameter" else None
        name = name or node.child_by_field_name(
            "left" if node.type == "foreach_statement" else "name"
        )
        scope = node if node.type == "foreach_statement" else node.parent
        while scope is not None and scope.type not in _SCOPES:
            scope = scope.parent
        if name is not None and scope is not None:
            self.scoped.setdefault((name.text or b"").decode(), set()).add(scope.id)

    def _hidden(self, name: str, at: Node) -> bool:
        ids = self.scoped.get(name)
        node = at.parent
        while ids and node is not None:
            if node.id in ids:
                return True
            node = node.parent
        return False

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

    def unshadowed(self, name: str, at: Node) -> bool:
        """Whether the bare *name* may be a static member imported by ``using static``."""
        first = name.partition(".")[0]
        return not (
            first in self.types
            or first in self.aliases
            or first in self.methods
            or self._hidden(first, at)
        )

    def resolve(self, name: str, at: Node) -> str | None:
        """The listed full type name *name*, written at node *at*, stands for, or None."""
        first, _, rest = name.partition(".")
        if first in self.types or self._hidden(first, at):
            return None
        if first in self.aliases:
            candidates = [self.aliases[first] + (f".{rest}" if rest else "")]
        else:
            # A using directive imports the types of a namespace, never its child namespaces.
            candidates = [name] + ([] if rest else [f"{ns}.{name}" for ns in self.namespaces])
        return next((c for c in candidates if c in self.listed), None)


def _declared_type(node: Node) -> Node | None:
    """The declared type a target-typed ``new(...)`` is created as, from its declaration."""
    parent = node.parent
    for _ in range(2):  # the initializer: variable_declarator, or an equals_value_clause in it
        if parent is None or parent.type in ("variable_declaration", "property_declaration"):
            break
        parent = parent.parent
    declared = parent.child_by_field_name("type") if parent is not None else None
    if declared is not None and declared.type == "nullable_type":
        return declared.named_children[0]
    return declared


def _position(source: bytes, node: Node) -> tuple[int, int]:
    """The 1-based (line, column) of *node*, the column in characters, not bytes."""
    start = node.start_byte
    line_start = source.rfind(b"\n", 0, start) + 1
    return node.start_point.row + 1, len(source[line_start:start].decode("utf-8")) + 1


def type_uses(source: bytes, listed: Collection[str]) -> dict[str, list[tuple[int, int]]] | None:
    """Each listed full type name *source* uses, with the 1-based (line, column) of every use.

    *source* must be valid UTF-8. A bare call, or a bare capitalized name that no ``using``
    resolves and the file does not declare, is attributed to every listed type imported by
    ``using static``: the syntax tree cannot say which type declares the member, so such a name
    can over-report (``Path.Combine`` next to ``using static System.IO.File``). A target-typed
    ``new(...)`` takes its type from the declaration it initializes. Returns None when the file
    has syntax errors, since a tree recovered from them proves nothing.
    """
    root = parse("c_sharp", source)
    if error_count(root):
        return None
    imports = _Imports(root, listed)
    static_types = [s for s in imports.static if s in listed]
    found: dict[str, list[tuple[int, int]]] = {}

    def add(full: str | None, node: Node) -> None:
        if full is not None:
            found.setdefault(full, []).append(_position(source, node))

    stack = [root]
    while stack:
        node = stack.pop()
        function = node.child_by_field_name("function")
        if (
            node.type == "invocation_expression"
            and (function.text if function else b"") == b"nameof"
        ):
            continue  # a name, not a use: `nameof(File.Delete)` never runs File
        stack.extend(reversed(node.children))
        if node.type == "object_creation_expression":
            kind = node.child_by_field_name("type")
            name = _dotted(kind)
            if kind is not None and name is not None:
                add(imports.resolve(name, kind), kind)
        elif node.type == "member_access_expression":
            target = node.child_by_field_name("expression")
            name = _dotted(target)
            if target is not None and name is not None:
                full = imports.resolve(name, target)
                if full is not None:
                    add(full, target)
                elif target.type == "identifier" and name[0].isupper():
                    if imports.unshadowed(name, target):
                        for owner in static_types:
                            add(owner, target)
        elif node.type == "implicit_object_creation_expression":
            declared = _declared_type(node)
            name = _dotted(declared)
            if declared is not None and name is not None:
                add(imports.resolve(name, declared), node)
        elif node.type == "invocation_expression" and static_types:
            if (
                function is not None
                and function.type == "identifier"
                and imports.unshadowed((function.text or b"").decode(), function)
            ):
                for owner in static_types:
                    add(owner, function)
    return {name: sorted(sites) for name, sites in found.items()}
