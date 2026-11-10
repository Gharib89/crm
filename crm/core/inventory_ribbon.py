"""Ribbon diff half of the `form-handler-context` and `ribbon-odata-adapter` transforms (ADR 0034).

A ribbon diff's ``<JavaScriptFunction>`` actions call script functions. This module maps each
one to its bytes in the RibbonDiffXml text, so a command function that now takes the form context
gets ``<CrmParameter Value="PrimaryControl" />`` as its first parameter as a minimal edit and the
rest of the file stays byte for byte as it was.

``PrimaryControl`` is the form context only on a form command (Learn, "Pass data from a page as a
parameter to ribbon actions"): ``SelectedControl`` is the grid context, for subgrids and home page
grids. So an action is patched only when its command is shown on a form, decided by the
``Location`` of every control that names the command, else by the command's own ``Id``.

The adapter half inserts a call to the generated OData v2 adapter's no-op ahead of the first
patched action of a command. A fresh page load clears the adapter's global, so every command with
a patched action loads it itself; a ``CustomRule`` runs at render, before any action, so a rule
that calls a patched library cannot be covered and is only reported.
"""

from __future__ import annotations

import re
from collections import Counter
from collections.abc import Collection, Mapping
from dataclasses import dataclass
from html import unescape
from xml.etree.ElementTree import Element, ParseError

# The ribbon rule and this patch must read a library name the same way, so share assess's reader.
from crm.core.inventory_assess import (
    RULE_OWNERS,
    _web_resource_name,  # pyright: ignore[reportPrivateUsage]
)
from crm.core.inventory_transforms import Edit, Plan, Unproven
from crm.utils.safe_xml import fromstring

PARAMETER = "PrimaryControl"
#: The no-op member the generated adapter exports for the inserted action to call.
ADAPTER_FUNCTION = "CrmODataV2.load"
_TAG = re.compile(rb"<JavaScriptFunction\b(?:\"[^\"]*\"|'[^']*'|[^>\"'])*>")
_RULE = re.compile(rb"<CustomRule\b(?:\"[^\"]*\"|'[^']*'|[^>\"'])*>")
_FORM = "mscrm.form."


@dataclass(frozen=True)
class RibbonAction:
    """One ``<JavaScriptFunction>`` of a ribbon diff: what it calls and where its tag sits."""

    #: ``(lowercase library web resource name, function)``; the library is "" for a platform script.
    key: tuple[str, str]
    #: False for a function outside any ``<CommandDefinition>``: never patched, only a caller.
    in_command: bool
    parameters: tuple[str, ...]
    #: Why the command is not known to sit on a form, else None.
    not_form: str | None
    line: int
    #: The edit that adds the parameter first.
    edit: Edit
    #: Which ``<CommandDefinition>`` of the diff (in document order) holds the action.
    command_index: int = -1
    #: Where the tag starts, and whether it is a direct child of its command's ``<Actions>``, the
    #: only place an inserted action is known to run in order.
    start: int = 0
    placeable: bool = False
    command: str = ""

    @property
    def passes(self) -> bool:
        return PARAMETER in self.parameters


@dataclass(frozen=True)
class RuleCall:
    """One ``<CustomRule>``: the function it calls, its rule's id and the commands that use it."""

    key: tuple[str, str]
    rule: str
    commands: tuple[str, ...]
    line: int


@dataclass(frozen=True)
class RibbonDiff:
    """A ribbon diff read: its actions, and the functions its ``CustomRule`` elements call."""

    actions: list[RibbonAction]
    rule_calls: frozenset[tuple[str, str]]
    rules: tuple[RuleCall, ...] = ()


def action_key(library: str, function: str) -> tuple[str, str]:
    return (_web_resource_name(library) or "").lower(), function


def _not_form(command: str, locations: list[str]) -> str | None:
    if locations:
        away = next((x for x in locations if not x.lower().startswith(_FORM)), None)
        if away is None:
            return None
        if not away:
            return f"command {command} is named by a control outside any CustomAction"
        return f"command {command} is shown at {away}, not on a form"
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
        locations.setdefault(command, []).append(
            (up.get("Location") or "") if up is not None else ""
        )
    owner: dict[Element, Element] = {}
    definitions = list(root.iter("CommandDefinition"))
    for definition in definitions:
        for el in definition.iter("JavaScriptFunction"):
            owner[el] = definition
    index = {d: i for i, d in enumerate(definitions)}
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
                action_key(el.get("Library") or "", el.get("FunctionName") or ""),
                definition is not None,
                tuple(p.get("Value") or "" for p in el.findall("CrmParameter")),
                _not_form(command, locations.get(command, [])),
                text.count(b"\n", 0, m.start()) + 1,
                edit,
                index.get(definition, -1) if definition is not None else -1,
                m.start(),
                definition is not None
                and (up := parents.get(el)) is not None
                and up.tag == "Actions"
                and parents.get(up) is definition,
                command,
            )
        )
    custom = list(root.iter("CustomRule"))
    rules = frozenset(
        action_key(r.get("Library") or "", r.get("FunctionName") or "") for r in custom
    )
    return RibbonDiff(actions, rules, _rule_calls(text, custom, parents, definitions))


def _rule_calls(
    text: bytes,
    custom: list[Element],
    parents: dict[Element, Element],
    definitions: list[Element],
) -> tuple[RuleCall, ...]:
    """Each ``CustomRule`` with its rule's id and the commands that name that rule."""
    users: dict[str, list[str]] = {}
    for definition in definitions:
        for ref in (*definition.iter("EnableRule"), *definition.iter("DisplayRule")):
            if ref.get("Id"):
                users.setdefault(ref.get("Id") or "", []).append(definition.get("Id") or "")
    starts = [m.start() for m in _RULE.finditer(text)]
    out: list[RuleCall] = []
    for i, el in enumerate(custom):
        up = parents.get(el)
        while up is not None and not (up.tag in RULE_OWNERS and up.get("Id")):
            up = parents.get(up)
        rule = (up.get("Id") or "") if up is not None else ""
        line = text.count(b"\n", 0, starts[i]) + 1 if len(starts) == len(custom) else 1
        out.append(
            RuleCall(
                action_key(el.get("Library") or "", el.get("FunctionName") or ""),
                rule,
                tuple(dict.fromkeys(users.get(rule, ()))),
                line,
            )
        )
    return tuple(out)


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


def ribbon_calls(
    text: bytes,
) -> tuple[Counter[tuple[str, str]], frozenset[tuple[str, str]]] | None:
    """How many ``<JavaScriptFunction>`` elements call each ``(library, function)`` without
    ``PrimaryControl``, and which functions ``CustomRule`` elements call, or None when the text
    is not XML.

    For an effective ribbon, which holds the diffs' actions and every other action in the org:
    it is read for who calls a function, never edited.
    """
    try:
        root = fromstring(text.decode("utf-8"))
    except (ParseError, ValueError):
        return None
    calls = Counter(
        action_key(el.get("Library") or "", el.get("FunctionName") or "")
        for el in root.iter("JavaScriptFunction")
        if PARAMETER not in (p.get("Value") for p in el.findall("CrmParameter"))
    )
    rules = frozenset(
        action_key(r.get("Library") or "", r.get("FunctionName") or "")
        for r in root.iter("CustomRule")
    )
    return calls, rules


_ATTR = re.compile(rb"([\w:.-]+)\s*=\s*(\"[^\"]*\"|'[^']*')")


def ribbon_function_names(text: bytes) -> list[tuple[str, str]]:
    """The ``(lowercase library, function)`` of every ``<JavaScriptFunction>`` and ``<CustomRule>``
    the text shows.

    For a diff that cannot be mapped: it still calls these, so a function they name must not
    have its signature changed behind them.
    """
    out: list[tuple[str, str]] = []
    for m in (*_TAG.finditer(text), *_RULE.finditer(text)):
        attrs = {
            a.group(1).decode(): unescape(a.group(2)[1:-1].decode("utf-8", "replace"))
            for a in _ATTR.finditer(m.group(0))
        }
        out.append(action_key(attrs.get("Library", ""), attrs.get("FunctionName", "")))
    return out


def _adapter_element(adapter: str) -> bytes:
    name = adapter.replace("&", "&amp;").replace("<", "&lt;").replace('"', "&quot;")
    return (
        f'<JavaScriptFunction Library="$webresource:{name}" FunctionName="{ADAPTER_FUNCTION}" />'
    ).encode()


def _commands(actions: list[RibbonAction]) -> list[list[RibbonAction]]:
    """The actions of each command, in document order; those outside any command are left out."""
    groups: dict[int, list[RibbonAction]] = {}
    for a in actions:
        if a.in_command:
            groups.setdefault(a.command_index, []).append(a)
    return list(groups.values())


def unproven_rules(diff: RibbonDiff, rewritten: Collection[str]) -> list[Unproven]:
    """One entry per ``CustomRule`` calling a library in *rewritten* (lowercase names).

    A rule runs at render, before any action can load the adapter, so it is only reported.
    """
    out: list[Unproven] = []
    for call in diff.rules:
        if call.key[0] in rewritten:
            used = ", ".join(call.commands) or "no command in this diff"
            out.append(
                Unproven(
                    call.line,
                    f"CustomRule {call.rule or '(no id)'} (used by {used}) calls {call.key[1]}"
                    f" from {call.key[0]}, rewritten in this run: a rule runs at render, before"
                    " any action can load the adapter, so check it by hand",
                )
            )
    return out


def plan_ribbon_adapter(diff: RibbonDiff, adapter: str, rewritten: Collection[str]) -> Plan:
    """Insert the adapter's action ahead of the first action of each command whose library is in
    *rewritten* (lowercase names), and report a ``CustomRule`` calling such a library.

    A command whose adapter action already runs first needs nothing. One that has it after the
    patched action, or whose action is not a direct child of ``<Actions>``, is unproven, as is a
    rule (see `unproven_rules`).
    """
    edits: list[Edit] = []
    unproven: list[Unproven] = []
    at = adapter.lower()
    for group in _commands(diff.actions):
        names = [a.key[0] for a in group]
        first = next((i for i, n in enumerate(names) if n in rewritten), None)
        if first is None:
            continue
        target = group[first]
        if at in names:
            if names.index(at) > first:
                unproven.append(
                    Unproven(
                        target.line,
                        f"the adapter {adapter} is called after {target.key[1]} in command"
                        f" {target.command}: move it ahead of that action",
                    )
                )
        elif not target.placeable:
            unproven.append(
                Unproven(
                    target.line,
                    f"the adapter action cannot be placed ahead of {target.key[1]} in command"
                    f" {target.command}: it is not a direct child of its <Actions>",
                )
            )
        else:
            edits.append(Edit(target.start, target.start, _adapter_element(adapter)))
    unproven.extend(unproven_rules(diff, rewritten))
    return Plan(tuple(edits), tuple(unproven))


def ribbon_adapter_gate(
    original: bytes, patched: bytes, adapter: str, rewritten: Collection[str]
) -> str | None:
    """The emit gate for an adapter patch: None when the patch may be written, else why not.

    Each command must read as before, or as before with one adapter action inserted ahead of its
    first action in *rewritten*.
    """
    before, after = read_ribbon_diff(original), read_ribbon_diff(patched)
    if before is None or after is None:
        return "the patched ribbon's actions can no longer be mapped to its text"
    key = (adapter.lower(), ADAPTER_FUNCTION)
    old, new = _commands(before.actions), _commands(after.actions)
    if len(old) != len(new):
        return "the patch changes the commands of the ribbon"
    for was, now in zip(old, new, strict=True):
        keys, after_keys = [a.key for a in was], [a.key for a in now]
        if after_keys == keys:
            continue
        first = next((i for i, a in enumerate(was) if a.key[0] in rewritten), None)
        if first is None or after_keys != [*keys[:first], key, *keys[first:]]:
            return "the patch does not insert one adapter action ahead of the patched action"
    return None
