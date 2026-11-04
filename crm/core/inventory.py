"""Migration inventory manifest v1: the contract every extractor emits (ADR 0032).

A migration inventory is a folder holding ``manifest.json`` (the inventory
manifest) and ``artifacts/<kind>/<file>``. This module owns the format both the
CRM 2011 path and the v9/cloud paths write: the component key per kind, artifact
paths and hashes, the typed records, a deterministic writer and a reader that
applies the version rule. The JSON Schema of the same format ships at
``crm/schemas/inventory-manifest-v1.schema.json``; the reader validates
structure without it, so no runtime code depends on ``jsonschema``.
"""

from __future__ import annotations

import hashlib
import json
import re
from collections.abc import Mapping
from dataclasses import asdict, dataclass
from pathlib import Path
from typing import Any, cast
from urllib.parse import urlsplit

_BOM = b"\xef\xbb\xbf"

#: The kinds of v1 and, per kind, the fields its component key joins, in order.
#: A field holding another component's key (a step's ``plugin_type``, a
#: membership's ``member_key``) nests that key as one escaped part.
KEY_FIELDS: dict[str, tuple[str, ...]] = {
    "entity": ("logical_name",),
    "attribute": ("entity", "logical_name"),
    "relationship": ("schema_name",),
    "global_option_set": ("name",),
    "local_option_set": ("entity", "attribute"),
    "form": ("entity", "form_type", "name"),
    "view": ("entity", "name"),
    "web_resource": ("name",),
    "plugin_assembly": ("name",),
    "plugin_type": ("assembly", "type_name"),
    "step": ("message", "primary_entity", "stage", "mode", "plugin_type", "name"),
    "step_image": ("step", "alias", "image_type"),
    "process": ("category", "primary_entity", "name"),
    "solution": ("unique_name",),
    "solution_membership": ("solution", "member_kind", "member_key"),
    "dependency": (
        "dependent_kind",
        "dependent_key",
        "required_kind",
        "required_key",
        "dependency_type",
    ),
    "ribbon": ("entity",),
    "sitemap": ("unique_name",),
}
KINDS: tuple[str, ...] = tuple(KEY_FIELDS)

#: The key a null single-part key takes: the application ribbon, the default sitemap.
_NULL_KEY: dict[str, str] = {"ribbon": "application", "sitemap": "default"}


class InventoryError(Exception):
    """A manifest, record or key that breaks the inventory manifest contract."""


def _key_part(value: object) -> str:
    if value is None:
        return ""
    if isinstance(value, bool) or not isinstance(value, str | int):
        raise InventoryError(f"key part must be a string, an integer or null, got {value!r}")
    return str(value).replace("%", "%25").replace("|", "%7C")


def component_key(kind: str, fields: Mapping[str, Any]) -> str:
    """Build a record's component key from its normalized fields.

    The key joins the kind's `KEY_FIELDS` with ``|``, each part escaping ``%``
    and ``|`` (as ``%25`` and ``%7C``) so two different field tuples never give
    one key. A null part is the empty string; a null ribbon entity is
    ``application`` and a null sitemap name ``default``.

    Raises:
        InventoryError: the kind is unknown or a key field is missing.
    """
    names = KEY_FIELDS.get(kind)
    if names is None:
        raise InventoryError(f"unknown kind {kind!r}; v1 kinds: {', '.join(KINDS)}")
    missing = [name for name in names if name not in fields]
    if missing:
        raise InventoryError(f"{kind} record lacks key field(s): {', '.join(missing)}")
    if kind in _NULL_KEY and fields[names[0]] is None:
        return _NULL_KEY[kind]
    return "|".join(_key_part(fields[name]) for name in names)


def artifact_path(kind: str, key: str, name: str, *, discriminator: str | None = None) -> str:
    """Return the manifest-relative path of one record's artifact.

    The file is ``artifacts/<kind>/<slug>-<hash>.<name>``: a readable slug of
    the key plus the first 12 hex digits of a SHA-256 over the kind and full
    key, so two keys never share a file even when they slug alike. Records
    sharing an ambiguous key pass a *discriminator* (their source id) so each
    keeps its own file. *name* names the artifact within the record
    (``form.xml``, ``fetch.xml``, ``content.js``).
    """
    slug = re.sub(r"[^a-z0-9._]+", "-", key.lower()).strip("-.")[:60] or "component"
    digest = hashlib.sha256("\0".join((kind, key, discriminator or "")).encode("utf-8")).hexdigest()
    return f"artifacts/{kind}/{slug}-{digest[:12]}.{name}"


@dataclass(frozen=True)
class ArtifactHashes:
    """The raw and the normalized SHA-256 of one artifact's bytes."""

    sha256: str
    normalized_sha256: str


def normalize_text(data: bytes) -> bytes:
    """Apply the v1 text normalization: strip a leading UTF-8 BOM, convert CRLF to LF."""
    if data.startswith(_BOM):
        data = data[len(_BOM) :]
    return data.replace(b"\r\n", b"\n")


def artifact_hashes(data: bytes, *, text: bool) -> ArtifactHashes:
    """Hash an artifact's raw bytes; a text artifact's normalized hash follows `normalize_text`.

    A binary artifact uses the raw hash for both, since v1 defines no binary
    normalization.
    """
    raw = hashlib.sha256(data).hexdigest()
    normalized = hashlib.sha256(normalize_text(data)).hexdigest() if text else raw
    return ArtifactHashes(sha256=raw, normalized_sha256=normalized)


SCHEMA_VERSION = 1
MANIFEST_NAME = "manifest.json"

PLATFORMS = ("crm2011", "onprem", "cloud")
ORIGINS = ("custom", "system", "unknown")
PUBLICATION_STATES = ("published", "unknown")
COVERAGE_STATUSES = ("complete", "partial", "unsupported", "failed")
CONTENTS = ("text", "binary")
_SHA256 = re.compile(r"[0-9a-f]{64}")


@dataclass(frozen=True)
class Source:
    """The org an inventory was read from; *org_url* never carries credentials."""

    platform: str
    org_url: str
    organization_id: str | None
    version: str


@dataclass(frozen=True)
class Scope:
    """What the extraction read: the whole org (``org``) or the named ``solutions``."""

    mode: str
    solutions: tuple[str, ...]


@dataclass(frozen=True)
class Artifact:
    path: str
    content: str
    sha256: str
    normalized_sha256: str


@dataclass(frozen=True)
class Record:
    """One component. *key* decides matching; *id* (the source GUID) never does."""

    kind: str
    key: str
    id: str | None
    display_name: str | None
    origin: str
    managed: bool | None
    publication_state: str
    fields: Mapping[str, Any]
    artifacts: tuple[Artifact, ...]


@dataclass(frozen=True)
class Coverage:
    status: str
    pages: int
    records: int
    truncated: bool
    errors: tuple[str, ...]


@dataclass(frozen=True)
class Gap:
    """Something the source cannot expose, named by *key* or *description*."""

    kind: str | None
    key: str | None
    description: str | None
    reason: str


@dataclass(frozen=True)
class Inventory:
    schema_version: int
    source: Source
    extracted_at: str
    cli_version: str
    scope: Scope
    publication: str
    records: tuple[Record, ...]
    coverage: Mapping[str, Coverage]
    gaps: tuple[Gap, ...]

    def ambiguous(self) -> dict[tuple[str, str], tuple[Record, ...]]:
        """Return each (kind, key) that two or more records share, records in manifest order."""
        groups: dict[tuple[str, str], list[Record]] = {}
        for record in self.records:
            groups.setdefault((record.kind, record.key), []).append(record)
        return {k: tuple(v) for k, v in groups.items() if len(v) > 1}


# ── Reader ──────────────────────────────────────────────────────────────────


def _obj(value: object, where: str) -> dict[str, Any]:
    if not isinstance(value, dict):
        raise InventoryError(f"{where} must be an object")
    return cast("dict[str, Any]", value)


def _list(value: object, where: str) -> list[Any]:
    if not isinstance(value, list):
        raise InventoryError(f"{where} must be an array")
    return cast("list[Any]", value)


def _req(obj: dict[str, Any], name: str, where: str) -> object:
    if name not in obj:
        raise InventoryError(f"{where} lacks required field {name!r}")
    return obj[name]


def _str(obj: dict[str, Any], name: str, where: str, *, choices: tuple[str, ...] = ()) -> str:
    value = _req(obj, name, where)
    if not isinstance(value, str) or not value:
        raise InventoryError(f"{where}.{name} must be a non-empty string")
    if choices and value not in choices:
        raise InventoryError(f"{where}.{name} is {value!r}; expected one of {', '.join(choices)}")
    return value


def _opt_str(obj: dict[str, Any], name: str, where: str) -> str | None:
    value = _req(obj, name, where)
    if value is not None and not isinstance(value, str):
        raise InventoryError(f"{where}.{name} must be a string or null")
    return value


def _int(obj: dict[str, Any], name: str, where: str) -> int:
    value = _req(obj, name, where)
    if isinstance(value, bool) or not isinstance(value, int) or value < 0:
        raise InventoryError(f"{where}.{name} must be a non-negative integer")
    return value


def _bool(obj: dict[str, Any], name: str, where: str, *, nullable: bool = False) -> bool | None:
    value = _req(obj, name, where)
    if not isinstance(value, bool) and not (nullable and value is None):
        raise InventoryError(f"{where}.{name} must be a boolean{' or null' if nullable else ''}")
    return value


def _strings(value: object, where: str) -> tuple[str, ...]:
    items = _list(value, where)
    if not all(isinstance(item, str) for item in items):
        raise InventoryError(f"{where} must hold strings only")
    return tuple(cast("list[str]", items))


def _source(value: object) -> Source:
    obj = _obj(value, "source")
    url = _str(obj, "org_url", "source")
    if urlsplit(url).username is not None or "@" in urlsplit(url).netloc:
        raise InventoryError("source.org_url must not carry credentials")
    return Source(
        platform=_str(obj, "platform", "source", choices=PLATFORMS),
        org_url=url,
        organization_id=_opt_str(obj, "organization_id", "source"),
        version=_str(obj, "version", "source"),
    )


def _scope(value: object) -> Scope:
    obj = _obj(value, "scope")
    mode = _str(obj, "mode", "scope", choices=("org", "solutions"))
    solutions = _strings(_req(obj, "solutions", "scope"), "scope.solutions")
    if (mode == "solutions") != bool(solutions):
        raise InventoryError(
            "scope.solutions must name solutions exactly when scope.mode is 'solutions'"
        )
    return Scope(mode=mode, solutions=solutions)


def _artifact(value: object, kind: str, where: str) -> Artifact:
    obj = _obj(value, where)
    path = _str(obj, "path", where)
    prefix = f"artifacts/{kind}/"
    if (
        not path.startswith(prefix)
        or "/" in path[len(prefix) :]
        or path.endswith("/")
        or ".." in path
    ):
        raise InventoryError(f"{where}.path {path!r} must be a file directly under {prefix}")
    hashes = [_str(obj, name, where) for name in ("sha256", "normalized_sha256")]
    if not all(_SHA256.fullmatch(h) for h in hashes):
        raise InventoryError(f"{where} hashes must be 64 lowercase hex digits")
    return Artifact(
        path=path,
        content=_str(obj, "content", where, choices=CONTENTS),
        sha256=hashes[0],
        normalized_sha256=hashes[1],
    )


def _record(value: object, where: str) -> Record:
    obj = _obj(value, where)
    kind = _str(obj, "kind", where, choices=KINDS)
    key = _str(obj, "key", where)
    fields = _obj(_req(obj, "fields", where), f"{where}.fields")
    expected = component_key(kind, fields)
    if key != expected:
        raise InventoryError(
            f"{where} key {key!r} does not match its fields' component key {expected!r}"
        )
    artifacts = _list(_req(obj, "artifacts", where), f"{where}.artifacts")
    return Record(
        kind=kind,
        key=key,
        id=_opt_str(obj, "id", where),
        display_name=_opt_str(obj, "display_name", where),
        origin=_str(obj, "origin", where, choices=ORIGINS),
        managed=_bool(obj, "managed", where, nullable=True),
        publication_state=_str(obj, "publication_state", where, choices=PUBLICATION_STATES),
        fields=fields,
        artifacts=tuple(
            _artifact(a, kind, f"{where}.artifacts[{i}]") for i, a in enumerate(artifacts)
        ),
    )


def _coverage(value: object, kind: str) -> Coverage:
    where = f"coverage.{kind}"
    obj = _obj(value, where)
    coverage = Coverage(
        status=_str(obj, "status", where, choices=COVERAGE_STATUSES),
        pages=_int(obj, "pages", where),
        records=_int(obj, "records", where),
        truncated=bool(_bool(obj, "truncated", where)),
        errors=_strings(_req(obj, "errors", where), f"{where}.errors"),
    )
    if coverage.status == "complete" and coverage.truncated:
        raise InventoryError(f"{where} is complete but truncated: a truncated read is partial")
    return coverage


def _gap(value: object, where: str) -> Gap:
    obj = _obj(value, where)
    kind = _opt_str(obj, "kind", where)
    if kind is not None and kind not in KINDS:
        raise InventoryError(f"{where}.kind {kind!r} is not a v1 kind")
    gap = Gap(
        kind=kind,
        key=_opt_str(obj, "key", where),
        description=_opt_str(obj, "description", where),
        reason=_str(obj, "reason", where),
    )
    if not gap.key and not gap.description:
        raise InventoryError(f"{where} must name a key or a description")
    return gap


def parse_manifest(data: object) -> Inventory:
    """Build an `Inventory` from a decoded manifest, applying the v1 contract.

    The version rule runs first: an unknown ``schema_version`` is refused
    whatever else the manifest holds. Fields v1 does not define are ignored.

    Raises:
        InventoryError: the manifest breaks the contract; the message names where.
    """
    obj = _obj(data, "manifest")
    version = obj.get("schema_version")
    if type(version) is not int or version != SCHEMA_VERSION:
        raise InventoryError(
            f"manifest schema_version {version!r} is not supported; "
            f"this crm reads schema_version {SCHEMA_VERSION}"
        )
    records = tuple(
        _record(r, f"records[{i}]")
        for i, r in enumerate(_list(_req(obj, "records", "manifest"), "records"))
    )
    raw_coverage = _obj(_req(obj, "coverage", "manifest"), "coverage")
    unknown = sorted(set(raw_coverage) - set(KINDS))
    if unknown:
        raise InventoryError(f"coverage names unknown kind(s): {', '.join(unknown)}")
    coverage = {kind: _coverage(raw_coverage[kind], kind) for kind in sorted(raw_coverage)}
    counts: dict[str, int] = {}
    for record in records:
        counts[record.kind] = counts.get(record.kind, 0) + 1
    for kind, count in sorted(counts.items()):
        if kind not in coverage:
            raise InventoryError(
                f"no coverage entry for kind {kind!r}, which holds {count} record(s)"
            )
    for kind, entry in coverage.items():
        if entry.records != counts.get(kind, 0):
            raise InventoryError(
                f"coverage.{kind} counts {entry.records} records "
                f"but the manifest holds {counts.get(kind, 0)}"
            )
    seen: set[str] = set()
    for record in records:
        for artifact in record.artifacts:
            if artifact.path in seen:
                raise InventoryError(f"artifact path {artifact.path!r} is shared by two records")
            seen.add(artifact.path)
    return Inventory(
        schema_version=SCHEMA_VERSION,
        source=_source(_req(obj, "source", "manifest")),
        extracted_at=_str(obj, "extracted_at", "manifest"),
        cli_version=_str(obj, "cli_version", "manifest"),
        scope=_scope(_req(obj, "scope", "manifest")),
        publication=_str(obj, "publication", "manifest", choices=("published",)),
        records=records,
        coverage=coverage,
        gaps=tuple(
            _gap(g, f"gaps[{i}]")
            for i, g in enumerate(_list(_req(obj, "gaps", "manifest"), "gaps"))
        ),
    )


def read_inventory(folder: Path) -> Inventory:
    """Read and check the ``manifest.json`` of the migration inventory at *folder*.

    The reader checks the manifest only; it does not re-hash the artifact files.
    """
    path = folder / MANIFEST_NAME
    try:
        data = json.loads(path.read_text(encoding="utf-8-sig"))
    except (OSError, json.JSONDecodeError) as exc:
        raise InventoryError(f"cannot read {path}: {exc}") from exc
    return parse_manifest(data)


# ── Writer ──────────────────────────────────────────────────────────────────


def _canonical(value: object) -> str:
    return json.dumps(value, sort_keys=True, ensure_ascii=False)


def dump_manifest(inv: Inventory) -> str:
    """Serialize *inv* deterministically: two dumps of the same content are byte-identical.

    Records sort by kind, then key, then their own content (so records sharing
    an ambiguous key keep a stable order); artifacts by path; gaps by content.
    Keys are sorted and the text ends with a newline. The result is checked by
    `parse_manifest`, so a writer cannot emit a manifest the reader refuses.
    """
    records = [_record_dict(r) for r in inv.records]
    records.sort(key=lambda r: (r["kind"], r["key"], _canonical(r)))
    gaps = [asdict(g) for g in inv.gaps]
    gaps.sort(key=_canonical)
    manifest: dict[str, Any] = {
        "schema_version": inv.schema_version,
        "source": asdict(inv.source),
        "extracted_at": inv.extracted_at,
        "cli_version": inv.cli_version,
        "scope": {"mode": inv.scope.mode, "solutions": sorted(inv.scope.solutions)},
        "publication": inv.publication,
        "records": records,
        "coverage": {
            kind: {**asdict(c), "errors": list(c.errors)} for kind, c in inv.coverage.items()
        },
        "gaps": gaps,
    }
    parse_manifest(json.loads(_canonical(manifest)))
    return json.dumps(manifest, sort_keys=True, ensure_ascii=False, indent=2) + "\n"


def _record_dict(record: Record) -> dict[str, Any]:
    out = asdict(record)
    out["fields"] = dict(record.fields)
    out["artifacts"] = sorted((asdict(a) for a in record.artifacts), key=lambda a: a["path"])
    return out
