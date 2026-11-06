"""Ribbon diff half of the `form-handler-context` transform (ADR 0034).

A ribbon diff's ``<JavaScriptFunction>`` actions call script functions. This module maps each
one to its bytes in the RibbonDiffXml text, so a command function that now takes the form context
gets ``<CrmParameter Value="PrimaryControl" />`` as its first parameter as a minimal edit and the
rest of the file stays byte for byte as it was.

``PrimaryControl`` is the form context only on a form command (Learn, "Pass data from a page as a
parameter to ribbon actions"): ``SelectedControl`` is the grid context, for subgrids and home page
grids. So an action is patched only when its command is shown on a form, decided by the
``Location`` of every control that names the command, else by the command's own ``Id``.
"""

from __future__ import annotations

import re
from collections.abc import Mapping
from dataclasses import dataclass
from html import unescape
from xml.etree.ElementTree import Element, ParseError

from crm.core.inventory_assess import _web_resource_name  # pyright: ignore[reportPrivateUsage]
from crm.core.inventory_transforms import Edit, Plan, Unproven
from crm.utils.safe_xml import fromstring

PARAMETER = "PrimaryControl"
_TAG = re.compile(rb"<JavaScriptFunction\b(?:\"[^\"]*\"|'[^']*'|[^>\"'])*>")
_FORM = "mscrm.form."


@dataclass(frozen=True)
class RibbonAction:
    """One ``<JavaScriptFunction>`` of a ribbon diff: what it calls and where its tag sits."""

    #: ``(lowercase library web resource name, function)``; the library is "" for a platform script.
    key: tuple[str, str]
    command: str
    #: False for a function outside any ``<CommandDefinition>``: never patched, only a caller.
    in_command: bool
    parameters: tuple[str, ...]
    #: Why the command is not known to sit on a form, else None.
    not_form: str | None
    line: int
    #: The edit that adds the parameter first.
    edit: Edit

    @property
    def passes(self) -> bool:
        return PARAMETER in self.parameters


@dataclass(frozen=True)
class RibbonDiff:
    """A ribbon diff read: its actions, and the functions its ``CustomRule`` elements call."""

    actions: list[RibbonAction]
    rule_calls: frozenset[tuple[str, str]]


def _key(library: str, function: str) -> tuple[str, str]:
    return (_web_resource_name(library) or "").lower(), function


def _not_form(command: str, locations: list[str]) -> str | None:
    if locations:
        away = next((x for x in locations if not x.lower().startswith(_FORM)), None)
        return None if away is None else f"command {command} is shown at {away}, not on a form"
    if command.lower().startswith(_FORM):
        return None
    return f"command {command} names no control in this diff, and its id is not a form command's"


def read_ribbon_diff(text: bytes) -> RibbonDiff | None:
    """The actions of a RibbonDiffXml in document order, or None when they cannot be mapped.

    The text and a real XML parse must agree on how many actions there are, so a tag in a
    comment or an unreadable file is refused rather than guessed at.
    """
    try:
        root = fromstring(text.decode("utf-8"))
    except (ParseError, ValueError):
        return None
    tags = list(_TAG.finditer(text))
    elements = list(root.iter("JavaScriptFunction"))
    if len(tags) != len(elements):
        return None
    parents: dict[Element, Element] = {c: p for p in root.iter() for c in p}
    locations: dict[str, list[str]] = {}
    for el in root.iter():
        command = el.get("Command")
        if command is None:
            continue
        up = parents.get(el)
        while up is not None and up.tag != "CustomAction":
            up = parents.get(up)
        locations.setdefault(command, []).append(up.get("Location") or "" if up is not None else "")
    owner: dict[Element, Element] = {}
    for definition in root.iter("CommandDefinition"):
        for el in definition.iter("JavaScriptFunction"):
            owner[el] = definition
    actions: list[RibbonAction] = []
    for el, m in zip(elements, tags, strict=True):
        definition = owner.get(el)
        command = (definition.get("Id") or "") if definition is not None else ""
        raw = m.group(0)
        if raw.endswith(b"/>"):
            at = m.end() - 2
            edit = Edit(
                at, m.end(), f'><CrmParameter Value="{PARAMETER}" /></JavaScriptFunction>'.encode()
            )
        else:
            edit = Edit(m.end(), m.end(), f'<CrmParameter Value="{PARAMETER}" />'.encode())
        actions.append(
            RibbonAction(
                _key(unescape(el.get("Library") or ""), el.get("FunctionName") or ""),
                command,
                definition is not None,
                tuple(p.get("Value") or "" for p in el.findall("CrmParameter")),
                _not_form(command, locations.get(command, [])),
                text.count(b"\n", 0, m.start()) + 1,
                edit,
            )
        )
    rules = frozenset(
        _key(r.get("Library") or "", r.get("FunctionName") or "") for r in root.iter("CustomRule")
    )
    return RibbonDiff(actions, rules)


def plan_ribbon_actions(
    actions: list[RibbonAction],
    rewritten: set[tuple[str, str]],
    reasons: Mapping[tuple[str, str], str],
) -> Plan:
    """Add ``PrimaryControl`` first to each action of a function in *rewritten*.

    A function is a ``(lowercase library, function)`` pair.

    An action in *reasons* is one the library's rewrite did not cover, so it stays and becomes
    an unproven site. Any other action is not this transform's concern.
    """
    edits: list[Edit] = []
    unproven: list[Unproven] = []
    for a in actions:
        if not a.in_command or a.passes:
            continue
        if a.key in rewritten:
            edits.append(a.edit)
        elif a.key in reasons:
            unproven.append(Unproven(a.line, reasons[a.key]))
    return Plan(tuple(edits), tuple(unproven))


_ATTR = re.compile(rb"([\w:.-]+)\s*=\s*(\"[^\"]*\"|'[^']*')")


def ribbon_function_names(text: bytes) -> list[tuple[str, str]]:
    """The ``(lowercase library, function)`` of every ``<JavaScriptFunction>`` the text shows.

    For a diff that cannot be mapped: it still calls these, so a function they name must not
    have its signature changed behind them.
    """
    out: list[tuple[str, str]] = []
    for m in _TAG.finditer(text):
        attrs = {
            a.group(1).decode(): unescape(a.group(2)[1:-1].decode("utf-8", "replace"))
            for a in _ATTR.finditer(m.group(0))
        }
        out.append(_key(attrs.get("Library", ""), attrs.get("FunctionName", "")))
    return out
