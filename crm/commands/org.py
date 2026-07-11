"""`crm org` — org-level orientation commands (#790).

`brief` is the group's first (and currently only) verb: a single read-only call
that returns a summarized org inventory shaped for an agent's context budget.
"""

# pyright: basic
from __future__ import annotations

import click

from crm.cli import CLIContext, pass_ctx
from crm.commands._helpers import d365_errors
from crm.core import org as org_mod


def _cell(value) -> str:
    """Render a table cell: None → '' (never the literal string 'None')."""
    return "" if value is None else str(value)


@click.group("org")
def org_group():
    """Org-level orientation (inventory, recon)."""


@org_group.command("brief")
@pass_ctx
def org_brief(ctx: CLIContext):
    """One-call read-only org inventory: counts and key names, never full rows.

    Consolidates what six fat list verbs (`solution list`, `metadata entities`,
    `webresource list`, `plugin list`, `workflow list`, `app list`) would return
    into one summarized brief, so an agent can orient without spending its context
    budget. Sections: identity, solutions, publishers, schema, apps, automation,
    components. Read-only — safe to run first.
    """
    with d365_errors(ctx):
        brief = org_mod.org_brief(ctx.backend())

    meta = {
        "custom_entities": brief["schema"]["custom_entities"],
        "solutions": brief["solutions"]["managed"] + brief["solutions"]["unmanaged"],
        "apps": brief["apps"]["count"],
        "plugin_steps": brief["automation"]["plugin_steps"],
        "workflows": brief["automation"]["workflows"]["total"],
    }

    if ctx.json_mode:
        ctx.emit(True, data=brief, meta=meta)
        return

    # Human mode: one labeled table per section (emit renders only a single
    # table, so drive the skin directly — cf. `metadata relationships`).
    ident = brief["identity"]
    ctx.skin.section("Identity")
    ctx.skin.table(
        ["key", "value"],
        [
            [k, _cell(ident.get(k))]
            for k in (
                "org_name",
                "version",
                "url",
                "profile",
                "api_version",
                "user_id",
                "organization_id",
            )
        ],
    )

    sol = brief["solutions"]
    ctx.skin.section("Solutions")
    ctx.skin.table(
        ["managed", "unmanaged", "candidate targets (unmanaged, non-default)"],
        [
            [
                str(sol["managed"]),
                str(sol["unmanaged"]),
                ", ".join(sol["unmanaged_names"]) or "(none)",
            ]
        ],
    )

    ctx.skin.section("Publishers")
    pubs = brief["publishers"]["items"]
    ctx.skin.table(
        ["unique_name", "prefix", "friendly_name"],
        [
            [_cell(p.get("unique_name")), _cell(p.get("prefix")), _cell(p.get("friendly_name"))]
            for p in pubs
        ]
        or [["(none)", "", ""]],
    )

    schema = brief["schema"]
    ctx.skin.section("Schema")
    ctx.skin.table(
        ["custom entities", "global option sets", "custom entity names"],
        [
            [
                str(schema["custom_entities"]),
                str(schema["global_optionsets"]),
                ", ".join(schema["custom_entity_names"]) or "(none)",
            ]
        ],
    )

    apps = brief["apps"]
    ctx.skin.section("Apps")
    ctx.skin.table(
        ["count", "names"],
        [[str(apps["count"]), ", ".join(apps["names"]) or "(none)"]],
    )

    auto = brief["automation"]
    ctx.skin.section("Automation")
    ctx.skin.table(
        ["plugin assemblies", "plugin steps", "workflows", "SLAs"],
        [
            [
                str(auto["plugin_assemblies"]),
                str(auto["plugin_steps"]),
                str(auto["workflows"]["total"]),
                str(auto["slas"]),
            ]
        ],
    )
    wf_rows = [
        [cat, str(counts["total"]), str(counts["activated"])]
        for cat, counts in sorted(auto["workflows"]["by_category"].items())
    ]
    if wf_rows:
        ctx.skin.table(["workflow category", "total", "activated"], wf_rows)

    comp = brief["components"]
    wr = comp["webresources"]
    ctx.skin.section("Components")
    ctx.skin.table(
        ["web resources", "by type", "custom security roles", "duplicate rules"],
        [
            [
                str(wr["total"]),
                ", ".join(f"{k}={v}" for k, v in wr["by_type"].items()),
                str(comp["security_roles_custom"]),
                str(comp["duplicate_rules"]),
            ]
        ],
    )
