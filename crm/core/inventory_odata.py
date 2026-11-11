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
from dataclasses import dataclass
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


@dataclass(frozen=True)
class TargetEntity:
    logical: str
    set_name: str | None
    #: Each attribute's logical name and lower-cased type.
    attributes: Mapping[str, str]


@dataclass(frozen=True)
class Target:
    """The names of a target migration inventory, by entity logical name."""

    entities: Mapping[str, TargetEntity]


def read_target(folder: Path) -> Target:
    """Read the entity set names and attribute types of the target inventory at *folder*."""
    inv = read_inventory(folder)
    attributes: dict[str, dict[str, str]] = {}
    for r in inv.records:
        if r.kind == "attribute":
            attributes.setdefault(str(r.fields["entity"]), {})[str(r.fields["logical_name"])] = str(
                r.fields.get("type") or ""
            ).lower()
    entities: dict[str, TargetEntity] = {}
    for r in inv.records:
        if r.kind == "entity":
            name = str(r.fields["logical_name"])
            set_name = r.fields.get("entity_set_name")
            entities[name] = TargetEntity(
                name,
                set_name if isinstance(set_name, str) and set_name else None,
                attributes.get(name, {}),
            )
    return Target(entities)


@dataclass(frozen=True)
class _Url:
    text: str
    entity: TargetEntity
    selected: tuple[Used, ...]


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


def translate(template: str, target: Target) -> _Url:
    """Translate the v2 URL *template* (from the endpoint path on, a `_HOLE` for each non-literal
    piece) to its Web API form, else raise `_No` with why not.
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
    params: list[str] = []
    used: tuple[Used, ...] = ()
    for part in (m.group(3) or "").split("&"):
        if not part:
            continue
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
    if not used:
        raise _No("there is no $select: the v2 spelling of the response's attributes is unknown")
    path = f"/api/data/{API_VERSION}/{entity.set_name}{key}"
    text = path + ("?" + "&".join(params) if params else "")
    if text.count(_HOLE) != template.count(_HOLE):
        raise _No("a non-literal piece of the URL sits where the rewrite cannot keep it")
    return _Url(text, entity, used)


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


def _site(
    call: Node, url: Node, target: Target, xhr: frozenset[tuple[int, str]]
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
    method = _literal(args.named_children[0])
    if method is None or method.upper() != "GET":
        raise _No("the method is not the literal GET: a v2 write body is not translated")
    if b"\n" in (call.text or b"")[: args.start_byte + 1 - call.start_byte]:
        raise _No("the call head spans lines")

    pieces = _flatten(url)
    hits = [
        i
        for i, p in enumerate(pieces)
        if p.type == "string" and _V2.search(p.text or b"") is not None
    ]
    if len(hits) != 1 or len(_V2.findall(url.text or b"")) != 1:
        raise _No("the URL names the v2 endpoint other than once in a literal")
    tail = pieces[hits[0] :]
    first = _literal(tail[0])
    if first is None:
        raise _No("the URL literal holds an escape or a line break")
    at = _V2.search((tail[0].text or b"")[1:-1])
    prefix = first[: at.start()] if at is not None else ""
    # Gaps: a run of literals, or nothing, between non-literal pieces; one translated segment each.
    runs: list[list[Node]] = [[]]
    for piece in tail:
        if piece.type == "string":
            if _literal(piece) is None:
                raise _No("a URL literal holds an escape or a line break")
            runs[-1].append(piece)
        else:
            runs.append([])
    template = _HOLE.join("".join(_literal(p) or "" for p in run) for run in runs)
    template = template[len(prefix) :]
    result = translate(template, target)
    if (_scope(call), (recv.text or b"").decode()) not in xhr:
        raise _No(
            "the receiver of open is not a variable bound only to new XMLHttpRequest() in its own"
            " function: the adapter needs the native request object"
        )
    segments = result.text.split(_HOLE)
    edits: list[Edit] = []
    for n, (run, segment) in enumerate(zip(runs, segments, strict=True)):
        if not run:
            if segment:
                raise _No("a translated segment has no literal to hold it")
            continue
        quote = (run[0].text or b"")[:1]
        text = (prefix if n == 0 else "") + segment
        if quote.decode() in text:
            raise _No("the translated URL contains the string's own quote")
        base = url.start_byte
        span = (url.text or b"")[run[0].start_byte - base : run[-1].end_byte - base]
        edits.append(
            Edit(
                run[0].start_byte,
                run[-1].end_byte,
                quote + text.encode() + quote + b"\n" * span.count(b"\n"),
            )
        )
    edits.append(
        Edit(
            fn.start_byte,
            args.start_byte + 1,
            f"{ADAPTER_GLOBAL}.open({(recv.text or b'').decode()}, ".encode(),
        )
    )
    return edits, result.selected


def _plan_odata(root: Node, target: Target | None) -> Plan:
    stack = [root]
    strings: list[Node] = []
    while stack:
        node = stack.pop()
        stack.extend(node.children)
        if node.type in ("string", "template_string") and _V2.search(node.text or b""):
            strings.append(node)
    strings.sort(key=lambda n: n.start_byte)
    sites: dict[int, tuple[Node, Node]] = {}
    unproven: list[Unproven] = []
    for s in strings:
        top = s
        while top.parent is not None and top.parent.type in (
            "binary_expression",
            "parenthesized_expression",
        ):
            top = top.parent
        args = top.parent
        call = args.parent if args is not None and args.type == "arguments" else None
        fn = call.child_by_field_name("function") if call is not None else None
        name = fn.child_by_field_name("property") if fn is not None else None
        if (
            call is None
            or args is None
            or fn is None
            or fn.type != "member_expression"
            or name is None
            or name.text != b"open"
            or len(args.named_children) < 2
            or args.named_children[1] != top
            or s.type != "string"
        ):
            unproven.append(
                Unproven.at(
                    s,
                    "an OData v2 URL outside a <request>.open(<method>, <url>) call"
                    " is a shape this rewrite does not cover",
                )
            )
        else:
            sites.setdefault(call.id, (call, top))
    xhr = _xhr_names(root)
    edits: list[Edit] = []
    used: list[Used] = []
    done = 0
    for call, url in sites.values():
        call.start_point.row + 1
        try:
            if target is None:
                raise _No("no --target-inventory: the Web API names cannot be resolved")
            found, selected = _site(call, url, target, xhr)
        except _No as why:
            unproven.append(Unproven.at(call, str(why)))
            continue
        edits.extend(found)
        used.extend(selected)
        done += 1
    edits.sort(key=lambda e: e.start)
    unproven.sort(key=lambda u: u.line)
    return Plan(tuple(edits), tuple(unproven), cleared=done, adapter=tuple(used))


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

  global.CrmODataV2 = {
    // A no-op: a ribbon command calls it as its first action so this file loads before the rest.
    load: function () {},
    toV2: toV2,
    open: function (xhr, method, url) {
      var set = SET.exec(String(url));
      xhr.open.apply(xhr, Array.prototype.slice.call(arguments, 1));
      xhr.setRequestHeader("OData-MaxVersion", "4.0");
      xhr.setRequestHeader("OData-Version", "4.0");
      xhr.setRequestHeader("Prefer", 'odata.include-annotations="*"');
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


def render_adapter(used: set[Used]) -> str:
    """The adapter module for the attributes *used* by a work package's rewritten calls."""
    table: dict[str, tuple[str, dict[str, list[str]]]] = {}
    for u in sorted(used):
        _, attrs = table.setdefault(u.set_name, (u.entity, {}))
        attrs.setdefault(u.attribute, [u.v2_name, _KINDS.get(u.kind, "plain")])
    table_json = {k: {"entity": e, "attrs": a} for k, (e, a) in table.items()}
    return _ADAPTER.replace("__TABLE__", json.dumps(table_json, sort_keys=True, indent=2))
