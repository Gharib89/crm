# `apply` spec surface extends to UI kinds — forms, model-driven app, sitemap

ADR 0014 made `apply` a convergent desired-state engine and ADR 0018 widened the
reconciled field surface; both stopped at *schema and automation* kinds. The
spec-surface PRD (#791) extends the surface to the *UI* kinds — **forms**,
**model-driven apps**, and **sitemaps** — so a single spec carries a whole
customization (schema + automation + UI) through the same convergent, publish-once,
plan-gated pipeline. This ADR fixes the cross-cutting decisions the PRD's downstream
tickets (#793–#798) build on, so they are unblocked by *decisions*, not by code:
the per-field classification for all three kinds, the **form-ownership stance**,
prune eligibility, the plan-format decision, and the export-spec projection approach
for the non-entity-rooted app kind. Only the **forms create path** is implemented
here (#792); the rest land per the classification below.

## Form-ownership stance — converge the platform-generated main form

A spec `forms:` block is **nested under an entity** and **converges that entity's
platform-generated main form** — it does not create a form from scratch. The
decision, and why:

- **The platform already auto-creates a valid, renderable main form** the moment an
  entity is created. There is *no from-scratch form-create verb* in the CLI today,
  by design: a hand-forged `<form>` skeleton is a large, brittle surface (a real
  main form carries `header`/`Navigation`/`DisplayConditions`/`clientresources`/…),
  and the platform rejects or mis-renders an incomplete one. Reusing the
  platform's own main form as the base makes every applied form valid by
  construction.
- **Convergence reuses the existing, integrity-checked builders.** The declared
  tabs → sections → fields → libraries → handlers are layered onto the live formxml
  with the same `add_*_to_formxml` primitives the `crm form` verbs use (each already
  asserts classid / sibling-GUID integrity), then committed with one `formxml`
  PATCH. Field control `classid`s resolve from live attribute metadata; a declared
  library must already exist as a web resource.
- **Target selection.** A block's optional `name` selects among the entity's main
  forms; absent, the entity's primary main form is used. A named form that does not
  exist is not created from nothing — the stance is *converge an existing main
  form*, so an unknown name is an error, not a silent create.
- **Create vs reconcile split.** #792 (this ADR) implements the **additive create
  path**: a declared tab/section/field/library/handler *absent* from the live form
  is added; a component already present is left untouched (re-apply is idempotent →
  `skipped`). Converging *drift* in a component that is present but differs
  (relabel, re-place, re-order, toggle) is the **reconcile** slice (#793), per the
  classification below.

On a new entity the whole thing composes: `apply` creates the entity, the platform
auto-creates the main form, and the forms phase converges it in the same run
(verified live). A greenfield entity whose main form is not yet readable — or a
`--dry-run` would-create entity — reports the form as `planned`.

## Classification

**Updatable** = converged in place. **Replace-blocked** = identity/destructive
divergence, refused with no write (ADR 0014 stance). **Create-only** = set at create,
not reconciled.

### Forms (`forms:` under an entity)

| Component / field | Verdict |
|---|---|
| tab present/absent; section present/absent; field placement (add) | **updatable** (additive; the #792 create path) |
| tab label / section label; field tab+section (re-place); tab/section order | **updatable** (reconcile, #793 — `rename_*`/`move_*` builders) |
| library registration; handler (event, function[, onchange field]) | **updatable** (additive create path; reconcile converges flags) |
| removing the only tab, or a tab/section still holding bound fields | **replace-blocked** (the builders already refuse to orphan bound fields) |
| field control `classid` (derived from attribute type) | **create-only** (a placed field's control type is not retyped in place) |
| form `name` / `type` / owning entity | **replace-blocked** (identity) |

### Model-driven app (`apps:` top-level — #795/#796)

| Component / field | Verdict |
|---|---|
| app component set (tables, forms, views, …) added/removed | **updatable** (converge the app-module component set) |
| sitemap areas / groups / subareas | **updatable — whole-document replacement** (a sitemap is edited as one XML document; converge = replace the declared sitemap wholesale, not a per-node diff) |
| app `uniquename` | **replace-blocked** (identity) |
| app existence (create) | **create-only** via the existing app-module + sitemap builders |

### Sitemap

A sitemap is owned by its app and converges *with* it (whole-document replacement,
above). A standalone sitemap kind is out of scope — navigation is expressed through
the app's `apps:` block.

Two cross-cutting principles carry over from ADR 0018: **identity divergence →
replace-blocked**, and **destructive-only divergence → refused** (never
drop-and-recreate).

## Prune eligibility

**Forms and apps are out of scope for `--prune`.** The six prune-eligible kinds
(entity, attribute, view, security-role, webresource, plugin-step; ADR 0014) are
unchanged. A form under spec control is a *converged platform main form*, not an
independently owned component — deleting it (or a model-driven app) is a
destructive, identity-laden act with no safe solution-bounded detection, so neither
is added to the eligible set. This is a documented decision, not a deferral.

## Plan-format version — no bump

The plan artifact (ADR 0022, `plan_format` v1) records each component as
`{kind, name, verdict, changed-field set}` with `kind` a free string. Form and app
components fit that shape unchanged — a `kind: "form"` entry carrying its added/
converged `components` needs no new structure. **`plan_format` stays at 1**; #798
wires the UI kinds into the plan → verify → `--from-plan` loop without a format
bump. Recording the *no-bump* rationale is the point: a bump would needlessly
invalidate every pending plan across the release that ships UI kinds.

## Export-spec projection for apps (the non-entity-rooted kind)

ADR 0019 established the seedable invariant (emit only what a *real* `apply` can
re-seed) and an **entity-rooted** exporter (only `entity` solution members drive
projection). The UI kinds split along that axis:

- **Forms are entity-rooted** — a form rides along inside its parent entity's
  projection, emitted under that entity's `forms:` block, subject to the ADR 0019
  seedable invariant (#794). A form that cannot round-trip lands in `skipped` with a
  reason, never emitted diff-only.
- **Apps are not entity-rooted.** A model-driven app is a top-level solution member
  the current entity walk cannot reach. The exporter gains a **separate top-level
  pass over `appmodule` solution members** that projects each app (components +
  sitemap) under a top-level `apps:` block (#797), governed by the same seedable
  invariant. This is the documented mechanism the ADR fixes; the entity walk is left
  untouched.

## Why record this

The form-ownership stance (converge, don't forge) and the updatable-vs-destructive
line per UI field are safety decisions, hard to reverse once specs and operators
depend on them, and not obvious from the code. Fixing them here lets #793–#798 build
on decisions rather than re-litigate them per ticket, alongside ADR 0014 (convergent
engine), 0018 (wider reconcile surface), 0019 (export seedable invariant), and 0022
(plan artifact).

## Consequences

- A spec can now declare an entity's form layout; re-applying converges the main
  form and publishes once with the rest of the customization. The `applied` /
  `planned` / `skipped` buckets make each form change visible, exactly like schema
  kinds.
- `apply` never forges a form from scratch — the create path always starts from the
  platform's valid main form, so an applied form is renderable by construction.
- Forms/apps stay out of `--prune`; an operator removing a form or app does so
  deliberately, outside `apply`.
- The plan artifact carries UI kinds at `plan_format` 1 — no pending-plan
  invalidation when the feature ships.
