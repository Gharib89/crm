# Inventory: extract, read coverage, diff stages

Step 2 of the workflow. `crm describe` and `--help` own each command's flags; the
`crm` skill's inventory page owns what `extract` and `diff` record and how their
output reads. This page says when to run them and what each answer means for the
migration.

## Extract from any org

`crm inventory extract` reads a `crm2011` profile over SOAP and a v9 on-premises
or Dataverse online profile over the Web API with the same command. A read-only
profile runs it. Run it once per org and stage, into a folder named for that
stage, and record the org's version beside the folder
(`reference/environments.md`).

- **Run it in the background with stderr captured.** A real org takes many
  minutes, and the stage lines tell a slow run from a stuck one.
- **Extract every stage with the same scope, and write the scope into the stage
  notes**, because a diff across two scopes compares different things. The
  exception is a migrated solution imported managed: extract it with
  `--solution` and diff it against the default-scope source (below). The default
  scope leaves stock components out, so audit a managed component customized in
  place, or a step registered on a managed assembly, with a full-scope extract.
- **Treat a Dataverse online ribbon with custom rules or labels as unread**, since
  a whole-org online run records a partial `ribbon_diff`, or extract it with
  `--solution`.
- **Extract with usage where unused components matter.** Count `state: unknown`
  as unconfirmed, and read the `usage` gaps and summary `errors` before trusting
  a count of unused rows.
- **`crm org brief` is orientation only.** It returns counts and key names,
  never full rows; the extracted manifest is the baseline.
- **`crm report get <id>` shows one report's data provider** (`fetch`, `sql` or
  `unknown`), without an extract.
- **Read each extracted report's data provider as a migration answer.** Online
  runs only `fetch`, so `sql` is a report to rebuild and `unknown` is an unknown,
  not a pass. Report the shared data sources of SSRS reports (a gap naming no
  report) as manual follow-up: they are defined in SSRS, which the endpoint does
  not reach.

## Read coverage before the records

The summary gives each kind's status and a gap count. The reasons live only in
the manifest, so open it and read:

- **`coverage`**, one entry per kind: `complete`, `partial`, `unsupported` or `failed`, with its
  `errors`. Exit code 1 means at least one kind failed; the run still wrote the
  rest.
- **`gaps`**: what the extraction knew it could not capture, each with a
  `reason` (an ISV page on the web server, a plug-in binary registered on disk).

Every kind that is not `complete`, and every gap, becomes an **unknown** in the
report with its stated reason. Retry a `failed` kind once; if it fails again,
report it rather than looping.

Done when each kind reads `complete`, or is listed as an unknown with the
manifest's reason.

## Diff stages

After each upgrade stage, extract again and run `crm inventory diff <earlier>
<later>` (offline). Read each verdict as a next step:

| Verdict | Next step |
|---|---|
| `unchanged`, `identity_only` | Expected; a GUID changing with the upgrade is normal. Matching is evidence, not proof of equal behavior. |
| `added` | Note it. |
| `changed` | Read its entry in the `--out` file and decide whether the upgrade explains it. With the stages between unobserved (cloud-first route), a change cannot be tied to a hop: ask the team that ran the hops, or record it as an unknown. Before reporting one, rule out the older-extract effects the `crm` skill's inventory page lists (every ribbon `changed`, column facets skipped): re-extract that side. A `form` whose `fields.active` changed is a form to activate or retire: the upgrade added new forms beside the old ones and left activation to the customer. Against a CRM 2011 source no form reads `changed` on `active`: use the arrival checklist's read (`reference/arrival.md`). |
| `removed` | Treat as a lost component: find out why before any further stage runs. With the stages between unobserved (cloud-first route), no hop can be named: it is a question for the team that ran the hops (`reference/handover.md`), or an unknown. |
| `ambiguous` | The pair cannot be matched: resolve by hand and list it as an unknown. |
| `not_comparable` | Neither a pass nor a difference. Fix the extraction and re-diff, or carry it as an unknown. |

Read the header: `same_platform: false` is normal across the CRM 2011 to
on-premises step, and `same_scope: false` means the counts do not compare like
with like. The per-key entries exist only in the `--out` file.

A `--solution` target against a default-scope source is a restricted diff, read as
the `crm` skill's inventory page says: report the left-out source keys as out of
scope, and a managed import reading `changed` on `origin` or `managed` alone as
expected.

Done when every `removed`, `changed`, `ambiguous` and `not_comparable` entry has
an explanation or an open question. On the staged path the next stage starts only
after; on the cloud-first route there is no next stage to hold.

With a 9.1 extract in hand, run two diffs (2011 to 9.1, then 9.1 to cloud) in
place of the single one, and tie each entry to the hop it first appears in.
