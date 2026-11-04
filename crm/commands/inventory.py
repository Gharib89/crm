"""`crm inventory`: extract and compare migration inventories (ADR 0032)."""

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
from crm.core.inventory_diff import VERDICTS, diff_inventories, dump_diff, read_pair
from crm.core.webapi_inventory import WebApiSource
from crm.utils.d365_backend import PLATFORM_CRM2011


@click.group("inventory")
def inventory_group():
    """Extract and compare migration inventories: manifest v1 plus raw artifacts (ADR 0032)."""


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
