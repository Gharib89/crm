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
    """Stack `--solution` on a mutating metadata command."""
    f = click.option(
        "--solution", default=None,
        help="Target unmanaged solution uniquename (MSCRM.SolutionUniqueName). "
             "Required for customization writes.",
    )(f)
    return f


def _resolve_solution(
    ctx: "CLIContext", explicit: str | None,
) -> tuple[str, str | None]:
    """Resolve the effective solution for a mutating metadata command.

    `--solution` is now always required. Raises UsageError (exit 2) when no
    explicit solution is provided.

    Returns `(solution, warning)`. The second element is always `None` at
    runtime (the old "no solution resolved" warning path is gone), but is
    typed `str | None` so the `solution, warning = ...` unpacking at every call
    site keeps inferring `warning` as `str | None` (callers build `list[str]`
    warning lists from it).
    """
    if not explicit:
        raise click.UsageError(
            "--solution is required for customization writes — components must "
            "target an explicit unmanaged solution. Pass --solution <unique_name>."
        )
    return explicit, None


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
