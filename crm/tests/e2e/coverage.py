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
# reason to be written down here whenever a verb is added to this map.
#
# Empty today: every D365-touching verb has live coverage. `workflow clone` /
# `import` / `delete` used to live here on the premise that the platform rejects
# upserting a workflow definition via the Web API ("… created outside the Microsoft
# Dynamics 365 Web application", 0x80045040). #534 disproved that: the wall is
# XAML-provenance-sensitive, not target-sensitive — the platform rejects only
# foreign hand-authored XAML and accepts genuine designer XAML, on both targets.
# clone/import reuse a real workflow's designer XAML, so the upsert is accepted;
# they now have live coverage in test_workflow.py.
#
# `workflow activate` / `deactivate` are @covers-stamped (test_workflow.py) so they
# are NOT in this map, but they remain *data-gated*: the test toggles a pre-existing
# draft and skips on a bare org. They are deliberately not self-seeded — activating
# a throwaway clone leaks an undeletable type=2 activation copy on on-prem v9.1 (it
# survives deactivate and orphans on parent-delete, 0x80045004), so a seed-and-delete
# cycle cannot leave the org clean. They run whenever a suitable draft exists (e.g.
# the ADR 0012 / #503 seeded workflow). See test_workflow_activate_deactivate.
E2E_SKIP: dict[str, str] = {}


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
