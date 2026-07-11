# PR merge gate

Inbound PRs arrive from several shipping streams — the cloud-ship routine, codex
runs, and teammates' agent sessions — each stopping at its own merge gate with
tests green and review-bot rounds (Copilot and CodeRabbit) addressed. The **`merge-gate` skill**
(`.claude/skills/merge-gate/`, source of truth) is the maintainer's independent
second gate: run locally (the only environment with both live-org profiles), it
re-verifies the PR instead of trusting the author agent's claims, and leaves it
either merge-confident or explicitly failed. The maintainer merges; the gate
never does.

## What a gate run does

1. **Reviews** the diff — the `code-review` skill's Standards + Spec axes plus a
   seven-point repo drift checklist (docs-sync, e2e coverage gate, test
   classification, bump discipline, emit contract, genericity, scope discipline).
2. **Integration-tests** D365-touching changes live: targeted e2e for the touched
   command groups, on every target the commands support, org confirmed via
   `crm connection whoami`.
3. **Fixes in place** — scoped gaps (CI red, checklist failures, valid findings)
   are committed straight onto the PR branch; design-level problems are escalated
   instead of rewritten.
4. **Iterates the review bots** when it pushed fixes — Copilot re-requested,
   CodeRabbit re-reviewing the push automatically. Gate rounds carry their own
   budget of 3 and are exempt from the shipping run's 3-round ceiling — that
   ceiling bounds the author agent's pipeline, not the gate.
5. **Posts a verdict** comment (checklist with evidence, fixes pushed, review
   dispositions) and sets `gate-passed` or `gate-failed`.
6. **Reports follow-ups** to the maintainer in the session — issues the PR says
   need filing plus anything the review surfaced. Inform-only; the maintainer
   decides.

## Labels

| Label | Meaning |
| ----- | ------- |
| `gate-passed` | Merge-confident: checklist clean, live runs green, review converged. `gh pr list --label gate-passed` = the merge queue. |
| `gate-failed` | Needs a maintainer decision: unconverged review, design-level finding, or an unfixable gap — the verdict comment names it. |

Invocation: `/merge-gate <n>` for one PR, bare `/merge-gate` to sweep every open
non-draft PR carrying neither label.
