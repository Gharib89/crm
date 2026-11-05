"""`crm legacy`: read-only commands for a CRM 2011 (crm2011) profile (ADR 0031)."""

# pyright: basic
from __future__ import annotations

import sys
from pathlib import Path

import click

from crm.cli import CLIContext, pass_ctx
from crm.commands._helpers import _output_option, d365_errors, in_use_options
from crm.core import legacy_reads, usage
from crm.core.legacy import LegacyClient
from crm.utils.d365_backend import normalize_guid


def _meta(ctx: CLIContext, client: LegacyClient) -> dict | None:
    return {"profile": client.profile.name, "url": client.endpoint} if ctx.json_mode else None


def _emit_with_failures(
    ctx: CLIContext, data, meta, errors: list[dict], total: int, noun: str, label: str
) -> None:
    """Emit *data*; a non-empty *errors* makes it a failure envelope that still carries the data.

    *meta* (the connection identity) goes on success only: an error envelope's
    meta is reserved for status, code, category and retryable, as `emit` keeps it.
    """
    if not errors:
        ctx.emit(True, data=data, meta=meta)
        return
    detail = "; ".join(f"{e[label]}: {e['error']}" for e in errors)
    ctx.emit(False, data=data, error=f"{len(errors)} of {total} {noun} failed: {detail}")


@click.group("legacy")
def legacy_group():
    """Read a legacy CRM 2011 org over its read-only SOAP endpoint.

    Runs only on a crm2011 profile, which `crm profile add` saves when the org
    has no Web API. Tested on CRM 2011 UR15 (5.0.9690.3557), on Linux and Windows.
    """


@legacy_group.command("whoami")
@pass_ctx
def legacy_whoami(ctx: CLIContext):
    """Issue WhoAmI: the caller's user, business unit and organization ids."""
    with d365_errors(ctx):
        client = ctx.legacy_client()
        results = client.execute("WhoAmI")
    data = {
        "user_id": results.get("UserId"),
        "business_unit_id": results.get("BusinessUnitId"),
        "organization_id": results.get("OrganizationId"),
    }
    ctx.emit(True, data=data, meta=_meta(ctx, client))


@legacy_group.command("version")
@pass_ctx
def legacy_version(ctx: CLIContext):
    """Issue RetrieveVersion: the org's build string (5.x on CRM 2011)."""
    with d365_errors(ctx):
        client = ctx.legacy_client()
        results = client.execute("RetrieveVersion")
    data = {"version": results.get("Version")}
    ctx.emit(True, data=data, meta=_meta(ctx, client))


@legacy_group.command("metadata")
@click.argument("entities", nargs=-1)
@click.option(
    "--option-sets", is_flag=True, help="List the global option sets instead of entities."
)
@in_use_options
@pass_ctx
def legacy_metadata(
    ctx: CLIContext,
    entities: tuple[str, ...],
    option_sets: bool,
    in_use: bool,
    exclude_unknown: bool,
):
    """Read entity metadata: read-only, over RetrieveAllEntities / RetrieveEntity.

    With no ENTITY, lists every entity (logical name, custom flag, object type
    code). With ENTITY names, reads each one's attributes, relationships and
    option sets; one that fails is reported and the others still read
    (continue-and-report), and the exit code is 1 if any failed. `--option-sets`
    lists the global option sets and takes no ENTITY.

    `--in-use` applies to the entity listing: each row gains a `usage` object
    (an existence read per entity) and the rows proven unused are dropped.
    """
    if option_sets and entities:
        raise click.UsageError("--option-sets takes no ENTITY.")
    if in_use and (option_sets or entities):
        raise click.UsageError(
            "--in-use applies to the entity listing: no ENTITY, no --option-sets."
        )
    errors: list[dict] = []
    with d365_errors(ctx):
        client = ctx.legacy_client()
        if option_sets:
            data = legacy_reads.list_option_sets(client)
        elif not entities:
            if not in_use:
                data = legacy_reads.list_entities(client)
            else:
                data, primary_ids = legacy_reads.list_entities_with_primary_ids(client)
                states = usage.probe_legacy_entities(
                    client,
                    primary_ids,
                    progress=usage.UsageProgress(sys.stderr.write, "entities"),
                )
                data = usage.attach(
                    data, states, key=lambda e: e["logical_name"], exclude_unknown=exclude_unknown
                )
        else:
            read, errors = legacy_reads.read_entities(client, list(entities))
            data = {"entities": read, "errors": errors}
    _emit_with_failures(ctx, data, _meta(ctx, client), errors, len(entities), "entities", "entity")


@legacy_group.command("components")
@click.argument("kinds", nargs=-1, type=click.Choice(list(legacy_reads.KINDS)))
@click.option(
    "--out",
    "out_dir",
    type=click.Path(file_okay=False, path_type=Path),
    help="Write payloads (web resource content, form XML, process XAML) under DIR.",
)
@click.option(
    "--solution",
    metavar="UNIQUENAME",
    help="Read only the members of this solution, as it exports them.",
)
@click.option(
    "--entity",
    metavar="LOGICALNAME",
    help=f"Read only the components of this entity ({', '.join(legacy_reads.ENTITY_KINDS)}).",
)
@in_use_options
@pass_ctx
def legacy_components(
    ctx: CLIContext,
    kinds: tuple[str, ...],
    out_dir: Path | None,
    solution: str | None,
    entity: str | None,
    in_use: bool,
    exclude_unknown: bool,
):
    """Read solution components: read-only, paged to the last page over SOAP.

    With no KIND, reads every kind. Each kind is paged with the server's paging
    cookie until the last page; one that fails keeps the rows it already read,
    is reported in `errors`, and the next kind still runs (continue-and-report),
    with exit code 1. Plug-in assemblies stored on disk or in the GAC are listed
    with `binary: unavailable` and a reason, never skipped. With `--out`, large
    payloads are written to files and the row carries `<column>_file` instead.

    `--solution` keeps a component the solution holds directly or through its
    parent: plug-in types through their assembly, step images through their
    step, forms and views through their entity. `--entity` reads only the kinds
    that name an entity (all of them when no KIND is given). Both together
    apply both. An unknown solution or entity fails with exit code 1 before any
    kind is read.

    `--in-use` applies to `plugin-assemblies`, `plugin-types` and `plugin-steps`
    (all three when no KIND is given): each row gains a `usage` object and the
    rows proven unused are dropped. Only a disabled step is proven unused; a
    step with an async job row is used; every other step is `unknown`.
    """
    if in_use:
        wrong = [k for k in kinds if k not in usage.PLUGIN_KINDS]
        if wrong:
            raise click.UsageError(f"--in-use does not apply to {', '.join(wrong)}.")
        kinds = kinds or tuple(
            k for k in usage.PLUGIN_KINDS if entity is None or k in legacy_reads.ENTITY_KINDS
        )
    if entity is not None:
        wrong = [k for k in kinds if k not in legacy_reads.ENTITY_KINDS]
        if wrong:
            raise click.UsageError(f"--entity does not apply to {', '.join(wrong)}.")
    names = list(kinds) or list(
        legacy_reads.ENTITY_KINDS if entity is not None else legacy_reads.KINDS
    )
    if out_dir is not None:
        try:
            out_dir.mkdir(parents=True, exist_ok=True)
        except OSError as exc:
            ctx.emit(False, error=f"--out {out_dir}: {exc}")
            return
    with d365_errors(ctx):
        client = ctx.legacy_client()
        scope = legacy_reads.resolve_scope(client, names, solution, entity)
        data, errors = legacy_reads.read_components(client, names, out_dir, scope)
        if in_use:
            data = usage.annotate_legacy_components(client, data, exclude_unknown=exclude_unknown)
    _emit_with_failures(
        ctx, {**data, "errors": errors}, _meta(ctx, client), errors, len(names), "kinds", "kind"
    )


@legacy_group.command("dependencies")
@click.argument("object_id")
@click.option(
    "--component-type",
    type=int,
    required=True,
    help="The component's type code (1 entity, 2 attribute, 9 option set, 61 web resource, ...).",
)
@click.option(
    "--for",
    "for_",
    type=click.Choice(list(legacy_reads.DEPENDENCY_REQUESTS)),
    default="required",
    show_default=True,
    help=(
        "required = what the component needs (RetrieveRequiredComponents); "
        "dependents = what needs it (RetrieveDependentComponents); "
        "delete = what blocks its deletion (RetrieveDependenciesForDelete)."
    ),
)
@pass_ctx
def legacy_dependencies(ctx: CLIContext, object_id: str, component_type: int, for_: str):
    """Read one component's dependencies: read-only, over a dependency request.

    OBJECT_ID is the component's id (a MetadataId for entities, attributes,
    relationships and option sets, which `crm legacy metadata` returns). Each
    row names both sides by component type and object id, plus the dependency
    type (1 solution internal, 2 published, 4 unpublished).
    """
    guid = normalize_guid(object_id)
    if guid is None:
        raise click.BadParameter(f"{object_id!r} is not a GUID.", param_hint="OBJECT_ID")
    with d365_errors(ctx):
        client = ctx.legacy_client()
        rows = legacy_reads.dependencies(client, guid, component_type, for_)
    meta = _meta(ctx, client)
    ctx.emit(True, data=rows, meta={**meta, "for": for_} if meta else None)


@legacy_group.command("export-solution")
@click.argument("unique_name")
@_output_option(required=True, help="Write the solution zip to this file.")
@pass_ctx
def legacy_export_solution(ctx: CLIContext, unique_name: str, output: str):
    """Export one solution unmanaged, as stored: read-only, over ExportSolution.

    Nothing is published first (publishing writes), so the zip holds the
    published customizations only.
    """
    with d365_errors(ctx):
        client = ctx.legacy_client()
        data = legacy_reads.export_solution(client, unique_name)
    target = Path(output)
    try:
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_bytes(data)
    except OSError as exc:
        ctx.emit(False, error=f"--output {target}: {exc}")
        return
    ctx.emit(
        True,
        data={"solution": unique_name, "path": str(target), "bytes": len(data)},
        meta=_meta(ctx, client),
    )
