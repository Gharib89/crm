# Duplicate-detection rules

Create duplicate-detection rules, add match conditions, publish/unpublish them,
and test a candidate record against the published rules. Group: `dup`.
Flags/choices/operators: `crm dup --help` (and `crm dup add-condition --help`).

```bash
crm --json dup create account --name "Accounts with the same name"
crm --json dup add-condition "Accounts with the same name" --attr name --operator exact
crm --json dup publish "Accounts with the same name" --wait
crm --json dup check account --data '{"name": "Contoso"}'
crm --json dup unpublish "Accounts with the same name"
crm --json dup list                       # or: dup list --entity account
crm --json dup get "Accounts with the same name"
```

## Workflow & gotchas

**Order matters: create → add-condition → publish.** A new rule is *unpublished*
and inert. Publishing a rule with **no conditions** fails (`0x80048414`), so add
at least one condition first. Only **published** rules participate in detection —
`dup check` against an unpublished (or condition-less) rule always returns empty,
even for an obvious duplicate. At most **five** rules can be published per entity.

**`publish` is async; `unpublish` is synchronous.** `publish` submits the
`PublishDuplicateRule` background job (builds match codes); without `--wait` it
returns `status: "submitted"` + a `job_id`, with `--wait` it polls to
`status: "completed"`. `unpublish` (`UnpublishDuplicateRule`) completes
immediately — no job, no `--wait`. (Mechanism note: publish is a *bound* action on
the rule; unpublish is an *unbound* action taking the id in its body — both
handled internally.)

**`--operator-param` pairs only with the character-count operators.** The
`same-first` / `same-last` operators **require** `--operator-param` (the
character count); every other operator **rejects** it. Mismatching the two is a
clean error before any backend call. Multiple conditions on a rule are AND-ed.

**`check` tests a candidate record by value, not id.** Supply the would-be
record's columns via `--data` (inline JSON) or `--data-file`; the record need not
exist. Detection requires the entity to be duplicate-detection-enabled.

**`<rule>` is a name *or* id** everywhere (`add-condition`, `publish`,
`unpublish`, `get`) — a name is resolved by exact match, or pass the
`duplicateruleid` GUID directly.

**`--solution` / `--dry-run` on writes.** `create` and `add-condition` honor
`--solution <unique_name>` and `--dry-run` (echoes the would-be POST,
`meta.dry_run: true`; reads still run for real).

## JSON contract for `check` and `get`

`check` → `data` is `{entity, matching_entity, count, duplicates}` where
`duplicates` is the array of matching existing records (empty when none):

```json
{"entity": "account", "matching_entity": "account", "count": 1,
 "duplicates": [{"accountid": "<guid>", "name": "Contoso"}]}
```

`get` → `data` is the rule record plus a `conditions` array:

```json
{"duplicateruleid": "<guid>", "name": "Accounts with the same name",
 "baseentityname": "account", "matchingentityname": "account",
 "conditions": [{"duplicateruleconditionid": "<guid>", "baseattributename": "name",
   "matchingattributename": "name", "operatorcode": 0, "operatorparam": null}]}
```

`operatorcode` is the raw numeric code (`0` = Exact Match, `1` = Same First
Characters, `2` = Same Last Characters, `3` = Same Date, `4` = Same Date and
Time, `5`/`6` = picklist label/value).
