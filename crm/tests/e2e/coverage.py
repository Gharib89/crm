"""Coverage registry + Click-tree walker for the e2e gate.

`crm.cli.cli` is a `_LazyJsonAwareGroup`: `group.commands` is EMPTY until a
subcommand is loaded. A naive `.commands` recursion returns 0 leaves and the
gate would pass vacuously. The walker therefore drives the lazy loader via
`list_commands`/`get_command` with a real `click.Context`.
"""
# pyright: basic
from __future__ import annotations

import click

from crm.cli import cli

# ── Coverage registry ────────────────────────────────────────────────────
COVERED: set[str] = set()


def covers(*paths: str):
    """Stamp a test with the command path(s) it exercises. Accepts multiple so
    one lifecycle test can own several verbs (create/get/update/delete)."""
    if not paths:
        raise ValueError("@covers requires at least one command path")

    def deco(fn):
        COVERED.update(paths)
        fn._covers = (*getattr(fn, "_covers", ()), *paths)
        return fn

    return deco


# ── Out-of-scope verbs ─────────────────────────────────────────────────────
# Top-level groups that touch no Web API — unit-tested elsewhere.
LOCAL_GROUPS = frozenset(
    {"profile", "session", "skill", "self-update", "repl", "scaffold", "completion"}
)

# D365-touching verbs that genuinely cannot be auto-e2e'd yet. The gate forces a
# reason to be written down. Fill as the gate enumerates the gap (Task 6+).
E2E_SKIP: dict[str, str] = {
    "solution stage-and-upgrade": "needs a managed solution installed first; org-stateful",
    "solution apply-upgrade": "needs a holding solution staged from a prior managed-solution upgrade first; org-stateful (same constraint as stage-and-upgrade)",
    "workflow run": "async side effects on live records; dispatch-only not asserted",
    # extract/pack wrap the legacy, Windows-only, Microsoft-deprecated
    # SolutionPackager.exe, which has no supported Linux runtime — so the .NET SDK
    # added for the plugin fixture does NOT unblock them. Cross-platform migration
    # to `pac solution` is tracked in #500; until then they stay skipped.
    "solution extract": "wraps the legacy, Windows-only, Microsoft-deprecated SolutionPackager.exe (no supported Linux runtime); cross-platform migration to `pac solution` is tracked in #500",
    "solution pack": "wraps the legacy, Windows-only, Microsoft-deprecated SolutionPackager.exe (no supported Linux runtime); cross-platform migration to `pac solution` is tracked in #500",
    # Platform-level (NOT org-specific) Web API restriction: Dataverse rejects
    # creating/upserting a workflow definition via the Web API with
    # "This workflow cannot be created, updated or published because it was created
    # outside the Microsoft Dynamics 365 Web application." The platform enforces
    # this on every org, so a different org does not unblock it. clone/delete/import
    # all require upserting a workflow record and so cannot be exercised live.
    "workflow clone": "clone upserts a new workflow definition via the Web API, which the platform rejects ('… created outside the Microsoft Dynamics 365 Web application'); this is a platform-level block on every org, not org-specific, so a different org does not unblock it",
    "workflow delete": "exercising delete needs a throwaway workflow created via Web API upsert, which the platform blocks on every org ('… created outside the Microsoft Dynamics 365 Web application') — a platform-level restriction, not org-specific",
    "workflow import": "import upserts a workflow definition via the Web API, which the platform blocks on every org ('… created outside the Microsoft Dynamics 365 Web application') — a platform-level restriction, not org-specific, so a different org does not unblock it",
    # The server rejects creating a fieldpermission unless the target attribute is
    # field-secured (IsSecured=true + published) first — verified live: error
    # 0x8004f508 "… is NOT secured …". Securing a standard attribute is heavy,
    # org-stateful metadata setup with teardown risk; the happy path is covered by
    # the wire-level unit tests in crm/tests/test_fieldsec.py.
    "fieldsec add-permission": "creating a fieldpermission requires a field-secured attribute (IsSecured=true + publish) first; org-stateful metadata setup, covered by wire-level unit tests",
    # Creating a usable SLA is heavy, org-stateful setup: `sla create` flips the
    # target entity's IsSLAEnabled metadata flag (a publish-requiring change with
    # no inverse CLI verb to restore it) and SLA records have no clean teardown,
    # and the cloud test org has no Customer Service / SLA configuration. The
    # request shapes (objecttypecode, IsSLAEnabled flip, slaitem condition columns)
    # are covered by the wire-level unit tests in crm/tests/test_sla.py.
    "sla create": "flips target-entity IsSLAEnabled metadata (publish-requiring, no inverse verb) + creates an SLA with no clean teardown; org-stateful, covered by wire-level unit tests",
    "sla add-kpi": "needs a created SLA + an SLA-enabled entity with valid per-KPI FetchXML conditions first (org-stateful, same constraints as `sla create`); covered by wire-level unit tests",
}


# ── Walker ─────────────────────────────────────────────────────────────────
def walk_commands(group: click.Command | None = None,
                  ctx: click.Context | None = None,
                  prefix: tuple[str, ...] = ()):
    """Yield full leaf command paths ('metadata add-attribute', ...)."""
    if group is None:
        group = cli
    if ctx is None:
        ctx = click.Context(group, info_name="crm")
    if not isinstance(group, click.Group):
        yield " ".join(prefix)
        return
    for name in group.list_commands(ctx):       # triggers lazy load on the root
        sub = group.get_command(ctx, name)      # materializes the command
        if sub is None:
            continue
        path = (*prefix, name)
        if isinstance(sub, click.Group):
            yield from walk_commands(sub, click.Context(sub, info_name=name, parent=ctx), path)
        else:
            yield " ".join(path)
