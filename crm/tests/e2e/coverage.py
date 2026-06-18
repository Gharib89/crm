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
    "plugin register-assembly": "needs a prebuilt signed test .dll; tracked in GH issue",
    "solution stage-and-upgrade": "needs a managed solution installed first; org-stateful",
    "solution apply-upgrade": "needs a holding solution staged from a prior managed-solution upgrade first; org-stateful (same constraint as stage-and-upgrade)",
    "workflow run": "async side effects on live records; dispatch-only not asserted",
    # async cancel modifies live job state; no safe always-present cancellable job exists
    "async cancel": "cancelling arbitrary live asyncoperations is destructive; no safe read-only test target",
    "solution extract": "requires the SolutionPackager .NET tool (Microsoft.CrmSdk.CoreTools); not available in the Linux CI environment",
    "solution pack": "requires the SolutionPackager .NET tool (Microsoft.CrmSdk.CoreTools); not available in the Linux CI environment",
    "solution job-cancel": "needs an in-flight async import job to cancel; not deterministically reproducible without races",
    # Both test orgs reject Web API workflow upsert/create with org-level policy:
    # "This workflow cannot be created, updated or published because it was created
    # outside the Microsoft Dynamics 365 Web application." clone/delete/import all
    # require creating or upserting a workflow record and cannot be exercised safely.
    "workflow clone": "creating a workflow via the Web API is blocked by org policy on both test orgs; clone requires upsert of a new workflow definition",
    "workflow delete": "creating a throwaway workflow to delete requires Web API upsert which is blocked by org policy on both test orgs",
    "workflow import": "import (upsert) of a workflow definition is blocked by org policy on both test orgs",
    # plugin unregister-* require a registered assembly/step to delete; safe
    # setup (register-assembly + register-step) is itself in E2E_SKIP.
    "plugin unregister-assembly": "requires a registered plugin assembly to unregister; register-assembly is in E2E_SKIP (needs a prebuilt signed .dll)",
    "plugin unregister-step": "requires a registered plugin step to unregister; creating one needs a registered assembly (register-assembly is in E2E_SKIP)",
    "audit detail": "needs a pre-existing audit row to decode; the cloud test org has org-level auditing disabled (isauditenabled=false) and an empty audits table, and enabling org auditing + generating audited data is not safe/deterministic within e2e",
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
