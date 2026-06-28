# `apply` reconcile extends to the wider spec surface

ADR 0014 made `apply` a convergent desired-state engine for the kinds it then
reconciled (entity, attribute, global option set, web resource, security role,
plug-in assembly, plug-in step). It deferred two things: **relationships were
create-only** (no reconcile at all), and the spec-adapter registry (#603) later
made a **wider field surface** expressible — relationship cascade/menu/hierarchical
and the relationship-backed lookup label/required, entity `has_notes`/`has_activities`,
view `filter_active`/columns/etc. — without a matching reconcile path. This ADR
records how each newly-reconcilable field is classified — **updatable** (converged
in place) versus **replace-blocked** (refused as a destructive or identity
divergence) — and which fields stay deferred.

## Classification

| Kind | Field(s) | Verdict |
|---|---|---|
| Relationship 1:N | cascade_assign/delete/reparent/share/unshare/merge | **updatable** (`CascadeConfiguration`) |
| Relationship 1:N | menu_label / menu_behavior / menu_order | **updatable** (`AssociatedMenuConfiguration`) |
| Relationship 1:N | is_hierarchical | **updatable** (platform rejects an invalid toggle → `failed`, not blocked) |
| Relationship 1:N | lookup_display / lookup_description / lookup_required | **updatable** (the lookup attribute — closes ADR 0014's lookup deferral) |
| Relationship 1:N | referenced_entity / referencing_entity | **replace-blocked** (identity) |
| Entity | has_notes / has_activities: off→on | **updatable** (additive) |
| Entity | has_notes / has_activities: on→off | **replace-blocked** (platform forbids disabling) |
| Entity | is_activity | **replace-blocked** (identity) |
| View | description / filter_active / columns / order_by / is_default | **updatable** (`PATCH savedqueries`) |
| View | name / query_type | **not in-place; a change creates a new view** (see below) |

Two cross-cutting principles fall out:

- **Enable-only capability.** `has_notes`/`has_activities` can be switched on after
  creation but the platform *forbids switching them off* ("can only be enabled once.
  After it is enabled it cannot be disabled" — MS Learn). So enabling is updatable;
  an explicit disable is replace-blocked, because the only way to honour it is a
  destructive drop-and-recreate, which `apply` refuses (the ADR 0014 stance).
- **Identity divergence → replace-blocked.** A change to a component's identity —
  entity ownership (0014), attribute data-type (0014), a relationship's
  referenced/referencing entity, `is_activity` — is not editable in place; `apply`
  refuses rather than drop-and-recreate.

## Why record this

The updatable-vs-destructive line per field is a safety decision, hard to reverse
once specs and operators depend on it, and not obvious from the code. It also
corrects an intuitive-but-wrong assumption: a view `query_type` change looks like a
replace-blocked identity change, but it is not — see views below.

## Scope and mechanism

- **Relationship reconcile** reuses the existing `update_relationship` primitive
  (`metadata_update.py`): the write half is already done (the cast-read / un-cast-PUT
  contract and the #267 405 workaround), so this is reconcile *wiring* in `apply.py`
  — read live → diff → classify → route — mirroring `_reconcile_entity`, not a new
  subsystem. It is matched by `SchemaName`; a referenced/referencing/type divergence
  on a matched relationship is replace-blocked. The same path also reconciles the
  relationship-backed **lookup attribute** (display/description/required via
  `update_attribute` on the referencing entity), reported as one `updated` entry per
  relationship block.
- **Entity** reconcile gains `has_notes`/`has_activities` (enable-only) and
  `is_activity` (identity) on the existing `_reconcile_entity` path.
- **View** reconcile is a new but simple **record** path: `PATCH savedqueries(id)`
  for description / fetchxml (from `filter_active`/`order_by`/`order_desc`) / layoutxml
  (from `columns`) / `isdefault`. `savedquery` has no alternate key, so a view is
  matched by `(entity, name, query_type)` — its identity for matching. A changed
  `name` or `query_type` therefore does not reconcile the live view; it creates a new
  one (the old one is left for `--prune`). An ambiguous match (>1 view) skips with a
  reason rather than patching an arbitrary row.
- Reconcile reads run under `--dry-run` with writes suppressed and a field-level
  `diff` reported, per ADR 0014's reads-execute rule.

## Deferred (out of scope)

- **`customer`** polymorphic lookups (two backing 1:N relationships) — reconcile
  stays skipped, as in ADR 0014.
- **Virtual / external entity fields** (`data_provider_id`, `data_source_id`,
  `external_name`, `external_collection_name`) — own semantics, niche surface.
- **N:N relationships** — the spec surface is 1:N only; `update_relationship` already
  rejects N:N cascade/menu/hierarchical.

## Consequences

- Editing cascade/menu/hierarchical, a lookup label, or an enable-only capability on
  an existing component now converges on re-apply where it was previously silently
  skipped — the `updated` / `replace_blocked` buckets make every change visible.
- An explicit spec that would disable notes/activities, change a relationship's
  entities, or convert `is_activity` now **fails** (`ok=false`, exit `1`) instead of
  doing nothing — louder, but the safe outcome.
- Implementation decomposes by *mechanism* (relationship reconcile; entity + view
  reconcile), not by the three buckets of #598.
