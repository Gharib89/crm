# Schema authoring — scaffold a table, stage then publish

Create one table with its columns in a single call, and batch staged changes
behind one publish. Commands: `scaffold table`, `solution publish-all` /
`publish`. Flags/choices: `crm scaffold table --help`, `crm solution --help`.
The declarative `apply` spec: `reference/authoring.md`.

## Scaffold a table — `scaffold table`

Quick one-liner to create an entity + N columns in a single publish, through the same
`apply` engine. Each resource is `if_exists=skip` — re-running is a no-op.

```bash
crm --json scaffold table "Project" --solution ContosoCore \
  --column "Name:string:max_length=200,required=ApplicationRequired" \
  --column "Due Date:datetime" \
  --column "Owner:lookup:target_entity=systemuser" \
  --column "Priority:picklist:optionset_name=new_priority"

crm --dry-run --json scaffold table "Project" --solution ContosoCore --column "Name:string"   # plan only
crm --stage-only --json scaffold table "Project" --solution ContosoCore --column "Name:string" # no publish
```

Emits the same `{applied, updated, skipped, replace_blocked, pruned, planned, failed}` envelope as `apply`.

**Column shorthand:** `DISPLAY:KIND[:key=value,...]`.

### Dry-run reference-check

Under `--dry-run`, the name-taking writes — `scaffold table`, `metadata
create-one-to-many`, and `metadata add-attribute` — resolve the server objects
they would point at (a lookup's target entity, a picklist's global option set,
a relationship's referenced/referencing entities) and report each under
`data.references[] = {kind, value, _exists}`. A reference that does not resolve
keeps the preview non-failing (`ok: true`) and adds a `meta.warnings` advisory
naming it — so a dangling target catches before the real write 400s, even when
the table itself is only `planned`. (`apply -f` does not yet probe references.)

- `string`/`memo` take an optional `max_length` (defaults 100/2000); `max_length` on
  any other kind is an error.
- `lookup` requires `target_entity=<logical_name>`.
- `picklist`/`multiselect` require `optionset_name=<name>` (an **existing global**
  option set — inline options are not supported here; use `apply` for those).

Column schema names are derived `<publisher_prefix>_<PascalCase(DISPLAY)>` from the
profile's `publisher_prefix` (**required — a missing prefix is exit 2**).
`--schema-name` overrides the entity schema only, not column names.

**Limitations:** no views, no inline picklist options, single entity only — use
`apply -f spec.yaml` for those.

**Global vs local (inline) option set — pick deliberately.** Reserve a *global*
set (`metadata create-optionset`, `apply` top-level `optionsets:`, or
`--optionset-name`) for a list genuinely shared across columns or mapped across
relationships. A single-use list belongs *local* — inline on the column
(`metadata add-attribute --option <value>:<label>`, or `options:` on an `apply`
attribute). Every global set lands permanently in the org-wide catalogue;
minting one per column pollutes it for every future customizer.

## Stage many changes, then publish once

Batch flagless customization writes, then publish once at the end (the
staged-writes contract in SKILL.md):

```bash
crm metadata add-attribute new_widget \
    --kind string --schema-name new_Label --display Label --max-length 100 --solution ContosoCore
crm metadata create-optionset --name new_priority --display Priority \
    --option 1:Low --option 2:High --solution ContosoCore
# ... more staged changes ...
crm solution publish-all   # single publish for all staged customizations
```

Publishing is disruptive on a busy org — an org-wide `publish-all` against
production belongs in a quiet window. Publish selectively instead of all-at-once:

```bash
crm solution publish --xml \
    '<importexportxml><entities><entity>account</entity></entities></importexportxml>'
```

To confirm a staged change actually landed after publish, poll with `--expect` (see
`reference/metadata.md`).
