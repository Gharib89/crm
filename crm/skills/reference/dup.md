# Duplicate-detection rules

Create duplicate-detection rules, add match conditions, publish/unpublish them,
test a candidate record against the published rules, and sweep a table for
existing duplicates. Group: `dup`.
Flags/choices/operators: `crm dup --help` (and `crm dup add-condition --help`).

```bash
crm --json dup create account --name "Accounts with the same name" --solution ContosoCore
crm --json dup add-condition "Accounts with the same name" --attr name --operator exact --solution ContosoCore
crm --json dup publish "Accounts with the same name" --wait
crm --json dup check account --data '{"name": "Contoso"}'      # one candidate record
crm --json dup bulk-detect account --wait                      # sweep existing rows
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

**`bulk-detect` sweeps *existing* rows; `check` tests *one candidate*.**
`bulk-detect ENTITY` submits the async `BulkDetectDuplicates` job over a whole
table (or the `--fetchxml`/`--fetchxml-file` scope, whose `<entity>` must be
`ENTITY`; converted to a `QueryExpression` server-side like `data delete`). Like
`publish` it is async: without `--wait` → `status: "submitted"` + `job_id`, with
`--wait` it polls to `completed` and lists the flagged records. Detection **only**
— nothing is merged/deleted; results are logged as `duplicaterecord` rows (server
caps a job at **5,000**). It writes *data*, not customizations, so it takes **no
`--solution`**. Needs the entity duplicate-detection-enabled with published rules.

**`<rule>` is a name *or* id** everywhere (`add-condition`, `publish`,
`unpublish`, `get`) — a name is resolved by exact match, or pass the
`duplicateruleid` GUID directly.

**Solution-scoped:** `create` and `add-condition` require `--solution` (SKILL.md).
`--dry-run` echoes the would-be POST; `--solution` is validated before the reads.

## JSON contract for `check`, `bulk-detect`, and `get`

`check` → `data` is `{entity, matching_entity, count, duplicates}` where
`duplicates` is the array of matching existing records (empty when none):

```json
{"entity": "account", "matching_entity": "account", "count": 1,
 "duplicates": [{"accountid": "<guid>", "name": "Contoso"}]}
```

`bulk-detect` → `data` is `{job_id, job_name, entity, status}`; submitted →
`status: "submitted"` (no results yet); with `--wait` → `status: "completed"`
plus `count` + `duplicates` (the logged `duplicaterecord` rows). `_baserecordid_value`
is the flagged record; `duplicateid` is only the log row's own id. **The async job
does not populate a matched-counterpart ref** (`_duplicaterecordid_value` stays
empty), so the result is the set of records flagged under the rules, not pairs:

```json
{"job_id": "<guid>", "job_name": "crm dup bulk-detect account", "entity": "account",
 "status": "completed", "count": 2,
 "duplicates": [{"_baserecordid_value": "<guid>", "duplicateid": "<log-row-guid>"}]}
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
