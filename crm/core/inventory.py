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
    raise NotImplementedError


def component_key(kind: str, fields: Mapping[str, Any]) -> str:
    """Build a record's component key from its normalized fields.

    The key joins the kind's `KEY_FIELDS` with ``|``, each part escaping ``%``
    and ``|`` (as ``%25`` and ``%7C``) so two different field tuples never give
    one key. A null part is the empty string; a null ribbon entity is
    ``application`` and a null sitemap name ``default``.

    Raises:
        InventoryError: the kind is unknown or a key field is missing.
    """
    raise NotImplementedError


def artifact_path(kind: str, key: str, name: str, *, discriminator: str | None = None) -> str:
    """Return the manifest-relative path of one record's artifact.

    The file is ``artifacts/<kind>/<slug>-<hash>.<name>``: a readable slug of
    the key plus the first 12 hex digits of a SHA-256 over the kind and full
    key, so two keys never share a file even when they slug alike. Records
    sharing an ambiguous key pass a *discriminator* (their source id) so each
    keeps its own file. *name* names the artifact within the record
    (``form.xml``, ``fetch.xml``, ``content.js``).
    """
    raise NotImplementedError


@dataclass(frozen=True)
class ArtifactHashes:
    """The raw and the normalized SHA-256 of one artifact's bytes."""

    sha256: str
    normalized_sha256: str


def normalize_text(data: bytes) -> bytes:
    """Apply the v1 text normalization: strip a leading UTF-8 BOM, convert CRLF to LF."""
    raise NotImplementedError


def artifact_hashes(data: bytes, *, text: bool) -> ArtifactHashes:
    """Hash an artifact's raw bytes; a text artifact's normalized hash follows `normalize_text`.

    A binary artifact uses the raw hash for both, since v1 defines no binary
    normalization.
    """
    raise NotImplementedError


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
        raise NotImplementedError


# ── Reader ──────────────────────────────────────────────────────────────────


def _obj(value: object, where: str) -> dict[str, Any]:
    raise NotImplementedError


def _list(value: object, where: str) -> list[Any]:
    raise NotImplementedError


def _req(obj: dict[str, Any], name: str, where: str) -> object:
    raise NotImplementedError


def _str(obj: dict[str, Any], name: str, where: str, *, choices: tuple[str, ...] = ()) -> str:
    raise NotImplementedError


def _opt_str(obj: dict[str, Any], name: str, where: str) -> str | None:
    raise NotImplementedError


def _int(obj: dict[str, Any], name: str, where: str) -> int:
    raise NotImplementedError


def _bool(obj: dict[str, Any], name: str, where: str, *, nullable: bool = False) -> bool | None:
    raise NotImplementedError


def _strings(value: object, where: str) -> tuple[str, ...]:
    raise NotImplementedError


def _source(value: object) -> Source:
    raise NotImplementedError


def _scope(value: object) -> Scope:
    raise NotImplementedError


def _artifact(value: object, kind: str, where: str) -> Artifact:
    raise NotImplementedError


def _record(value: object, where: str) -> Record:
    raise NotImplementedError


def _coverage(value: object, kind: str) -> Coverage:
    raise NotImplementedError


def _gap(value: object, where: str) -> Gap:
    raise NotImplementedError


def parse_manifest(data: object) -> Inventory:
    """Build an `Inventory` from a decoded manifest, applying the v1 contract.

    The version rule runs first: an unknown ``schema_version`` is refused
    whatever else the manifest holds. Fields v1 does not define are ignored.

    Raises:
        InventoryError: the manifest breaks the contract; the message names where.
    """
    raise NotImplementedError


def read_inventory(folder: Path) -> Inventory:
    """Read and check the ``manifest.json`` of the migration inventory at *folder*.

    The reader checks the manifest only; it does not re-hash the artifact files.
    """
    raise NotImplementedError


# ── Writer ──────────────────────────────────────────────────────────────────


def _canonical(value: object) -> str:
    raise NotImplementedError


def dump_manifest(inv: Inventory) -> str:
    """Serialize *inv* deterministically: two dumps of the same content are byte-identical.

    Records sort by kind, then key, then their own content (so records sharing
    an ambiguous key keep a stable order); artifacts by path; gaps by content.
    Keys are sorted and the text ends with a newline. The result is checked by
    `parse_manifest`, so a writer cannot emit a manifest the reader refuses.
    """
    raise NotImplementedError


def _record_dict(record: Record) -> dict[str, Any]:
    raise NotImplementedError
