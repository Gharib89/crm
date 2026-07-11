# Phase 7 — driving the automated review to convergence

The goal is a clean review without you babysitting it. A review bot re-reads the
**whole PR** on each round and ignores your replies, so treat every round's output
as a fresh read of the committed tree, not a conversation.

Two reviewer roles, assigned by project instructions:

- **Round-1 reviewer** — auto-reviews once on PR creation and is **dispositioned
  once**: address each thread, or decline with evidence. **Never re-requested in
  the ship flow** (a plain push does not re-trigger it; `review_on_push: false`).
- **Iterating reviewer** — a push-triggered bot (if the repo runs one) that
  **re-reviews automatically on every push** and **owns iteration**. Its rounds
  are free; drive them to quiet.

A repo may run one or both. If it runs neither, phase-4 self-review + green CI is
the review gate — skip this phase.

## The round-1 reviewer — disposition once

1. **Round 1 is automatic** on PR creation (per project instructions). Wait for
   it; don't re-request.
2. **Auto-triage every comment** — the canonical phase-4 definition in SKILL.md,
   on the judgment tier. Fix the valid ones; decline the rest with evidence. If it
   re-raises a known non-issue, confirm the project's known-non-issues note wasn't
   trimmed before re-arguing.
3. That's it — **no second round in the ship flow**. The threads are
   dispositioned; iteration (if any) belongs to the push-triggered reviewer below.
   (The lone exception — the merge-gate spending one re-request on a PR it
   significantly rewrote — lives in the `merge-gate` skill, not here.)

## The iterating (push-triggered) reviewer — drive to quiet

If project instructions name a reviewer that re-reviews on push:

- **Same auto-triage** (judgment tier): address or decline with evidence. Unlike
  the round-1 reviewer, it *reads replies* — reply **on each review thread**
  ("fixed in `<sha>`" / decline + evidence), and use its documented
  thread-resolution mechanism only once **every** thread carries a disposition.
- **Batch fixes into one push per round** — every push spends its (usually
  rate-limited) review quota and triggers one fresh round.
- **Its rounds are free** — no re-request, no ceiling. Keep going until a push
  comes back quiet.

## Converged — the phase-7 exit

**Converged = the iterating reviewer quiet on the latest push + every round-1
thread dispositioned.** With only a round-1 reviewer, converged = its threads
dispositioned + green CI. Each reviewer's dispositions get their **own block** in
the merge summary (per-reviewer, not merged into one list) — the lanes need that
accountability.

If the iterating reviewer stays substantive round after round, that's a shape
problem more rounds won't fix — stop and report, don't loop forever.

## Poll mechanics

Reviews take minutes. For a single PR, **poll directly** with a short bounded loop
(`gh pr view <n> --json reviews,statusCheckRollup`, a `sleep`, repeat to a cap),
then act. If you delegate polling to a subagent to keep context clean, it **must
block and return ONE final summary** — never a detached background monitor that
emits partial "still waiting" notifications. Tell it explicitly: poll in a bounded
loop, return only when the review lands (or the cap hits), and report review state
+ comments + final check conclusions in one message. Keep the poll loop on the
**cheap tier**; auto-triage on the **judgment tier** (model-tier table in SKILL.md).

## Infra flakes — don't wait forever

- A review body that says it "encountered an error and was unable to review" with
  zero comments is an **infra failure**, not feedback. For the iterating reviewer,
  the next push re-triggers it; after a couple consecutive error bodies, proceed
  on green CI.
- A re-review that simply never lands (silence, no error) is flakiness, not a
  missed poll. Bounded wait (~one poll window), then proceed — don't loop forever.

## Cleanup

Stop the poller **surgically** (its recorded PID / task handle), never a broad
pattern-kill that could match — and silently drop — the command you run next.
After any merge command, re-verify PR state before declaring done.
