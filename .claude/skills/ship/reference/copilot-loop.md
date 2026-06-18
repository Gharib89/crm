# Phase 7 — driving the automated review to a ceiling

The goal is a clean review (or a sane ceiling) without you babysitting it. The
review bot re-reads the **whole PR** on each round and ignores your replies, so
treat every round's output as a fresh read of the committed tree, not a
conversation.

## The loop

1. **Round 1 is automatic.** Most setups request the bot on PR *creation* (per
   project instructions). Don't re-request round 1 — just wait for it.
2. **Poll for the review, don't hand back to the user.** Reviews take minutes.
   For a single PR, the simplest reliable approach is to **poll directly** with a
   short bounded loop (`gh pr view <n> --json reviews,statusCheckRollup`, a
   `sleep`, repeat to a cap), then act on the result. If you instead delegate to a
   subagent to keep context clean, it **must block and return ONE final summary** —
   do not have it set up an async/background monitor that detaches and emits
   partial "still waiting" notifications (that adds noise and returns nothing
   actionable). Tell the subagent explicitly: poll in a bounded loop, return only
   when the review has landed (or the cap is hit), and report review state +
   comments + final check conclusions in one message.
3. **Auto-triage every comment** with the same judgment as the self-review (run
   this triage / any re-invoked `review` skill on the **judgment tier** — opus, or
   Gemini Pro in agy; the poll loop in step 2 is mechanical and stays on the
   **cheap tier** — haiku / Flash. See the model-tier table in SKILL.md):
   - Harden rather than remove capability when a comment flags a footgun.
   - Verify nit-level claims against the **pinned** dependency versions before
     acting — APIs the bot "remembers" may not match what's installed.
   - Reject known non-issues the project has already documented as such; if the
     bot re-raises one, confirm the project's known-non-issues note wasn't
     trimmed before re-arguing.
   - Fix the valid ones. Batch the fixes into **one push per round** (each round
     costs review credits).
   - Record a one-line disposition per comment (fixed / rejected-because / n/a)
     for the merge summary.
4. **Re-request the next round** via the project's documented mechanism (the
   re-request path is project-specific — read project instructions; a plain push
   usually does **not** re-trigger it). **Verify the re-request actually took** —
   some APIs return success but silently no-op.
5. **Repeat to the ceiling.**

## The 3-round hard ceiling

Stop requesting after **~3 rounds**. It's a hard ceiling, not a soft target —
later rounds are re-read artifacts and nits, not new signal. After the third
round closes, move to CI + the merge gate.

For small, targeted PRs (single bug fix, doc tweak), **one round is enough** —
address round 1, then go straight to the merge gate. Don't re-request. A
`docs`-class change is **capped at one round** by rule (it often draws zero
actionable comments — a clean read plus green CI is the green light; proceed).

## What counts as "done reviewing"

- **Clean pass:** a review with zero actionable comments. That plus green CI is
  the green light — don't mistake a quiet clean review for "hasn't run yet".
- **Ceiling reached:** ~3 rounds done, remaining items triaged. Proceed.

## Infra flakes — don't burn the ceiling on them

- A review whose body says it "encountered an error and was unable to review"
  with zero comments is an **infra failure**, not feedback. Re-request; after a
  couple consecutive error bodies, stop and proceed on green CI.
- A correctly-formed re-request can simply produce **no review at all** (silence,
  no error). That's flakiness, not a missed poll. Bounded wait (~one poll
  window), then proceed per the ceiling — don't loop forever.

## Cleanup

Stop the poller **surgically** (its recorded PID / the task handle), never a
broad pattern-kill that could match — and silently drop — the command you run
next. After any merge command, re-verify PR state before declaring done.
