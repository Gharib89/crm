# `solution export-spec` emits only apply-seedable components

`crm solution export-spec <unique_name>` (#613) projects a live solution into a
single merged desired-state spec — the source side of the org-to-org drift recipe
(`export-spec` on dev → `crm --dry-run apply -f <file>` on prod, parent #611). It walks
the solution's members and merges every entity it touches via `build_entity_spec`
(ADR 0014's projector inverse). This ADR records the invariant that governs *which*
components the verb is allowed to emit.

## Decision

**The exporter emits only components that round-trip through a *real* `apply`
(create + reconcile) — not merely through `--dry-run`.** A component that cannot be
re-created from the projected spec is reported in a `skipped` bucket, never emitted.

The litmus test is "can `apply_spec` build this from the spec alone, against an org
that does not already have it?" — because the recipe's whole point is seeding a
*second* org, not just diffing the first.

| Component | Verdict | Why |
|---|---|---|
| entity / attribute / global option set / view / 1:N relationship | **emitted** | `build_entity_spec` projects them and `apply` creates + reconciles them (ADR 0014/0018). |
| plug-in assembly / type / step | **skipped** | The assembly's DLL **bytes do not exist in a live org's metadata** — only registration rows do. A spec could carry the registration but not the binary, so `apply` could not seed it on the other org. |
| security role / web resource | **skipped (deferred)** | Apply-seedable in principle (ADR 0014 reconciles them), but projection is a follow-up slice; reported, not silently dropped. |
| everything else (forms, workflows, sitemap, …) | **skipped** | Not in the apply spec surface. |

## Why record this

The tempting alternative is to emit plug-ins as a *diff-only* block — enough for a
`--dry-run` comparison to flag "present on dev, absent on prod". This ADR rejects
that: a spec that `--dry-run` accepts but a real `apply` cannot seed is a **trap** —
an operator who runs the recipe end-to-end would believe the spec reproduces the
solution, then discover at apply time that the plug-in never transferred. The skip
with an explicit reason is honest where a diff-only block would mislead. The rule is
hard to reverse once operators depend on the emitted spec being seed-complete, and
it is not obvious from the code — hence the record, alongside ADR 0014 (apply
convergent engine) and ADR 0018 (apply reconcile wider spec surface).

## Scope and mechanism

- **Entity-rooted only, full-definition.** Only `entity` solution members drive
  projection; each is resolved (`objectid = MetadataId` → logical name, one
  `EntityDefinitions` GET) and projected in full via `build_entity_spec`
  (`with_views`, `with_relationships`). An entity's attributes, views, 1:N
  relationships and referenced global option sets ride along inside that
  projection; global option sets shared by multiple entities are de-duplicated by
  name.
- **À-la-carte single-column membership is a documented simplification.** A
  subcomponent member added without its parent entity (a lone `attribute` / `view` /
  `relationship`) is *not* separately projected — it lands in `skipped` with that
  reason. The common case (a whole entity in the solution) needs no such handling;
  the simplification only affects hand-curated partial solutions.
- **`solution:` scope key.** The emitted spec carries `{"solution": {"unique_name":
  …}}` so the round-trip `apply --dry-run` auto-scopes its drift/prune report to the
  same solution. Publisher is omitted — a live solution does not expose it and a
  dry-run diff does not need it.
- **Never fails on an unsupported component.** Every non-emitted member is recorded
  in `skipped` (`{type, objectid, reason}`); the verb exits `0` / `ok=true` even when
  the whole solution is unsupported. Read-only throughout (pure GETs).

## Consequences

- The exported spec is **seed-complete for the kinds it contains**: what it emits can
  be applied to a fresh org, not just diffed against the source.
- A solution that leans on plug-ins exports a spec covering only its entity surface;
  the `skipped` bucket tells the operator exactly what the recipe will not carry, so
  plug-in transfer stays a conscious, separate step (managed solution import).
- Widening the emitted surface (security roles, web resources) is additive in a later
  slice and does not change this invariant — it only moves rows from `skipped` to
  `emitted` once their projection round-trips a real apply.
