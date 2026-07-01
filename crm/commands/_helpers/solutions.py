"""Solution / publish / schema-name resolution helpers."""
# pyright: basic
from __future__ import annotations
import re
from typing import TYPE_CHECKING
import click
from crm.core import session as session_mod
if TYPE_CHECKING:
    from crm.cli import CLIContext
    from crm.utils.d365_backend import ConnectionProfile


_EXPORT_SETTING_KEYS: dict[str, str] = {
    "autonumbering":       "export_autonumbering",
    "calendar":            "export_calendar",
    "customizations":      "export_customizations",
    "email-tracking":      "export_email_tracking",
    "general":             "export_general",
    "isv-config":          "export_isv_config",
    "marketing":           "export_marketing",
    "outlook-sync":        "export_outlook_sync",
    "relationship-roles":  "export_relationship_roles",
    "sales":               "export_sales",
}


def _resolve_publish(ctx: "CLIContext", publish: bool) -> bool:
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
        "--publish/--no-publish", default=True,
        help="Run PublishAllXml after the change. Default: publish.",
    )(f)


def _active_profile(ctx: "CLIContext") -> ConnectionProfile | None:
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
        "--solution", default=None,
        help="Target unmanaged solution uniquename (MSCRM.SolutionUniqueName). "
             "Required for customization writes; pass --solution Default for a "
             "deliberate Default-Solution-only write.",
    )(f)


def _optional_solution_option(f):
    """Stack an OPTIONAL `--solution` on a hard-delete metadata verb (#636).

    Unlike `_solution_option`, a hard metadata delete removes the component
    globally — `MSCRM.SolutionUniqueName` cannot scope or orphan a deletion —
    so `--solution` is *not* required here. Retained as an optional back-compat
    passthrough, forwarded to the backend when given (no `_resolve_solution`).
    """
    return click.option(
        "--solution", default=None,
        help="Optional. Forwarded as MSCRM.SolutionUniqueName; a hard delete "
             "removes the component globally, so this does not scope the delete.",
    )(f)


def _resolve_solution(ctx: "CLIContext", explicit: str | None) -> str:
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


def _resolve_schema_name(
    ctx: "CLIContext", schema_name: str | None, token: str | None, flag: str,
) -> str:
    """Resolve a create command's schema name from an explicit value or prefix.

    If `schema_name` is given, return it verbatim. Otherwise build
    `<publisher_prefix>_<PascalToken>` from the active profile prefix and the
    display/name token. Raises UsageError when neither is available.
    """
    if schema_name:
        return schema_name
    profile = _active_profile(ctx)
    prefix = profile.publisher_prefix if profile else None
    if not prefix:
        raise click.UsageError(
            f"{flag} is required (no publisher_prefix on the active profile to "
            "default from)."
        )
    if not token:
        raise click.UsageError(f"{flag} is required to default the schema name.")
    # PascalCase across word boundaries and drop non-alphanumerics so a
    # multi-word display like "Project Task" -> "ProjectTask", not the invalid
    # "Project Task". Preserve casing of the rest of each word (don't lower it).
    pascal = "".join(
        w[:1].upper() + w[1:] for w in re.split(r"[^0-9A-Za-z]+", token) if w
    )
    if not pascal:
        raise click.UsageError(
            f"{flag} could not be defaulted from {token!r} (no alphanumeric "
            f"characters); pass {flag} explicitly."
        )
    return f"{prefix}_{pascal}"
