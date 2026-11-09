"""HTML page half of the OData v2 transforms of `crm inventory convert` (ADR 0034, #1182).

A web resource page (type 1) loads libraries with ``<script src>`` and may carry inline scripts.
This module locates each script tag in the page text with the standard library's `html.parser`,
checks every position against a scan of the same text, and plans the one edit that loads the
generated adapter ahead of the first script the run rewrote. It adds no grammar: an inline
script body is parsed with the JavaScript grammar and its edits are shifted back into the page.
"""

from __future__ import annotations

import posixpath
import re
from collections.abc import Callable, Collection
from dataclasses import dataclass
from html.parser import HTMLParser
from typing import TYPE_CHECKING
from urllib.parse import unquote

from crm.core.code_parse import error_count, parse
from crm.core.inventory_transforms import Edit, Plan, Unproven

if TYPE_CHECKING:
    from tree_sitter import Node

#: The ``web_resource_type`` of an HTML page.
HTML_PAGE = 1

_OPEN = re.compile(r"<script\b(?:\"[^\"]*\"|'[^']*'|[^>\"'])*>", re.IGNORECASE)
_CLOSE = re.compile(r"</script\s*>", re.IGNORECASE)
_SCHEME = re.compile(r"^(?:[a-z][a-z0-9+.-]*:|//)", re.IGNORECASE)
#: The ``type`` values of a classic script: the page's inline code only runs as one.
_CLASSIC = frozenset(
    ("", "text/javascript", "application/javascript", "text/ecmascript", "application/ecmascript")
)


@dataclass(frozen=True)
class ScriptTag:
    """One ``<script>`` element: where it starts, what it loads and where its body sits (bytes)."""

    start: int
    tag_end: int
    body: tuple[int, int]
    src: str | None
    javascript: bool
    line: int


class _Reader(HTMLParser):
    def __init__(self, text: str) -> None:
        super().__init__(convert_charrefs=False)
        self.text = text
        lines = [0]
        for m in re.finditer("\n", text):
            lines.append(m.end())
        self._lines = lines
        self.opens: list[tuple[int, int, dict[str, str | None]]] = []
        self.closes: list[int] = []
        self.odd = False

    def _at(self) -> int:
        line, col = self.getpos()
        return self._lines[line - 1] + col

    def handle_starttag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
        if tag == "script":
            start = self._at()
            self.opens.append((start, start + len(self.get_starttag_text() or ""), dict(attrs)))

    def handle_startendtag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
        # A browser reads ``<script />`` as an opening tag, the parser as a whole element.
        if tag == "script":
            self.odd = True

    def handle_endtag(self, tag: str) -> None:
        if tag == "script":
            self.closes.append(self._at())


def script_tags(text: bytes) -> list[ScriptTag] | None:
    """The script elements of a page in document order, or None when they cannot be placed.

    The parser's position of every opening tag and closing tag must equal the scan's, so a tag
    in a comment or a CDATA section, a script that writes another, an unclosed script or a
    page that is not UTF-8 is refused rather than guessed at.
    """
    try:
        page = text.decode("utf-8")
    except UnicodeDecodeError:
        return None
    reader = _Reader(page)
    reader.feed(page)
    reader.close()
    opens = [(m.start(), m.end()) for m in _OPEN.finditer(page)]
    closes = [m.start() for m in _CLOSE.finditer(page)]
    if (
        reader.odd
        or len(opens) != len(closes)
        or opens != [(s, e) for s, e, _ in reader.opens]
        or closes != reader.closes
        or any(not a[1] <= c for a, c in zip(opens, closes, strict=True))
        or any(c > a[0] for c, a in zip(closes, opens[1:], strict=False))
    ):
        return None

    def at(i: int) -> int:
        return i if page.isascii() else len(page[:i].encode("utf-8"))

    out: list[ScriptTag] = []
    for (start, end, attrs), close in zip(reader.opens, closes, strict=True):
        src = attrs.get("src")
        kind = (attrs.get("type") or "").strip().lower()
        out.append(
            ScriptTag(
                at(start),
                at(end),
                (at(end), at(close)),
                src.strip() if src is not None else None,
                kind in _CLASSIC,
                page.count("\n", 0, start) + 1,
            )
        )
    return out


def resolve_src(page: str, src: str) -> str | None:
    """The web resource name (as written, case kept) a page's script *src* loads, or None.

    Web resource names are virtual paths, so a relative *src* resolves against the page's own
    name and a leading ``/WebResources/`` is dropped. An address on another host, a path above
    the root or an empty value names no web resource of this org, and is never matched by file
    name.
    """
    value = re.split(r"[?#]", src.strip(), maxsplit=1)[0]
    if not value or _SCHEME.match(value):
        return None
    value = unquote(value)
    if value.startswith("/"):
        value = re.sub(r"^/webresources/", "", value, flags=re.IGNORECASE).lstrip("/")
    else:
        value = posixpath.join(posixpath.dirname(page), value)
    value = posixpath.normpath(value)
    if not value or value == "." or value.startswith("../") or value == "..":
        return None
    return value


def adapter_src(page: str, adapter: str) -> str:
    """The ``src`` that loads the adapter web resource *adapter* from *page*: its name relative to
    the page's folder, which `resolve_src` maps back to the name.
    """
    return posixpath.relpath(adapter, posixpath.dirname(page) or ".")


def plan_inline(text: bytes, language: str, plan: Callable[[Node], Plan]) -> Plan | str:
    """The *plan* of every inline classic script of a page, as one plan in the page's offsets.

    Returns the reason instead when the page's scripts cannot be placed. A tag with a ``src``
    ignores its body, and a script of another ``type`` is not code of *language*.
    """
    tags = script_tags(text)
    if tags is None:
        return "the page's <script> tags cannot be placed in its text"
    edits: list[Edit] = []
    unproven: list[Unproven] = []
    cleared = 0
    adapter = ()
    for tag in tags:
        if tag.src is not None or not tag.javascript:
            continue
        low, high = tag.body
        sub = plan(parse(language, text[low:high]))
        shift = text.count(b"\n", 0, low)
        edits += [Edit(e.start + low, e.end + low, e.text) for e in sub.edits]
        unproven += [Unproven(u.line + shift, u.reason) for u in sub.unproven]
        cleared += len(sub.edits) if sub.cleared is None else sub.cleared
        adapter += sub.adapter
    return Plan(tuple(edits), tuple(unproven), cleared=cleared, adapter=adapter)


def inline_errors(text: bytes, language: str) -> int | None:
    """The syntax error nodes across a page's inline classic scripts, or None when its script
    tags cannot be placed. The markup around the scripts is not code of *language*.
    """
    tags = script_tags(text)
    if tags is None:
        return None
    return sum(
        error_count(parse(language, text[t.body[0] : t.body[1]]))
        for t in tags
        if t.src is None and t.javascript
    )


def inline_hits(text: bytes, tags: list[ScriptTag], pattern: re.Pattern[str]) -> dict[int, int]:
    """For each inline classic script of *tags* (by index) holding a string literal that matches
    *pattern*, the page line of the first such string.

    A match in a comment, in the markup or in an attribute is not a call.
    """
    hits: dict[int, int] = {}
    for i, tag in enumerate(tags):
        if tag.src is not None or not tag.javascript:
            continue
        low, high = tag.body
        shift = text.count(b"\n", 0, low)
        stack = [parse("javascript", text[low:high])]
        while stack:
            node = stack.pop()
            stack.extend(node.children)
            if node.type in ("string", "template_string") and pattern.search(
                (node.text or b"").decode("utf-8", "replace")
            ):
                line = node.start_point.row + 1 + shift
                hits[i] = min(hits.get(i, line), line)
    return hits


def _loads(tag: ScriptTag, page: str, name: str) -> bool:
    """True when *tag* loads the web resource *name* (lowercase) from *page*."""
    resolved = resolve_src(page, tag.src) if tag.src is not None else None
    return resolved is not None and resolved.lower() == name


def _patched(
    tags: list[ScriptTag],
    source: list[ScriptTag],
    page: str,
    current: bytes,
    original: bytes,
    rewrote: Collection[str],
) -> list[bool]:
    """For each script of *current*, whether this run rewrote it: a ``src`` naming a library in
    *rewrote* (lowercase names) or an inline body that differs from the *original* page's.
    """
    flags: list[bool] = []
    for tag, before in zip(tags, source, strict=True):
        if tag.src is not None:
            name = resolve_src(page, tag.src)
            flags.append(name is not None and name.lower() in rewrote)
        else:
            flags.append(
                current[tag.body[0] : tag.body[1]] != original[before.body[0] : before.body[1]]
            )
    return flags


def plan_page_adapter(
    current: bytes, original: bytes, page: str, adapter: str, rewrote: Collection[str]
) -> Edit | str | None:
    """The insert that loads *adapter* just before the first script this run rewrote.

    *current* is the page after its inline rewrite and *original* before it; *rewrote* holds the
    lowercase names of the libraries rewritten in this run. Returns None when the adapter is
    already loaded ahead of that script (the page needs nothing), and else the reason it cannot
    be inserted. An empty *adapter* names no script, so only the reasons that do not depend on
    the name are given, and the answer is otherwise None.
    """
    tags, source = script_tags(current), script_tags(original)
    if tags is None or source is None or len(tags) != len(source):
        return "the page's <script> tags cannot be placed in its text"
    flags = _patched(tags, source, page, current, original, rewrote)
    first = next((i for i, f in enumerate(flags) if f), None)
    if first is None:
        return "no script the page loads had its OData v2 calls rewritten in this run"
    if not adapter:
        return None
    where = adapter.lower()
    loads = [i for i, t in enumerate(tags) if _loads(t, page, where)]
    if loads and loads[0] < first:
        return None
    if loads:
        return (
            f"the adapter {adapter} is already loaded after a script the run rewrote:"
            " move it ahead of that script"
        )
    src = adapter_src(page, adapter)
    src = src.replace("&", "&amp;").replace("<", "&lt;").replace('"', "&quot;")
    return Edit(tags[first].start, tags[first].start, f'<script src="{src}"></script>'.encode())


def adapter_gate(
    current: bytes,
    patched: bytes,
    original: bytes,
    page: str,
    adapter: str,
    rewrote: Collection[str],
) -> str | None:
    """The emit gate for a page's adapter insert: None when it may be written, else why not.

    The script tags must still be placed, differ from *current*'s by exactly one added tag that
    loads the adapter, and have no script the run rewrote ahead of it.
    """
    before, after = script_tags(current), script_tags(patched)
    if before is None or after is None:
        return "the patched page's <script> tags can no longer be placed in its text"

    def shape(text: bytes, tags: list[ScriptTag]) -> list[tuple[str | None, bytes]]:
        return [(t.src, text[t.body[0] : t.body[1]]) for t in tags]

    at = next((i for i, t in enumerate(after) if _loads(t, page, adapter.lower())), None)
    old, new = shape(current, before), shape(patched, after)
    if at is None or len(new) != len(old) + 1 or new[:at] + new[at + 1 :] != old:
        return "the patch does not insert the adapter into the page's scripts"
    kept = script_tags(original)
    if kept is None or len(kept) != len(before):
        return "the page's <script> tags cannot be placed in its text"
    flags = _patched(before, kept, page, current, original, rewrote)
    if any(flags[:at]):
        return "the adapter is still loaded after a script it serves"
    return None
