# The small lane — the reduced spine

A change that passes all three lane keys (SKILL.md → *The lanes*) skips the
ceremony that can't matter and keeps the safety that always does.

## What collapses

Keys 1–2 already make the phase-4 docs-sync gate and the phase-3 integrated test
no-ops by construction (nothing on the public surface changed; nothing live to
test). On top of that:

- **Skip the phase-4 self-review *iff* the repo has an auto-review bot** — its
  automatic round-1 (phase 7) is the review gate. No bot → keep the self-review
  pass, else the change gets no review.
- **Local gate (phase 5) = the secret/security scan + the one regression test
  node** (run that node locally for red→green proof). Lean on CI for the rest of
  the suite, lint, and type-check — CI re-runs them, and a red CI on a small
  change is a cheap round-trip.
- **Round-1 review is the whole review gate** — disposition it once and go; the
  iterating reviewer (if any) still owns its free push-rounds. See
  [copilot-loop.md](copilot-loop.md).
- **Subagents: usually none of your own.** You can already point at the file (no
  mapper), and the proving test node's output is short (run it inline); the
  `code-review` skill and the poll loop bring their own. The delegation rule is in
  [context-discipline.md](context-discipline.md).

## The floor — never collapses

The worktree (phase 0), **one regression test** proving any behavior change, the
**local secret scan** (the repo may be public — a leaked cred is irreversible),
the PR, CI, and the **merge gate**.

## Revocable

The lane is falsifiable: any later contradiction — CI red on behavior, the bot
flags a real bug, the secret scan hits, or you find the change touches the public
surface — **downgrades to the full lane** for the remaining phases: run the
skipped integrated test / self-review, add the missing test or docs-sync, and the
review budget becomes the full 3. Downgrading once is cheap; shipping a non-small
change as small is the failure.
