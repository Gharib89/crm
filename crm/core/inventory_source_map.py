"""Map a migration inventory's components to an application repository (ADR 0032).

A **source map** relates each custom web resource, plug-in assembly and plug-in
type an inventory records to the files and projects of a local repository
checkout. It is evidence, not proof: a hash match says the bytes agree, a name
or declaration match only that they line up. The scan reads files and never
builds, runs or writes anything in the repository, and touches no org.
"""

from __future__ import annotations

import hashlib
import json
import os
import posixpath
import re
from collections.abc import Iterable, Mapping
from dataclasses import asdict, dataclass, field, replace
from pathlib import Path
from typing import Any, cast
from urllib.parse import urlsplit

from crm.core.inventory import (
    MANIFEST_NAME,
    Inventory,
    InventoryError,
    Record,
    artifact_hashes,
    read_inventory,
)

#: The source map's own format version. A reader refuses any other value,
#: whatever else the file holds, the inventory manifest's version rule.
SCHEMA_VERSION = 1
#: Every status, in report order.
STATUSES = ("matched", "stale", "ambiguous", "candidate", "missing_source")
#: The kinds a source map covers.
MAPPED_KINDS = ("web_resource", "plugin_assembly", "plugin_type")

#: Folders never scanned: package caches and build output. Dot folders are skipped too,
#: and NuGet's restore folder, a ``packages`` folder beside a ``.sln``.
_SKIP_DIRS = frozenset(("node_modules", "bin", "obj"))
#: Extensions of files a web resource can be deployed from.
_DEPLOYABLE = frozenset(
    (".htm", ".html", ".css", ".js", ".xml", ".xsl", ".xslt", ".svg", ".resx")
    + (".png", ".jpg", ".jpeg", ".gif", ".ico", ".xap")
)
_TEXT = frozenset((".htm", ".html", ".css", ".js", ".ts", ".xml", ".xsl", ".xslt", ".svg", ".resx"))
#: A text line this long marks minified or generated output.
_MINIFIED_LINE = 500
#: Base types a plug-in or custom workflow activity class derives from.
_PLUGIN_BASES = frozenset(("IPlugin", "CodeActivity"))
_BASIS = (
    "web resources matched by normalized content hash, else by name; plug-in types by C# "
    "declaration and project assembly name; a match is evidence, not proof of the same build"
)

_WEBRESOURCE_REF = re.compile(r"\$webresource:([^\"'<>\s&?#]+)", re.IGNORECASE)
_FORM_LIBRARY = re.compile(
    r"<Library\b[^>]*?\bname\s*=\s*\"([^\"]+)\"|\blibraryName\s*=\s*\"([^\"]+)\"", re.IGNORECASE
)
_HTML_REF = re.compile(r"\b(src|href)\s*=\s*[\"']([^\"']+)[\"']", re.IGNORECASE)
_ASSEMBLY_NAME = re.compile(r"<AssemblyName>\s*([^<\s]+)\s*</AssemblyName>", re.IGNORECASE)
_PROJECT_REF = re.compile(r"<ProjectReference\b[^>]*?\bInclude\s*=\s*\"([^\"]+)\"", re.IGNORECASE)
_PACKAGE_REF = re.compile(
    r"<PackageReference\b([^>]*?)(?:/>|>(.*?)</PackageReference>)", re.IGNORECASE | re.DOTALL
)
_PACKAGES_CONFIG = re.compile(r"<package\b([^>]*?)/?>", re.IGNORECASE)
_CS_NOISE = re.compile(
    r"//[^\n]*|/\*.*?\*/|(?:\$@|@\$|@)\"(?:[^\"]|\"\")*\"|\$?\"(?:[^\"\\\n]|\\.)*\""
    r"|'(?:[^'\\\n]|\\.)*'",
    re.DOTALL,
)
_CS_TOKEN = re.compile(r"@?[A-Za-z_][\w.]*|[{};:,<>]")
_CS_TYPE_KEYWORDS = frozenset(("class", "struct", "interface", "enum", "record"))
_CS_MODIFIERS = frozenset(
    (
        *("public", "internal", "private", "protected", "sealed", "static", "abstract"),
        *("partial", "unsafe", "new", "file", "readonly", "ref"),
    )
)


def _attr(attrs: str, name: str) -> str | None:
    match = re.search(rf"\b{name}\s*=\s*\"([^\"]*)\"", attrs, re.IGNORECASE)
    return match.group(1) if match else None


@dataclass(frozen=True)
class ComponentSource:
    """Where one component's source is: its status, the files and projects behind it, and why.

    *files* are repository-relative paths; *projects* the C# projects (``.csproj``)
    a plug-in assembly or type builds from. *consumers* names, for a web
    resource, each component that references it, as ``<kind>:<key>``.
    """

    kind: str
    key: str
    status: str
    files: tuple[str, ...] = ()
    projects: tuple[str, ...] = ()
    reason: str | None = None
    consumers: tuple[str, ...] = ()

    @property
    def shared(self) -> bool:
        """A web resource more than one component references."""
        return len(self.consumers) > 1

    def to_dict(self) -> dict[str, Any]:
        return {
            "kind": self.kind,
            "key": self.key,
            "status": self.status,
            "files": list(self.files),
            "projects": list(self.projects),
            "reason": self.reason,
            "consumers": list(self.consumers),
            "shared": self.shared,
        }


@dataclass(frozen=True)
class SharedProject:
    """A C# project that two or more plug-in projects reference: a shared library."""

    project: str
    assembly: str
    referenced_by: tuple[str, ...]


@dataclass(frozen=True)
class ExternalReference:
    """A dependency from outside the repository and the org.

    *kind* ``nuget`` is a package reference (*name*, *version*); ``script`` a web
    resource's script reference to another host (*name* is the URL).
    *used_by* lists the projects or web resources that reference it.
    """

    kind: str
    name: str
    version: str | None
    used_by: tuple[str, ...]


@dataclass(frozen=True)
class UnmatchedSource:
    """A repository file that looks deployable but no component maps to.

    *kind* is ``web_resource`` for a file a web resource can be deployed from, or
    ``plugin_type`` for a plug-in or workflow activity class, *type_name* its
    full name.
    """

    path: str
    kind: str
    type_name: str | None = None


@dataclass(frozen=True)
class SourceMap:
    """One inventory's components matched against one repository checkout."""

    inventory: Mapping[str, Any]
    incomplete: Mapping[str, str]
    components: tuple[ComponentSource, ...]
    shared_projects: tuple[SharedProject, ...]
    external: tuple[ExternalReference, ...]
    unmatched_source: tuple[UnmatchedSource, ...]
    schema_version: int = SCHEMA_VERSION
    #: Each plug-in project, and each project those reference in turn, mapped to the
    #: C# projects it references; a project with no reference is left out.
    project_references: Mapping[str, tuple[str, ...]] = field(
        default_factory=lambda: dict[str, tuple[str, ...]]()
    )

    def summary(self) -> dict[str, Any]:
        """Return the header, counts per kind and status, and the totals of the other lists."""
        counts: dict[str, dict[str, int]] = {}
        for c in self.components:
            per = counts.setdefault(c.kind, {})
            per[c.status] = per.get(c.status, 0) + 1
        return {
            "schema_version": self.schema_version,
            "inventory": dict(self.inventory),
            "basis": _BASIS,
            "incomplete": dict(self.incomplete),
            "counts": {
                kind: {s: counts[kind][s] for s in STATUSES if s in counts[kind]}
                for kind in MAPPED_KINDS
                if kind in counts
            },
            "shared": {
                "web_resources": sum(1 for c in self.components if c.shared),
                "projects": len(self.shared_projects),
            },
            "external": len(self.external),
            "unmatched_source": len(self.unmatched_source),
        }

    def to_dict(self) -> dict[str, Any]:
        """Return the summary plus every entry: the full source map."""
        return {
            **self.summary(),
            "components": [c.to_dict() for c in self.components],
            "shared_projects": [asdict(p) for p in self.shared_projects],
            "external": [asdict(e) for e in self.external],
            "unmatched_source": [asdict(u) for u in self.unmatched_source],
            "project_references": {p: list(r) for p, r in self.project_references.items()},
        }


def dump_source_map(source_map: SourceMap) -> str:
    """Serialize *source_map*: the same inventory and repository give the same bytes."""
    return json.dumps(source_map.to_dict(), sort_keys=True, ensure_ascii=False, indent=2) + "\n"


def _map_obj(value: object, where: str) -> dict[str, Any]:
    if not isinstance(value, dict):
        raise InventoryError(f"source map {where} must be an object")
    return cast("dict[str, Any]", value)


def _map_list(value: object, where: str) -> list[Any]:
    if not isinstance(value, list):
        raise InventoryError(f"source map {where} must be an array")
    return cast("list[Any]", value)


def _map_strings(value: object, where: str) -> tuple[str, ...]:
    items = _map_list(value, where)
    if not all(isinstance(item, str) for item in items):
        raise InventoryError(f"source map {where} must hold strings only")
    return tuple(cast("list[str]", items))


def _map_str(obj: dict[str, Any], name: str, where: str, *, optional: bool = False) -> str | None:
    value = obj.get(name)
    if (value is None and optional) or (isinstance(value, str) and value):
        return value
    raise InventoryError(f"source map {where}.{name} must be a non-empty string")


def parse_source_map(data: object) -> SourceMap:
    """Build a `SourceMap` from a decoded ``map-source --out`` file.

    The version rule runs first: a ``schema_version`` other than this module's
    is refused. A field this version does not define is ignored, and
    ``project_references``, which a map written before it existed lacks, reads
    as empty.

    Raises:
        InventoryError: the file breaks the source map's shape; the message names where.
    """
    obj = _map_obj(data, "file")
    version = obj.get("schema_version")
    if type(version) is not int or version != SCHEMA_VERSION:
        raise InventoryError(
            f"source map schema_version {version!r} is not supported; "
            f"this crm reads schema_version {SCHEMA_VERSION}"
        )
    components: list[ComponentSource] = []
    for i, item in enumerate(_map_list(obj.get("components"), "components")):
        where = f"components[{i}]"
        c = _map_obj(item, where)
        kind, status = _map_str(c, "kind", where), _map_str(c, "status", where)
        if kind not in MAPPED_KINDS or status not in STATUSES:
            raise InventoryError(f"source map {where} has kind {kind!r}, status {status!r}")
        components.append(
            ComponentSource(
                kind,
                str(_map_str(c, "key", where)),
                status,
                _map_strings(c.get("files", []), f"{where}.files"),
                _map_strings(c.get("projects", []), f"{where}.projects"),
                _map_str(c, "reason", where, optional=True),
                _map_strings(c.get("consumers", []), f"{where}.consumers"),
            )
        )
    shared = tuple(
        SharedProject(
            str(_map_str(p, "project", f"shared_projects[{i}]")),
            str(_map_str(p, "assembly", f"shared_projects[{i}]")),
            _map_strings(p.get("referenced_by", []), f"shared_projects[{i}].referenced_by"),
        )
        for i, p in (
            (i, _map_obj(v, f"shared_projects[{i}]"))
            for i, v in enumerate(_map_list(obj.get("shared_projects", []), "shared_projects"))
        )
    )
    external = tuple(
        ExternalReference(
            str(_map_str(e, "kind", f"external[{i}]")),
            str(_map_str(e, "name", f"external[{i}]")),
            _map_str(e, "version", f"external[{i}]", optional=True),
            _map_strings(e.get("used_by", []), f"external[{i}].used_by"),
        )
        for i, e in (
            (i, _map_obj(v, f"external[{i}]"))
            for i, v in enumerate(_map_list(obj.get("external", []), "external"))
        )
    )
    unmatched = tuple(
        UnmatchedSource(
            str(_map_str(u, "path", f"unmatched_source[{i}]")),
            str(_map_str(u, "kind", f"unmatched_source[{i}]")),
            _map_str(u, "type_name", f"unmatched_source[{i}]", optional=True),
        )
        for i, u in (
            (i, _map_obj(v, f"unmatched_source[{i}]"))
            for i, v in enumerate(_map_list(obj.get("unmatched_source", []), "unmatched_source"))
        )
    )
    references = _map_obj(obj.get("project_references", {}), "project_references")
    incomplete = _map_obj(obj.get("incomplete", {}), "incomplete")
    return SourceMap(
        _map_obj(obj.get("inventory"), "inventory"),
        {str(k): str(v) for k, v in incomplete.items()},
        tuple(components),
        shared,
        external,
        unmatched,
        project_references={
            str(p): _map_strings(r, f"project_references.{p}") for p, r in references.items()
        },
    )


def read_source_map(path: Path) -> SourceMap:
    """Read and check the source map file ``crm inventory map-source --out`` wrote."""
    try:
        data = json.loads(path.read_text(encoding="utf-8-sig"))
    except (OSError, ValueError) as exc:  # ValueError: malformed UTF-8 or JSON
        raise InventoryError(f"cannot read {path}: {exc}") from exc
    return parse_source_map(data)


# ── Repository scan ─────────────────────────────────────────────────────────


@dataclass(frozen=True)
class _File:
    path: str  # repo-relative, posix
    sha256: str
    normalized_sha256: str
    minified: bool
    #: An .xml or .resx file inside a C# project folder: a build input, never unmatched source.
    build_input: bool = False

    @property
    def lower(self) -> str:
        return self.path.lower()


@dataclass(frozen=True)
class _Project:
    path: str
    assembly: str
    references: tuple[str, ...]
    packages: tuple[tuple[str, str | None], ...]


@dataclass(frozen=True)
class _TypeDecl:
    name: str  # the CLR full name: Namespace.Outer+Inner
    path: str
    bases: tuple[str, ...]
    abstract: bool
    partial: bool


@dataclass
class _Repo:
    files: list[_File] = field(default_factory=lambda: [])
    projects: dict[str, _Project] = field(default_factory=lambda: {})
    types: list[tuple[_TypeDecl, tuple[str, ...]]] = field(default_factory=lambda: [])


def _walk(root: Path) -> Iterable[str]:
    """Yield every regular file under *root* as a sorted, posix, root-relative path."""
    for current, dirs, files in os.walk(root):
        solution = any(name.lower().endswith(".sln") for name in files)
        dirs[:] = sorted(
            d
            for d in dirs
            if not d.startswith(".")
            and d.lower() not in _SKIP_DIRS
            and not (solution and d.lower() == "packages")
        )
        base = Path(current)
        for name in sorted(files):
            path = base / name
            if not path.is_symlink():
                yield path.relative_to(root).as_posix()


def _is_minified(data: bytes) -> bool:
    return any(len(line) >= _MINIFIED_LINE for line in data.splitlines())


def _project_name(rel: str) -> str:
    """The project file name less ``.csproj``: what a project with no AssemblyName builds."""
    return posixpath.basename(rel)[: -len(".csproj")]


def _project(root: Path, rel: str) -> _Project:
    text = (root / rel).read_text(encoding="utf-8-sig", errors="replace")
    match = _ASSEMBLY_NAME.search(text)
    # $(MSBuildProjectName) is the one MSBuild property an AssemblyName commonly uses that
    # the project file alone resolves; any other property is compared as written.
    assembly = (
        re.sub(r"\$\(MSBuildProjectName\)", _project_name(rel), match.group(1), flags=re.IGNORECASE)
        if match
        else _project_name(rel)
    )
    folder = posixpath.dirname(rel)
    references = sorted(
        {
            posixpath.normpath(posixpath.join(folder, ref.replace("\\", "/")))
            for ref in _PROJECT_REF.findall(text)
        }
    )
    packages: set[tuple[str, str | None]] = set()
    for attrs, body in _PACKAGE_REF.findall(text):
        name = _attr(attrs, "Include")
        if name:
            version = _attr(attrs, "Version")
            if version is None:
                inner = re.search(r"<Version>\s*([^<\s]+)", body or "", re.IGNORECASE)
                version = inner.group(1) if inner else None
            packages.add((name, version))
    config = root / folder / "packages.config"
    if config.is_file():
        for attrs in _PACKAGES_CONFIG.findall(
            config.read_text(encoding="utf-8-sig", errors="replace")
        ):
            name = _attr(attrs, "id")
            if name:
                packages.add((name, _attr(attrs, "version")))
    return _Project(
        path=rel,
        assembly=assembly,
        references=tuple(references),
        packages=tuple(sorted(packages, key=lambda p: (p[0].lower(), p[0], p[1] or ""))),
    )


def _cs_types(text: str, path: str) -> list[_TypeDecl]:
    """Find the type declarations of one C# file, block- and file-scoped namespaces alike.

    A lexical scan, not a parse: comments and string literals are blanked, then
    braces are tracked to name each type by its namespace and enclosing types.
    """
    tokens = _CS_TOKEN.findall(_CS_NOISE.sub(" ", text))
    out: list[_TypeDecl] = []
    file_ns: list[str] = []
    stack: list[tuple[str, str] | None] = []  # ("ns" | "type", name), None for any other block
    pending: tuple[str, str] | None = None
    i = 0
    while i < len(tokens):
        tok = tokens[i]
        nxt = tokens[i + 1] if i + 1 < len(tokens) else ""
        if tok == "namespace" and nxt and nxt not in "{};:,<>":
            if i + 2 < len(tokens) and tokens[i + 2] == ";":
                file_ns = [nxt]
                i += 3
                continue
            pending = ("ns", nxt)
            i += 2
            continue
        if tok in _CS_TYPE_KEYWORDS:
            j = i + 1
            if tok == "record" and j < len(tokens) and tokens[j] in ("class", "struct"):
                j += 1
            name = tokens[j] if j < len(tokens) else ""
            if name and (name[0].isalpha() or name[0] in "_@") and name not in _CS_TYPE_KEYWORDS:
                bases, end = _header(tokens, j + 1)
                namespaces = file_ns + [s[1] for s in stack if s and s[0] == "ns"]
                types = [s[1] for s in stack if s and s[0] == "type"] + [name.lstrip("@")]
                full = "+".join(types)
                k = i
                while k > 0 and tokens[k - 1] in _CS_MODIFIERS:
                    k -= 1
                modifiers = set(tokens[k:i])
                # An interface is never registered, so it counts with abstract classes.
                abstract = tok == "interface" or "abstract" in modifiers
                decl = _TypeDecl(
                    ".".join([*namespaces, full]), path, bases, abstract, "partial" in modifiers
                )
                out.append(decl)
                if end < len(tokens) and tokens[end] == "{":
                    stack.append(("type", name.lstrip("@")))
                i = end + 1
                continue
        if tok == "{":
            stack.append(pending)
            pending = None
        elif tok == "}":
            if stack:
                stack.pop()
        elif tok == ";":
            pending = None
        i += 1
    return out


def _header(tokens: list[str], start: int) -> tuple[tuple[str, ...], int]:
    """Read a type header from *start* to its ``{`` or ``;``: the base type names and that index."""
    bases: list[str] = []
    depth = 0
    in_bases = False
    expect = False
    i = start
    while i < len(tokens) and tokens[i] not in ("{", ";"):
        tok = tokens[i]
        if tok == "<":
            depth += 1
        elif tok == ">":
            depth -= 1
        elif depth == 0 and tok == ":" and not in_bases:
            in_bases = expect = True
        elif depth == 0 and tok == ",":
            expect = in_bases
        elif depth == 0 and tok == "where":
            in_bases = expect = False
        elif expect and depth == 0:
            bases.append(tok.rsplit(".", 1)[-1])
            expect = False
        i += 1
    return tuple(bases), i


def _scan(root: Path) -> _Repo:
    repo = _Repo()
    paths = list(_walk(root))
    for rel in paths:
        if rel.lower().endswith(".csproj"):
            repo.projects[rel] = _project(root, rel)
    # A ProjectReference spelled in another letter case names the same file on Windows:
    # the checkout's own spelling wins, else the first spelling in sort order.
    spelled: dict[str, str] = {}
    for ref in sorted(r for p in repo.projects.values() for r in p.references):
        spelled.setdefault(ref.lower(), ref)
    spelled.update({rel.lower(): rel for rel in repo.projects})
    for rel, project in repo.projects.items():
        refs = sorted({spelled.get(ref.lower(), ref) for ref in project.references})
        repo.projects[rel] = replace(project, references=tuple(refs))
    by_folder: dict[str, list[str]] = {}
    for rel in repo.projects:
        by_folder.setdefault(posixpath.dirname(rel), []).append(rel)

    def owners_of(rel: str) -> tuple[str, ...]:
        folder = posixpath.dirname(rel)
        while folder not in by_folder:
            if not folder:
                return ()
            folder = posixpath.dirname(folder)
        return tuple(by_folder[folder])

    for rel in paths:
        ext = posixpath.splitext(rel)[1].lower()
        if ext == ".cs":
            text = (root / rel).read_text(encoding="utf-8-sig", errors="replace")
            owners = owners_of(rel)
            repo.types.extend((decl, owners) for decl in _cs_types(text, rel))
        elif ext in _DEPLOYABLE or ext == ".ts":
            data = (root / rel).read_bytes()
            text = ext in _TEXT
            hashes = artifact_hashes(data, text=text)
            repo.files.append(
                _File(
                    rel,
                    hashes.sha256,
                    hashes.normalized_sha256,
                    text and _is_minified(data),
                    ext in (".xml", ".resx") and bool(owners_of(rel)),
                )
            )
    return repo


# ── Inventory side ──────────────────────────────────────────────────────────


def _artifact_bytes(folder: Path, record: Record) -> list[bytes]:
    out: list[bytes] = []
    for artifact in record.artifacts:
        if artifact.content != "text":
            continue
        path = folder / artifact.path
        try:
            out.append(path.read_bytes())
        except OSError as exc:
            raise InventoryError(f"cannot read artifact {artifact.path}: {exc}") from exc
    return out


def _consumers(
    inv: Inventory, folder: Path, names: Mapping[str, str]
) -> tuple[dict[str, set[str]], dict[str, set[str]]]:
    """Return, per web resource name, its consumers; and per external script URL, its users.

    *names* maps each recorded web resource name, lowercased, to its spelling.
    """
    consumers: dict[str, set[str]] = {}
    scripts: dict[str, set[str]] = {}
    org_host = (urlsplit(inv.source.org_url).hostname or "").lower()

    def add(name: str, who: str, self_key: str | None = None) -> None:
        spelled = names.get(name.lower())
        if spelled is not None and spelled != self_key:
            consumers.setdefault(spelled, set()).add(who)

    for record in inv.records:
        if record.kind not in ("form", "ribbon", "sitemap", "web_resource"):
            continue
        who = f"{record.kind}:{record.key}"
        self_key = record.key if record.kind == "web_resource" else None
        if record.kind == "form":
            libraries: object = record.fields.get("libraries")
            if isinstance(libraries, list):
                for lib in libraries:  # pyright: ignore[reportUnknownVariableType]
                    if isinstance(lib, str):
                        add(lib, who)
        for data in _artifact_bytes(folder, record):
            text = data.decode("utf-8", errors="replace")
            for name in _WEBRESOURCE_REF.findall(text):
                add(name, who, self_key)
            if record.kind == "form":
                for library, handler in _FORM_LIBRARY.findall(text):
                    add(library or handler, who)
            if record.kind != "web_resource":
                continue
            base = posixpath.dirname(record.key)
            for match in _HTML_REF.finditer(text):
                attr, ref = match.group(1), match.group(2)
                try:
                    parts = urlsplit(ref)
                except ValueError:  # a malformed URL (an unbalanced IPv6 bracket) names nothing
                    continue
                if parts.scheme in ("http", "https") or ref.startswith("//"):
                    host = (parts.hostname or "").lower()
                    if attr.lower() == "src" and host and host != org_host:
                        scripts.setdefault(ref, set()).add(record.key)
                    continue
                if parts.scheme:
                    continue
                target = parts.path
                lowered = target.lower()
                if "/webresources/" in lowered:
                    target = target[lowered.index("/webresources/") + len("/webresources/") :]
                else:
                    target = posixpath.normpath(posixpath.join(base, target))
                add(target, who, self_key)
    return consumers, scripts


# ── Matching ────────────────────────────────────────────────────────────────


def _name_hits(name: str, files: list[_File]) -> list[_File]:
    """Files whose path ends with the web resource name's path, else share its file name."""
    parts = name.lower().split("/")
    deployable = [f for f in files if posixpath.splitext(f.lower)[1] in _DEPLOYABLE]
    if len(parts) > 1:
        suffix = [f for f in deployable if f.lower.split("/")[-len(parts) :] == parts]
        if suffix:
            return suffix
    return [f for f in deployable if posixpath.basename(f.lower) == parts[-1]]


def _derived_hits(name: str, files: list[_File]) -> list[_File]:
    """Files a build may have generated the web resource from: un-minified or TypeScript names."""
    base = posixpath.basename(name.lower())
    stem, ext = posixpath.splitext(base)
    sources = {f"{stem[: -len('.min')]}{ext}"} if stem.endswith(".min") else set[str]()
    if ext == ".js":
        sources |= {f"{s[: -len('.js')]}.ts" for s in sources | {base}}
    sources.discard(base)
    return [f for f in files if posixpath.basename(f.lower) in sources]


def _paths(files: Iterable[_File]) -> tuple[str, ...]:
    return tuple(sorted(f.path for f in files))


def _web_resource(record: Record, data: list[bytes], files: list[_File]) -> ComponentSource:
    kind, key = record.kind, record.key
    artifact = record.artifacts[0] if record.artifacts else None
    names = _name_hits(key, files)
    if artifact is not None:
        text = artifact.content == "text"
        hits = [
            f
            for f in files
            if (f.normalized_sha256 if text else f.sha256) == artifact.normalized_sha256
        ]
        if len(hits) == 1:
            return ComponentSource(kind, key, "matched", _paths(hits))
        if hits:
            named = [f for f in hits if f in names]
            if len(named) == 1:
                return ComponentSource(kind, key, "matched", _paths(named))
            return ComponentSource(
                kind,
                key,
                "ambiguous",
                _paths(hits),
                reason="several files hold the deployed content",
            )
    if names:
        if artifact is None:
            reason = "no deployed content was recorded, so only the name lines up"
            return ComponentSource(kind, key, "candidate", _paths(names), reason=reason)
        # Minified on one side only: a build output against its source, so the hashes
        # cannot agree. Minified on both sides is two builds, compared as usual.
        deployed_min = any(_is_minified(d) for d in data) or ".min." in key.lower()
        repo_min = any(f.minified or ".min." in f.lower for f in names)
        if deployed_min != repo_min:
            side = "the deployed content" if deployed_min else "the repository file"
            reason = f"minified: only {side} is minified, so the hashes cannot agree"
            return ComponentSource(kind, key, "candidate", _paths(names), reason=reason)
        if len(names) == 1:
            reason = "the name lines up but the content differs; which side is newer is unknown"
            return ComponentSource(kind, key, "stale", _paths(names), reason=reason)
        reason = "several files line up by name and none holds the deployed content"
        return ComponentSource(kind, key, "ambiguous", _paths(names), reason=reason)
    derived = _derived_hits(key, files)
    if derived:
        reason = "generated: the deployed file may be built from these sources"
        return ComponentSource(kind, key, "candidate", _paths(derived), reason=reason)
    return ComponentSource(kind, key, "missing_source")


def _same_assembly(a: str, b: str) -> bool:
    """Assembly names compare case-insensitively, as .NET loads them."""
    return a.casefold() == b.casefold()


def _assembly(record: Record, repo: _Repo) -> ComponentSource:
    name = str(record.fields["name"])
    projects = sorted(p.path for p in repo.projects.values() if _same_assembly(p.assembly, name))
    if len(projects) == 1:
        return ComponentSource(record.kind, record.key, "matched", projects=tuple(projects))
    if projects:
        reason = f"several projects build {name}"
        return ComponentSource(
            record.kind, record.key, "ambiguous", projects=tuple(projects), reason=reason
        )
    return ComponentSource(record.kind, record.key, "missing_source")


def _plugin_type(record: Record, repo: _Repo) -> ComponentSource:
    kind, key = record.kind, record.key
    assembly, type_name = str(record.fields["assembly"]), str(record.fields["type_name"])
    decls = [(d, owners) for d, owners in repo.types if d.name == type_name]
    agree = [
        (d, o)
        for d, owners in decls
        for o in owners
        if _same_assembly(repo.projects[o].assembly, assembly)
    ]
    files = tuple(sorted({d.path for d, _ in agree}))
    projects = tuple(sorted({o for _, o in agree}))
    # A partial class spans several files of one project; full copies in two files do not.
    if len(projects) == 1 and (len(files) == 1 or all(d.partial for d, _ in agree)):
        return ComponentSource(kind, key, "matched", files, projects)
    if projects:
        where = (
            "several projects that build"
            if len(projects) > 1
            else "several files of a project building"
        )
        reason = f"{type_name} is declared in {where} {assembly}"
        return ComponentSource(kind, key, "ambiguous", files, projects, reason=reason)
    if decls:
        owners = sorted({o for _, decl_owners in decls for o in decl_owners})
        built = sorted({repo.projects[o].assembly for o in owners})
        where = f"projects building {', '.join(built)}" if built else "no project"
        reason = f"{type_name} is declared in {where}, not {assembly}"
        return ComponentSource(
            kind, key, "candidate", tuple(sorted({d.path for d, _ in decls})), tuple(owners), reason
        )
    builders = sorted(
        p.path for p in repo.projects.values() if _same_assembly(p.assembly, assembly)
    )
    if builders:
        reason = f"a project builds {assembly}, but no declaration of {type_name} was found"
        return ComponentSource(kind, key, "candidate", (), tuple(builders), reason)
    return ComponentSource(kind, key, "missing_source")


def map_source(inventory_dir: Path, repo: Path) -> SourceMap:
    """Match the custom components of the inventory at *inventory_dir* to the checkout at *repo*.

    Web resources: ``matched`` when a repository file's normalized hash (the
    inventory's normalization) equals the deployed one; else ``stale`` when one
    file lines up by name (its path ends with the web resource name, or with no
    such file, it shares the file name), ``ambiguous`` with several, and
    ``candidate`` when only one side is minified or the file looks generated
    from a TypeScript or un-minified source. Plug-in types: matched
    when a C# declaration of the full type name sits in a project whose
    assembly name (``AssemblyName`` with ``$(MSBuildProjectName)`` expanded,
    else the project file name) is the
    registered one; a declaration or a project alone is a ``candidate``. No
    candidate at all is ``missing_source``. System components are not mapped.

    Raises:
        InventoryError: the inventory cannot be read, or an artifact it names is missing.
    """
    inv = read_inventory(inventory_dir)
    manifest_sha = hashlib.sha256((inventory_dir / MANIFEST_NAME).read_bytes()).hexdigest()
    scanned = _scan(repo)
    names = {r.key.lower(): r.key for r in inv.records if r.kind == "web_resource"}
    consumers, scripts = _consumers(inv, inventory_dir, names)

    components: list[ComponentSource] = []
    for record in inv.records:
        if record.kind not in MAPPED_KINDS or record.origin == "system":
            continue
        if record.kind == "web_resource":
            entry = replace(
                _web_resource(record, _artifact_bytes(inventory_dir, record), scanned.files),
                consumers=tuple(sorted(consumers.get(record.key, ()))),
            )
        elif record.kind == "plugin_assembly":
            entry = _assembly(record, scanned)
        else:
            entry = _plugin_type(record, scanned)
        components.append(entry)
    components.sort(key=lambda c: (MAPPED_KINDS.index(c.kind), c.key, json.dumps(c.to_dict())))

    plugin_projects = {
        p
        for c in components
        if c.kind == "plugin_assembly" and c.status in ("matched", "ambiguous")
        for p in c.projects
    }
    referenced: dict[str, set[str]] = {}
    for path in plugin_projects:
        for ref in scanned.projects[path].references:
            referenced.setdefault(ref, set()).add(path)
    shared = tuple(
        SharedProject(
            ref,
            scanned.projects[ref].assembly if ref in scanned.projects else _project_name(ref),
            tuple(sorted(users)),
        )
        for ref, users in sorted(referenced.items())
        if len(users) > 1
    )
    project_references: dict[str, tuple[str, ...]] = {}
    pending = sorted(plugin_projects)
    while pending:
        path = pending.pop()
        if path in project_references or path not in scanned.projects:
            continue
        refs = scanned.projects[path].references
        if refs:
            project_references[path] = refs
            pending.extend(refs)

    packages: dict[tuple[str, str | None], set[str]] = {}
    for project in scanned.projects.values():
        for package in project.packages:
            packages.setdefault(package, set()).add(project.path)
    external = [
        ExternalReference("nuget", n, v, tuple(sorted(users)))
        for (n, v), users in sorted(
            packages.items(), key=lambda p: (p[0][0].lower(), p[0][0], p[0][1] or "")
        )
    ] + [
        ExternalReference("script", url, None, tuple(sorted(users)))
        for url, users in sorted(scripts.items())
    ]

    used = {f for c in components for f in c.files}
    # A class deriving from IPlugin or CodeActivity, directly or through the repo's own
    # base classes (a PluginBase template), by simple name.
    plugin_bases = set(_PLUGIN_BASES)
    while True:
        derived = {
            d.name.rsplit(".", 1)[-1].rsplit("+", 1)[-1]
            for d, _ in scanned.types
            if plugin_bases & set(d.bases)
        }
        if derived <= plugin_bases:
            break
        plugin_bases |= derived
    registered = {str(r.fields["type_name"]) for r in inv.records if r.kind == "plugin_type"}
    unmatched = [
        UnmatchedSource(f.path, "web_resource")
        for f in scanned.files
        if posixpath.splitext(f.lower)[1] in _DEPLOYABLE
        and not f.build_input
        and f.path not in used
    ] + [
        UnmatchedSource(d.path, "plugin_type", d.name)
        for d, _ in scanned.types
        if plugin_bases & set(d.bases) and not d.abstract and d.name not in registered
    ]
    unmatched.sort(key=lambda u: (u.path, u.kind, u.type_name or ""))

    incomplete = {
        kind: (inv.coverage[kind].status if kind in inv.coverage else "absent")
        for kind in MAPPED_KINDS
        if kind not in inv.coverage or inv.coverage[kind].status != "complete"
    }
    header = {
        "manifest_sha256": manifest_sha,
        "platform": inv.source.platform,
        "org_url": inv.source.org_url,
        "version": inv.source.version,
        "extracted_at": inv.extracted_at,
    }
    return SourceMap(
        header,
        incomplete,
        tuple(components),
        shared,
        tuple(external),
        tuple(unmatched),
        project_references=dict(sorted(project_references.items())),
    )
