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
import json
import os
import re
import shutil
from collections import Counter
from collections.abc import Callable, Iterable, Iterator
from dataclasses import dataclass, field, replace
from datetime import UTC, datetime
from pathlib import Path
from typing import Any, Protocol, cast

from crm.core import legacy_reads
from crm.core.inventory import (
    KEY_FIELDS,
    KINDS,
    MANIFEST_NAME,
    SCHEMA_VERSION,
    Artifact,
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
from crm.utils.d365_backend import PLATFORM_CRM2011, D365Error

CHECKPOINT_DIR = ".checkpoint"

# The solutioncomponent / dependency type code of an option set, global or local by the set.
_OPTION_SET = 9
_ENTITY = 1

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

# Web resource types whose content is text, by webresourcetype code.
_TEXT_WEB_RESOURCES = frozenset((1, 2, 3, 4, 9))
_WEB_RESOURCE_EXT = dict(
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

    @property
    def org_url(self) -> str: ...

    def identity(self) -> dict[str, Any]:
        """``organization_id`` and ``version`` of the org."""
        ...

    def list_entities(self) -> list[dict[str, Any]]: ...

    def list_option_sets(self) -> list[dict[str, Any]]: ...

    def entity_detail(self, name: str) -> dict[str, Any]: ...

    def ribbon_xml(self, entity: str | None) -> bytes: ...

    def pages(self, fetch_xml: str, *, page_size: int, start: Page | None) -> Iterator[Page]: ...


class LegacySource:
    """A CRM 2011 org, read over the read-only SOAP client."""

    platform = PLATFORM_CRM2011
    api = "the CRM 2011 SOAP endpoint"
    static_gaps = (integration_gap(api), ssrs_gap(api))

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

    def pages(self, fetch_xml: str, *, page_size: int, start: Page | None) -> Iterator[Page]:
        return self.client.pages(fetch_xml, page_size=page_size, start=start)


def _ribbon_gaps(unlabelled: list[str]) -> list[ExtractionGap]:
    if not unlabelled:
        return []
    return [
        ExtractionGap(
            kind="ribbon",
            key=None,
            description=f"ribbons of {len(unlabelled)} entities with no display label:"
            f" {', '.join(unlabelled)}",
            reason="an entity with no display label has no UI, so its ribbon is not read"
            " (CRM 2011 faults RetrieveEntityRibbon for one)",
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
    """

    kind: str
    entity: str
    attributes: tuple[str, ...]
    payloads: tuple[tuple[str, str], ...] = ()
    extra: str = ""
    page_size: int = 5000

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
        cls, out: Path, org_url: str, now: str, solutions: tuple[str, ...] = ()
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
            return cp, state, True
        check_out(out)
        state = {"org_url": org_url, "started_at": now, "solutions": scope}
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
    def __init__(self, source: InventorySource, cp: Checkpoint):
        self.source = source
        self.cp = cp
        self.tallies: dict[str, _Tally] = {}
        self.errors: list[str] = []  # failed units that feed no kind (the source read)

    def tally(self, kind: str) -> _Tally:
        return self.tallies.setdefault(kind, _Tally())

    def unit(self, kinds: Iterable[str], unit: str, read: Callable[[], Any]) -> Any:
        """Return *unit*'s saved data, or read and save it; a per-item failure returns None."""
        tallies = [self.tally(k) for k in kinds]
        data = self.cp.load(unit)
        if data is not None:
            for t in tallies:
                t.pages += 1
                t.reused += 1
            return data
        try:
            data = read()
        except D365Error as exc:
            if legacy_reads.connection_wide(exc):
                raise
            for t in tallies:
                t.errors.append(f"{unit}: {exc}")
            if not tallies:
                self.errors.append(f"{unit}: {exc}")
            return None
        self.cp.save(unit, data)
        for t in tallies:
            t.pages += 1
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
        return rows

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
                    f"content.{_WEB_RESOURCE_EXT.get(code if isinstance(code, int) else 0, 'bin')}"
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


def _member_entities(
    run: _Run, rows: dict[str, list[dict[str, Any]]], solutions: tuple[str, ...]
) -> tuple[set[str], tuple[str, ...]]:
    """The ids of the entities the named solutions hold, and the names as the org spells them.

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
    members = {
        str(_ref_id(r.get("objectid")))
        for r in rows.get("solution_membership", [])
        if r.get("componenttype") == _ENTITY and (_ref_id(r.get("solutionid")) or "") in ids
    }
    return members, tuple(sorted(spelled.get(name, name) for name in solutions))


def extract(
    source: InventorySource,
    out: Path,
    *,
    cli_version: str,
    solutions: tuple[str, ...] = (),
    now: Callable[[], str] = _utc_now,
) -> ExtractSummary:
    """Extract the org's migration inventory into *out*, resuming a checkpoint there.

    *solutions* narrows the inventory to those solutions (by unique name):
    their records, their memberships, the components they hold and the
    subcomponents of the entities they hold. Entity details and ribbons are
    read only for those entities; the org-wide reads still run, since a
    membership names its component only by id.

    Raises:
        D365Error: *out* cannot be used, or a connection-wide failure stopped
            the read (the checkpoint keeps every finished unit for a re-run).
    """
    solutions = tuple(sorted({name.lower() for name in solutions}))
    cp, state, resumed = Checkpoint.open(out, source.org_url, now(), solutions)
    run = _Run(source, cp)
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
    rows = {read.kind: run.pages(read) for read in reads}
    if solutions:
        members, solutions = _member_entities(run, rows, solutions)
        entities_read = [e for e in entities if str(e.get("metadata_id")).lower() in members]
    else:
        entities_read = entities
    details: list[dict[str, Any]] = []
    for entity in entities_read:
        name = str(entity["logical_name"])
        detail = run.unit(
            ("attribute", "relationship", "local_option_set"),
            f"entity/{name}",
            lambda name=name: source.entity_detail(name),
        )
        if detail is not None:
            details.append(detail)
    ribbons: dict[str | None, bytes] = {}
    # An entity with no display label has no UI: its ribbon read faults
    # ("Could not find localized name"), so it is not read (measured on UR15).
    labelled = [str(e["logical_name"]) for e in entities_read if e.get("display_name")]
    unlabelled = sorted(str(e["logical_name"]) for e in entities_read if not e.get("display_name"))
    # The application ribbon belongs to no solution's entities, so a scoped run skips it.
    for entity_name in [*([] if solutions else [None]), *labelled]:
        unit = f"ribbon/{entity_name or '_application'}"

        def read_ribbon(entity_name: str | None = entity_name) -> str:
            xml = source.ribbon_xml(entity_name)
            cp.stage("ribbon", entity_name or "_application", "ribbon.xml", xml)
            return "ribbon.xml"

        if run.unit(("ribbon",), unit, read_ribbon) is not None:
            ribbons[entity_name] = cp.staged(
                "ribbon", entity_name or "_application", "ribbon.xml"
            ).read_bytes()

    built = _Builder(cp, out, rows, source.api)
    built.metadata(entities, option_sets, details)
    built.components()
    for entity_name, xml in ribbons.items():
        built.add(
            "ribbon",
            None,
            {"entity": entity_name},
            None,
            "unknown",  # the ribbon XML merges the system ribbon and customizations
            None,
            [("ribbon.xml", xml, True)],
        )
    built.memberships_and_dependencies()
    if solutions:
        built.scope(solutions)
    records = built.finish()

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
        scope=Scope(mode="solutions" if solutions else "org", solutions=solutions),
        publication="published",
        records=tuple(records),
        coverage=coverage,
        gaps=(
            *source.static_gaps,
            *built.gaps(),
            *_ribbon_gaps(unlabelled),
            *_source_gaps(run.errors),
        ),
    )
    (out / MANIFEST_NAME).write_text(dump_manifest(inventory), encoding="utf-8")

    errors = [*run.errors, *(e for t in run.tallies.values() for e in t.errors)]
    complete = not errors and all(c.status == "complete" for c in coverage.values())
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
    payloads: list[tuple[str, bytes, bool]]


class _Builder:
    """Turns the units' rows into records, resolving cross-kind keys and finding gaps."""

    def __init__(self, cp: Checkpoint, out: Path, rows: dict[str, list[dict[str, Any]]], api: str):
        self.cp = cp
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

    def add(
        self,
        kind: str,
        row_id: str | None,
        fields: dict[str, Any],
        display_name: str | None,
        origin: str,
        managed: bool | None,
        payloads: list[tuple[str, bytes, bool]],
        component_type: int | None = None,
    ) -> str | None:
        """Queue a record and index its key; None when a key field's value breaks the contract.

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
        self.pending.append(
            _Pending(kind, key, row_id, display_name, origin, managed, fields, payloads)
        )
        if row_id:
            self.by_id.setdefault(kind, {})[row_id.lower()] = key
            if component_type is not None:
                self.index[(component_type, row_id.lower())] = (kind, key)
        return key

    def _origin(self, row: dict[str, Any]) -> str:
        solution = _ref_id(row.get("solutionid"))
        if solution is None or solution not in self.solution_names:
            return "unknown"
        return "system" if self.solution_names[solution] == "System" else "custom"

    def _staged(
        self, kind: str, row: dict[str, Any], text: bool = True
    ) -> list[tuple[str, bytes, bool]]:
        names = cast("list[str]", row.get("_artifacts", []))
        return [(n, self.cp.staged(kind, str(row["id"]), n).read_bytes(), text) for n in names]

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
                None, "custom" if s["custom"] else "system", None, [], _OPTION_SET,
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
            )  # fmt: skip
            for _, data, is_text in payloads:
                if is_text and key is not None:
                    self._scan_isv(data, ("web_resource", key))
        for r in rows.get("plugin_assembly", []):
            source = r.get("sourcetype")
            key = self.add(
                "plugin_assembly", r["id"],
                {"name": r["name"], "version": r.get("version"), "culture": r.get("culture"),
                 "public_key_token": r.get("publickeytoken"), "source_type": source},
                r.get("name"), self._origin(r), r.get("ismanaged"),
                self._staged("plugin_assembly", r, text=False), 91,
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
                self._unresolve("plugin_type", 91)
                continue
            self.add(
                "plugin_type", r["id"],
                {"assembly": assembly, "type_name": r["typename"],
                 "workflow_activity": r.get("isworkflowactivity")},
                r.get("friendlyname"), self._origin(r), r.get("ismanaged"), [], 90,
            )  # fmt: skip
        types = self.by_id.get("plugin_type", {})
        for r in rows.get("step", []):
            plugin_type = types.get(_ref_id(r.get("plugintypeid")) or "")
            message = r.get("message.name")
            if plugin_type is None or not message:
                self._unresolve("step", 90)
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
            )  # fmt: skip
        steps = self.by_id.get("step", {})
        for r in rows.get("step_image", []):
            step = steps.get(_ref_id(r.get("sdkmessageprocessingstepid")) or "")
            if step is None:
                self._unresolve("step_image", 92)
                continue
            self.add(
                "step_image", r["id"],
                {"step": step, "alias": r["entityalias"], "image_type": r["imagetype"],
                 "attributes": r.get("attributes"),
                 "message_property": r.get("messagepropertyname")},
                r.get("name"), self._origin(r), r.get("ismanaged"), [], 93,
            )  # fmt: skip
        for r in rows.get("process", []):
            self.add(
                "process", r["id"],
                {"category": r["category"], "primary_entity": _entity_name(r.get("primaryentity")),
                 "name": r["name"], "state": r.get("statecode")},
                r.get("name"), self._origin(r), r.get("ismanaged"), self._staged("process", r), 29,
            )  # fmt: skip
        for r in rows.get("form", []):
            self.add(
                "form", r["id"],
                {"entity": r["objecttypecode"], "form_type": r["type"], "name": r["name"]},
                r.get("name"), self._origin(r), r.get("ismanaged"), self._staged("form", r), 60,
            )  # fmt: skip
        for r in rows.get("view", []):
            self.add(
                "view", r["id"],
                {"entity": r["returnedtypecode"], "name": r["name"],
                 "query_type": r.get("querytype")},
                r.get("name"), self._origin(r), r.get("ismanaged"), self._staged("view", r), 26,
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
            )
            for _, data, _ in payloads:
                if key is not None:
                    self._scan_isv(data, ("sitemap", key))

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
        kept by the same rule (its solution, its dependent); the unkeyed-row gaps
        stay org-wide.

        Kept: the solutions, their memberships, each member, each record of a
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

    def finish(self) -> list[Record]:
        """Write each record's artifacts and return the records.

        Every payload is re-derived from the checkpoint, so an earlier run's
        artifacts are dropped first: a resumed run leaves no unreferenced file.
        """
        if (self.out / "artifacts").exists():
            shutil.rmtree(self.out / "artifacts")
        counts: dict[tuple[str, str], int] = {}
        for p in self.pending:
            counts[(p.kind, p.key)] = counts.get((p.kind, p.key), 0) + 1
        records: list[Record] = []
        for p in self.pending:
            # A record with no source id (a ribbon) is told apart by its key fields'
            # values: the application ribbon's null entity meets an `application` entity.
            discriminator = (
                (p.id or json.dumps([p.fields[n] for n in KEY_FIELDS[p.kind]]))
                if counts[(p.kind, p.key)] > 1
                else None
            )
            artifacts: list[Artifact] = []
            for name, data, text in p.payloads:
                path = artifact_path(p.kind, p.key, name, discriminator=discriminator)
                target = self.out / path
                target.parent.mkdir(parents=True, exist_ok=True)
                target.write_bytes(data)
                hashes = artifact_hashes(data, text=text)
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
            where = "; ".join(f"{kind} {key}" for kind, key in sorted(refs))
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
