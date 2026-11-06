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
from collections.abc import Callable, Iterable
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Literal, cast

from crm.core.code_parse import error_count, parse
from crm.core.inventory import MANIFEST_NAME, InventoryError, Record, normalize_text, read_inventory
from crm.core.inventory_assess import (
    ContentEvidence,
    ContentPattern,
    Finding,
    FormHandlerContext,
    RegistrationEvidence,
    Rule,
    load_rules,
    read_findings,
)
from crm.core.inventory_form_handlers import (
    HandlerTag,
    form_handler_names,
    form_handler_tags,
    plan_form_handlers,
)
from crm.core.inventory_packages import PACKAGE_ID
from crm.core.inventory_packages import SCHEMA_VERSION as PACKAGES_VERSION
from crm.core.inventory_source_map import read_source_map
from crm.core.inventory_transforms import HANDLER_TRANSFORM, TRANSFORMS, Edit, Plan, Transform

#: The format version of the ledger.
SCHEMA_VERSION = 1
LEDGER_NAME = "ledger.json"
#: The package id of a component no work package holds.
UNPACKAGED = "unpackaged"
#: The kinds whose artifacts may call a script's functions by name.
_CALLER_KINDS = frozenset(("web_resource", "ribbon", "ribbon_diff", "sitemap"))
#: The per-package call-graph counts of the ledger's ``call_graph``: helpers given a
#: ``formContext``, functions no handler reaches, helpers a caller outside the file holds back.
GRAPH_COUNTS = ("threaded", "unreachable", "boundary")


@dataclass(frozen=True)
class ManualReview:
    """A site a rule matches but the rewrite is not proven for. *line* is None for a whole file."""

    file: str
    line: int | None
    rule: str
    reason: str

    def to_dict(self) -> dict[str, Any]:
        return {"file": self.file, "line": self.line, "rule": self.rule, "reason": self.reason}


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
    pattern: Any, transform: Transform, original: bytes, patched: bytes, n: int
) -> str | None:
    """The emit gate: None when the patch may be written, else why it may not."""
    try:
        before_text, after_text = original.decode("utf-8"), patched.decode("utf-8")
    except UnicodeDecodeError:
        return "the text is not UTF-8"
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


def _pattern(rule: Rule, rules: dict[str, Rule]) -> re.Pattern[str] | None:
    """The content pattern a rule's rewrite is gated on: its own, or its library rule's."""
    d = rule.detection
    if isinstance(d, FormHandlerContext):
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


def _read_forms(
    inventory_dir: Path, records: list[Record], findings: Iterable[Finding], rule: Rule
) -> list[_Form]:
    """Read every form of the inventory against its hash, noting which carry *rule*'s finding."""
    hits = {
        f.key: f.evidence
        for f in findings
        if f.rule == rule.id and f.cls != "clear" and isinstance(f.evidence, RegistrationEvidence)
    }
    out: list[_Form] = []
    for rec in records:
        ev = hits.get(rec.key) if rec.kind == "form" else None
        if rec.kind != "form" or not (ev or rec.artifacts):
            continue
        path, sha = (ev.path, ev.sha256) if ev else (rec.artifacts[0].path, rec.artifacts[0].sha256)
        named = [
            (str(r.get("library", "")).lower(), str(r.get("function", "")))
            for r in (ev.registrations if ev else ())
        ]
        form = _Form(rec.key, path, b"", [], "", ev is not None, named)
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
    inventory_dir: Path, records: Iterable[Record], registered: frozenset[str]
) -> Callable[[str], frozenset[str] | None]:
    """Map a script's path to the names the inventory's other scripts mention.

    None means one of those scripts is unreadable.

    A function another web resource (of any type), a ribbon or a form registration names may be
    called from there, a caller the rewrite of one file cannot update, so the transform leaves
    its signature alone. *registered* is every function name a form registers. The scan is of
    the raw text, strings and comments included: it can only over-report.
    """
    paths = [r.artifacts[0].path for r in records if r.kind in _CALLER_KINDS and r.artifacts]
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
        if any(t.passes for _, t in found):
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


def convert(
    inventory_dir: Path,
    findings_path: Path,
    packages_dir: Path,
    out: Path,
    *,
    source_map_path: Path | None = None,
) -> ConvertResult:
    """Plan, gate and write the conversion of every component carrying a transformable finding.

    Raises:
        InventoryError: an input cannot be read or belongs to another inventory, or
            *out* is inside the inventory or not empty.
    """
    inv_root, out_root = inventory_dir.resolve(), out.resolve()
    if out_root == inv_root or inv_root in out_root.parents:
        raise InventoryError(f"--out {out} is inside the inventory; name a folder outside it")
    if out.exists() and any(out.iterdir()):
        raise InventoryError(f"--out {out} is not empty; name a new or empty folder")
    inv = read_inventory(inventory_dir)
    manifest_sha = _hex((inventory_dir / MANIFEST_NAME).read_bytes())
    findings = read_findings(findings_path)
    if findings.manifest_sha256 != manifest_sha:
        raise InventoryError(
            "the findings file was made from another inventory (its manifest_sha256 differs);"
            " re-run `crm inventory assess` on this one"
        )
    matched: dict[tuple[str, str], str] = {}
    if source_map_path is not None:
        source_map = read_source_map(source_map_path)
        if source_map.inventory.get("manifest_sha256") != manifest_sha:
            raise InventoryError(
                "the source map was made from another inventory (its manifest_sha256 differs);"
                " re-run `crm inventory map-source` on this one"
            )
        matched = {
            (c.kind, c.key): c.files[0]
            for c in source_map.components
            if c.status == "matched" and c.files
        }
    held = _members(packages_dir, manifest_sha)
    records = {(r.kind, r.key): r for r in inv.records}
    rules = {r.id: r for r in load_rules().rules}

    # One component can carry findings of several rules that name a transform. Their
    # transforms run in findings order, each on the text the one before left, so every
    # rule's edits land in one patch and none is dropped.
    groups: dict[tuple[str, str, str], dict[str, Rule]] = {}
    for f in findings.findings:
        rule = rules.get(f.rule or "")
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
    }

    # Form handlers: the library's functions that are registered, read from the forms. A
    # library with convertible ones joins its group with the form rule, which runs last.
    form_rule = next((r for r in rules.values() if r.transform == HANDLER_TRANSFORM), None)
    forms: list[_Form] = []
    reasons: dict[tuple[str, str], str] = {}
    handlers: dict[tuple[str, str, str], dict[str, bool]] = {}
    libraries: dict[str, tuple[str, str, str]] = {}
    if form_rule is not None:
        script_types = TRANSFORMS[HANDLER_TRANSFORM].web_resource_types
        for gkey in groups:
            rec = records.get((gkey[0], gkey[1]))
            if rec is not None and rec.fields.get("web_resource_type") in script_types:
                libraries[str(rec.fields.get("name", "")).lower()] = gkey

        def packages_of(kind: str, key: str) -> set[str]:
            return set(held.get(f"{kind}:{key}", [UNPACKAGED]))

        forms = _read_forms(inventory_dir, list(inv.records), findings.findings, form_rule)
        handlers, reasons = _eligible(forms, libraries, packages_of)
        for gkey in handlers:
            groups[gkey].setdefault(form_rule.id, form_rule)
    lib_of = {g: n for n, g in libraries.items()}
    named = {t.function for f in forms for t in f.tags} | {k[1] for f in forms for k in f.named}
    others = _other_scripts(
        inventory_dir,
        inv.records,
        frozenset(named | {n.rsplit(".", 1)[-1] for n in named}),
    )
    rewritten: set[tuple[str, str]] = set()

    def emit(
        kind: str,
        key: str,
        path: str,
        steps: list[tuple[Rule, Plan, str, bool]],
        source: bytes,
        current: bytes,
    ) -> None:
        for pkg in held.get(f"{kind}:{key}", [UNPACKAGED]):
            files: dict[str, str] = {}
            for rule, plan, reason, applied in steps:
                entry = Entry(pkg, kind, key, rule.id, cast(str, rule.transform), "manual-review")
                entry.manual_review = [
                    ManualReview(path, u.line, rule.id, u.reason) for u in plan.unproven
                ]
                if rule.transform == HANDLER_TRANSFORM and kind == "web_resource":
                    entry.graph = Counter(
                        threaded=plan.threaded if applied else 0,
                        unreachable=plan.unreachable,
                        boundary=plan.boundary,
                    )
                if reason:
                    entry.manual_review.insert(0, ManualReview(path, None, rule.id, reason))
                elif applied:
                    entry.status, entry.edits = "patched", len(plan.edits)
                    entry.original_sha256, entry.patched_sha256 = _hex(source), _hex(current)
                    entry.repo_path = matched.get((kind, key))
                    if not files:
                        _write(out, entry, path, source, current)
                        files = entry.files
                    entry.files = dict(files)
                entries.append(entry)

    entries: list[Entry] = []
    for (kind, key, path), group in groups.items():
        record = records.get((kind, key))
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
                tree = parse(transform.language, current)
                plan = (
                    transform.plan(tree, handlers.get((kind, key, path), {}), others(path))
                    if transform.needs_handlers
                    else transform.plan(tree)
                )
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
                    reason = _gate(pattern, transform, current, patched, n) or ""
                if reason:
                    reason = f"emit gate rejected the rewrite: {reason}"
                elif patched is not None:
                    current, applied = patched, True
                    rewritten.update((lib_of.get((kind, key, path), ""), f) for f in plan.done)
            if plan.edits or plan.unproven or reason:
                steps.append((rule, plan, reason, applied))
        emit(kind, key, path, steps, source, current)
    for form in (f for f in forms if f.target and form_rule is not None):
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
            emit(
                "form",
                form.key,
                form.path,
                [(cast(Rule, form_rule), plan, reason, applied)],
                form.source,
                patched,
            )
    entries.sort(key=lambda e: (e.package, e.kind, e.key))
    result = ConvertResult(manifest_sha, tuple(entries))
    out.mkdir(parents=True, exist_ok=True)
    (out / LEDGER_NAME).write_text(
        json.dumps(result.ledger(), sort_keys=True, ensure_ascii=False, indent=2) + "\n",
        encoding="utf-8",
    )
    return result


def _write(out: Path, entry: Entry, path: str, source: bytes, patched: bytes) -> None:
    """Write the diff, the patched file and, for a matched source, the repo-path diff."""
    folder = out / entry.package
    folder.mkdir(parents=True, exist_ok=True)
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
    for label, (fname, text) in files.items():
        (folder / fname).write_text(text, encoding="utf-8", newline="")
        entry.files[label] = f"{entry.package}/{fname}"
