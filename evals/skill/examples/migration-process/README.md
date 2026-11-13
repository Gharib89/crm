# Worked example: one process through the crm-migration path

A committed run of the offline `crm inventory` stages over one synthetic process, a
project form with a shared script, a ribbon button and a plug-in step. It ships outside the
wheel, beside the evals. Everything is synthetic: placeholder names, no real ids, hosts or
clients. The live pilot is a separate piece of work (#1036).

## Inputs

- `../../fixtures/migration-process/`: the inventory (`inventory/`, a CRM 2011 extraction
  that reads every kind but `dependency`), the application source (`repo/`) and the named
  business scenario (`scenarios.json`).
- `../../fixtures/migration-results/results.json` and `evidence/`: testers' results
  recorded against `packages/` below: one pass, one fail, one blocked.

## Outputs, in stage order

| File | Made by |
|---|---|
| `source-map.json` | `crm inventory map-source inventory --repo repo --out source-map.json` |
| `findings.json` | `crm inventory assess inventory --target online --out findings.json` |
| `packages/` | `crm inventory packages inventory --source-map source-map.json --scenarios scenarios.json --findings findings.json --out packages` |
| `verification-report.json` | `crm inventory verify packages --results results.json --out verification-report.json` |

Run each command from the fixture folder, with the paths above made relative to it. The
outputs are deterministic: a test (`test_migration_evals.py`) re-runs all four and compares
them byte for byte, except `findings.json`'s `generated_at`.

## When to regenerate

Regenerate all four outputs in the same change that edits `crm/rules/compatibility-rules.json`
(its `version` or any rule), since `findings.json` records the ruleset's `version` and
`sha256`. If `packages/` changes, also copy it over `../../fixtures/migration-results/packages/`
and set `packages_sha256` in `../../fixtures/migration-results/results.json` to the new
`packages/` digest: `crm inventory verify` refuses results recorded against other bytes.
`evals/` is outside `testpaths`, so CI does not run the byte-for-byte test: run
`pytest evals/skill/test_migration_evals.py` after such a change.

## What it says

- **Assessment:** `legacy.js` is a `blocker` (`web-resource-crmform`), `project.js` is
  `deprecated` (`web-resource-xrm-page`), the plug-in assembly is `unknown` (an extraction
  gap, unrecorded fields, and its project and source rules not run without `--source-map`),
  the form is `deprecated` too (`form-handler-no-execution-context`: its `onload` handler
  does not pass the execution context to `project.js`), and each kind no rule covers
  (`entity`, `plugin_type`, `ribbon`, `step_image`) is one `uncovered_kinds` entry with its
  record count, never `clear`.
- **Repair backlog:** two packages. The form, ribbon and four scripts form one package
  (`pkg-123c7f4cd189`) carrying the blocker and the deprecated finding; the plug-in
  assembly, type, step and image form the other.
- **Verification:** the form script passed, the ribbon button failed, the plug-in step was
  blocked. Both packages are therefore not `complete`: `failed` and `blocked`.

`fixtures/migration-results/packages/` is a byte copy of `packages/` here, so a trial can be
handed the backlog without the answer; a test keeps the two equal.
