"""`crm inventory`: extract, compare, map, package, verify and assess migration inventories."""

# pyright: basic
from __future__ import annotations

from dataclasses import asdict
from pathlib import Path

import click

from crm import __version__
from crm.cli import CLIContext, pass_ctx
from crm.commands._helpers import d365_errors
from crm.core import inventory_extract
from crm.core.inventory import InventoryError
from crm.core.inventory_assess import CLASSES, TARGETS, assess, dump_findings
from crm.core.inventory_diff import VERDICTS, diff_inventories, dump_diff, read_pair
from crm.core.inventory_packages import build_packages, read_packages, write_packages
from crm.core.inventory_source_map import STATUSES, dump_source_map, map_source, read_source_map
from crm.core.inventory_verify import dump_report, verify
from crm.core.webapi_inventory import WebApiSource
from crm.utils.d365_backend import PLATFORM_CRM2011


@click.group("inventory")
def inventory_group():
    """Extract, compare, map to source, package, verify and assess migration inventories."""


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
@pass_ctx
def inventory_extract_cmd(ctx: CLIContext, out_dir: Path, solutions: tuple[str, ...]):
    """Extract the org's migration inventory into DIR: read-only.

    Runs on every profile: a crm2011 profile is read over the CRM 2011 SOAP
    endpoint, a v9 on-prem or Dataverse online profile over the Web API. It
    reads every v1 kind and never publishes or writes to the org, so a
    read-only profile runs it. --solution narrows the records to those
    solutions, their members and their entities' parts. Each finished read is
    checkpointed under DIR/.checkpoint, so re-running the same command after
    an interruption resumes, reusing the finished reads. A failed kind is
    recorded in the manifest's coverage and the run goes on (exit code 1); the
    checkpoint is removed once every read succeeds. Returns a short summary;
    the manifest holds the records, coverage and gaps.
    """
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
                source, out_dir, cli_version=__version__, solutions=solutions
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
    if ctx.json_mode:
        ctx.emit(True, data=data)
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
    "--out",
    "out_dir",
    required=True,
    type=click.Path(file_okay=False, path_type=Path),
    help="An empty or new folder for index.json plus one file per work package.",
)
@pass_ctx
def inventory_packages_cmd(
    ctx: CLIContext,
    inventory: Path,
    source_map_file: Path,
    scenarios_file: Path | None,
    out_dir: Path,
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
    unknown, so a shared library and its consumers are one package. Each
    package lists its source, affected consumers, findings (unknowns), expected
    behavior (from --scenarios, else unknown) and verification requirements,
    each requirement with a stable id; an unknown edge from a node no package
    holds (an entity, a system web resource) is listed in index.json instead.
    --out gets index.json and one <package id>.json per package, the same
    bytes for the same inputs. Returns the package count and sizes, the
    unknown counts, the scenario seeds not found or in no package, and the
    packages_sha256 a verification results file names (`crm inventory verify`).
    """
    try:
        result = build_packages(inventory, read_source_map(source_map_file), scenarios_file)
        write_packages(result, out_dir)
        sha = read_packages(out_dir).sha256
    except (InventoryError, OSError) as exc:
        ctx.emit(False, error=str(exc))
        return
    data = {**result.summary(), "packages_sha256": sha, "out": str(out_dir)}
    if ctx.json_mode:
        ctx.emit(True, data=data)
        return
    rows = [[p.id, len(p.members), p.unknowns(), ", ".join(p.scenarios)] for p in result.packages]
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
    if data["incomplete"]:
        meta["incomplete"] = ", ".join(f"{k} ({v})" for k, v in data["incomplete"].items())
    headers = ["package", "members", "unknowns", "scenarios"]
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
    package's verdict, the escaped controls and the unknown results; --out
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
@pass_ctx
def inventory_assess_cmd(ctx: CLIContext, inventory: Path, target: str, out_file: Path):
    """Run the compatibility rules shipped with crm over INVENTORY for --target: offline.

    Reads the inventory folder and touches no org. Each rule hit carries its
    fix and Microsoft Learn sources. Every component gets at least one finding, keyed by
    component key: a rule hit gives its class with evidence (the artifact
    path, its sha256 and the matched lines, or the recorded field); a
    component no rule applies to gives unknown "no rule"; one the rules find
    nothing in gives clear. An extraction gap naming the component, or its
    kind's coverage not complete, adds an unknown saying so. Findings are a
    verdict, so blockers exit 0; an unreadable inventory, an unknown
    schema_version or an --out inside the inventory exits 1. --out gets the
    findings file (its own schema_version), sorted, so two runs differ only in
    generated_at. Returns the counts per class and per kind and the out path.
    """
    try:
        result = assess(inventory, target, out=out_file)
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
    ctx.emit(True, table={"headers": ["kind", *CLASSES], "rows": rows}, meta=meta)
