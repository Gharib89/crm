---
name: ship
description: >-
  Take a tracker issue all the way to a merge-ready PR in one run: isolate a
  worktree, implement test-first, run integrated tests, self-review, open the PR,
  drive the automated code review to a ceiling, gate on green CI, then stop at a
  human merge gate. Composes the `tdd` and `review` skills. Use when the user
  wants to ship an issue or take an issue through to a PR; also invoked by the
  cloud ship routine.
argument-hint: "[issue-number]"
---

# ship

Drive one issue from nothing to a **merge-ready PR**, hands-off, stopping only at
a final human merge gate. You run `/ship <issue>`, walk away, and come back to a
PR implemented test-first, integration-tested, self-reviewed, bot-reviewed, and
CI-green — every decision summarized for you to approve before the merge.

This skill is **generic** — everything repo-specific lives in that repo's
**project instructions** (`CLAUDE.md` / `AGENTS.md`). **Read them first** and pull
what the phases below need; if any is missing, surface the gap — don't guess: the
**test command** (run from a worktree), **integrated/live-test** targets + creds,
the **full local-gate set CI runs** (not a fixed triad), **docs-sync rules**, the
**commit-subject convention**, and **whether a review bot exists** + how to
trigger/re-request it.

## The autonomy contract

`/ship` runs unattended through implementation, testing, review, and CI, to **one
guaranteed stop: the merge gate** — your single review point. It pauses in only
three places:

- **Merge gate (always).** Merging to the default branch is effectively
  irreversible; a human approves it. Never auto-merge.
- **Ambiguity rail (phase 1, only if needed).** Issue too underspecified to derive
  a plan → stop and ask rather than build the wrong thing.
- **Integrated-test hand-off (phase 3, only if needed).** Live-test creds absent →
  hand the exact command back and wait.

Everything else — triaging your own and the bot's findings, fixing, re-running —
is **autonomous**, no mid-loop pause.

**Never proceed on red.** Any failure before the merge gate (failing test, lint,
type-check, CI) gets a bounded self-fix-and-retry (~2 attempts). Still red after that, or the
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

| Work | Model |
|------|-------|
| Investigation / mapping, poll loops (review + CI) | haiku |
| Mechanical edits & fixes, docs-sync helper, running the gates/tests, `review` skill's **Spec** axis | sonnet |
| Triage judgment (phases 4 & 7), `review` skill's **Standards / code** axis | opus |

Triage and code review are judgment — running them on the cheap tier under-reads
diffs. Poll loops, file-mapping, and running the gates are mechanical — running them
on the strong tier burns budget for nothing. The `review` skill sets its own
per-axis models (Standards = opus, Spec = sonnet). Fall back to the nearest
available tier rather than running everything on one model.

## The small lane

Most issues run the full pipeline. A genuinely **small** change runs a reduced
spine — skip the ceremony that can't matter, keep the safety that always does.

**Small ⟺ all three hold** (assert at phase 2, announced like the class; when
unsure, it's *not* small):

1. **No public-surface change** — adds/removes/renames no command, flag, option, or
   choice; changes no default, output format, or API/output contract.
2. **Provable without a live call** — a unit/regression test fully proves it; no
   need to hit the live target.
3. **Single-concern** — no new dependency, no new logic branch beyond the fix itself.

Behavior change is allowed — a bugfix *is* one. Small means narrow + locally
provable + invisible to the documented surface, not zero-behavior.

**What collapses.** Items 1–2 already make the docs-sync gate and the phase-3
integrated test no-ops by construction (nothing user-visible changed; nothing live
to test). On top of that, two explicit cuts:

- **Skip phase 4 self-review *iff* the repo has an auto-review bot** — its automatic
  round-1 (phase 7) is the review gate. No bot → keep one phase-4 pass, else the
  change gets no review.
- **Local gate = the secret/security scan only.** Lean on CI for the suite, lint,
  and type-check — CI re-runs them, and a red CI on a small change is a cheap
  round-trip.

**The floor — never collapses, even for small:** the worktree (phase 0), **one
regression test** proving any behavior change, the **local secret scan** (the repo
may be public — a leaked cred is irreversible), the PR, CI, and the **merge gate**.

**Revocable.** The lane is falsifiable: any later contradiction — CI red on
behavior, the bot flags a real bug, the secret scan hits, or you find it touches
public surface — **downgrades to the full lane** for the remaining phases (run the
skipped integrated test / self-review, add the test). Downgrading once is cheap;
shipping a non-small change as small is the failure.

## The pipeline

Work the phases in order; keep the main thread on orchestration and decisions,
delegating noisy work to subagents. **First**, read
[reference/context-discipline.md](reference/context-discipline.md) — it covers how
to keep this long run from bloating the window **and your required first action:
creating the run's ten-item task list** (one per phase below). Don't start phase 0
until that list exists.

**Compose, don't reinline.** Load the `tdd` skill (phase 2) and the `review` skill
(phases 4, 7) through the Skill tool when their phase begins — never hand-roll
their logic. The `review` skill picks its own per-axis tiers (opus code / sonnet
spec); run finding-**triage** at the judgment tier, mechanical helpers at the cheap
tier (table above).

**0 · Isolate.** **Pre-flight:** confirm the issue is actionable and not already in
flight — if it's closed, already has an open or merged PR, or a branch already
exists for it, **stop and report** instead of opening a duplicate. (A picker-based
runner like the cloud routine relies on the phase-1 `agent-working` claim to block
concurrent re-picks; this guard catches a *manual* re-run or an already-shipped
issue, where there is no picker.) Then create an isolated workspace on a fresh
branch off the default branch — `EnterWorktree`, or `git worktree add`. Name the
branch `<type>/<slug>-<issue>` where `<type>` matches the issue (feat/fix/…). All
work, commits, and the PR happen from this branch; clean it up after merge.
**Commit as you go** — intermediate messages don't matter, but the PR needs real
commits. (The branch
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
class and the skip path it implies — **and whether it meets the small-lane
checklist** (if so, announce that and take the reduced spine) — then implement
test-first per class —
**full detail (classes, TDD override, external-claim verification) in
[reference/implement.md](reference/implement.md).** **Stay surgical** — implement
only what the issue asks; every changed line should trace to it. An adjacent bug or
cleanup you spot is **out of scope**: file a `needs-triage` issue for it and move
on, don't fix it inline.

**3 · Integrated test.** Live-test **only what you touched**, on the environment
the bug was reported against — **detail in
[reference/implement.md](reference/implement.md).** A `docs` change has nothing to
integration-test — skip to the local gate.

**4 · Self-review.** Invoke the `review` skill against the diff (it runs its two
axes on their own tiers — opus for code, sonnet for spec).
**Auto-triage** each finding: harden rather than rip out capability, verify nits
against the **pinned** dependency versions, reject known non-issues; fix the valid
ones; record a one-line disposition per finding for the merge summary.

**5 · Local gate.** *Precondition:* phase 3 passed **or** the class is `docs` — if
neither holds, you skipped a verification; stop and go back.

**Docs-sync gate (conditional) — run it first.** Fire **only if this change altered
the documented surface or observable behavior** — added/removed/renamed a command,
flag, option, or choice; changed a default, an output format, or an API/output
contract; or changed a documented behavior. Then bring the project's documented
artifacts (README, `docs/`, any shipped skill, tests/coverage) back in line **per
the project's docs-sync rules** — using the project's docs-sync subagent at the
mechanical tier if it has one, else by hand — and fold the edits into this change.
Do this **before** the gate below, so the docs build, link-check, and tests cover
its new files. **Skip** when nothing user-visible changed — an internal refactor
(`infra`), a bugfix that restores already-documented behavior, test-only / build /
tooling changes, or pure comments; when you skip, say so in one line at the merge
gate.

Then run the project's full verification green before opening the PR, **mirroring
the checks CI actually runs** (per project instructions) — not a fixed triad:
tests, lint, type-check, docs build, **and any secret/security scan the repo gates
on** (cheap to pre-empt locally, expensive to discover after the PR is open). If you
can't run a check locally, at least *anticipate* it.

**6 · Open PR.** Open a **ready** (non-draft) PR — drafts may not trigger the
project's automated review. Title it as a Conventional-Commit subject derived from
the issue (release tooling reads this on squash-merge). **If the repo has a PR
template (`.github/PULL_REQUEST_TEMPLATE.md` / `docs/pull_request_template.md`),
fill it in** — write the summary and tick / strike-through each checklist item
honestly against the work you did, keeping the `Closes #<issue>` keyword. Don't
pass a raw `--body` that bypasses the template; let it populate, then edit. (No
template → a plain body that closes the issue.) An
automated round-1 review may fire on PR creation — **don't re-request round 1.**
**Reflect the PR back on the issue** right after opening (typically a comment
linking the PR) so a scheduled run won't re-pick it; skip if no documented
convention.

**7 · Review-bot loop.** **Only if the repo has an automated reviewer configured**
(per project instructions — never an assumption). If not, skip: phase-4 self-review
plus green CI is the review gate. Otherwise drive it to a ceiling — poll,
auto-triage and fix valid comments (same auto-triage as phase 4, on the judgment tier),
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
