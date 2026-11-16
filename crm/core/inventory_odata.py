"""The ``odata-v2`` transform of `crm inventory convert`: OData v2 calls to the Web API (ADR 0034).

A synchronous v2 call stays synchronous. The transform rewrites the URL of an
``XMLHttpRequest`` GET that reads ``/XRMServices/2011/OrganizationData.svc`` to the Web API,
with the entity set name and the attribute names taken from a **target inventory** (never
derived by pluralising), and routes the request through one generated adapter module per
work package, ``CrmODataV2.open``, which hands the caller a v2-shaped ``responseText``
(``d.results``, ``{Value}`` for option sets and money, ``/Date()/`` dates). The consumer
code that reads the response is not touched, and the third argument of ``open`` (sync or
async) is kept as written, so control flow never changes.

Where a call is not provably the same on the Web API, it stays as written and becomes a
manual-review entry: a write, a URL shape or query option this module does not translate, a
name the target inventory does not resolve, and a call with no ``$select`` (the v2 spelling
of the response's attribute names is known only from the ``$select`` list). This module
touches no org.
"""

from __future__ import annotations

import json
import re
from collections.abc import Mapping
from dataclasses import dataclass, field
from pathlib import Path
from typing import TYPE_CHECKING

from crm.core.inventory import read_inventory
from crm.core.inventory_html import HTML_PAGE
from crm.core.inventory_transforms import Edit, Plan, Transform, Unproven, Used, register

if TYPE_CHECKING:
    from tree_sitter import Node

#: The adapter's file name in each work package folder, and the global it defines.
ADAPTER_NAME = "crm-odata-v2-adapter.js"
ADAPTER_GLOBAL = "CrmODataV2"
#: The Web API version the rewritten URLs name: every v9 target serves it.
API_VERSION = "v9.0"
#: Stands in for a non-literal piece of a URL while its literal pieces are translated.
_HOLE = "\x00"

_V2 = re.compile(rb"(?i)/XRMServices/2011/OrganizationData\.svc")
_PATH = re.compile(
    r"(?i)/XRMServices/2011/OrganizationData\.svc/(\w+)"
    r"(\(guid'(?:[0-9a-f-]{36}|\x00)'\))?(?:\?(.*))?",
    re.S,
)
#: Length of the ``(guid'`` that opens a by-key segment.
_GUID_OPEN = len("(guid'")
_LOOKUPS = frozenset(("lookup", "customer", "owner"))
#: Attribute types a v2 filter reads through ``/Value``.
_VALUES = frozenset(("picklist", "state", "status", "money"))
_KEYWORDS = frozenset(
    ("and", "or", "not", "eq", "ne", "gt", "ge", "lt", "le", "true", "false", "null")
)
_FUNCTIONS = frozenset(("startswith", "endswith"))
_NAME = re.compile(r"\w+")
_TOKEN = re.compile(
    r"""(?P<ws>\s+)
    |guid'(?P<guid>[0-9a-fA-F-]{36}|\x00)'
    |datetime'(?P<date>\d{4}-\d\d-\d\dT\d\d:\d\d:\d\d)'
    |(?P<str>'(?:[^']|'')*')
    |(?P<hole>\x00)
    |(?P<num>-?\d+(?:\.\d+)?)(?![\w.])
    |(?P<name>[A-Za-z_]\w*(?:/[A-Za-z_]\w*)*)
    |(?P<punct>[(),])""",
    re.X,
)


class _No(Exception):
    """The reason a call is not rewritten."""


class _Cut(_No):
    """A followed variable's literal cuts the URL inside a name or a query option."""

    def __init__(self, read: Node) -> None:
        super().__init__(
            "a followed variable cuts the URL inside a name or a query option, where its pieces"
            " cannot be translated apart"
        )
        #: The read that followed the variable: left unfollowed, its value stays as written.
        self.read = read


@dataclass(frozen=True)
class TargetEntity:
    logical: str
    set_name: str | None
    #: Each attribute's logical name and lower-cased type.
    attributes: Mapping[str, str]
    #: Each attribute's schema name (the v2 spelling of a write body), where recorded.
    schema_names: Mapping[str, str] = field(default_factory=dict[str, str])
    #: The DateOnly date attributes.
    date_only: frozenset[str] = frozenset()
    #: Each lookup's navigation property by the entity it points at.
    navigation: Mapping[str, Mapping[str, str]] = field(
        default_factory=dict[str, Mapping[str, str]]
    )
    #: Why a write to the entity cannot be translated, or None.
    unwritable: str | None = None


@dataclass(frozen=True)
class Target:
    """The names of a target migration inventory, by entity logical name."""

    entities: Mapping[str, TargetEntity]


_REEXTRACT = (
    "the target inventory was extracted before writes were translated: {what}; re-extract it"
    " with `crm inventory extract`"
)
#: The entities an owner lookup points at, all through its one relationship to ``owner``.
_OWNERS = ("systemuser", "team")


def read_target(folder: Path) -> Target:
    """Read the entity set names, attribute types and write names of the target inventory."""
    inv = read_inventory(folder)
    attributes: dict[str, dict[str, str]] = {}
    schema: dict[str, dict[str, str]] = {}
    dates: dict[str, set[str]] = {}
    navs: dict[str, dict[str, dict[str, str]]] = {}
    missing: dict[str, set[str]] = {}
    for r in inv.records:
        f = r.fields
        if r.kind == "attribute":
            entity, name = str(f["entity"]), str(f["logical_name"])
            kind = str(f.get("type") or "").lower()
            attributes.setdefault(entity, {})[name] = kind
            if isinstance(f.get("schema_name"), str):
                schema.setdefault(entity, {})[name] = str(f["schema_name"])
            else:
                missing.setdefault(entity, set()).add("attribute schema names")
            if kind == "datetime" and "date_behavior" not in f:
                missing.setdefault(entity, set()).add("date behaviours")
            if f.get("date_behavior") == "DateOnly":
                dates.setdefault(entity, set()).add(name)
        elif r.kind == "relationship" and f.get("type") == "one_to_many":
            entity = str(f.get("referencing_entity") or "")
            if "navigation_property" not in f:
                missing.setdefault(entity, set()).add("relationship navigation properties")
            elif f["navigation_property"]:
                to = str(f.get("referenced_entity") or "")
                lookup = navs.setdefault(entity, {}).setdefault(
                    str(f.get("referencing_attribute") or ""), {}
                )
                for each in _OWNERS if to == "owner" else (to,):
                    lookup[each] = str(f["navigation_property"])
    entities: dict[str, TargetEntity] = {}
    for r in inv.records:
        if r.kind == "entity":
            name = str(r.fields["logical_name"])
            set_name = r.fields.get("entity_set_name")
            gaps = missing.get(name)
            entities[name] = TargetEntity(
                name,
                set_name if isinstance(set_name, str) and set_name else None,
                attributes.get(name, {}),
                schema.get(name, {}),
                frozenset(dates.get(name, ())),
                navs.get(name, {}),
                _REEXTRACT.format(what=f"its records of {name} lack {', '.join(sorted(gaps))}")
                if gaps
                else None,
            )
    return Target(entities)


@dataclass(frozen=True)
class _Url:
    text: str
    entity: TargetEntity
    selected: tuple[Used, ...]
    #: Each structural boundary of the template (after the endpoint, after the entity set and
    #: key, around each query option) mapped to the same boundary in *text*: the places a URL
    #: built from several variables may be cut, each piece still translating on its own.
    cuts: Mapping[int, int]


def _field(entity: TargetEntity, name: str) -> tuple[str, str]:
    """The logical name and type of the v2 attribute *name* in *entity*, else `_No`."""
    logical = name.lower()
    kind = entity.attributes.get(logical)
    if kind is None:
        raise _No(f"{name} is not an attribute of {entity.logical} in the target inventory")
    return logical, kind


def _v4(logical: str, kind: str) -> str:
    return f"_{logical}_value" if kind in _LOOKUPS else logical


def _select(value: str, entity: TargetEntity, set_name: str) -> tuple[str, tuple[Used, ...]]:
    out: list[str] = []
    used: list[Used] = []
    for part in value.split(","):
        name = part.strip()
        if not _NAME.fullmatch(name):
            raise _No(f"$select item {part!r} is not a plain attribute name")
        logical, kind = _field(entity, name)
        out.append(_v4(logical, kind))
        used.append(Used(set_name, entity.logical, logical, name, kind))
    return ",".join(out), tuple(used)


def _orderby(value: str, entity: TargetEntity) -> str:
    out: list[str] = []
    for part in value.split(","):
        m = re.fullmatch(r"\s*(\w+)(\s+(?:asc|desc))?\s*", part)
        if m is None:
            raise _No(f"$orderby item {part!r} is not an attribute with an optional direction")
        logical, kind = _field(entity, m.group(1))
        out.append(_v4(logical, kind) + (m.group(2) or ""))
    return ",".join(out)


def _attribute(path: str, entity: TargetEntity) -> str:
    """The v4 spelling of a v2 filter operand ``Name``, ``Lookup/Id`` or ``OptionSet/Value``."""
    head, _, tail = path.partition("/")
    logical, kind = _field(entity, head)
    if not tail:
        if kind in _LOOKUPS:
            raise _No(f"{head} is a lookup: a v2 filter compares {head}/Id")
        return logical
    if tail == "Id" and kind in _LOOKUPS:
        return _v4(logical, kind)
    if tail == "Value" and kind in _VALUES:
        return logical
    raise _No(f"{path} is not translated for a {kind} attribute")


def _filter(value: str, entity: TargetEntity) -> str:
    tokens: list[tuple[str, str, str, str]] = []  # kind, text, group, leading whitespace
    pos, lead = 0, ""
    while pos < len(value):
        m = _TOKEN.match(value, pos)
        if m is None:
            raise _No(
                f"$filter has syntax this rewrite does not cover near {value[pos : pos + 20]!r}"
            )
        pos = m.end()
        kind = m.lastgroup or ""
        if kind == "ws":
            lead = m.group()
        else:
            group = m.group(kind) if kind in ("guid", "date") else ""
            tokens.append((kind, m.group(), group, lead))
            lead = ""
    out: list[str] = []
    i = 0
    while i < len(tokens):
        kind, text, group, lead = tokens[i]
        nxt = tokens[i + 1][1] if i + 1 < len(tokens) else ""
        if kind == "name" and text == "substringof":
            w = tokens[i + 1 : i + 6]
            if (
                len(w) == 5
                and [t[1] for t in (w[0], w[2], w[4])] == ["(", ",", ")"]
                and w[1][0] == "str"
                and w[3][0] == "name"
            ):
                out.append(f"{lead}contains({_attribute(w[3][1], entity)},{w[1][1]})")
                i += 6
                continue
            raise _No("substringof is translated only as substringof(<string>, <attribute>)")
        if kind == "name" and nxt == "(" and text not in _KEYWORDS:
            if text not in _FUNCTIONS:
                raise _No(f"the $filter function {text} is not translated")
            out.append(lead + text)
        elif kind == "name":
            out.append(lead + (text if text in _KEYWORDS else _attribute(text, entity)))
        elif kind == "hole":
            raise _No(
                "a non-literal piece of $filter outside a string or guid literal may be v2 syntax"
            )
        elif kind == "guid":
            out.append(lead + (group if group != _HOLE else _HOLE))
        elif kind == "date":
            out.append(f"{lead}{group}Z")
        else:
            out.append(lead + text)
        i += 1
    return "".join(out)


def _written(entity: TargetEntity, target: Target) -> tuple[Used, ...]:
    """Every attribute of the written *entity*, for the adapter's body translation."""
    out: list[Used] = []
    for logical, kind in sorted(entity.attributes.items()):
        navs = tuple(
            (to, nav, other.set_name)
            for to, nav in sorted(entity.navigation.get(logical, {}).items())
            if (other := target.entities.get(to)) is not None and other.set_name
        )
        out.append(
            Used(
                entity.set_name or "",
                entity.logical,
                logical,
                entity.schema_names[logical],
                kind,
                True,
                logical in entity.date_only,
                navs,
            )
        )
    return tuple(out)


def translate(template: str, target: Target, method: str = "GET") -> _Url:
    """Translate the v2 URL *template* (from the endpoint path on, a `_HOLE` for each non-literal
    piece) to its Web API form for the request *method* (``GET`` or a Web API write), else raise
    `_No` with why not.
    """
    m = _PATH.fullmatch(template)
    if m is None:
        raise _No("the URL shape is not covered: only <Set> and <Set>(guid'...') with a query")
    v2_set = m.group(1)
    if not v2_set.endswith("Set"):
        raise _No(f"{v2_set} is not a v2 entity set name (<SchemaName>Set)")
    entity = target.entities.get(v2_set[:-3].lower())
    if entity is None:
        raise _No(f"entity {v2_set[:-3]} is not in the target inventory")
    if entity.set_name is None:
        raise _No(f"the target inventory records no entity set name for {entity.logical}")
    key = f"({m.group(2)[_GUID_OPEN:-2]})" if m.group(2) else ""
    api = f"/api/data/{API_VERSION}"
    path = f"{api}/{entity.set_name}{key}"
    cuts = {0: 0, m.start(1) - 1: len(api), m.start(1): len(api) + 1}
    cuts[m.end(2) if m.group(2) else m.end(1)] = len(path)
    if method != "GET":
        if m.group(3) is not None:
            raise _No("a v2 write URL with a query option is not translated")
        if (method == "POST") == bool(m.group(2)):
            raise _No("a create posts to <Set>; an update or a delete names <Set>(guid'...')")
        if entity.unwritable:
            raise _No(entity.unwritable)
        if path.count(_HOLE) != template.count(_HOLE):
            raise _No("a non-literal piece of the URL sits where the rewrite cannot keep it")
        return _Url(path, entity, _written(entity, target), cuts)
    params: list[str] = []
    used: tuple[Used, ...] = ()
    # `start` walks the template's query options, `at` the translated ones: each option's
    # leading `?` or `&` and its first character are cuts.
    start, at = m.start(3), len(path) + 1
    for part in (m.group(3) or "").split("&"):
        here, start = start, start + len(part) + 1
        if not part:
            continue
        cuts[here - 1], cuts[here] = at - 1, at
        name, _, value = part.partition("=")
        if name == "$select":
            text, used = _select(value, entity, entity.set_name)
            params.append(f"$select={text}")
        elif name == "$filter":
            params.append(f"$filter={_filter(value, entity)}")
        elif name == "$orderby":
            params.append(f"$orderby={_orderby(value, entity)}")
        elif name == "$top" and re.fullmatch(r"\d+|\x00", value):
            params.append(part)
        elif name == "$inlinecount" and value == "allpages":
            params.append("$count=true")
        else:
            raise _No(f"the query option {name} is not translated")
        at += len(params[-1]) + 1
    if not used:
        raise _No("there is no $select: the v2 spelling of the response's attributes is unknown")
    text = path + ("?" + "&".join(params) if params else "")
    if text.count(_HOLE) != template.count(_HOLE):
        raise _No("a non-literal piece of the URL sits where the rewrite cannot keep it")
    cuts[len(template)] = len(text)
    return _Url(text, entity, used, cuts)


def _flatten(node: Node) -> list[Node]:
    """The operands of a ``+`` chain, left to right; any other node is one operand."""
    if node.type == "parenthesized_expression" and node.named_child_count == 1:
        return _flatten(node.named_children[0])
    op = node.child_by_field_name("operator")
    left, right = node.child_by_field_name("left"), node.child_by_field_name("right")
    if node.type == "binary_expression" and op is not None and op.text == b"+" and left and right:
        return _flatten(left) + _flatten(right)
    return [node]


def _literal(node: Node) -> str | None:
    """The text of a plain string literal that holds no escape or line break, else None."""
    text = (node.text or b"").decode("utf-8", "replace")
    if node.type != "string" or "\\" in text or "\n" in text:
        return None
    return text[1:-1]


_PARAM_PARENTS = frozenset(
    ("formal_parameters", "assignment_pattern", "rest_pattern", "catch_clause")
)
_SCOPES = frozenset(
    (
        "program",
        "function_declaration",
        "function_expression",
        "function",
        "generator_function",
        "generator_function_declaration",
        "arrow_function",
        "method_definition",
    )
)


def _scope(node: Node) -> int:
    """The id of the function or program *node* sits in: its binding scope, as `var` hoists."""
    cur = node.parent
    while cur is not None and cur.type not in _SCOPES:
        cur = cur.parent
    return cur.id if cur is not None else -1


def _encloses(scope: int, node: Node) -> bool:
    """True when the function or program *scope* (an id) holds *node*."""
    cur = node.parent
    while cur is not None and cur.id != scope:
        cur = cur.parent
    return cur is not None


def _xhr_names(root: Node) -> frozenset[tuple[int, str]]:
    """The (scope, name) pairs every binding of which is `new XMLHttpRequest()` in that scope.

    The adapter assumes the native request object, so a name also bound to anything else in the
    scope (a parameter, another value, a reassignment) is not proven one, and a call in another
    function than the declaration is not tied to it.
    """
    xhr: set[tuple[int, str]] = set()
    other: set[tuple[int, str]] = set()
    stack = [root]
    while stack:
        node = stack.pop()
        stack.extend(node.children)
        name: Node | None = None
        value: Node | None = None
        if node.type == "variable_declarator":
            name, value = node.child_by_field_name("name"), node.child_by_field_name("value")
        elif node.type == "assignment_expression":
            name, value = node.child_by_field_name("left"), node.child_by_field_name("right")
        elif node.type == "identifier" and node.parent is not None:
            parent = node.parent
            if parent.type in _PARAM_PARENTS or (
                parent.type == "arrow_function" and parent.child_by_field_name("parameter") == node
            ):
                other.add((_scope(node), (node.text or b"").decode()))
        if name is None or name.type != "identifier":
            continue
        key = (_scope(name), (name.text or b"").decode())
        ctor = value.child_by_field_name("constructor") if value is not None else None
        is_xhr = value is not None and value.type == "new_expression" and ctor is not None
        (xhr if is_xhr and ctor and ctor.text == b"XMLHttpRequest" else other).add(key)
    return frozenset(xhr - other)


#: A name's binding: the id of the function or program that declares it, and the name.
_Binding = tuple[int, str]
_DECLARATIONS = frozenset(
    (
        "function_declaration",
        "generator_function_declaration",
        "class_declaration",
        "function_expression",
        "function",
        "generator_function",
    )
)
_HOISTED = frozenset(
    ("function_declaration", "generator_function_declaration", "class_declaration")
)
_PATTERNS = frozenset(("object_pattern", "array_pattern", "rest_pattern"))
_OUTSIDE = (
    "an OData v2 URL outside a <request>.open(<method>, <url>) call is a shape this rewrite"
    " does not cover"
)


def _text(node: Node) -> str:
    return (node.text or b"").decode("utf-8", "replace")


def _field_is(parent: Node, field: str, node: Node) -> bool:
    return parent.child_by_field_name(field) == node


_PATTERN_PARTS = frozenset(
    (*_PATTERNS, "pair_pattern", "assignment_pattern", "object_assignment_pattern")
)


def _pattern_home(node: Node) -> tuple[Node, Node | None]:
    """The outermost destructuring pattern holding *node* (or *node* itself), and its parent."""
    top = node
    while top.parent is not None and top.parent.type in _PATTERN_PARTS:
        top = top.parent
    return top, top.parent


def _declares(node: Node) -> str | None:
    """How the identifier *node* declares its name (``var``, ``param``, ``other``), or None.

    A name in a destructuring assignment (``[a] = x``) declares nothing: it is a write.
    """
    p = node.parent
    if p is None:
        return None
    if p.type in _DECLARATIONS:
        return "other" if _field_is(p, "name", node) else None
    if p.type in ("assignment_pattern", "object_assignment_pattern") and not _field_is(
        p, "left", node
    ):
        return None
    if p.type == "pair_pattern" and not _field_is(p, "value", node):
        return None
    top, home = _pattern_home(node)
    if home is None:
        return None
    if home.type == "variable_declarator":
        if not _field_is(home, "name", top):
            return None
        if top is not node:
            return "other"
        # A `let` or `const` counts as the function's own only at the top of the program or
        # of a function body; one in a nested block or a `for` head, the program's included,
        # binds only there, which the scope walk (one scope per function) cannot place.
        decl = home.parent
        body = decl.parent if decl is not None else None
        if (
            decl is not None
            and decl.type == "lexical_declaration"
            and not (
                body is not None
                and (
                    body.type == "program"
                    or (
                        body.type == "statement_block"
                        and body.parent is not None
                        and body.parent.type in _SCOPES - {"program"}
                    )
                )
            )
        ):
            return "other"
        return "var"
    if home.type in ("formal_parameters", "catch_clause") or (
        home.type == "arrow_function" and _field_is(home, "parameter", top)
    ):
        return "param"
    if home.type == "for_in_statement" and _field_is(home, "left", top):
        return "other" if any(c.type in ("var", "let", "const") for c in home.children) else None
    return None


class _Scopes:
    """Where each name of one file is declared, how often it is written, and each read of it.

    Every `var`, `let` and `const` binds in its function (block scopes are not told apart, so
    two blocks declaring one name count as two writes). The follow takes a name only where this
    proves one binding with one write; anything it cannot read counts against following.
    """

    def __init__(self, root: Node) -> None:
        self.kinds: dict[_Binding, set[str]] = {}
        self.values: dict[_Binding, list[Node]] = {}
        self.writes: dict[_Binding, int] = {}
        self.reads: dict[_Binding, list[Node]] = {}
        nodes: list[Node] = []
        stack = [root]
        while stack:
            node = stack.pop()
            nodes.append(node)
            stack.extend(node.children)
        for node in nodes:
            if node.type in ("identifier", "shorthand_property_identifier_pattern"):
                kind = _declares(node)
                if kind is not None:
                    p = node.parent
                    # A declaration's name binds outside it; a function expression's, inside.
                    if p is not None and p.type in _HOISTED:
                        where = _scope(p)
                    elif p is not None and p.type in _DECLARATIONS:
                        where = p.id
                    else:
                        where = _scope(node)
                    self.kinds.setdefault((where, _text(node)), set()).add(kind)
        for node in nodes:
            target: Node | None = None
            value: Node | None = None
            if node.type == "variable_declarator":
                target, value = node.child_by_field_name("name"), node.child_by_field_name("value")
                if value is None:
                    continue
            elif node.type == "assignment_expression":
                target, value = node.child_by_field_name("left"), node.child_by_field_name("right")
            elif node.type in ("augmented_assignment_expression", "for_in_statement"):
                target = node.child_by_field_name("left")
            elif node.type == "update_expression":
                target = node.child_by_field_name("argument")
            elif node.type in ("identifier", "shorthand_property_identifier"):
                b = self.resolve(node)
                if b is not None and not self._binds(node):
                    self.reads.setdefault(b, []).append(node)
                continue
            if target is not None and target.type in _PATTERN_PARTS:
                # Each name a destructuring write reaches gets a write no follow can read. Names
                # in a default value or a computed key count too: that refuses more, never less.
                stack2 = [target]
                while stack2:
                    part = stack2.pop()
                    stack2.extend(part.children)
                    if part.type in ("identifier", "shorthand_property_identifier_pattern"):
                        hit = self.resolve(part)
                        if hit is not None:
                            self.writes[hit] = self.writes.get(hit, 0) + 2
                continue
            if target is None or target.type != "identifier":
                continue
            b = self.resolve(target)
            if b is None:
                continue
            # A compound write (`+=`, `++`, a for-in) is never the one write a follow needs.
            self.writes[b] = self.writes.get(b, 0) + (1 if value is not None else 2)
            if value is not None:
                self.values.setdefault(b, []).append(value)

    @staticmethod
    def _binds(node: Node) -> bool:
        p = node.parent
        if p is None:
            return False
        if p.type in ("assignment_expression", "augmented_assignment_expression"):
            return _field_is(p, "left", node)
        return p.type == "update_expression" or _declares(node) is not None

    def resolve(self, node: Node) -> _Binding | None:
        """The binding the name *node* refers to, or None for a name this file never declares."""
        name = _text(node)
        cur = node.parent
        while cur is not None:
            if cur.type in _SCOPES and (cur.id, name) in self.kinds:
                return (cur.id, name)
            cur = cur.parent
        return None

    def why_not(self, b: _Binding) -> str | None:
        """Why the follow cannot take *b* to its value, or None when it can."""
        name = b[1]
        kinds = self.kinds[b]
        if "param" in kinds:
            return f"`{name}` is a function parameter"
        if kinds != {"var"}:
            return f"`{name}` is not a plain variable"
        if self.writes.get(b, 0) > 1:
            return f"`{name}` is assigned more than once"
        if not self.values.get(b):
            return f"`{name}` is never assigned"
        return None


@dataclass(frozen=True)
class _Leaf:
    """One operand of a URL once its variables are followed: a string literal, or a hole."""

    node: Node
    #: The `+` chain the leaf is an operand of: literals next to each other in one chain are one
    #: edit, as an inline URL's are.
    chain: Node
    literal: bool
    #: Each variable followed to reach the leaf, with the read that followed it.
    via: tuple[tuple[_Binding, Node], ...]
    #: For a hole, where and why the follow stopped, if it was a follow stop.
    stop: str | None = None


def _expand(
    node: Node,
    scopes: _Scopes,
    keep: frozenset[int] = frozenset(),
    via: tuple[tuple[_Binding, Node], ...] = (),
) -> list[_Leaf]:
    """The leaves of the URL expression *node*, a variable assigned once replaced by its value,
    except at the reads whose ids are in *keep*.
    """
    out: list[_Leaf] = []
    for p in _flatten(node):
        stop: str | None = None
        if p.type == "string":
            out.append(_Leaf(p, node, True, via))
            continue
        if p.type == "identifier":
            b = scopes.resolve(p)
            if p.id in keep:
                pass
            elif b is None:
                stop = f"`{_text(p)}` is not declared in this file"
            elif any(b == seen for seen, _ in via):
                stop = f"`{_text(p)}` refers to itself"
            else:
                stop = scopes.why_not(b)
                if stop is None and not _encloses(_scope(scopes.values[b][0]), p):
                    stop = _not_enclosing(b)
                if stop is None:
                    out.extend(_expand(scopes.values[b][0], scopes, keep, (*via, (b, p))))
                    continue
        elif p.type == "call_expression":
            stop = (
                f"`{via[-1][0][1]}` holds" if via else "it is"
            ) + " a value returned from a function"
        out.append(_Leaf(p, node, False, via, stop))
    return out


@dataclass
class _Site:
    call: Node
    leaves: list[_Leaf]
    #: The v2 endpoint literals the URL reaches.
    hits: list[Node]
    #: Each variable whose literals the rewrite edits, with the ids of the reads that reach it.
    via: dict[_Binding, set[int]]
    edits: list[Edit] = field(default_factory=list[Edit])
    selected: tuple[Used, ...] = ()
    reason: str | None = None
    #: The reason is the site's own, not a refusal shared through a variable.
    own: bool = True
    #: The reads left unfollowed, and the read a `_Cut` asks to leave unfollowed next.
    keep: frozenset[int] = frozenset()
    cut: int | None = None


def _attempt(
    call: Node,
    url: Node,
    scopes: _Scopes,
    keep: frozenset[int],
    target: Target | None,
    xhr: frozenset[tuple[int, str]],
) -> _Site | None:
    """The ``open`` *call* planned with its *url* followed (except at *keep*), or None when the URL
    reaches no v2 literal.
    """
    leaves = _expand(url, scopes, keep)
    hits = [x for x in leaves if x.literal and _V2.search(x.node.text or b"")]
    if not hits:
        return None
    via: dict[_Binding, set[int]] = {}
    for leaf in leaves[leaves.index(hits[0]) :]:
        if leaf.literal:
            for b, read in leaf.via:
                via.setdefault(b, set()).add(read.id)
    site = _Site(call, leaves, [x.node for x in hits], via, keep=keep)
    try:
        if target is None:
            raise _No("no --target-inventory: the Web API names cannot be resolved")
        site.edits, site.selected = _site(call, leaves, target, xhr)
    except _Cut as why:
        site.reason = str(why)
        site.cut = why.read.id if why.read.id not in keep else None
    except _No as why:
        site.reason = str(why)
    return site


def _site(
    call: Node, leaves: list[_Leaf], target: Target, xhr: frozenset[tuple[int, str]]
) -> tuple[list[Edit], tuple[Used, ...]]:
    fn = call.child_by_field_name("function")
    args = call.child_by_field_name("arguments")
    recv = fn.child_by_field_name("object") if fn is not None else None
    if fn is None or args is None or recv is None or len(args.named_children) < 2:
        raise _No("the call is not <request>.open(<method>, <url>, ...)")
    if recv.type != "identifier":
        raise _No("the receiver of open is not a plain variable")
    if any(c.type == "optional_chain" for c in (*fn.children, *call.children)):
        raise _No("an optional chain: the rewrite would drop its short-circuit")
    method_node = args.named_children[0]
    method = _literal(method_node)
    if method is None or method.upper() not in ("GET", "POST"):
        raise _No("the method is not the literal GET or POST")
    method = method.upper()
    header: Node | None = None
    if method == "POST":
        method, header = _write_method(call, recv)
    if b"\n" in (call.text or b"")[: args.start_byte + 1 - call.start_byte]:
        raise _No("the call head spans lines")

    hits = [i for i, x in enumerate(leaves) if x.literal and _V2.search(x.node.text or b"")]
    if len(hits) != 1 or sum(len(_V2.findall(x.node.text or b"")) for x in leaves) != 1:
        raise _No("the URL names the v2 endpoint other than once in a literal")
    tail = leaves[hits[0] :]
    first = _literal(tail[0].node)
    if first is None:
        raise _No("the URL literal holds an escape or a line break")
    at = _V2.search((tail[0].node.text or b"")[1:-1])
    prefix = first[: at.start()] if at is not None else ""
    # Places: runs of literals next to each other in one chain, each one edit; holes between.
    places: list[list[_Leaf]] = []
    holes: list[_Leaf] = []
    template = ""
    spans: list[tuple[int, int]] = []
    last: _Leaf | None = None
    for leaf in tail:
        if not leaf.literal:
            holes.append(leaf)
            template += _HOLE
        else:
            piece = _literal(leaf.node)
            if piece is None:
                raise _No("a URL literal holds an escape or a line break")
            if (
                last is not None
                and last.literal
                and (last.chain, last.via) == (leaf.chain, leaf.via)
            ):
                places[-1].append(leaf)
                spans[-1] = (spans[-1][0], len(template) + len(piece))
            else:
                places.append([leaf])
                spans.append((len(template), len(template) + len(piece)))
            template += piece
        last = leaf
    template = template[len(prefix) :]
    spans = [(max(s - len(prefix), 0), e - len(prefix)) for s, e in spans]
    named = re.match(r"(?i)/XRMServices/2011/OrganizationData\.svc/([^(?]*)", template)
    if named is not None and _HOLE in named.group(1):
        stop = holes[template[: named.start(1) + named.group(1).index(_HOLE)].count(_HOLE)].stop
        raise _No("the entity set name is not a literal" + (f": {stop}" if stop else ""))
    result = translate(template, target, method)
    if (_scope(call), (recv.text or b"").decode()) not in xhr:
        raise _No(
            "the receiver of open is not a variable bound only to new XMLHttpRequest() in its own"
            " function: the adapter needs the native request object"
        )
    cuts = dict(result.cuts)
    new_holes = [i for i, c in enumerate(result.text) if c == _HOLE]
    old_holes = [i for i, c in enumerate(template) if c == _HOLE]
    for h, g in zip(old_holes, new_holes, strict=True):
        for a, b in ((h, g), (h + 1, g + 1)):
            if cuts.setdefault(a, b) != b:
                raise _No("a non-literal piece of the URL sits where the rewrite cannot keep it")
    uncut = [p for p, (s, e) in zip(places, spans, strict=True) if s not in cuts or e not in cuts]
    if uncut:
        # Cut at the innermost variable, so an outer one that also holds v2 literals stays
        # followed (`url` built from a filter value `name` keeps `name` as written).
        followed = max(uncut, key=lambda p: len(p[0].via))
        if followed[0].via:
            raise _Cut(followed[0].via[-1][1])
        raise _No("a non-literal piece of the URL sits where the rewrite cannot keep it")
    covered = set(new_holes)
    edits: list[Edit] = []
    for n, (place, (s, e)) in enumerate(zip(places, spans, strict=True)):
        segment = result.text[cuts[s] : cuts[e]]
        covered.update(range(cuts[s], cuts[e]))
        quote = (place[0].node.text or b"")[:1]
        text = (prefix if n == 0 else "") + segment
        if quote.decode() in text:
            raise _No("the translated URL contains the string's own quote")
        chain = place[0].chain
        span = (chain.text or b"")[
            place[0].node.start_byte - chain.start_byte : place[-1].node.end_byte - chain.start_byte
        ]
        edits.append(
            Edit(
                place[0].node.start_byte,
                place[-1].node.end_byte,
                quote + text.encode() + quote + b"\n" * span.count(b"\n"),
            )
        )
    if len(covered) != len(result.text):
        raise _No("a translated segment has no literal to hold it")
    edits.append(
        Edit(
            fn.start_byte,
            args.start_byte + 1,
            f"{ADAPTER_GLOBAL}.open({(recv.text or b'').decode()}, ".encode(),
        )
    )
    if header is not None:
        quote = (method_node.text or b'"')[:1]
        edits.append(
            Edit(method_node.start_byte, method_node.end_byte, quote + method.encode() + quote)
        )
        edits.append(Edit(header.start_byte, header.end_byte, b""))
    return edits, result.selected


def _write_method(call: Node, recv: Node) -> tuple[str, Node | None]:
    """The Web API method of the v2 ``POST`` *call* and the ``X-HTTP-Method`` statement to
    remove: ``POST`` and None for a create, else ``PATCH`` (``MERGE``) or ``DELETE``.

    Only one ``<recv>.setRequestHeader("X-HTTP-Method", <MERGE or DELETE>)`` statement in the
    call's own function is read; any other shape of that header raises `_No`.
    """
    name = recv.text or b""
    scope = _scope(call)
    found: list[Node] = []
    root = call
    while root.parent is not None:
        root = root.parent
    stack = [root]
    while stack:
        node = stack.pop()
        stack.extend(node.children)
        fn = node.child_by_field_name("function") if node.type == "call_expression" else None
        obj = fn.child_by_field_name("object") if fn and fn.type == "member_expression" else None
        prop = fn.child_by_field_name("property") if fn and fn.type == "member_expression" else None
        args = node.child_by_field_name("arguments")
        if obj is None or prop is None or args is None or prop.text != b"setRequestHeader":
            continue
        first = _literal(args.named_children[0]) if args.named_children else None
        if obj.text == name and first is not None and first.lower() == "x-http-method":
            found.append(node)
    if not found:
        return "POST", None
    if any(_scope(n) != scope for n in found):
        raise _No("X-HTTP-Method is set on the request in another function")
    if len(found) > 1:
        raise _No("X-HTTP-Method is set on the request more than once")
    (node,) = found
    args = node.child_by_field_name("arguments")
    value = _literal(args.named_children[1]) if args and len(args.named_children) == 2 else None
    if value is None:
        raise _No("the X-HTTP-Method value is not a literal")
    method = {"MERGE": "PATCH", "DELETE": "DELETE"}.get(value.upper())
    if method is None:
        raise _No(f"the X-HTTP-Method {value} is not MERGE or DELETE")
    statement = node.parent
    if statement is None or statement.type != "expression_statement":
        raise _No("the X-HTTP-Method call is not a statement of its own")
    return method, statement


def _not_enclosing(b: _Binding) -> str:
    """The stop for a variable written in a function the reading call is not inside."""
    return f"`{b[1]}` is assigned in a function that does not enclose the call"


def _opens(node: Node) -> bool:
    """Whether *node* is a call to a member named ``open``: ``<x>.open(...)``."""
    fn = node.child_by_field_name("function") if node.type == "call_expression" else None
    name = fn.child_by_field_name("property") if fn and fn.type == "member_expression" else None
    return name is not None and name.text == b"open"


def _shared(
    via: Mapping[_Binding, set[int]], scopes: _Scopes, reached: Mapping[_Binding, set[int]]
) -> _Binding | None:
    """The first followed variable in *via* with a read outside *reached*, if any."""
    return next((b for b in via if {r.id for r in scopes.reads.get(b, [])} - reached[b]), None)


def _unreached(s: Node, scopes: _Scopes) -> str:
    """Why the v2 literal *s*, which no ``open`` call's URL reaches, is not rewritten."""
    top = s
    while top.parent is not None and top.parent.type in (
        "binary_expression",
        "parenthesized_expression",
    ):
        top = top.parent
    p = top.parent
    if p is None:
        return _OUTSIDE
    if p.type == "return_statement":
        return "an OData v2 URL returned from a function: the follow stops at a return value"
    if p.type == "arguments" and p.parent is not None and not _opens(p.parent):
        return (
            "an OData v2 URL passed to a function other than <request>.open: the follow"
            " stops at a function parameter"
        )
    held: Node | None = None
    if p.type == "variable_declarator" and _field_is(p, "value", top):
        held = p.child_by_field_name("name")
    elif p.type in ("assignment_expression", "augmented_assignment_expression") and _field_is(
        p, "right", top
    ):
        held = p.child_by_field_name("left")
    if held is not None and held.type == "identifier":
        b = scopes.resolve(held)
        why = scopes.why_not(b) if b is not None else None
        if (
            b is not None
            and why is None
            and not all(_encloses(_scope(top), r) for r in scopes.reads.get(b, []))
        ):
            why = _not_enclosing(b)
        if why:
            return f"an OData v2 URL held in a variable the follow cannot take: {why}"
    return _OUTSIDE


def _plan_odata(root: Node, target: Target | None) -> Plan:
    stack = [root]
    strings: list[Node] = []
    #: Each `<x>.open(...)` call with its URL argument.
    calls: list[tuple[Node, Node]] = []
    while stack:
        node = stack.pop()
        stack.extend(node.children)
        if node.type in ("string", "template_string") and _V2.search(node.text or b""):
            strings.append(node)
        args = node.child_by_field_name("arguments")
        if _opens(node) and args and len(args.named_children) >= 2:
            calls.append((node, args.named_children[1]))
    scopes = _Scopes(root)
    xhr = _xhr_names(root)
    sites: list[_Site] = []
    for call, url in calls:
        site = _attempt(call, url, scopes, frozenset(), target, xhr)
        # A variable whose literal sits inside a query option (a filter value, say) is kept as
        # written, a non-literal piece, as it would be if the follow had not taken it.
        while site is not None and isinstance(site.cut, int):
            retry = _attempt(call, url, scopes, site.keep | {site.cut}, target, xhr)
            if retry is None:
                break
            site = retry
        if site is not None:
            sites.append(site)
    # All or nothing per variable: a followed variable is rewritten only when every read of it
    # sits in a call that converts, and every such call wants the same text from it.
    changed = True
    while changed:
        changed = False
        ok = [x for x in sites if x.reason is None]
        reached: dict[_Binding, set[int]] = {}
        wanted: dict[tuple[int, int], set[bytes]] = {}
        for x in ok:
            for b, reads in x.via.items():
                reached.setdefault(b, set()).update(reads)
            for e in x.edits:
                wanted.setdefault((e.start, e.end), set()).add(e.text)
        for x in ok:
            shared = _shared(x.via, scopes, reached)
            if shared is not None:
                x.reason, x.own = (
                    (
                        f"`{shared[1]}` also feeds a call or a use this rewrite does not convert:"
                        " a shared variable is rewritten for every use or for none"
                    ),
                    False,
                )
            elif any(len(wanted[(e.start, e.end)]) > 1 for e in x.edits):
                x.reason, x.own = "two calls need different rewrites of one shared literal", False
            changed = changed or x.reason is not None
    unproven: list[Unproven] = []
    for x in sites:
        if x.reason is None:
            continue
        reason = x.reason
        if x.own:
            mine = set[int]().union(*x.via.values())
            shared = _shared(x.via, scopes, dict.fromkeys(x.via, mine))
            if shared is not None:
                reason += f"; `{shared[1]}` is shared, so the calls that read it stay unchanged too"
        unproven.append(Unproven.at(x.call, reason))
    seen = {h.id for x in sites for h in x.hits}
    unproven.extend(Unproven.at(s, _unreached(s, scopes)) for s in strings if s.id not in seen)
    done = [x for x in sites if x.reason is None]
    edits = sorted(
        {(e.start, e.end, e.text): e for x in done for e in x.edits}.values(), key=lambda e: e.start
    )
    unproven.sort(key=lambda u: u.line)
    return Plan(
        tuple(edits),
        tuple(unproven),
        cleared=len({h.id for x in done for h in x.hits}),
        adapter=tuple(u for x in done for u in x.selected),
    )


register("odata-v2", Transform("javascript", (HTML_PAGE, 3), _plan_odata, needs_target=True))

# ── the adapter module ──────────────────────────────────────────────────────

_KINDS = {
    "lookup": "lookup",
    "customer": "lookup",
    "owner": "lookup",
    "picklist": "option",
    "state": "option",
    "status": "option",
    "money": "money",
    "datetime": "date",
    "decimal": "string",
    "bigint": "string",
}

_ADAPTER = r"""/* Generated by `crm inventory convert` (ADR 0034): do not edit, re-run convert.
   Load this web resource before the scripts that call CrmODataV2.open. */
(function (global) {
  "use strict";
  // entity set -> { entity, attrs: logical name -> [the v2 spelling, the adapter kind] }
  var TABLE = __TABLE__;
  var SET = /\/api\/data\/v[0-9.]+\/([^\/(?]+)/;
  var RAW = Object.getOwnPropertyDescriptor(XMLHttpRequest.prototype, "responseText");

  // v2 never returns null for an option set, money or lookup: the empty form is an object.
  function convert(kind, value) {
    if (value === null || value === undefined) {
      return kind === "option" || kind === "money" ? { Value: null } : null;
    }
    if (kind === "option") { return { Value: value }; }
    if (kind === "money") { return { Value: String(value) }; }
    if (kind === "date") { return "/Date(" + Date.parse(value) + ")/"; }
    if (kind === "string") { return String(value); }
    return value;
  }

  function row(entity, source) {
    var out = {};
    Object.keys(source).forEach(function (key) {
      if (key.indexOf("@") >= 0) { return; }
      var lookup = /^_(.+)_value$/.exec(key);
      var def = entity.attrs[lookup ? lookup[1] : key] || [lookup ? lookup[1] : key, "plain"];
      out[def[0]] = lookup
        ? (source[key] === null ? { Id: null, LogicalName: null, Name: null } : {
            Id: source[key],
            LogicalName: source[key + "@Microsoft.Dynamics.CRM.lookuplogicalname"],
            Name: source[key + "@OData.Community.Display.V1.FormattedValue"]
          })
        : convert(def[1], source[key]);
    });
    return out;
  }

  function toV2(set, text) {
    var body = JSON.parse(text);
    if (body.error) {
      var message = body.error.message;
      return JSON.stringify({ error: { code: body.error.code,
        message: { lang: "en-US", value: typeof message === "string" ? message : "" } } });
    }
    var entity = TABLE[set] || { attrs: {} };
    var d;
    if (Array.isArray(body.value)) {
      d = { results: body.value.map(function (r) { return row(entity, r); }) };
      if (body["@odata.count"] !== undefined) { d.__count = String(body["@odata.count"]); }
    } else {
      d = row(entity, body);
    }
    return JSON.stringify({ d: d });
  }

  // A v2 write body to the Web API's, by TABLE[set].write: schema name -> [logical name, the
  // write kind, { entity it can point at: [navigation property, its entity set] }]. A value it
  // cannot map throws before anything is sent: a wrong write corrupts data.
  var GUID = /^\{?([0-9a-fA-F]{8}-(?:[0-9a-fA-F]{4}-){3}[0-9a-fA-F]{12})\}?$/;
  var V2DATE = /^\/Date\((-?[0-9]+)(?:[+-][0-9]{4})?\)\/$/;
  var DAY = /^[0-9]{4}-[0-9]{2}-[0-9]{2}$/;

  function fail(why) { throw new Error("CrmODataV2: " + why + "; nothing was sent"); }

  function pad(n) { return (n < 10 ? "0" : "") + n; }

  function number(where, value) {
    var n = typeof value === "string" && value.trim() !== "" ? Number(value) : value;
    if (typeof n !== "number" || !isFinite(n)) { fail(where + " is not a number"); }
    return n;
  }

  function wrapped(where, value, what) {
    if (value === null || typeof value !== "object" || !("Value" in value)) {
      fail(where + " is " + what + " written as {Value: ...}");
    }
    return value.Value;
  }

  function date(where, value, dateOnly) {
    if (dateOnly && typeof value === "string" && DAY.test(value)) { return value; }
    var m = typeof value === "string" ? V2DATE.exec(value) : null;
    var ms = m ? Number(m[1]) : (typeof value === "string" ? Date.parse(value) : NaN);
    if (isNaN(ms)) { fail(where + " is a date neither /Date(n)/ nor ISO 8601"); }
    var d = new Date(ms);
    // A DateOnly column (Edm.Date) takes no time part: the calendar day the script built.
    return dateOnly ? d.getFullYear() + "-" + pad(d.getMonth() + 1) + "-" + pad(d.getDate())
      : d.toISOString();
  }

  function bind(out, where, kind, navs, value) {
    var targets = Object.keys(navs).sort();
    if (value === null) {
      // A customer's navigation properties each clear it, whatever it points at (live probe).
      if ((kind === "customer" && targets.length) || (kind === "lookup" && targets.length === 1)) {
        out[navs[targets[0]][0] + "@odata.bind"] = null;
        return;
      }
      fail(where + " is a " + kind + " lookup no one navigation property clears");
    }
    var id = value && typeof value.Id === "string" ? GUID.exec(value.Id) : null;
    if (!id || typeof value.LogicalName !== "string") {
      fail(where + " is a lookup not written as {Id, LogicalName}");
    }
    var nav = navs[value.LogicalName];
    if (!nav) { fail(where + " cannot point at " + value.LogicalName + " in the target"); }
    out[nav[0] + "@odata.bind"] = "/" + nav[1] + "(" + id[1] + ")";
  }

  function toWebApi(table, body) {
    var out = {};
    Object.keys(body).forEach(function (name) {
      if (name === "__metadata") { return; }
      var where = table.entity + "." + name;
      var def = table.write[name];
      if (!def) { fail(where + " is not an attribute in the target inventory"); }
      var logical = def[0], kind = def[1], value = body[name];
      if (kind === "lookup" || kind === "customer" || kind === "owner") {
        bind(out, where, kind, def[2], value);
        return;
      }
      if (kind === "none") { fail(where + " has a type a write does not translate"); }
      if (value === null) { out[logical] = null; return; }
      if (kind === "option") {
        value = wrapped(where, value, "an option set");
        if (value !== null && (typeof value !== "number" || value % 1 !== 0)) {
          fail(where + " is an option set whose Value is not an integer");
        }
      } else if (kind === "money") {
        value = wrapped(where, value, "money");
        value = value === null ? null : number(where, value);
      } else if (kind === "date" || kind === "dateonly") {
        value = date(where, value, kind === "dateonly");
      } else if (kind === "number") {
        value = number(where, value);
      } else if (typeof value !== kind) {
        fail(where + " is not a " + kind);
      }
      out[logical] = value;
    });
    return out;
  }

  // A converted write: the method was translated at convert time, so an X-HTTP-Method header
  // would turn an update or a delete back into a create. The body is translated at send.
  function guard(xhr, method, table) {
    var header = xhr.setRequestHeader, send = xhr.send, bad = false;
    xhr.setRequestHeader = function (name) {
      if (String(name).toLowerCase() === "x-http-method") {
        bad = true;
        fail("X-HTTP-Method is set on a converted " + method);
      }
      return header.apply(xhr, arguments);
    };
    xhr.send = function (body) {
      if (bad) { fail("X-HTTP-Method is set on a converted " + method); }
      if (method !== "DELETE" && body !== null && body !== undefined) {
        var parsed;
        try { parsed = JSON.parse(body); } catch (e) { fail(method + " body is not JSON"); }
        if (parsed === null || typeof parsed !== "object" || Array.isArray(parsed)) {
          fail(method + " body is not a JSON object");
        }
        body = JSON.stringify(toWebApi(table, parsed));
      }
      return send.call(xhr, body);
    };
  }

  global.CrmODataV2 = {
    // A no-op: a ribbon command calls it as its first action so this file loads before the rest.
    load: function () {},
    toV2: toV2,
    open: function (xhr, method, url) {
      var set = SET.exec(String(url));
      xhr.open.apply(xhr, Array.prototype.slice.call(arguments, 1));
      xhr.setRequestHeader("OData-MaxVersion", "4.0");
      xhr.setRequestHeader("OData-Version", "4.0");
      // A create returns its record, as v2 did.
      xhr.setRequestHeader("Prefer", (method === "POST" ? "return=representation," : "") +
        'odata.include-annotations="*"');
      // A PATCH on a missing key would create the record; v2 MERGE failed instead.
      if (method === "PATCH") { xhr.setRequestHeader("If-Match", "*"); }
      if (method !== "GET") {
        guard(xhr, method, (set && TABLE[set[1]]) || { entity: "", write: {} });
      }
      var seen, converted;
      Object.defineProperty(xhr, "responseText", { configurable: true, get: function () {
        var text = RAW.get.call(xhr);
        if (xhr.readyState !== 4 || !text || !set) { return text; }
        if (text !== seen) {
          try { converted = toV2(set[1], text); } catch (e) { converted = text; }
          seen = text;
        }
        return converted;
      } });
    }
  };
})(typeof window !== "undefined" ? window : globalThis);
"""

#: The adapter's write kind for each attribute type; any other type is "none" and throws.
_WRITE_KINDS = {
    "lookup": "lookup",
    "customer": "customer",
    "owner": "owner",
    "picklist": "option",
    "state": "option",
    "status": "option",
    "money": "money",
    "datetime": "date",
    "integer": "number",
    "bigint": "number",
    "decimal": "number",
    "double": "number",
    "string": "string",
    "memo": "string",
    "uniqueidentifier": "string",
    "boolean": "boolean",
}


def render_adapter(used: set[Used]) -> str:
    """The adapter module for the attributes *used* by a work package's rewritten calls."""
    table: dict[str, tuple[str, dict[str, list[str]], dict[str, list[object]]]] = {}
    for u in sorted(used):
        _, attrs, write = table.setdefault(u.set_name, (u.entity, {}, {}))
        attrs.setdefault(u.attribute, [u.v2_name, _KINDS.get(u.kind, "plain")])
        if u.write:
            kind = "dateonly" if u.date_only else _WRITE_KINDS.get(u.kind, "none")
            write[u.v2_name] = [u.attribute, kind, {t: [n, s] for t, n, s in u.navs}]
    table_json = {
        k: {"entity": e, "attrs": a, **({"write": w} if w else {})}
        for k, (e, a, w) in table.items()
    }
    return _ADAPTER.replace("__TABLE__", json.dumps(table_json, sort_keys=True, indent=2))
