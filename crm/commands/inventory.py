"""`crm inventory`: extract, compare, map, package, verify and assess migration inventories."""

# pyright: basic
from __future__ import annotations

import sys
import time
from collections.abc import Callable
from dataclasses import asdict
from pathlib import Path

import click

from crm import __version__
from crm.cli import FAILURE_EXIT_CODE, CLIContext, pass_ctx
from crm.commands._helpers import d365_errors, in_use_options
from crm.core import inventory_extract
from crm.core.inventory import InventoryError, read_inventory
from crm.core.inventory_applied import STATUSES as APPLIED_STATUSES
from crm.core.inventory_applied import check_applied
from crm.core.inventory_assess import CLASSES, TARGETS, assess, dump_findings, read_findings
from crm.core.inventory_convert import convert
from crm.core.inventory_diff import VERDICTS, diff_inventories, dump_diff, read_pair
from crm.core.inventory_extract import Progress
from crm.core.inventory_packages import (
    COMPAT_CLASSES,
    build_packages,
    read_packages,
    reference_edges,
    write_packages,
)
from crm.core.inventory_readiness import readiness
from crm.core.inventory_source_map import STATUSES, dump_source_map, map_source, read_source_map
from crm.core.inventory_usage_filter import SORTS, filter_findings
from crm.core.inventory_verify import dump_report, verify
from crm.core.usage import JOB_TIME_CAP
from crm.core.webapi_inventory import WebApiSource
from crm.utils.d365_backend import PLATFORM_CRM2011


@click.group("inventory")
def inventory_group():
    """Extract, compare, map to source, package, verify and assess migration inventories."""


def _usage_options(fn):
    """The shared `--in-use` / `--exclude-unknown` pair plus `--sort` (`packages`, `assess`)."""
    return in_use_options(
        click.option(
            "--sort",
            type=click.Choice(SORTS),
            default="default",
            help="`usage` lists used rows first, then unknown or not read, then unused.",
        )(fn)
    )


def _eta(seconds: float) -> str:
    if (secs := round(seconds)) < 60:
        return f"~{secs}s"
    if (mins := round(secs / 60)) < 60:
        return f"~{mins}m"
    hours, mins = divmod(mins, 60)
    return f"~{hours}h{mins:02d}m"


class ProgressLines:
    """Write extract's `Progress` events as `[crm] extract key=value` lines, at most one a second.

    A stage's first line (done 0) and its last (done == total) always print, as
    `solution import`'s terminal tick does; the lines between are dropped inside
    the same second. One line per tick, never a redrawn bar, so a CI log or an
    agent capturing stderr reads it the same as a terminal does.
    """

    def __init__(
        self,
        write: Callable[[str], object],
        clock: Callable[[], float] = time.monotonic,
    ):
        self._write = write
        self._clock = clock
        self._last: float | None = None

    def __call__(self, p: Progress) -> None:
        now = self._clock()
        start = p.done == 0 and p.pages is None
        last = p.total is not None and p.done >= p.total and p.pages is None
        if not (start or last) and self._last is not None and now - self._last < 1.0:
            return
        self._last = now
        parts = [f"stage={p.stage}"]
        if start:
            if p.total is not None:
                parts.append(f"total={p.total}")
        else:
            parts.append(f"done={p.done}/{p.total}")
            if p.kind:
                parts.append(f"kind={p.kind} pages={p.pages}")
            if p.reused:
                parts.append(f"reused={p.reused}")
            if p.eta is not None:
                parts.append(f"eta={_eta(p.eta)} (estimate)")
        self._write("[crm] extract " + " ".join(parts) + "\n")


@inventory_group.command("extract")
@click.option(
    "--out",
    "out_dir",
    required=True,
    type=click.Path(file_okay=False, path_type=Path),
    help="Folder to write manifest.json and artifacts/ into; a re-run resumes it.",
)
@click.option(
    "--solution",
    "solutions",
    multiple=True,
    metavar="UNIQUE_NAME",
    help="Narrow the inventory to this solution (repeatable); default: the whole org.",
)
@click.option(
    "--include-system",
    is_flag=True,
    help=(
        "Keep the stock components too (managed, or owned by a managed solution, except"
        " a row the Active solution owns, which the default keeps)."
    ),
)
@click.option(
    "--with-usage",
    is_flag=True,
    help="Record a usage object on each entity, plug-in and process row (extra reads).",
)
@click.option(
    "--usage-time-cap",
    type=click.FloatRange(min=1),
    default=None,
    metavar="SECONDS",
    help=f"With --with-usage: stop each CRM 2011 async-job read after this long (default "
    f"{JOB_TIME_CAP:g}); the rows it did not reach read unknown.",
)
@click.option("--quiet", "-q", is_flag=True, help="Suppress the progress lines on stderr.")
@pass_ctx
def inventory_extract_cmd(
    ctx: CLIContext,
    out_dir: Path,
    solutions: tuple[str, ...],
    include_system: bool,
    with_usage: bool,
    usage_time_cap: float | None,
    quiet: bool,
):
    """Extract the org's migration inventory into DIR: read-only.

    Runs on every profile: a crm2011 profile is read over the CRM 2011 SOAP
    endpoint, a v9 on-prem or Dataverse online profile over the Web API. It
    reads every v1 kind and never publishes or writes to the org, so a
    read-only profile runs it. By default it keeps the migration scope and
    leaves stock components out (managed, or owned by a managed solution, but not
    a row the Active solution owns, which is a customised stock component;
    their payloads are never fetched), still counting any edge to one;
    --include-system keeps them. --solution narrows the records to those
    solutions, their members and their entities' parts (a member assembly's
    plug-in types included), and keeps stock. A managed solution cannot be
    exported, so its ribbon diff is not read: it is recorded as a gap, and
    `ribbon_diff` reads `unsupported` when every named solution is managed. A whole-org
    run on Dataverse online, which cannot export the Default solution, rebuilds the diffs
    from the unmanaged rows of the ribbon diff and ribbon command tables: `ribbon_diff`
    reads `partial`, with a gap for the rules, templates, labels and managed diffs it
    omits, and `unsupported` only if a table read faults (the run still exits 0).
    Each report is a `report` record keyed by its name, with its RDL body as an artifact
    and a `data_provider` (`fetch`, `sql` or `unknown`); a Reporting Services report whose
    body the endpoint does not return reads `unknown` and gets an extraction gap naming it.
    Each finished read is checkpointed under DIR/.checkpoint, so re-running
    the same command after an interruption resumes, reusing the finished reads. A failed kind is
    recorded in the manifest's coverage and the run goes on (exit code 1); the
    checkpoint is removed once every read succeeds. A table deleted since the
    entity list was read is no failed read: it is left out of the records and
    recorded as a gap, and a resume does not read it again. Returns a short
    summary; the manifest holds the records, coverage and gaps.

    Progress goes to stderr, one `[crm] extract` line per stage start and per
    tick (about one a second), with an ETA marked as an estimate where the
    stage has a known total; stdout is unchanged. -q suppresses it.

    --with-usage adds one read stage after the records (default off: a default
    extract is unchanged, and a whole-org one already issues thousands of reads).
    It records `usage` ({state, signal, value, as_of, reason}) on entity, plug-in
    assembly, type and step, and process rows, and `scope.usage_read` in the
    manifest. A row with no `usage` was not read, which is not `unused`; a probe
    that fails leaves its rows `unknown` with the reason, lists an extraction
    gap, and exits 1 with the checkpoint kept, so a re-run probes only it again.
    """
    if usage_time_cap is not None and not with_usage:
        raise click.UsageError("--usage-time-cap applies only with --with-usage.")
    saved = ctx.saved_profile()
    meta = None
    with d365_errors(ctx):
        try:
            inventory_extract.check_out(out_dir)
            if saved is not None and saved.platform == PLATFORM_CRM2011:
                client = ctx.legacy_client()
                source = inventory_extract.LegacySource(client)
                if ctx.json_mode:
                    meta = {"profile": client.profile.name, "url": client.endpoint}
            else:
                source = WebApiSource(ctx.backend())
            summary = inventory_extract.extract(
                source,
                out_dir,
                cli_version=__version__,
                solutions=solutions,
                include_system=include_system,
                progress=None if quiet else ProgressLines(sys.stderr.write),
                with_usage=with_usage,
                usage_time_cap=usage_time_cap or JOB_TIME_CAP,
            )
        except OSError as exc:
            ctx.emit(False, error=f"--out {out_dir}: {exc}")
            return
    data = asdict(summary)
    if summary.errors:
        ctx.emit(
            False,
            data=data,
            error=f"{len(summary.errors)} read(s) failed: {'; '.join(summary.errors)}",
        )
        return
    ctx.emit(True, data=data, meta=meta)


@inventory_group.command("diff")
@click.argument("a", type=click.Path(file_okay=False, path_type=Path))
@click.argument("b", type=click.Path(file_okay=False, path_type=Path))
@click.option(
    "--out",
    "out_file",
    type=click.Path(dir_okay=False, path_type=Path),
    help="Write the full per-key diff to this JSON file.",
)
@pass_ctx
def inventory_diff_cmd(ctx: CLIContext, a: Path, b: Path, out_file: Path | None):
    """Compare migration inventory A (the earlier stage) with B: offline.

    Reads the two inventory folders and touches no org. Records are matched by
    component key within each kind, never by GUID, and each key gets one
    verdict: added, removed, changed, identity_only (only the GUID differs),
    unchanged, ambiguous (the key repeats) or not_comparable (a kind's coverage
    is not complete on both sides, or what one side lacks its extraction gap
    explains).
    One side extracted with --solution against a whole-org side compares only the
    narrowed side's keys and the whole-org side's members of the same-named
    solutions; the header's `restricted` says so and counts the keys left out.
    Differences exit 0; an unreadable inventory exits 1. Returns the header
    (both sources and scopes), counts per kind and verdict, counts per origin,
    the kinds not read complete on both sides, and both sides' gaps; --out
    writes the same plus every key's entry, byte for byte the same for the
    same inputs. Matching evidence is not proof of equivalent behavior.
    """
    try:
        diff = diff_inventories(*read_pair(a, b))
        if out_file is not None:
            out_file.write_text(dump_diff(diff), encoding="utf-8")
    except InventoryError as exc:
        ctx.emit(False, error=str(exc))
        return
    except OSError as exc:
        ctx.emit(False, error=f"--out {out_file}: {exc}")
        return
    data = {**diff.summary(), "out": str(out_file) if out_file else None}
    warning = (
        "only one side was read with --with-usage: usage is not compared"
        if diff.a.scope.usage_read != diff.b.scope.usage_read
        else None
    )
    if ctx.json_mode:
        ctx.emit(True, data=data, meta={"warning": warning} if warning else None)
        return
    counts = data["counts"]
    rows = [[kind, *(counts[kind].get(v, 0) for v in VERDICTS)] for kind in sorted(counts)]
    sides = {s: data["header"][s] for s in ("a", "b")}
    meta = {s.upper(): f"{h['platform']} {h['version']} ({h['org_url']})" for s, h in sides.items()}
    incomplete = data["incomplete"]
    if incomplete:
        meta["incomplete"] = ", ".join(
            f"{kind} (A {s['a']}, B {s['b']})" for kind, s in incomplete.items()
        )
    if diff.restriction is not None:
        meta["restricted"] = diff.restriction.describe()
    if warning:
        meta["warning"] = warning
    if out_file is not None:
        meta["out"] = str(out_file)
    ctx.emit(True, table={"headers": ["kind", *VERDICTS], "rows": rows}, meta=meta)


@inventory_group.command("map-source")
@click.argument("inventory", type=click.Path(file_okay=False, path_type=Path))
@click.option(
    "--repo",
    required=True,
    type=click.Path(exists=True, file_okay=False, path_type=Path),
    help="The application repository checkout to match against; read only, never built.",
)
@click.option(
    "--out",
    "out_file",
    type=click.Path(dir_okay=False, path_type=Path),
    help="Write the full source map to this JSON file.",
)
@pass_ctx
def inventory_map_source_cmd(ctx: CLIContext, inventory: Path, repo: Path, out_file: Path | None):
    """Match INVENTORY's custom components to files in an application repository: offline.

    Reads the inventory folder and the --repo checkout, touches no org and never
    builds. Each custom web resource, plug-in assembly and plug-in type gets one
    status: matched (a web resource's normalized content hash equals a repo
    file's; a plug-in type's C# declaration sits in a project whose AssemblyName,
    or project file name, is the registered assembly), stale (one file lines up
    by name but its content differs; which side is newer is unknown), ambiguous
    (several files line up, all listed), candidate (a minified or generated
    file, a type in a project building another assembly, or an assembly's
    project without the type; the reason says which) or missing_source. Also
    lists web resources more than one component references (shared), C#
    projects two plug-in projects reference (shared libraries), NuGet packages
    and scripts from other hosts (external), and deployable repo files nothing
    matched (unmatched_source). Returns the header and counts; --out writes the
    full map (its own schema_version), the same bytes for the same inputs.
    """
    try:
        source_map = map_source(inventory, repo)
        if out_file is not None:
            out_file.write_text(dump_source_map(source_map), encoding="utf-8")
    except (InventoryError, OSError) as exc:
        ctx.emit(False, error=str(exc))
        return
    data = {**source_map.summary(), "out": str(out_file) if out_file else None}
    if ctx.json_mode:
        ctx.emit(True, data=data)
        return
    counts = data["counts"]
    rows = [[kind, *(counts[kind].get(s, 0) for s in STATUSES)] for kind in counts]
    meta = {
        "inventory": f"{data['inventory']['platform']} {data['inventory']['version']}"
        f" ({data['inventory']['org_url']})",
        "shared": f"{data['shared']['web_resources']} web resource(s),"
        f" {data['shared']['projects']} project(s)",
        "external": str(data["external"]),
        "unmatched_source": str(data["unmatched_source"]),
    }
    if data["incomplete"]:
        meta["incomplete"] = ", ".join(f"{k} ({v})" for k, v in data["incomplete"].items())
    if out_file is not None:
        meta["out"] = str(out_file)
    ctx.emit(True, table={"headers": ["kind", *STATUSES], "rows": rows}, meta=meta)


@inventory_group.command("packages")
@click.argument("inventory", type=click.Path(file_okay=False, path_type=Path))
@click.option(
    "--source-map",
    "source_map_file",
    required=True,
    type=click.Path(exists=True, dir_okay=False, path_type=Path),
    help="The source map `crm inventory map-source --out` wrote for this inventory.",
)
@click.option(
    "--scenarios",
    "scenarios_file",
    type=click.Path(exists=True, dir_okay=False, path_type=Path),
    help="A JSON file of named business scenarios, each with seed component keys.",
)
@click.option(
    "--findings",
    "findings_file",
    type=click.Path(exists=True, dir_okay=False, path_type=Path),
    help="The findings file `crm inventory assess --out` wrote for this inventory.",
)
@click.option(
    "--out",
    "out_dir",
    required=True,
    type=click.Path(file_okay=False, path_type=Path),
    help="An empty or new folder for index.json plus one file per work package.",
)
@_usage_options
@pass_ctx
def inventory_packages_cmd(
    ctx: CLIContext,
    inventory: Path,
    source_map_file: Path,
    scenarios_file: Path | None,
    findings_file: Path | None,
    out_dir: Path,
    in_use: bool,
    exclude_unknown: bool,
    sort: str,
):
    """Group INVENTORY into work packages over its dependency graph: offline.

    Reads the inventory folder, the source map made from it (--source-map) and
    an optional scenario file; touches no org. The graph joins components
    through platform dependencies, form libraries and event handlers, ribbon,
    sitemap and web resource references, step, image and activity keys, and
    the source map's files and C# project references. A reference that cannot
    be resolved statically (a dynamic name, an ISV page, another host's URL, a
    name not in the inventory) is an unknown edge, never dropped. A work
    package is a connected group holding a source-mapped component or an
    unknown, so a shared library and its consumers are one package. A web
    resource name missing from an inventory that did not read the whole org is
    out_of_scope instead: listed and counted apart, not an unknown, and alone
    it forms no package. Each
    package lists its source, affected consumers, findings (unknowns), expected
    behavior (from --scenarios, else unknown) and verification requirements,
    each requirement with a stable id; an unknown edge from a node no package
    holds (an entity, a system web resource) is listed in index.json instead.
    --findings (the file `crm inventory assess --out` wrote) adds each blocker,
    deprecated, optional and unknown finding to the package holding its
    component, keeping its class, rule id, evidence and Microsoft Learn sources,
    and counts them per package; a clear finding is not merged, a finding whose
    component is in no package is listed as unplaced, and a findings file made
    from another inventory exits 1. A blocker or deprecated finding on a process
    also makes a package of its group when none holds it (a package of one for an
    isolated dialog); no other finding creates a package.
    --out gets index.json and one <package id>.json per package, the same
    bytes for the same inputs. Returns the package count and sizes, the
    unknown counts, the scenario seeds not found or in no package, and the
    packages_sha256 a verification results file names (`crm inventory verify`).

    Each package carries a `usage` ({state, signal, value, as_of, reason}): its
    members' usage rolled up, `used` if any member is and `unused` only if every
    member is. A member's usage is the one `extract --with-usage` read, else
    derived from the inventory alone: a web resource a form, ribbon or sitemap
    references is `used`, an unreferenced one `unknown` (never `unused`), and a
    form, view or ribbon of an entity with no records is `unused` at low
    confidence. A package with no usage at all (nothing read or derived) has no
    `usage` field: not read. --in-use drops a package whose usage is `unused`,
    --exclude-unknown (which needs --in-use) also one that is `unknown`; neither
    drops a package that has no usage. --sort usage lists used packages first, then unknown, then
    unused. usage_filtered counts the dropped packages; a scenario lists only
    the packages that remain.
    """
    try:
        result = build_packages(
            inventory,
            read_source_map(source_map_file),
            scenarios_file,
            read_findings(findings_file) if findings_file else None,
            in_use=in_use,
            exclude_unknown=exclude_unknown,
            sort=sort,
        )
        write_packages(result, out_dir)
        sha = read_packages(out_dir).sha256
    except (InventoryError, OSError) as exc:
        ctx.emit(False, error=str(exc))
        return
    data = {**result.summary(), "packages_sha256": sha, "out": str(out_dir)}
    if ctx.json_mode:
        ctx.emit(True, data=data)
        return
    headers = ["package", "members", "unknowns", "usage", "scenarios"]
    counts = [list(p.counts().values()) if "compatibility" in data else [] for p in result.packages]
    rows = [
        [
            p.id,
            len(p.members),
            p.unknowns(),
            f"{p.usage.state} ({p.usage.signal})" if p.usage else "not read",
            *c,
            ", ".join(p.scenarios),
        ]
        for p, c in zip(result.packages, counts, strict=True)
    ]
    meta = {
        "inventory": f"{data['inventory']['platform']} {data['inventory']['version']}"
        f" ({data['inventory']['org_url']})",
        "unknowns": str(data["unknowns"]),
        "out": str(out_dir),
    }
    if data["unmatched_scenarios"]:
        meta["unmatched scenarios"] = ", ".join(data["unmatched_scenarios"])
    if data["seeds_not_found"]:
        meta["seeds not found"] = ", ".join(
            f"{s['scenario']}: {s['seed']}" for s in data["seeds_not_found"]
        )
    if data["seeds_unpackaged"]:
        meta["seeds in no package"] = ", ".join(
            f"{s['scenario']}: {s['seed']}" for s in data["seeds_unpackaged"]
        )
    if data["unattached_gaps"]:
        meta["gaps in no package"] = f"{data['unattached_gaps']} (see index.json)"
    if data["unattached_unknowns"]:
        meta["unknowns in no package"] = f"{data['unattached_unknowns']} (see index.json)"
    if data["out_of_scope"]:
        meta["out of scope"] = f"{data['out_of_scope']} (see index.json)"
    if data["incomplete"]:
        meta["incomplete"] = ", ".join(f"{k} ({v})" for k, v in data["incomplete"].items())
    if data["usage_filtered"]:
        meta["dropped by usage"] = str(data["usage_filtered"])
    if "compatibility" in data:
        comp = data["compatibility"]
        headers[4:4] = list(COMPAT_CLASSES)
        meta["findings"] = f"target {comp['target']}, " + ", ".join(
            f"{comp[c]} {c}" for c in COMPAT_CLASSES
        )
        if comp["unplaced"]:
            meta["findings in no package"] = f"{comp['unplaced']} (see index.json)"
    ctx.emit(True, table={"headers": headers, "rows": rows}, meta=meta)


@inventory_group.command("verify")
@click.argument("packages", type=click.Path(file_okay=False, path_type=Path))
@click.option(
    "--results",
    "results_file",
    required=True,
    type=click.Path(exists=True, dir_okay=False, path_type=Path),
    help="The verification results file recorded against PACKAGES.",
)
@click.option(
    "--out",
    "out_file",
    type=click.Path(dir_okay=False, path_type=Path),
    help="Write the full report, every requirement's status, to this JSON file.",
)
@pass_ctx
def inventory_verify_cmd(
    ctx: CLIContext, packages: Path, results_file: Path, out_file: Path | None
):
    """Judge recorded verification results against PACKAGES' requirements: offline.

    Reads the folder `crm inventory packages --out` wrote and a verification
    results file (--results); runs no check and touches no org. Each package
    gets a verdict, the first that applies: failed (a requirement failed),
    blocked (one could not run), not_tested (one has no result, or a pass with
    no evidence or with an evidence file missing or changed), else complete. A
    negative control (a deliberate regression) recording pass is reported
    escaped and keeps its package from complete. A result naming a package or
    requirement PACKAGES lacks is listed as an unknown result. A results file
    recorded against another packages output is refused, as is an --out that
    would overwrite an input. Verdicts exit 0; an unreadable or refused input
    exits 1. Returns the verdict counts, each
    package's verdict and its requirements per status (requirement_counts),
    the escaped controls and the unknown results; --out
    writes the same plus every requirement's status, byte for byte the same
    for the same inputs.
    """
    try:
        report = verify(packages, results_file, out_file)
        if out_file is not None:
            out_file.write_text(dump_report(report), encoding="utf-8")
    except InventoryError as exc:
        ctx.emit(False, error=str(exc))
        return
    except OSError as exc:
        ctx.emit(False, error=f"--out {out_file}: {exc}")
        return
    data = {**report.summary(), "out": str(out_file) if out_file else None}
    if ctx.json_mode:
        ctx.emit(True, data=data)
        return
    rows = [
        [
            p.id,
            p.verdict,
            f"{sum(r.status == 'pass' for r in p.requirements)}/{len(p.requirements)}",
        ]
        for p in report.packages
    ]
    meta = {"verdicts": ", ".join(f"{v} {n}" for v, n in data["counts"].items())}
    if data["escaped"]:
        meta["escaped controls"] = ", ".join(
            f"{e['package']}: {e['requirement']}" for e in data["escaped"]
        )
    if data["unknown_results"]:
        meta["unknown results"] = ", ".join(
            f"{u['package']}: {u['requirement']} ({u['reason']})" for u in data["unknown_results"]
        )
    if out_file is not None:
        meta["out"] = str(out_file)
    headers = ["package", "verdict", "passed"]
    ctx.emit(True, table={"headers": headers, "rows": rows}, meta=meta)


@inventory_group.command("convert")
@click.argument("inventory", type=click.Path(file_okay=False, path_type=Path))
@click.option(
    "--findings",
    "findings_file",
    required=True,
    type=click.Path(exists=True, dir_okay=False, path_type=Path),
    help="The findings file `crm inventory assess --out` wrote for this inventory.",
)
@click.option(
    "--packages",
    "packages_dir",
    required=True,
    type=click.Path(exists=True, file_okay=False, path_type=Path),
    help="The folder `crm inventory packages --out` wrote for this inventory.",
)
@click.option(
    "--source-map",
    "source_map_file",
    type=click.Path(exists=True, dir_okay=False, path_type=Path),
    help="The source map `crm inventory map-source --out` wrote; adds a repo-path diff for"
    " each web resource it links to a file by hash.",
)
@click.option(
    "--target-inventory",
    "target_inventory",
    type=click.Path(exists=True, file_okay=False, path_type=Path),
    help="An inventory extracted from the target org: its entity set and attribute names"
    " are the Web API names the OData v2 rewrite writes. Without it each v2 call is a"
    " manual-review entry.",
)
@click.option(
    "--repo",
    "repo_dir",
    type=click.Path(exists=True, file_okay=False, path_type=Path),
    help="The application repository --source-map was made from; adds the SDK-style net462"
    " retarget of each plug-in project the map matches to an assembly.",
)
@click.option(
    "--adapter-resource",
    "adapter_resource",
    help="The web resource name the OData v2 adapter will have in the target org; {package}"
    " expands to the work package id. A form that lists a rewritten library gets the adapter"
    " listed ahead of it, and an HTML page that loads a rewritten library or holds a rewritten"
    " inline script gets a script tag for it, and a ribbon command that calls a rewritten library"
    " gets an adapter action ahead of it. Without it each such form, page or command is a"
    " manual-review entry.",
)
@click.option(
    "--out",
    "out_dir",
    required=True,
    type=click.Path(file_okay=False, path_type=Path),
    help="An empty or new folder for the patches and the ledger.",
)
@pass_ctx
def inventory_convert_cmd(
    ctx: CLIContext,
    inventory: Path,
    findings_file: Path,
    packages_dir: Path,
    source_map_file: Path | None,
    target_inventory: Path | None,
    repo_dir: Path | None,
    adapter_resource: str | None,
    out_dir: Path,
):
    """Rewrite the code of INVENTORY that a compatibility rule can prove: offline.

    Reads the inventory folder, the --findings file and the --packages folder, and
    touches no org and writes no repository. A component whose finding names a registered
    transform (today `Xrm.Page.context.<method>()` to
    `Xrm.Utility.getGlobalContext().<method>()` in a JavaScript web resource) is
    parsed with tree-sitter; only a real call is rewritten, never text in a string
    or comment. Per work package, --out gets a unified diff against the extracted
    text, the full patched file, and, with --source-map, a repo-path diff for a web
    resource the map links to a file by hash. With --repo (the checkout the map was made
    from) each plug-in assembly the map matches to one project also gets that project
    rewritten to SDK-style `net462` with the `Microsoft.CrmSdk.CoreAssemblies` package in
    place of SDK 5.0 references, signing kept; a `Microsoft.Xrm.Client` or ILMerge use, or a
    project that is not a class library, is a manual-review entry. The build stays outside
    convert. `ledger.json` (its own schema_version)
    records each patch with the SHA-256 of the original and the patched text, and
    each manual-review entry (file, line, rule, reason) where a rule matches but the
    rewrite is not proven. A patch is written only if the patched code parses with no
    new error nodes and re-running the rule finds exactly the rewritten sites fewer;
    otherwise the code stays untouched and the ledger says why. A sync or async
    `XMLHttpRequest` GET of the OData v2 endpoint is rewritten to the Web API with the
    names of --target-inventory and routed through a generated adapter module per work
    package (`crm-odata-v2-adapter.js`, listed under the ledger's `adapters`) that hands
    the caller v2-shaped JSON. With --adapter-resource (the web resource name the adapter will
    have in the org, `{package}` expanding to the work package id) a form that lists a
    rewritten library gets a `Library` element for the adapter immediately ahead of the first
    such library, on the same line, and the ledger's `adapters` entry records that name: upload
    the adapter under it and publish. An HTML web resource (also read by the OData v2
    transform, in its inline scripts) gets a `<script src>` for the adapter immediately ahead
    of its first rewritten script, on the same line; its inline rewrite stays only when that
    registration is proven. Without the flag each such form or page is a manual-review entry,
    and several work packages with adapters need `{package}` in the name (exit 1 otherwise,
    before anything is written). A component with no
    transformable finding writes nothing. Nothing is written outside --out; apply a
    patch yourself with `git apply`. An unreadable input, an input made from another
    inventory, a non-empty --out or one inside the inventory exits 1. Returns the
    counts of patched components and manual-review entries, and the out path. --repo
    without --source-map is a usage error (exit 2).
    """
    if repo_dir is not None and source_map_file is None:
        raise click.UsageError("--repo needs --source-map: the map names each project's path.")
    try:
        result = convert(
            inventory,
            findings_file,
            packages_dir,
            out_dir,
            source_map_path=source_map_file,
            target_inventory=target_inventory,
            repo=repo_dir,
            adapter_resource=adapter_resource,
        )
    except InventoryError as exc:
        ctx.emit(False, error=str(exc))
        return
    except OSError as exc:
        ctx.emit(False, error=str(exc))
        return
    if ctx.json_mode:
        ctx.emit(True, data={**result.summary(), "out": str(out_dir)})
        return
    rows = [
        [e.package, e.key, e.status, str(e.edits), str(len(e.manual_review))]
        for e in result.entries
    ]
    headers = ["package", "component", "status", "edits", "manual review"]
    ctx.emit(True, table={"headers": headers, "rows": rows}, meta={"out": str(out_dir)})


@inventory_group.command("assess")
@click.argument("inventory", type=click.Path(file_okay=False, path_type=Path))
@click.option(
    "--target",
    required=True,
    type=click.Choice(TARGETS),
    help="The migration target the rules judge against.",
)
@click.option(
    "--out",
    "out_file",
    required=True,
    type=click.Path(dir_okay=False, path_type=Path),
    help="Write the findings file, every component's findings, to this JSON file.",
)
@click.option(
    "--source-map",
    "source_map_file",
    type=click.Path(exists=True, dir_okay=False, path_type=Path),
    help="The source map `crm inventory map-source --out` wrote for this inventory; with --repo,"
    " judges each plug-in project it matches to an assembly.",
)
@click.option(
    "--repo",
    "repo_dir",
    type=click.Path(exists=True, file_okay=False, path_type=Path),
    help="The application repository --source-map was made from.",
)
@click.option(
    "--source-host",
    "source_hosts",
    multiple=True,
    help="A host name of the source org, repeatable (crm01 or crm01.contoso.com).",
)
@click.option(
    "--checker-report",
    "checker_report",
    type=click.Path(exists=True, dir_okay=False, path_type=Path),
    help="A Solution Checker report you already ran: the zip `pac solution check` wrote, or its"
    " SARIF file. Read only.",
)
@_usage_options
@pass_ctx
def inventory_assess_cmd(
    ctx: CLIContext,
    inventory: Path,
    target: str,
    out_file: Path,
    source_map_file: Path | None,
    repo_dir: Path | None,
    source_hosts: tuple[str, ...],
    checker_report: Path | None,
    in_use: bool,
    exclude_unknown: bool,
    sort: str,
):
    """Run the compatibility rules shipped with crm over INVENTORY for --target: offline.

    Reads the inventory folder and touches no org. Each rule hit carries its
    fix and Microsoft Learn sources. Every component gets at least one finding, keyed by
    component key: a rule hit gives its class with evidence (the artifact
    path, its sha256 and the matched lines, or the recorded field); a
    component of a covered kind that no rule selects gives unknown "no rule",
    and a kind no active rule covers rolls up to one uncovered_kinds entry
    (its record count, reason "no rule") instead; one the rules find
    nothing in gives clear. An extraction gap naming the component, or its
    kind's coverage not complete, adds an unknown saying so. Findings are a
    verdict, so blockers exit 0; an unreadable inventory, an unknown
    schema_version or an --out inside the inventory exits 1. --out gets the
    findings file (its own schema_version), sorted, so two runs differ only in
    generated_at. Returns the counts per class, per kind and per usage, and the
    out path.

    Each finding carries its row's `usage` ({state, signal, value, as_of,
    reason}) when it has one: what `extract --with-usage` read, else derived from
    the inventory alone (a web resource a form, ribbon or sitemap references is
    `used`, an unreferenced one `unknown`, never `unused`; a form, view or ribbon
    of an entity with no records is `unused`, low confidence). A row with no
    usage was not read (counted `not_read`). --in-use drops the findings of
    `unused` rows (a blocker on one goes with it), --exclude-unknown, which needs
    --in-use, also those of `unknown` rows; neither drops a row with no usage.
    --sort usage lists used rows first.

    --source-map with --repo (the checkout the map was made from; one without the
    other is a usage error, exit 2) also reads each plug-in project the map matches to
    exactly one assembly: a blocker `csproj-sdk-net462` when the project targets .NET
    Framework below 4.6.2 (no declared version counts) or references the SDK 5.0
    Microsoft.Xrm.Sdk or Microsoft.Crm.Sdk.Proxy, its evidence naming the repository path,
    the sha256, the declared version and the references; a project that cannot be read or
    parsed is unknown with a reason. For --target online it also reads the project's own
    C# files: a blocker `plugin-source-sql-client`, `plugin-source-filesystem` or
    `plugin-source-registry` per file that creates or accesses a SQL client, file system or
    registry type the sandbox blocks (the file path, its sha256, line and column locations);
    a file that cannot be read or parsed is unknown. Nothing rewrites that code. A map from
    another inventory exits 1. Without both, no project is read. A plug-in assembly the map
    does not match to one project (every assembly, without both) is read from its registered
    binary instead: a deprecated `plugin-binary-sdk-net462` when it was built for .NET
    Framework below 4.6.2 or references the SDK 5.0 assemblies, unknown with a reason when
    the inventory holds no readable binary for it.

    --source-host (repeatable) names the source org's host, which stays on the command line and
    never enters the rules file. After a database upgrade the stored text still points at a
    server that is switched off, so a blocker `source-host-web-resource` (script and HTML web
    resources), `source-host-sitemap`, `source-host-ribbon`, `source-host-ribbon-diff` or
    `source-host-process` is reported per component whose text names a host, with the same
    evidence as a content rule (matched lines, comments included). The match ignores case
    and covers the whole host: crm01 matches crm01.contoso.com, not crm010 or crm01-test.
    A plug-in step's configuration is not recorded in the inventory, so it is not read. A
    host with a scheme, port or path exits 1. The findings file lists the hosts, sorted, as
    source_hosts; without --source-host none of this runs and the file is as before.

    --checker-report merges a Power Apps Solution Checker report you ran yourself (`pac
    solution check` on an unmanaged solution; the zip it writes or the SARIF file inside) into
    the findings. This complements the packaged rules and replaces none of them: crm only reads
    the file, never runs `pac` or calls the checker service, and the checker does not flag
    everything the packaged rules do. A result on a web resource the inventory holds (its
    file is the web resource name with a leading slash) becomes a finding whose rule is the
    checker's rule id as written (`web-avoid-2011-api`), with the artifact path, sha256 and
    the result's lines as evidence. Class follows the severity read from the result (Critical
    and High blocker, Medium deprecated, Low and Informational optional), except where the
    override table `crm/rules/checker-overrides.json` names the rule (`web-use-async` and
    `web-avoid-eval` are deprecated). A result on lines a packaged finding of the corresponding
    rule already flags is skipped and counted (`skipped_duplicates`); a packaged finding is never
    dropped. A result on no web resource of the inventory, from a family other than `web-`, or
    with no line or severity goes to `checker_unmatched`, not the findings. `convert` leaves
    checker findings alone. The findings file records the report's sha256 and result count
    as `checker_report`; a file that is not a zip or SARIF exits 1. Without the option the
    file has neither key.
    """
    if (source_map_file is None) != (repo_dir is None):
        raise click.UsageError("--source-map and --repo go together: the map names each project.")
    try:
        result = assess(
            inventory,
            target,
            out=out_file,
            source_map=source_map_file,
            repo=repo_dir,
            source_hosts=source_hosts,
            checker_report=checker_report,
        )
        parsed = read_inventory(inventory)
        try:
            edges = reference_edges(parsed, inventory)
        except InventoryError:
            # An unreadable artifact must not cost the assessment it already holds; only a
            # run that asked for usage needs the references.
            if in_use or exclude_unknown or sort != "default":
                raise
        else:
            result = filter_findings(
                result,
                parsed.records,
                edges,
                parsed.extracted_at,
                in_use=in_use,
                exclude_unknown=exclude_unknown,
                sort=sort,
            )
        out_file.write_text(dump_findings(result), encoding="utf-8")
    except InventoryError as exc:
        ctx.emit(False, error=str(exc))
        return
    except OSError as exc:
        ctx.emit(False, error=f"--out {out_file}: {exc}")
        return
    data = {**result.summary(), "out": str(out_file)}
    if ctx.json_mode:
        ctx.emit(True, data=data)
        return
    by_kind = data["counts"]["by_kind"]
    rows = [[kind, *(by_kind[kind].get(c, 0) for c in CLASSES)] for kind in by_kind]
    meta = {
        "inventory": f"{data['inventory']['platform']} {data['inventory']['version']}"
        f" ({data['inventory']['org_url']})",
        "target": target,
        "rules": f"version {data['rules']['version']}",
        "out": str(out_file),
    }
    if data["incomplete"]:
        meta["incomplete"] = ", ".join(f"{k} ({v})" for k, v in data["incomplete"].items())
    if data["uncovered_kinds"]:
        kinds = data["uncovered_kinds"]
        meta["no rule"] = ", ".join(f"{u['kind']} ({u['records']})" for u in kinds)
    if "checker" in data:
        c = data["checker"]
        meta["checker"] = (
            f"{c['results']} results: {c['merged']} merged, {c['skipped_duplicates']} skipped,"
            f" {c['unmatched']} unmatched"
        )
    ctx.emit(True, table={"headers": ["kind", *CLASSES], "rows": rows}, meta=meta)


@inventory_group.command("readiness")
@click.argument("findings", type=click.Path(exists=True, dir_okay=False, path_type=Path))
@pass_ctx
def inventory_readiness_cmd(ctx: CLIContext, findings: Path):
    """Read a findings file as a readiness checklist for a move to Dataverse online: offline.

    FINDINGS is the file `crm inventory assess INVENTORY --target online --out FILE` wrote; one
    written for --target onprem-9.1 is a usage error (exit 2), as is one made with other rules
    than this crm ships or with --in-use or --exclude-unknown that dropped findings. Touches no
    org. Prints one row per gate: pass, fail, warn or unknown, the
    components behind it, the rule that decided it and its basis (an OP2OL whitepaper gate, or a
    Dataverse online requirement that is not one). Gate rows come from the packaged online
    blocker rules, so a new rule adds a row. A source version below 9.0 fails the version row;
    isolation mode none is a warn (the migration converts it to sandbox mode); a gate whose
    component kind has an extraction gap or unknown findings, or whose input assess was not given
    (--source-host, --source-map with --repo), is unknown, never pass. The closing manual rows are
    the gates the API cannot see (collation, size, TDE, integrity checks, target users and
    licences): they carry no status. This is a checklist, not the OP2OL service, Lifecycle
    Services or the upgrade hops. Exits 0 whenever the checklist was produced, whatever its
    rows say.
    """
    try:
        result = readiness(findings)
    except InventoryError as exc:
        raise click.UsageError(str(exc)) from exc
    if ctx.json_mode:
        ctx.emit(True, data=result.to_dict())
        return
    rows = [[r.gate, r.display_status, r.components, r.basis, r.note] for r in result.rows]
    rows += [
        [m["gate"], "manual", "", m["basis"], "the API cannot see this"] for m in result.manual
    ]
    ctx.emit(
        True,
        table={"headers": ["gate", "status", "components", "basis", "note"], "rows": rows},
        meta={"source version": result.source_version, "target": result.target},
    )


@inventory_group.command("check-applied")
@click.argument(
    "convert_out", metavar="CONVERT_OUT", type=click.Path(file_okay=False, path_type=Path)
)
@click.option(
    "--repo",
    required=True,
    type=click.Path(exists=True, file_okay=False, path_type=Path),
    help="The application repository checkout the patches were applied to; read only.",
)
@pass_ctx
def inventory_check_applied_cmd(ctx: CLIContext, convert_out: Path, repo: Path):
    """Prove a repository carries the patches of a convert output: offline.

    Reads the `crm inventory convert --out` folder's ledger and the --repo checkout; writes
    nothing, touches no org and needs no profile. Each ledger entry that patches a repository
    file gets one status, from the file's normalized hash (no BOM, LF, as convert hashed):
    applied (equals the entry's patched hash), not_applied (equals its original hash),
    diverged (equals neither) or missing (the file is not there). An entry with no repository
    file (a manual-review entry, or a patch for an inventory artifact only) is
    not_checkable, with the reason, and does not affect the exit code. Exits 0 when every
    checkable entry is applied, 1 otherwise; a missing or empty CONVERT_OUT, or a ledger of
    an unknown schema_version, is a usage error (exit 2). Returns the counts and one row per
    entry. This is the gate between applying the patches and building.
    """
    try:
        report = check_applied(convert_out, repo)
    except InventoryError as exc:
        raise click.UsageError(str(exc)) from exc
    data = report.summary()
    if ctx.json_mode:
        ctx.emit(report.ok, data=data, error=None if report.ok else "not every patch is applied")
        return
    rows = sorted(report.rows, key=lambda r: r.status == "applied")
    table = {
        "headers": ["status", "component", "path", "reason"],
        "rows": [[r.status, r.key, r.path or "", r.reason or ""] for r in rows],
    }
    counts = data["counts"]
    meta = {"counts": ", ".join(f"{s} {counts[s]}" for s in APPLIED_STATUSES)}
    ctx.emit(True, table=table, meta=meta)
    if not report.ok:
        raise click.exceptions.Exit(FAILURE_EXIT_CODE)
