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
from dataclasses import dataclass, field, replace
from pathlib import Path
from typing import Any
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

#: Folders never scanned: tool state, package caches and build output.
_SKIP_DIRS = frozenset(("node_modules", "bin", "obj", "packages"))
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
            "shared_projects": [
                {
                    "project": p.project,
                    "assembly": p.assembly,
                    "referenced_by": list(p.referenced_by),
                }
                for p in self.shared_projects
            ],
            "external": [
                {"kind": e.kind, "name": e.name, "version": e.version, "used_by": list(e.used_by)}
                for e in self.external
            ],
            "unmatched_source": [
                {"path": u.path, "kind": u.kind, "type_name": u.type_name}
                for u in self.unmatched_source
            ],
        }


def dump_source_map(source_map: SourceMap) -> str:
    """Serialize *source_map*: the same inventory and repository give the same bytes."""
    return json.dumps(source_map.to_dict(), sort_keys=True, ensure_ascii=False, indent=2) + "\n"


# ── Repository scan ─────────────────────────────────────────────────────────


@dataclass(frozen=True)
class _File:
    path: str  # repo-relative, posix
    sha256: str
    normalized_sha256: str
    minified: bool

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


@dataclass
class _Repo:
    files: list[_File] = field(default_factory=lambda: [])
    projects: dict[str, _Project] = field(default_factory=lambda: {})
    types: list[tuple[_TypeDecl, tuple[str, ...]]] = field(default_factory=lambda: [])


def _walk(root: Path) -> Iterable[str]:
    """Yield every regular file under *root* as a sorted, posix, root-relative path."""
    for current, dirs, files in os.walk(root):
        dirs[:] = sorted(d for d in dirs if not d.startswith(".") and d.lower() not in _SKIP_DIRS)
        base = Path(current)
        for name in sorted(files):
            path = base / name
            if not path.is_symlink():
                yield path.relative_to(root).as_posix()


def _is_minified(data: bytes) -> bool:
    return any(len(line) >= _MINIFIED_LINE for line in data.splitlines())


def _project(root: Path, rel: str) -> _Project:
    text = (root / rel).read_text(encoding="utf-8-sig", errors="replace")
    match = _ASSEMBLY_NAME.search(text)
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
        assembly=match.group(1) if match else posixpath.basename(rel)[: -len(".csproj")],
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
                out.append(_TypeDecl(".".join([*namespaces, full]), path, bases))
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
    by_folder: dict[str, list[str]] = {}
    for rel in repo.projects:
        by_folder.setdefault(posixpath.dirname(rel), []).append(rel)
    for rel in paths:
        ext = posixpath.splitext(rel)[1].lower()
        if ext == ".cs":
            owners: tuple[str, ...] = ()
            folder = posixpath.dirname(rel)
            while True:
                if folder in by_folder:
                    owners = tuple(by_folder[folder])
                    break
                if not folder:
                    break
                folder = posixpath.dirname(folder)
            text = (root / rel).read_text(encoding="utf-8-sig", errors="replace")
            repo.types.extend((decl, owners) for decl in _cs_types(text, rel))
        elif ext in _DEPLOYABLE or ext == ".ts":
            data = (root / rel).read_bytes()
            text = ext in _TEXT
            hashes = artifact_hashes(data, text=text)
            repo.files.append(
                _File(rel, hashes.sha256, hashes.normalized_sha256, text and _is_minified(data))
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
                for a, b in _FORM_LIBRARY.findall(text):
                    add(a or b, who)
            if record.kind != "web_resource":
                continue
            base = posixpath.dirname(record.key)
            for match in _HTML_REF.finditer(text):
                attr, ref = match.group(1), match.group(2)
                parts = urlsplit(ref)
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
        deployed_min = any(_is_minified(d) for d in data) or ".min." in key.lower()
        if deployed_min or any(f.minified for f in names):
            side = "the deployed content" if deployed_min else "the repository file"
            reason = f"minified: {side} is minified, so the hashes cannot agree; the name lines up"
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


def _same(a: str, b: str) -> bool:
    return a.casefold() == b.casefold()


def _assembly(record: Record, repo: _Repo) -> ComponentSource:
    name = str(record.fields["name"])
    projects = sorted(p.path for p in repo.projects.values() if _same(p.assembly, name))
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
    agree = sorted(
        {
            (d.path, o)
            for d, owners in decls
            for o in owners
            if _same(repo.projects[o].assembly, assembly)
        }
    )
    if len(agree) == 1:
        return ComponentSource(kind, key, "matched", (agree[0][0],), (agree[0][1],))
    if agree:
        return ComponentSource(
            kind,
            key,
            "ambiguous",
            tuple(sorted({p for p, _ in agree})),
            tuple(sorted({o for _, o in agree})),
            reason=f"{type_name} is declared in several projects that build {assembly}",
        )
    if decls:
        owners = sorted({o for _, os_ in decls for o in os_})
        built = sorted({repo.projects[o].assembly for o in owners})
        where = f"projects building {', '.join(built)}" if built else "no project"
        reason = f"{type_name} is declared in {where}, not {assembly}"
        return ComponentSource(
            kind, key, "candidate", tuple(sorted({d.path for d, _ in decls})), tuple(owners), reason
        )
    projects = sorted(p.path for p in repo.projects.values() if _same(p.assembly, assembly))
    if projects:
        reason = f"a project builds {assembly}, but no declaration of {type_name} was found"
        return ComponentSource(kind, key, "candidate", (), tuple(projects), reason)
    return ComponentSource(kind, key, "missing_source")


def map_source(inventory_dir: Path, repo: Path) -> SourceMap:
    """Match the custom components of the inventory at *inventory_dir* to the checkout at *repo*.

    Web resources: ``matched`` when a repository file's normalized hash (the
    inventory's normalization) equals the deployed one; else ``stale`` when one
    file lines up by name (its path ends with the web resource name, or with no
    such file, it shares the file name), ``ambiguous`` with several, and
    ``candidate`` when minified content keeps the hashes apart or the file looks
    generated from a TypeScript or un-minified source. Plug-in types: matched
    when a C# declaration of the full type name sits in a project whose
    assembly name (``AssemblyName``, else the project file name) is the
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
        if c.kind == "plugin_assembly" and c.status == "matched"
        for p in c.projects
    }
    referenced: dict[str, set[str]] = {}
    for path in plugin_projects:
        for ref in scanned.projects[path].references:
            referenced.setdefault(ref, set()).add(path)
    shared = tuple(
        SharedProject(
            ref,
            scanned.projects[ref].assembly if ref in scanned.projects else posixpath.basename(ref),
            tuple(sorted(users)),
        )
        for ref, users in sorted(referenced.items())
        if len(users) > 1
    )

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
    unmatched = [
        UnmatchedSource(f.path, "web_resource")
        for f in scanned.files
        if posixpath.splitext(f.lower)[1] in _DEPLOYABLE and f.path not in used
    ] + [
        UnmatchedSource(d.path, "plugin_type", d.name)
        for d, _ in scanned.types
        if _PLUGIN_BASES & set(d.bases) and d.path not in used
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
        header, incomplete, tuple(components), shared, tuple(external), tuple(unmatched)
    )
