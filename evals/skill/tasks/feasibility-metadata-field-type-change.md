---
id: feasibility-metadata-field-type-change
domain: metadata-writes
tier: 3
source:
  type: forum
  url: https://community.dynamics.com/forums/thread/details/?threadid=76dc9646-c5a2-4487-92bc-bf3e509a2d26
# Metadata demand cluster (#899), pilot harvest #885 row 5 — "Changing the Field type of Existing
# field" (convert a text/String column to a Whole Number and keep the existing values). Modeled
# as feasibility, not do: the discriminator is knowing that a column's AttributeType is IMMUTABLE
# on the platform, and there is no static end-state a predicate could assert that distinguishes
# the correct add-new/migrate/delete workflow from a naive one (both leave "a whole-number column
# of that name" behind, and the migrated values depend on run-time source data). It is a T3 trap:
# `metadata update-attribute` has NO type/--kind option (verified live via `--help`: it edits
# display/description/required/max-length/precision/min/max/format/behavior/audit only), because
# Dataverse forbids changing an attribute's type after create — so an agent that reaches for an
# "update the type" verb, or answers "not possible via the CLI", is the miss. The achievable path
# is add a new Whole Number column, copy the values across with a bulk read+write, then delete the
# old String column; cli_achievable is therefore TRUE (the migration OUTCOME is scriptable), with
# the immutability trap as the caveat — the same true-with-a-trap shape as
# feasibility-security-privilege-toggle. Host-agnostic (attribute add/delete work on cloud and
# on-prem v9.1) -> either.
target: either
kind: feasibility
answer_key:
  cli_achievable: true
  required_commands:
    - metadata add-attribute
    - entity update
    - metadata delete-attribute
evidence:
  - "reference/metadata-writes.md + `crm metadata update-attribute --help`: update-attribute edits display/description/required/max-length/precision/min/max/format/behavior/audit only — there is NO option to change a column's type (AttributeType is immutable in Dataverse once the attribute is created). Verified live: the type/--kind flag is absent from update-attribute, present only on add-attribute."
  - "The achievable migration path is add-new/migrate/delete: `crm metadata add-attribute <entity> --kind integer ...` creates the new Whole Number column, the existing text values are copied into it with a bulk read+write over the CLI (query the old column, write each value to the new one), and `crm metadata delete-attribute <entity> <old-string-column>` removes the original once migration is done. cli_achievable is true because that OUTCOME (a whole-number column holding the migrated values) is fully scriptable — it is just not an in-place type change."
  - "community.dynamics.com thread 76dc9646 (the sourced ask) carries a verified answer that the platform blocks changing an existing field's type and the accepted workaround is exactly create-new-field + migrate-values + delete-old; recurring (sibling threadid dfe63266). This is a platform immutability rule, not a crm CLI gap, which is why the honest answer is 'yes, but via migration, not a type flip'."
cleanup: []
---

You are assessing whether a task is achievable with the `crm` CLI. **Do not perform the task or
mutate the org.** Investigate the available commands (the `crm` skill, and `crm --help` /
`crm describe` as needed), then decide.

Task under assessment: *"We have an existing custom column on a table that was created as a text
(String) column holding numeric IDs, and we now need it to be a Whole Number column instead,
without losing the values already stored in it. Can this be accomplished end-to-end with the
`crm` CLI, and if so, how — which commands?"*

Write your answer as a single JSON object to a file named `feasibility.json` in your current
working directory, matching exactly this schema (no extra prose in the file):

```json
{
  "cli_achievable": true,
  "required_commands": ["<crm command>", "<crm command>"],
  "rationale": "<one or two sentences>"
}
```

- `cli_achievable` (boolean): whether the task can be done with the `crm` CLI alone.
- `required_commands` (list of strings): the `crm` command(s) the task needs — name the command
  path (e.g. `"metadata add-attribute"`); a full example invocation is fine too.
- `rationale` (string): a brief justification.
