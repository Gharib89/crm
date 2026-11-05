"""Run the packaged compatibility rules over a migration inventory (ADR 0033).

A **compatibility rule** classifies one kind of component for one migration
step: the source versions and targets it applies to, a class (``blocker``,
``deprecated``, ``optional``), a detection, the fix and the Microsoft Learn
pages that justify it. The rules ship as data, ``crm/rules/compatibility-rules.json``
(format: ``crm/schemas/compatibility-rules-v1.schema.json``). `assess` gives every
component of an inventory at least one **compatibility finding**, keyed by
component key: a rule's class with its evidence, ``unknown`` with a reason (no
rule covers it, an extraction gap, a kind not read complete, a field or artifact
the rule needs is missing) or ``clear`` (the rules that apply found nothing). The
findings file's format is ``crm/schemas/compatibility-findings-v1.schema.json``.
This module touches no org.
"""

from __future__ import annotations

import hashlib
import json
import re
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path
from typing import Any, cast

from crm.core.inventory import (
    KINDS,
    MANIFEST_NAME,
    Inventory,
    InventoryError,
    Record,
    normalize_text,
    parse_manifest,
)

#: The format version of the findings file.
SCHEMA_VERSION = 1
#: The format version of the rules file this module reads.
RULES_SCHEMA_VERSION = 1
RULES_PATH = Path(__file__).resolve().parent.parent / "rules" / "compatibility-rules.json"
TARGETS = ("onprem-9.1", "online")
RULE_CLASSES = ("blocker", "deprecated", "optional")
#: Finding classes, most severe first.
CLASSES = ("blocker", "deprecated", "optional", "unknown", "clear")
LEARN = "https://learn.microsoft.com/"
#: Match locations a finding lists; ``match_count`` still counts every match.
MAX_LOCATIONS = 10
_VERSION = re.compile(r"[0-9]+(\.[0-9]+)*")
_RULE_ID = re.compile(r"[a-z0-9]+(-[a-z0-9]+)*")


def _dotted(text: str) -> tuple[int, ...] | None:
    if _VERSION.fullmatch(text) is None:
        return None
    return tuple(int(part) for part in text.split("."))


def _version(text: str, where: str) -> tuple[int, ...]:
    version = _dotted(text)
    if version is None:
        raise InventoryError(f"{where} {text!r} is not a dotted version")
    return version


@dataclass(frozen=True)
class ContentPattern:
    """A regular expression over the text artifacts of the web resource types listed."""

    pattern: re.Pattern[str]
    web_resource_types: tuple[int, ...]


@dataclass(frozen=True)
class FieldIn:
    """A record field whose value is one of *values*."""

    field: str
    values: tuple[object, ...]


@dataclass(frozen=True)
class Rule:
    """One compatibility rule, applying from source version *source_from* to *source_before*."""

    id: str
    kind: str
    cls: str
    title: str
    source_from: str
    source_before: str | None
    targets: tuple[str, ...]
    detection: ContentPattern | FieldIn
    fix: str
    sources: tuple[str, ...]

    def applies(self, v: tuple[int, ...]) -> bool:
        """True when an inventory read from source version *v* is in this rule's range."""
        low = _version(self.source_from, "source.from")
        return v >= low and (
            self.source_before is None or v < _version(self.source_before, "source.before")
        )

    def selects(self, record: Record) -> bool:
        if record.kind != self.kind:
            return False
        d = self.detection
        return not isinstance(d, ContentPattern) or (
            record.fields.get("web_resource_type") in d.web_resource_types
        )


@dataclass(frozen=True)
class Rules:
    """The rules file: its content *version*, the SHA-256 of its bytes and its rules."""

    version: int
    sha256: str
    rules: tuple[Rule, ...]


def _obj(value: object, where: str) -> dict[str, Any]:
    if type(value) is not dict:
        raise InventoryError(f"rules file {where} is not an object")
    return cast(dict[str, Any], value)


def _text(obj: dict[str, Any], name: str, where: str) -> str:
    value = obj.get(name)
    if not isinstance(value, str) or not value:
        raise InventoryError(f"rules file {where}.{name} must be a non-empty string")
    return value


def _items(obj: dict[str, Any], name: str, where: str) -> list[Any]:
    value = obj.get(name)
    if type(value) is not list or not value:
        raise InventoryError(f"rules file {where}.{name} must be a non-empty list")
    return cast(list[Any], value)


def _detection(value: object, kind: str, where: str) -> ContentPattern | FieldIn:
    obj = _obj(value, where)
    detection_type = obj.get("type")
    if detection_type == "content_pattern":
        if kind != "web_resource":
            raise InventoryError(
                f"rules file {where}: content_pattern applies to web_resource only"
            )
        try:
            pattern = re.compile(_text(obj, "pattern", where))
        except re.error as exc:
            raise InventoryError(f"rules file {where}.pattern does not compile: {exc}") from exc
        types = _items(obj, "web_resource_types", where)
        if not all(type(t) is int for t in types):
            raise InventoryError(f"rules file {where}.web_resource_types must hold integers")
        return ContentPattern(pattern, tuple(cast(list[int], types)))
    if detection_type == "field_in":
        values = _items(obj, "values", where)
        return FieldIn(_text(obj, "field", where), tuple(values))
    raise InventoryError(
        f"rules file {where}.type {detection_type!r} is not content_pattern or field_in"
    )


def _rule(value: object, where: str) -> Rule:
    obj = _obj(value, where)
    rule_id = _text(obj, "id", where)
    if not _RULE_ID.fullmatch(rule_id):
        raise InventoryError(f"rules file {where}.id {rule_id!r} must be lowercase words and '-'")
    kind = _text(obj, "kind", where)
    if kind not in KINDS:
        raise InventoryError(f"rules file {where}.kind {kind!r} is not a v1 kind")
    cls = obj.get("class")
    if cls not in RULE_CLASSES:
        raise InventoryError(f"rules file {where}.class {cls!r} is not one of {RULE_CLASSES}")
    if "source" not in obj:
        raise InventoryError(f"rules file {where} has no source version range")
    source = _obj(obj["source"], f"{where}.source")
    low = _text(source, "from", f"{where}.source")
    _version(low, f"rules file {where}.source.from")
    high = source.get("before")
    if high is not None:
        if not isinstance(high, str):
            raise InventoryError(f"rules file {where}.source.before must be a string or null")
        _version(high, f"rules file {where}.source.before")
    targets = _items(obj, "targets", where)
    if not all(t in TARGETS for t in targets):
        raise InventoryError(f"rules file {where}.targets must name only {TARGETS}")
    sources = _items(obj, "sources", where)
    if not all(isinstance(s, str) for s in sources) or not any(
        s.startswith(LEARN) for s in cast(list[str], sources)
    ):
        raise InventoryError(
            f"rules file {where}.sources must cite a Microsoft Learn page ({LEARN})"
        )
    return Rule(
        id=rule_id,
        kind=kind,
        cls=cast(str, cls),
        title=_text(obj, "title", where),
        source_from=low,
        source_before=high,
        targets=tuple(cast(list[str], targets)),
        detection=_detection(obj.get("detection"), kind, f"{where}.detection"),
        fix=_text(obj, "fix", where),
        sources=tuple(cast(list[str], sources)),
    )


def parse_rules(data: object, sha256: str = "") -> Rules:
    """Build `Rules` from a decoded rules file; the version rule runs first.

    Raises:
        InventoryError: the file breaks the rules format, a rule lacks its
            version range or a Microsoft Learn source, or two rules share an id.
    """
    obj = _obj(data, "file")
    schema = obj.get("schema_version")
    if type(schema) is not int or schema != RULES_SCHEMA_VERSION:
        raise InventoryError(
            f"rules file schema_version {schema!r} is not supported; "
            f"this crm reads schema_version {RULES_SCHEMA_VERSION}"
        )
    version = obj.get("version")
    if type(version) is not int or version < 1:
        raise InventoryError("rules file version must be a positive integer")
    rules = tuple(_rule(r, f"rules[{i}]") for i, r in enumerate(_items(obj, "rules", "file")))
    ids = [r.id for r in rules]
    if len(set(ids)) != len(ids):
        raise InventoryError("rules file holds two rules with one id")
    return Rules(version, sha256, rules)


def load_rules(path: Path = RULES_PATH) -> Rules:
    """Read and check the rules file at *path*, the packaged one by default."""
    try:
        raw = path.read_bytes()
        data = json.loads(raw.decode("utf-8-sig"))
    except (OSError, ValueError) as exc:  # ValueError: malformed UTF-8 or JSON
        raise InventoryError(f"cannot read {path}: {exc}") from exc
    return parse_rules(data, hashlib.sha256(raw).hexdigest())


# ── Findings ────────────────────────────────────────────────────────────────


@dataclass(frozen=True)
class ContentEvidence:
    """Where a content rule matched: the artifact, its hash and 1-based (line, column) locations."""

    path: str
    sha256: str
    locations: tuple[tuple[int, int], ...]
    match_count: int

    def to_dict(self) -> dict[str, Any]:
        return {
            "path": self.path,
            "sha256": self.sha256,
            "locations": [{"line": line, "column": col} for line, col in self.locations],
            "match_count": self.match_count,
        }


@dataclass(frozen=True)
class FieldEvidence:
    """The recorded field value a field rule matched."""

    field: str
    value: object

    def to_dict(self) -> dict[str, Any]:
        return {"field": self.field, "value": self.value}


@dataclass(frozen=True)
class Finding:
    """One compatibility finding on the component *kind*/*key*; *cls* is one of `CLASSES`.

    *rule* names the rule a hit, or an ``unknown`` one rule could not decide,
    came from, and is None for a finding no single rule gives; *reason* says why
    a finding is ``unknown``.
    """

    kind: str
    key: str
    cls: str
    rule: str | None
    reason: str | None
    evidence: ContentEvidence | FieldEvidence | None
    #: The row's usage (`crm.core.usage.Usage.to_dict`), set by the usage filter; absent means
    #: not read, never unused.
    usage: dict[str, Any] | None = None

    def to_dict(self) -> dict[str, Any]:
        out: dict[str, Any] = {
            "kind": self.kind,
            "key": self.key,
            "class": self.cls,
            "rule": self.rule,
            "reason": self.reason,
            "evidence": None if self.evidence is None else self.evidence.to_dict(),
        }
        if self.usage is not None:
            out["usage"] = self.usage
        return out


@dataclass(frozen=True)
class AssessResult:
    """The findings of one inventory against one target, with what they were computed from."""

    inventory: dict[str, Any]
    target: str
    rules: Rules
    findings: tuple[Finding, ...]
    unattached_gaps: tuple[dict[str, Any], ...]
    incomplete: dict[str, str]
    usage_filtered: int = 0

    def summary(self) -> dict[str, Any]:
        by_kind: dict[str, dict[str, int]] = {}
        for f in self.findings:
            row = by_kind.setdefault(f.kind, {})
            row[f.cls] = row.get(f.cls, 0) + 1
        states = [f.usage["state"] if f.usage else "not_read" for f in self.findings]
        return {
            "schema_version": SCHEMA_VERSION,
            "inventory": self.inventory,
            "target": self.target,
            "rules": {"version": self.rules.version, "sha256": self.rules.sha256},
            "counts": {
                "by_class": {c: sum(f.cls == c for f in self.findings) for c in CLASSES},
                "by_kind": {k: by_kind[k] for k in sorted(by_kind)},
                "by_usage": {s: states.count(s) for s in ("used", "unknown", "unused", "not_read")},
            },
            "unattached_gaps": len(self.unattached_gaps),
            "incomplete": self.incomplete,
            "usage_filtered": self.usage_filtered,
        }


def _canonical(value: object) -> str:
    return json.dumps(value, sort_keys=True, ensure_ascii=False)


def dump_findings(result: AssessResult, *, generated_at: str | None = None) -> str:
    """The findings file, sorted: only *generated_at* (default: now, UTC) differs between runs."""
    stamp = generated_at or datetime.now(UTC).strftime("%Y-%m-%dT%H:%M:%SZ")
    used = sorted({f.rule for f in result.findings if f.rule})
    by_id = {r.id: r for r in result.rules.rules}
    body = {
        "schema_version": SCHEMA_VERSION,
        "generated_at": stamp,
        "inventory": result.inventory,
        "target": result.target,
        "rules": {
            "schema_version": RULES_SCHEMA_VERSION,
            "version": result.rules.version,
            "sha256": result.rules.sha256,
        },
        "rules_hit": {
            i: {
                "class": by_id[i].cls,
                "title": by_id[i].title,
                "fix": by_id[i].fix,
                "sources": list(by_id[i].sources),
            }
            for i in used
        },
        "findings": [f.to_dict() for f in result.findings],
        "unattached_gaps": list(result.unattached_gaps),
        "incomplete": result.incomplete,
    }
    if result.usage_filtered:
        body["usage_filtered"] = result.usage_filtered
    return json.dumps(body, sort_keys=True, ensure_ascii=False, indent=2) + "\n"


@dataclass(frozen=True)
class FindingsFile:
    """A findings file read back: the inventory it was made from, its findings, each rule's sources.

    *sources* maps a rule id to the Microsoft Learn URLs that justify it, so a
    reader can keep a finding's provenance without the rules file.
    """

    manifest_sha256: str
    target: str
    findings: tuple[Finding, ...]
    sources: dict[str, tuple[str, ...]]


def _get(obj: Any, name: str) -> Any:
    return cast(dict[str, Any], obj).get(name) if isinstance(obj, dict) else None


def _field(obj: Any, name: str, kind: type, where: str) -> Any:
    """``obj[name]``, which must be a *kind*: the reader's one check, naming *where* on failure."""
    value = _get(obj, name)
    if type(value) is not kind:
        raise InventoryError(f"{where}: {name} must be a {kind.__name__}")
    return value


def _evidence(value: Any, where: str) -> ContentEvidence | FieldEvidence | None:
    if value is None:
        return None
    if _get(value, "field") is not None:
        return FieldEvidence(_field(value, "field", str, where), _get(value, "value"))
    locations = tuple(
        (_field(loc, "line", int, where), _field(loc, "column", int, where))
        for loc in _field(value, "locations", list, where)
    )
    return ContentEvidence(
        _field(value, "path", str, where),
        _field(value, "sha256", str, where),
        locations,
        _field(value, "match_count", int, where),
    )


def _sources(entry: object, where: str) -> list[str]:
    urls = _field(entry, "sources", list, where)
    if not all(type(u) is str for u in urls):
        raise InventoryError(f"{where}: sources must be a list of strings")
    return urls


def read_findings(path: Path) -> FindingsFile:
    """Read the findings file `dump_findings` wrote to *path*.

    The version rule of ADR 0032 holds: an unknown ``schema_version`` is refused,
    an unknown field is ignored.

    Raises:
        InventoryError: the file is unreadable, of another version or malformed.
    """
    where = f"findings file {path}"
    try:
        data: Any = json.loads(path.read_text(encoding="utf-8-sig"))
    except (OSError, ValueError) as exc:  # ValueError: malformed UTF-8 or JSON
        raise InventoryError(f"cannot read {where}: {exc}") from exc
    version = _get(data, "schema_version")
    if type(version) is not int or version != SCHEMA_VERSION:
        raise InventoryError(
            f"{where}: schema_version {version!r} is not supported;"
            f" this crm reads findings schema_version {SCHEMA_VERSION}"
        )
    hit = _field(data, "rules_hit", dict, where)
    findings: list[Finding] = []
    for i, item in enumerate(_field(data, "findings", list, where)):
        at = f"{where}: findings[{i}]"
        cls = _field(item, "class", str, at)
        if cls not in CLASSES:
            raise InventoryError(f"{at}: unknown class {cls!r}")
        rule, reason = _get(item, "rule"), _get(item, "reason")
        if rule is not None and type(rule) is not str:
            raise InventoryError(f"{at}: rule must be a string or null")
        if rule is not None and rule not in hit:
            raise InventoryError(f"{at}: rule {rule!r} has no entry in rules_hit")
        findings.append(
            Finding(
                _field(item, "kind", str, at),
                _field(item, "key", str, at),
                cls,
                rule,
                reason if isinstance(reason, str) else None,
                _evidence(_get(item, "evidence"), f"{at}: evidence"),
            )
        )
    sources = {
        rule: tuple(_sources(entry, f"{where}: rules_hit.{rule}")) for rule, entry in hit.items()
    }
    sha = _field(_field(data, "inventory", dict, where), "manifest_sha256", str, where)
    target = _field(data, "target", str, where)
    if target not in TARGETS:
        raise InventoryError(f"{where}: target {target!r} is not one of {', '.join(TARGETS)}")
    return FindingsFile(sha, target, tuple(findings), sources)


def _locations(pattern: re.Pattern[str], text: str) -> tuple[tuple[tuple[int, int], ...], int]:
    found: list[tuple[int, int]] = []
    count = 0
    for match in pattern.finditer(text):
        count += 1
        if len(found) < MAX_LOCATIONS:
            line = text.count("\n", 0, match.start()) + 1
            found.append((line, match.start() - (text.rfind("\n", 0, match.start()) + 1) + 1))
    return tuple(found), count


def _texts(record: Record, folder: Path) -> tuple[dict[str, str], str | None]:
    """Decode the record's text artifacts, or say why they cannot be trusted."""
    texts: dict[str, str] = {}
    for a in record.artifacts:
        if a.content != "text":
            continue
        try:
            data = (folder / a.path).read_bytes()
        except OSError:
            return {}, f"artifact unreadable: {a.path}"
        if hashlib.sha256(data).hexdigest() != a.sha256:
            return {}, f"artifact changed since extraction: {a.path}"
        texts[a.path] = normalize_text(data).decode("utf-8", errors="replace")
    if not texts:
        return {}, "no text artifact"
    return texts, None


def _assess_record(record: Record, rules: list[Rule], folder: Path) -> list[Finding]:
    def finding(
        cls: str,
        rule: str | None,
        reason: str | None,
        evidence: ContentEvidence | FieldEvidence | None = None,
    ) -> Finding:
        return Finding(record.kind, record.key, cls, rule, reason, evidence)

    if not rules:
        return [finding("unknown", None, "no rule")]
    out: list[Finding] = []
    content = [r for r in rules if isinstance(r.detection, ContentPattern)]
    texts: dict[str, str] = {}
    if content:
        texts, problem = _texts(record, folder)
        if problem is not None:
            out.append(finding("unknown", None, problem))
    sha = {a.path: a.sha256 for a in record.artifacts}
    for rule in rules:
        d = rule.detection
        if isinstance(d, FieldIn):
            value = record.fields.get(d.field)
            if value is None:
                out.append(finding("unknown", rule.id, f"field {d.field} not recorded"))
            elif any(type(value) is type(v) and value == v for v in d.values):
                out.append(finding(rule.cls, rule.id, None, FieldEvidence(d.field, value)))
            continue
        for path, text in sorted(texts.items()):
            locations, count = _locations(d.pattern, text)
            if count:
                evidence = ContentEvidence(path, sha[path], locations, count)
                out.append(finding(rule.cls, rule.id, None, evidence))
    return out or [finding("clear", None, None)]


def assess(
    folder: Path, target: str, *, rules: Rules | None = None, out: Path | None = None
) -> AssessResult:
    """Run *rules* (default: the packaged ones) over the inventory at *folder* for *target*.

    Every record gets at least one finding. A rule applies to a record when its
    kind, its source range (against the inventory's ``source.version``), its
    targets and, for a content rule, the web resource type all match. A record
    no rule applies to is ``unknown`` "no rule"; one the applying rules find
    nothing in is ``clear``. With a ``source.version`` that is not a dotted
    version (the extractor writes "unknown" when it cannot read one), no range
    can be decided, so a record some rule covers is ``unknown`` instead. Each
    record named by an extraction gap, or of a kind not read ``complete``, gets
    one more ``unknown`` saying so. A gap naming no record is listed in
    *unattached_gaps*. *out*, the file the caller will write, is refused inside
    the inventory folder.

    Raises:
        InventoryError: the inventory or rules file is unreadable or breaks its
            format, *target* is unknown, or *out* lies inside the inventory.
    """
    if target not in TARGETS:
        raise InventoryError(f"target {target!r} is not one of {TARGETS}")
    if out is not None and out.resolve().is_relative_to(folder.resolve()):
        raise InventoryError(f"--out {out} lies inside the inventory folder: pass another path")
    rules = rules or load_rules()
    path = folder / MANIFEST_NAME
    try:
        raw = path.read_bytes()
        data = json.loads(raw.decode("utf-8-sig"))
    except (OSError, ValueError) as exc:  # ValueError: malformed UTF-8 or JSON
        raise InventoryError(f"cannot read {path}: {exc}") from exc
    inv = parse_manifest(data)
    version = _dotted(inv.source.version)
    active = [r for r in rules.rules if target in r.targets]
    if version is not None:
        active = [r for r in active if r.applies(version)]
    unversioned = f"source version {inv.source.version!r} is not a dotted version"
    findings: list[Finding] = []
    for record in inv.records:
        selected = [r for r in active if r.selects(record)]
        if version is None and selected:
            findings.append(Finding(record.kind, record.key, "unknown", None, unversioned, None))
        else:
            findings += _assess_record(record, selected, folder)
        status = inv.coverage[record.kind].status
        if status != "complete":
            findings.append(
                Finding(record.kind, record.key, "unknown", None, f"kind coverage {status}", None)
            )
    unattached: list[dict[str, Any]] = []
    for gap in inv.gaps:
        named = [
            r
            for r in inv.records
            if gap.key is not None and r.key == gap.key and gap.kind in (None, r.kind)
        ]
        reason = f"extraction gap: {gap.reason}"
        findings += [Finding(r.kind, r.key, "unknown", None, reason, None) for r in named]
        if not named:
            unattached.append(
                {
                    "kind": gap.kind,
                    "key": gap.key,
                    "description": gap.description,
                    "reason": gap.reason,
                }
            )
    return AssessResult(
        inventory=_header(inv, raw),
        target=target,
        rules=rules,
        findings=_sorted_findings(findings),
        unattached_gaps=tuple(sorted(unattached, key=_canonical)),
        incomplete={k: c.status for k, c in sorted(inv.coverage.items()) if c.status != "complete"},
    )


def _sorted_findings(findings: list[Finding]) -> tuple[Finding, ...]:
    """Dedupe and sort by canonical JSON: a field value may be a list or dict, so not hashable."""
    unique = {_canonical(_order(f)): f for f in findings}
    return tuple(unique[k] for k in sorted(unique))


def _order(f: Finding) -> list[Any]:
    return [f.kind, f.key, CLASSES.index(f.cls), f.rule or "", f.reason or "", f.to_dict()]


def _header(inv: Inventory, raw: bytes) -> dict[str, Any]:
    """The inventory's identity: its source, extraction time and the manifest's hash."""
    return {
        "platform": inv.source.platform,
        "org_url": inv.source.org_url,
        "version": inv.source.version,
        "extracted_at": inv.extracted_at,
        "cli_version": inv.cli_version,
        "manifest_sha256": hashlib.sha256(raw).hexdigest(),
    }
