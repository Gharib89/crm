# Assessment: sort findings into four classes

Step 3 of the workflow. `crm inventory assess <inventory> --target <target>
--out <file>` is offline. Run it once per target the plan passes through (the
on-premises 9.1 stop and the cloud), into separate files.

A finding carries a `usage` when its row has one (`reference/remediation.md`
says how to read it). `--in-use` drops the findings of `unused` rows and
`--exclude-unknown` those of `unknown` rows, so `counts.by_class` then covers
only what is left: say so when you quote it. A finding with no `usage` stays.

## Map findings to the four classes

Read `counts.by_class` in the summary, not the exit code: a blocker exits 0.

| Finding class | Workflow class |
|---|---|
| `blocker` | Confirmed blocker |
| `deprecated` | Deprecated but working |
| `optional` | Optional modernization |
| `unknown` | Unknown |
| `clear` | None yet: judge it from the component's source or runtime evidence |

`clear` is not "compatible": the rules cover few kinds, and a component no rule
covers is `unknown`.

## Quote the evidence

Copy each finding's rule id and `evidence`, and the rule's `fix` and Microsoft
Learn `sources` (the findings file's `rules_hit`, keyed by rule id), into the
report beside its class; a class without them is an assertion.

## Turn every unknown into a question or a manual-review entry

Read each `unknown`'s `reason` and act on it:

- **`no rule`**: write a manual-review entry naming the component and the
  evidence that would settle it (its source, a runtime trace, the
  documentation).
- **`field ... not recorded`**: the inventory holds no value for the field the
  rule needs. Re-extract with the current crm; if the org left the field empty,
  ask the user what it should hold.
- **`extraction gap`** or **`kind coverage`**: the inventory never fully read it
  (`reference/inventory.md`). Close the gap, or carry the unknown forward with
  its reason.
- **`source version`**: extract could not read the org's version. Re-extract.
- **`artifact unreadable`**, **`artifact changed since extraction`** or **`no
  text artifact`**: re-extract; a changed artifact means the folder was edited
  after extraction.

`unattached_gaps` counts extraction gaps that name no component; they stay in
the report as unknowns too.

Classic workflows have their own check, `crm workflow migration-assess`. It reads
the org through a profile and judges only their conversion to cloud flows, which
Dataverse online does not require. Enter its findings as "deprecated but
working" conversion candidates marked "needs redesign"; they become confirmed
blockers only when the plan requires the conversion.

Done when every component carries one of the four classes with its rule or
evidence, or an entry open on a named question, and no `clear` or `unknown` is
left unjudged.
