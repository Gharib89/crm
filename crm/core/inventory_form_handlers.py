"""Form XML half of the `form-handler-context` transform (ADR 0034).

A form's ``<Handler>`` elements register script functions. This module maps each one to its
bytes in the FormXml text, so a rewritten handler gets ``passExecutionContext="true"`` as a
minimal edit and the rest of the file stays byte for byte as it was.
"""

from __future__ import annotations

import re
from collections.abc import Mapping
from dataclasses import dataclass
from html import unescape
from xml.etree.ElementTree import ParseError

from crm.core.inventory_transforms import Edit, Plan, Unproven
from crm.utils.safe_xml import fromstring

_TAG = re.compile(rb"<Handler\b(?:\"[^\"]*\"|'[^']*'|[^>\"'])*>")
_ATTR = re.compile(rb"([\w:.-]+)\s*=\s*(\"[^\"]*\"|'[^']*')")


@dataclass(frozen=True)
class HandlerTag:
    """One ``<Handler>`` of a form: what it registers and where its tag sits in the text."""

    function: str
    library: str
    enabled: bool
    passes: bool
    parameters: str
    line: int
    #: Where ``passExecutionContext``'s value (quotes excluded) sits, else None.
    flag: tuple[int, int] | None
    #: Where an added attribute goes: before the tag's closing ``>`` or ``/>``.
    insert: int


def _true(value: str | None) -> bool:
    return value is not None and value.strip().lower() == "true"


def form_handler_tags(text: bytes) -> list[HandlerTag] | None:
    """The handlers of a FormXml in document order, or None when they cannot be mapped to the text.

    The text and a real XML parse must agree on how many handlers there are, so a tag in a
    comment or an unreadable file is refused rather than guessed at.
    """
    try:
        parsed = fromstring(text.decode("utf-8"))
    except (ParseError, ValueError):
        return None
    tags = list(_TAG.finditer(text))
    if len(tags) != len(parsed.findall(".//event/Handlers/Handler")) or len(tags) != len(
        parsed.findall(".//Handler")
    ):
        return None
    out: list[HandlerTag] = []
    for m in tags:
        attrs: dict[str, str] = {}
        flag = None
        for a in _ATTR.finditer(m.group(0)):
            name = a.group(1).decode()
            attrs[name] = unescape(a.group(2)[1:-1].decode("utf-8"))
            if name == "passExecutionContext":
                if b"\n" in a.group(2) or b"\r" in a.group(2):
                    return None
                flag = (m.start() + a.start(2) + 1, m.start() + a.end(2) - 1)
        end = m.end() - (2 if m.group(0).endswith(b"/>") else 1)
        while text[end - 1 : end] in (b" ", b"\t", b"\r", b"\n"):
            end -= 1
        out.append(
            HandlerTag(
                attrs.get("functionName", ""),
                attrs.get("libraryName", ""),
                _true(attrs.get("enabled", "true")),
                _true(attrs.get("passExecutionContext")),
                attrs.get("parameters", ""),
                text.count(b"\n", 0, m.start()) + 1,
                flag,
                end,
            )
        )
    return out


def form_handler_names(text: bytes) -> list[tuple[str, str]]:
    """The ``(lowercase library, function)`` of every ``<Handler>`` the text shows, mapped or not.

    For a form whose handlers cannot be mapped: it still registers these, so a function they
    name must not have its signature changed behind them.
    """
    out: list[tuple[str, str]] = []
    for m in _TAG.finditer(text):
        attrs = {
            a.group(1).decode(): unescape(a.group(2)[1:-1].decode("utf-8", "replace"))
            for a in _ATTR.finditer(m.group(0))
        }
        out.append((attrs.get("libraryName", "").lower(), attrs.get("functionName", "")))
    return out


def plan_form_handlers(
    tags: list[HandlerTag],
    rewritten: set[tuple[str, str]],
    reasons: Mapping[tuple[str, str], str],
) -> Plan:
    """Set the flag on each enabled handler of a ``(lowercase library, function)`` in *rewritten*.

    A handler in *reasons* is one the library's rewrite did not cover, so it stays and
    becomes an unproven site. Any other handler is not this transform's concern.
    """
    edits: list[Edit] = []
    unproven: list[Unproven] = []
    for t in tags:
        key = (t.library.lower(), t.function)
        if not t.enabled or t.passes:
            continue
        if key in rewritten:
            if t.flag is not None:
                edits.append(Edit(t.flag[0], t.flag[1], b"true"))
            else:
                edits.append(Edit(t.insert, t.insert, b' passExecutionContext="true"'))
        elif key in reasons:
            unproven.append(Unproven(t.line, reasons[key]))
    return Plan(tuple(edits), tuple(unproven))
