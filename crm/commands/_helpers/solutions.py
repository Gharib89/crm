"""Solution / publish / schema-name resolution helpers."""

# pyright: basic
from __future__ import annotations

from typing import TYPE_CHECKING

import click

from crm.core import session as session_mod

if TYPE_CHECKING:
    from crm.cli import CLIContext
    from crm.utils.d365_backend import ConnectionProfile


def _resolve_publish(ctx: CLIContext, publish: bool) -> bool:
    """Derive the effective publish value, honoring the global --stage-only flag.

    When `ctx.stage_only` is set, every metadata-mutating command behaves as
    --no-publish. Passing an explicit --publish on the command line alongside
    --stage-only is contradictory and rejected. An explicit --no-publish is fine.
    """
    if not ctx.stage_only:
        return publish
    # Imported from click.core (not top-level click) because pyright's bundled click
    # stubs only export ParameterSource there; `click.ParameterSource` / `from click
    # import ParameterSource` fail strict type-checking even though both work at runtime.
    from click.core import ParameterSource

    source = click.get_current_context().get_parameter_source("publish")
    if source == ParameterSource.COMMANDLINE and publish:
        raise click.UsageError("--publish cannot be combined with --stage-only")
    return False


def _publish_option(f):
    """Stack the standard `--publish/--no-publish` flag on a mutating command.

    Pairs with `_resolve_publish` in the verb body, mirroring how
    `_solution_option` pairs with `_resolve_solution`. The help text is uniform
    across all sites (it was inconsistent / absent before #294).
    """
    return click.option(
        "--publish/--no-publish",
        default=False,
        help="Run PublishAllXml after the change. Default: stage (no publish); "
        "run `solution publish-all` when your set is done.",
    )(f)


def _active_profile(ctx: CLIContext) -> ConnectionProfile | None:
    """Load the active connection profile, or None if none is resolvable."""
    name = ctx.profile_name
    if not name:
        state = session_mod.load_session(ctx.session_name)
        name = state.get("active_profile")
    if not name:
        return None
    try:
        return session_mod.load_profile(name)
    except FileNotFoundError:
        return None


def _solution_option(f):
    """Stack the mandatory `--solution` flag on a customization-write command.

    Pairs with `_resolve_solution` in the verb body, which raises a UsageError
    (exit 2) when it is omitted — there is no profile default and no opt-out
    (#636). `--solution Default` is the explicit escape hatch for a deliberate
    Default-Solution-only write.
    """
    return click.option(
        "--solution",
        default=None,
        help="Target unmanaged solution uniquename (MSCRM.SolutionUniqueName). "
        "Required for customization writes; pass --solution Default for a "
        "deliberate Default-Solution-only write.",
    )(f)


def _resolve_solution(ctx: CLIContext, explicit: str | None) -> str:
    """Resolve the target unmanaged solution for a customization write (#636).

    An explicit `--solution` is mandatory: a component filed without a named
    solution is silently orphaned into only the system Default Solution, so the
    target must always be on the command line — there is no profile
    `default_solution` fallback. Raises `click.UsageError` (exit 2) when none is
    given, before any backend call (including under `--dry-run`). Deliberate
    Default-Solution-only writes pass `--solution Default` explicitly.
    """
    if explicit:
        return explicit
    raise click.UsageError(
        "--solution is required for customization writes — components must "
        "target an explicit unmanaged solution. Pass --solution <unique_name>."
    )
