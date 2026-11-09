"""Extract a migration inventory from an org (ADR 0031, ADR 0032).

``crm inventory extract`` runs this on every platform. A **source** answers the
reads: `LegacySource` over the CRM 2011 SOAP client, and
`crm.core.webapi_inventory.WebApiSource` over the Web API of a v9 on-prem or
Dataverse online org. Both run the same FetchXML reads (`_READS`) and return
rows and metadata in one shape, so this module reads every kind of the v1
manifest the same way and writes the inventory folder `crm.core.inventory`
defines.

The read is split into **units** (one metadata request, one entity's detail,
one FetchXML page, one ribbon). Each finished unit is saved under
``<out>/.checkpoint/`` before the next starts, with its payloads staged beside
it, so an interrupted run resumes from its output folder: a finished unit is
reused, and a kind's paging resumes after its last finished page from that
page's cookie. Records are built only once every unit has run, because a
step's key needs its plug-in type's key, and a membership or a dependency
needs the key of the component it names. The checkpoint is removed once a run
reads every unit without an error.

A failed unit is recorded in its kind's coverage and the run goes on
(continue-and-report, ADR 0007); a connection-wide failure (401, no HTTP
response) stops the run, and a re-run resumes it.
"""

from __future__ import annotations

import base64
import hashlib
import json
import os
import re
import shutil
import time
import zipfile
import zlib
from collections import Counter
from collections.abc import Callable, Collection, Iterable, Iterator
from dataclasses import dataclass, field, replace
from datetime import UTC, datetime
from pathlib import Path
from typing import Any, Protocol, cast
from xml.etree.ElementTree import ParseError

from crm.core import legacy_reads
from crm.core.inventory import (
    KEY_FIELDS,
    KINDS,
    MANIFEST_NAME,
    SCHEMA_VERSION,
    Artifact,
    ArtifactHashes,
    ExtractionGap,
    Inventory,
    KeyFieldValueError,
    KindCoverage,
    Record,
    Scope,
    Source,
    artifact_hashes,
    artifact_path,
    component_key,
    dump_manifest,
)
from crm.core.legacy import LegacyClient, Page
from crm.core.ribbon_diff import read_ribbon_diffs
from crm.core.usage import INVENTORY_GROUPS, JOB_TIME_CAP, GroupUsage, Usage, probe_legacy_group
from crm.utils.d365_backend import PLATFORM_CRM2011, D365Error

CHECKPOINT_DIR = ".checkpoint"

# The solutioncomponent / dependency type code of an option set, global or local by the set.
_OPTION_SET = 9
_ENTITY = 1
_WEB_RESOURCE = 61
_ASSEMBLY = 91

# The relationship fields naming an entity, 1:N and N:N.
_RELATIONSHIP_SIDES = ("referenced_entity", "referencing_entity", "entity1", "entity2")
# The component types a scoped run reads only for member entities (RetrieveEntity):
# attribute, local option set, relationship.
_ENTITY_DETAIL_TYPES = frozenset({2, _OPTION_SET, 10})
_UNRESOLVED_REASON = (
    "the component is of a kind v1 does not define, its own read failed,"
    " or its own row has no component key"
)
_OUT_OF_SCOPE_REASON = (
    "a --solution run reads attributes, relationships and local option sets of member"
    " entities only, so the component may belong to an entity outside the named"
    " solutions (a whole-org run records it); or "
)

# Kinds whose rows carry no ismanaged: the stock scope tells them apart by their own
# custom and managed flags (metadata) or does not drop them (the rest).
_METADATA_KINDS = frozenset(
    ("entity", "attribute", "relationship", "global_option_set", "local_option_set")
)
# The first id segment of a ribbon entry the platform ships (measured on a Dataverse org,
# 2026-11-05: ``Mscrm.`` alone leaves every one of 828 ribbons "customized").
_STOCK_RIBBON_IDS = frozenset((
    "mscrm", "msrcm", "aibuilder", "fcb", "accesschecker", "ismodernclienttype",
    "solutionchecker", "clientutility", "microsoft", "msdyn", "mspp", "portals", "adx",
    "analysisjob",
))  # fmt: skip
_RIBBON_ID = re.compile(r'\bId="([^"]+)"')
# `ribbon hide --method hide-action` writes a HideCustomAction whose ids are the target's stock
# ones; none of the 828 ribbons measured on that org holds one, so its presence is a customization.
_RIBBON_HIDE = re.compile(r"<HideCustomAction\b")
_PLATFORM_REASON = (
    "platform: the default migration scope leaves stock components out (managed, or owned by"
    " a managed solution); --include-system keeps them"
)

# Web resource types whose content is text, by webresourcetype code.
_TEXT_WEB_RESOURCES = frozenset((1, 2, 3, 4, 9))
WEB_RESOURCE_EXT = dict(
    enumerate(("htm", "css", "js", "xml", "png", "jpg", "gif", "xap", "xsl", "ico"), start=1)
)
_ISV_PAGE = re.compile(r"/ISV/[^\s\"'<>?#)\\,;]+", re.IGNORECASE)


def integration_gap(api: str) -> ExtractionGap:
    """The gap every platform has: integrations live outside the org's customizations."""
    return ExtractionGap(
        kind=None,
        key=None,
        description="external integrations: services that call the org, or that its"
        " plug-ins and processes call",
        reason="an integration's endpoints and credentials live outside the org's"
        f" customizations, so {api} cannot list them",
    )


def ssrs_gap(api: str) -> ExtractionGap:
    """The on-premises gap: an SSRS report's SQL data source is configured in SSRS."""
    return ExtractionGap(
        kind=None,
        key=None,
        description="SQL data sources of SSRS reports",
        reason="reports are not a v1 kind, and a report's data source is configured in"
        f" SSRS, which {api} does not reach",
    )


class InventorySource(Protocol):
    """The reads one platform answers; rows and metadata come back in the legacy shapes.

    *platform* is the manifest's ``source.platform``; *api* names the endpoint
    in gap reasons; *static_gaps* are the gaps the platform always has.
    """

    platform: str
    api: str
    static_gaps: tuple[ExtractionGap, ...]
    default_export_supported: bool  # whether the platform exports the Default solution

    @property
    def org_url(self) -> str: ...

    def identity(self) -> dict[str, Any]:
        """``organization_id`` and ``version`` of the org."""
        ...

    def list_entities(self) -> list[dict[str, Any]]: ...

    def list_option_sets(self) -> list[dict[str, Any]]: ...

    def entity_detail(self, name: str) -> dict[str, Any]: ...

    def ribbon_xml(self, entity: str | None) -> bytes: ...

    def export_solution(self, unique_name: str, dest: Path) -> None:
        """Write the unmanaged export of the solution to *dest* (a zip)."""
        ...

    def pages(self, fetch_xml: str, *, page_size: int, start: Page | None) -> Iterator[Page]: ...

    def probe_usage(
        self,
        group: str,
        *,
        names: Collection[str],
        time_cap: float,
        progress: Callable[[int, int], None] | None,
    ) -> GroupUsage:
        """Usage of one `INVENTORY_GROUPS` group, by the probes `--in-use` runs.

        *names* are the entity logical names to probe; *time_cap* bounds a capped read.
        """
        ...


class LegacySource:
    """A CRM 2011 org, read over the read-only SOAP client."""

    platform = PLATFORM_CRM2011
    api = "the CRM 2011 SOAP endpoint"
    static_gaps = (integration_gap(api), ssrs_gap(api))
    default_export_supported = True

    def __init__(self, client: LegacyClient):
        self.client = client

    @property
    def org_url(self) -> str:
        return self.client.profile.url

    def identity(self) -> dict[str, Any]:
        return {
            "organization_id": self.client.execute("WhoAmI").get("OrganizationId"),
            "version": self.client.execute("RetrieveVersion").get("Version"),
        }

    def list_entities(self) -> list[dict[str, Any]]:
        return legacy_reads.list_entities(self.client)

    def list_option_sets(self) -> list[dict[str, Any]]:
        return legacy_reads.list_option_sets(self.client)

    def entity_detail(self, name: str) -> dict[str, Any]:
        return legacy_reads.entity_detail(self.client, name)

    def ribbon_xml(self, entity: str | None) -> bytes:
        return legacy_reads.ribbon_xml(self.client, entity)

    def export_solution(self, unique_name: str, dest: Path) -> None:
        dest.write_bytes(legacy_reads.export_solution(self.client, unique_name))

    def pages(self, fetch_xml: str, *, page_size: int, start: Page | None) -> Iterator[Page]:
        return self.client.pages(fetch_xml, page_size=page_size, start=start)

    def probe_usage(
        self,
        group: str,
        *,
        names: Collection[str],
        time_cap: float,
        progress: Callable[[int, int], None] | None,
    ) -> GroupUsage:
        return probe_legacy_group(self.client, group, names, time_cap=time_cap, progress=progress)


def _ribbon_readable(entity: dict[str, Any]) -> bool:
    """Whether the entity's ribbon can be read: it has a display label and, where the
    source reads a collection label (the Web API source does, the crm2011 source has
    no ``collection_name`` key), that label too. RetrieveEntityRibbon faults for an
    entity with no collection label in the user's language (seen on a cloud org).
    """
    return bool(entity.get("display_name")) and (
        "collection_name" not in entity or bool(entity["collection_name"])
    )


def _ribbon_gaps(unlabelled: list[str]) -> list[ExtractionGap]:
    if not unlabelled:
        return []
    return [
        ExtractionGap(
            kind="ribbon",
            key=None,
            description=f"ribbons of {len(unlabelled)} entities with no display or collection"
            f" label: {', '.join(unlabelled)}",
            reason="an entity with no display label has no UI, so its ribbon is not read"
            " (CRM 2011 faults RetrieveEntityRibbon for one); the Web API faulted it, on a cloud"
            " org, for an entity with no collection label in the user's language",
        )
    ]


def _source_gaps(errors: list[str]) -> list[ExtractionGap]:
    """One gap per failed source read, so the manifest says why its build is unknown."""
    return [
        ExtractionGap(
            kind=None,
            key=None,
            description="source identity and build (WhoAmI, RetrieveVersion)",
            reason=f"the read failed: {error.partition(': ')[2]}",
        )
        for error in errors
    ]


def check_out(out: Path) -> None:
    """Refuse *out* when it holds files but no checkpoint, before anything connects.

    Raises:
        D365Error: *out* holds a finished inventory or unrelated files.
    """
    if out.exists() and not (out / CHECKPOINT_DIR / "state.json").is_file() and any(out.iterdir()):
        raise D365Error(
            f"--out {out} holds files and no checkpoint to resume; pass an empty or new folder"
        )


@dataclass(frozen=True)
class _Read:
    """One RetrieveMultiple read: the inventory kind it feeds and its query.

    *payloads* maps a column holding a large body to the artifact name it is
    staged under (``content``'s name is chosen per row for web resources).
    *member_type* is the solution component type of a kind whose payloads a
    ``--solution`` run reads only for the scope's members, in batches of
    *content_batch* ids, after the rows themselves are read without them.
    """

    kind: str
    entity: str
    attributes: tuple[str, ...]
    payloads: tuple[tuple[str, str], ...] = ()
    extra: str = ""
    page_size: int = 5000
    member_type: int | None = None
    content_batch: int = 50

    @property
    def payload_columns(self) -> tuple[str, ...]:
        return tuple(column for column, _ in self.payloads)

    @property
    def key_attribute(self) -> str:
        """The entity's primary key column (``systemform``'s is ``formid``)."""
        return "formid" if self.entity == "systemform" else f"{self.entity}id"

    def without_payloads(self) -> _Read:
        return replace(
            self,
            attributes=tuple(a for a in self.attributes if a not in self.payload_columns),
            payloads=(),
        )

    def fetch(self) -> str:
        columns = "".join(f'<attribute name="{a}"/>' for a in self.attributes)
        return f'<fetch><entity name="{self.entity}">{columns}{self.extra}</entity></fetch>'


def _link(entity: str, key: str, alias: str, column: str) -> str:
    return (
        f'<link-entity name="{entity}" from="{key}" to="{key}" link-type="outer"'
        f' alias="{alias}"><attribute name="{column}"/></link-entity>'
    )


# Run order: solutions first (origin needs them), plug-in types before steps.
_READS: tuple[_Read, ...] = (
    _Read(
        "solution",
        "solution",
        ("uniquename", "friendlyname", "version", "ismanaged", "publisherid", "isvisible"),
    ),
    _Read(
        "web_resource",
        "webresource",
        ("name", "displayname", "webresourcetype", "ismanaged", "solutionid", "content"),
        payloads=(("content", "content"),),
        member_type=_WEB_RESOURCE,
    ),
    _Read(
        "plugin_assembly",
        "pluginassembly",
        (
            "name",
            "version",
            "culture",
            "publickeytoken",
            "isolationmode",
            "sourcetype",
            "ismanaged",
            "solutionid",
            "content",
        ),
        payloads=(("content", "content.dll"),),
        page_size=20,  # each row carries its assembly binary
        member_type=_ASSEMBLY,
        content_batch=20,
    ),  # fmt: skip
    _Read(
        "plugin_type",
        "plugintype",
        (
            "typename",
            "friendlyname",
            "pluginassemblyid",
            "isworkflowactivity",
            "ismanaged",
            "solutionid",
        ),
    ),
    _Read(
        "step",
        "sdkmessageprocessingstep",
        (
            "name",
            "stage",
            "mode",
            "rank",
            "plugintypeid",
            "statecode",
            "filteringattributes",
            "supporteddeployment",
            "ismanaged",
            "solutionid",
        ),
        extra=_link("sdkmessage", "sdkmessageid", "message", "name")
        + _link("sdkmessagefilter", "sdkmessagefilterid", "filter", "primaryobjecttypecode"),
    ),  # fmt: skip
    _Read(
        "step_image",
        "sdkmessageprocessingstepimage",
        (
            "name",
            "entityalias",
            "imagetype",
            "attributes",
            "messagepropertyname",
            "sdkmessageprocessingstepid",
            "ismanaged",
            "solutionid",
        ),
    ),  # fmt: skip
    _Read(
        "process",
        "workflow",
        ("name", "category", "primaryentity", "statecode", "ismanaged", "solutionid", "xaml"),
        payloads=(("xaml", "workflow.xaml"),),
        # type 1 is a definition; activations (type 2) duplicate it.
        extra='<filter><condition attribute="type" operator="eq" value="1"/></filter>',
    ),
    _Read(
        "form",
        "systemform",
        ("name", "objecttypecode", "type", "ismanaged", "solutionid", "formxml"),
        payloads=(("formxml", "form.xml"),),
    ),
    _Read(
        "view",
        "savedquery",
        (
            "name",
            "returnedtypecode",
            "querytype",
            "ismanaged",
            "solutionid",
            "fetchxml",
            "layoutxml",
        ),
        payloads=(("fetchxml", "fetch.xml"), ("layoutxml", "layout.xml")),
    ),
    _Read(
        "sitemap",
        "sitemap",
        ("sitemapxml", "ismanaged", "solutionid"),
        payloads=(("sitemapxml", "sitemap.xml"),),
    ),
    _Read("solution_membership", "solutioncomponent", ("solutionid", "componenttype", "objectid")),
    _Read(
        "dependency",
        "dependency",
        (
            "dependentcomponentobjectid",
            "dependentcomponenttype",
            "requiredcomponentobjectid",
            "requiredcomponenttype",
            "dependencytype",
        ),
    ),  # fmt: skip
)
# Columns v9 has and CRM 2011 lacks: an app sitemap's unique name (one sitemap per
# model-driven app, null for the default), and how much of a member entity a solution
# holds (rootcomponentbehavior: 0 with its subcomponents, 1 without, 2 as a shell).
_V9_COLUMNS = {
    "sitemap": ("sitemapnameunique",),
    "solution_membership": ("rootcomponentbehavior",),
}
_V9_READS = tuple(
    replace(r, attributes=(*r.attributes, *_V9_COLUMNS.get(r.kind, ()))) for r in _READS
)
# The kinds read per entity from the entity list; a failed list leaves them unread.
_ENTITY_DETAIL_KINDS = ("attribute", "relationship", "local_option_set", "ribbon")
# The reads a --solution run finds its scope from.
_SCOPE_KINDS = ("solution", "solution_membership")


_ETA_MIN_READS = 5  # units read (not reused) in a stage before its ETA is shown


@dataclass(frozen=True)
class Progress:
    """One progress event; `crm inventory extract` prints it as a stderr line.

    *done* counts units restored from the checkpoint too (*reused* of them).
    *total* is None where the stage has no countable total. *kind* and *pages*
    name the record read in progress. *eta* is an estimate in seconds, set only
    in a stage of uniform units once `_ETA_MIN_READS` of them were read this run.
    """

    stage: str
    done: int
    total: int | None
    kind: str | None = None
    pages: int | None = None
    reused: int = 0
    eta: float | None = None


@dataclass
class _Tally:
    """What one inventory kind's reads did: units read, reused, truncation, errors."""

    pages: int = 0
    reused: int = 0
    truncated: bool = False
    errors: list[str] = field(default_factory=lambda: list[str]())


@dataclass(frozen=True)
class ExtractSummary:
    """The short result `crm inventory extract` returns; the manifest holds the rest."""

    out: str
    manifest: str
    records: int
    kinds: dict[str, dict[str, Any]]
    gaps: int
    errors: list[str]
    reused: dict[str, int]
    resumed: bool
    complete: bool


class Checkpoint:
    """The finished units of one extraction, under ``<out>/.checkpoint/``."""

    def __init__(self, out: Path):
        self.root = out / CHECKPOINT_DIR

    @classmethod
    def open(
        cls,
        out: Path,
        org_url: str,
        now: str,
        solutions: tuple[str, ...] = (),
        system: str = "included",
    ) -> tuple[Checkpoint, dict[str, Any], bool]:
        """Open *out* for a new run or a resume; return the checkpoint, its state, and resumed.

        A folder holding files but no checkpoint is refused, so a finished
        inventory or unrelated files are never mixed into a new one, and so is
        a checkpoint of another org or another ``--solution`` scope.
        """
        cp = cls(out)
        state_file = cp.root / "state.json"
        scope = sorted(solutions)
        if state_file.is_file():
            state = cast("dict[str, Any]", json.loads(state_file.read_text(encoding="utf-8")))
            if state.get("org_url") != org_url:
                raise D365Error(f"--out {out} holds a checkpoint of another org; pass a new folder")
            if state.get("solutions", []) != scope:
                raise D365Error(
                    f"--out {out} holds a checkpoint of another scope"
                    f" ({', '.join(state.get('solutions', [])) or 'the whole org'});"
                    " pass the same --solution values or a new folder"
                )
            if state.get("system", "included") != system:
                raise D365Error(
                    f"--out {out} holds a checkpoint of another stock scope"
                    f" (system {state.get('system', 'included')}); pass the same --include-system"
                    " choice or a new folder"
                )
            return cp, state, True
        check_out(out)
        state = {"org_url": org_url, "started_at": now, "solutions": scope, "system": system}
        cp.save("state", state)
        return cp, state, False

    def _path(self, unit: str) -> Path:
        return self.root / f"{unit}.json"

    def load(self, unit: str) -> Any:
        path = self._path(unit)
        return json.loads(path.read_text(encoding="utf-8")) if path.is_file() else None

    def save(self, unit: str, data: object) -> None:
        """Save *unit* atomically, so a crash never leaves half a unit to reuse."""
        path = self._path(unit)
        path.parent.mkdir(parents=True, exist_ok=True)
        tmp = path.with_suffix(".tmp")
        tmp.write_text(json.dumps(data), encoding="utf-8")
        os.replace(tmp, path)

    def stage(self, kind: str, row_id: str, name: str, data: bytes) -> None:
        path = self.staged(kind, row_id, name)
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_bytes(data)

    def staged(self, kind: str, row_id: str, name: str) -> Path:
        return self.root / "payload" / kind / f"{row_id}.{name}"

    def remove(self) -> None:
        shutil.rmtree(self.root)


class _Run:
    def __init__(
        self,
        source: InventorySource,
        cp: Checkpoint,
        progress: Callable[[Progress], None] | None = None,
        clock: Callable[[], float] = time.monotonic,
    ):
        self.source = source
        self.cp = cp
        self.tallies: dict[str, _Tally] = {}
        self.errors: list[str] = []  # failed units that feed no kind (the source read)
        self._progress = progress
        self._clock = clock
        self._stage_name = ""
        self._total: int | None = None
        self._estimate = False
        self._done = self._reused = self._read = 0
        self._spent = 0.0

    def tally(self, kind: str) -> _Tally:
        return self.tallies.setdefault(kind, _Tally())

    def begin(self, stage: str, total: int | None = None, *, estimate: bool = False) -> None:
        """Start *stage* of *total* units (None: uncountable); *estimate* turns its ETA on."""
        self._stage_name, self._total, self._estimate = stage, total, estimate
        self._done = self._reused = self._read = 0
        self._spent = 0.0
        self._report()

    def _report(self, kind: str | None = None, pages: int | None = None) -> None:
        if self._progress is None:
            return
        eta = None
        if self._estimate and self._total is not None and self._read >= _ETA_MIN_READS:
            eta = self._spent / self._read * (self._total - self._done)
        self._progress(
            Progress(self._stage_name, self._done, self._total, kind, pages, self._reused, eta)
        )

    def tick(self, done: int, total: int) -> None:
        """Report *done* of *total* units of the open stage, for a probe that counts its own."""
        self._done, self._total = done, total
        self._report()

    def _finish(self, *, reused: bool, seconds: float = 0.0) -> None:
        self._done += 1
        if reused:
            self._reused += 1
        else:
            self._read += 1
            self._spent += seconds
        self._report()

    def unit(self, kinds: Iterable[str], unit: str, read: Callable[[], Any]) -> Any:
        """Return *unit*'s saved data, or read and save it; a per-item failure returns None."""
        tallies = [self.tally(k) for k in kinds]
        data = self.cp.load(unit)
        if data is not None:
            for t in tallies:
                t.pages += 1
                t.reused += 1
            self._finish(reused=True)
            return data
        began = self._clock()
        try:
            data = read()
        except D365Error as exc:
            if legacy_reads.connection_wide(exc):
                raise
            for t in tallies:
                t.errors.append(f"{unit}: {exc}")
            if not tallies:
                self.errors.append(f"{unit}: {exc}")
            self._finish(reused=False, seconds=self._clock() - began)
            return None
        self.cp.save(unit, data)
        for t in tallies:
            t.pages += 1
        self._finish(reused=False, seconds=self._clock() - began)
        return data

    def pages(self, read: _Read) -> list[dict[str, Any]]:
        """Page *read* to its end, reusing the pages a checkpoint holds."""
        tally = self.tally(read.kind)
        rows: list[dict[str, Any]] = []
        last: Page | None = None
        number = 1
        while (saved := self.cp.load(f"rm/{read.kind}/{number:05d}")) is not None:
            rows.extend(saved["rows"])
            last = Page(read.entity, number, [], saved["more"], saved["cookie"])
            tally.pages += 1
            tally.reused += 1
            number += 1
            self._report(read.kind, number - 1)
        fetched = False
        try:
            if last is None or (last.more and last.cookie is not None):
                for page in self.source.pages(read.fetch(), page_size=read.page_size, start=last):
                    page_rows = [self._stage(read, r) for r in page.records]
                    self.cp.save(
                        f"rm/{read.kind}/{page.number:05d}",
                        {"rows": page_rows, "more": page.more, "cookie": page.cookie},
                    )
                    rows.extend(page_rows)
                    last = page
                    tally.pages += 1
                    fetched = True
                    self._report(read.kind, tally.pages)
        except (D365Error, ValueError) as exc:
            # ValueError: web resource or assembly content that is not base64.
            if isinstance(exc, D365Error) and legacy_reads.connection_wide(exc):
                raise
            failed = last.number + 1 if last is not None else 1
            tally.errors.append(f"{read.entity} page {failed}: {exc}")
        if last is not None and last.more and last.cookie is None:
            tally.truncated = True
            tally.errors.append(
                f"{read.entity} page {last.number} reports more records but carries no paging"
                " cookie"
            )
        self._finish(reused=last is not None and not fetched)
        return rows

    def content(self, read: _Read, rows: list[dict[str, Any]], ids: set[str]) -> None:
        """Fetch and stage *read*'s payloads for the rows of *rows* named in *ids*.

        The second pass of a scoped read, whose *rows* came without payloads. Ids go
        in batches, each saved as a unit so a resume skips the finished ones, and the
        pass counts as one unit of the records stage, as a read's pages do. The kind's
        coverage `pages` stays the rows' own pages, so a scoped manifest is unchanged.
        """
        tally = self.tally(read.kind)
        by_id = {str(r["id"]).lower(): r for r in rows}
        wanted = sorted(i for i in ids if i in by_id)
        size = read.content_batch
        fetched = False
        for number, start in enumerate(range(0, len(wanted), size), 1):
            unit = f"content/{read.kind}/{number:05d}"
            batch = wanted[start : start + size]
            saved = self.cp.load(unit)
            # A unit is reused only for the ids it was fetched for: a retry that
            # finds more members repartitions the batches.
            names = saved["names"] if saved and saved.get("ids") == batch else None
            if names is None:
                try:
                    names = self._fetch_content(read, batch, by_id)
                except (D365Error, ValueError) as exc:
                    # ValueError: content that is not base64, as in `pages`.
                    if isinstance(exc, D365Error) and legacy_reads.connection_wide(exc):
                        raise
                    tally.errors.append(f"{read.entity} content batch {number}: {exc}")
                    continue
                self.cp.save(unit, {"ids": batch, "names": names})
                fetched = True
            else:
                tally.reused += 1
            for row_id, artifacts in names.items():
                by_id[row_id]["_artifacts"] = artifacts
            self._report(read.kind, tally.pages)
        self._finish(reused=not fetched)

    def _fetch_content(
        self, read: _Read, batch: list[str], by_id: dict[str, dict[str, Any]]
    ) -> dict[str, list[str]]:
        values = "".join(f"<value>{i}</value>" for i in batch)
        query = replace(
            read,
            attributes=read.payload_columns,
            extra=f'<filter><condition attribute="{read.key_attribute}" operator="in">{values}'
            "</condition></filter>",
        )
        staged: dict[str, list[str]] = {}
        for page in self.source.pages(query.fetch(), page_size=len(batch), start=None):
            for fetched in page.records:
                row = by_id.get(str(fetched["id"]).lower())
                if row is None:
                    continue
                # `_stage` names a web resource's artifact from its type, which the rows
                # read without payloads still carry.
                carrier: dict[str, object] = {
                    "id": row["id"],
                    "webresourcetype": row.get("webresourcetype"),
                }
                for column in read.payload_columns:
                    carrier[column] = fetched.get(column)
                staged[str(row["id"]).lower()] = cast(
                    "list[str]", self._stage(read, carrier)["_artifacts"]
                )
        return staged

    def _stage(self, read: _Read, row: dict[str, object]) -> dict[str, object]:
        """Move *row*'s payloads into staged files, leaving the artifact names on it."""
        staged: list[str] = []
        for column, name in read.payloads:
            value = row.pop(column, None)
            if not isinstance(value, str) or not value:
                continue
            if read.kind == "web_resource":
                code = row.get("webresourcetype")
                name = (
                    f"content.{WEB_RESOURCE_EXT.get(code if isinstance(code, int) else 0, 'bin')}"
                )
                data = base64.b64decode(value, validate=True)
            elif read.kind == "plugin_assembly":
                data = base64.b64decode(value, validate=True)
            else:
                data = value.encode("utf-8")
            self.cp.stage(read.kind, str(row["id"]), name, data)
            staged.append(name)
        row["_artifacts"] = staged
        return row


def _utc_now() -> str:
    return datetime.now(UTC).strftime("%Y-%m-%dT%H:%M:%SZ")


def _ribbon_custom(path: Path) -> bool:
    """Whether the ribbon XML at *path* holds an entry the platform does not ship."""
    text = path.read_text(encoding="utf-8", errors="replace")
    return bool(_RIBBON_HIDE.search(text)) or any(
        re.split(r"[.\-_]", i, maxsplit=1)[0].lower() not in _STOCK_RIBBON_IDS
        for i in _RIBBON_ID.findall(text)
    )


def _managed_ids(solutions: list[dict[str, Any]]) -> set[str]:
    """The ids of the managed solutions among the solution rows."""
    return {str(r["id"]).lower() for r in solutions if r.get("id") and r.get("ismanaged") is True}


def _kept_ids(rows: list[dict[str, Any]], solutions: list[dict[str, Any]]) -> set[str]:
    """The ids of *rows* that are not stock: the ones whose payloads the scope fetches."""
    managed = _managed_ids(solutions)
    return {str(r["id"]).lower() for r in rows if not _is_stock(r, managed)}


def _is_stock(row: dict[str, Any], managed_solutions: set[str]) -> bool:
    """A row is stock when it is managed or owned by a managed solution.

    A row with no ``ismanaged`` is kept: the source gave nothing to drop it by.
    """
    flag = row.get("ismanaged")
    if flag is None:
        return False
    return flag is True or (_ref_id(row.get("solutionid")) or "") in managed_solutions


def _scope_members(
    run: _Run, rows: dict[str, list[dict[str, Any]]], solutions: tuple[str, ...]
) -> tuple[dict[int, set[str]], tuple[str, ...]]:
    """The ids the named solutions hold by component type, and the names as the org spells them.

    Unique names match case-insensitively, as on the server; an unknown name is an error.
    """
    ids = {
        str(r["id"]).lower(): str(r.get("uniquename"))
        for r in rows.get("solution", [])
        if str(r.get("uniquename")).lower() in solutions
    }
    spelled = {name.lower(): name for name in ids.values()}
    for name in solutions:
        if name not in spelled:
            run.tally("solution").errors.append(
                f"solution {name}: no solution with this unique name"
            )
    members: dict[int, set[str]] = {}
    for r in rows.get("solution_membership", []):
        code, member = r.get("componenttype"), _ref_id(r.get("objectid"))
        if isinstance(code, int) and member and (_ref_id(r.get("solutionid")) or "") in ids:
            members.setdefault(code, set()).add(member)
    return members, tuple(sorted(spelled.get(name, name) for name in solutions))


def extract(
    source: InventorySource,
    out: Path,
    *,
    cli_version: str,
    solutions: tuple[str, ...] = (),
    include_system: bool = False,
    now: Callable[[], str] = _utc_now,
    progress: Callable[[Progress], None] | None = None,
    clock: Callable[[], float] = time.monotonic,
    with_usage: bool = False,
    usage_time_cap: float = JOB_TIME_CAP,
) -> ExtractSummary:
    """Extract the org's migration inventory into *out*, resuming a checkpoint there.

    *solutions* narrows the inventory to those solutions (by unique name):
    their records, their memberships, the components they hold and the
    subcomponents of the entities they hold. Entity details and ribbons are
    read only for the member entities; the org-wide reads still run, since a
    membership names its component only by id, but a web resource's content and
    an assembly's binary are fetched for the members only. An entity held as a shell or
    without subcomponents brings only the subcomponents the solutions list on
    their own.

    By default the inventory holds the migration scope: stock components (managed,
    or owned by a managed solution) are left out, and their payloads are never
    fetched. *include_system* keeps them. A ``--solution`` run names its scope
    itself and keeps stock, so *include_system* has no effect on it.

    *with_usage* adds a `usage` to each entity, plug-in assembly, type and step and
    process row, after the kept rows are known (`_attach_usage`); *usage_time_cap*
    bounds each capped CRM 2011 async read, in seconds.

    *progress* receives a `Progress` event as each stage starts and each unit
    finishes; *clock* (monotonic seconds) times the units for its ETA.

    Raises:
        D365Error: *out* cannot be used, or a connection-wide failure stopped
            the read (the checkpoint keeps every finished unit for a re-run).
    """
    solutions = tuple(sorted({name.lower() for name in solutions}))
    stock_scope = not include_system and not solutions
    system = "excluded" if stock_scope else "included"
    cp, state, resumed = Checkpoint.open(out, source.org_url, now(), solutions, system)
    run = _Run(source, cp, progress, clock)
    run.begin("source", 3)
    ident = cast("dict[str, Any]", run.unit((), "source", source.identity) or {})
    entities = cast(
        "list[dict[str, Any]] | None", run.unit(("entity",), "entities", source.list_entities)
    )
    if entities is None:
        for kind in _ENTITY_DETAIL_KINDS:
            run.tally(kind).errors.append("entities: the entity list read failed")
        entities = []
    option_sets = cast(
        "list[dict[str, Any]]",
        run.unit(("global_option_set",), "option-sets", source.list_option_sets) or [],
    )
    reads = _READS if source.platform == PLATFORM_CRM2011 else _V9_READS
    # A scoped run reads the member-payload kinds without their payloads and fetches the
    # members' payloads once the scope is known, so the scope's own reads run first.
    deferred = [
        r
        for r in reads
        if (solutions and r.member_type is not None) or (stock_scope and r.payloads)
    ]
    first = [r.without_payloads() if r in deferred else r for r in reads]
    if solutions:
        first.sort(key=lambda r: r.kind not in _SCOPE_KINDS)
    run.begin("records", len(reads) + len(deferred))
    rows = {read.kind: run.pages(read) for read in first}
    if stock_scope:
        gone = run.tally("solution")
        if gone.errors or gone.truncated:
            for kind in sorted({r.kind for r in deferred}):
                run.tally(kind).errors.append(
                    "solution: the read fell short, so the stock scope may keep components"
                    " owned by a managed solution"
                )
        for read in deferred:
            run.content(read, rows[read.kind], _kept_ids(rows[read.kind], rows["solution"]))
    if solutions:
        # The scope is found from these two reads: if either fell short, the kinds it
        # filters may be missing components, so none of them can read complete. That
        # takes in the membership rows (each needs its solution's name) but not the
        # solution records, which are kept by name alone.
        short = [k for k in _SCOPE_KINDS if run.tally(k).errors or run.tally(k).truncated]
        if short:
            for kind in sorted(set(KINDS) - {"solution", *short}):
                run.tally(kind).errors.append(
                    f"{', '.join(short)}: the read fell short, so the --solution scope"
                    " may miss components"
                )
        members, solutions = _scope_members(run, rows, solutions)
        for read in deferred:
            run.content(read, rows[read.kind], members.get(cast("int", read.member_type), set()))
        entities_read = [
            e for e in entities if str(e.get("metadata_id")).lower() in members.get(_ENTITY, set())
        ]
    else:
        entities_read = entities
    details: list[dict[str, Any]] = []
    run.begin("entity_detail", len(entities_read), estimate=True)
    for entity in entities_read:
        name = str(entity["logical_name"])
        detail = run.unit(
            ("attribute", "relationship", "local_option_set"),
            f"entity/{name}",
            lambda name=name: source.entity_detail(name),
        )
        if detail is not None:
            details.append(detail)
    ribbons: dict[str | None, Path] = {}
    # An entity with no display label (measured on UR15) or no collection label (a
    # cloud org) faults its ribbon read ("Could not find localized ... name"), so it
    # is not read.
    labelled = [str(e["logical_name"]) for e in entities_read if _ribbon_readable(e)]
    unlabelled = sorted(str(e["logical_name"]) for e in entities_read if not _ribbon_readable(e))
    # The application ribbon belongs to no solution's entities, so a scoped run skips it.
    ribbon_units = [*([] if solutions else [None]), *labelled]
    run.begin("ribbons", len(ribbon_units), estimate=True)
    for entity_name in ribbon_units:
        unit = f"ribbon/{entity_name or '_application'}"

        def read_ribbon(entity_name: str | None = entity_name) -> str:
            xml = source.ribbon_xml(entity_name)
            cp.stage("ribbon", entity_name or "_application", "ribbon.xml", xml)
            return "ribbon.xml"

        if run.unit(("ribbon",), unit, read_ribbon) is not None:
            ribbons[entity_name] = cp.staged("ribbon", entity_name or "_application", "ribbon.xml")

    diff_units, diff_unsupported_reason = _read_ribbon_diffs(source, cp, run, solutions)

    run.begin("build")
    built = _Builder(cp, out, rows, source.api, stock_scope)
    built.metadata(entities, option_sets, details)
    built.components()
    # A custom (unmanaged) entity's ribbon is the entity's own, whatever ids it holds.
    system_entities = {
        e["logical_name"] for e in entities if not e["custom"] or e.get("managed") is True
    }
    for entity_name, ribbon in ribbons.items():
        built.add(
            "ribbon",
            None,
            {"entity": entity_name},
            None,
            "unknown",  # the ribbon XML merges the system ribbon and customizations
            None,
            [("ribbon.xml", ribbon, True)],
            stock=stock_scope
            and (entity_name is None or entity_name in system_entities)
            and not _ribbon_custom(ribbon),
        )
    seen: set[str | None] = set()
    for solution, diff_entities in diff_units:
        for entity_name in diff_entities:
            if entity_name in seen:
                continue  # the first solution to hold an entity's diff wins
            seen.add(entity_name)
            staged = cp.staged("ribbon_diff", _diff_id(solution, entity_name), "ribbon-diff.xml")
            built.add(
                "ribbon_diff",
                None,
                {"entity": entity_name},
                None,
                "unknown",  # the diff layers a solution's changes over the stock ribbon
                None,
                [("ribbon-diff.xml", staged, True)],
            )
    built.memberships_and_dependencies()
    if solutions:
        built.scope(solutions)
    if stock_scope:
        built.drop_stock()
    records = built.finish()
    usage_gaps: list[ExtractionGap] = []
    usage_errors: list[str] = []
    if with_usage:
        records = _attach_usage(
            source, cp, run, records, usage_time_cap, now(), usage_gaps, usage_errors
        )

    coverage: dict[str, KindCoverage] = {}
    counts: dict[str, int] = {}
    for record in records:
        counts[record.kind] = counts.get(record.kind, 0) + 1
    for kind in KINDS:
        tally = run.tallies.get(kind, _Tally())
        n = counts.get(kind, 0)
        if tally.errors and n == 0:
            status = "failed"
        elif tally.errors or tally.truncated:
            status = "partial"
        else:
            status = "complete"
        coverage[kind] = KindCoverage(status, tally.pages, n, tally.truncated, tuple(tally.errors))
    if diff_unsupported_reason:
        coverage["ribbon_diff"] = KindCoverage(
            "unsupported", 0, 0, False, (diff_unsupported_reason,)
        )

    inventory = Inventory(
        schema_version=SCHEMA_VERSION,
        source=Source(
            platform=source.platform,
            org_url=source.org_url,
            organization_id=ident.get("organization_id"),
            version=ident.get("version") or "unknown",
        ),
        extracted_at=state["started_at"],
        cli_version=cli_version,
        scope=Scope(
            mode="solutions" if solutions else "org",
            solutions=solutions,
            system=system,
            usage_read=with_usage,
        ),
        publication="published",
        records=tuple(records),
        coverage=coverage,
        gaps=(
            *source.static_gaps,
            *built.gaps(),
            *_ribbon_gaps(unlabelled),
            *_ribbon_diff_gaps(diff_unsupported_reason),
            *_source_gaps(run.errors),
            *usage_gaps,
        ),
    )
    (out / MANIFEST_NAME).write_text(dump_manifest(inventory), encoding="utf-8")

    errors = [*run.errors, *usage_errors, *(e for t in run.tallies.values() for e in t.errors)]
    complete = not errors and all(
        c.status in ("complete", "unsupported") for c in coverage.values()
    )
    if complete:
        cp.remove()
    return ExtractSummary(
        out=str(out),
        manifest=str(out / MANIFEST_NAME),
        records=len(records),
        kinds={k: {"status": c.status, "records": c.records} for k, c in coverage.items()},
        gaps=len(inventory.gaps),
        errors=errors,
        reused={k: t.reused for k, t in sorted(run.tallies.items()) if t.reused},
        resumed=resumed,
        complete=complete,
    )


_DEFAULT_SOLUTION = "Default"


def _diff_id(solution: str, entity: str | None) -> str:
    return f"{solution}.{entity or '_application'}"


def _read_ribbon_diffs(
    source: InventorySource, cp: Checkpoint, run: _Run, solutions: tuple[str, ...]
) -> tuple[list[tuple[str, list[str | None]]], str | None]:
    """Export each solution in scope and keep the ribbon diffs that hold content.

    The scope is the named solutions, else the Default solution, which holds every
    customization. One checkpoint unit per solution: its zip is staged on disk, streamed
    and deleted, and only the diffs are kept. Returns each solution's entities (``None``
    is the application) and, when the platform cannot export the scope, why not.
    """
    if not solutions and not source.default_export_supported:
        return [], (
            f"not read: {source.api} refuses to export the Default solution, which holds"
            " the whole org's ribbon diffs; run with --solution to read the named solutions'"
        )
    names = solutions or (_DEFAULT_SOLUTION,)
    run.begin("ribbon_diffs", len(names), estimate=True)
    units: list[tuple[str, list[str | None]]] = []
    for index, name in enumerate(names):

        def read(name: str = name, index: int = index) -> list[str | None]:
            # The file name is ours, never the user's solution name: a name like
            # ``/tmp/backup`` must not point the export (or its cleanup) outside the checkpoint.
            zip_path = cp.root / "tmp" / f"export-{index}.zip"
            zip_path.parent.mkdir(parents=True, exist_ok=True)
            try:
                source.export_solution(name, zip_path)
                diffs = read_ribbon_diffs(zip_path)
            # zlib.error and RuntimeError (encrypted) are what a damaged member raises
            # past BadZipFile.
            except (
                OSError,
                KeyError,
                ValueError,
                RuntimeError,
                zlib.error,
                zipfile.BadZipFile,
                ParseError,
            ) as exc:
                raise D365Error(f"the export of {name} could not be read: {exc}") from exc
            finally:
                zip_path.unlink(missing_ok=True)
            for entity, xml in diffs.items():
                cp.stage("ribbon_diff", _diff_id(name, entity), "ribbon-diff.xml", xml)
            return list(diffs)

        data = run.unit(("ribbon_diff",), f"ribbon-diff/{name}", read)
        if data is not None:
            units.append((name, data))
    return units, None


def _ribbon_diff_gaps(unsupported_reason: str | None) -> list[ExtractionGap]:
    if not unsupported_reason:
        return []
    return [
        ExtractionGap(
            kind="ribbon_diff",
            key=None,
            description="the ribbon diffs of the org-wide customizations",
            reason=unsupported_reason,
        )
    ]


_USAGE_LABEL = {
    "entity": "entities",
    "plugins": "plug-in assemblies, types and steps",
    "process": "processes",
}


def _attach_usage(
    source: InventorySource,
    cp: Checkpoint,
    run: _Run,
    records: list[Record],
    time_cap: float,
    as_of: str,
    gaps: list[ExtractionGap],
    errors: list[str],
) -> list[Record]:
    """Give each row of a kind with a runtime signal its `usage` (`--with-usage`).

    One probe per group of kinds, run after the kept rows are known so a stock-excluded run
    never probes a stock entity, and each group's answer is a checkpoint unit so a resume
    skips a finished group. A row the answer lacks is `unknown`, never `unused`. A group whose
    probe fails (not connection-wide) leaves its rows `unknown` with the reason, a gap and
    an error (the run goes on and the failed group is probed again on a resume); no answer
    is saved for it.
    """
    out: dict[int, dict[str, Any]] = {}
    for group, (kinds, signal) in INVENTORY_GROUPS.items():
        rows = [i for i, r in enumerate(records) if r.kind in kinds]
        if not rows:
            continue
        unit = f"usage/{group}"
        names: set[str] = {records[i].key for i in rows} if group == "entity" else set()
        total = len(rows) if group == "entity" else 1
        run.begin(f"usage-{group}", total)
        saved = cp.load(unit)
        # A resumed scope can gain entities the saved answer never probed: read again.
        if saved is not None and not names <= set(saved["names"]):
            saved = None
        why = "not in the usage read"
        if saved is not None:
            answer = saved["answer"]
        else:
            try:
                got = source.probe_usage(group, names=names, time_cap=time_cap, progress=run.tick)
            except D365Error as exc:
                if legacy_reads.connection_wide(exc):
                    raise
                why = f"usage probe failed: {exc}"
                gaps.append(
                    ExtractionGap(
                        kind=None,
                        key=None,
                        description=f"usage of {_USAGE_LABEL[group]}",
                        reason=why,
                    )
                )
                errors.append(f"usage/{group}: {exc}")
                answer = {}
            else:
                answer = {k: {i: u.to_dict() for i, u in v.items()} for k, v in got.items()}
                cp.save(unit, {"names": sorted(names), "answer": answer})
        run.tick(total, total)
        found = {
            k: {(i if k == "entity" else i.lower()): v for i, v in per.items()}
            for k, per in answer.items()
        }
        for i in rows:
            r = records[i]
            key = r.key if r.kind == "entity" else (r.id or "").lower()
            out[i] = (
                found.get(r.kind, {}).get(key)
                or Usage("unknown", signal, None, as_of, why).to_dict()
            )
    return [replace(r, usage=out[i]) if i in out else r for i, r in enumerate(records)]


def _file_hashes(path: Path, *, text: bool, ribbon: bool = False) -> ArtifactHashes:
    """`artifact_hashes` of a file; a binary one is streamed rather than held in memory."""
    if text:
        return artifact_hashes(path.read_bytes(), text=True, ribbon=ribbon)
    with path.open("rb") as handle:
        raw = hashlib.file_digest(handle, "sha256").hexdigest()
    return ArtifactHashes(sha256=raw, normalized_sha256=raw)


def _ref_id(value: object) -> str | None:
    if isinstance(value, dict):
        ref = cast("dict[str, Any]", value).get("id")
        return ref.lower() if isinstance(ref, str) else None
    return value.lower() if isinstance(value, str) else None


def _ref_name(value: object) -> str | None:
    """The display name an EntityReference column carries, or None."""
    if isinstance(value, dict):
        name = cast("dict[str, Any]", value).get("name")
        return name if isinstance(name, str) else None
    return None


def _entity_name(value: object) -> str | None:
    """An entity-name column; ``none`` (no entity) is null."""
    return None if value in (None, "", "none") else str(value)


@dataclass
class _Pending:
    kind: str
    key: str
    id: str | None
    display_name: str | None
    origin: str
    managed: bool | None
    fields: dict[str, Any]
    payloads: list[tuple[str, Path, bool]]
    stock: bool = False


def _stock_origin(parent: _Pending) -> None:
    """A managed row kept only as a reference parent is platform stock, never `custom`."""
    if parent.managed is True and parent.origin == "custom":
        parent.origin = "system"


class _Builder:
    """Turns the units' rows into records, resolving cross-kind keys and finding gaps."""

    def __init__(
        self,
        cp: Checkpoint,
        out: Path,
        rows: dict[str, list[dict[str, Any]]],
        api: str,
        stock_scope: bool = False,
    ):
        self.cp = cp
        self.stock_scope = stock_scope
        self.no_managed: Counter[str] = Counter()
        self.api = api
        self.out = out
        self.rows = rows
        self.pending: list[_Pending] = []
        # (component type, object id) -> (kind, key), for memberships and dependencies.
        self.index: dict[tuple[int, str], tuple[str, str]] = {}
        self.by_id: dict[str, dict[str, str]] = {}
        # Rows naming a component this run did not record: (kind, component type,
        # the record a scoped run keeps the row with, or None).
        self.unresolved: list[tuple[str, int, tuple[str, str] | None]] = []
        # (solution, entity key) of entities a solution holds without their subcomponents.
        self.shells: set[tuple[str, str]] = set()
        self.scoped = False
        self.unkeyed: dict[str, tuple[int, str]] = {}  # kind: (rows, the first error)
        self.extra_gaps: list[ExtractionGap] = []
        # ISV page -> the (kind, key) of each record referencing it.
        self.isv: dict[str, set[tuple[str, str]]] = {}
        self.isv_spelling: dict[str, str] = {}
        solutions = rows.get("solution", [])
        self.solution_names = {
            str(r["id"]).lower(): str(r.get("uniquename")) for r in solutions if r.get("id")
        }
        self.managed_solutions = _managed_ids(solutions)
        # (kind, the stock kind its row names) -> rows the stock scope left an edge to.
        self.platform_edges: Counter[tuple[str, str]] = Counter()
        # Stock plug-in rows by (kind, source id): stock flows down assembly, type, step, image.
        self.stock_ids: set[tuple[str, str]] = set()

    def add(
        self,
        kind: str,
        row_id: str | None,
        fields: dict[str, Any],
        display_name: str | None,
        origin: str,
        managed: bool | None,
        payloads: list[tuple[str, Path, bool]],
        component_type: int | None = None,
        stock: bool = False,
    ) -> str | None:
        """Queue a record and index its key; None when a key field's value breaks the contract.

        *stock* marks a row the stock scope leaves out; an entity, attribute,
        relationship or option set is stock by its own flags instead (not custom, or managed).

        Org data can hold what the contract cannot key (a step with an empty
        name, measured on a Dataverse trial org): such a row is counted into one
        gap per kind rather than failing the whole run.

        Raises:
            InventoryError: an unknown kind or a missing key field, a builder bug.
        """
        try:
            key = component_key(kind, fields)
        except KeyFieldValueError as exc:
            count, first = self.unkeyed.get(kind, (0, str(exc)))
            self.unkeyed[kind] = (count + 1, first)
            return None
        if kind in _METADATA_KINDS:
            stock = origin == "system" or managed is True
        self.pending.append(
            _Pending(kind, key, row_id, display_name, origin, managed, fields, payloads, stock)
        )
        if row_id:
            self.by_id.setdefault(kind, {})[row_id.lower()] = key
            if component_type is not None:
                self.index[(component_type, row_id.lower())] = (kind, key)
        return key

    def _stock(self, kind: str, row: dict[str, Any]) -> bool:
        """Whether the stock scope drops *row* (a kind with an ``ismanaged`` column)."""
        if not self.stock_scope:
            return False
        if row.get("ismanaged") is None:
            self.no_managed[kind] += 1
        return _is_stock(row, self.managed_solutions)

    def _chain_stock(
        self, kind: str, row: dict[str, Any], parent: tuple[str, object] | None = None
    ) -> bool:
        """Stock by the row's own test, or because its parent in the plug-in chain is stock.

        Dataverse marks platform steps and types unmanaged, so only the assembly tells.
        """
        stock = self._stock(kind, row) or (
            parent is not None and (parent[0], _ref_id(parent[1]) or "") in self.stock_ids
        )
        if stock:
            self.stock_ids.add((kind, str(row["id"]).lower()))
        return stock

    def _origin(self, row: dict[str, Any]) -> str:
        solution = _ref_id(row.get("solutionid"))
        if solution is None or solution not in self.solution_names:
            return "unknown"
        return "system" if self.solution_names[solution] == "System" else "custom"

    def _staged(
        self, kind: str, row: dict[str, Any], text: bool = True
    ) -> list[tuple[str, Path, bool]]:
        names = cast("list[str]", row.get("_artifacts", []))
        return [(n, self.cp.staged(kind, str(row["id"]), n), text) for n in names]

    def metadata(
        self,
        entities: list[dict[str, Any]],
        option_sets: list[dict[str, Any]],
        details: list[dict[str, Any]],
    ) -> None:
        for e in entities:
            self.add(
                "entity", e.get("metadata_id"),
                {"logical_name": e["logical_name"], "object_type_code": e["object_type_code"]},
                e.get("display_name"), "custom" if e["custom"] else "system", e.get("managed"),
                [], 1,
            )  # fmt: skip
        for s in option_sets:
            self.add(
                "global_option_set", s.get("metadata_id"),
                {"name": s["name"], "type": s["type"], "options": s["options"]},
                None, "custom" if s["custom"] else "system",
                s.get("managed") if self.stock_scope else None,  # a full run records what it did
                [], _OPTION_SET,
            )  # fmt: skip
        seen_relationships: set[str] = set()
        for d in details:
            entity = d["logical_name"]
            local_sets = {s["name"]: s for s in d["option_sets"] if not s["global"]}
            for a in d["attributes"]:
                self.add(
                    "attribute", a.get("metadata_id"),
                    {"entity": entity, "logical_name": a["logical_name"], "type": a["type"],
                     "required_level": a["required_level"], "option_set": a["option_set"]},
                    None, "custom" if a["custom"] else "system", a.get("managed"), [], 2,
                )  # fmt: skip
                local = local_sets.get(a["option_set"] or "")
                if local is not None:
                    self.add(
                        "local_option_set", local.get("metadata_id"),
                        {"entity": entity, "attribute": a["logical_name"], "name": local["name"],
                         "type": local["type"], "options": local["options"]},
                        None, "custom" if a["custom"] else "system", a.get("managed"), [],
                        _OPTION_SET,
                    )  # fmt: skip
            rels = d["relationships"]
            for shape, items in (
                ("one_to_many", rels["one_to_many"] + rels["many_to_one"]),
                ("many_to_many", rels["many_to_many"]),
            ):
                for r in items:
                    if r["schema_name"] in seen_relationships:
                        continue
                    seen_relationships.add(r["schema_name"])
                    fields = {
                        k: v for k, v in r.items() if k not in ("metadata_id", "custom", "managed")
                    }
                    self.add(
                        "relationship", r.get("metadata_id"), {**fields, "type": shape},
                        None, "custom" if r["custom"] else "system", r.get("managed"), [], 10,
                    )  # fmt: skip

    def components(self) -> None:
        rows = self.rows
        for r in rows.get("solution", []):
            visible = r.get("isvisible") is not False and r.get("uniquename") != "Default"
            self.add(
                "solution", r["id"],
                {"unique_name": r["uniquename"], "version": r.get("version"),
                 "publisher": _ref_name(r.get("publisherid"))},
                r.get("friendlyname"), "custom" if visible else "system", r.get("ismanaged"), [],
            )  # fmt: skip
        for r in rows.get("web_resource", []):
            code = r.get("webresourcetype")
            text = code in _TEXT_WEB_RESOURCES
            payloads = self._staged("web_resource", r, text)
            key = self.add(
                "web_resource", r["id"], {"name": r["name"], "web_resource_type": code},
                r.get("displayname"), self._origin(r), r.get("ismanaged"), payloads, 61,
                self._stock("web_resource", r),
            )  # fmt: skip
            for _, path, is_text in payloads:
                if is_text and key is not None:
                    self._scan_isv(path.read_bytes(), ("web_resource", key))
        for r in rows.get("plugin_assembly", []):
            source = r.get("sourcetype")
            key = self.add(
                "plugin_assembly", r["id"],
                {"name": r["name"], "version": r.get("version"), "culture": r.get("culture"),
                 "public_key_token": r.get("publickeytoken"), "source_type": source,
                 "isolation_mode": r.get("isolationmode")},
                r.get("name"), self._origin(r), r.get("ismanaged"),
                self._staged("plugin_assembly", r, text=False), 91,
                self._chain_stock("plugin_assembly", r),
            )  # fmt: skip
            if key is not None and source != 0:
                where = {1: "on disk", 2: "in the GAC"}.get(
                    source if isinstance(source, int) else -1
                )
                self.extra_gaps.append(
                    ExtractionGap(
                        kind="plugin_assembly", key=key, description=None,
                        reason=f"the assembly binary is stored {where or 'outside the database'}"
                        f" on the CRM server, not in the database, so {self.api} cannot read it",
                    )
                )  # fmt: skip
        assemblies = {str(r["id"]).lower(): str(r["name"]) for r in rows.get("plugin_assembly", [])}
        for r in rows.get("plugin_type", []):
            ref = r.get("pluginassemblyid")
            assembly = assemblies.get(_ref_id(ref) or "") or _ref_name(ref)
            if not assembly:
                self._unresolve("plugin_type", 91, None)
                continue
            self.add(
                "plugin_type", r["id"],
                {"assembly": assembly, "type_name": r["typename"],
                 "workflow_activity": r.get("isworkflowactivity")},
                r.get("friendlyname"), self._origin(r), r.get("ismanaged"), [], 90,
                self._chain_stock("plugin_type", r, ("plugin_assembly", r.get("pluginassemblyid"))),
            )  # fmt: skip
        types = self.by_id.get("plugin_type", {})
        for r in rows.get("step", []):
            plugin_type = types.get(_ref_id(r.get("plugintypeid")) or "")
            message = r.get("message.name")
            if plugin_type is None or not message:
                self._unresolve("step", 90, None)
                continue
            self.add(
                "step", r["id"],
                {"message": message,
                 "primary_entity": _entity_name(r.get("filter.primaryobjecttypecode")),
                 "stage": r["stage"], "mode": r["mode"], "plugin_type": plugin_type,
                 "name": r["name"], "rank": r.get("rank"),
                 "filtering_attributes": r.get("filteringattributes"),
                 "state": r.get("statecode"), "supported_deployment": r.get("supporteddeployment")},
                r.get("name"), self._origin(r), r.get("ismanaged"), [], 92,
                self._chain_stock("step", r, ("plugin_type", r.get("plugintypeid"))),
            )  # fmt: skip
        steps = self.by_id.get("step", {})
        for r in rows.get("step_image", []):
            step = steps.get(_ref_id(r.get("sdkmessageprocessingstepid")) or "")
            if step is None:
                self._unresolve("step_image", 92, None)
                continue
            self.add(
                "step_image", r["id"],
                {"step": step, "alias": r["entityalias"], "image_type": r["imagetype"],
                 "attributes": r.get("attributes"),
                 "message_property": r.get("messagepropertyname")},
                r.get("name"), self._origin(r), r.get("ismanaged"), [], 93,
                self._chain_stock(
                    "step_image", r, ("step", r.get("sdkmessageprocessingstepid"))
                ),
            )  # fmt: skip
        for r in rows.get("process", []):
            self.add(
                "process", r["id"],
                {"category": r["category"], "primary_entity": _entity_name(r.get("primaryentity")),
                 "name": r["name"], "state": r.get("statecode")},
                r.get("name"), self._origin(r), r.get("ismanaged"), self._staged("process", r), 29,
                self._stock("process", r),
            )  # fmt: skip
        for r in rows.get("form", []):
            self.add(
                "form", r["id"],
                {"entity": r["objecttypecode"], "form_type": r["type"], "name": r["name"]},
                r.get("name"), self._origin(r), r.get("ismanaged"), self._staged("form", r), 60,
                self._stock("form", r),
            )  # fmt: skip
        for r in rows.get("view", []):
            self.add(
                "view", r["id"],
                {"entity": r["returnedtypecode"], "name": r["name"],
                 "query_type": r.get("querytype")},
                r.get("name"), self._origin(r), r.get("ismanaged"), self._staged("view", r), 26,
                self._stock("view", r),
            )  # fmt: skip
        for r in rows.get("sitemap", []):
            payloads = self._staged("sitemap", r)
            key = self.add(
                "sitemap",
                r["id"],
                {"unique_name": r.get("sitemapnameunique") or None},
                None,
                self._origin(r),
                r.get("ismanaged"),
                payloads,
                62,
                self._stock("sitemap", r),
            )
            for _, path, _ in payloads:
                if key is not None:
                    self._scan_isv(path.read_bytes(), ("sitemap", key))

    def _scan_isv(self, data: bytes, where: tuple[str, str]) -> None:
        for match in _ISV_PAGE.finditer(data.decode("utf-8", errors="replace")):
            # One gap per page, spelled as first seen (IIS paths are case-insensitive).
            page = self.isv_spelling.setdefault(match.group(0).lower(), match.group(0))
            self.isv.setdefault(page, set()).add(where)

    def _unresolve(self, kind: str, component_type: int, owner: tuple[str, str] | None) -> None:
        self.unresolved.append((kind, component_type, owner))

    def _resolve(self, component_type: object, object_id: object) -> tuple[str, str] | None:
        oid = _ref_id(object_id)
        if not isinstance(component_type, int) or oid is None:
            return None
        return self.index.get((component_type, oid))

    def memberships_and_dependencies(self) -> None:
        for r in self.rows.get("solution_membership", []):
            solution = self.solution_names.get(_ref_id(r.get("solutionid")) or "")
            member = self._resolve(r.get("componenttype"), r.get("objectid"))
            if solution is None or member is None:
                owner = ("solution", solution) if solution else None
                self._unresolve("solution_membership", int(r.get("componenttype") or 0), owner)
                continue
            if member[0] == "entity" and r.get("rootcomponentbehavior") in (1, 2):
                self.shells.add((solution, member[1]))
            self.add(
                "solution_membership", r["id"],
                {"solution": solution, "member_kind": member[0], "member_key": member[1]},
                None, "unknown", None, [],
            )  # fmt: skip
        for r in self.rows.get("dependency", []):
            dependent = self._resolve(
                r.get("dependentcomponenttype"), r.get("dependentcomponentobjectid")
            )
            required = self._resolve(
                r.get("requiredcomponenttype"), r.get("requiredcomponentobjectid")
            )
            if dependent is None or required is None:
                code = (
                    r.get("requiredcomponenttype") if dependent else r.get("dependentcomponenttype")
                )
                self._unresolve("dependency", code if isinstance(code, int) else 0, dependent)
                continue
            self.add(
                "dependency", r["id"],
                {"dependent_kind": dependent[0], "dependent_key": dependent[1],
                 "required_kind": required[0], "required_key": required[1],
                 "dependency_type": r["dependencytype"]},
                None, "unknown", None, [],
            )  # fmt: skip

    def scope(self, solutions: tuple[str, ...]) -> None:
        """Keep only what the named solutions hold, and the gaps about those records.

        A membership or dependency row naming a component the run did not record is
        kept by the same rule (its solution, its dependent); an orphaned plug-in
        type, step or image has no owner to keep it by and is left out. The
        unkeyed-row gaps stay org-wide.

        Kept: the solutions, their memberships, each member, each ribbon diff (read
        from the solutions' own exports), each record of a
        member entity held with its subcomponents (`attribute`,
        `local_option_set`, `form`, `view`, `ribbon`, and a `relationship` on
        either side), and each dependency whose dependent is kept. An entity
        held without its subcomponents, or as a shell, brings only the members
        listed on their own.
        """
        named = set(solutions)
        members = {
            (p.fields["member_kind"], p.fields["member_key"])
            for p in self.pending
            if p.kind == "solution_membership" and p.fields["solution"] in named
        }
        entities = {
            p.fields["member_key"]
            for p in self.pending
            if p.kind == "solution_membership"
            and p.fields["solution"] in named
            and p.fields["member_kind"] == "entity"
            and (p.fields["solution"], p.fields["member_key"]) not in self.shells
        }

        def held(p: _Pending) -> bool:
            if p.kind == "solution":
                return p.fields["unique_name"] in named
            if p.kind == "solution_membership":
                return p.fields["solution"] in named
            if p.kind == "ribbon_diff":
                return True  # read from the named solutions' own exports
            if p.kind in ("attribute", "local_option_set", "form", "view", "ribbon"):
                return (p.kind, p.key) in members or p.fields["entity"] in entities
            if p.kind == "relationship":
                sides = {p.fields.get(k) for k in _RELATIONSHIP_SIDES}
                return (p.kind, p.key) in members or not sides.isdisjoint(entities)
            return (p.kind, p.key) in members

        kept = [p for p in self.pending if p.kind != "dependency" and held(p)]
        keys = {(p.kind, p.key) for p in kept}
        kept += [
            p
            for p in self.pending
            if p.kind == "dependency"
            and (p.fields["dependent_kind"], p.fields["dependent_key"]) in keys
        ]
        self.pending = kept
        self.extra_gaps = [g for g in self.extra_gaps if (g.kind, g.key) in keys]
        self.unresolved = [u for u in self.unresolved if u[2] in keys]
        self.scoped = True
        self.isv = {page: refs & keys for page, refs in self.isv.items()}

    def drop_stock(self) -> None:
        """Leave out the stock records, but keep each parent a kept record needs.

        A stock entity holding a kept attribute, relationship, form, view or ribbon
        stays as a reference row so keys and edges resolve. A membership or dependency naming a
        left-out component is counted into a ``platform`` gap, never dropped silently;
        one whose dependent is itself stock is stock's own edge and goes quietly.
        """
        self._keep_parents()
        # Edges name a component by (kind, key): a key a kept record also holds is not left out.
        left = {(p.kind, p.key) for p in self.pending if p.stock} - {
            (p.kind, p.key) for p in self.pending if not p.stock
        }
        kept: list[_Pending] = []
        for p in self.pending:
            if p.stock:
                continue
            if p.kind == "solution_membership":
                member = (p.fields["member_kind"], p.fields["member_key"])
                if member in left:
                    self.platform_edges[(p.kind, member[0])] += 1
                    continue
            elif p.kind == "dependency":
                if (p.fields["dependent_kind"], p.fields["dependent_key"]) in left:
                    continue
                required = (p.fields["required_kind"], p.fields["required_key"])
                if required in left:
                    self.platform_edges[(p.kind, required[0])] += 1
                    continue
            kept.append(p)
        self.pending = kept
        keys = {(p.kind, p.key) for p in kept}
        self.extra_gaps = [g for g in self.extra_gaps if (g.kind, g.key) in keys]
        self.unresolved = [u for u in self.unresolved if u[2] is None or u[2] in keys]
        self.isv = {page: refs & keys for page, refs in self.isv.items()}

    def _keep_parents(self) -> None:
        """Un-stock the entity a kept record names.

        A kept plug-in step needs no parent kept: stock flows down the chain, so a kept
        step's type and assembly are kept in their own right.
        """
        by_key = {(p.kind, p.key): p for p in self.pending}
        for p in self.pending:
            if p.stock or p.kind in ("solution_membership", "dependency"):
                continue
            entities = [p.fields.get("entity")]
            if p.kind == "relationship":
                entities = [p.fields.get(k) for k in _RELATIONSHIP_SIDES]
            for name in entities:
                if isinstance(name, str) and name:
                    parent = by_key.get(("entity", component_key("entity", {"logical_name": name})))
                    if parent is not None:
                        parent.stock = False
                        _stock_origin(parent)

    def finish(self) -> list[Record]:
        """Write each record's artifacts and return the records.

        Every payload is re-derived from the checkpoint, so an earlier run's
        artifacts are dropped first: a resumed run leaves no unreferenced file.
        An artifact is a hard link to its staged payload (a copy where the
        filesystem refuses links), so a payload is one set of bytes on disk. The
        stage stays for a resume: this runs before completeness is known.
        """
        if (self.out / "artifacts").exists():
            shutil.rmtree(self.out / "artifacts")
        counts: dict[tuple[str, str], int] = {}
        for p in self.pending:
            counts[(p.kind, p.key)] = counts.get((p.kind, p.key), 0) + 1
        records: list[Record] = []
        for p in self.pending:
            # A record with no source id (a ribbon) sharing its key with another is told
            # apart by its key fields' values.
            discriminator = (
                (p.id or json.dumps([p.fields[n] for n in KEY_FIELDS[p.kind]]))
                if counts[(p.kind, p.key)] > 1
                else None
            )
            artifacts: list[Artifact] = []
            for name, staged, text in p.payloads:
                path = artifact_path(p.kind, p.key, name, discriminator=discriminator)
                target = self.out / path
                target.parent.mkdir(parents=True, exist_ok=True)
                try:
                    os.link(staged, target)
                except OSError:
                    shutil.copyfile(staged, target)
                hashes = _file_hashes(target, text=text, ribbon=p.kind == "ribbon")
                artifacts.append(
                    Artifact(
                        path, "text" if text else "binary", hashes.sha256, hashes.normalized_sha256
                    )
                )
            records.append(
                Record(p.kind, p.key, p.id, p.display_name, p.origin, p.managed, "published",
                       p.fields, tuple(artifacts))
            )  # fmt: skip
        return records

    def gaps(self) -> list[ExtractionGap]:
        gaps = list(self.extra_gaps)
        for page, refs in sorted(self.isv.items()):
            if not refs:
                continue
            where = "; ".join(f"{kind} {key or '(default)'}" for kind, key in sorted(refs))
            gaps.append(
                ExtractionGap(
                    kind=None, key=None,
                    description=f"ISV page {page}, referenced from {where}",
                    reason=f"ISV folder pages are files on the CRM web server, which {self.api}"
                    " cannot read",
                )
            )  # fmt: skip
        for kind, (n, first) in sorted(self.unkeyed.items()):
            gaps.append(
                ExtractionGap(
                    kind=kind, key=None,
                    description=f"{n} {kind} row(s) not recorded; the first: {first}",
                    reason="the org's data leaves a component key field empty or mistyped,"
                    " so the row has no component key",
                )
            )  # fmt: skip
        for (kind, target), n in sorted(self.platform_edges.items()):
            gaps.append(
                ExtractionGap(
                    kind=kind, key=None,
                    description=f"{n} {kind} row(s) naming a stock {target} that this inventory"
                    " leaves out",
                    reason=_PLATFORM_REASON,
                )
            )  # fmt: skip
        for kind, n in sorted(self.no_managed.items()):
            gaps.append(
                ExtractionGap(
                    kind=kind, key=None,
                    description=f"{n} {kind} row(s) carry no ismanaged",
                    reason="the source returns no ismanaged for this kind, so the stock scope"
                    " cannot tell stock from custom and kept the rows",
                )
            )  # fmt: skip
        unresolved = Counter((kind, code) for kind, code, _ in self.unresolved)
        for (kind, code), n in sorted(unresolved.items()):
            reason = _UNRESOLVED_REASON
            if self.scoped and code in _ENTITY_DETAIL_TYPES:
                reason = _OUT_OF_SCOPE_REASON + reason
            gaps.append(
                ExtractionGap(
                    kind=kind, key=None,
                    description=f"{n} {kind} row(s) naming a component of type {code}"
                    " that this inventory does not record",
                    reason=reason,
                )
            )  # fmt: skip
        return gaps
