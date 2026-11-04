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
from crm.core.inventory_source_map import SourceMap
from crm.utils.safe_xml import fromstring

#: The format version of the index and package files.
SCHEMA_VERSION = 1
INDEX_NAME = "index.json"
#: The kinds a work package groups; every other component is context.
CONNECTING_KINDS = frozenset(
    ("web_resource", "plugin_assembly", "plugin_type", "step", "step_image", "process")
    + ("form", "ribbon", "sitemap")
)
#: Kinds that carry source; a system-origin one does not bridge two groups.
_SOURCE_KINDS = frozenset(("web_resource", "plugin_assembly", "plugin_type"))
_CONSUMER_KINDS = frozenset(("form", "ribbon", "sitemap", "process", "step"))
#: Source map statuses that leave a package's source in doubt.
_DOUBTFUL = frozenset(("stale", "missing_source", "ambiguous"))
#: Records that are edges or metadata, never graph nodes.
_NOT_NODES = frozenset(("dependency", "solution", "solution_membership"))

_WR_TOKEN = re.compile(r"\$webresource:([^\"'<>\s&?#]+)", re.IGNORECASE)
_WR_PATH = re.compile(r"[\"'][^\"'\s]*?/WebResources/([^\"'?#\s]+)[\"']", re.IGNORECASE)
_WR_DYNAMIC = re.compile(r"(?:\$webresource:|/WebResources/)[\"']\s*\+", re.IGNORECASE)
_OPEN_WR = re.compile(r"openWebResource\s*\(\s*([^,)]*)")
_LITERAL = re.compile(r"([\"'])([^\"']+)\1")
_HTML_REF = re.compile(r"\b(src|href)\s*=\s*[\"']([^\"']+)[\"']", re.IGNORECASE)
_URL_ATTR = re.compile(r"\b(?:Url|Address)\s*=\s*\"([^\"]*)\"", re.IGNORECASE)
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

    *type* is ``unknown_edge``, ``extraction_gap`` or ``source_status``; the
    compatibility rules of a later slice add their own types to the same list.
    """

    type: str
    component: str
    reason: str | None
    status: str | None = None
    reference: str | None = None

    def sort_key(self) -> tuple[str, ...]:
        return (self.component, self.type, self.reason or "", self.reference or "")

    def to_dict(self) -> dict[str, Any]:
        return {
            "type": self.type,
            "component": self.component,
            "status": self.status,
            "reason": self.reason,
            "reference": self.reference,
        }


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

    def unknowns(self) -> int:
        return sum(1 for f in self.findings if isinstance(f, Finding))

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
    incomplete: Mapping[str, str]

    def summary(self) -> dict[str, Any]:
        sizes = [len(p.members) for p in self.packages]
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
            "unattached_gaps": len(self.unattached_gaps),
            "incomplete": dict(self.incomplete),
        }

    def index(self) -> dict[str, Any]:
        """The summary plus a line per package and scenario, and the unattached gaps."""
        return {
            **self.summary(),
            "packages": [
                {
                    "id": p.id,
                    "file": f"{p.id}.json",
                    "members": len(p.members),
                    "unknowns": p.unknowns(),
                    "scenarios": list(p.scenarios),
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


# ── References read from the inventory's artifacts ─────────────────────────


@dataclass
class _Refs:
    """What one pass over the inventory found: edges, and each form's event handlers."""

    names: Mapping[str, str]  # lowercased web resource name -> its spelling
    org_host: str
    edges: set[Edge]
    handlers: dict[str, list[dict[str, str | None]]]

    def name(self, source: str, name: str, edge_type: str) -> None:
        """Add an edge to the web resource *name*, or an unknown one when none has that name."""
        spelled = self.names.get(name.lower())
        if spelled is None:
            self.edges.add(Edge(source, None, "unknown", "not_in_inventory", name))
        elif _id("web_resource", spelled) != source:
            self.edges.add(Edge(source, _id("web_resource", spelled), edge_type))

    def unknown(self, source: str, reason: str, reference: str) -> None:
        self.edges.add(Edge(source, None, "unknown", reason, reference))

    def url(self, source: str, value: str, edge_type: str) -> bool:
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
                self.name(source, _after_webresources(parts.path), edge_type)
            return True
        if parts.scheme:
            return True  # javascript:, mailto:, data:
        if "/webresources/" in "/" + path:
            self.name(source, _after_webresources("/" + parts.path), edge_type)
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
            if value and not refs.url(source, value, "form_control"):
                refs.name(source, value, "form_control")
    for library in libraries:
        if library:
            refs.name(source, library, "form_library")
    unique: list[dict[str, str | None]] = []
    for handler in handlers:
        if handler not in unique:
            unique.append(handler)
            if handler["library"]:
                refs.name(source, str(handler["library"]), "event_handler")
    refs.handlers[source] = unique


def _web_resource(refs: _Refs, source: str, key: str, text: str) -> None:
    for match in _OPEN_WR.finditer(text):
        arg = match.group(1).strip()
        literal = _LITERAL.fullmatch(arg)
        if literal:
            refs.name(source, literal.group(2), "open_web_resource")
        else:
            refs.unknown(source, "dynamic", arg)
    for match in _WR_DYNAMIC.finditer(text):
        refs.unknown(source, "dynamic", match.group(0))
    for name in _WR_PATH.findall(text):
        refs.name(source, name, "web_resource_reference")
    for match in _HTML_REF.finditer(text):
        ref = match.group(2).strip()
        if refs.url(source, ref, "web_resource_reference"):
            continue
        path = ref.split("#", 1)[0].split("?", 1)[0]
        if not path or path.startswith("/") or path.lower().endswith(".aspx"):
            continue  # an anchor, or a platform page such as ClientGlobalContext.js.aspx
        refs.name(
            source, posixpath.normpath(posixpath.join(posixpath.dirname(key), path)),
            "web_resource_reference",
        )  # fmt: skip


def _references(inv: Inventory, folder: Path) -> _Refs:
    names = {r.key.lower(): r.key for r in inv.records if r.kind == "web_resource"}
    refs = _Refs(names, (urlsplit(inv.source.org_url).hostname or "").lower(), set(), {})
    for record in inv.records:
        if record.kind not in ("form", "ribbon", "sitemap", "web_resource"):
            continue
        source = _id(record.kind, record.key)
        texts = _texts(folder, record)
        if record.kind == "form":
            _form(refs, source, record, texts)
        edge_type = {"ribbon": "ribbon_reference", "sitemap": "sitemap_reference"}.get(
            record.kind, "web_resource_reference"
        )
        for _, text in texts:
            for name in _WR_TOKEN.findall(text):
                refs.name(source, name, edge_type)
            if record.kind in ("ribbon", "sitemap"):
                for value in _URL_ATTR.findall(text):
                    refs.url(source, value, edge_type)
            elif record.kind == "web_resource":
                _web_resource(refs, source, record.key, text)
    return refs


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
            on = [h for h in handlers.get(member, []) if h["library"] in libraries]
            out.append(req("form_script", member, {"libraries": libraries, "handlers": on}))
        elif record.kind in ("ribbon", "sitemap") and (libraries or member in unknown):
            out.append(
                req(
                    "ribbon_command" if record.kind == "ribbon" else "sitemap_entry",
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
            "entity" if record.kind in ("form", "ribbon") else "primary_entity"
        )
        if isinstance(entity, str) and entity:
            entities.setdefault(_id("entity", entity), set()).add(member)
    for node, refs in entities.items():
        kind, key = node.split(":", 1)
        out.append(Consumer(kind, key, tuple(sorted(refs))))
    return sorted(out, key=lambda c: (c.kind, c.key))


def build_packages(
    inventory_dir: Path, source_map: SourceMap, scenarios: Path | None = None
) -> PackagesResult:
    """Build the dependency graph of the inventory at *inventory_dir* and group it into packages.

    *source_map* must have been made from this inventory (its ``manifest_sha256``
    matches); *scenarios*, when given, is a scenario file (see `_scenarios`).

    Raises:
        InventoryError: an input cannot be read, or the source map is of another inventory.
    """
    inv = read_inventory(inventory_dir)
    manifest_sha = hashlib.sha256((inventory_dir / MANIFEST_NAME).read_bytes()).hexdigest()
    if source_map.inventory.get("manifest_sha256") != manifest_sha:
        raise InventoryError(
            "the source map was made from another inventory (its manifest_sha256 differs);"
            " re-run `crm inventory map-source` on this one"
        )
    nodes: dict[str, Record] = {}
    for record in inv.records:
        if record.kind not in _NOT_NODES:
            nodes.setdefault(_id(record.kind, record.key), record)
    refs = _references(inv, inventory_dir)
    edges = sorted(
        refs.edges | _structural(inv, nodes) | _source_edges(source_map), key=Edge.sort_key
    )
    mapped = {_id(c.kind, c.key): c for c in source_map.components}
    gaps: dict[str, list[ExtractionGap]] = {}
    for gap in inv.gaps:
        if gap.kind and gap.key:
            gaps.setdefault(_id(gap.kind, gap.key), []).append(gap)
    read_scenarios = _scenarios(scenarios) if scenarios is not None else []

    drafts: list[tuple[str, set[str], list[str], list[Edge], list[Finding]]] = []
    attached: set[str] = set()
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
        if not members or not (findings or members & mapped.keys()):
            continue
        attached |= members & gaps.keys()
        package_id = f"pkg-{hashlib.sha256('\n'.join(sorted(members)).encode()).hexdigest()[:12]}"
        drafts.append((package_id, members, group, group_edges, findings))

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
    for package_id, members, group, group_edges, findings in sorted(drafts):
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
                or (Unknown("no compatibility rule evaluated; no unknown found"),),
                tuple(behaviors) or (Unknown("no scenario seeds a member of this package"),),
                tuple(_requirements(package_id, members, nodes, group_edges, refs.handlers))
                or (Unknown("no member kind states a verification requirement"),),
                tuple(b.scenario for b in behaviors),
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
        g for g in inv.gaps if not (g.kind and g.key and _id(g.kind, g.key) in attached)
    )
    incomplete = {k: c.status for k, c in inv.coverage.items() if c.status != "complete"}
    return PackagesResult(header, tuple(packages), tuple(reports), unattached, incomplete)
