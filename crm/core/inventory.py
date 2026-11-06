"""Migration inventory manifest v3: the contract every extractor emits (ADR 0032).

A migration inventory is a folder holding ``manifest.json`` (the inventory
manifest) and ``artifacts/<kind>/<file>``. This module owns the format both the
CRM 2011 path and the v9/cloud paths write: the component key per kind, artifact
paths and hashes, the typed records, a deterministic writer and a reader that
applies the version rule. The JSON Schema of the same format ships at
``crm/schemas/inventory-manifest-v3.schema.json``; the reader validates
structure without it, so no runtime code depends on ``jsonschema``.
"""

from __future__ import annotations

import hashlib
import json
import re
import xml.etree.ElementTree as ET
from collections.abc import Mapping
from dataclasses import asdict, dataclass, replace
from pathlib import Path
from typing import Any, cast

from crm.utils import safe_xml

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
    "ribbon_diff": ("entity",),
}
KINDS: tuple[str, ...] = tuple(KEY_FIELDS)

#: Key fields holding a platform integer code (shared by CRM 2011 and v9, so keys
#: match across stages). Every other key field is a non-empty string, except that a
#: key reference (`_REFERENCE_FIELDS`) to the application ribbon or default sitemap is "".
INTEGER_KEY_FIELDS = frozenset(
    ("form_type", "stage", "mode", "image_type", "category", "dependency_type")
)
#: The (kind, field) pairs whose key field may be null.
NULLABLE_KEY_FIELDS = frozenset(
    (
        ("step", "primary_entity"),
        ("process", "primary_entity"),
        ("ribbon", "entity"),
        ("sitemap", "unique_name"),
        ("ribbon_diff", "entity"),
    )
)

#: The key a v1 manifest gave a null single-part key (the application ribbon, the
#: default sitemap). v2 keys them ``""``, which no entity logical name or sitemap
#: unique name can be; the reader upgrades a v1 record by its field, not its key.
_V1_NULL_KEY: dict[str, str] = {"ribbon": "application", "sitemap": "default"}
#: The kinds whose key may be (or be referenced as) the empty key.
_EMPTY_KEY_KINDS = frozenset(_V1_NULL_KEY)
#: The kinds whose own record may hold the empty key: those, and the application ribbon diff.
_EMPTY_RECORD_KINDS = _EMPTY_KEY_KINDS | {"ribbon_diff"}


class InventoryError(Exception):
    """A manifest, record or key that breaks the inventory manifest contract."""


class KeyFieldValueError(InventoryError):
    """A key field present but holding a value the key contract refuses (empty or mistyped)."""


#: Key-reference fields mapped to the field naming the referenced kind. A reference
#: to the application ribbon or default sitemap is the empty key, which is legal here.
_REFERENCE_FIELDS = {
    "member_key": "member_kind",
    "dependent_key": "dependent_kind",
    "required_key": "required_kind",
}


def _key_part(kind: str, name: str, value: object) -> str:
    if name in INTEGER_KEY_FIELDS:
        if type(value) is not int:
            raise KeyFieldValueError(f"{kind}.{name} must be an integer, got {value!r}")
        return str(value)
    if value is None and (kind, name) in NULLABLE_KEY_FIELDS:
        return ""
    if not isinstance(value, str) or not value:
        nullable = " or null" if (kind, name) in NULLABLE_KEY_FIELDS else ""
        raise KeyFieldValueError(
            f"{kind}.{name} must be a non-empty string{nullable}, got {value!r}"
        )
    return value.replace("%", "%25").replace("|", "%7C")


def component_key(kind: str, fields: Mapping[str, Any]) -> str:
    """Build a record's component key from its normalized fields.

    The key joins the kind's `KEY_FIELDS` with ``|``, each part escaping ``%``
    and ``|`` (as ``%25`` and ``%7C``) so two different field tuples never give
    one key. An integer field (`INTEGER_KEY_FIELDS`) is written in decimal;
    only `NULLABLE_KEY_FIELDS` may be null, as the empty part, so a null and
    an empty string never meet. The application ribbon (null entity) and the
    default sitemap (null name) therefore key ``""``, never a name an entity or
    sitemap can hold. A reference to either, in a membership's or dependency's
    key-reference field (`_REFERENCE_FIELDS`), is that same empty key.

    Raises:
        InventoryError: the kind is unknown, or a key field is missing or mistyped.
    """
    names = KEY_FIELDS.get(kind)
    if names is None:
        raise InventoryError(f"unknown kind {kind!r}; v1 kinds: {', '.join(KINDS)}")
    missing = [name for name in names if name not in fields]
    if missing:
        raise InventoryError(f"{kind} record lacks key field(s): {', '.join(missing)}")
    parts: list[str] = []
    for name in names:
        referenced = fields.get(_REFERENCE_FIELDS[name]) if name in _REFERENCE_FIELDS else None
        parts.append("" if fields[name] == "" and referenced in _EMPTY_KEY_KINDS
                     else _key_part(kind, name, fields[name]))  # fmt: skip
    return "|".join(parts)


def artifact_path(kind: str, key: str, name: str, *, discriminator: str | None = None) -> str:
    """Return the manifest-relative path of one record's artifact.

    The file is ``artifacts/<kind>/<slug>-<hash>.<name>``: a readable slug of
    the key plus the first 12 hex digits of a SHA-256 over the kind and full
    key, so two keys never share a file even when they slug alike. Records
    sharing an ambiguous key pass a *discriminator* (their source id, or with
    none their key fields' values) so each keeps its own file. *name* names
    the artifact within the record (``form.xml``, ``fetch.xml``, ``content.js``).
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


# The ribbon parents whose children the server may return in any order (#1112).
_RIBBON_UNORDERED = frozenset(("EnableRules", "DisplayRules", "CommandDefinitions"))


def _canonical_xml(element: ET.Element) -> str:
    return ET.canonicalize(ET.tostring(element, encoding="unicode"), strip_text=True)


def canonical_ribbon(data: bytes) -> bytes | None:
    """A ribbon's canonical form, or ``None`` when it is not well-formed XML.

    The children of ``EnableRules``, ``DisplayRules`` and ``CommandDefinitions``
    are ordered by tag, ``Id`` and full canonical content: ``Id`` alone is not
    unique among siblings. Everything else keeps its order, which may carry
    meaning.
    """
    try:
        root = safe_xml.fromstring(normalize_text(data))
        # Reversed document order puts every descendant before its ancestor, so a parent's
        # sort keys are taken over already-ordered children.
        for parent in reversed(list(root.iter())):
            if parent.tag in _RIBBON_UNORDERED:
                keyed = [(c.tag, c.get("Id", ""), _canonical_xml(c), c) for c in parent]
                keyed.sort(key=lambda k: k[:3])
                parent[:] = [k[3] for k in keyed]
        return _canonical_xml(root).encode("utf-8")
    except ET.ParseError:
        return None


def artifact_hashes(data: bytes, *, text: bool, ribbon: bool = False) -> ArtifactHashes:
    """Hash an artifact's raw bytes; a text artifact's normalized hash follows `normalize_text`.

    A *ribbon* hashes its `canonical_ribbon` form instead, and a ribbon that does
    not parse falls back to the text normalization. A binary artifact uses the
    raw hash for both, since v1 defines no binary normalization.
    """
    raw = hashlib.sha256(data).hexdigest()
    if not text:
        return ArtifactHashes(sha256=raw, normalized_sha256=raw)
    normalized = (canonical_ribbon(data) if ribbon else None) or normalize_text(data)
    return ArtifactHashes(sha256=raw, normalized_sha256=hashlib.sha256(normalized).hexdigest())


SCHEMA_VERSION = 3
#: The versions the reader accepts; v1 and v2 are upgraded on read (see `_record`,
#: `_upgrade_ribbon_diff_coverage`).
READ_VERSIONS = (1, 2, 3)
#: `READ_VERSIONS` as an error message names them: ``1, 2 and 3``.
READ_VERSIONS_TEXT = ", ".join(map(str, READ_VERSIONS[:-1])) + f" and {READ_VERSIONS[-1]}"
MANIFEST_NAME = "manifest.json"

PLATFORMS = ("crm2011", "onprem", "cloud")
ORIGINS = ("custom", "system", "unknown")
PUBLICATION_STATES = ("published", "unknown")
COVERAGE_STATUSES = ("complete", "partial", "unsupported", "failed")
CONTENTS = ("text", "binary")
_SHA256 = re.compile(r"[0-9a-f]{64}")
# The schema's org_url pattern, ^https?://[^/@]+(/.*)?$, with the authority captured so an
# "@" in it reads as credentials. A regex, not urlsplit: urlsplit lowercases the scheme,
# strips newlines and raises ValueError on a malformed IPv6 host.
_ORG_URL = re.compile(r"https?://([^/]+)(/.*)?")
# An RFC 3339 date-time, the schema's extracted_at pattern.
_EXTRACTED_AT = re.compile(
    r"[0-9]{4}-[0-9]{2}-[0-9]{2}T[0-9]{2}:[0-9]{2}:[0-9]{2}(\.[0-9]+)?(Z|[+-][0-9]{2}:[0-9]{2})"
)


@dataclass(frozen=True)
class Source:
    """The org an inventory was read from; *org_url* never carries credentials."""

    platform: str
    org_url: str
    organization_id: str | None
    version: str


SYSTEM_SCOPES = ("included", "excluded")


@dataclass(frozen=True)
class Scope:
    """What the extraction read: the whole org (``org``) or the named ``solutions``.

    *system* says whether stock components (managed, or owned by a managed
    solution) are ``included`` or ``excluded``; a manifest without it is full.
    *usage_read* says the extraction read usage (``--with-usage``): a record with no
    ``usage`` then has no signal to read, and in a manifest without it usage was never
    read. Neither ever means unused.
    """

    mode: str
    solutions: tuple[str, ...]
    system: str = "included"
    usage_read: bool = False

    def to_dict(self) -> dict[str, Any]:
        """The manifest form; ``system`` and ``usage_read`` are written only off their default."""
        out: dict[str, Any] = {"mode": self.mode, "solutions": sorted(self.solutions)}
        if self.system != "included":
            out["system"] = self.system
        if self.usage_read:
            out["usage_read"] = True
        return out


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
    fields: dict[str, Any]
    artifacts: tuple[Artifact, ...]
    #: Usage as extracted with ``--with-usage`` (`crm.core.usage.Usage.to_dict`), else None:
    #: not read, which is distinct from ``state: unknown`` (read, no honest answer).
    usage: dict[str, Any] | None = None


@dataclass(frozen=True)
class KindCoverage:
    status: str
    pages: int
    records: int
    truncated: bool
    errors: tuple[str, ...]


@dataclass(frozen=True)
class ExtractionGap:
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
    coverage: Mapping[str, KindCoverage]
    gaps: tuple[ExtractionGap, ...]

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
    match = _ORG_URL.fullmatch(url)
    if match is None:
        raise InventoryError(f"source.org_url must be an http(s) URL, got {url!r}")
    if "@" in match.group(1):
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
    system = _str(obj, "system", "scope", choices=SYSTEM_SCOPES) if "system" in obj else "included"
    usage_read = bool(_bool(obj, "usage_read", "scope")) if "usage_read" in obj else False
    return Scope(mode=mode, solutions=solutions, system=system, usage_read=usage_read)


def _artifact(value: object, kind: str, where: str) -> Artifact:
    obj = _obj(value, where)
    path = _str(obj, "path", where)
    prefix = f"artifacts/{kind}/"
    if (
        not path.startswith(prefix)
        or "/" in path[len(prefix) :]
        or path.endswith("/")
        or path[len(prefix) :] in (".", "..")
    ):
        raise InventoryError(f"{where}.path {path!r} must be a file directly under {prefix}")
    hashes = [_str(obj, name, where) for name in ("sha256", "normalized_sha256")]
    if not all(_SHA256.fullmatch(h) for h in hashes):
        raise InventoryError(f"{where} hashes must be 64 lowercase hex digits")
    content = _str(obj, "content", where, choices=CONTENTS)
    if content == "binary" and hashes[0] != hashes[1]:
        raise InventoryError(f"{where} is binary, so its normalized_sha256 must equal its sha256")
    return Artifact(path=path, content=content, sha256=hashes[0], normalized_sha256=hashes[1])


def _defined_fields(kind: str, fields: dict[str, Any], where: str) -> None:
    """Check the fields v1 defines beyond the key, when present."""
    if kind == "form" and "libraries" in fields:
        _strings(fields["libraries"], f"{where}.libraries")
    if kind == "form" and "handlers" in fields:
        for i, item in enumerate(_list(fields["handlers"], f"{where}.handlers")):
            handler = _obj(item, f"{where}.handlers[{i}]")
            for name in ("event", "function", "library"):
                _str(handler, name, f"{where}.handlers[{i}]")
            _opt_str(handler, "attribute", f"{where}.handlers[{i}]")
            _bool(handler, "enabled", f"{where}.handlers[{i}]")
    if kind == "process" and "activities" in fields:
        _strings(fields["activities"], f"{where}.activities")


USAGE_STATES = ("used", "unused", "unknown")


def _usage(obj: dict[str, Any], where: str) -> dict[str, Any] | None:
    """The record's optional ``usage``, checked; absent means not read."""
    if "usage" not in obj:
        return None
    where = f"{where}.usage"
    usage = _obj(obj["usage"], where)
    _str(usage, "state", where, choices=USAGE_STATES)
    _str(usage, "signal", where)
    _str(usage, "as_of", where)
    _opt_str(usage, "reason", where)
    if _req(usage, "value", where) is not None:
        _int(usage, "value", where)
    return {k: usage[k] for k in ("state", "signal", "value", "as_of", "reason")}


def _record(value: object, where: str, version: int = SCHEMA_VERSION) -> Record:
    obj = _obj(value, where)
    kind = _str(obj, "kind", where, choices=KINDS)
    key = _req(obj, "key", where)
    # Only the application ribbon and default sitemap key "" (below, by their fields).
    if not isinstance(key, str) or (not key and kind not in _EMPTY_RECORD_KINDS):
        raise InventoryError(f"{where}.key must be a non-empty string")
    fields = _obj(_req(obj, "fields", where), f"{where}.fields")
    _defined_fields(kind, fields, f"{where}.fields")
    expected = component_key(kind, fields)
    if version == 1 and kind in _V1_NULL_KEY and fields[KEY_FIELDS[kind][0]] is None:
        # Upgrade: v1 keyed this record by a name an entity or sitemap can hold.
        if key != _V1_NULL_KEY[kind]:
            raise InventoryError(
                f"{where} key {key!r} does not match its fields' component key "
                f"{_V1_NULL_KEY[kind]!r}"
            )
        key = expected
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
        usage=_usage(obj, where),
    )


def _coverage(value: object, kind: str) -> KindCoverage:
    where = f"coverage.{kind}"
    obj = _obj(value, where)
    coverage = KindCoverage(
        status=_str(obj, "status", where, choices=COVERAGE_STATUSES),
        pages=_int(obj, "pages", where),
        records=_int(obj, "records", where),
        truncated=bool(_bool(obj, "truncated", where)),
        errors=_strings(_req(obj, "errors", where), f"{where}.errors"),
    )
    if coverage.status == "complete" and coverage.truncated:
        raise InventoryError(f"{where} is complete but truncated: a truncated read is partial")
    return coverage


def _gap(value: object, where: str) -> ExtractionGap:
    obj = _obj(value, where)
    kind = _opt_str(obj, "kind", where)
    if kind is not None and kind not in KINDS:
        raise InventoryError(f"{where}.kind {kind!r} is not a v1 kind")
    gap = ExtractionGap(
        kind=kind,
        key=_opt_str(obj, "key", where),
        description=_opt_str(obj, "description", where),
        reason=_str(obj, "reason", where),
    )
    # The empty key names the application ribbon or default sitemap, and only those.
    names_a_key = bool(gap.key) or (gap.key == "" and kind in _EMPTY_RECORD_KINDS)
    if not names_a_key and not gap.description:
        raise InventoryError(f"{where} must name a key or a description")
    return gap


def _null_record_kinds(records: tuple[Record, ...]) -> tuple[set[str], set[str]]:
    """The kinds holding a null ribbon or sitemap, and those where no record shares its v1 key.

    A v1 reference to `application` names the application ribbon only where no entity
    `application` has a ribbon of its own; where one does, the reference is ambiguous.
    """
    held = {kind for kind in _V1_NULL_KEY if any(r.kind == kind and r.key == "" for r in records)}
    unambiguous = {
        kind
        for kind in held
        if not any(r.kind == kind and r.key == _V1_NULL_KEY[kind] for r in records)
    }
    return held, unambiguous


def _upgrade_references(records: tuple[Record, ...]) -> tuple[Record, ...]:
    """Re-key an unambiguous v1 reference to the application ribbon or default sitemap to `""`."""
    _, upgradable = _null_record_kinds(records)
    out: list[Record] = []
    for record in records:
        fields = dict(record.fields)
        for name, kind_field in _REFERENCE_FIELDS.items():
            kind = fields.get(kind_field)
            if kind in upgradable and fields.get(name) == _V1_NULL_KEY[kind]:
                fields[name] = ""
        if fields != record.fields:
            record = replace(record, fields=fields, key=component_key(record.kind, fields))
        out.append(record)
    return tuple(out)


def _upgrade_gaps(
    gaps: tuple[ExtractionGap, ...], records: tuple[Record, ...]
) -> tuple[ExtractionGap, ...]:
    """Re-key a v1 gap on the application ribbon or default sitemap to its v2 key.

    Where the v1 key is ambiguous (an entity `application` has a ribbon too), or the gap
    names no kind, the original stays beside its twin: it may still be another record's.
    """
    held, unambiguous = _null_record_kinds(records)
    out: list[ExtractionGap] = []
    for gap in gaps:
        keep = True
        for kind, old in _V1_NULL_KEY.items():
            if gap.key != old or gap.kind not in (None, kind) or kind not in held:
                continue
            out.append(ExtractionGap(kind, "", gap.description, gap.reason))
            keep = gap.kind is None or kind not in unambiguous
        if keep:
            out.append(gap)
    return tuple(out)


def _extracted_at(obj: dict[str, Any]) -> str:
    value = _str(obj, "extracted_at", "manifest")
    if not _EXTRACTED_AT.fullmatch(value):
        raise InventoryError(f"manifest.extracted_at must be an RFC 3339 date-time, got {value!r}")
    return value


def parse_manifest(data: object) -> Inventory:
    """Build an `Inventory` from a decoded manifest, applying the v2 contract.

    The version rule runs first: a ``schema_version`` outside `READ_VERSIONS`
    is refused whatever else the manifest holds. A v1 manifest is upgraded: its
    application ribbon and default sitemap take the v2 key ``""``. A v1 or v2
    manifest gains ``unsupported`` coverage for the v3 ``ribbon_diff`` kind.
    The result reads as v3. Fields the contract does not define are ignored.

    Raises:
        InventoryError: the manifest breaks the contract; the message names where.
    """
    obj = _obj(data, "manifest")
    version = obj.get("schema_version")
    if type(version) is not int or version not in READ_VERSIONS:
        raise InventoryError(
            f"manifest schema_version {version!r} is not supported; "
            f"this crm reads schema_version {READ_VERSIONS_TEXT}"
        )
    records = tuple(
        _record(r, f"records[{i}]", version)
        for i, r in enumerate(_list(_req(obj, "records", "manifest"), "records"))
    )
    if version == 1:
        records = _upgrade_references(records)
    raw_coverage = _obj(_req(obj, "coverage", "manifest"), "coverage")
    unknown = sorted(set(raw_coverage) - set(KINDS))
    if unknown:
        raise InventoryError(f"coverage names unknown kind(s): {', '.join(unknown)}")
    coverage = {kind: _coverage(raw_coverage[kind], kind) for kind in sorted(raw_coverage)}
    if version < 3 and "ribbon_diff" not in coverage:
        # Upgrade: an older extraction never read the kind, so say so rather than leave it absent.
        coverage["ribbon_diff"] = KindCoverage(
            "unsupported",
            0,
            0,
            False,
            (f"not read: a schema_version {version} extraction predates the ribbon_diff kind",),
        )
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
    gaps = tuple(
        _gap(g, f"gaps[{i}]") for i, g in enumerate(_list(_req(obj, "gaps", "manifest"), "gaps"))
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
        extracted_at=_extracted_at(obj),
        cli_version=_str(obj, "cli_version", "manifest"),
        scope=_scope(_req(obj, "scope", "manifest")),
        publication=_str(obj, "publication", "manifest", choices=("published",)),
        records=records,
        coverage=coverage,
        gaps=_upgrade_gaps(gaps, records) if version == 1 else gaps,
    )


def load_manifest(folder: Path) -> object:
    """Decode the ``manifest.json`` at *folder* without checking it against the contract.

    For a caller that must look at a field (``schema_version``) before
    `parse_manifest` refuses the manifest; everyone else calls `read_inventory`.
    """
    path = folder / MANIFEST_NAME
    try:
        return json.loads(path.read_text(encoding="utf-8-sig"))
    except (OSError, ValueError) as exc:  # ValueError: malformed UTF-8 or JSON
        raise InventoryError(f"cannot read {path}: {exc}") from exc


def read_inventory(folder: Path) -> Inventory:
    """Read and check the ``manifest.json`` of the migration inventory at *folder*.

    The reader checks the manifest only; it does not re-hash the artifact files.
    """
    return parse_manifest(load_manifest(folder))


# ── Writer ──────────────────────────────────────────────────────────────────


def _canonical(value: object) -> str:
    try:
        return json.dumps(value, sort_keys=True, ensure_ascii=False, allow_nan=False)
    except (TypeError, ValueError) as exc:
        raise InventoryError(f"manifest holds a value JSON cannot carry: {exc}") from exc


def dump_manifest(inv: Inventory) -> str:
    """Serialize *inv* deterministically: two dumps of the same content are byte-identical.

    Records sort by kind, then key, then their own content (so records sharing
    an ambiguous key keep a stable order); artifacts by path; gaps by content;
    coverage errors by text.
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
        "scope": inv.scope.to_dict(),
        "publication": inv.publication,
        "records": records,
        "coverage": {
            kind: {**asdict(c), "errors": sorted(c.errors)} for kind, c in inv.coverage.items()
        },
        "gaps": gaps,
    }
    parse_manifest(json.loads(_canonical(manifest)))
    return json.dumps(manifest, sort_keys=True, ensure_ascii=False, indent=2) + "\n"


def _record_dict(record: Record) -> dict[str, Any]:
    out = asdict(record)
    if record.usage is None:
        del out["usage"]  # a default extract stays byte-identical to one before --with-usage
    out["fields"] = dict(record.fields)
    out["artifacts"] = sorted((asdict(a) for a in record.artifacts), key=lambda a: a["path"])
    return out
