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
3. **Auto-triage every comment with the same triage as phase 4** (harden rather
   than rip out capability, verify nits against the **pinned** versions, reject
   documented non-issues, fix the valid ones, record a one-line disposition for the
   merge summary) — run it on the **judgment tier**; the poll loop in step 2 stays
   on the **cheap tier** (model-tier table in SKILL.md). Two phase-7 specifics:
   - **Batch the fixes into one push per round** — each round costs review credits.
   - If the bot **re-raises** a known non-issue, confirm the project's
     known-non-issues note wasn't trimmed before re-arguing.
4. **Re-request the next round** via the project's documented mechanism (the
   re-request path is project-specific — read project instructions; a plain push
   usually does **not** re-trigger it). **Verify the re-request actually took** —
   some APIs return success but silently no-op.
5. **Repeat to the ceiling.**

## Counting rounds — what increments the counter

A **round** is one *substantive* bot review of the current committed tree: a real
read that lands with either actionable comments or a clean pass. Count them so the
ceiling is unambiguous:

- **+1 — round 1:** the automatic review on PR creation (step 1). Always the first
  round.
- **+1 — each re-requested review that lands** (step 4) with a real read: a body
  with comments, or a zero-comment clean pass.
- **Does NOT count** (free retries — never burn the ceiling on these):
  - an **infra-flake** body ("encountered an error and was unable to review");
  - a re-request that yields **no review at all** within the poll window;
  - a **push with no re-request** — the bot doesn't re-read on push
    (`review_on_push: false`), so it produces no round.

## The ceiling — split by change class

Hard ceilings, not soft targets — past the budget, reviews are re-read artifacts
and nits, not new signal. Match the budget to the change:

| Change | Round budget |
|--------|--------------|
| `docs`-class | **1** (hard cap — often zero actionable comments; clean read + green CI is the green light) |
| **small** — passes the small-lane test (below) | **1** — round 1 (automatic) is the review gate; address it, then straight to the merge gate; don't re-request |
| everything else (full lane) | **up to 3** |

**"Small" is the small-lane test, not a vibe.** Spend the **1**-round budget only
when a change passes *all three* keys of *The small lane* (SKILL.md): **no
public-surface change**, **provable without a live call**, **single-concern**.
Miss any one — or you're unsure — and it's **not** small: take the full **3**.
(A bugfix still qualifies; small means narrow + locally provable + invisible to
the documented surface, not zero-behavior — the full keys are in SKILL.md.) The
budget is **revocable**: if a "small" PR turns out to touch public surface, or
round 1 surfaces a real bug, it has downgraded to the full lane and its budget is
now **3**.

When the budget is spent, **triage any remaining items and proceed** to CI + the
merge gate — do not re-request.

**A post-budget fix does not reopen the budget.** Once a class's rounds are spent,
a later CI-red fix or extra push earns another round **only if the class still has
budget left** — full lane only; `docs` and small are capped at **1**, so they get
**none** (proceed on green) — **and** the fix **materially changed behavior**. A
lint / format / flake fix never earns a round. The **3-round ceiling** is the
absolute cap regardless: a full-lane change never exceeds it.

## What counts as "done reviewing"

- **Clean pass:** a review with zero actionable comments. That plus green CI is
  the green light — don't mistake a quiet clean review for "hasn't run yet".
- **Ceiling reached:** the change's round budget is spent, remaining items
  triaged. Proceed.

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
