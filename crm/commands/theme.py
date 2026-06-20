"""Theme (application branding) command group.

Themes are an ordinary ``themes`` entity plus the ``PublishTheme`` action. They
are **not solution-aware** — a theme does not travel with a solution export — so
this group has no ``--solution`` flag; the group help states that explicitly.
"""
# pyright: basic
from __future__ import annotations

import json
from typing import Any

import click

from crm.cli import CLIContext, pass_ctx
from crm.commands._helpers import _journal, d365_errors
from crm.core import themes as themes_mod


@click.group("theme")
def theme_group() -> None:
    """Author application themes (product branding: colors, logo) headlessly.

    A theme is an ordinary record; `publish` promotes one to the active org
    theme via PublishTheme. Themes are NOT solution-aware — they do not travel
    with a solution export, so there is no --solution flag here.
    """


def _parse_set(pairs: tuple[str, ...]) -> dict[str, Any]:
    """Parse repeatable ``--set FIELD=VALUE`` flags into a theme attribute dict.

    Split on the FIRST '=' so a VALUE may itself contain '='. The KEY is used
    verbatim; the VALUE is parsed as JSON, falling back to the raw string when it
    is not valid JSON (so `type=false` is a bool, `maincolor=#0066cc` a string).
    A pair missing '=' or with an empty field is a usage error (exit 2),
    validated before any backend call so a typo never costs a round-trip.
    """
    out: dict[str, Any] = {}
    for raw in pairs:
        key, sep, value = raw.partition("=")
        if not sep or not key.strip():
            raise click.UsageError(
                f"--set must be FIELD=VALUE with a non-empty field, got {raw!r}")
        try:
            out[key.strip()] = json.loads(value)
        except json.JSONDecodeError:
            out[key.strip()] = value
    return out


@theme_group.command("list")
@pass_ctx
def theme_list(ctx: CLIContext) -> None:
    """List all themes (org-wide; summary columns only — use `theme get` for colors)."""
    with d365_errors(ctx):
        themes = themes_mod.list_themes(ctx.backend())
    headers = ["name", "themeid", "type", "isdefaulttheme"]
    rows = [
        [t["name"], t.get("themeid") or "", str(t.get("type")),
         str(t["isdefaulttheme"])]
        for t in themes
    ]
    ctx.emit(True, data=themes, table={"headers": headers, "rows": rows})


@theme_group.command("get")
@click.argument("theme_id")
@pass_ctx
def theme_get(ctx: CLIContext, theme_id: str) -> None:
    """Get a single theme by THEME_ID (branding columns included)."""
    with d365_errors(ctx):
        info = themes_mod.get_theme(ctx.backend(), theme_id)
    ctx.emit(True, data=info)


_SET_OPTION = click.option(
    "--set", "set_pairs", multiple=True, metavar="FIELD=VALUE",
    help="Repeatable; a theme branding column, e.g. --set maincolor=#0066cc. "
         "VALUE is parsed as JSON with a raw-string fallback.")
_LOGO_OPTION = click.option(
    "--logo", default=None,
    help="Web resource name or GUID to bind as the theme logo.")


@theme_group.command("create")
@click.option("--name", required=True, help="Theme name.")
@_SET_OPTION
@_LOGO_OPTION
@pass_ctx
def theme_create(
    ctx: CLIContext, name: str, set_pairs: tuple[str, ...], logo: str | None,
) -> None:
    """Create a theme. Set branding columns with repeatable --set FIELD=VALUE."""
    attributes = _parse_set(set_pairs)
    with d365_errors(ctx):
        info = themes_mod.create_theme(
            ctx.backend(), name=name, attributes=attributes, logo=logo)
    ctx.emit(True, data=info)
    _journal(ctx, name, info)


@theme_group.command("update")
@click.argument("theme_id")
@click.option("--name", default=None, help="New theme name.")
@_SET_OPTION
@_LOGO_OPTION
@pass_ctx
def theme_update(
    ctx: CLIContext, theme_id: str, name: str | None,
    set_pairs: tuple[str, ...], logo: str | None,
) -> None:
    """Update THEME_ID's name, branding columns (--set), and/or --logo."""
    attributes = _parse_set(set_pairs)
    with d365_errors(ctx):
        info = themes_mod.update_theme(
            ctx.backend(), theme_id, name=name, attributes=attributes, logo=logo)
    ctx.emit(True, data=info)
    _journal(ctx, theme_id, info)


@theme_group.command("publish")
@click.argument("theme_id")
@pass_ctx
def theme_publish(ctx: CLIContext, theme_id: str) -> None:
    """Publish THEME_ID as the active org theme (PublishTheme)."""
    with d365_errors(ctx):
        info = themes_mod.publish_theme(ctx.backend(), theme_id)
    ctx.emit(True, data=info)
    _journal(ctx, theme_id, info)
