# Phase steps: the local gate, CI and hand-back

## Phase 5 local gate

*Precondition:* phases 3 and 4 meet their `Done when:`; otherwise go back.

Run `base-fresh` first, because CI tests the merge ref, so a branch that
predates a merge still goes green while every "does this exist?" answer taken
from the worktree was pre-merge; behind, follow its advice, re-run, continue.
Run `carry-check` on the `Carry:` files: one `differs` or `missing` is
`carried file modified`. Then run
the gate at the profile's `Location:` from the worktree, inline, and `run-file
gate record` its JSON; read unparseable output or a missing `gates.secrets`
as `unavailable`. Verdict `fail`: fix loop. Gate `deferred-to-ci`: proceed, the
merge summary naming each deferred gate. `unavailable`: stop `local gate
unavailable`, the PR unopened.

## Phase 8 CI

CI runs from PR-open and overlaps phase 7; `ci-wait <pr>` covers it, reading the
profile's `Legs:`. On `conflict`, its stderr carries the recovery. `no-checks`
is fine only where `No-checks legal:` says so. A red leg named on a
verification's `Also proven by CI:` line is that verification failing: back to
phase 2. Red after the reviewers exited: fix, push, proceed on green. Honour
`Push policy:`: a push spends CI minutes and review quota, so push when the tree
changed.

## Hand-back

`handback` unassigns, drops `ready-for-agent`, adds `ready-for-human` and
comments the reason; one that exits 1 says on its own output what the claim is
left as. In an attended run you stop and ask, and hand back only if the human
says stop, to the human queue: never to the agent queue, which loops forever.
