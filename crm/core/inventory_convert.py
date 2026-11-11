"""Convert the code of a migration inventory with registered transforms (ADR 0034).

`convert` reads a migration inventory, the findings file `assess` wrote and the
packages folder `packages` wrote, and optionally a source map. For each component
that carries a finding whose rule names a registered transform
(`inventory_transforms`), it plans the rewrite, passes it through the **emit gate**
and writes, under *out* only, per work package:

- ``<name>.diff``: a unified diff against the extracted text (normalized: no BOM, LF),
- ``<name>.patched<ext>``: the full patched file, its SHA-256 in the ledger,
- ``<name>.repo.diff``: the same patch against the repository path, only when the
  source map links the component to a file by hash (status ``matched``),
- ``ledger.json``: one entry per work package, component key and rule, with a
  **manual-review entry** (file, line, rule, reason) for each site a rule matches but
  the transform cannot prove.
- with a source map and the repository, ``<project>.diff`` and ``<project>.patched.csproj``
  for each plug-in assembly the map matches to one project: the SDK-style retarget
  (`inventory_csproj`), its ledger entry keyed by the assembly and held in its work packages.

The emit gate writes a patch only if the patched code parses with no more error
nodes than the original and re-running the rule's detection finds exactly the
rewritten sites fewer, and the rewrite keeps the line count. A rejected plan changes
nothing and becomes a manual-review entry. No file is written into the inventory or a
repository. This module touches no org.
"""

from __future__ import annotations

import difflib
import hashlib
import json
import re
import unicodedata
from collections import Counter
from collections.abc import Callable, Iterable, Mapping
from dataclasses import dataclass, field
from pathlib import Path
from typing import TYPE_CHECKING, Any, Literal, cast
from xml.etree.ElementTree import ParseError

from crm.core.code_parse import error_count, parse
from crm.core.inventory import MANIFEST_NAME, InventoryError, Record, normalize_text, read_inventory
from crm.core.inventory_assess import (
    ContentEvidence,
    ContentPattern,
    Finding,
    FormHandlerContext,
    RegistrationEvidence,
    RibbonActionParameter,
    Rule,
    # The gate counts actions the way the ribbon rule does, so it reuses assess's reader.
    _ribbon_actions,  # pyright: ignore[reportPrivateUsage]
    load_rules,
    read_findings,
)
from crm.core.inventory_csproj import plan_csproj
from crm.core.inventory_form_handlers import (
    HandlerTag,
    form_handler_names,
    form_handler_tags,
    plan_form_handlers,
)
from crm.core.inventory_form_libraries import (
    adapter_gate,
    form_library_tags,
    library_id,
    plan_adapter_library,
)
from crm.core.inventory_html import (
    HTML_PAGE,
    inline_errors,
    plan_inline,
    plan_page_adapter,
    resolve_src,
    script_tags,
)
from crm.core.inventory_html import adapter_gate as page_adapter_gate
from crm.core.inventory_odata import ADAPTER_NAME, Target, read_target, render_adapter
from crm.core.inventory_packages import PACKAGE_ID
from crm.core.inventory_packages import SCHEMA_VERSION as PACKAGES_VERSION
from crm.core.inventory_ribbon import (
    PARAMETER,
    RibbonAction,
    RibbonDiff,
    action_key,
    plan_ribbon_actions,
    plan_ribbon_adapter,
    read_ribbon_diff,
    ribbon_adapter_gate,
    ribbon_calls,
    ribbon_function_names,
    unproven_rules,
)
from crm.core.inventory_source_map import mapped_projects, read_source_map, require_inventory
from crm.core.inventory_transforms import (
    ADAPTER_TRANSFORM,
    HANDLER_TRANSFORM,
    PAGE_ADAPTER_TRANSFORM,
    RIBBON_ADAPTER_TRANSFORM,
    TRANSFORMS,
    Edit,
    Plan,
    Transform,
    Used,
)
from crm.utils.safe_xml import fromstring

if TYPE_CHECKING:
    from tree_sitter import Node

#: The format version of the ledger.
SCHEMA_VERSION = 1
LEDGER_NAME = "ledger.json"
#: The package id of a component no work package holds.
UNPACKAGED = "unpackaged"
#: The transform id of the plug-in project retarget. Its ledger entries carry it as their
#: ``rule`` too, the id of the rules-file rule `assess --source-map --repo` flags the same
#: project with: the repository, not the inventory, is what both detect on.
CSPROJ_TRANSFORM = "csproj-sdk-net462"
#: The kinds whose artifacts may call a script's functions by name.
_CALLER_KINDS = frozenset(("web_resource", "ribbon", "ribbon_diff", "sitemap"))
#: The per-package call-graph counts of the ledger's ``call_graph``: helpers given a
#: ``formContext``, functions no handler reaches, helpers a caller outside the file holds back.
GRAPH_COUNTS = ("threaded", "unreachable", "boundary")


@dataclass(frozen=True)
class ManualReview:
    """A site a rule matches but the rewrite is not proven for. *line* is None for a whole file.

    The last three fields help a reviewer finish the edit and are written only when known:
    the *snippet* of the statement at the site, the rule's *replacement* and the *return_use*
    of a call whose result the transform classifies.
    """

    file: str
    line: int | None
    rule: str
    reason: str
    snippet: str | None = None
    replacement: str | None = None
    return_use: str | None = None

    def to_dict(self) -> dict[str, Any]:
        base = {"file": self.file, "line": self.line, "rule": self.rule, "reason": self.reason}
        optional = {
            "snippet": self.snippet,
            "replacement": self.replacement,
            "return_use": self.return_use,
        }
        return base | {k: v for k, v in optional.items() if v is not None}


#: The most characters of a manual-review entry's ``snippet``, its closing ``...`` included.
SNIPPET_MAX = 240
_DECLARATIONS = ("lexical_declaration", "variable_declaration")


def _is_statement(node: Node) -> bool:
    return node.type.endswith("_statement") or node.type in _DECLARATIONS


def _snippet(source: bytes, line: int, root: Node | None, column: int | None = None) -> str | None:
    """The source text of the statement holding 1-based *line* (at 0-based *column* when the
    transform gave one, else the innermost statement on the line), trimmed and capped at
    `SNIPPET_MAX`; with no JavaScript tree *root* (an HTML page, XML), the line itself.
    """
    row = line - 1
    if root is None:
        lines = source.split(b"\n")
        text = lines[row] if 0 <= row < len(lines) else b""
    elif column is not None:
        node = root.descendant_for_point_range((row, column), (row, column))
        while node is not None and not _is_statement(node):
            node = node.parent
        text = node.text if node is not None and node.text else b""
    else:
        best: Node | None = None
        stack = [root]
        while stack:
            node = stack.pop()
            if not node.start_point.row <= row <= node.end_point.row:
                continue
            stack.extend(node.children)
            if _is_statement(node) and (
                best is None or node.end_byte - node.start_byte <= best.end_byte - best.start_byte
            ):
                best = node
        text = best.text if best is not None and best.text else b""
    out = text.decode("utf-8", "replace").strip()
    if len(out) > SNIPPET_MAX:
        out = out[: SNIPPET_MAX - 3] + "..."
    return out or None


@dataclass
class Entry:
    """One ledger row: a component in a work package, patched or left for review."""

    package: str
    kind: str
    key: str
    rule: str
    transform: str
    status: Literal["patched", "manual-review"]
    edits: int = 0
    original_sha256: str | None = None
    patched_sha256: str | None = None
    repo_path: str | None = None
    files: dict[str, str] = field(default_factory=lambda: {})
    manual_review: list[ManualReview] = field(default_factory=lambda: [])
    #: The call-graph counts of a handler transform's JS entry, aggregated per package in the
    #: ledger, not written on the entry itself.
    graph: Counter[str] | None = None

    def to_dict(self) -> dict[str, Any]:
        return {
            "package": self.package,
            "kind": self.kind,
            "key": self.key,
            "rule": self.rule,
            "transform": self.transform,
            "status": self.status,
            "edits": self.edits,
            "original_sha256": self.original_sha256,
            "patched_sha256": self.patched_sha256,
            "repo_path": self.repo_path,
            "files": self.files,
            "manual_review": [m.to_dict() for m in self.manual_review],
        }


@dataclass(frozen=True)
class ConvertResult:
    manifest_sha256: str
    entries: tuple[Entry, ...]
    #: The generated OData v2 adapter module of each work package that has one.
    adapters: Mapping[str, dict[str, Any]] = field(default_factory=lambda: {})

    def ledger(self) -> dict[str, Any]:
        call_graph: dict[str, dict[str, int]] = {}
        for e in self.entries:
            if e.graph is not None:
                total = call_graph.setdefault(e.package, {k: 0 for k in GRAPH_COUNTS})
                for k in GRAPH_COUNTS:
                    total[k] += e.graph[k]
        return {
            "schema_version": SCHEMA_VERSION,
            "inventory": {"manifest_sha256": self.manifest_sha256},
            "call_graph": call_graph,
            "adapters": self.adapters,
            "entries": [e.to_dict() for e in self.entries],
        }

    def summary(self) -> dict[str, Any]:
        return {
            "patched": sum(e.status == "patched" for e in self.entries),
            "manual_review": sum(len(e.manual_review) for e in self.entries),
            "entries": len(self.entries),
        }


def _hex(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def _members(packages_dir: Path, manifest_sha: str) -> dict[str, list[str]]:
    """Map each ``<kind>:<key>`` member to the ids of the work packages that hold it.

    The folder must be a packages output of this inventory, and every package id must
    be a real one, since the id names a folder under ``--out``.
    """

    def load(path: Path) -> dict[str, Any]:
        try:
            data: Any = json.loads(path.read_text(encoding="utf-8"))
        except (OSError, ValueError) as exc:
            raise InventoryError(f"cannot read {path}: {exc}") from exc
        if type(data) is not dict:
            raise InventoryError(f"{path}: not a JSON object")
        return cast(dict[str, Any], data)

    held: dict[str, list[str]] = {}
    index = load(packages_dir / "index.json")
    if index.get("schema_version") != PACKAGES_VERSION:
        raise InventoryError(
            f"{packages_dir / 'index.json'}: schema_version {index.get('schema_version')!r} is not"
            f" supported; this crm reads packages schema_version {PACKAGES_VERSION}"
        )
    inventory = index.get("inventory")
    if (
        type(inventory) is not dict
        or cast(dict[str, Any], inventory).get("manifest_sha256") != manifest_sha
    ):
        raise InventoryError(
            "the packages folder was made from another inventory (its manifest_sha256 differs);"
            " re-run `crm inventory packages` on this one"
        )
    packages = index.get("packages")
    if type(packages) is not list:
        raise InventoryError(f"{packages_dir / 'index.json'}: packages is not a list")
    for item in cast(list[Any], packages):
        pid = cast(dict[str, Any], item).get("id") if type(item) is dict else None
        if type(pid) is not str or not PACKAGE_ID.fullmatch(pid):
            raise InventoryError(f"{packages_dir / 'index.json'}: malformed package {item!r}")
        package = load(packages_dir / f"{pid}.json")
        if package.get("schema_version") != PACKAGES_VERSION or package.get("id") != pid:
            raise InventoryError(
                f"{packages_dir / pid}.json: id or schema_version is not this crm's"
                f" (packages schema_version {PACKAGES_VERSION})"
            )
        members = package.get("members")
        if type(members) is not list:
            raise InventoryError(f"{packages_dir / pid}.json: members is not a list")
        for member in cast(list[Any], members):
            held.setdefault(str(member), []).append(pid)
    return held


def _apply(source: bytes, edits: tuple[Edit, ...]) -> bytes | None:
    """Apply non-overlapping *edits*, or None when two overlap or one is out of range."""
    out, at = bytearray(), 0
    for e in edits:
        if e.start < at or e.end < e.start or e.end > len(source):
            return None
        out += source[at : e.start] + e.text
        at = e.end
    return bytes(out + source[at:])


def _gate(
    pattern: Any,
    transform: Transform,
    original: bytes,
    patched: bytes,
    n: int,
    page: bool = False,
) -> str | None:
    """The emit gate: None when the patch may be written, else why it may not. On a *page* the
    syntax errors are counted in its inline scripts, the markup around them not being code.
    """
    try:
        before_text, after_text = original.decode("utf-8"), patched.decode("utf-8")
    except UnicodeDecodeError:
        return "the text is not UTF-8"
    if page:
        before, after = (
            inline_errors(original, transform.language),
            inline_errors(patched, transform.language),
        )
        if before is None or after is None:
            return "the patched page's <script> tags can no longer be placed in its text"
    else:
        before = error_count(parse(transform.language, original))
        after = error_count(parse(transform.language, patched))
    if after > before:
        return f"the patched code has {after - before} new syntax error node(s)"
    left = len(pattern.findall(before_text)) - len(pattern.findall(after_text))
    if left != n:
        return f"re-running the rule clears {left} of the {n} rewritten sites"
    return None


def _form_gate(original: bytes, patched: bytes, n: int) -> str | None:
    """The emit gate for a form patch: the XML still maps, and exactly *n* handlers now pass."""
    before, after = form_handler_tags(original), form_handler_tags(patched)
    if before is None or after is None:
        return "the patched form no longer parses"

    def waiting(tags: list[HandlerTag]) -> int:
        return sum(t.enabled and not t.passes for t in tags)

    if waiting(before) - waiting(after) != n:
        return f"the patch sets the flag on {waiting(before) - waiting(after)} of {n} handlers"
    return None


def _ribbon_gate(original: bytes, patched: bytes, n: int) -> str | None:
    """The emit gate for a ribbon patch: the XML still maps, and exactly *n* actions now pass."""
    try:
        before = _ribbon_actions(fromstring(original.decode("utf-8")), PARAMETER)
        after = _ribbon_actions(fromstring(patched.decode("utf-8")), PARAMETER)
    except (ParseError, ValueError):
        return "the patched ribbon no longer parses"
    if read_ribbon_diff(patched) is None:
        return "the patched ribbon's actions can no longer be mapped to its text"
    if len(before) - len(after) != n:
        return f"the patch adds {PARAMETER} to {len(before) - len(after)} of {n} actions"
    return None


def _pattern(rule: Rule, rules: dict[str, Rule]) -> re.Pattern[str] | None:
    """The content pattern a rule's rewrite is gated on: its own, or its library rule's."""
    d = rule.detection
    if isinstance(d, FormHandlerContext | RibbonActionParameter):
        d = rules[d.library_rule].detection
    return d.pattern if isinstance(d, ContentPattern) else None


def _diff(old: str, new: str, path: str) -> str:
    def lines(text: str) -> list[str]:
        out = re.findall(r"[^\n]*\n|[^\n]+", text)
        if out and not out[-1].endswith("\n"):
            out[-1] += "\n\\ No newline at end of file\n"
        return out

    return "".join(difflib.unified_diff(lines(old), lines(new), f"a/{path}", f"b/{path}"))


@dataclass
class _Form:
    """A form read and mapped, or the reason it cannot be. A *target* carries the handler
    finding, so this run patches it; any other form is only read for what it registers.
    """

    key: str
    path: str
    source: bytes
    tags: list[HandlerTag]
    problem: str
    target: bool
    #: The ``(library, function)`` registrations the finding's evidence names.
    named: list[tuple[str, str]]
    #: The form carries the adapter rule's finding, so this run lists the adapter in it.
    adapter: bool = False


def _hits(findings: Iterable[Finding], rule: Rule | None) -> dict[str, RegistrationEvidence]:
    """The registration evidence of each component carrying one of *rule*'s findings."""
    return {
        f.key: f.evidence
        for f in findings
        if rule is not None
        and f.rule == rule.id
        and f.cls != "clear"
        and isinstance(f.evidence, RegistrationEvidence)
    }


def _read_forms(
    inventory_dir: Path,
    records: list[Record],
    findings: Iterable[Finding],
    rule: Rule | None,
    adapter_rule: Rule | None,
) -> list[_Form]:
    """Read every form of the inventory against its hash, noting which carry *rule*'s finding
    and which carry *adapter_rule*'s.
    """
    findings = list(findings)
    hits, listed = _hits(findings, rule), _hits(findings, adapter_rule)
    out: list[_Form] = []
    for rec in records:
        ev = hits.get(rec.key) if rec.kind == "form" else None
        seen = ev or listed.get(rec.key)
        if rec.kind != "form" or not (seen or rec.artifacts):
            continue
        path, sha = (
            (seen.path, seen.sha256) if seen else (rec.artifacts[0].path, rec.artifacts[0].sha256)
        )
        named = [
            (str(r.get("library", "")).lower(), str(r.get("function", "")))
            for r in (ev.registrations if ev else ())
        ]
        form = _Form(rec.key, path, b"", [], "", ev is not None, named, rec.key in listed)
        try:
            raw = (inventory_dir / path).read_bytes()
            if _hex(raw) != sha:
                form.problem = "artifact changed since extraction"
                form.named += form_handler_names(normalize_text(raw))
            else:
                form.source = normalize_text(raw)
                tags = form_handler_tags(form.source)
                if tags is None:
                    form.problem = "the form's handlers cannot be mapped to its text"
                    form.named += form_handler_names(form.source)
                else:
                    form.tags = tags
        except OSError as exc:
            form.problem = f"artifact unreadable: {exc.strerror or exc}"
        out.append(form)
    return out


@dataclass
class _Ribbon:
    """A ribbon diff read and mapped, or the reason it cannot be. A *target* carries the ribbon
    finding, so this run patches it; any other diff is only read for what it calls.
    """

    key: str
    path: str
    source: bytes
    diff: RibbonDiff | None
    problem: str
    target: bool
    #: The ``(library, function)`` calls the diff makes, for one that cannot be mapped.
    named: list[tuple[str, str]]
    #: The diff carries the adapter rule's finding, so this run loads the adapter in its commands.
    adapter: bool = False


def _read_ribbons(
    inventory_dir: Path,
    records: list[Record],
    findings: Iterable[Finding],
    rule: Rule | None,
    adapter_rule: Rule | None,
) -> list[_Ribbon]:
    """Read every ribbon diff against its hash, noting which carry *rule*'s finding and which
    carry *adapter_rule*'s.
    """
    findings = list(findings)
    hits, listed = _hits(findings, rule), _hits(findings, adapter_rule)
    out: list[_Ribbon] = []
    for rec in records:
        ev = hits.get(rec.key) if rec.kind == "ribbon_diff" else None
        seen = ev or listed.get(rec.key)
        if rec.kind != "ribbon_diff" or not (seen or rec.artifacts):
            continue
        path, sha = (
            (seen.path, seen.sha256) if seen else (rec.artifacts[0].path, rec.artifacts[0].sha256)
        )
        ribbon = _Ribbon(rec.key, path, b"", None, "", ev is not None, [], rec.key in listed)
        try:
            raw = (inventory_dir / path).read_bytes()
            ribbon.source = normalize_text(raw)
            if _hex(raw) != sha:
                ribbon.problem = "artifact changed since extraction"
            else:
                ribbon.diff = read_ribbon_diff(ribbon.source)
                if ribbon.diff is None:
                    ribbon.problem = "the ribbon's actions cannot be mapped to its text"
            if ribbon.problem:
                ribbon.named = ribbon_function_names(ribbon.source)
        except OSError as exc:
            ribbon.problem = f"artifact unreadable: {exc.strerror or exc}"
        if ribbon.problem and seen is not None:
            # What the assessment saw stays a caller when the artifact is stale or gone now.
            ribbon.named += [
                action_key(r.get("library") or "", r.get("function") or "")
                for r in seen.registrations
            ]
        out.append(ribbon)
    return out


def _effective_callers(
    inventory_dir: Path, records: list[Record], ribbons: list[_Ribbon]
) -> dict[tuple[str, str], str]:
    """The functions an effective ribbon calls from actions no readable ribbon diff carries.

    A diff holds the actions an org added to an entity's ribbon, and the effective ribbon holds
    those and every other action, so it can call a function the diffs do not (another solution's
    diff, an action the extraction missed). Per entity, a function the effective ribbon calls more
    often without ``PrimaryControl`` than the entity's diff and the application's diff together
    (an entity's ribbon shows the application's commands) has such a caller, as does a function
    its ``CustomRule`` calls. A ribbon that cannot be read names every function it shows: either
    way the function's signature stays.
    """
    mapped: dict[str, Counter[tuple[str, str]]] = {}
    for r in ribbons:
        counts = mapped.setdefault(r.key, Counter())
        if r.diff:
            counts.update(a.key for a in r.diff.actions if not a.passes)
    reasons: dict[tuple[str, str], str] = {}
    for rec in records:
        if rec.kind != "ribbon" or not rec.artifacts:
            continue
        where = f"entity {rec.key}" if rec.key else "the application"
        try:
            raw = (inventory_dir / rec.artifacts[0].path).read_bytes()
        except OSError:
            continue
        text = normalize_text(raw)
        read = ribbon_calls(text) if _hex(raw) == rec.artifacts[0].sha256 else None
        if read is None:
            for key in ribbon_function_names(text):
                reasons[key] = (
                    f"{key[1]} is called from the effective ribbon of {where}, which cannot be read"
                )
            continue
        calls, rules = read
        for key in rules:
            reasons.setdefault(key, f"{key[1]} is called by a CustomRule of the effective ribbon")
        have = mapped.get(rec.key, Counter()) + mapped.get("", Counter())
        for key, n in calls.items():
            if n > have[key]:
                reasons.setdefault(
                    key,
                    f"{key[1]} is called by the effective ribbon of {where} from a command"
                    " no readable ribbon diff carries",
                )
    return reasons


_ESCAPE = re.compile(r"\\u\{([0-9a-fA-F]{1,6})\}|\\u([0-9a-fA-F]{4})")
_ASCII_BREAK = re.compile(r"[^\x00-\x2f\x3a-\x40\x5b-\x5e\x60\x7b-\x7f]+")


def _unescape(match: re.Match[str]) -> str:
    code = int(match.group(1) or match.group(2), 16)
    return chr(code) if code <= 0x10FFFF else match.group(0)


def _identifiers(text: str) -> frozenset[str]:
    """Every word of *text* a JavaScript identifier could be, over-reporting by design.

    The text is read both as written and with ``\\uXXXX`` escapes decoded. A word holding a
    non-ASCII character is kept whole (combining marks and joiners belong to it) and also split
    at the characters an identifier cannot hold.
    """
    found: set[str] = set()
    for each in (text, _ESCAPE.sub(_unescape, text)):
        for word in _ASCII_BREAK.findall(each) + re.findall(r"[\w$]+", each):
            found.add(word)
            if not word.isascii():
                piece = ""
                for ch in word + " ":
                    if ch.isalnum() or ch in "_$\u200c\u200d" or unicodedata.category(ch)[0] == "M":
                        piece += ch
                    else:
                        found.add(piece)
                        piece = ""
    found.discard("")
    return frozenset(found)


def _other_scripts(
    inventory_dir: Path,
    records: Iterable[Record],
    registered: frozenset[str],
    kinds: frozenset[str] = _CALLER_KINDS,
) -> Callable[[str], frozenset[str] | None]:
    """Map a script's path to the names the inventory's other scripts mention.

    None means one of those scripts is unreadable.

    A function another web resource (of any type), a ribbon or a form registration names may be
    called from there, a caller the rewrite of one file cannot update, so the transform leaves
    its signature alone. *registered* is every function name a form registers. The scan is of
    the raw text, strings and comments included: it can only over-report.
    """
    paths = [r.artifacts[0].path for r in records if r.kind in kinds and r.artifacts]
    names: dict[str, frozenset[str] | None] = {}

    def others(path: str) -> frozenset[str] | None:
        if not names:
            for each in paths:
                try:
                    text = (inventory_dir / each).read_text(encoding="utf-8", errors="replace")
                except OSError:
                    names[each] = None
                else:
                    names[each] = _identifiers(text)
        seen: set[str] = set()
        for other, found in names.items():
            if other == path:
                continue
            if found is None:
                return None
            seen |= found
        return frozenset(seen) | registered

    return others


def _eligible(
    forms: list[_Form],
    libraries: dict[str, tuple[str, str, str]],
    packages: Callable[[str, str], set[str]],
    commands: frozenset[tuple[str, str]] = frozenset(),
) -> tuple[dict[tuple[str, str, str], dict[str, bool]], dict[tuple[str, str], str]]:
    """Which registered functions of each library may be rewritten, and why the others may not.

    A function is rewritten only when every enabled registration of it, on any form of the
    inventory, is one this run patches in exactly the work packages that ship the library, and
    none already passes the context: a function whose registrations are not all set together
    would be called with the wrong arguments. Returns the functions per library group (mapped to
    whether any registration has ``parameters``) and a reason per
    ``(lowercase library, function)`` left out.
    """
    regs: dict[tuple[str, str], list[tuple[_Form, HandlerTag]]] = {}
    reasons: dict[tuple[str, str], str] = {}
    for form in forms:
        for tag in form.tags:
            if tag.enabled:
                regs.setdefault((tag.library.lower(), tag.function), []).append((form, tag))
        for key in form.named if form.problem else []:
            reasons[key] = (
                f"{key[1]} is registered on a form that cannot be patched: {form.problem}"
            )
    functions: dict[tuple[str, str, str], dict[str, bool]] = {}
    for key, found in regs.items():
        group = libraries.get(key[0])
        if group is None or key in reasons or all(t.passes and f.target for f, t in found):
            continue
        mine = packages(group[0], group[1])
        if key in commands:
            reasons[key] = f"{key[1]} is also called by a ribbon command"
        elif any(t.passes for _, t in found):
            reasons[key] = f"{key[1]} is already registered with passExecutionContext on a form"
        elif any(not f.target for f, _ in found):
            reasons[key] = f"{key[1]} is also registered on a form this run does not patch"
        elif any(packages("form", f.key) != mine for f, _ in found):
            reasons[key] = (
                f"{key[1]} is not registered on forms in exactly the work packages"
                " that ship its library"
            )
        else:
            functions.setdefault(group, {})[key[1]] = any(t.parameters.strip() for _, t in found)
    return functions, reasons


def _eligible_commands(
    ribbons: list[_Ribbon],
    libraries: dict[str, tuple[str, str, str]],
    packages: Callable[[str, str], set[str]],
    handlers: frozenset[tuple[str, str]],
    scripts: Callable[[str], frozenset[str] | None],
    effective: Mapping[tuple[str, str], str],
) -> tuple[dict[tuple[str, str, str], frozenset[str]], dict[tuple[str, str], str]]:
    """Which ribbon command functions of each library may be rewritten, and why the others may not.

    A function is rewritten only when every action calling it, on any ribbon diff of the
    inventory, is a command action this run patches in exactly the work packages that ship the
    library, on a command shown on a form, with the same ``CrmParameter`` list, and none passes
    ``PrimaryControl`` already. A function a form handler, a ribbon ``CustomRule`` or an element
    outside a command also calls would be called with the wrong arguments, so it stays, as does
    one another web resource names (*scripts* maps a library's path to those names), and so does
    one an effective ribbon calls from a command no diff carries (*effective*, with the reasons).
    """
    regs: dict[tuple[str, str], list[tuple[_Ribbon, RibbonAction]]] = {}
    reasons: dict[tuple[str, str], str] = {}
    called: set[tuple[str, str]] = set()
    for ribbon in ribbons:
        for action in ribbon.diff.actions if ribbon.diff else ():
            regs.setdefault(action.key, []).append((ribbon, action))
        called |= ribbon.diff.rule_calls if ribbon.diff else frozenset()
        for key in ribbon.named:
            reasons[key] = (
                f"{key[1]} is called from a ribbon diff that cannot be patched: {ribbon.problem}"
            )
    found_commands: dict[tuple[str, str, str], set[str]] = {}
    for key, found in regs.items():
        group = libraries.get(key[0])
        if group is None or key in reasons or all(a.passes for _, a in found):
            continue
        mine = packages(group[0], group[1])
        away = next((a.not_form for _, a in found if a.not_form), None)
        if any(a.passes for _, a in found):
            reasons[key] = f"{key[1]} is already called with {PARAMETER} by a ribbon action"
        elif not all(a.in_command for _, a in found):
            reasons[key] = f"{key[1]} is also called by a ribbon element outside a command"
        elif key in called:
            reasons[key] = f"{key[1]} is also called by a ribbon CustomRule"
        elif key in handlers:
            reasons[key] = f"{key[1]} is also registered on a form handler"
        elif (names := scripts(group[2])) is None or key[1].rsplit(".", 1)[-1] in names:
            reasons[key] = f"{key[1]} is also named in another web resource: its callers stay"
        elif any(not r.target for r, _ in found):
            reasons[key] = f"{key[1]} is also called from a ribbon diff this run does not patch"
        elif key in effective:
            reasons[key] = effective[key]
        elif away:
            reasons[key] = f"{key[1]}: {away}"
        elif len({a.parameters for _, a in found}) > 1:
            reasons[key] = f"{key[1]} is called with different CrmParameters by different commands"
        elif any(packages("ribbon_diff", r.key) != mine for r, _ in found):
            reasons[key] = (
                f"{key[1]} is not called from ribbon diffs in exactly the work packages"
                " that ship its library"
            )
        else:
            found_commands.setdefault(group, set()).add(key[1])
    return {g: frozenset(v) for g, v in found_commands.items()}, reasons


def convert(
    inventory_dir: Path,
    findings_path: Path,
    packages_dir: Path,
    out: Path,
    *,
    source_map_path: Path | None = None,
    target_inventory: Path | None = None,
    repo: Path | None = None,
    adapter_resource: str | None = None,
) -> ConvertResult:
    """Plan, gate and write the conversion of every component carrying a transformable finding.

    With *repo*, the application repository the source map was made from, each plug-in
    assembly the map matches to one project also gets the SDK-style retarget of that project.
    *adapter_resource* is the web resource name the OData v2 adapter will have in the target org
    (``{package}`` expands to the work package id): a form listing a rewritten library gets the
    adapter listed ahead of it, and without the name it is a manual-review entry.

    Raises:
        InventoryError: an input cannot be read or belongs to another inventory, *out* is inside
            the inventory or not empty, or *adapter_resource* is blank or names one web resource
            for the adapters of several work packages.
    """
    if adapter_resource is not None and not adapter_resource.strip():
        raise InventoryError("--adapter-resource must name the adapter's web resource")
    if repo is not None and source_map_path is None:
        raise InventoryError("--repo needs --source-map: the map names each assembly's project")
    inv_root, out_root = inventory_dir.resolve(), out.resolve()
    if out_root == inv_root or inv_root in out_root.parents:
        raise InventoryError(f"--out {out} is inside the inventory; name a folder outside it")
    if out.exists() and any(out.iterdir()):
        raise InventoryError(f"--out {out} is not empty; name a new or empty folder")
    inv = read_inventory(inventory_dir)
    target: Target | None = read_target(target_inventory) if target_inventory is not None else None
    manifest_sha = _hex((inventory_dir / MANIFEST_NAME).read_bytes())
    findings = read_findings(findings_path)
    if findings.manifest_sha256 != manifest_sha:
        raise InventoryError(
            "the findings file was made from another inventory (its manifest_sha256 differs);"
            " re-run `crm inventory assess` on this one"
        )
    matched: dict[tuple[str, str], str] = {}
    projects: dict[str, str] = {}
    if source_map_path is not None:
        source_map = read_source_map(source_map_path)
        require_inventory(source_map, manifest_sha)
        matched = {
            (c.kind, c.key): c.files[0]
            for c in source_map.components
            if c.status == "matched" and c.files
        }
        projects = mapped_projects(source_map)
    held = _members(packages_dir, manifest_sha)
    # Packages do not group the ribbon diff, but the entity's effective ribbon holds every action
    # the diff adds, so it is linked to exactly the libraries the diff calls: the diff travels
    # with it. The application's diff (key "") pairs with the ribbon keyed "application".
    for member, pids in list(held.items()):
        if member.startswith("ribbon:"):
            entity = member.split(":", 1)[1]
            held.setdefault(f"ribbon_diff:{'' if entity == 'application' else entity}", pids)
    records = {(r.kind, r.key): r for r in inv.records}
    rules = {r.id: r for r in load_rules().rules}

    # One component can carry findings of several rules that name a transform. Their
    # transforms run in findings order, each on the text the one before left, so every
    # rule's edits land in one patch and none is dropped.
    groups: dict[tuple[str, str, str], dict[str, Rule]] = {}
    #: The pages carrying the adapter rule's finding, each with the sha256 of the text it read.
    page_targets: dict[tuple[str, str, str], str] = {}
    page_rule = next((r for r in rules.values() if r.transform == PAGE_ADAPTER_TRANSFORM), None)
    for f in findings.findings:
        rule = rules.get(f.rule or "")
        if (
            page_rule is not None
            and rule is page_rule
            and f.cls != "clear"
            and isinstance(f.evidence, RegistrationEvidence)
        ):
            page_targets[(f.kind, f.key, f.evidence.path)] = f.evidence.sha256
            groups.setdefault((f.kind, f.key, f.evidence.path), {})
            continue
        if (
            rule is None
            or rule.transform is None
            or f.cls == "clear"
            or not isinstance(f.evidence, ContentEvidence)
        ):
            continue
        if rule.transform not in TRANSFORMS:
            raise InventoryError(
                f"rule {rule.id} names transform {rule.transform!r}, not registered"
            )
        groups.setdefault((f.kind, f.key, f.evidence.path), {}).setdefault(rule.id, rule)
    # global-context skips the methods context-settings owns. A findings file made before
    # that rule existed holds no finding for it, so the owner joins the group here.
    owner = rules.get("web-resource-context-settings")
    for group in groups.values():
        if owner is not None and any(r.transform == "global-context" for r in group.values()):
            group.setdefault(owner.id, owner)
    sha_of = {
        (f.kind, f.key, f.evidence.path): f.evidence.sha256
        for f in findings.findings
        if isinstance(f.evidence, ContentEvidence)
    } | page_targets

    # Form handlers: the library's functions that are registered, read from the forms. A
    # library with convertible ones joins its group with the form rule, which runs last.
    def owner_of(kind: str) -> Rule | None:
        return next(
            (r for r in rules.values() if r.transform == HANDLER_TRANSFORM and r.kind == kind),
            None,
        )

    form_rule, ribbon_rule = owner_of("form"), owner_of("ribbon_diff")
    adapter_rule = next((r for r in rules.values() if r.transform == ADAPTER_TRANSFORM), None)
    ribbon_adapter_rule = next(
        (r for r in rules.values() if r.transform == RIBBON_ADAPTER_TRANSFORM), None
    )
    forms: list[_Form] = []
    ribbons: list[_Ribbon] = []
    reasons: dict[tuple[str, str], str] = {}
    command_reasons: dict[tuple[str, str], str] = {}
    handlers: dict[tuple[str, str, str], dict[str, bool]] = {}
    commands: dict[tuple[str, str, str], frozenset[str]] = {}
    web_callers = _other_scripts(
        inventory_dir, inv.records, frozenset(), frozenset(("web_resource",))
    )
    libraries: dict[str, tuple[str, str, str]] = {}
    if (
        form_rule is not None
        or ribbon_rule is not None
        or adapter_rule is not None
        or ribbon_adapter_rule is not None
    ):
        script_types = TRANSFORMS[HANDLER_TRANSFORM].web_resource_types
        for gkey in groups:
            rec = records.get((gkey[0], gkey[1]))
            if rec is not None and rec.fields.get("web_resource_type") in script_types:
                libraries[str(rec.fields.get("name", "")).lower()] = gkey

        def packages_of(kind: str, key: str) -> set[str]:
            return set(held.get(f"{kind}:{key}", [UNPACKAGED]))

        if form_rule is not None or adapter_rule is not None:
            forms = _read_forms(
                inventory_dir, list(inv.records), findings.findings, form_rule, adapter_rule
            )
        if ribbon_rule is not None or ribbon_adapter_rule is not None:
            ribbons = _read_ribbons(
                inventory_dir,
                list(inv.records),
                findings.findings,
                ribbon_rule,
                ribbon_adapter_rule,
            )
        form_calls = frozenset(
            [(t.library.lower(), t.function) for f in forms for t in f.tags if t.enabled]
            + [k for f in forms if f.problem for k in f.named]
        )
        ribbon_calls = frozenset(
            [a.key for r in ribbons if r.diff for a in r.diff.actions if a.in_command]
            + [k for r in ribbons if r.diff for k in r.diff.rule_calls]
            + [k for r in ribbons for k in r.named]
        )
        handlers, reasons = _eligible(forms, libraries, packages_of, ribbon_calls)
        commands, command_reasons = _eligible_commands(
            ribbons,
            libraries,
            packages_of,
            form_calls,
            web_callers,
            _effective_callers(inventory_dir, list(inv.records), ribbons),
        )
        # One rule owns a library's context rewrite: the form rule when handlers are rewritten.
        for gkey in handlers.keys() | commands.keys():
            owner = form_rule if handlers.get(gkey) and form_rule is not None else ribbon_rule
            if owner is not None:
                groups[gkey].setdefault(owner.id, owner)
    lib_of = {g: n for n, g in libraries.items()}
    named = {t.function for f in forms for t in f.tags} | {k[1] for f in forms for k in f.named}
    others = _other_scripts(
        inventory_dir,
        inv.records,
        frozenset(named | {n.rsplit(".", 1)[-1] for n in named}),
    )
    rewritten: set[tuple[str, str]] = set()

    pending: list[tuple[str, str]] = []

    def emit(
        kind: str,
        key: str,
        path: str,
        steps: list[tuple[Rule, Plan, str, bool]],
        source: bytes,
        current: bytes,
    ) -> None:
        # Only a script has statements; a page's or an XML artifact's site is its line.
        record = records.get((kind, key))
        script = kind == "web_resource" and not is_page(record)
        tree = (
            parse("javascript", source)
            if script and any(p.unproven for _, p, *_ in steps)
            else None
        )
        for pkg in held.get(f"{kind}:{key}", [UNPACKAGED]):
            files: dict[str, str] = {}
            for rule, plan, reason, applied in steps:
                entry = Entry(pkg, kind, key, rule.id, cast(str, rule.transform), "manual-review")
                entry.manual_review = [
                    ManualReview(
                        path,
                        u.line,
                        rule.id,
                        u.reason,
                        _snippet(source, u.line, tree, u.column),
                        rule.fix,
                        u.return_use,
                    )
                    for u in plan.unproven
                ]
                if rule.transform == HANDLER_TRANSFORM and kind == "web_resource":
                    entry.graph = Counter(
                        threaded=plan.threaded if applied else 0,
                        unreachable=plan.unreachable,
                        boundary=plan.boundary,
                    )
                if reason:
                    entry.manual_review.insert(
                        0, ManualReview(path, None, rule.id, reason, replacement=rule.fix)
                    )
                elif applied:
                    entry.status, entry.edits = "patched", len(plan.edits)
                    entry.original_sha256, entry.patched_sha256 = _hex(source), _hex(current)
                    entry.repo_path = matched.get((kind, key))
                    if not files:
                        written = _outputs(entry, path, source, current)
                        pending.extend(written.values())
                        files = {label: rel for label, (rel, _) in written.items()}
                    entry.files = dict(files)
                entries.append(entry)

    def adapter_name(package: str) -> str:
        return (adapter_resource or "").replace("{package}", package)

    entries: list[Entry] = []
    adapter_uses: dict[str, set[Used]] = {}
    #: Each library (lowercase name) the odata-v2 transform rewrote, with its work packages.
    rewrote: dict[str, set[str]] = {}

    def is_page(record: Record | None) -> bool:
        return record is not None and record.fields.get("web_resource_type") == HTML_PAGE

    def page_step(
        key: str, name: str, source: bytes, current: bytes
    ) -> tuple[Plan, str, bool, bytes] | None:
        """The adapter rule's step on a page as ``(plan, reason, applied, patched)``, or None when
        the page already loads the adapter ahead of every script this run rewrote.
        """
        where = set(held.get(f"web_resource:{key}", [UNPACKAGED]))
        resource = adapter_name(min(where)) if adapter_resource is not None else ""
        found = plan_page_adapter(current, source, name, resource, rewrote.keys())
        loaded = {
            n.lower()
            for t in script_tags(current) or ()
            if t.src is not None and (n := resolve_src(name, t.src)) is not None
        } & rewrote.keys()
        if isinstance(found, str):
            reason = found
        elif adapter_resource is None:
            reason = "the adapter's web resource name is not known: pass --adapter-resource"
        elif any(rewrote[n] != where for n in loaded):
            reason = (
                "the page is not in exactly the work packages that ship the scripts it loads"
                " that were rewritten"
            )
        elif "{package}" in adapter_resource and len(where) != 1:
            reason = "the page sits in several work packages, whose adapters have different names"
        else:
            reason = ""
        if reason:
            return Plan((), ()), reason, False, current
        if found is None or isinstance(found, str):
            return None
        plan = Plan((found,), ())
        patched = _apply(current, plan.edits) or b""
        if patched.count(b"\n") != current.count(b"\n"):
            reason = "the rewrite changes the line count"
        else:
            reason = page_adapter_gate(current, patched, source, name, resource, rewrote.keys())
        if reason:
            return plan, f"emit gate rejected the rewrite: {reason}", False, current
        return plan, "", True, patched

    # Pages go last: a page's registration needs every library's rewrite settled first.
    for (kind, key, path), group in sorted(
        groups.items(), key=lambda g: is_page(records.get((g[0][0], g[0][1])))
    ):
        record = records.get((kind, key))
        page = is_page(record)
        staged: list[Plan] = []
        source, problem = b"", ""
        try:
            raw = (inventory_dir / path).read_bytes()
            if _hex(raw) != sha_of[(kind, key, path)]:
                problem = "artifact changed since extraction"
            else:
                source = normalize_text(raw)
        except OSError as exc:
            problem = f"artifact unreadable: {exc.strerror or exc}"
        current: bytes = source
        steps: list[tuple[Rule, Plan, str, bool]] = []
        for rule in group.values():
            transform = TRANSFORMS[cast(str, rule.transform)]
            plan, reason = Plan((), ()), problem
            if (
                not reason
                and record is not None
                and record.fields.get("web_resource_type") in transform.web_resource_types
            ):
                if page:
                    got = plan_inline(
                        current,
                        transform.language,
                        (lambda root, t=transform: t.plan(root, target))
                        if transform.needs_target
                        else transform.plan,
                    )
                    plan, reason = (Plan((), ()), got) if isinstance(got, str) else (got, "")
                else:
                    tree = parse(transform.language, current)
                    if transform.needs_handlers:
                        plan = transform.plan(
                            tree,
                            handlers.get((kind, key, path), {}),
                            others(path),
                            commands.get((kind, key, path), frozenset()),
                        )
                    elif transform.needs_target:
                        plan = transform.plan(tree, target)
                    else:
                        plan = transform.plan(tree)
            elif not reason:
                reason = f"transform {rule.transform} does not read this web resource type"
            applied = False
            if plan.edits:
                patched = _apply(current, plan.edits)
                if patched is None:
                    reason = "the planned edits overlap"
                elif patched.count(b"\n") != current.count(b"\n"):
                    reason = "the rewrite changes the line count"
                elif (pattern := _pattern(rule, rules)) is not None:
                    n = len(plan.edits) if plan.cleared is None else plan.cleared
                    reason = _gate(pattern, transform, current, patched, n, page) or ""
                if reason:
                    reason = f"emit gate rejected the rewrite: {reason}"
                elif patched is not None:
                    current, applied = patched, True
                    if page:
                        staged.append(plan)
                    else:
                        for pkg in held.get(f"{kind}:{key}", [UNPACKAGED]):
                            adapter_uses.setdefault(pkg, set()).update(plan.adapter)
                        rewritten.update((lib_of.get((kind, key, path), ""), f) for f in plan.done)
                        if rule.transform == "odata-v2" and record is not None:
                            rewrote[str(record.fields.get("name", "")).lower()] = set(
                                held.get(f"{kind}:{key}", [UNPACKAGED])
                            )
            if plan.edits or plan.unproven or reason:
                steps.append((rule, plan, reason, applied))
        # A page with a rewritten inline script needs its registration even where the findings
        # file holds no page-rule hit (made before the rule, or filtered by hand).
        if page and page_rule is not None and ((kind, key, path) in page_targets or staged):
            step = (
                (Plan((), ()), problem, False, source)
                if problem
                else page_step(
                    key, str(record.fields.get("name", "")) if record else "", source, current
                )
            )
            if step is not None:
                step_plan, step_reason, step_applied, current = step
                steps.append((page_rule, step_plan, step_reason, step_applied))
                if not step_applied:
                    # No registration, so the page's rewritten calls would have no adapter: the
                    # page stays as it was and says why on each entry.
                    steps = [
                        (
                            r,
                            p,
                            f"the adapter cannot be registered on this page: {step_reason}",
                            False,
                        )
                        if ap
                        else (r, p, rs, ap)
                        for r, p, rs, ap in steps
                    ]
                    staged, current = [], source
        for plan in staged:
            for pkg in held.get(f"{kind}:{key}", [UNPACKAGED]):
                adapter_uses.setdefault(pkg, set()).update(plan.adapter)
        emit(kind, key, path, steps, source, current)
    # The adapter's web resource name per work package: known now, before any file is written, so
    # a name that cannot serve every package refuses the run.
    adapter_pkgs = sorted(pkg for pkg, used in adapter_uses.items() if used)
    if (
        adapter_resource is not None
        and "{package}" not in adapter_resource
        and len(adapter_pkgs) > 1
    ):
        raise InventoryError(
            f"--adapter-resource {adapter_resource!r} names one web resource, but the adapters of"
            f" the work packages {', '.join(adapter_pkgs)} differ: put {{package}} in the name"
            " to give each package its own"
        )

    def adapter_step(form: _Form, current: bytes) -> tuple[Plan, str, bool, bytes] | None:
        """The adapter rule's step on *form* as ``(plan, reason, applied, patched)``, or None when
        the form already lists the adapter ahead of the libraries it serves.
        """
        reason = form.problem if not form.source else ""
        tags = None if reason else form_library_tags(current)
        if not reason and tags is None:
            reason = "the form's library list cannot be mapped to its text"
        if tags is None:
            return Plan((), ()), reason, False, current
        listed = {t.name.lower() for t in tags}
        mine = {n for n in rewrote if n in listed}
        where = set(held.get(f"form:{form.key}", [UNPACKAGED]))
        if not mine:
            reason = (
                "no library the form lists had its OData v2 calls rewritten in this run:"
                " see the libraries' entries"
            )
        elif adapter_resource is None:
            reason = "the adapter's web resource name is not known: pass --adapter-resource"
        elif any(rewrote[n] != where for n in mine):
            reason = (
                "the form is not in exactly the work packages that ship the libraries"
                " it lists that were rewritten"
            )
        elif "{package}" in adapter_resource and len(where) != 1:
            reason = "the form sits in several work packages, whose adapters have different names"
        if reason:
            return Plan((), ()), reason, False, current
        name = adapter_name(next(iter(where)))
        found = plan_adapter_library(tags, name, mine, library_id(form.key, name))
        if found is None:
            return None
        if isinstance(found, str):
            return Plan((), ()), found, False, current
        plan = Plan((found,), ())
        patched = _apply(current, plan.edits) or b""
        if patched.count(b"\n") != current.count(b"\n"):
            reason = "the rewrite changes the line count"
        else:
            reason = adapter_gate(current, patched, name, mine) or ""
        if reason:
            return plan, f"emit gate rejected the rewrite: {reason}", False, current
        return plan, "", True, patched

    for form in forms:
        steps: list[tuple[Rule, Plan, str, bool]] = []
        current = form.source
        if form.target and form_rule is not None:
            plan, reason, applied, patched = Plan((), ()), form.problem, False, form.source
            if not reason:
                gone = {
                    k: reasons.get(k)
                    or f"{k[1]} was not rewritten in {k[0]}: see the library's entries"
                    for k in ((t.library.lower(), t.function) for t in form.tags)
                    if k[0] in libraries and k not in rewritten
                }
                plan = plan_form_handlers(form.tags, rewritten, gone)
                if plan.edits:
                    patched = _apply(form.source, plan.edits) or b""
                    if not patched:
                        reason = "the planned edits overlap"
                    elif patched.count(b"\n") != form.source.count(b"\n"):
                        reason = "the rewrite changes the line count"
                    else:
                        reason = _form_gate(form.source, patched, len(plan.edits)) or ""
                    if reason:
                        reason = f"emit gate rejected the rewrite: {reason}"
                    else:
                        applied = True
            if plan.edits or plan.unproven or reason:
                steps.append((form_rule, plan, reason, applied))
            if applied:
                current = patched
        if form.adapter and adapter_rule is not None and (step := adapter_step(form, current)):
            steps.append((adapter_rule, *step[:3]))
            current = step[3]
        if steps:
            emit("form", form.key, form.path, steps, form.source, current)

    def ribbon_adapter_step(
        ribbon: _Ribbon, current: bytes
    ) -> tuple[Plan, str, bool, bytes] | None:
        """The adapter rule's step on *ribbon* as ``(plan, reason, applied, patched)``, or None
        when every command already loads the adapter ahead of the libraries it serves.
        """
        diff = None if ribbon.problem else read_ribbon_diff(current)
        if diff is None:
            reason = ribbon.problem or "the ribbon's actions cannot be mapped to its text"
            return Plan((), ()), reason, False, current
        where = set(held.get(f"ribbon_diff:{ribbon.key}", [UNPACKAGED]))
        called = {a.key[0] for a in diff.actions if a.in_command} & rewrote.keys()
        # Rule callers are reported whatever else keeps the commands from being patched.
        rules_only = Plan((), tuple(unproven_rules(diff, rewrote.keys())))
        if not called:
            if rules_only.unproven:
                return rules_only, "", False, current
            reason = (
                "no library the ribbon's commands call had its OData v2 calls rewritten in this"
                " run: see the libraries' entries"
            )
        elif adapter_resource is None:
            reason = "the adapter's web resource name is not known: pass --adapter-resource"
        elif any(rewrote[n] != where for n in called):
            reason = (
                "the ribbon is not in exactly the work packages that ship the libraries"
                " its commands call that were rewritten"
            )
        elif "{package}" in adapter_resource and len(where) != 1:
            reason = "the ribbon sits in several work packages, whose adapters have different names"
        else:
            reason = ""
        if reason:
            return rules_only, reason, False, current
        name = adapter_name(next(iter(where)))
        plan = plan_ribbon_adapter(diff, name, rewrote.keys())
        if not plan.edits and not plan.unproven:
            return None
        if not plan.edits:
            return plan, "", False, current
        patched = _apply(current, plan.edits) or b""
        if not patched:
            reason = "the planned edits overlap"
        elif patched.count(b"\n") != current.count(b"\n"):
            reason = "the rewrite changes the line count"
        else:
            reason = ribbon_adapter_gate(current, patched, name, rewrote.keys()) or ""
        if reason:
            return plan, f"emit gate rejected the rewrite: {reason}", False, current
        return plan, "", True, patched

    for ribbon in ribbons:
        steps = []
        current = ribbon.source
        if ribbon.target and ribbon_rule is not None:
            plan, reason, applied, patched = Plan((), ()), ribbon.problem, False, ribbon.source
            if not reason and ribbon.diff is not None:
                gone = {
                    a.key: command_reasons.get(a.key)
                    or f"{a.key[1]} was not rewritten in {a.key[0]}: see the library's entries"
                    for a in ribbon.diff.actions
                    if a.in_command
                    and not a.passes
                    and a.key[0] in libraries
                    and a.key not in rewritten
                }
                plan = plan_ribbon_actions(ribbon.diff.actions, rewritten, gone)
                if plan.edits:
                    patched = _apply(ribbon.source, plan.edits) or b""
                    if not patched:
                        reason = "the planned edits overlap"
                    elif patched.count(b"\n") != ribbon.source.count(b"\n"):
                        reason = "the rewrite changes the line count"
                    else:
                        reason = _ribbon_gate(ribbon.source, patched, len(plan.edits)) or ""
                    if reason:
                        reason = f"emit gate rejected the rewrite: {reason}"
                    else:
                        applied = True
            if plan.edits or plan.unproven or reason:
                steps.append((ribbon_rule, plan, reason, applied))
            if applied:
                current = patched
        if (
            ribbon.adapter
            and ribbon_adapter_rule is not None
            and (step := ribbon_adapter_step(ribbon, current))
        ):
            steps.append((ribbon_adapter_rule, *step[:3]))
            current = step[3]
        if steps:
            emit("ribbon_diff", ribbon.key, ribbon.path, steps, ribbon.source, current)
    for rel, text in pending:
        (out / rel).parent.mkdir(parents=True, exist_ok=True)
        (out / rel).write_text(text, encoding="utf-8", newline="")
    adapters: dict[str, dict[str, Any]] = {}
    for pkg, used in sorted(adapter_uses.items()):
        if not used:
            continue
        text = render_adapter(used)
        (out / pkg).mkdir(parents=True, exist_ok=True)
        (out / pkg / ADAPTER_NAME).write_text(text, encoding="utf-8", newline="")
        adapters[pkg] = {
            "file": f"{pkg}/{ADAPTER_NAME}",
            "sha256": _hex(text.encode("utf-8")),
            "entity_sets": sorted({u.set_name for u in used}),
        }
        if adapter_resource is not None:
            adapters[pkg]["web_resource"] = adapter_name(pkg)
        for e in entries:
            if e.package == pkg and e.transform == "odata-v2" and e.status == "patched":
                e.files["adapter"] = f"{pkg}/{ADAPTER_NAME}"
    if repo is not None:
        for key, rel in sorted(projects.items()):
            entries.extend(_project_entries(out, repo, key, rel, held))
    entries.sort(key=lambda e: (e.package, e.kind, e.key))
    result = ConvertResult(manifest_sha, tuple(entries), adapters)
    out.mkdir(parents=True, exist_ok=True)
    (out / LEDGER_NAME).write_text(
        json.dumps(result.ledger(), sort_keys=True, ensure_ascii=False, indent=2) + "\n",
        encoding="utf-8",
    )
    return result


def _project_entries(
    out: Path, repo: Path, key: str, rel: str, held: Mapping[str, list[str]]
) -> list[Entry]:
    """One ledger entry per work package holding assembly *key*, from its project *rel*."""
    entries = [
        Entry(pkg, "plugin_assembly", key, CSPROJ_TRANSFORM, CSPROJ_TRANSFORM, "manual-review")
        for pkg in held.get(f"plugin_assembly:{key}", [UNPACKAGED])
    ]
    try:
        source = normalize_text((repo / rel).read_bytes())
        text = source.decode("utf-8")
    except (OSError, UnicodeDecodeError) as exc:
        reason = f"project unreadable: {getattr(exc, 'strerror', None) or exc}"
        for entry in entries:
            entry.manual_review = [ManualReview(rel, None, CSPROJ_TRANSFORM, reason)]
        return entries
    plan = plan_csproj(text)
    review = [ManualReview(rel, r.line, CSPROJ_TRANSFORM, r.reason) for r in plan.review]
    if plan.patched is None:
        for entry in entries:
            entry.manual_review = list(review)
        return entries
    patched = plan.patched.encode("utf-8")
    for entry in entries:
        entry.manual_review = list(review)
        entry.status, entry.edits = "patched", 1
        entry.original_sha256, entry.patched_sha256 = _hex(source), _hex(patched)
        entry.repo_path = rel
        entry.files = _write_project(out, entry.package, rel, source, patched)
    return entries


def _write_project(
    out: Path, package: str, rel: str, source: bytes, patched: bytes
) -> dict[str, str]:
    """Write a project's diff against its repository path and the full patched project.

    The folder path is folded into the file name, "_" escaped as "_u" and "/" as "__", so the
    fold is injective: no two project paths share a file.
    """
    folder = out / package
    folder.mkdir(parents=True, exist_ok=True)
    stem = rel.removesuffix(".csproj").replace("_", "_u").replace("/", "__")
    new = patched.decode("utf-8")
    files = {
        "diff": (f"{stem}.diff", _diff(source.decode("utf-8"), new, rel)),
        "patched": (f"{stem}.patched.csproj", new),
    }
    for fname, text in files.values():
        (folder / fname).write_text(text, encoding="utf-8", newline="")
    return {label: f"{package}/{fname}" for label, (fname, _) in files.items()}


def _outputs(entry: Entry, path: str, source: bytes, patched: bytes) -> dict[str, tuple[str, str]]:
    """The diff, the patched file and, for a matched source, the repo-path diff, each as its
    label and ``(path under out, text)``. Nothing is written: convert writes them all once every
    check that could still refuse the run has passed.
    """
    name = Path(path).name
    stem, sep, ext = name.rpartition(".content")
    if not sep:
        stem, ext = name, ""
    old, new = source.decode("utf-8"), patched.decode("utf-8")
    files = {
        "diff": (f"{stem}.diff", _diff(old, new, path)),
        "patched": (f"{stem}.patched{ext}", new),
    }
    if entry.repo_path is not None:
        files["repo_diff"] = (f"{stem}.repo.diff", _diff(old, new, entry.repo_path))
    return {label: (f"{entry.package}/{fname}", text) for label, (fname, text) in files.items()}
