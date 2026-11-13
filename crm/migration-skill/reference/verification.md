# Verification: prove a repair on the target, then let the gate judge

Steps 6 and 7 of the workflow. `crm inventory verify` is the only route to
"complete"; this page is how to feed it honest results. Run order: confirm the
target, collect evidence, pick tests, record outcomes, run the gate, write the
checkpoint.

## Confirm the target before any mutation

Run `crm --profile <name> connection whoami` and compare the profile name, org
and user with the environment the work names as the designated dev or test org.
Done when they match. A different org, a production-looking host or an
unexpected identity ends the step: stop, name what answered, and ask the user.
Nothing is deployed, imported or written until this passes, and a profile that
serves as a source stays read-only (`reference/environments.md`).

## Deploy and re-extract: the loop's second half

Starts once the loop in `reference/remediation.md` has a green build. Each step
has its gate; stop at the first that fails.

1. Confirm the target (above). Gate: whoami matches the named dev or test org.
2. Deploy by the route that fits the component. A web resource: `crm webresource
   push`. A plug-in assembly: `crm plugin register-assembly`. Form, ribbon or
   sitemap XML: repack with `crm solution pack`, import the zip with `crm
   solution import`, then `crm solution publish-all` (an import publishes
   workflows only, not customizations). `solution pack` shells out to `pac`
   (the Power Platform CLI), so install it first. `solution import` overwrites
   the target's unmanaged customizations by default: settle with the
   environment's owner whether to keep them (the command's `--help` has the
   switch) before importing. Gate:
   the command exits 0 and, for XML, the publish ran.
3. Re-extract the target scoped to that solution, then `crm inventory map-source`
   it against the repository. Gate: each deployed component is `matched`.
4. Test and record results as below, then `crm inventory verify`. Gate: the verdict.

Out of bounds for the whole loop, from the first patch to the last deploy: a
production org, a `crm2011` profile and hosted CI. A package is complete only
when `verify` says so.

## Collect errors and traces with existing commands

The `crm` skill owns the how: its troubleshooting page covers errors, retries,
connection diagnostics, `session audit` and `audit history`; its automation page
covers plug-in steps, workflows and plug-in trace logs only. Pull the evidence a
failing or blocked requirement needs from those commands, save each artifact
under the results file's folder, and cite it in the result's `evidence` or
`errors`. Done when each failing or blocked requirement has its error or trace saved.

## Choose the test kind each requirement needs

Read each package's `verification` entries from `crm inventory packages`
(`index.json` lists the packages, `<package id>.json` holds each). A requirement states its kind; the test that proves it:

| Requirement kind | Test that proves it |
|---|---|
| `form_script` | browser test: open the form, trigger each handler, assert the visible result |
| `ribbon_command`, `sitemap_entry` | browser test: the control appears, runs and reaches the right target |
| `plugin_step` | integration test: perform the message, assert the step's effect and its images |
| `process_trigger` | integration test: fire the trigger, assert the process outcome |

Add a **permission test** (run as a user holding only the intended role) where
the behavior depends on security roles, and a **business scenario** for each
`expected_behavior` a package carries. Logic in a shared library also gets a unit
test in the application repository.

A `process_trigger` records no trigger (`trigger` is `unknown`), so pick the check from
the process. A dialog runs only from the web application: exercise its replacement (a
business process flow or a canvas app), or the redesigned workflow. A dialog package has
no waiver: one for a dialog being retired stays open, and the decision is recorded in the
package's `open_decisions`. Report each `blocked` and `not_tested` result with its reason.

A requirement with an `unknown` field or no scenario goes back to the user for
the expected behavior; a test written without one asserts a guess.

The CLI runs no browser or .NET test runner. Run those with the repository's own
tooling, then record what they produced. Done when every requirement has a
named test kind or an open question.

## Record each outcome with its evidence

Write one result per requirement into the verification results file; the `crm`
skill's inventory page gives its shape. Set `packages_sha256` from
`data.packages_sha256` of the packages run being judged.

- `pass`: the check ran and its assertion held. Attach evidence files, each by
  relative path and SHA-256; a pass without evidence is a `not_tested`.
- `fail`: the check ran and its assertion did not hold. Put the observed result
  in `actual` and the traces in `errors`.
- `blocked`: the check could not run (environment down, missing data, missing
  access, no runner). Say what blocked it in `errors`. A blocked check is never
  a `pass`.
- `not_tested`: nobody ran it.

A requirement takes the worst of its results, so a later `pass` never clears an
earlier `fail`: fix the component, then replace the failed result.

Keep every evidence file inside the results file's folder and cite it by a path
relative to that folder; `..`, an absolute path, `\` and `:` are refused. Copy a
step log kept elsewhere into the folder first, then cite its new path and
SHA-256.

Record `deployed` (component and hash) for the artifact the test exercised, and
`environment` with the profile and org from the `whoami` check. A result
produced with no org (a unit suite on a local checkout) records
`{"profile": "local", "org": "none"}`; a recorded `environment` needs both keys
as strings, so `{"runner": "pytest"}` is refused.

**A failing assertion is a finding.** Fix the component and re-run the same
assertion; changing the expected value, loosening a match or skipping the check
to turn it green hides the defect. Change an assertion only when the user
confirms the expected behavior was wrong, and record that as an `open_decisions`
entry.

**A negative control shows a check can catch the defect it guards.** Make the
regression in a throwaway export or copy that never reaches the branch or the
target. Run the check and record it with `"negative_control": true`, then revert
the regression and run the same check green, recorded as an ordinary result
(no `negative_control`) with its own evidence: the gate derives a requirement's
status from its ordinary results alone, so a control row cannot stand in for it.
The gate maps the control's outcome:

- `fail`: `held`. The check caught the regression.
- `pass`: `escaped`. The check missed it. Record it as it ran: the gate marks a
  requirement whose own checks pass `not_tested` with reason `control_escaped`,
  and the report names the control as a test gap.
- `blocked` (or `not_tested`): `not_run`.

When the only check that covers a pattern runs on the target (a browser case
needing the regression deployed), keep the target untouched: record the control
`blocked` with the reason in `errors`.

A worked control row, a unit suite run offline against a throwaway copy:

```json
{
  "package": "pkg-example",
  "requirement": "req-example",
  "outcome": "fail",
  "negative_control": true,
  "evidence": [{"path": "evidence/control-unit.txt", "sha256": "9e71be1f6eab21541fd197ed90f19d113c67cb05074533d7c38c668e900adb21"}],
  "actual": "unit suite failed on the seeded regression: 1 failed, 41 passed",
  "environment": {"profile": "local", "org": "none"}
}
```

Done when every requirement of every package has a result, or is named as
unrecorded in the final report.

## Run the gate and report its verdicts

```
crm --json inventory verify PACKAGES --results RESULTS --out verify.json
```

The gate proves each requirement has a recorded outcome and that each evidence
file exists inside the results folder with the recorded hash. It does not read
the evidence: a `pass` citing an unrelated file still makes a package
`complete`. Confirm each evidence file shows the assertion its `pass` claims before you report `complete`.

Every verdict exits 0, so read the `--out` report: it alone carries each
requirement's `reason`. Report each package as the gate
gives it:

- `complete`: say so, and only for packages the gate marks `complete`.
- `failed`, `blocked`, `not_tested`: give the verdict, the `reason` of each
  requirement that holds it, and the open work that clears it. Give the package's
  `requirement_counts` beside the verdict, so a partly repaired package shows
  what was proved.
- Also report every `escaped` negative control, every `unknown_results` entry
  and every package's `open_decisions`.

Wording for a blocked package: "Package `pkg-...` is `blocked`. Requirement
`req-...` could not run: <errors>. It stays open until <the action that unblocks
it>; no completion is claimed." A summary line counting `complete` packages
carries the count of the others beside it. Done when every package appears in
the report with the gate's verdict and, if not `complete`, its reason.

A results file recorded against another packages output is refused (exit 1):
re-record against the current output, or re-run `crm inventory packages` if the
inventory changed.

## Checkpoints: resume from the latest stage

Each stage's output is its checkpoint, in this order: inventory folder, source
map, findings (`assess` file), packages folder, results file, verify report.
To resume, find the latest stage present and continue from it. Re-run an earlier
stage only when its input changed: a re-extracted inventory invalidates the
source map, findings, packages and results that follow it (the packages hash
changes, so the old results file is refused).

The checkpoint note beside the verify report lists each package that is not
`complete`, its verdict and its next action. Done when a new session could pick
up from the note and the stage files alone.
