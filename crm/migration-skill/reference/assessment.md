# Assessment: sort findings into four classes

Step 3 of the workflow. `crm inventory assess <inventory> --target <target>
--out <file>` is offline. Run it once per target the plan passes through (the
on-premises 9.1 stop and the cloud), into separate files. The `crm` skill's
assess page (`inventory-assess.md`) owns what each rule family flags, its
evidence and its `unknown` reasons; this page says how each answer enters the
migration report.

When `--in-use` or `--exclude-unknown` was passed, quote `counts.by_class` with
the flag named: it covers only the rows left.

## Map findings to the four classes

| Finding class | Workflow class |
|---|---|
| `blocker` | Confirmed blocker |
| `deprecated` | Deprecated but working |
| `optional` | Optional modernization |
| `unknown` | Unknown |
| `clear` | None yet: judge it from the component's source or runtime evidence |

`clear` is not "compatible": the rules cover few kinds, and a component no rule
covers is `unknown`.

Rule families that need a word beside their class:

- **A plug-in step on `systemuser`** (`step-on-systemuser`): "Confirmed blocker".
  Judge every other step from the plug-in's source.
- **A classic dialog** (`process-dialog`): "Deprecated but working"; name the
  replacement, a business process flow or a canvas app.
- **A classic workflow's wait condition or custom activity**: "Optional
  modernization". A cloud flow has no equivalent for either, and Dataverse online
  still runs classic workflows.
- **A custom SQL-based report** (`report-sql-datasource`, judged by record origin, not the
  `custom` flag): "Confirmed blocker",
  though the report is lost, not the migration stopped: the migration removes
  unconverted custom SQL-based reports. Microsoft documents no converter, and the
  FetchXML rewrite must still pass the Dataverse RDL sandbox. Settle an `unknown`
  provider on the live org or in SSRS.
- **A plug-in project or source blocker** has a patch only for the project file
  (`reference/remediation.md`); refactor the C# by hand and prove it in the
  target (`reference/verification.md`). The same holds for a source host match
  and a Solution Checker finding: `convert` rewrites neither. Enter each
  `checker_unmatched` result as an open question.

## Quote the evidence

Copy each finding's rule id and `evidence`, and the rule's `fix` and Microsoft
Learn `sources` (the findings file's `rules_hit`, keyed by rule id), into the
report beside its class; a class without them is an assertion.

## Turn every unknown into a question or a manual-review entry

Read each `unknown`'s `reason` and act on it:

- **`no rule`**: write a manual-review entry naming the component and the
  evidence that would settle it (its source, a runtime trace, the
  documentation).
- **`field ... not recorded`**: re-extract with the current crm; if the org left
  the field empty, ask the user what it should hold.
- **`extraction gap`** or **`kind coverage`**: close the gap
  (`reference/inventory.md`), or carry the unknown forward with its reason.
- **`source version`** or an **artifact** reason: re-extract.
- **A `library <name>` reason**: a library not in the inventory is often a
  platform or managed script, so name it in the entry; one that names more than
  one web resource, or no script web resource, needs the registration read by
  hand; one with an artifact problem needs a re-extract.
- **A `project <path>` or `source <path>` reason**: fix the repository or the
  source map, then re-run; name in the entry a valid C# file the parser rejects.

`unattached_gaps` stay in the report as unknowns too.

Classic workflows also have `crm workflow migration-assess`. It is live (it reads
the org through a profile), needs a Web API profile (a `crm2011` profile is refused; on the 2011 source use the
inventory's two workflow rules), covers category 0 only, also judges real-time mode, and
writes no findings file, and no package holds its verdicts. The inventory's two
workflow rules give the same wait and custom-activity verdicts; only `migration-assess`
sees mode. Dataverse online
does not require the conversion, so enter its findings as "Optional modernization"
marked "needs redesign"; they become confirmed blockers only when the plan requires
the conversion.

## Roll the findings up as a readiness checklist

`crm inventory readiness <findings>` rolls an `--target online` file up into one row
per gate (the `crm` skill's assess page explains each row). It performs no upgrade hop.
Microsoft Learn documents the hops 2011 (UR14 or later) to 2013 and 8.2 to 9.0 only;
the hops between are undocumented there, so mark each one an unknown. Enter each
`unknown` row as an open question, and ask the owner of each manual row
(database-side or tenant-side) and record the answer, never a `pass`.

Done when every component carries one of the four classes with its rule or
evidence, or an entry open on a named question, and no `clear` or `unknown` is
left unjudged.
