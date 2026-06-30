---
status: accepted
supersedes: ADR 0002 (partial — `default_solution` auto-wire only; `publisher_prefix` auto-wire remains)
---

# Require explicit `--solution`; drop profile `default_solution`

Issue #623.

## Decision

**`--solution <unique_name>` is required on every customization write** — metadata
create/update/delete of components, plug-in registration, web resources, charts,
dashboards, forms, reports, ribbons, sitemaps, and any other command that sends
`MSCRM.SolutionUniqueName`. Omitting it raises `UsageError` (exit 2):

> `--solution is required for customization writes — components must target an
> explicit unmanaged solution. Pass --solution <unique_name>.`

The profile `default_solution` field is removed. Legacy profile JSON files that
carry the key load fine and silently drop it on the next save — no migration
needed.

The `--require-solution` flag and `CRM_REQUIRE_SOLUTION` env var are removed.
Strict mode is now the only mode, so the opt-in mechanism is redundant.

`solution create --set-default` is removed (it auto-wired `default_solution`).
`solution create-publisher --set-default` (which auto-wires `publisher_prefix`)
is kept — a publisher prefix is a schema-name aid, not a solution targeting
mechanism, and its auto-wire is still safe and useful.

## Rationale

In D365 / Dataverse, every new metadata component is **always** added to the
system Default Solution regardless of the `MSCRM.SolutionUniqueName` header;
the header only *additionally* files the component into a named unmanaged
solution. A profile-level `default_solution` therefore cannot prevent a write
from landing in the Default Solution — it only hides which (if any) named
solution the component also targets.

The former behaviour (profile default, opt-in `--require-solution`) created a
silent failure mode: a user who forgot `--solution`, or whose profile default
had drifted, would silently customise the Default Solution rather than their
intended unmanaged solution. The only indication was a non-fatal warning in
`meta.warnings` — easy to miss in agent pipelines. With a hard `UsageError`
the mistake is caught at the point of invocation, not after the fact.

## Considered options

- **Keep `default_solution` with a deprecation warning.** Rejected: a deprecated
  field that still silently succeeds is not meaningfully different from the
  status quo. Turning every silently-wrong write into an explicit error is the
  point.
- **Keep `--require-solution` as an opt-in.** Rejected: once `--solution` is
  required by default, `--require-solution` is a no-op — it adds UI surface with
  no behaviour difference. Removing it keeps the flag set clean.
- **Auto-infer `--solution` from `solution list --unmanaged` (single result).**
  Rejected: deterministic only when exactly one unmanaged solution exists. A
  heuristic that silently targets the wrong solution in the two-solution case is
  worse than the current silent Default Solution write. Explicit is better.

## Consequences

- Every scripted or agent-driven customization workflow must pass `--solution`
  explicitly. This is a **breaking change** for any caller that relied on the
  profile default.
- `crm --json profile list` no longer emits `default_solution` in its data
  rows. Any automation parsing that field will see it absent (not `null`).
- `crm profile add` and `crm profile edit` no longer accept `--default-solution`.
  A caller passing it gets a `No such option` error (exit 2).
- The `apply -f spec.yaml` flow is unaffected: the spec's `solution:` block
  already provides the target explicitly.
- Trade-off: less convenience (no zero-arg metadata writes) for no
  silently-mistargeted customizations.

## New verb: `profile rename`

Issue #623 also adds `crm profile rename OLD NEW` as a companion housekeeping
verb (unrelated to the `default_solution` removal, but shipped in the same
change). It renames a profile file, rewrites the active-session pointer when the
renamed profile is currently active, migrates the OS keyring entry (best-effort),
and moves the cache directory. It refuses to clobber an existing profile named
`NEW`. This is recorded here rather than a separate ADR because it does not
introduce a new design question — it is a straightforward CRUD operation on the
profile store, consistent with `profile add`, `profile edit`, and `profile rm`.
