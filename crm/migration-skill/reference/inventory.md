# Inventory: extract, read coverage, diff stages

Step 2 of the workflow. The inventory is the baseline every later step reads, so
its completeness is the first thing to settle. The `crm` skill's inventory page
owns the commands' flags and output shapes.

## Extract from any org

`crm inventory extract` reads one org into a folder: a `crm2011` profile over
SOAP, a v9 on-premises or Dataverse online profile over the Web API. It is
read-only, so a read-only profile runs it. Run it once per org and stage, into a
folder named for that stage, and record the org's version beside the folder
(`reference/environments.md`).

- **The whole org is the default.** Narrowing to a solution yields a partial
  scope; write that scope into the stage notes, because a diff across two
  scopes compares different things.
- **An interrupted run resumes**: re-run the same command into the same folder.
- **`crm org brief` is orientation only.** It samples; the extracted manifest is
  the baseline.

## Read coverage before the records

The command's summary omits the two sections that decide whether the inventory
can be trusted. Open the manifest and read:

- **`coverage`**, one entry per kind: `complete`, `partial`, `failed` or
  `unsupported`, with its `errors`. Exit code 1 means at least one kind failed;
  the run still wrote the rest.
- **`gaps`**: what the extraction knew it could not capture, each with a
  `reason` (an ISV page on the web server, a plug-in binary registered on disk).

Every kind that is not `complete`, and every gap, becomes an **unknown** in the
report with its stated reason. A `crm2011` inventory carries no dependency
records (`unsupported`), so later dependency edges are unknown by construction.
Retry a `failed` kind once; if it fails again, report it rather than looping.

Done when each kind reads `complete`, or is listed as an unknown with the
manifest's reason.

## Diff stages

After each upgrade stage, extract again and compare it with the previous stage:
`crm inventory diff <earlier> <later>`. It is offline and matches components by
key, never by GUID. Read each verdict as a next step:

| Verdict | Next step |
|---|---|
| `unchanged`, `identity_only` | Expected; `identity_only` is a GUID that changed with the upgrade. Matching is evidence, not proof of equal behavior. |
| `added` | Note it; the upgrade or a parallel change introduced it. |
| `changed` | Read its entry in the `--out` file and decide whether the change is the upgrade's doing. |
| `removed` | Treat as a lost component: find out why before any further stage runs. |
| `ambiguous` | The key repeats, so the pair cannot be matched: resolve by hand and list it as an unknown. |
| `not_comparable` | A kind's coverage was not `complete` on both sides, or a gap explains what one side lacks: neither a pass nor a difference. Fix the extraction and re-diff, or carry it as an unknown. |

Read the header: `same_platform: false` is normal across the CRM 2011 to
on-premises step, and `same_scope: false` means the counts do not compare like
with like. Pass `--out` to keep the per-key entries; the summary holds counts
only.

Done when every `removed`, `changed`, `ambiguous` and `not_comparable` entry has
an explanation or an open question, and the next stage starts only after.
