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
PR that has been implemented test-first, integration-tested, self-reviewed,
review-bot-reviewed, and CI-green — with every decision summarized for you to
approve before the irreversible merge.

This skill is **generic**. Everything specific to a given repo — how to run its
integrated tests, which test targets exist and how to pick between them, how to
set up credentials, the local-gate commands, docs-sync rules, the commit-subject
convention, and the review-bot re-request mechanism — lives in that repo's
**project instructions** (its `CLAUDE.md` / `AGENTS.md`). This file states each
step generically and points to "project instructions" for the how. **Before
starting, read the project instructions** and extract these specifics; if any are
missing, that's a gap to surface, not to guess at:

- **Test command** + how to run them from an isolated worktree (venv/path quirks).
- **Integrated/live test** targets, how to pick between them, credential setup.
- **Local-gate commands** — and crucially, *the full set of checks CI runs* (lint,
  type-check, docs build, and any **secret/security scan**), so the local gate can
  mirror CI rather than a fixed triad.
- **Docs-sync rules** — what docs must ship in the same change.
- **Commit-subject convention** (what release tooling reads on squash-merge).
- **Review-bot mechanism** — *whether the repo has an automated reviewer at all*,
  how it's triggered, and how to re-request later rounds.

A repo that documents all of this well lets `/ship` run hands-off; a thin one will
force guesses — surface the gap instead.

## The autonomy contract — why it's shaped this way

The whole point is to stop babysitting a long review loop. So `/ship` runs
unattended through implementation, testing, review, and CI, and reaches **one
guaranteed stop: the merge gate.** That gate is your single review point — you
see every disposition there before approving the squash-merge.

It pauses in only three places:

- **Merge gate (always).** Merging to the default branch is effectively
  irreversible; a human approves it. Never auto-merge.
- **Ambiguity stop (phase 1, only if needed).** If the issue is too
  underspecified to derive a plan, stop and ask rather than build the wrong
  thing — a misread would otherwise only surface at the merge gate, after the
  whole loop ran.
- **Integrated-test hand-off (phase 3, only if needed).** If live-test
  credentials aren't available in the environment, hand the exact command back
  and wait for confirmation.

Everything else — triaging your own review findings and the bot's comments,
fixing them, re-running — happens **autonomously**. No mid-loop pause.

**Never proceed on red.** Any failure before the merge gate (failing test, lint,
type-check, CI) gets a bounded self-fix-and-retry. If it's still red after that,
or the failure means the approach is wrong, **stop and report** — don't push
through, and never merge on red. When you stop, make the report a **fast yes**:
attach the concrete evidence (the failing output / live error) and, if it's cheap
to get, a **verified-working alternative** — converting a blocking question into
a one-glance approve-or-redirect decision instead of an open-ended "what now?".

## Argument

`$ARGUMENTS` is the issue number. If it's omitted, ask which issue. If it's free
text rather than a number, treat it as the task spec directly and skip the issue
fetch in phase 1.

## Consult current docs — don't trust training data for APIs

While implementing (phase 2), triaging review findings (phases 4 and 7), or
debugging a library's behavior, verify against **current** documentation rather
than memory — your training data may lag the installed version. Reach for:

- **context7** (`ctx7` CLI / MCP) for any library, framework, SDK, or CLI tool —
  API syntax, config options, version migration, library-specific debugging.
- **Microsoft Learn** (MCP / docs) for Microsoft / Dataverse / Power Platform /
  Azure APIs, when the project targets them.

This matters most when a review comment cites an API detail: confirm the claim
against the **pinned** version before acting — a review bot may "remember" an API
the installed version doesn't have, and acting on it would be a regression.

## The pipeline

Work the phases in order. Keep the main thread focused on orchestration and
decisions; delegate noisy work (long polls, multi-file scans) to subagents so
this context stays clean.

**Your first action — before phase 0, before the worktree — is to create the
run's task list with the harness task tools** (`TaskCreate` per item; `TaskUpdate`
to change status; `TaskList` to re-read). If those tools aren't already loaded,
fetch their schemas first via `ToolSearch` (`select:TaskCreate,TaskUpdate,TaskList`)
— in some harnesses they're deferred. (Older harnesses name this `TodoWrite`; use
whichever this one exposes.) If `ToolSearch` also turns up nothing, the harness
exposes no task tools at all (some sandboxes — e.g. cloud routines — don't): fall
back to a plain markdown checklist you keep up to date in your replies. Don't stall
on the missing tool — the list's *content* is what matters, not which tool holds it. One item per phase below, exactly one `in_progress`
at a time, each marked `completed` only when its verification passed. Do not start
phase 0 until that list exists (task tools, or the markdown fallback if they're absent). This is the progress surface for an unattended run
and the map back if the context is summarized mid-run — without it, a mid-run
summary leaves you unable to tell which phase you were in, so you skip or repeat
one. Create exactly these ten items:

- [ ] 0 · Isolate — worktree on a fresh branch off default
- [ ] 1 · Understand — fetch issue, derive success, apply spec precedence
- [ ] 2 · Implement — classify (docs/code/infra), then TDD per class
- [ ] 3 · Integrated test — live-test only what you touched, on the reported target
- [ ] 4 · Self-review — `review` skill on the diff, auto-triage findings
- [ ] 5 · Local gate — mirror the full CI checks, all green
- [ ] 6 · Open PR — ready (non-draft), Conventional-Commit title
- [ ] 7 · Review-bot loop — only if the repo has a bot; drive to the ceiling
- [ ] 8 · CI — resolve any base-branch conflict, then land the checks green
- [ ] 9 · Merge gate — hard stop for human merge approval

**Compose, don't reinline.** Load the `tdd` skill (phase 2) and the `review`
skill (phases 4 and 7) through the Skill tool when their phase begins — never
hand-roll their logic. Run review work on subagents at **opus** (review is
judgment; the default is sonnet, which under-reads diffs); mechanical helpers
like a poll loop can stay on the default.

**0 · Isolate.** Before any edit, create an isolated workspace on a fresh branch
off the default branch — `EnterWorktree`, or `git worktree add`. Name the branch
`<type>/<slug>-<issue>` where `<type>` matches the issue (feat/fix/…). All work,
commits, and the PR happen from this branch; clean it up after merge. (The branch
`<type>` is just a label — the commit/PR Conventional-Commit type may differ once
you see the actual change, e.g. a `feat/`-branched enhancement best committed as
`test:` or `docs:`. The squash subject, not the branch, drives release tooling.)

**1 · Understand.** Fetch the issue and its comments. Derive what success looks
like. **Spec precedence:** a later triage brief / authoritative comment can
*supersede* the issue body — when they conflict (scope reduced, an option chosen,
an axis dropped), the latest authoritative spec wins, and the body's original
acceptance criteria no longer bind. Note this explicitly, because a review bot
reading the stale body will flag "missing" requirements you deliberately cut —
you'll reject those in phases 4/7 with this as the reason. **If it's too vague to
plan, stop and ask** (the ambiguity rail). Otherwise continue without pausing.

**2 · Implement.** First **classify the change** into one of three classes — this
decides whether TDD applies and (later) the review ceiling. **Announce the class
you chose and the skip path it implies** — e.g. "classified `docs` → skipping TDD
and the phase-3 integrated test, going straight to the local gate" — so a wrong
label is a visible decision now, not a silently-skipped verification later. Later
phases refer back to this class by name:

- **`docs`** — markdown, comments, config text with no logic: **skip TDD** (no
  behavior to red→green; the phase-5 docs build + link check is the verification).
  Mark the commit `docs:`. Don't manufacture a contrived test.
- **`code`** (feature / bugfix): invoke the `tdd` skill **autonomously** —
  red→green→refactor **without pausing for plan approval** (you're intentionally
  overriding tdd's plan-approval checkpoint; the merge gate is the review point).
- **`infra`** (tooling / refactor where a strict red→green is awkward — the change
  *is* a test harness, build script, or fixture): don't force a contrived red.
  Extract the logic into a testable seam and unit-test its **observable behavior**
  through that seam; let the real run (phase 3) be the integration proof.

When in doubt between `code` and `docs`, treat it as `code` and write the test.

**Verify the spec's external-system claims before building on them.** If the
issue asserts a *causal mechanism* about something outside the code — an API/SDK
behavior, a platform/version constraint, "the server does X / honors Y" — treat
it as a **hypothesis, not a fact** and confirm it against the real target with the
cheapest possible probe (one live read / export / call) *before* writing the fix
around it. A triage brief's root cause is frequently a plausible guess; building
on a wrong one means implementing the fix, having phase 3 disprove it, and
rebuilding from scratch. Verifying up front collapses that loop — and if the
probe contradicts the brief, that's an early stop-and-report, not a phase-3
surprise.

**3 · Integrated test.** Run the project's integrated/live tests **for only what
you touched** — never the whole suite — following project instructions for
targets and credentials, and create or update those tests as part of the work.
**Run on the environment the bug was actually reported against:** if the issue
names a specific target / version / config, test *there* — a different
environment may auto-heal the bug (e.g. a server that silently rewrites the bad
input) and hand you a misleading green. Green ≠ fixed unless it's green where it
failed. If live creds aren't available, print the exact command + required setup,
hand it back, and wait for the user to confirm it passed. (A `docs` change has
nothing to integration-test — skip straight to the local gate.)

**4 · Self-review.** Invoke the `review` skill against the diff. **Auto-triage**
each finding with judgment — harden rather than rip out capability, verify nits
against the pinned dependency versions, reject known non-issues — fix the valid
ones, and record a one-line disposition per finding for the merge summary.

**5 · Local gate.** *Precondition:* phase 3 passed **or** the class is `docs`
(nothing to integration-test) — if neither holds, you skipped a verification;
stop and go back rather than papering over it. Run the project's full local
verification and get it green before opening the PR. **Mirror the checks CI will actually run** (per project
instructions) — not a fixed triad: tests, lint, type-check, docs build, **and any
secret/security scan the repo gates on** (e.g. a credential scanner — its
false-positive patterns are cheap to pre-empt locally and expensive to discover
only after the PR is open). If you can't run a check locally, at least *anticipate*
it. Make sure docs ship in the same change if the project requires it.

**6 · Open PR.** Open a **ready** (non-draft) PR — drafts may not trigger the
project's automated review. Title it as a Conventional-Commit subject derived
from the issue (this is what release tooling reads on squash-merge — see project
instructions), body closes the issue. An automated round-1 review may fire on PR
creation per project config; **don't re-request round 1.**

**7 · Review-bot loop.** **Only if the repo has an automated reviewer configured**
(per project instructions — not every repo does; this is a repo config, never an
assumption). If it doesn't, skip this phase: phase-4 self-review plus green CI is
the review gate. Otherwise drive the automated review to a ceiling — poll,
auto-triage and fix valid comments (same judgment + **opus** review subagents as
phase 4), re-request later rounds via the project's documented mechanism, and
enforce a **3-round hard ceiling**. Scale the ceiling to the change: a small,
targeted PR needs only **one** round, and a `docs` change is capped at **one**
round — address anything actionable, then go to the merge gate; **do not
re-request**. Details and known traps (incl. how to poll without burning
context): **read `reference/copilot-loop.md`.**

**8 · CI.** CI usually runs concurrently from PR-open, so phases 7 and 8 overlap
rather than strictly follow. **First confirm the PR isn't conflicted with the base
branch** — `gh pr view <n> --json mergeable,mergeStateStatus` (`mergeable:
CONFLICTING` / `mergeStateStatus: DIRTY` means conflict). A conflicted PR has no
merge ref, so merge-commit-based checks never start and CI sits **pending
forever** — don't wait on it. Resolve first: fetch the latest default branch and
rebase (or merge) it into the PR branch, fix the conflicts, **re-run the local
gate (phase 5)**, and push — that recomputes the merge ref and lets CI run. Then
ensure the checks that actually run land green. If CI goes red **after** the
review ceiling closed, fix and push, then proceed on green — re-request another
review round only if the fix changed behavior materially (a lint/format/flake fix
doesn't earn a fresh round).

**9 · Merge gate.** **Hard stop.** Post the summary and wait for the user's
explicit "merge"; on approval, squash-merge, delete the branch, and clean up the
worktree. Summary format and merge mechanics: **read `reference/merge-gate.md`.**

## Reference files

- `reference/copilot-loop.md` — phase 7: poll mechanics, re-requesting later
  rounds, the 3-round ceiling, handling bot infra flakes and known non-issues.
- `reference/merge-gate.md` — phase 9: the merge-summary template and the
  squash-merge / cleanup mechanics.
