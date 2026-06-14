"""Solution / publish / schema-name resolution helpers."""
# pyright: basic
from __future__ import annotations
import os
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


def _require_solution(require_flag: bool) -> bool:
    """Strict mode active when the --require-solution flag OR CRM_REQUIRE_SOLUTION set."""
    if require_flag:
        return True
    return os.environ.get("CRM_REQUIRE_SOLUTION", "").strip().lower() in (
        "1", "true", "yes", "on",
    )


def _solution_option(f):
    """Stack `--solution` / `--require-solution` on a mutating metadata command."""
    f = click.option(
        "--require-solution", is_flag=True, default=False,
        help="Fail if no solution resolves (also via CRM_REQUIRE_SOLUTION).",
    )(f)
    f = click.option(
        "--solution", default=None,
        help="Target solution uniquename (MSCRM.SolutionUniqueName). "
             "Defaults to the profile's default_solution.",
    )(f)
    return f


def _resolve_solution(
    ctx: "CLIContext", explicit: str | None, require_solution: bool,
) -> tuple[str | None, str | None]:
    """Resolve the effective solution for a mutating metadata command.

    Precedence: explicit `--solution` > profile `default_solution` > None.

    `require_solution` is the raw `--require-solution` flag; the strict-mode
    OR-with-`CRM_REQUIRE_SOLUTION` check is folded in here (it was a
    `_require_solution(...)` wrapper at every call site). Always-strict callers
    pass a literal `True`.

    Returns `(solution, warning)`. When none resolves and strict mode is off,
    `warning` is a non-empty string the caller stashes under the JSON `meta`
    envelope (or prints via skin.warning in human mode). When none resolves and
    strict mode is on, this routes a hard failure through `ctx.emit(False)`
    (raising click.exceptions.Exit per ADR 0001) instead of returning.
    """
    if explicit:
        return explicit, None
    profile = _active_profile(ctx)
    if profile and profile.default_solution:
        return profile.default_solution, None
    msg = (
        "No solution resolved: pass --solution or set a profile default_solution. "
        "The change targets the default (unmanaged) solution."
    )
    if _require_solution(require_solution):
        ctx.emit(False, error=msg)
        return None, None  # unreachable: emit(False) raises Exit
    return None, msg


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
