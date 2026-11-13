"""Run the packaged compatibility rules over a migration inventory (ADR 0033).

A **compatibility rule** classifies one kind of component for one migration
step: the source versions and targets it applies to, a class (``blocker``,
``deprecated``, ``optional``), a detection, the fix and the Microsoft Learn
pages that justify it. The rules ship as data, ``crm/rules/compatibility-rules.json``
(format: ``crm/schemas/compatibility-rules-v1.schema.json``). `assess` gives every
component of a covered kind at least one **compatibility finding**, keyed by
component key: a rule's class with its evidence, ``unknown`` with a reason (no
rule selects it, an extraction gap, a kind not read complete, a field or artifact
the rule needs is missing) or ``clear`` (the rules that apply found nothing). A kind
no active rule covers is an **uncovered kind**: its records get no "no rule" finding,
and the run lists the kind once with its record count. The findings file's format is
``crm/schemas/compatibility-findings-v2.schema.json``.
A **registration rule** reads how a form or a ribbon diff calls a script library and
judges the call against what the library's own text finds (ADR 0034). This module
touches no org.
"""

from __future__ import annotations

import hashlib
import json
import re
from collections.abc import Callable, Iterable, Mapping
from dataclasses import dataclass, field, replace
from datetime import UTC, datetime
from pathlib import Path
from typing import Any, cast
from xml.etree.ElementTree import Element, ParseError

from crm.core.inventory import (
    KINDS,
    MANIFEST_NAME,
    Inventory,
    InventoryError,
    Record,
    normalize_text,
    parse_manifest,
)
from crm.core.inventory_checker import (
    OVERRIDES_PATH,
    CheckerReport,
    load_overrides,
    read_report,
    result_class,
)
from crm.core.inventory_csproj import detect_csproj
from crm.core.inventory_html import HTML_PAGE, inline_hits, resolve_src, script_tags
from crm.core.inventory_plugin_source import type_uses
from crm.core.inventory_source_map import (
    mapped_projects,
    project_sources,
    read_source_map,
    require_inventory,
)
from crm.core.report import RDL_REPORT
from crm.utils.safe_xml import fromstring

#: The format version of the findings file. Version 2 rolls an uncovered kind up to one
#: ``uncovered_kinds`` entry, so a record with no finding means its kind has no rule.
SCHEMA_VERSION = 2
#: The reason an uncovered kind, or a record of a covered kind no rule selects, carries.
NO_RULE = "no rule"


def uncovered_entries(counts: Mapping[str, int]) -> list[dict[str, Any]]:
    """The ``uncovered_kinds`` entries for *counts* (kind to record count), sorted by kind."""
    return [{"kind": k, "records": n, "reason": NO_RULE} for k, n in sorted(counts.items())]


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
NO_HOSTS = re.compile(r"(?!)")
_NOT_MAPPED = (
    "not run: this assembly has no single mapped project;"
    " give assess --source-map and --repo, with a map that matches it to one project"
)
_HOST = re.compile(r"[a-z0-9]([a-z0-9_-]*[a-z0-9])?(\.[a-z0-9]([a-z0-9_-]*[a-z0-9])?)*")


def _dotted(text: str) -> tuple[int, ...] | None:
    if _VERSION.fullmatch(text) is None:
        return None
    return tuple(int(part) for part in text.split("."))


def _version(text: str, where: str) -> tuple[int, ...]:
    version = _dotted(text)
    if version is None:
        raise InventoryError(f"{where} {text!r} is not a dotted version")
    return version


#: Written in a content_pattern where JavaScript allows whitespace or a comment between two tokens.
GAP_TOKEN = "<gap>"
#: Possessive, so a run of slashes cannot be split into line comments exponentially many ways.
_GAP = r"(?:\s|/\*[\s\S]*?\*/|//[^\n]*+)*+"


@dataclass(frozen=True)
class ContentPattern:
    """A regular expression over the text artifacts of the web resource types listed."""

    pattern: re.Pattern[str]
    web_resource_types: tuple[int, ...]


@dataclass(frozen=True)
class HostPattern:
    """The source org's host names, searched in the text artifacts of the rule's kind.

    The packaged rule holds no host: it carries the placeholder `NO_HOSTS`, which matches
    nothing, and `assess` swaps in the pattern built from the hosts of the run.
    *web_resource_types* limits a web resource rule, and is empty for any other kind.
    """

    pattern: re.Pattern[str]
    web_resource_types: tuple[int, ...] = ()


@dataclass(frozen=True)
class XamlPattern:
    """A regular expression over the stored XAML of the processes whose category is listed."""

    pattern: re.Pattern[str]
    categories: tuple[int, ...]


@dataclass(frozen=True)
class FieldIn:
    """A record field whose value is one of *values*."""

    field: str
    values: tuple[object, ...]


@dataclass(frozen=True)
class ReportDataProvider:
    """A non-system Reporting Services report whose recorded data provider is *provider*.

    A link or Excel report and a stock (origin `system`) report are outside the rule: a data
    provider is a property of an RDL, and Microsoft documents no outcome for stock ones.
    `fields.custom` is not read: a CRM 2011 org leaves it false on its own SQL reports (#1272).
    """

    provider: str


@dataclass(frozen=True)
class FormHandlerContext:
    """A form event handler that does not pass the execution context to a library carrying
    *library_rule*'s content pattern.
    """

    library_rule: str


@dataclass(frozen=True)
class RibbonActionParameter:
    """A ribbon ``JavaScriptFunction`` with no ``CrmParameter`` *parameter*, calling a library
    carrying *library_rule*'s content pattern.
    """

    library_rule: str
    parameter: str


@dataclass(frozen=True)
class PluginProject:
    """The plug-in project the source map names for an assembly: below .NET Framework 4.6.2, or
    referencing the SDK 5.0 ``Microsoft.Xrm.Sdk`` or ``Microsoft.Crm.Sdk.Proxy``.

    The repository, not the inventory, is what this detects on, so the inventory loop skips it.
    """


@dataclass(frozen=True)
class SourceTypes:
    """The type names of one namespace that a plug-in source rule lists."""

    namespace: str
    types: tuple[str, ...]


@dataclass(frozen=True)
class PluginSource:
    """A C# file of the plug-in project the source map names for an assembly that creates or
    accesses one of the listed types (`crm.core.inventory_plugin_source`).

    Like `PluginProject` it reads the repository, so the inventory loop skips it.
    """

    entries: tuple[SourceTypes, ...]

    @property
    def listed(self) -> frozenset[str]:
        return frozenset(f"{e.namespace}.{t}" for e in self.entries for t in e.types)


@dataclass(frozen=True)
class FormLibraryOrder:
    """A form whose library list names a script carrying *library_rule*'s content pattern with no
    library matching *adapter_pattern* ahead of it.
    """

    library_rule: str
    adapter_pattern: re.Pattern[str]


@dataclass(frozen=True)
class HtmlLibraryOrder:
    """An HTML web resource whose script loaded by ``src``, or inline, carries *library_rule*'s
    content pattern with no script matching *adapter_pattern* ahead of it.
    """

    library_rule: str
    adapter_pattern: re.Pattern[str]


@dataclass(frozen=True)
class RibbonLibraryOrder:
    """A ribbon command whose action calls a script carrying *library_rule*'s content pattern with
    no action matching *adapter_pattern* ahead of it, or a ``CustomRule`` calling such a script.
    """

    library_rule: str
    adapter_pattern: re.Pattern[str]


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
    detection: (
        ContentPattern
        | HostPattern
        | XamlPattern
        | FieldIn
        | ReportDataProvider
        | FormHandlerContext
        | RibbonActionParameter
        | FormLibraryOrder
        | HtmlLibraryOrder
        | RibbonLibraryOrder
        | PluginProject
        | PluginSource
    )
    fix: str
    sources: tuple[str, ...]
    #: Id of a registered code transform (`inventory_convert`) that rewrites what this rule
    #: finds, or None when the rule is detect-only.
    transform: str | None = None
    #: Id of the Solution Checker rule this rule corresponds to, as `pac solution check` writes
    #: it; a checker result on lines this rule flagged is skipped as already covered.
    checker_rule: str | None = None

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
        if isinstance(d, HtmlLibraryOrder):
            return record.fields.get("web_resource_type") == HTML_PAGE
        if isinstance(d, XamlPattern):
            return record.fields.get("category") in d.categories
        if isinstance(d, ContentPattern | HostPattern) and d.web_resource_types:
            return record.fields.get("web_resource_type") in d.web_resource_types
        return True


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


def _transform(obj: dict[str, Any], where: str) -> str | None:
    value = obj.get("transform")
    if value is None:
        return None
    if type(value) is not str or not _RULE_ID.fullmatch(value):
        raise InventoryError(f"rules file {where}.transform must be a lowercase-hyphen id")
    return value


def _checker_rule(obj: dict[str, Any], where: str) -> str | None:
    value = obj.get("checker_rule")
    if value is None:
        return None
    if type(value) is not str or not _RULE_ID.fullmatch(value):
        raise InventoryError(f"rules file {where}.checker_rule must be a lowercase-hyphen id")
    return value


def _detection(
    value: object, kind: str, where: str
) -> (
    ContentPattern
    | HostPattern
    | XamlPattern
    | FieldIn
    | ReportDataProvider
    | FormHandlerContext
    | RibbonActionParameter
    | FormLibraryOrder
    | HtmlLibraryOrder
    | RibbonLibraryOrder
    | PluginProject
    | PluginSource
):
    obj = _obj(value, where)
    detection_type = obj.get("type")
    if detection_type == "content_pattern":
        if kind != "web_resource":
            raise InventoryError(
                f"rules file {where}: content_pattern applies to web_resource only"
            )
        try:
            pattern = re.compile(_text(obj, "pattern", where).replace(GAP_TOKEN, _GAP))
        except re.error as exc:
            raise InventoryError(f"rules file {where}.pattern does not compile: {exc}") from exc
        types = _items(obj, "web_resource_types", where)
        if not all(type(t) is int for t in types):
            raise InventoryError(f"rules file {where}.web_resource_types must hold integers")
        return ContentPattern(pattern, tuple(cast(list[int], types)))
    if detection_type == "host_pattern":
        types = _items(obj, "web_resource_types", where) if "web_resource_types" in obj else []
        if not all(type(t) is int for t in types):
            raise InventoryError(f"rules file {where}.web_resource_types must hold integers")
        if bool(types) != (kind == "web_resource"):
            raise InventoryError(
                f"rules file {where}: host_pattern needs web_resource_types on a web_resource"
                " rule and takes none on another kind"
            )
        return HostPattern(NO_HOSTS, tuple(cast(list[int], types)))
    if detection_type == "xaml_pattern":
        if kind != "process":
            raise InventoryError(f"rules file {where}: xaml_pattern applies to process only")
        try:
            xaml = re.compile(_text(obj, "pattern", where))
        except re.error as exc:
            raise InventoryError(f"rules file {where}.pattern does not compile: {exc}") from exc
        categories = _items(obj, "categories", where)
        if not all(type(c) is int for c in categories):
            raise InventoryError(f"rules file {where}.categories must hold integers")
        return XamlPattern(xaml, tuple(cast(list[int], categories)))
    if detection_type == "field_in":
        values = _items(obj, "values", where)
        return FieldIn(_text(obj, "field", where), tuple(values))
    if detection_type == "report_data_provider":
        if kind != "report":
            raise InventoryError(f"rules file {where}: report_data_provider applies to report only")
        return ReportDataProvider(_text(obj, "provider", where))
    if detection_type == "form_handler_context":
        if kind != "form":
            raise InventoryError(f"rules file {where}: form_handler_context applies to form only")
        return FormHandlerContext(_text(obj, "library_rule", where))
    if detection_type == "form_library_order":
        if kind != "form":
            raise InventoryError(f"rules file {where}: form_library_order applies to form only")
        try:
            adapter = re.compile(_text(obj, "adapter_pattern", where))
        except re.error as exc:
            raise InventoryError(
                f"rules file {where}.adapter_pattern does not compile: {exc}"
            ) from exc
        return FormLibraryOrder(_text(obj, "library_rule", where), adapter)
    if detection_type == "html_library_order":
        if kind != "web_resource":
            raise InventoryError(
                f"rules file {where}: html_library_order applies to web_resource only"
            )
        try:
            adapter = re.compile(_text(obj, "adapter_pattern", where))
        except re.error as exc:
            raise InventoryError(
                f"rules file {where}.adapter_pattern does not compile: {exc}"
            ) from exc
        return HtmlLibraryOrder(_text(obj, "library_rule", where), adapter)
    if detection_type == "ribbon_library_order":
        if kind != "ribbon_diff":
            raise InventoryError(
                f"rules file {where}: ribbon_library_order applies to ribbon_diff only"
            )
        try:
            adapter = re.compile(_text(obj, "adapter_pattern", where))
        except re.error as exc:
            raise InventoryError(
                f"rules file {where}.adapter_pattern does not compile: {exc}"
            ) from exc
        return RibbonLibraryOrder(_text(obj, "library_rule", where), adapter)
    if detection_type == "ribbon_action_parameter":
        if kind != "ribbon_diff":
            raise InventoryError(
                f"rules file {where}: ribbon_action_parameter applies to ribbon_diff only"
            )
        return RibbonActionParameter(
            _text(obj, "library_rule", where), _text(obj, "parameter", where)
        )
    if detection_type == "plugin_project":
        if kind != "plugin_assembly":
            raise InventoryError(
                f"rules file {where}: plugin_project applies to plugin_assembly only"
            )
        return PluginProject()
    if detection_type == "plugin_source":
        if kind != "plugin_assembly":
            raise InventoryError(
                f"rules file {where}: plugin_source applies to plugin_assembly only"
            )
        entries: list[SourceTypes] = []
        for i, item in enumerate(_items(obj, "entries", where)):
            entry = _obj(item, f"{where}.entries[{i}]")
            names = _items(entry, "types", f"{where}.entries[{i}]")
            if not all(isinstance(n, str) and n for n in names):
                raise InventoryError(
                    f"rules file {where}.entries[{i}].types must hold non-empty strings"
                )
            entries.append(
                SourceTypes(
                    _text(entry, "namespace", f"{where}.entries[{i}]"),
                    tuple(cast(list[str], names)),
                )
            )
        return PluginSource(tuple(entries))
    raise InventoryError(
        f"rules file {where}.type {detection_type!r} is not content_pattern, xaml_pattern, "
        "field_in, report_data_provider, form_handler_context, form_library_order, "
        "html_library_order, ribbon_library_order, ribbon_action_parameter, plugin_project or "
        "plugin_source"
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
        transform=_transform(obj, where),
        checker_rule=_checker_rule(obj, where),
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
    content = {r.id for r in rules if isinstance(r.detection, ContentPattern)}
    for r in rules:
        d = r.detection
        if not isinstance(
            d,
            FormHandlerContext
            | RibbonActionParameter
            | FormLibraryOrder
            | HtmlLibraryOrder
            | RibbonLibraryOrder,
        ):
            continue
        if d.library_rule not in content:
            raise InventoryError(
                f"rules file rule {r.id}: library_rule {d.library_rule!r} "
                "is not a content_pattern rule"
            )
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
    #: A Solution Checker finding whose result spans lines: the last line, *locations* holding
    #: the first. None for every packaged rule's finding.
    end_line: int | None = None

    def to_dict(self) -> dict[str, Any]:
        out: dict[str, Any] = {
            "path": self.path,
            "sha256": self.sha256,
            "locations": [{"line": line, "column": col} for line, col in self.locations],
            "match_count": self.match_count,
        }
        if self.end_line is not None:
            out["end_line"] = self.end_line
        return out


@dataclass(frozen=True)
class RegistrationEvidence:
    """The registrations a registration rule flagged in one artifact, as the XML records them.

    A form handler is ``event``, ``attribute``, ``function``, ``library`` and the current
    ``passExecutionContext`` flag; a ribbon action is ``command``, ``function``, ``library`` and
    its ``crm_parameters``. *match_count* counts every one; *registrations* keeps the first
    `MAX_LOCATIONS`.
    """

    path: str
    sha256: str
    registrations: tuple[dict[str, Any], ...]
    match_count: int

    def to_dict(self) -> dict[str, Any]:
        return {
            "path": self.path,
            "sha256": self.sha256,
            "registrations": list(self.registrations),
            "match_count": self.match_count,
        }


@dataclass(frozen=True)
class ProjectEvidence:
    """What a plug-in project holds that the rule flagged.

    *path* is the project's repository path and *sha256* its normalized text. *fired* names the
    conditions that held, ``framework`` (the declared *target_framework_version*, None when
    absent, is below 4.6.2) and ``sdk_references`` (the SDK 5.0 assemblies in *sdk_references*).
    It carries a content finding's *locations* and *match_count* too, so a reader that predates it
    reads it as one.
    """

    path: str
    sha256: str
    locations: tuple[tuple[int, int], ...]
    match_count: int
    target_framework_version: str | None
    sdk_references: tuple[str, ...]
    fired: tuple[str, ...]

    def to_dict(self) -> dict[str, Any]:
        return {
            "path": self.path,
            "sha256": self.sha256,
            "locations": [{"line": line, "column": col} for line, col in self.locations],
            "match_count": self.match_count,
            "target_framework_version": self.target_framework_version,
            "sdk_references": list(self.sdk_references),
            "fired": list(self.fired),
        }


@dataclass(frozen=True)
class FieldEvidence:
    """The recorded field value a field rule matched."""

    field: str
    value: object

    def to_dict(self) -> dict[str, Any]:
        return {"field": self.field, "value": self.value}


#: What a finding's *evidence* holds, by the kind of rule that raised it.
Evidence = ContentEvidence | RegistrationEvidence | ProjectEvidence | FieldEvidence


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
    evidence: Evidence | None
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
class CheckerMerge:
    """What merging a Solution Checker report did: *merged* findings added, *skipped* results a
    packaged finding already covered, *unmatched* results kept out of the findings, and each
    checker rule a finding names as its ``rules_hit`` row.
    """

    sha256: str
    results: int
    merged: int
    skipped: int
    unmatched: tuple[dict[str, Any], ...]
    rules: dict[str, dict[str, Any]]

    def summary(self) -> dict[str, Any]:
        return {
            "sha256": self.sha256,
            "results": self.results,
            "merged": self.merged,
            "skipped_duplicates": self.skipped,
            "unmatched": len(self.unmatched),
        }


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
    #: The source org host names the run searched for; empty when none was given.
    source_hosts: tuple[str, ...] = ()
    #: The Solution Checker report merged in; None when none was given.
    checker: CheckerMerge | None = None
    #: Each uncovered kind (no active rule names it) and the keys of its records the run
    #: assessed; those records get no "no rule" finding.
    uncovered: dict[str, tuple[str, ...]] = field(default_factory=lambda: {})

    def uncovered_kinds(self) -> list[dict[str, Any]]:
        """One entry per uncovered kind, sorted by kind: the kind, its record count, the reason."""
        return uncovered_entries({k: len(keys) for k, keys in self.uncovered.items()})

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
            "uncovered_kinds": self.uncovered_kinds(),
            "unattached_gaps": len(self.unattached_gaps),
            "incomplete": self.incomplete,
            "usage_filtered": self.usage_filtered,
            **({"source_hosts": list(self.source_hosts)} if self.source_hosts else {}),
            **({"checker": self.checker.summary()} if self.checker else {}),
        }


def _canonical(value: object) -> str:
    return json.dumps(value, sort_keys=True, ensure_ascii=False)


def dump_findings(result: AssessResult, *, generated_at: str | None = None) -> str:
    """The findings file, sorted: only *generated_at* (default: now, UTC) differs between runs."""
    stamp = generated_at or datetime.now(UTC).strftime("%Y-%m-%dT%H:%M:%SZ")
    used = sorted({f.rule for f in result.findings if f.rule})
    by_id = {r.id: r for r in result.rules.rules}
    checked = result.checker.rules if result.checker else {}
    body: dict[str, Any] = {
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
            if i in by_id
            else checked[i]
            for i in used
        },
        "findings": [f.to_dict() for f in result.findings],
        "uncovered_kinds": result.uncovered_kinds(),
        "unattached_gaps": list(result.unattached_gaps),
        "incomplete": result.incomplete,
    }
    if result.usage_filtered:
        body["usage_filtered"] = result.usage_filtered
    if result.source_hosts:
        body["source_hosts"] = list(result.source_hosts)
    if result.checker:
        body["checker_report"] = {
            "sha256": result.checker.sha256,
            "results": result.checker.results,
            "skipped_duplicates": result.checker.skipped,
        }
        body["checker_unmatched"] = list(result.checker.unmatched)
    return json.dumps(body, sort_keys=True, ensure_ascii=False, indent=2) + "\n"


@dataclass(frozen=True)
class FindingsFile:
    """A findings file read back: the inventory it was made from, its findings, each rule's sources.

    *sources* maps a rule id to the Microsoft Learn URLs that justify it, so a
    reader can keep a finding's provenance without the rules file. *version* is the inventory's
    ``source.version`` text, *rules_sha256* the rules file the run used, *source_hosts* the hosts
    it searched for, *usage_filtered* the findings and uncovered-kind records `--in-use` or
    `--exclude-unknown` dropped, *unattached_gaps* the gaps naming no component, *incomplete*
    the kinds not read complete and *uncovered_kinds* each kind no rule covered, with its record
    count.
    """

    manifest_sha256: str
    target: str
    findings: tuple[Finding, ...]
    sources: dict[str, tuple[str, ...]]
    version: str = ""
    rules_sha256: str = ""
    source_hosts: tuple[str, ...] = ()
    unattached_gaps: tuple[dict[str, Any], ...] = ()
    incomplete: dict[str, str] = field(default_factory=lambda: {})
    usage_filtered: int = 0
    uncovered_kinds: dict[str, int] = field(default_factory=lambda: {})


def _get(obj: Any, name: str) -> Any:
    return cast(dict[str, Any], obj).get(name) if isinstance(obj, dict) else None


def _field(obj: Any, name: str, kind: type, where: str) -> Any:
    """``obj[name]``, which must be a *kind*: the reader's one check, naming *where* on failure."""
    value = _get(obj, name)
    if type(value) is not kind:
        raise InventoryError(f"{where}: {name} must be a {kind.__name__}")
    return value


def _listed(obj: Any, name: str, kind: type, where: str) -> Any:
    """``obj[name]`` when present: a *kind*, or absent/empty (an empty *kind*)."""
    value = _get(obj, name)
    if not value:
        return kind()
    if type(value) is not kind:
        raise InventoryError(f"{where}: {name} must be a {kind.__name__}")
    return value


def _evidence(value: Any, where: str) -> Evidence | None:
    if value is None:
        return None
    if _get(value, "field") is not None:
        return FieldEvidence(_field(value, "field", str, where), _get(value, "value"))
    if _get(value, "registrations") is not None:
        registrations = _field(value, "registrations", list, where)
        if not all(type(r) is dict for r in registrations):
            raise InventoryError(f"{where}: registrations must be a list of objects")
        return RegistrationEvidence(
            _field(value, "path", str, where),
            _field(value, "sha256", str, where),
            tuple(registrations),
            _field(value, "match_count", int, where),
        )
    locations = tuple(
        (_field(loc, "line", int, where), _field(loc, "column", int, where))
        for loc in _field(value, "locations", list, where)
    )
    if _get(value, "fired") is not None:
        framework = _get(value, "target_framework_version")
        if framework is not None and type(framework) is not str:
            raise InventoryError(f"{where}: target_framework_version must be a string or null")
        fired = _field(value, "fired", list, where)
        references = _field(value, "sdk_references", list, where)
        if not all(type(n) is str for n in [*fired, *references]):
            raise InventoryError(f"{where}: fired and sdk_references must be lists of strings")
        return ProjectEvidence(
            _field(value, "path", str, where),
            _field(value, "sha256", str, where),
            locations,
            _field(value, "match_count", int, where),
            framework,
            tuple(references),
            tuple(fired),
        )
    end_line = _get(value, "end_line")
    return ContentEvidence(
        _field(value, "path", str, where),
        _field(value, "sha256", str, where),
        locations,
        _field(value, "match_count", int, where),
        end_line if type(end_line) is int else None,
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
            f" this crm reads findings schema_version {SCHEMA_VERSION}:"
            " re-run `crm inventory assess` to write it again"
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
    header = _field(data, "inventory", dict, where)
    sha = _field(header, "manifest_sha256", str, where)
    target = _field(data, "target", str, where)
    if target not in TARGETS:
        raise InventoryError(f"{where}: target {target!r} is not one of {', '.join(TARGETS)}")
    gaps: list[Any] = _listed(data, "unattached_gaps", list, where)
    hosts: list[Any] = _listed(data, "source_hosts", list, where)
    incomplete: dict[Any, Any] = _listed(data, "incomplete", dict, where)
    uncovered: dict[str, int] = {}
    for i, item in enumerate(_listed(data, "uncovered_kinds", list, where)):
        at = f"{where}: uncovered_kinds[{i}]"
        uncovered[_field(item, "kind", str, at)] = _field(item, "records", int, at)
    usage_filtered = _get(data, "usage_filtered") or 0
    return FindingsFile(
        sha,
        target,
        tuple(findings),
        sources,
        str(_get(header, "version") or ""),
        str(_get(_get(data, "rules"), "sha256") or ""),
        tuple(h for h in hosts if type(h) is str),
        tuple(cast(dict[str, Any], g) for g in gaps if type(g) is dict),
        {str(k): str(v) for k, v in incomplete.items()},
        usage_filtered if type(usage_filtered) is int else 0,
        uncovered,
    )


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


class _Libraries:
    """The web resources of an inventory by lowercased name, and what a content rule finds in each.

    A registration rule asks whether a script library carries the pattern of another rule
    (``Xrm.Page``), however that library's own range or targets read; the answer is the
    library's text, so a library no script text decides is unknown, never clear.
    """

    def __init__(
        self, records: tuple[Record, ...], folder: Path, rules: tuple[Rule, ...] = ()
    ) -> None:
        self._folder = folder
        self._rules = {r.id: r for r in rules}
        self._by_name: dict[str, list[Record]] = {}
        for r in records:
            if r.kind == "web_resource" and isinstance(r.fields.get("name"), str):
                self._by_name.setdefault(str(r.fields["name"]).lower(), []).append(r)
        self._known: dict[tuple[str, str, str], tuple[bool, str | None]] = {}

    def carries(
        self, rule_id: str, name: str, pattern: re.Pattern[str] | None = None
    ) -> tuple[bool, str | None]:
        """``(True, None)`` when library *name* matches the rule *rule_id*, ``(False, None)`` when
        it does not, and ``(False, why)`` when its text cannot say. A *pattern* replaces the rule's
        own, the rule still deciding which web resources are scripts.
        """
        slot = (rule_id, name.lower(), pattern.pattern if pattern else "")
        if slot not in self._known:
            rule = self._rules.get(rule_id)
            self._known[slot] = (
                self._read(rule, name, pattern)
                if rule
                else (False, f"library rule {rule_id} is not loaded")
            )
        return self._known[slot]

    def known(self, name: str) -> bool:
        """True when a web resource of that name is in the inventory."""
        return name.lower() in self._by_name

    def pattern(self, rule_id: str) -> re.Pattern[str] | None:
        """The content pattern of rule *rule_id*, or None when it is not a loaded content rule."""
        rule = self._rules.get(rule_id)
        return (
            rule.detection.pattern if rule and isinstance(rule.detection, ContentPattern) else None
        )

    def _read(
        self, rule: Rule, name: str, pattern: re.Pattern[str] | None
    ) -> tuple[bool, str | None]:
        matches = self._by_name.get(name.lower(), [])
        if not matches:
            return False, f"library {name} is not in the inventory"
        if len(matches) > 1:
            return False, f"library {name} names more than one web resource"
        record = matches[0]
        if not rule.selects(record):
            return False, f"library {name} is not a script web resource"
        texts, problem = _texts(record, self._folder)
        if problem is not None:
            return False, f"library {name}: {problem}"
        found = pattern or cast(ContentPattern, rule.detection).pattern
        return any(found.search(t) for t in texts.values()), None


def _flag(value: str | None) -> bool:
    """A FormXml boolean; a missing ``passExecutionContext`` is false, as the platform reads it."""
    return value is not None and value.strip().lower() == "true"


def _form_handlers(root: Element) -> list[dict[str, Any]]:
    """The enabled, editable handlers of a FormXml that do not pass the execution context."""
    out: list[dict[str, Any]] = []
    for event in root.iter("event"):
        for handler in event.findall("Handlers/Handler"):  # not the platform's InternalHandlers
            if not _flag(handler.get("enabled", "true")) or _flag(
                handler.get("passExecutionContext")
            ):
                continue
            out.append(
                {
                    "event": event.get("name") or "",
                    "attribute": event.get("attribute"),
                    "function": handler.get("functionName") or "",
                    "library": handler.get("libraryName") or "",
                    "passExecutionContext": False,
                }
            )
    return sorted(out, key=_canonical)


_WEB_RESOURCE = "$webresource:"


def _web_resource_name(library: str) -> str | None:
    """The web resource a ribbon action's ``Library`` names, or None for a platform script.

    ``$webresource:<name>`` and a ``/WebResources/<name>`` path (with or without an org prefix)
    name the org's web resource. Any other value is returned as it stands, so the inventory
    lookup finds no such library and the registration is unknown, never clear.
    """
    lowered = library.lower()
    if lowered.startswith("/_static/"):
        return None
    if lowered.startswith(_WEB_RESOURCE):
        return library[len(_WEB_RESOURCE) :]
    marker = "/webresources/"
    at = lowered.find(marker)
    return library[at + len(marker) :] if at >= 0 else library


RULE_OWNERS = frozenset(("EnableRule", "DisplayRule", "TabDisplayRule", "GroupDisplayRule"))


def _ribbon_actions(root: Element, parameter: str) -> list[dict[str, Any]]:
    """The ribbon ``JavaScriptFunction`` actions on a web resource that pass no *parameter*."""
    out: list[dict[str, Any]] = []
    for command in root.iter("CommandDefinition"):
        for action in command.iter("JavaScriptFunction"):
            raw = action.get("Library") or ""
            library = _web_resource_name(raw)
            if library is None:
                continue  # a platform script (/_static/...) is not a web resource of the org
            passed = [p.get("Value") or "" for p in action.findall("CrmParameter")]
            if parameter not in passed:
                out.append(
                    {
                        "command": command.get("Id") or "",
                        "function": action.get("FunctionName") or "",
                        "library": library,
                        "crm_parameters": passed,
                    }
                )
    return sorted(out, key=_canonical)


def _library_order(
    rule: Rule,
    d: FormLibraryOrder,
    path: str,
    sha256: str,
    root: Element,
    libs: _Libraries,
    finding: Callable[..., Finding],
) -> list[Finding]:
    """A form is a hit when a library carrying *d*'s library rule loads with no adapter ahead."""
    unknown: set[str] = set()
    adapter_seen, first = False, None
    for lib in root.findall("./formLibraries/Library"):
        name = lib.get("name") or ""
        is_adapter, why = libs.carries(d.library_rule, name, d.adapter_pattern)
        if why is not None:
            unknown.add(why)
        elif is_adapter:
            adapter_seen = True
        elif first is None and not adapter_seen and libs.carries(d.library_rule, name)[0]:
            first = name
    out = [finding("unknown", rule.id, why) for why in sorted(unknown)]
    if first is not None:
        evidence = RegistrationEvidence(path, sha256, ({"library": first},), 1)
        out.append(finding(rule.cls, rule.id, None, evidence))
    return out


def _ribbon_order(
    rule: Rule,
    d: RibbonLibraryOrder,
    path: str,
    sha256: str,
    root: Element,
    libs: _Libraries,
    finding: Callable[..., Finding],
) -> list[Finding]:
    """A ribbon diff is a hit when a command's action calls a library carrying *d*'s library rule
    with no adapter action ahead of it, or a ``CustomRule`` calls such a library.
    """
    unknown: set[str] = set()
    hits: list[dict[str, Any]] = []
    parents = {c: p for p in root.iter() for c in p}
    for command in root.iter("CommandDefinition"):
        adapter_seen, first = False, None
        for action in command.iter("JavaScriptFunction"):
            library = _web_resource_name(action.get("Library") or "")
            if library is None:
                continue
            is_adapter, why = libs.carries(d.library_rule, library, d.adapter_pattern)
            if why is not None:
                unknown.add(why)
            elif is_adapter:
                adapter_seen = True
            elif first is None and not adapter_seen and libs.carries(d.library_rule, library)[0]:
                first = {
                    "command": command.get("Id") or "",
                    "function": action.get("FunctionName") or "",
                    "library": library,
                }
        if first is not None:
            hits.append(first)
    for custom in root.iter("CustomRule"):
        library = _web_resource_name(custom.get("Library") or "")
        if library is None:
            continue
        carries, why = libs.carries(d.library_rule, library)
        if why is not None:
            unknown.add(why)
        elif carries:
            owner = parents.get(custom)
            while owner is not None and not (owner.tag in RULE_OWNERS and owner.get("Id")):
                owner = parents.get(owner)
            hits.append(
                {
                    "rule": (owner.get("Id") or "") if owner is not None else "",
                    "function": custom.get("FunctionName") or "",
                    "library": library,
                }
            )
    out = [finding("unknown", rule.id, why) for why in sorted(unknown)]
    if hits:
        evidence = RegistrationEvidence(
            path, sha256, tuple(sorted(hits, key=_canonical)[:MAX_LOCATIONS]), len(hits)
        )
        out.append(finding(rule.cls, rule.id, None, evidence))
    return out


def _html_order(
    rule: Rule,
    d: HtmlLibraryOrder,
    path: str,
    sha256: str,
    text: str,
    page: str,
    libs: _Libraries,
    finding: Callable[..., Finding],
) -> list[Finding]:
    """A page is a hit when a script it loads, or an inline script, carries *d*'s library rule with
    no adapter script ahead of it.

    A ``src`` that names no web resource of the inventory (another host, a path above the root,
    a name the inventory lacks) is not a patched library and is never matched by file name.
    """
    data = text.encode("utf-8")
    tags = script_tags(data)
    if tags is None:
        return [finding("unknown", rule.id, f"the page's <script> tags cannot be placed: {path}")]
    call = libs.pattern(d.library_rule)
    inline = inline_hits(data, tags, call) if call is not None else {}
    unknown: set[str] = set()
    adapter_seen, first = False, None
    for i, tag in enumerate(tags):
        body = data[tag.body[0] : tag.body[1]].decode("utf-8")
        if tag.src is None:
            if tag.javascript and d.adapter_pattern.search(body):
                adapter_seen = True
            elif first is None and not adapter_seen and i in inline:
                first = {"inline": inline[i]}
            continue
        name = resolve_src(page, tag.src)
        if name is None or not libs.known(name):
            continue
        is_adapter, why = libs.carries(d.library_rule, name, d.adapter_pattern)
        if why is not None:
            unknown.add(why)
        elif is_adapter:
            adapter_seen = True
        elif first is None and not adapter_seen and libs.carries(d.library_rule, name)[0]:
            first = {"library": name}
    out = [finding("unknown", rule.id, why) for why in sorted(unknown)]
    if first is not None:
        out.append(
            finding(rule.cls, rule.id, None, RegistrationEvidence(path, sha256, (first,), 1))
        )
    return out


def _registrations(
    rule: Rule,
    path: str,
    sha256: str,
    text: str,
    libs: _Libraries,
    finding: Callable[..., Finding],
) -> list[Finding]:
    d = cast(
        FormHandlerContext | RibbonActionParameter | FormLibraryOrder | RibbonLibraryOrder,
        rule.detection,
    )
    try:
        root = fromstring(text)
    except ParseError:  # malformed, or carrying a DTD that safe_xml refuses
        return [finding("unknown", rule.id, f"artifact is not well-formed XML: {path}")]
    if isinstance(d, FormLibraryOrder):
        return _library_order(rule, d, path, sha256, root, libs, finding)
    if isinstance(d, RibbonLibraryOrder):
        return _ribbon_order(rule, d, path, sha256, root, libs, finding)
    candidates = (
        _form_handlers(root)
        if isinstance(d, FormHandlerContext)
        else _ribbon_actions(root, d.parameter)
    )
    hits: list[dict[str, Any]] = []
    unknown: set[str] = set()
    for candidate in candidates:
        carries, why = libs.carries(d.library_rule, candidate["library"])
        if why is not None:
            unknown.add(why)
        elif carries:
            hits.append(candidate)
    out = [finding("unknown", rule.id, why) for why in sorted(unknown)]
    if hits:
        evidence = RegistrationEvidence(path, sha256, tuple(hits[:MAX_LOCATIONS]), len(hits))
        out.append(finding(rule.cls, rule.id, None, evidence))
    return out


def _project_findings(rule: Rule, record: Record, repo: Path, rel: str) -> list[Finding]:
    """The finding for the project at *rel* under *repo*: a hit with its evidence, ``unknown``
    when it cannot be read or judged, nothing when it is at the floor with no SDK 5.0 reference.
    """
    try:
        data = normalize_text((repo / rel).read_bytes())
        text = data.decode("utf-8")
    except (OSError, UnicodeDecodeError) as exc:
        why = f"unreadable: {getattr(exc, 'strerror', None) or exc}"
        return [Finding(record.kind, record.key, "unknown", rule.id, f"project {rel} {why}", None)]
    found = detect_csproj(text)
    if found.reason is not None:
        reason = f"project {rel}: {found.reason}"
        return [Finding(record.kind, record.key, "unknown", rule.id, reason, None)]
    fired = (("framework",) if found.below_floor else ()) + (
        ("sdk_references",) if found.sdk_references else ()
    )
    if not fired:
        return []
    evidence = ProjectEvidence(
        rel,
        hashlib.sha256(data).hexdigest(),
        found.sites[:MAX_LOCATIONS],
        len(found.sdk_references) + found.below_floor,
        found.declared,
        found.sdk_references,
        fired,
    )
    return [Finding(record.kind, record.key, rule.cls, rule.id, None, evidence)]


@dataclass(frozen=True)
class _Source:
    """One C# file of a plug-in project read once for every source rule: its repository path, the
    sha256 of its normalized bytes and its uses of the listed types, or why it could not be read.
    """

    path: str
    sha256: str
    uses: dict[str, list[tuple[int, int]]]
    problem: str | None = None


def _read_sources(repo: Path, project: str, listed: frozenset[str]) -> list[_Source]:
    """Read each C# file the source map attributes to *project*, in path order."""
    out: list[_Source] = []
    for rel in project_sources(repo, project):
        try:
            data = normalize_text((repo / rel).read_bytes())
        except OSError as exc:
            why = f"unreadable: {exc.strerror or exc}"
            out.append(_Source(rel, "", {}, why))
            continue
        digest = hashlib.sha256(data).hexdigest()
        try:
            data.decode("utf-8")
        except UnicodeDecodeError:
            out.append(_Source(rel, digest, {}, "is not UTF-8"))
            continue
        uses = type_uses(data, listed)
        if uses is None:
            out.append(_Source(rel, digest, {}, "has syntax errors"))
        else:
            out.append(_Source(rel, digest, uses))
    return out


def _source_findings(
    rule: Rule, d: PluginSource, record: Record, sources: list[_Source]
) -> list[Finding]:
    """One finding per readable file *rule* flags."""
    out: list[Finding] = []
    for source in sources:
        if source.problem is not None:
            continue
        sites = sorted(s for name in d.listed for s in source.uses.get(name, ()))
        if sites:
            evidence = ContentEvidence(
                source.path, source.sha256, tuple(sites[:MAX_LOCATIONS]), len(sites)
            )
            out.append(Finding(record.kind, record.key, rule.cls, rule.id, None, evidence))
    return out


def _assess_record(
    record: Record,
    rules: list[Rule],
    folder: Path,
    libs: _Libraries | None = None,
    project: tuple[Path, str] | None = None,
) -> list[Finding]:
    def finding(
        cls: str,
        rule: str | None,
        reason: str | None,
        evidence: Evidence | None = None,
    ) -> Finding:
        return Finding(record.kind, record.key, cls, rule, reason, evidence)

    if not rules:
        return [finding("unknown", None, NO_RULE)]
    out: list[Finding] = []
    content = [
        r
        for r in rules
        if not isinstance(r.detection, FieldIn | ReportDataProvider | PluginProject | PluginSource)
    ]
    sources: list[_Source] | None = None
    texts: dict[str, str] = {}
    if content:
        texts, problem = _texts(record, folder)
        if problem is not None:
            out.append(finding("unknown", None, problem))
    sha = {a.path: a.sha256 for a in record.artifacts}
    for rule in rules:
        d = rule.detection
        if isinstance(
            d, FormHandlerContext | RibbonActionParameter | FormLibraryOrder | RibbonLibraryOrder
        ):
            for path, text in sorted(texts.items()):
                out += _registrations(
                    rule, path, sha[path], text, libs or _Libraries((), folder), finding
                )
            continue
        if isinstance(d, HtmlLibraryOrder):
            for path, text in sorted(texts.items()):
                out += _html_order(
                    rule,
                    d,
                    path,
                    sha[path],
                    text,
                    str(record.fields.get("name", "")),
                    libs or _Libraries((), folder),
                    finding,
                )
            continue
        if isinstance(d, PluginProject):
            if project is not None:
                out += _project_findings(rule, record, *project)
            continue
        if isinstance(d, PluginSource):
            if project is not None:
                if sources is None:
                    listed: frozenset[str] = frozenset(
                        name
                        for r in rules
                        if isinstance(r.detection, PluginSource)
                        for name in r.detection.listed
                    )
                    sources = _read_sources(*project, listed)
                    # No single rule reads a file it cannot parse: one unknown per file.
                    out += [
                        finding("unknown", None, f"source {x.path} {x.problem}")
                        for x in sources
                        if x.problem is not None
                    ]
                out += _source_findings(rule, d, record, sources)
            continue
        if isinstance(d, ReportDataProvider):
            # Origin, not `fields.custom`, separates stock from custom (see ReportDataProvider).
            rtype = record.fields.get("report_type")
            if rtype is None:
                out.append(finding("unknown", rule.id, "field report_type not recorded"))
                continue
            if rtype != RDL_REPORT or record.origin == "system":
                continue
            provider = record.fields.get("data_provider")
            if provider is None:
                out.append(finding("unknown", rule.id, "field data_provider not recorded"))
            elif provider == "unknown":
                reason = record.fields.get("data_provider_reason")
                out.append(finding("unknown", rule.id, str(reason or "data provider not decided")))
            elif provider == d.provider and record.origin == "unknown":
                # Not provably the customer's, not provably stock: never clear.
                out.append(
                    finding(
                        "unknown", rule.id, "record origin not recorded: stock or custom report"
                    )
                )
            elif provider == d.provider:
                out.append(
                    finding(rule.cls, rule.id, None, FieldEvidence("data_provider", provider))
                )
            continue
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


def normalize_hosts(hosts: Iterable[str]) -> tuple[str, ...]:
    """The host names of *hosts*: lowercased, deduplicated and sorted.

    Raises:
        InventoryError: a host is not a bare name (a scheme, port, path or space).
    """
    out: set[str] = set()
    for host in hosts:
        name = host.strip().lower()
        if _HOST.fullmatch(name) is None:
            raise InventoryError(
                f"--source-host {host!r} is not a bare host name: give crm01 or crm01.contoso.com,"
                " with no scheme, port or path"
            )
        out.add(name)
    return tuple(sorted(out))


def _host_regex(hosts: tuple[str, ...]) -> re.Pattern[str]:
    """Whole-host, case-insensitive: `crm01` matches `crm01.contoso.com` but not `crm010`,
    `crm01-test` or `a.crm01.com`.
    """
    names = "|".join(re.escape(h) for h in hosts)
    return re.compile(rf"(?<![a-z0-9_.-])(?:{names})(?![a-z0-9_-])", re.IGNORECASE)


def assess(
    folder: Path,
    target: str,
    *,
    rules: Rules | None = None,
    out: Path | None = None,
    source_map: Path | None = None,
    repo: Path | None = None,
    source_hosts: Iterable[str] = (),
    checker_report: Path | None = None,
    checker_overrides: Path | None = None,
) -> AssessResult:
    """Run *rules* (default: the packaged ones) over the inventory at *folder* for *target*.

    A rule is active when it names *target*, its source range holds the inventory's
    ``source.version`` and the run gave the input it needs (*source_hosts* for a host rule,
    *source_map* and *repo* for a plug-in project or source rule). A kind no active rule
    names is uncovered: its records get no "no rule" finding and land in
    `AssessResult.uncovered` instead. Every other record gets at least one finding. A rule
    applies to a record when its kind, its source range, its targets and, for a content
    rule, the web resource type all match. A record of a covered kind no rule applies to
    is ``unknown`` "no rule"; one the applying rules find
    nothing in is ``clear``. With a ``source.version`` that is not a dotted
    version (the extractor writes "unknown" when it cannot read one), no range
    can be decided, so a record some rule covers is ``unknown`` instead. Each
    record named by an extraction gap, or of a kind not read ``complete``, gets
    one more ``unknown`` saying so. A gap naming no record is listed in
    *unattached_gaps*. *out*, the file the caller will write, is refused inside
    the inventory folder.

    With *source_map* and *repo*, the application repository the map was made from, each plug-in
    assembly the map matches to exactly one project is also judged by what that project holds
    (`PluginProject`), the same projects `convert` retargets, and its C# files are read for the
    sandbox breaks the source rules list (`PluginSource`). Without them no project is read, and a
    plug-in assembly those rules apply to and the map does not match to exactly one project is
    ``unknown`` once per skipped rule (rule id set, reason naming both flags), never ``clear``.

    With *source_hosts*, the host names of the source org, each component whose stored text
    names one gets the `host_pattern` rule of its kind. Without, those rules do not run.

    With *checker_report*, a Solution Checker report a person already produced (the zip
    `pac solution check` writes, or its SARIF file), each result that names a web resource the
    inventory holds joins the findings as a finding of the checker's rule (`_merge_checker`);
    *checker_overrides* is the table of per-rule classes, the packaged one by default. The report
    is only read: no `pac` run, no checker service call. Without it nothing changes.

    Raises:
        InventoryError: the inventory or rules file is unreadable or breaks its
            format, *target* is unknown, the checker report is unreadable, *out* lies inside
            the inventory, or the source map was made from another inventory or is given
            without *repo*.
    """
    if target not in TARGETS:
        raise InventoryError(f"target {target!r} is not one of {TARGETS}")
    if out is not None and out.resolve().is_relative_to(folder.resolve()):
        raise InventoryError(f"--out {out} lies inside the inventory folder: pass another path")
    if (repo is None) != (source_map is None):
        raise InventoryError(
            "--source-map and --repo go together: the map names each project's path"
        )
    hosts = normalize_hosts(source_hosts)
    report = read_report(checker_report) if checker_report is not None else None
    rules = rules or load_rules()
    path = folder / MANIFEST_NAME
    try:
        raw = path.read_bytes()
        data = json.loads(raw.decode("utf-8-sig"))
    except (OSError, ValueError) as exc:  # ValueError: malformed UTF-8 or JSON
        raise InventoryError(f"cannot read {path}: {exc}") from exc
    inv = parse_manifest(data)
    projects: dict[str, str] = {}
    if source_map is not None:
        mapping = read_source_map(source_map)
        require_inventory(mapping, hashlib.sha256(raw).hexdigest())
        projects = mapped_projects(mapping)
    version = _dotted(inv.source.version)
    active = [r for r in rules.rules if target in r.targets]
    if hosts:
        host_pattern = _host_regex(hosts)
        active = [
            replace(r, detection=HostPattern(host_pattern, r.detection.web_resource_types))
            if isinstance(r.detection, HostPattern)
            else r
            for r in active
        ]
    else:
        active = [r for r in active if not isinstance(r.detection, HostPattern)]
    if version is not None:
        active = [r for r in active if r.applies(version)]
    unversioned = f"source version {inv.source.version!r} is not a dotted version"
    covered = {
        r.kind for r in active if repo or not isinstance(r.detection, PluginProject | PluginSource)
    }
    uncovered: dict[str, list[str]] = {}
    libs = _Libraries(inv.records, folder, rules.rules)
    findings: list[Finding] = []
    for record in inv.records:
        project = (repo, projects[record.key]) if repo and record.key in projects else None
        applying = [r for r in active if r.selects(record)]
        selected = [
            r
            for r in applying
            if project or not isinstance(r.detection, PluginProject | PluginSource)
        ]
        # Rules that apply but need a project this record lacks were not run: never `clear`.
        skipped = [r for r in applying if r not in selected] if version is not None else []
        if version is None and selected:
            findings.append(Finding(record.kind, record.key, "unknown", None, unversioned, None))
        elif record.kind not in covered:
            # Rolled up for the kind; no rule was run, so no per-record finding is owed.
            uncovered.setdefault(record.kind, []).append(record.key)
        else:
            judged = (
                _assess_record(record, selected, folder, libs, project)
                if selected or not skipped
                else []
            )
            if skipped:
                judged = [f for f in judged if f.cls != "clear"]
            findings += judged
            findings += [
                Finding(record.kind, record.key, "unknown", r.id, _NOT_MAPPED, None)
                for r in skipped
            ]
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
    checker: CheckerMerge | None = None
    if report is not None:
        overrides = load_overrides(checker_overrides or OVERRIDES_PATH)
        findings, checker = _merge_checker(report, overrides, inv, findings, rules, folder)
    return AssessResult(
        inventory=_header(inv, raw),
        target=target,
        rules=rules,
        findings=_sorted_findings(findings),
        unattached_gaps=tuple(sorted(unattached, key=_canonical)),
        incomplete={k: c.status for k, c in sorted(inv.coverage.items()) if c.status != "complete"},
        source_hosts=hosts,
        checker=checker,
        uncovered={k: tuple(sorted(keys)) for k, keys in uncovered.items()},
    )


def _merge_checker(
    report: CheckerReport,
    overrides: dict[str, str],
    inv: Inventory,
    findings: list[Finding],
    rules: Rules,
    folder: Path,
) -> tuple[list[Finding], CheckerMerge]:
    """Add one finding per checker result that names a web resource the inventory holds.

    A web-family result matches the web resource whose name is the result's file without its
    leading slash; its evidence is the inventory artifact's path and sha256 (what `convert`
    opens) with the result's lines, and its rule is the checker's own rule id. A result is
    skipped, and counted, only when the same component already has a packaged finding whose
    rule names that checker rule (`Rule.checker_rule`) and whose matched lines overlap the
    result's: a packaged finding is never dropped. A result that matches no component, or
    cannot be placed or classed, goes to the unmatched list. A component that gains a finding
    is no longer ``clear``.
    """
    by_name = {str(r.fields.get("name")): r for r in inv.records if r.kind == "web_resource"}
    counterpart = {r.id: r.checker_rule for r in rules.rules if r.checker_rule}
    by_id = {r.id: r for r in rules.rules}
    records = {(r.kind, r.key): r for r in inv.records}
    covered: dict[tuple[str, str, str], list[int]] = {}
    for f in findings:
        if f.rule in counterpart and isinstance(f.evidence, ContentEvidence):
            at = (f.kind, f.key, counterpart[f.rule])
            lines = [line for line, _ in f.evidence.locations]
            detection = by_id[f.rule].detection
            if f.evidence.match_count > len(lines) and isinstance(detection, ContentPattern):
                # The evidence keeps MAX_LOCATIONS hits; a result may sit on a later one.
                text = _texts(records[(f.kind, f.key)], folder)[0].get(f.evidence.path, "")
                lines = [
                    text.count("\n", 0, m.start()) + 1 for m in detection.pattern.finditer(text)
                ]
            covered.setdefault(at, []).extend(lines)
    added: list[Finding] = []
    unmatched: list[dict[str, Any]] = []
    hit: dict[str, dict[str, Any]] = {}
    skipped = 0
    for r in report.results:
        record = by_name.get(r.uri.removeprefix("/"))
        text = next((a for a in record.artifacts if a.content == "text"), None) if record else None
        cls = result_class(r, overrides)
        why = (
            "not a web resource rule family"
            if not r.rule.startswith("web-")
            else "file is not a web resource of the inventory"
            if record is None
            else "the inventory holds no text for the web resource"
            if text is None
            else "the result names no line"
            if r.line is None
            else f"severity {r.severity!r} has no class"
            if cls is None
            else None
        )
        if why is not None or record is None or text is None or r.line is None or cls is None:
            unmatched.append(
                {
                    "rule": r.rule,
                    "severity": r.severity,
                    "uri": r.uri,
                    "line": r.line,
                    "column": r.column,
                    "message": r.message,
                    "reason": why,
                }
            )
            continue
        last = max(r.end_line or r.line, r.line)
        if any(r.line <= n <= last for n in covered.get((record.kind, record.key, r.rule), ())):
            skipped += 1
            continue
        evidence = ContentEvidence(
            text.path, text.sha256, ((r.line, r.column),), 1, last if last > r.line else None
        )
        added.append(Finding(record.kind, record.key, cls, r.rule, None, evidence))
        info = report.rules.get(r.rule)
        row = hit.setdefault(
            r.rule,
            {
                "class": cls,
                "title": info.title if info else r.rule,
                "fix": info.fix if info else r.rule,
                "sources": [info.source] if info and info.source else [],
            },
        )
        if CLASSES.index(cls) < CLASSES.index(row["class"]):
            row["class"] = cls
    touched = {(f.kind, f.key) for f in added}
    kept = [f for f in findings if not (f.cls == "clear" and (f.kind, f.key) in touched)]
    merge = CheckerMerge(
        report.sha256,
        len(report.results),
        len(added),
        skipped,
        tuple(sorted(unmatched, key=_canonical)),
        hit,
    )
    return kept + added, merge


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
