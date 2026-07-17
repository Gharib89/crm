---
name: quiz-before-merge
description: Context report + quiz on a change (PR, branch, or session) that the maintainer must pass before merging.
disable-model-invocation: true
argument-hint: "[pr-number | branch | session]"
metadata:
  internal: true
---

# quiz-before-merge

An agent-built change can land with the maintainer understanding less of it than they think — the diff alone hides behavior that depends on existing code paths. This skill closes that gap: a context report, then a quiz; the merge waits until the quiz is passed.

## 1 · Ingest the change

`$ARGUMENTS` names a PR number, a branch, or `session` (the work done in this conversation). Read the full diff **and** the surrounding code of every touched region — call sites, shared state, error paths — because the quiz must cover how the change interacts with what was already there. For a PR, also read the body, linked issue, review threads, and any deviations section. Done when you can explain each hunk's purpose without re-reading it.

## 2 · Context report

Write for a maintainer catching up, not a changelog. Cover, in order: the intuition (what changed and why, in plain language), the mechanics (how it works, which existing paths it touches), the judgment calls the agent made on its own (deviations, chosen trade-offs), and what to watch after merge. Keep it short enough to read before the quiz — the quiz enforces the reading.

## 3 · Quiz

Five to eight questions, answerable from the report plus reasoning — no diff trivia. Weight toward:

- Behavior at the boundaries with existing code: "what happens when X calls this now?"
- The agent's judgment calls: would the maintainer have decided the same way?
- Failure modes: "what happens if Y is empty / offline / already exists?"

Ask all questions at once, wait for answers, grade honestly. For each miss, explain the correct answer, then pose one variant question on the same point.

## 4 · Verdict

Passed = every question, including variants, answered correctly. State pass or fail plainly. On fail, recommend against merging yet and name exactly what to re-read — the specific report section or code region, not "review the change".
