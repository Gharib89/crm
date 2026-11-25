# Metadata — export-spec, clone-entity, incremental sync

Round-trip a live entity to an apply spec, clone a custom entity, and sync
metadata incrementally. Group: `metadata`. Flags/choices: `crm metadata --help`.
Browsing and picklists: `reference/metadata.md`. A whole solution as one spec
(`solution export-spec`): `reference/solutions-drift.md`.

## Export a live entity as an apply spec (round-trip)

```bash
crm metadata export-spec new_project --with-views --with-relationships --with-forms \
    --solution ContosoCore -o project.yaml
crm apply -f project.yaml   # re-create / idempotently re-apply in any environment
```

`--solution` bakes the mandatory top-level `solution:` block into the spec — `apply`
requires one (there is no `apply --solution` flag); without it here, `apply -f`
would reject the spec.

`export-spec` reads the entity over the Web API (pure GETs) and emits a `crm apply -f`
desired-state spec (see `reference/authoring.md`). With `-o FILE` it writes bare YAML
directly consumable by `apply`; without `-o` the spec is wrapped in the JSON envelope.

It captures: entity definition, primary-name attribute, all custom apply-creatable
columns (including calculated and rollup columns with their `source_type` and
`formula_definition` XAML), referenced global option sets, and (with flags)
relationships, views, and the seedable main form.

`--with-forms` projects the entity's main form as a `forms:` block — the *inverse*
of `apply`'s forms convergence (ADR 0024), governed by the ADR 0019 seedable
invariant. It emits only what a real `apply` can layer back onto a fresh org's
platform main form: the **custom** fields and their tab/section placement,
registered JS libraries, and seedable event handlers. The block carries no form
`name`, so a round-trip apply targets the destination org's own primary main form.
Silently omitted: the primary-name field and every platform/system field (already
on the destination form), and tabs/sections that carry no custom field. Dropped to
`meta.warnings`: a field whose control type has no seedable classid (multi-select,
double, …) and a handler on a non-seedable event. Reported in the `skipped` bucket
(`meta.skipped` under the JSON envelope, `data.skipped` in the `-o` summary): an
additional (non-primary) main form — `apply` converges only the destination primary
main form, never forging a second one. A field whose form label differs from its
column's display name carries that `label`; `apply` sets it only when it adds the
field to the form.
A publisher is never emitted. A top-level `solution:` block is emitted only when
`--solution <name>` is passed to `export-spec` — `apply` requires one (there is no
`apply --solution` flag), so a spec exported without it is valid but not appliable
until you add the block (or re-export with `--solution`). **Fidelity caveats**
(these silently lose information on round-trip):

- A string column whose live format is `Json` or `RichText` is re-created as plain `Text`.
- A datetime column's display *format* is NOT captured (re-created with the server
  default format); its `DateTimeBehavior` IS captured as `behavior_name` when it
  differs from the `UserLocal` default.
- A polymorphic (multi-target) lookup is exported with its **first target only** and
  re-created as a single-target lookup.
- A calculated/rollup column whose `FormulaDefinition` cannot be read is exported as
  a plain simple column (a warning is emitted in `meta.warnings`). The reconcile
  pass does **not** compare formulas — formula drift is not detected or updated.

`apply` ignores unknown keys, so the spec always stays apply-consumable.

## Clone a custom entity

Duplicate a custom entity under a new schema name. The bare clone copies entity
definition, custom attributes (lookups recreated pointing at the same parent tables),
and reuses referenced global option sets by name. Forms, views, workflows, and charts
are opt-in.

`clone-entity` is solution-scoped (`--solution` required).

```bash
# skeleton only (entity + attributes + lookups + reused option sets)
crm --json metadata clone-entity new_project contoso_TicketClone --display "Ticket Clone" --solution MySolution

# everything cloneable over the API
crm --json metadata clone-entity new_project contoso_TicketClone --with-all --solution MySolution

# opt-in flags
crm --json metadata clone-entity new_project contoso_TicketClone \
    --with-forms --with-views --with-workflows --with-charts --solution MySolution
```

**Not cloned (Web API limits):**

- **Ribbon** — `RibbonDiffXml` has no Web API write path (solution import only). The
  result carries a `ribbon_note` confirming this. The clone's ribbon is still
  *recreatable*: `ribbon export` on the source to inspect its buttons, then
  `ribbon add-button` on the target (see `reference/webresource-ribbon.md`).
- **N:N relationships**, and 1:N where the source is the *parent* (referenced) side —
  cloning those would add lookups on *other* tables.
- **Polymorphic / Customer lookups** — only single-target lookups come across.
- **Personal charts** (`userqueryvisualization`) — not cloned; public system charts are
  handled by `--with-charts`.

`--with-workflows` copies every classic workflow/business rule (`type=1`) whose
primary entity is the source, including managed ones (no "is custom" filter available).
Actions, BPFs, dialogs, and modern flows are skipped (reported under
`skipped_workflows`) — read each entry's `reason` rather than treating the bucket
as a dead end: for Actions and BPFs it names the working path (`solution
export`/`import` redeploys the original process — it does not retarget it to the
clone; see `reference/automation-workflows.md`). On Unified Interface a cloned form may need adding to the
model-driven app's form list to be visible.

## Incremental metadata sync (`metadata changes`)

`metadata changes` wraps `RetrieveMetadataChanges`. The contract: save the returned
`server_version_stamp` and pass it back as `--since` next run to get only the delta.
Omit `--since` for a baseline snapshot.

```bash
# Baseline — returns all visible metadata + a fresh stamp to save
crm --json metadata changes

# Delta — only entities changed since the prior stamp
crm --json metadata changes --since "<saved stamp>"

# Scope to specific tables (strongly recommended on baseline calls)
crm --json metadata changes --since "<saved stamp>" --entity account --entity contact

# Also expand column definitions (larger response)
crm --json metadata changes --since "<saved stamp>" --entity account --attributes
```

**Critical gotcha — unfiltered baseline is expensive.** Omitting `--entity` on a
baseline call (`--since` omitted) is equivalent to `RetrieveAllEntities` — a heavy
call on orgs with many tables. Scope with `--entity` whenever you only need a known
subset of tables.

**JSON shape:**

```json
{
  "server_version_stamp": "<opaque cursor — save, pass as --since next run>",
  "entities": [
    {
      "logical_name": "account",
      "schema_name": "Account",
      "has_changed": true,
      "attributes": [{"logical_name": "name", "attribute_type": "String", "has_changed": true}]
    }
  ],
  "count": 1,
  "deleted_count": 0
}
```

`attributes[]` only appears when `--attributes` is passed. `deleted_count` is the
count of deleted metadata components since the stamp — the API returns the count
only, not their identities. This is a pure read; it runs live under `--dry-run`.
