"""Group a migration inventory into work packages over its dependency graph (ADR 0032).

The graph's nodes are the inventory's components (``<kind>:<key>``) and the
source files and C# projects a source map links them to (``file:<path>``,
``project:<path>``). Its edges come from the inventory (platform dependencies,
form libraries and event handlers, ribbon, sitemap and web resource references,
step, image and activity keys) and from the source map (component to file or
project, project to referenced project). A reference that cannot be resolved
statically is an ``unknown`` edge with no target, kept for manual review.

A **work package** is a connected group of that graph holding a source-mapped
component or an unknown, so a shared library and every consumer of it land in
one package. Only repairable kinds and source nodes join a group: entities,
attributes, option sets and views are context, and a system web resource or
plug-in does not bridge two groups. Everything here reads files and touches no org.
"""

from __future__ import annotations

import hashlib
import json
import posixpath
import re
from collections.abc import Iterable, Mapping
from dataclasses import dataclass
from pathlib import Path
from typing import Any, cast
from urllib.parse import urlsplit
from xml.etree.ElementTree import ParseError

from crm.core.inventory import (
    MANIFEST_NAME,
    ExtractionGap,
    Inventory,
    InventoryError,
    Record,
    read_inventory,
)
from crm.core.inventory_assess import Finding as AssessFinding
from crm.core.inventory_assess import FindingsFile
from crm.core.inventory_source_map import SourceMap
from crm.core.inventory_usage_filter import derive_usage, keeps, package_usage, rank
from crm.core.usage import Usage
from crm.utils.safe_xml import fromstring

#: The compatibility classes a package counts; ``clear`` is merged nowhere.
COMPAT_CLASSES = ("blocker", "deprecated", "optional", "unknown")
#: A finding of these classes on a process makes a package of its group.
PACKAGE_FORCING_CLASSES = ("blocker", "deprecated")

#: The format version of the index and package files.
SCHEMA_VERSION = 1
INDEX_NAME = "index.json"
#: The kinds a work package groups; every other component is context.
CONNECTING_KINDS = frozenset(
    ("web_resource", "plugin_assembly", "plugin_type", "step", "step_image", "process")
    + ("form", "ribbon", "ribbon_diff", "sitemap")
)
#: Kinds that carry source; a system-origin one does not bridge two groups.
_SOURCE_KINDS = frozenset(("web_resource", "plugin_assembly", "plugin_type"))
_CONSUMER_KINDS = frozenset(("form", "ribbon", "ribbon_diff", "sitemap", "process", "step"))
#: Source map statuses that leave a package's source in doubt.
_DOUBTFUL = frozenset(("stale", "missing_source", "ambiguous"))
#: Records that are edges or metadata, never graph nodes.
_NOT_NODES = frozenset(("dependency", "solution", "solution_membership"))

#: The reason of an unknown edge to a web resource a partial read does not hold: it may be
#: stock, or another solution's, so it is outside the scope rather than missing from the org.
OUT_OF_SCOPE = "out_of_scope"

_WR_TOKEN = re.compile(r"\$webresource:([^\"'<>\s&?#]+)", re.IGNORECASE)
_WR_PATH = re.compile(r"[\"'][^\"'\s]*?/WebResources/([^\"'?#\s]+)[\"']", re.IGNORECASE)
_WR_DYNAMIC = re.compile(r"(?:\$webresource:|/WebResources/)[\"']\s*\+", re.IGNORECASE)
_OPEN_WR = re.compile(r"openWebResource\s*\(\s*([^,)]*)")
_LITERAL = re.compile(r"([\"'])([^\"']+)\1")
_HTML_REF = re.compile(r"\b(src|href)\s*=\s*[\"']([^\"']+)[\"']", re.IGNORECASE)
_URL_ATTR = re.compile(r"\b(?:Url|Address)\s*=\s*\"([^\"]*)\"", re.IGNORECASE)
# Folders and pages the CRM server itself ships; a URL under one is no customization.
# Any other same-host, or relative ribbon or sitemap, URL nothing resolves is an
# ``unresolved_url`` unknown.
_PLATFORM_PAGES = (
    "/_root/", "/_grid/", "/_static/", "/_imgs/", "/_common/", "/_forms/", "/tools/",
    "/main.aspx", "/advancedfind/", "/crmreports/", "/userdefined/", "/sfa/", "/cs/", "/ma/",
    "/biz/",
)  # fmt: skip
_NO_TRIGGER = {"status": "unknown", "reason": "manifest v1 records no process trigger"}


def _id(kind: str, key: str) -> str:
    return f"{kind}:{key}"


def _hash(*parts: str) -> str:
    return hashlib.sha256("\0".join(parts).encode("utf-8")).hexdigest()


@dataclass(frozen=True)
class Edge:
    """One graph edge; an ``unknown`` edge has no *target* and says why in *reason*."""

    source: str
    target: str | None
    type: str
    reason: str | None = None
    reference: str | None = None

    def sort_key(self) -> tuple[str, ...]:
        return (self.source, self.target or "", self.type, self.reason or "", self.reference or "")

    def to_dict(self) -> dict[str, Any]:
        return {
            "from": self.source,
            "to": self.target,
            "type": self.type,
            "reason": self.reason,
            "reference": self.reference,
        }


@dataclass(frozen=True)
class Unknown:
    """The explicit entry a package field holds when the inventory has no data for it."""

    reason: str

    def to_dict(self) -> dict[str, Any]:
        return {"status": "unknown", "reason": self.reason}


@dataclass(frozen=True)
class SourceRef:
    """A member's source: its source map status, files and projects; or a referenced project."""

    kind: str
    key: str
    status: str
    files: tuple[str, ...] = ()
    projects: tuple[str, ...] = ()
    reason: str | None = None

    def to_dict(self) -> dict[str, Any]:
        return {
            "kind": self.kind,
            "key": self.key,
            "status": self.status,
            "files": list(self.files),
            "projects": list(self.projects),
            "reason": self.reason,
        }


@dataclass(frozen=True)
class Consumer:
    """A form, ribbon, sitemap, process, step or entity, and the members it references."""

    kind: str
    key: str
    references: tuple[str, ...]

    def to_dict(self) -> dict[str, Any]:
        return {"kind": self.kind, "key": self.key, "references": list(self.references)}


@dataclass(frozen=True)
class Finding:
    """A compatibility finding or unknown about one component.

    *type* is ``unknown_edge``, ``extraction_gap`` or ``source_status``, or
    ``compatibility`` for a finding `crm inventory assess` wrote: that one also
    carries its *cls* (``blocker``, ``deprecated``, ``optional`` or ``unknown``),
    *rule* id, *evidence* and the Microsoft Learn *sources* of its rule.
    """

    type: str
    component: str
    reason: str | None
    status: str | None = None
    reference: str | None = None
    cls: str | None = None
    rule: str | None = None
    evidence: Mapping[str, Any] | None = None
    sources: tuple[str, ...] = ()

    def sort_key(self) -> tuple[str, ...]:
        return (self.component, self.type, self.reason or "", self.reference or "", self.rule or "")

    @property
    def out_of_scope(self) -> bool:
        """An unknown edge to a name outside the read: listed, but not an unknown to review."""
        return self.type == "unknown_edge" and self.reason == OUT_OF_SCOPE

    def to_dict(self) -> dict[str, Any]:
        out: dict[str, Any] = {
            "type": self.type,
            "component": self.component,
            "status": self.status,
            "reason": self.reason,
            "reference": self.reference,
        }
        if self.cls is not None:
            out |= {
                "class": self.cls,
                "rule": self.rule,
                "evidence": self.evidence,
                "sources": list(self.sources),
            }
        return out


@dataclass(frozen=True)
class ExpectedBehavior:
    """A scenario naming a member, its behavior references and every package it spans."""

    scenario: str
    references: tuple[str, ...]
    packages: tuple[str, ...]

    def to_dict(self) -> dict[str, Any]:
        return {
            "scenario": self.scenario,
            "references": list(self.references),
            "packages": list(self.packages),
        }


@dataclass(frozen=True)
class Requirement:
    """What verifying a repair of *member* needs, stated, never run.

    *id* is ``req-`` plus 16 hex digits of a SHA-256 over the package id, *kind*
    and *member*, so a recorded verification result can be matched to it.
    """

    id: str
    kind: str
    member: str
    details: Mapping[str, Any]

    def to_dict(self) -> dict[str, Any]:
        return {"id": self.id, "kind": self.kind, "member": self.member, "details": self.details}


@dataclass(frozen=True)
class WorkPackage:
    """One repair job: a connected group of components and the source behind them.

    *id* is ``pkg-`` plus 12 hex digits of a SHA-256 over the sorted *members*
    (component ids) joined by newlines. Each of the five fields holds at least
    one entry: an `Unknown` where the inventory has no data for it.
    """

    id: str
    members: tuple[str, ...]
    edges: tuple[Edge, ...]
    source: tuple[SourceRef | Unknown, ...]
    consumers: tuple[Consumer | Unknown, ...]
    findings: tuple[Finding | Unknown, ...]
    expected_behavior: tuple[ExpectedBehavior | Unknown, ...]
    verification: tuple[Requirement | Unknown, ...]
    scenarios: tuple[str, ...]
    #: The members' usage rolled up (`usage_filter.package_usage`); None when none was read or
    #: derived, which is not `unused`.
    usage: Usage | None = None

    def unknowns(self) -> int:
        """The unknown edges, gaps and source statuses of the graph, not assess findings."""
        return sum(
            1
            for f in self.findings
            if isinstance(f, Finding) and f.cls is None and not f.out_of_scope
        )

    def out_of_scope_count(self) -> int:
        """The edges to names outside the read, which ``unknowns`` leaves out."""
        return sum(1 for f in self.findings if isinstance(f, Finding) and f.out_of_scope)

    def counts(self) -> dict[str, int]:
        """Findings per compatibility class; ``unknown`` adds the graph's unknowns."""
        found = [f.cls for f in self.findings if isinstance(f, Finding)]
        counts = {c: found.count(c) for c in COMPAT_CLASSES}
        counts["unknown"] += self.unknowns()
        return counts

    def to_dict(self) -> dict[str, Any]:
        return {
            "schema_version": SCHEMA_VERSION,
            "id": self.id,
            "members": list(self.members),
            "edges": [e.to_dict() for e in self.edges],
            "source": [s.to_dict() for s in self.source],
            "consumers": [c.to_dict() for c in self.consumers],
            "findings": [f.to_dict() for f in self.findings],
            "expected_behavior": [b.to_dict() for b in self.expected_behavior],
            "verification": [r.to_dict() for r in self.verification],
            "scenarios": list(self.scenarios),
            **({} if self.usage is None else {"usage": self.usage.to_dict()}),
        }


@dataclass(frozen=True)
class ScenarioReport:
    """Where one scenario landed: its packages, and its seeds no package holds."""

    name: str
    packages: tuple[str, ...]
    seeds_not_found: tuple[str, ...]
    seeds_unpackaged: tuple[str, ...]


@dataclass(frozen=True)
class PackagesResult:
    """The work packages of one inventory, with what fell outside every package."""

    inventory: Mapping[str, Any]
    packages: tuple[WorkPackage, ...]
    scenarios: tuple[ScenarioReport, ...]
    unattached_gaps: tuple[ExtractionGap, ...]
    unattached_unknowns: tuple[Edge, ...]
    incomplete: Mapping[str, str]
    #: The target of the findings file merged in, None when none was.
    findings_target: str | None = None
    #: Findings whose component is in no package.
    unplaced: tuple[AssessFinding, ...] = ()
    #: Out-of-scope edges from nodes no package holds, apart from ``unattached_unknowns``.
    unattached_out_of_scope: tuple[Edge, ...] = ()
    #: Packages `--in-use` or `--exclude-unknown` left out.
    usage_filtered: int = 0

    def _compatibility(self) -> dict[str, Any]:
        totals = [p.counts() for p in self.packages]
        return {
            "target": self.findings_target,
            **{c: sum(t[c] for t in totals) for c in COMPAT_CLASSES},
            "unplaced": len(self.unplaced),
        }

    def summary(self) -> dict[str, Any]:
        sizes = [len(p.members) for p in self.packages]
        compatibility = (
            {} if self.findings_target is None else {"compatibility": self._compatibility()}
        )
        return {
            "schema_version": SCHEMA_VERSION,
            "inventory": dict(self.inventory),
            "packages": len(self.packages),
            "members": sum(sizes),
            "largest": max(sizes, default=0),
            "unknowns": sum(p.unknowns() for p in self.packages),
            "scenarios": len(self.scenarios),
            "unmatched_scenarios": [s.name for s in self.scenarios if not s.packages],
            "seeds_not_found": [
                {"scenario": s.name, "seed": seed}
                for s in self.scenarios
                for seed in s.seeds_not_found
            ],
            "seeds_unpackaged": [
                {"scenario": s.name, "seed": seed}
                for s in self.scenarios
                for seed in s.seeds_unpackaged
            ],
            "unattached_gaps": len(self.unattached_gaps),
            "unattached_unknowns": len(self.unattached_unknowns),
            "out_of_scope": sum(p.out_of_scope_count() for p in self.packages)
            + len(self.unattached_out_of_scope),
            "incomplete": dict(self.incomplete),
            "usage_filtered": self.usage_filtered,
        } | compatibility

    def index(self) -> dict[str, Any]:
        """The summary plus a line per package and scenario, and the unattached gaps."""
        merged = self.findings_target is not None
        extra = (
            {
                "compatibility": {
                    **self._compatibility(),
                    "unplaced": [
                        {"kind": f.kind, "key": f.key, "class": f.cls, "rule": f.rule}
                        for f in self.unplaced
                    ],
                }
            }
            if merged
            else {}
        )
        return {
            **self.summary(),
            **extra,
            "packages": [
                {
                    "id": p.id,
                    "file": f"{p.id}.json",
                    "members": len(p.members),
                    "unknowns": p.unknowns(),
                    "out_of_scope": p.out_of_scope_count(),
                    **({"counts": p.counts()} if merged else {}),
                    "scenarios": list(p.scenarios),
                    **({} if p.usage is None else {"usage": p.usage.to_dict()}),
                }
                for p in self.packages
            ],
            "scenarios": [
                {
                    "name": s.name,
                    "packages": list(s.packages),
                    "seeds_not_found": list(s.seeds_not_found),
                    "seeds_unpackaged": list(s.seeds_unpackaged),
                }
                for s in self.scenarios
            ],
            "unattached_gaps": [
                {"kind": g.kind, "key": g.key, "description": g.description, "reason": g.reason}
                for g in self.unattached_gaps
            ],
            "unattached_unknowns": [e.to_dict() for e in self.unattached_unknowns],
            "unattached_out_of_scope": [e.to_dict() for e in self.unattached_out_of_scope],
        }


def _dump(data: object) -> str:
    return json.dumps(data, sort_keys=True, ensure_ascii=False, indent=2) + "\n"


def write_packages(result: PackagesResult, out: Path) -> None:
    """Write ``index.json`` and one ``<package id>.json`` per package into the empty folder *out*.

    Raises:
        InventoryError: *out* exists and is not empty.
    """
    if out.exists() and any(out.iterdir()):
        raise InventoryError(f"--out {out} is not empty; name a new or empty folder")
    out.mkdir(parents=True, exist_ok=True)
    (out / INDEX_NAME).write_text(_dump(result.index()), encoding="utf-8")
    for package in result.packages:
        (out / f"{package.id}.json").write_text(_dump(package.to_dict()), encoding="utf-8")


@dataclass(frozen=True)
class PackagesOutput:
    """A packages folder read back: its identity and each package's requirements.

    *sha256* is a SHA-256 over the lines ``<file sha256>  <file name>``, ``index.json``
    first, then each package file in index order, so changing any file changes it.
    *requirements* maps each package id to its requirements, empty when its
    verification is unknown.
    """

    sha256: str
    requirements: Mapping[str, tuple[Requirement, ...]]


PACKAGE_ID = re.compile(r"pkg-[0-9a-f]{12}")


def read_packages(folder: Path) -> PackagesOutput:
    """Read the ``index.json`` and package files `write_packages` wrote into *folder*.

    The version rule of ADR 0032 holds: an unknown ``schema_version`` is refused,
    an unknown field is ignored.

    Raises:
        InventoryError: a file is missing, unreadable, of another version or malformed.
    """
    digest = hashlib.sha256()

    def load(name: str) -> dict[str, Any]:
        path = folder / name
        try:
            raw = path.read_bytes()
            data = json.loads(raw.decode("utf-8"))
        except (OSError, ValueError) as exc:  # ValueError: malformed UTF-8 or JSON
            raise InventoryError(f"cannot read {path}: {exc}") from exc
        version = cast(dict[str, Any], data).get("schema_version") if type(data) is dict else None
        if type(version) is not int or version != SCHEMA_VERSION:
            raise InventoryError(
                f"{path}: schema_version {version!r} is not supported;"
                f" this crm reads packages schema_version {SCHEMA_VERSION}"
            )
        digest.update(f"{hashlib.sha256(raw).hexdigest()}  {name}\n".encode())
        return cast(dict[str, Any], data)

    entries = load(INDEX_NAME).get("packages")
    if type(entries) is not list:
        raise InventoryError(f"{folder / INDEX_NAME}: packages is not a list")
    requirements: dict[str, tuple[Requirement, ...]] = {}
    for entry in cast(list[Any], entries):
        pid = cast(dict[str, Any], entry).get("id") if type(entry) is dict else None
        if not (isinstance(pid, str) and PACKAGE_ID.fullmatch(pid)):
            raise InventoryError(f"{folder / INDEX_NAME}: malformed package entry {entry!r}")
        package = load(f"{pid}.json")
        items = package.get("verification")
        if package.get("id") != pid or type(items) is not list:
            raise InventoryError(f"{folder / pid}.json: id or verification is malformed")
        reqs: list[Requirement] = []
        for item in cast(list[Any], items):
            item = cast(dict[str, Any], item) if type(item) is dict else {}
            if item.get("status") == "unknown":
                continue
            fields = [item.get(k) for k in ("id", "kind", "member")]
            if not all(isinstance(f, str) for f in fields):
                raise InventoryError(f"{folder / pid}.json: malformed requirement {item!r}")
            rid, kind, member = cast(list[str], fields)
            reqs.append(Requirement(rid, kind, member, item.get("details") or {}))
        requirements[pid] = tuple(reqs)
    return PackagesOutput(digest.hexdigest(), requirements)


# ── References read from the inventory's artifacts ─────────────────────────


@dataclass
class _Refs:
    """What one pass over the inventory found: edges, and each form's event handlers."""

    names: Mapping[str, str]  # lowercased web resource name -> its spelling
    org_host: str
    org_path: str  # an on-prem org URL's /<org> prefix, lowercased, or ""
    edges: set[Edge]
    handlers: dict[str, list[dict[str, str | None]]]
    partial: bool = False  # the read left components out, so a missed name is not proven absent

    def platform_page(self, path: str) -> bool:
        """True when *path* (lowercased, leading slash) is under a folder the server ships."""
        if self.org_path and path.startswith(self.org_path + "/"):
            path = path[len(self.org_path) :]
        return path.startswith(_PLATFORM_PAGES)

    def link_name(self, source: str, name: str, edge_type: str) -> None:
        """Add an edge to the web resource *name*, or an unknown one when none has that name."""
        spelled = self.names.get(name.lower())
        if spelled is None:
            reason = OUT_OF_SCOPE if self.partial else "not_in_inventory"
            self.edges.add(Edge(source, None, "unknown", reason, name))
        elif _id("web_resource", spelled) != source:
            self.edges.add(Edge(source, _id("web_resource", spelled), edge_type))

    def unknown(self, source: str, reason: str, reference: str) -> None:
        self.edges.add(Edge(source, None, "unknown", reason, reference))

    def link_url(self, source: str, value: str, edge_type: str) -> bool:
        """Classify a URL; return False when it names no web resource, ISV page or other host."""
        low = value.strip().lower()
        if not low or low.startswith("$webresource:"):
            return True  # the $webresource: token scan reads it
        if "{" in low:
            self.unknown(source, "dynamic", value)
            return True
        if "/isv/" in low or low.startswith("isv/"):
            self.unknown(source, "isv_page", value)
            return True
        try:
            parts = urlsplit(value.strip())
        except ValueError:  # an unbalanced IPv6 bracket
            self.unknown(source, "unreadable", value)
            return True
        path = parts.path.lower()
        if parts.scheme in ("http", "https") or low.startswith("//"):
            if (parts.hostname or "").lower() != self.org_host:
                self.unknown(source, "external_url", value)
            elif "/webresources/" in path:
                self.link_name(source, _after_webresources(parts.path), edge_type)
            elif not self.platform_page(path):
                self.unknown(source, "unresolved_url", value)
            return True
        if parts.scheme:
            return True  # javascript:, mailto:, data:
        if "/webresources/" in "/" + path:
            self.link_name(source, _after_webresources("/" + parts.path), edge_type)
            return True
        return False


def _after_webresources(path: str) -> str:
    low = path.lower()
    return path[low.index("/webresources/") + len("/webresources/") :]


def _texts(folder: Path, record: Record) -> list[tuple[str, str]]:
    out: list[tuple[str, str]] = []
    for artifact in record.artifacts:
        if artifact.content != "text":
            continue
        try:
            data = (folder / artifact.path).read_bytes()
        except OSError as exc:
            raise InventoryError(f"cannot read artifact {artifact.path}: {exc}") from exc
        out.append((artifact.path, data.decode("utf-8", errors="replace")))
    return out


def _form(refs: _Refs, source: str, record: Record, texts: list[tuple[str, str]]) -> None:
    handlers = refs.handlers.setdefault(source, [])
    libraries: list[str] = [
        str(lib) for lib in cast("list[object]", record.fields.get("libraries") or [])
    ]
    for raw in cast("list[dict[str, Any]]", record.fields.get("handlers") or []):
        handlers.append(
            {
                "event": raw["event"],
                "attribute": raw.get("attribute"),
                "function": raw["function"],
                "library": raw["library"],
            }
        )
    for path, text in texts:
        try:
            root = fromstring(text)
        except ParseError:  # malformed, or carrying a DTD that safe_xml refuses
            refs.unknown(source, "unreadable", path)
            continue
        libraries.extend(lib.get("name") or "" for lib in root.iter("Library"))
        for event in root.iter("event"):
            for handler in event.iter("Handler"):
                handlers.append(
                    {
                        "event": event.get("name") or "",
                        "attribute": event.get("attribute"),
                        "function": handler.get("functionName") or "",
                        "library": handler.get("libraryName") or "",
                    }
                )
        for url in root.iter("Url"):
            value = (url.text or "").strip()
            if value and not refs.link_url(source, value, "form_control"):
                refs.link_name(source, value, "form_control")
    for library in libraries:
        if library:
            refs.link_name(source, library, "form_library")
    unique: list[dict[str, str | None]] = []
    for handler in handlers:
        if handler not in unique:
            unique.append(handler)
            if handler["library"]:
                refs.link_name(source, str(handler["library"]), "event_handler")
    refs.handlers[source] = unique


def _web_resource(refs: _Refs, source: str, key: str, text: str) -> None:
    for match in _OPEN_WR.finditer(text):
        arg = match.group(1).strip()
        literal = _LITERAL.fullmatch(arg)
        if literal:
            refs.link_name(source, literal.group(2), "open_web_resource")
        else:
            refs.unknown(source, "dynamic", arg)
    for match in _WR_DYNAMIC.finditer(text):
        refs.unknown(source, "dynamic", match.group(0))
    for name in _WR_PATH.findall(text):
        refs.link_name(source, name, "web_resource_reference")
    for match in _HTML_REF.finditer(text):
        ref = match.group(2).strip()
        if refs.link_url(source, ref, "web_resource_reference"):
            continue
        path = ref.split("#", 1)[0].split("?", 1)[0]
        if not path or path.startswith("/") or path.lower().endswith(".aspx"):
            continue  # an anchor, or a platform page such as ClientGlobalContext.js.aspx
        refs.link_name(
            source, posixpath.normpath(posixpath.join(posixpath.dirname(key), path)),
            "web_resource_reference",
        )  # fmt: skip


def _references(inv: Inventory, folder: Path) -> _Refs:
    names = {r.key.lower(): r.key for r in inv.records if r.kind == "web_resource"}
    org = urlsplit(inv.source.org_url)
    partial = inv.scope.mode == "solutions" or inv.scope.system == "excluded"
    refs = _Refs(
        names,
        (org.hostname or "").lower(),
        org.path.lower().rstrip("/"),
        set(),
        {},
        partial=partial,
    )
    for record in inv.records:
        if record.kind not in ("form", "ribbon", "ribbon_diff", "sitemap", "web_resource"):
            continue
        source = _id(record.kind, record.key)
        texts = _texts(folder, record)
        if record.kind == "form":
            _form(refs, source, record, texts)
        edge_type = {
            "ribbon": "ribbon_reference",
            "ribbon_diff": "ribbon_reference",
            "sitemap": "sitemap_reference",
        }.get(record.kind, "web_resource_reference")
        for _, text in texts:
            for name in _WR_TOKEN.findall(text):
                refs.link_name(source, name, edge_type)
            if record.kind in ("ribbon", "ribbon_diff", "sitemap"):
                for value in _URL_ATTR.findall(text):
                    path = "/" + value.strip().lower().lstrip("/")
                    if not refs.link_url(source, value, edge_type) and not refs.platform_page(path):
                        refs.unknown(source, "unresolved_url", value)
            elif record.kind == "web_resource":
                _web_resource(refs, source, record.key, text)
    return refs


def _nodes(inv: Inventory) -> dict[str, Record]:
    nodes: dict[str, Record] = {}
    for record in inv.records:
        if record.kind not in _NOT_NODES:
            nodes.setdefault(_id(record.kind, record.key), record)
    return nodes


def reference_edges(inv: Inventory, folder: Path) -> set[Edge]:
    """The edges naming a reference: artifact references and the platform's dependency records."""
    return _references(inv, folder).edges | _structural(inv, _nodes(inv))


# ── The graph and its packages ─────────────────────────────────────────────


def _structural(inv: Inventory, nodes: Mapping[str, Record]) -> set[Edge]:
    """Edges the component keys and the platform's dependency records name."""
    edges: set[Edge] = set()

    def link(source: str, target: str, edge_type: str) -> None:
        if target in nodes:
            edges.add(Edge(source, target, edge_type))
        elif target.split(":", 1)[0] in CONNECTING_KINDS:
            edges.add(Edge(source, None, "unknown", "not_in_inventory", target))

    for record in inv.records:
        source = _id(record.kind, record.key)
        fields = record.fields
        if record.kind == "step":
            link(source, _id("plugin_type", str(fields["plugin_type"])), "step_plugin_type")
        elif record.kind == "step_image":
            link(source, _id("step", str(fields["step"])), "step_image")
        elif record.kind == "plugin_type":
            link(source, _id("plugin_assembly", str(fields["assembly"])), "plugin_type_assembly")
        elif record.kind == "process":
            for activity in cast("list[str]", fields.get("activities") or []):
                link(source, _id("plugin_type", activity), "process_activity")
        elif record.kind == "dependency":
            dependent = _id(str(fields["dependent_kind"]), str(fields["dependent_key"]))
            required = _id(str(fields["required_kind"]), str(fields["required_key"]))
            if dependent in nodes:
                link(dependent, required, "platform_dependency")
            elif required in nodes and dependent.split(":", 1)[0] in CONNECTING_KINDS:
                edges.add(Edge(required, None, "unknown", "not_in_inventory", dependent))
    return edges


def _source_edges(source_map: SourceMap) -> set[Edge]:
    edges: set[Edge] = set()
    for c in source_map.components:
        cid = _id(c.kind, c.key)
        edges.update(Edge(cid, f"file:{f}", "source_file") for f in c.files)
        edges.update(Edge(cid, f"project:{p}", "source_project") for p in c.projects)
    for project, references in source_map.project_references.items():
        edges.update(
            Edge(f"project:{project}", f"project:{r}", "project_reference") for r in references
        )
    return edges


def _groups(nodes: Mapping[str, Record], edges: Iterable[Edge]) -> list[list[str]]:
    """Connected groups over the edges whose both ends may join a package."""

    def joins(node: str) -> bool:
        record = nodes.get(node)
        if record is None:
            return node.startswith(("file:", "project:"))
        if record.kind in _SOURCE_KINDS and record.origin == "system":
            return False
        return record.kind in CONNECTING_KINDS

    parent: dict[str, str] = {n: n for n in nodes if joins(n)}

    def find(node: str) -> str:
        while parent[node] != node:
            parent[node] = parent[parent[node]]
            node = parent[node]
        return node

    for edge in edges:
        if edge.target is None or not (joins(edge.source) and joins(edge.target)):
            continue
        parent.setdefault(edge.source, edge.source)
        parent.setdefault(edge.target, edge.target)
        a, b = find(edge.source), find(edge.target)
        if a != b:
            parent[max(a, b)] = min(a, b)
    groups: dict[str, list[str]] = {}
    for node in sorted(parent):
        groups.setdefault(find(node), []).append(node)
    return list(groups.values())


def _scenarios(path: Path) -> list[tuple[str, tuple[str, ...], tuple[str, ...]]]:
    """Read a scenario file: ``{"scenarios": [{"name", "seeds", "expected_behavior"}]}``."""
    try:
        data = json.loads(path.read_text(encoding="utf-8-sig"))
    except (OSError, ValueError) as exc:
        raise InventoryError(f"cannot read --scenarios {path}: {exc}") from exc
    if not isinstance(data, dict) or not isinstance(
        cast("dict[str, Any]", data).get("scenarios"), list
    ):
        raise InventoryError('--scenarios must be an object with a "scenarios" array')
    out: list[tuple[str, tuple[str, ...], tuple[str, ...]]] = []
    for i, item in enumerate(cast("list[object]", cast("dict[str, Any]", data)["scenarios"])):
        where = f"--scenarios scenarios[{i}]"
        if not isinstance(item, dict):
            raise InventoryError(f"{where} must be an object")
        scenario = cast("dict[str, object]", item)
        name, seeds = scenario.get("name"), scenario.get("seeds")
        behavior = scenario.get("expected_behavior", [])
        if not isinstance(name, str) or not name:
            raise InventoryError(f"{where}.name must be a non-empty string")
        for label, value in (("seeds", seeds), ("expected_behavior", behavior)):
            if not isinstance(value, list) or not all(
                isinstance(v, str) for v in cast("list[object]", value)
            ):
                raise InventoryError(f"{where}.{label} must be an array of strings")
        if any(name == seen for seen, _, _ in out):
            raise InventoryError(f"--scenarios names {name!r} twice")
        out.append((name, tuple(cast("list[str]", seeds)), tuple(cast("list[str]", behavior))))
    return out


def _requirements(
    package_id: str,
    members: set[str],
    nodes: Mapping[str, Record],
    edges: list[Edge],
    handlers: Mapping[str, list[dict[str, str | None]]],
) -> list[Requirement]:
    def req(kind: str, member: str, details: dict[str, Any]) -> Requirement:
        return Requirement(f"req-{_hash(package_id, kind, member)[:16]}", kind, member, details)

    targets: dict[str, list[str]] = {}
    unknown: dict[str, list[str]] = {}
    for e in edges:
        if e.target is None:
            unknown.setdefault(e.source, []).append(e.reference or "")
        elif e.target in members and e.target.startswith("web_resource:"):
            targets.setdefault(e.source, []).append(e.target.split(":", 1)[1])
    out: list[Requirement] = []
    for member in sorted(members):
        record = nodes[member]
        f = record.fields
        libraries = sorted(set(targets.get(member, [])))
        if record.kind == "form" and libraries:
            lowered = {lib.lower() for lib in libraries}  # link_name matches names caselessly
            on = [
                h
                for h in handlers.get(member, [])
                if h["library"] and h["library"].lower() in lowered
            ]
            out.append(req("form_script", member, {"libraries": libraries, "handlers": on}))
        elif record.kind in ("ribbon", "ribbon_diff", "sitemap") and (
            libraries or member in unknown
        ):
            out.append(
                req(
                    "sitemap_entry" if record.kind == "sitemap" else "ribbon_command",
                    member,
                    {"web_resources": libraries, "unknown": sorted(set(unknown.get(member, [])))},
                )
            )
        elif record.kind == "step":
            images = [
                {"alias": i.fields["alias"], "image_type": i.fields["image_type"],
                 "attributes": i.fields.get("attributes")}
                for i in nodes.values()
                if i.kind == "step_image" and i.fields["step"] == record.key
            ]  # fmt: skip
            images.sort(key=lambda i: (str(i["alias"]), str(i["image_type"])))
            details = {k: f.get(k) for k in ("message", "primary_entity", "stage", "mode")}
            details |= {"filtering_attributes": f.get("filtering_attributes"), "images": images}
            out.append(req("plugin_step", member, details))
        elif record.kind == "process":
            details = {"category": f.get("category"), "primary_entity": f.get("primary_entity")}
            out.append(req("process_trigger", member, details | {"trigger": _NO_TRIGGER}))
    return sorted(out, key=lambda r: (r.kind, r.member))


def _consumers(
    members: set[str], nodes: Mapping[str, Record], edges: Iterable[Edge]
) -> list[Consumer]:
    """Each consumer in the group, and each entity a consumer sits on or that requires a member."""
    neighbours: dict[str, set[str]] = {}
    entities: dict[str, set[str]] = {}
    for e in edges:
        if e.target is None:
            continue
        neighbours.setdefault(e.source, set()).add(e.target)
        neighbours.setdefault(e.target, set()).add(e.source)
        dependent = nodes.get(e.source)
        if (
            e.type == "platform_dependency"
            and e.target in members
            and dependent is not None
            and dependent.kind not in CONNECTING_KINDS
        ):
            entities.setdefault(e.source, set()).add(e.target)
    out: list[Consumer] = []
    for member in sorted(members):
        record = nodes[member]
        if record.kind not in _CONSUMER_KINDS:
            continue
        used = sorted(
            n for n in neighbours.get(member, ()) if n in members and nodes[n].kind in _SOURCE_KINDS
        )
        out.append(Consumer(record.kind, record.key, tuple(used)))
        entity = record.fields.get(
            "entity" if record.kind in ("form", "ribbon", "ribbon_diff") else "primary_entity"
        )
        if isinstance(entity, str) and entity:
            entities.setdefault(_id("entity", entity), set()).add(member)
    for node, refs in entities.items():
        kind, key = node.split(":", 1)
        out.append(Consumer(kind, key, tuple(sorted(refs))))
    return sorted(out, key=lambda c: (c.kind, c.key))


def _expected(behaviors: list[ExpectedBehavior]) -> Iterable[ExpectedBehavior | Unknown]:
    """Each tagging scenario, followed by an unknown when it names no expected behavior."""
    for b in behaviors:
        yield b
        if not b.references:
            yield Unknown(f"scenario {b.scenario!r} names no expected behavior")


def build_packages(
    inventory_dir: Path,
    source_map: SourceMap,
    scenarios: Path | None = None,
    findings_file: FindingsFile | None = None,
    *,
    in_use: bool = False,
    exclude_unknown: bool = False,
    sort: str = "default",
) -> PackagesResult:
    """Build the dependency graph of the inventory at *inventory_dir* and group it into packages.

    *source_map* must have been made from this inventory (its ``manifest_sha256``
    matches); *scenarios*, when given, is a scenario file (see `_scenarios`).
    *findings_file*, when given, must name the same inventory: each of its
    blocker, deprecated, optional and unknown findings joins the package holding
    its component, or is listed as unplaced. A blocker or deprecated finding on a
    ``process`` also makes a package of its group when no other rule did, so an
    isolated dialog or workflow is judged by ``verify``; no other finding creates one.
    Each package gets the `usage` of its members rolled up: read by `extract --with-usage`
    or derived from the manifest (`inventory_usage_filter`). *in_use* drops a package whose
    usage is `unused`, *exclude_unknown* also one that is `unknown`; a package with no usage
    (none was read or derived) stays. *sort* ``usage`` lists used packages first.

    Raises:
        InventoryError: an input cannot be read, or the source map or findings
            file is of another inventory.
    """
    inv = read_inventory(inventory_dir)
    manifest_sha = hashlib.sha256((inventory_dir / MANIFEST_NAME).read_bytes()).hexdigest()
    if source_map.inventory.get("manifest_sha256") != manifest_sha:
        raise InventoryError(
            "the source map was made from another inventory (its manifest_sha256 differs);"
            " re-run `crm inventory map-source` on this one"
        )
    if findings_file is not None and findings_file.manifest_sha256 != manifest_sha:
        raise InventoryError(
            "the findings file was made from another inventory (its manifest_sha256 differs);"
            " re-run `crm inventory assess` on this one"
        )
    nodes = _nodes(inv)
    refs = _references(inv, inventory_dir)
    structural = _structural(inv, nodes)
    edges = sorted(refs.edges | structural | _source_edges(source_map), key=Edge.sort_key)
    mapped = {_id(c.kind, c.key): c for c in source_map.components}
    gaps: dict[str, list[ExtractionGap]] = {}
    for gap in inv.gaps:
        if gap.kind and gap.key is not None:
            gaps.setdefault(_id(gap.kind, gap.key), []).append(gap)
    read_scenarios = _scenarios(scenarios) if scenarios is not None else []

    judged: dict[str, list[AssessFinding]] = {}
    for f in findings_file.findings if findings_file else ():
        if f.cls != "clear":
            judged.setdefault(_id(f.kind, f.key), []).append(f)
    placed: set[str] = set()
    # A process finding that blocks or retires the process forces a package (see docstring).
    forced = {
        m
        for m, found in judged.items()
        if m in nodes
        and nodes[m].kind == "process"
        and any(f.cls in PACKAGE_FORCING_CLASSES for f in found)
    }

    usages = derive_usage(inv.records, refs.edges | structural, inv.extracted_at)
    drafts: list[tuple[str, set[str], list[str], list[Edge], list[Finding], Usage | None]] = []
    filtered = 0
    attached: set[str] = set()
    packaged: set[str] = set()
    for group in _groups(nodes, edges):
        members = {n for n in group if n in nodes}
        in_group = set(group)
        group_edges = [e for e in edges if e.source in in_group]
        findings = [
            Finding("unknown_edge", e.source, e.reason, reference=e.reference)
            for e in group_edges
            if e.target is None
        ]
        findings += [
            Finding("extraction_gap", m, g.reason, reference=g.description)
            for m in sorted(members)
            for g in gaps.get(m, [])
        ]
        findings += [
            Finding("source_status", m, mapped[m].reason, status=mapped[m].status)
            for m in sorted(members)
            if m in mapped and mapped[m].status in _DOUBTFUL
        ]
        # An edge outside the read is listed, but alone it does not make a package.
        if not members or not (
            any(not f.out_of_scope for f in findings) or members & mapped.keys() or members & forced
        ):
            continue
        if findings_file is not None:
            for m in sorted(members & judged.keys()):
                placed.add(m)
                findings += [
                    Finding(
                        "compatibility",
                        m,
                        f.reason,
                        cls=f.cls,
                        rule=f.rule,
                        evidence=f.evidence.to_dict() if f.evidence else None,
                        sources=findings_file.sources.get(f.rule or "", ()),
                    )
                    for f in judged[m]
                    # The graph already carries the gap as an extraction_gap finding.
                    if not (m in gaps and (f.reason or "").startswith("extraction gap:"))
                ]
        attached |= members & gaps.keys()
        packaged |= in_group
        usage = package_usage((usages.get(m) for m in sorted(members)), inv.extracted_at)
        if not keeps(usage, in_use=in_use, exclude_unknown=exclude_unknown):
            filtered += 1
            continue
        package_id = f"pkg-{hashlib.sha256('\n'.join(sorted(members)).encode()).hexdigest()[:12]}"
        drafts.append((package_id, members, group, group_edges, findings, usage))

    drafts.sort(key=lambda d: (rank(d[5].state if d[5] else None) if sort == "usage" else 0, d[0]))
    where = {m: d[0] for d in drafts for m in d[1]}
    reports: list[ScenarioReport] = []
    tagged: dict[str, list[ExpectedBehavior]] = {}
    for name, seeds, behavior in read_scenarios:
        spans = tuple(sorted({where[s] for s in seeds if s in where}))
        reports.append(
            ScenarioReport(
                name,
                spans,
                tuple(s for s in seeds if s not in nodes),
                tuple(s for s in seeds if s in nodes and s not in where),
            )
        )
        for package_id in spans:
            tagged.setdefault(package_id, []).append(ExpectedBehavior(name, behavior, spans))

    packages: list[WorkPackage] = []
    for package_id, members, group, group_edges, findings, usage in drafts:
        source: list[SourceRef] = [
            SourceRef(c.kind, c.key, c.status, c.files, c.projects, c.reason)
            for c in (mapped[m] for m in sorted(members) if m in mapped)
        ]
        own = {p for s in source for p in s.projects}
        for node in group:
            path = node.removeprefix("project:")
            if node.startswith("project:") and path not in own:
                by = sorted(
                    e.source.removeprefix("project:")
                    for e in group_edges
                    if e.target == node and e.type == "project_reference"
                )
                source.append(
                    SourceRef(
                        "project", path, "referenced", reason=f"referenced by {', '.join(by)}"
                    )
                )
        behaviors = tagged.get(package_id, [])
        packages.append(
            WorkPackage(
                package_id,
                tuple(sorted(members)),
                tuple(group_edges),
                tuple(source) or (Unknown("no member is in the source map"),),
                tuple(_consumers(members, nodes, edges))
                or (Unknown("nothing in the inventory references a member"),),
                tuple(sorted(findings, key=Finding.sort_key))
                or (
                    Unknown(
                        "the findings file holds nothing but clear for the members"
                        if findings_file
                        else "no compatibility rule evaluated; no unknown found"
                    ),
                ),
                tuple(_expected(behaviors))
                or (Unknown("no scenario seeds a member of this package"),),
                tuple(_requirements(package_id, members, nodes, group_edges, refs.handlers))
                or (Unknown("no member kind states a verification requirement"),),
                tuple(b.scenario for b in behaviors),
                usage,
            )
        )
    header = {
        "manifest_sha256": manifest_sha,
        "platform": inv.source.platform,
        "org_url": inv.source.org_url,
        "version": inv.source.version,
        "extracted_at": inv.extracted_at,
    }
    unattached = tuple(
        g for g in inv.gaps if not (g.kind and g.key is not None and _id(g.kind, g.key) in attached)
    )
    # An unknown edge from a node no package holds (an entity, a system web resource).
    loose = [e for e in edges if e.target is None and e.source not in packaged]
    incomplete = {k: c.status for k, c in inv.coverage.items() if c.status != "complete"}
    unplaced = tuple(f for m, found in sorted(judged.items()) if m not in placed for f in found)
    return PackagesResult(
        header,
        tuple(packages),
        tuple(reports),
        unattached,
        tuple(e for e in loose if e.reason != OUT_OF_SCOPE),
        incomplete,
        findings_file.target if findings_file else None,
        unplaced,
        tuple(e for e in loose if e.reason == OUT_OF_SCOPE),
        filtered,
    )
