# Inventory: extract, read coverage, diff stages

Step 2 of the workflow. `crm describe` and `--help` own each command's flags; the
`crm` skill's inventory page owns the output shapes.

## Extract from any org

`crm inventory extract` reads a `crm2011` profile over SOAP and a v9 on-premises
or Dataverse online profile over the Web API with the same command. A read-only
profile runs it. Run it once per org and stage, into a folder named for that
stage, and record the org's version beside the folder
(`reference/environments.md`).

- **A real org takes many minutes; stderr shows where it is.** Stage lines
  (`[crm] extract stage=...`) tell a slow run from a stuck one; `--quiet`
  silences them. Run it in the background with stderr captured.
- **Narrowing to a solution yields a partial scope.** Write the scope into the
  stage notes, because a diff across two scopes compares different things.
- **The default drops stock components.** The manifest's `scope.system` says
  `excluded`; a managed component someone customized in place is missed, so audit
  those with an `--include-system` extract. Extract every stage the same way:
  `diff` refuses a default manifest against a full one.
- **`crm org brief` is orientation only.** It returns counts and key names,
  never full rows; the extracted manifest is the baseline.

## Read coverage before the records

The summary gives each kind's status and a gap count. The reasons live only in
the manifest, so open it and read:

- **`coverage`**, one entry per kind: `complete`, `partial` or `failed`, with its
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
| `changed` | Read its entry in the `--out` file and decide whether the upgrade explains it. |
| `removed` | Treat as a lost component: find out why before any further stage runs. |
| `ambiguous` | The pair cannot be matched: resolve by hand and list it as an unknown. |
| `not_comparable` | Neither a pass nor a difference. Fix the extraction and re-diff, or carry it as an unknown. |

Read the header: `same_platform: false` is normal across the CRM 2011 to
on-premises step, and `same_scope: false` means the counts do not compare like
with like. The per-key entries exist only in the `--out` file.

Done when every `removed`, `changed`, `ambiguous` and `not_comparable` entry has
an explanation or an open question, and the next stage starts only after.
