# How-to: dup

Manage **duplicate-detection rules** headlessly: create a rule for an entity,
add match conditions, publish it (so detection runs), and test a candidate
record against the published rules. See the [CLI reference](../reference/cli.md)
for every flag.

Duplicate detection in Dynamics 365 is driven by **duplicate rules**
(`duplicaterule`). A rule binds a *base* entity to a *matching* entity (usually
the same entity) and carries one or more **conditions** (`duplicaterulecondition`)
— each comparing a base column to a matching column with an operator. A freshly
created rule is **unpublished** and does nothing until you publish it: publishing
builds the match codes in a background (async) job. You can publish at most five
rules per entity type.

## The workflow

1. `dup create` — create the rule (unpublished).
2. `dup add-condition` — add one or more match conditions.
3. `dup publish` — build the match codes (async); the rule becomes active.
4. `dup check` — test a candidate record against the published rules.
5. `dup unpublish` — retire the rule (deletes its match codes; synchronous).

## Create a rule

```bash
crm --json dup create account --name "Accounts with the same name"
```

`ENTITY` is the base entity logical name; `--name` is required. By default the
matching entity is the same as the base entity — pass `--matching-entity` to
compare across entity types (e.g. leads against contacts). Supports
`--description`, `--solution <unique_name>` (sets `MSCRM.SolutionUniqueName`), and
`--dry-run`. Returns the new `duplicateruleid`. The rule is created **unpublished**.

## Add a condition

```bash
crm --json dup add-condition "Accounts with the same name" --attr name --operator exact
```

`RULE` is a rule **name or id**. `--attr` is the base column; `--matching-attr`
defaults to the same column. `--operator` is one of:

| Operator | Meaning |
|---|---|
| `exact` | Exact match |
| `same-first` | Same first *N* characters (requires `--operator-param N`) |
| `same-last` | Same last *N* characters (requires `--operator-param N`) |
| `same-date` | Same date |
| `same-datetime` | Same date and time |
| `exact-picklist-label` | Exact match on the choice label |
| `exact-picklist-value` | Exact match on the choice value |

`same-first` / `same-last` **require** `--operator-param N` (the character count,
`>= 1`); the other operators reject it. Pass `--ignore-blank-values` to treat
blank values as non-duplicates. Supports `--solution` and `--dry-run`. Conditions
on a rule are combined with logical **AND**.

## Publish and unpublish

```bash
crm --json dup publish "Accounts with the same name" --wait
crm --json dup unpublish "Accounts with the same name"
```

`publish` submits the asynchronous `PublishDuplicateRule` job (the rule must
already carry at least one condition). Without `--wait` the command returns once
the job is submitted (`status: "submitted"`, with a `job_id`); with `--wait` it
polls the async operation to completion (`status: "completed"`). `--timeout`
caps the wait.

`unpublish` calls `UnpublishDuplicateRule`, which is **synchronous** — it deletes
the match codes immediately, with no async job to wait on.

## Check a candidate record

```bash
crm --json dup check account --data '{"name": "Contoso"}'
crm --json dup check account --data-file candidate.json
```

`check` calls the `RetrieveDuplicates` function with the candidate record's
column values as the `BusinessEntity` — the record need not exist yet, so this is
the "would creating this be a duplicate?" check. Supply the values inline with
`--data` or from a JSON file with `--data-file`. `--matching-entity` defaults to
`ENTITY`; `--top` caps the number of matches returned (default 50). Returns:

```json
{
  "entity": "account",
  "matching_entity": "account",
  "count": 1,
  "duplicates": [ { "accountid": "...", "name": "Contoso" } ]
}
```

> **Detection only fires for published rules.** `check` (and any other detection)
> matches only against **published** rules on a duplicate-detection-enabled
> entity. With no published rule the result is always empty, even for an obvious
> duplicate.

## List and inspect rules

```bash
crm --json dup list
crm --json dup list --entity account
crm --json dup get "Accounts with the same name"
```

`list` returns every rule (id, name, base/matching entity, status); `--entity`
filters by base entity. `get` takes a rule name or id and returns the rule fields
plus a `conditions` list of the match conditions it carries.
