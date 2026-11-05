# Assessment: sort findings into four classes

Step 3 of the workflow. `crm inventory assess <inventory> --target <target>
--out <file>` is offline and applies the rules shipped with crm for one target.
Run it once per target the plan passes through (the on-premises 9.1 stop and the
cloud), into separate files.

## Map findings to the four classes

Every component gets at least one finding, keyed by component key. Read
`counts.by_class` in the summary rather than the exit code: a blocker exits 0.

| Finding class | Workflow class |
|---|---|
| `blocker` | Confirmed blocker |
| `deprecated` | Deprecated but working |
| `optional` | Optional modernization |
| `unknown` | Unknown |
| `clear` | None yet: the rules that apply found nothing |

`clear` is not "compatible". The rules cover few kinds, so a `clear` component
still needs a judgment from its source or runtime evidence before it takes a
class.

## Quote the evidence

A rule hit carries what it matched: for a script, the artifact `path`, its
`sha256` and the matched lines; for a registration, the recorded field and
value. The findings file's `rules_hit` section holds each rule's fix and its
Microsoft Learn sources. Copy the rule, the evidence and the link into the
report beside the class; a class without them is an assertion.

## Turn every unknown into a question or a manual-review entry

Read each `unknown`'s `reason` and act on it:

- **`no rule`**: no shipped rule judges this kind. Write a manual-review entry
  naming the component and the evidence that would settle it (its source, a
  runtime trace, the documentation). Never record it as compatible.
- **`field ... not recorded`**: the inventory came from an older crm. Re-extract,
  then re-assess.
- **`extraction gap`** or **`kind coverage`**: the inventory never fully read it
  (`reference/inventory.md`). Close the gap, or carry the unknown forward with
  its reason.
- **`source version`**: extract could not read the org's version. Re-extract.

`unattached_gaps` counts extraction gaps that name no component; they stay in
the report as unknowns too.

Classic workflows have their own check, `crm workflow migration-assess`; run it
for them and merge its blockers into the same four classes.

Done when every component carries one of the four classes with its rule or
evidence, or an entry that is open on a named question, and no `clear` or
`unknown` is left unjudged.
