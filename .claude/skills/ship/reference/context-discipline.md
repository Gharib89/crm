# Context discipline — a ship run is long; protect the main thread

A full ship touches many files across many turns. What bloats the window is raw
tool output landing in the main thread, not the work itself — so spend tokens on
decisions, not dumps. In rough order of impact:

- **Delegate reading, not just review.** Don't read N files into the main thread
  to build a mental model. Send the investigation to a subagent (at the cheap
  tier — see the model-tier table in SKILL.md) — "map how X, Y, Z connect; return
  signatures, call sites, and the data shapes" — and it returns a small
  conclusion, then read only the exact lines you will edit. This is the single
  biggest lever: a file body you only need to *understand* should never enter main
  context, only the hunk you *change* should.
- **Project every `gh` / CLI / API call.** Pipe `gh … --json <only-the-fields>
  --jq '…'`. A bare `gh pr view --json` serializes the entire PR object (repo
  metadata twice, every URL field) — kilobytes of noise from one call.
- **Investigate inside the worktree from the start** (phase 0 first), so you never
  read a file in the main checkout and then re-read it in the worktree to edit it.
- **Trust Edit/Write — don't verify-Read after a successful edit.** The tool
  errors if the match failed and the harness tracks file state for you; a re-Read
  to "confirm" the change is pure cost.
- **Targeted test nodes during the loop; full suite only at the local gate.** Name
  the nodes you touched; re-running the whole suite every cycle is slow noise.
- **Delegate the noisy verification *runs*, not just reads.** The full local gate
  (phase 5), live integration tests (phase 3), and CI polling (phase 8) each dump
  volumes of output. Run them in a cheap-tier subagent (model-tier table in
  SKILL.md) that returns a pass/fail summary plus only the failing lines — never
  let raw suite / build / CI logs land in the main thread.
- **One scratch file for the design/plan** (it survives a mid-run context
  summary); don't restate the same summary across turns.

## First action — the run's task list

**Before phase 0, before the worktree**, create the run's task list with the
harness task tools (`TaskCreate` per item; `TaskUpdate` to change status;
`TaskList` to re-read). If those tools aren't already loaded, fetch their schemas
first via `ToolSearch` (`select:TaskCreate,TaskUpdate,TaskList`) — in some
harnesses they're deferred. (Older harnesses name this `TodoWrite`; use whichever
this one exposes.) If `ToolSearch` also turns up nothing, the harness exposes no
task tools at all (some sandboxes — e.g. cloud routines — don't): fall back
to a plain markdown checklist you keep up to date in your replies. Don't stall on
the missing tool — the list's *content* is what matters, not which tool holds it.

One item per phase, exactly one `in_progress` at a time, each marked `completed`
only when its verification passed. This is the progress surface for an unattended
run and the map back if context is summarized mid-run — without it, a mid-run
summary leaves you unable to tell which phase you were in, so you skip or repeat
one. Create exactly these ten items:

- [ ] 0 · Isolate — worktree on a fresh branch off default
- [ ] 1 · Understand — fetch issue, derive success, claim it, apply spec precedence
- [ ] 2 · Implement — classify (docs/code/infra), then TDD per class
- [ ] 3 · Integrated test — live-test only what you touched, on the reported target
- [ ] 4 · Docs-sync + self-review — sync docs first so the review covers them, then `review` skill on the diff, auto-triage findings
- [ ] 5 · Local gate — mirror the full CI checks (covers the synced docs), all green
- [ ] 6 · Open PR — ready (non-draft), Conventional-Commit title, reflect on the issue
- [ ] 7 · Review-bot loop — only if the repo has a bot; drive to the ceiling
- [ ] 8 · CI — resolve any base-branch conflict, then land the checks green
- [ ] 9 · Merge gate — hard stop for human merge approval
