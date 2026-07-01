---
status: accepted
---

# Require an explicit `--solution` for every customization write

In Dynamics, **every** new or edited customization component is added to the system
Default Solution regardless of any header; `MSCRM.SolutionUniqueName` only
*additionally* files it into a named unmanaged solution. So a command that resolves
no solution does not "target the Default Solution" as a deliberate choice — it
silently orphans the component into Default with no ALM trail. Before this change the
solution target was optional: it fell back to a profile `default_solution`, and
strictness was opt-in (`--require-solution` / `CRM_REQUIRE_SOLUTION`). A profile
default cannot protect the operator (the component still always lands in Default too)
and a **stale** default silently mistargets every write — the failure mode is
orphaned, un-ALM'd customizations discovered late, in the wrong solution
(issue #623 → #636).

## Decision

The solution target is **explicit and mandatory** on every customization write.

- The shared resolver is `_resolve_solution(ctx, explicit) -> str`: it returns the
  explicit `--solution` or raises `click.UsageError` (exit 2) — **no profile-default
  fallback, no `(solution, warning)` tuple, no `require_solution` parameter.** The
  failure fires before any backend call, **including under `--dry-run`**.
- The requirement covers every solution-aware group (metadata create-\*, plugin, web
  resource, form, view, chart, dashboard, sitemap, app, SLA, report, connection role,
  duplicate-detection, field security, security role, scaffold, ribbon) **and**
  `metadata update-entity` / `update-attribute` / `update-relationship` and
  `workflow clone`, which now route through the shared helper for the first time —
  closing the old create-strict / update-silent asymmetry.
- The profile `default_solution` field is removed. A legacy profile JSON carrying the
  key loads fine and drops it on next save — **no migration**.
- Removed knobs (error as unknown option): `profile add/edit --default-solution`, the
  `profile list` `default_solution` column, `--require-solution`,
  `CRM_REQUIRE_SOLUTION`, and `solution create --set-default` (with its
  `default_solution` auto-wire). **Retained:** `solution create-publisher
  --set-default`, which auto-wires `publisher_prefix` (a schema-name default, not a
  solution target — a different concern).
- For the declarative `apply` engine the solution is **declared state**: the spec
  must carry a top-level `solution:` block with `unique_name`, validated before any
  write; the `apply --solution` override flag is removed, and the `--prune`
  target-solution guard collapses into the mandatory check.
  `metadata export-spec --solution X` bakes the `solution:` block into the emitted
  spec so the export→apply round-trip stays seedable (see ADR 0019); `export-spec`
  without it still emits a valid but non-appliable document.
- **Escape hatch:** a deliberate Default-Solution-only write passes `--solution
  Default` explicitly. There is no `--no-solution` flag — the requirement is always
  satisfiable, never silent.

Error message when omitted: *"--solution is required for customization writes —
components must target an explicit unmanaged solution. Pass --solution
<unique_name>."* Apply's spec-missing-solution message is analogous, pointing at the
`solution:` block / `export-spec --solution`.

## Considered options

- **Keep the opt-in strictness knobs (status quo).** Rejected: strictness that
  defaults off is the trap — the common path stays silent, and a stale profile
  default keeps mistargeting writes. Making strictness the *only* behavior is the
  point of #623.
- **Keep the profile `default_solution` as the source of truth.** Rejected: a profile
  default is invisible at the call site, cannot be audited from the command line, and
  a stale value silently mistargets. The target belongs on the command line (or in
  the spec file) — reproducible and reviewable.
- **`apply --solution` as a per-run override.** Rejected: the solution a spec applies
  into is part of its desired state, not a runtime knob; a spec that means different
  things depending on an out-of-band flag is not reproducible. Declaring it in the
  file keeps the export→apply round-trip honest.

## Consequences

- **Breaking.** Any script or profile relying on `default_solution`,
  `--require-solution`, `CRM_REQUIRE_SOLUTION`, `solution create --set-default`, or
  `apply --solution` breaks loudly (exit 2 / unknown option) rather than silently
  mistargeting. The fix is always to pass `--solution <name>` (or add a `solution:`
  block).
- No server behavior is added — only a client-side argument is made required — so the
  requirement is proven **offline** (CliRunner, exit 2 before any HTTP); no new live
  e2e test is warranted. Existing metadata/plugin/workflow/apply e2e tests pass an
  explicit `--solution`.
- **Partially supersedes ADR 0002:** the `solution create` → `default_solution`
  auto-wire half is removed; the `create-publisher` → `publisher_prefix` auto-wire
  half stands.
