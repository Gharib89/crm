---
name: ship
description: >-
  Take a tracker issue all the way to a merge-ready PR in one explicit run:
  isolate a worktree, implement test-first, run integrated tests, self-review,
  open the PR, drive the automated code review to a ceiling, gate on green CI,
  then STOP for human merge approval. Composes the `tdd` and `review` skills.
  Explicit-only — invoke deliberately with an issue number; never fires on its own.
user-invocable: true
argument-hint: "[issue-number]"
---

# ship

Drive one issue from nothing to a **merge-ready PR**, hands-off, stopping only at
a final human merge gate. You run `/ship <issue>`, walk away, and come back to a
PR implemented test-first, integration-tested, self-reviewed, bot-reviewed, and
CI-green — every decision summarized for you to approve before the merge.

This skill is **generic**. Everything repo-specific lives in that repo's
**project instructions** (`CLAUDE.md` / `AGENTS.md`). **Before starting, read them**
and extract these; if any is missing, surface the gap — don't guess:

- **Test command** + how to run it from an isolated worktree (venv/path quirks).
- **Integrated/live test** targets, how to pick between them, credential setup.
- **Local-gate commands** — *the full set CI runs* (lint, type-check, docs build,
  any **secret/security scan**), so the local gate mirrors CI, not a fixed triad.
- **Docs-sync rules** — what docs must ship in the same change.
- **Commit-subject convention** (what release tooling reads on squash-merge).
- **Review-bot mechanism** — *whether the repo has one at all*, how it's
  triggered, how to re-request later rounds.

## The autonomy contract

`/ship` runs unattended through implementation, testing, review, and CI, to **one
guaranteed stop: the merge gate** — your single review point. It pauses in only
three places:

- **Merge gate (always).** Merging to the default branch is effectively
  irreversible; a human approves it. Never auto-merge.
- **Ambiguity stop (phase 1, only if needed).** Issue too underspecified to derive
  a plan → stop and ask rather than build the wrong thing.
- **Integrated-test hand-off (phase 3, only if needed).** Live-test creds absent →
  hand the exact command back and wait.

Everything else — triaging your own and the bot's findings, fixing, re-running —
is **autonomous**, no mid-loop pause.

**Never proceed on red.** Any failure before the merge gate (failing test, lint,
type-check, CI) gets a bounded self-fix-and-retry. Still red after that, or the
failure means the approach is wrong → **stop and report**; never merge on red.
Make the report a **fast yes**: attach the concrete evidence (failing output /
live error) and, if cheap, a **verified-working alternative** — a one-glance
approve-or-redirect, not an open-ended "what now?".

## Argument

`$ARGUMENTS` is the issue number. Omitted → ask which issue. Free text rather than
a number → treat it as the task spec directly and skip the issue fetch in phase 1.

## Consult current docs — don't trust training data for APIs

While implementing (phase 2) or triaging findings (phases 4, 7), verify against
**current** docs, not memory — your training data may lag the installed version.
Use **context7** (`ctx7` CLI / MCP) for any library/SDK/CLI, and **Microsoft
Learn** (MCP) for Microsoft / Dataverse / Power Platform / Azure when relevant.
This matters most when a review comment cites an API detail: confirm the claim
against the **pinned** version before acting — a bot may "remember" an API the
installed version doesn't have, and acting on it would be a regression.

## Model tiers — match the model to the work

Use the cheapest model that fits; reserve the strong model for judgment. Tag every
subagent and the poll loop with a model explicitly — never default-inherit.

| Work | Claude | Gemini (agy, when Claude tiers absent) |
|------|--------|-----------------------------------------|
| Investigation / mapping, poll loop | haiku | Flash |
| Mechanical edits, `docs-sync` subagent | sonnet | Flash |
| Review judgment (phases 4 & 7 triage, re-invoked `review` skill) | opus | Pro |

Review is judgment — running it on the cheap tier under-reads diffs. Poll loops and
file-mapping are mechanical — running them on the strong tier burns budget for
nothing. Whichever family the host harness exposes, pick the matching row cell;
fall back to the nearest available tier rather than running everything on one model.

## The pipeline

Work the phases in order; keep the main thread on orchestration and decisions,
delegating noisy work to subagents. **First**, read
[reference/context-discipline.md](reference/context-discipline.md) — it covers how
to keep this long run from bloating the window **and your required first action:
creating the run's ten-item task list** (one per phase below). Don't start phase 0
until that list exists.

**Compose, don't reinline.** Load the `tdd` skill (phase 2) and the `review` skill
(phases 4, 7) through the Skill tool when their phase begins — never hand-roll
their logic. Run review work at the judgment tier (table above); mechanical helpers
stay on the cheap tier.

**0 · Isolate.** Before any edit, create an isolated workspace on a fresh branch
off the default branch — `EnterWorktree`, or `git worktree add`. Name the branch
`<type>/<slug>-<issue>` where `<type>` matches the issue (feat/fix/…). All work,
commits, and the PR happen from this branch; clean it up after merge. (The branch
`<type>` is just a label — the commit/PR Conventional-Commit type may differ once
you see the change, e.g. a `feat/`-branched enhancement best committed as `test:`
or `docs:`. The squash subject, not the branch, drives release tooling.)

**1 · Understand.** Fetch the issue and its comments. Derive what success looks
like. A later authoritative comment can supersede the issue body — **spec
precedence**, detailed in [reference/implement.md](reference/implement.md). **If
it's too vague to plan, stop and ask** (the ambiguity rail).
**Claim it before implementing** — mark the issue in-progress per the project's
claim convention so a concurrent run can't double-pick it (idempotent; skip if
there's no issue or no documented convention). Don't claim if you stopped on the
ambiguity rail; if you claim then stop blocked, hand the issue back.

**2 · Implement.** Classify the change as `docs` / `code` / `infra`, announce the
class and the skip path it implies, then implement test-first per class —
**full detail (classes, TDD override, external-claim verification) in
[reference/implement.md](reference/implement.md).**

**3 · Integrated test.** Live-test **only what you touched**, on the environment
the bug was reported against — **detail in
[reference/implement.md](reference/implement.md).** A `docs` change has nothing to
integration-test — skip to the local gate.

**4 · Self-review.** Invoke the `review` skill against the diff (judgment tier).
**Auto-triage** each finding: harden rather than rip out capability, verify nits
against the **pinned** dependency versions, reject known non-issues; fix the valid
ones; record a one-line disposition per finding for the merge summary.

**5 · Local gate.** *Precondition:* phase 3 passed **or** the class is `docs` — if
neither holds, you skipped a verification; stop and go back. Run the project's full
verification green before opening the PR, **mirroring the checks CI actually runs**
(per project instructions) — not a fixed triad: tests, lint, type-check, docs
build, **and any secret/security scan the repo gates on** (cheap to pre-empt
locally, expensive to discover after the PR is open). If you can't run a check
locally, at least *anticipate* it.

**Docs-sync gate (conditional).** Fire **only if this change altered the documented
CLI surface or observable behavior** — added/removed/renamed a command, flag,
option, or choice; changed a default, an output format, or the JSON contract; or
changed a documented behavior. Then spawn the **`docs-sync`** subagent (mechanical
tier) to bring README, `docs/`, the shipped `crm/skills/` skill, and e2e coverage
back in line, and fold its `FIXED` edits into this change. **Skip** when nothing
user-visible changed — an internal refactor (`infra`), a bugfix that restores
already-documented behavior, test-only / build / tooling changes, or pure comments.
When you skip, say so in one line at the merge gate. (No such subagent → apply the
docs-sync rules by hand under the same condition.)

**6 · Open PR.** Open a **ready** (non-draft) PR — drafts may not trigger the
project's automated review. Title it as a Conventional-Commit subject derived from
the issue (release tooling reads this on squash-merge); body closes the issue. An
automated round-1 review may fire on PR creation — **don't re-request round 1.**
**Reflect the PR back on the issue** right after opening (typically a comment
linking the PR) so a scheduled run won't re-pick it; skip if no documented
convention.

**7 · Review-bot loop.** **Only if the repo has an automated reviewer configured**
(per project instructions — never an assumption). If not, skip: phase-4 self-review
plus green CI is the review gate. Otherwise drive it to a ceiling — poll,
auto-triage and fix valid comments (same judgment + review tier as phase 4),
re-request later rounds via the project's mechanism, **3-round hard ceiling**.
Scale to the change: a small/targeted PR needs **one** round, a `docs` change is
capped at **one**. Mechanics and traps:
**[reference/copilot-loop.md](reference/copilot-loop.md).**

**8 · CI.** CI usually runs concurrently from PR-open, so phases 7 and 8 overlap.
**First confirm the PR isn't conflicted with the base branch** — `gh pr view <n>
--json mergeable,mergeStateStatus` (`CONFLICTING` / `DIRTY` = conflict). A
conflicted PR has no merge ref, so merge-commit checks never start and CI sits
**pending forever** — don't wait on it. Resolve: fetch the latest default branch,
rebase (or merge) it in, fix conflicts, **re-run the local gate (phase 5)**, and
push — that recomputes the merge ref and lets CI run. Then land the checks green.
If CI goes red **after** the review ceiling closed, fix and push, then proceed on
green — re-request another round only if the fix changed behavior materially (a
lint/format/flake fix doesn't earn one).

**9 · Merge gate.** **Hard stop.** Post the summary and wait for the user's
explicit "merge"; on approval, squash-merge, delete the branch, clean up the
worktree. Summary format and merge mechanics:
**[reference/merge-gate.md](reference/merge-gate.md).**

## Reference files

- `reference/context-discipline.md` — keeping the long run from bloating context;
  the required first-action task list.
- `reference/implement.md` — phases 1–3: spec precedence, change classification,
  external-claim verification, run-where-it-failed.
- `reference/copilot-loop.md` — phase 7: poll mechanics, re-requesting rounds, the
  3-round ceiling, bot infra flakes and known non-issues.
- `reference/merge-gate.md` — phase 9: the merge-summary template and the
  squash-merge / cleanup mechanics.
