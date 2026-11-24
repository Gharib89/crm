# Phase 9: the merge gate's branches

Read this file only on the branch you hit at the merge gate: a `merge`
refusal, a human asking for changes, a human saying "file defects", an unmet
criterion, or a `ship profile:` hold. The gate itself is
[merge-gate.md](merge-gate.md).

## Contents

- [Merge refusals](#merge-refusals)
- [Changes asked at the gate](#changes-asked-at-the-gate)
- [Filing a Ship defect](#filing-a-ship-defect)
- [Unmet criteria](#unmet-criteria)
- [A profile change](#a-profile-change)

## Merge refusals

Each refusal merges nothing and comes back to the gate:

- `head-moved`: the earlier approval covered the old head only. Re-run
  `read-pr`, read the new commits, cite or re-run the gate for the new
  `head_sha` per [merge-gate.md](merge-gate.md#attended-post-then-wait)'s
  **The gate's verdict is cited**, wait for `ci-wait` on it, save
  its settled answer to `ci-wait.json`, re-run `run-file gate summary <pr>`,
  rewrite the summary from that answer and ask again, or decide on it when
  opted in.
- `no-gate-record`: no admit record covers that head. Admit it, merge again.
- `checks-unsettled: <check>: <state>`: the merge call's own read of the head
  holds a `pending`, `failure` or `missing` check within `Legs:` and
  `No-checks legal:`. Re-run `ci-wait <pr> --sha <head>`, merge again; a red
  check is the fix loop's, not a waiver's.
- `cloud-sandbox`: `CLAUDE_CODE_REMOTE=true`, no `Sandbox merge: yes` in the
  profile. Post the summary and stop: a human merges.
- `base-retargeted`: a clean admission on a PR whose base is no longer the
  default branch, which the clean gate compared the ship profile against. Post
  the summary with the reply line and wait for "merge".
- `stale-base`: merge the base in, re-run the local gate, save the new head's
  settled `ci-wait` answer and come back to the merge gate for `gate summary`. A
  merge with no conflict to resolve leaves the reviewer stops and Verification
  passes covering the new head; one that resolved a conflict breaks that
  coverage.

## Changes asked at the gate

**If the human says no or wants changes**, treat the note as the next round of
work: apply it on the same branch, re-run the local gate, save the new head's
settled `ci-wait` answer, come back to the merge gate and re-run
`gate summary`. Do not re-open the whole pipeline. Under
`Merge: on-clean-gate` the change breaks coverage, so `gate clean` holds,
naming each piece of evidence
with both heads: every reviewer stop, until an on-push reviewer's own round on
the new head records a new stop
([review-loop.md](review-loop.md#the-round)), and every Verification pass whose
`Paths:` the change touched, or that has none or one matching no file, until
its re-run per
[verify.md](verify.md)'s **Head** records it again. Request no new review at
the gate; while a stop holds, the run waits for "merge".

## Filing a Ship defect

A Ship defect draft reaches the source repo on the human's word alone, and
"merge" is not that word: it approves the PR, not publishing the run's context
to a public repo. On "file defects", or a word naming one draft, run `file-issue
--repo Gharib89/skills --title "<title>" --body-file <draft> --label
needs-triage` per Ship defect draft and put its answer under `Ship defects`: the
number filed, the candidates it answered with instead, each read the way phase 2
reads one, or, on exit 1 with a `command`, that command verbatim for the human
to run where the write succeeds. Before or after the merge, either order holds.

## Unmet criteria

An **unmet criterion** is any issue criterion the run did not meet: one
asking for a host action no mechanic performs, which stays verbatim in the issue
([spec-gaps.md](spec-gaps.md#a-criterion-no-mechanic-can-perform)), or
one the run could not prove. Each carries `Criterion <n>: not met: <reason>` in
the Run file ([implement.md](implement.md#phase-1-criteria-the-run-owes)), or
is owed that line and has none, and the `Unmet criteria` item lists it verbatim
with its reason, which is the action a human must take where it is a host
action; the gap is also in the deviations log. The item is the human's to
clear: **merging needs an explicit waiver of each unmet criterion, beside the
merge word**: "merge" alone, or a waiver naming fewer than all of them, is asked
back. `gate clean` reads the same lines, holding on each criterion not met or
with no line, so `Merge: on-clean-gate` posts the summary with the reply line
and waits. A run whose Run file has no `## Criteria` section (a free-text run,
an `init` given no `--criteria`, or criteria text yielding none) gives
`gate clean` only its `Criterion <n>:` lines, plus one hold while its
numbered issue states criteria. With an unmet criterion
listed, wait the same way, whatever it answers.

## A profile change

A PR that changes the ship profile always holds the clean gate, since it
changes what the gate requires; `run-file --help` lists each `ship profile:`
hold. `Merge: on-clean-gate` then posts the summary with the reply line,
names the profile diff (`git diff <base>...<head> -- docs/agents/ship.md`)
for the human to read, and waits for "merge".
