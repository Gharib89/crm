# `apply` is a convergent desired-state engine

`crm apply` is no longer a create-only seeder. For the metadata kinds it already
handles — **entity, attribute, global option set** — it now **reconciles** a live
component against the spec instead of blindly skipping it when it exists: read the
live definition, diff it against the desired entry, and classify into one of three
outcomes.

- **equal** → no-op (counts as `skipped`; an unchanged re-apply is idempotent),
- **updatable** → update only the divergent fields the platform allows, in place
  (a retrieve-merge-write PUT or option-set action — the Web API has no metadata PATCH), and
- **immutable / destructive divergence** → **replace-blocked**: reported, **no write
  for that component**, the run ends `ok=false` (exit code `1`).

The result of `apply_spec()` and the verb's emit envelope grow three buckets —
`updated`, `replace_blocked`, `pruned` — alongside the existing
`applied`/`skipped`/`planned`/`failed`/`staged`. They render in human mode and in
`--json` `data`. `pruned` stays empty until the pruning slice (see "Opt-in
pruning" below); `replace_blocked` is populated now.

## Why record this

The shift is hard to reverse, surprising without context, and a real safety
trade-off — exactly what an ADR is for.

- **Convergent, not create-only.** Before, `apply` was a one-way seeder: a component
  that already existed was skipped, so editing a spec and re-applying changed
  nothing on a table that was already there. That quietly violated the "desired
  state" promise of a declarative spec. Now the spec is the source of truth and a
  re-apply converges the org toward it.

- **Refuse on destructive divergence — never silently drop-and-recreate.** Two of the
  divergences the existing kinds can express are not in-place editable on the
  Dataverse Web API: an **entity ownership change** (`OwnershipType` is rejected
  post-create) and an **attribute data-type change** (a column cannot be retyped).
  The only way to "apply" them would be to drop the component and recreate it —
  which **destroys its data and every dependency**. `apply` refuses: it reports the
  divergence as `replace_blocked`, writes nothing for that component, and fails the
  run. An operator who truly wants the change performs the destructive step
  deliberately, outside `apply`. This is the same fail-safe stance as ADR 0007
  (record-clone: no rollback, continue and report).

- **Per-component, no whole-run rollback.** A `replace_blocked` component is a *soft*
  outcome: it does not abort the run, so the components around it still reconcile
  (an updatable entity in the same spec is still updated in place). Metadata writes are not
  transactional, so the already-applied work stays applied and is reported per
  component — consistent with the pre-existing create-path behavior where the first
  hard error aborts the *remaining dependency-ordered* steps but never rolls back
  what already landed.

- **Opt-in pruning (forward reference).** Convergence raises the question of
  components that exist in the org but are *absent* from the spec. Deleting them to
  match desired state is destructive, so pruning will be **opt-in** in a later slice
  of #547 (a `--prune` flag), never the default. The `pruned` bucket is reserved
  now so the envelope shape is stable when that slice lands.

## Scope and limitations (this slice)

- Reconciliation runs on a **real apply only**. Under `--dry-run`, an existing
  component is still reported as `skipped` (the pre-existing preview behavior) — a
  dry-run that previews *would-update* / *would-block* is a follow-up.
- In-place updates cover what the platform allows on the existing kinds: entity
  display name / display-collection name / description; attribute display name,
  description, required level, and string **max-length growth** (shrinking is
  destructive and out of scope); adding spec-declared options to a global option
  set. Only spec-declared fields are reconciled — an omitted field is left as-is,
  never blanked.
- `--stage-only` continues to defer publishing for updates as it does for creates.
- Lookup/customer attributes are relationship-backed (the create path delegates them
  to the relationship builder); their reconciliation belongs with a later
  relationship slice and is skipped here.

## Consequences

- Editing a spec and re-applying now changes the org — intended, but a behavior
  change for anyone who relied on `apply` being create-only. The `updated` bucket
  makes every in-place change visible.
- A spec carrying an ownership or data-type change now **fails** (`ok=false`, exit
  `1`) where it previously silently skipped. This is the safety win, but it is a
  louder failure mode.
- Reconciling an existing component costs extra reads (the live definition, plus the
  typed cast read for string max-length). Metadata apply is not a hot path, so the
  clarity is worth the GETs.
