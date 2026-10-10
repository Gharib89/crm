# Phase 7: a bounded, best-effort pass per reviewer

## Contents

- [The round](#the-round)
- [Reading a round](#reading-a-round)
- [Triage, fix, reply](#triage-fix-reply)
- [The exit](#the-exit)
- [Fallbacks](#fallbacks)
- [Worked examples](#worked-examples)

The review gate is phase 4's self-review plus green CI; the reviewers under the
profile's `## Reviewers` are a second pair of eyes on top of it. So phase 7 is
best-effort: ask each reviewer for a round, wait a bounded time, triage whatever
landed, and report what happened. A reviewer that answers nothing costs one
bounded wait, not an investigation. Preflight has already refused the malformed
blocks. Zero reviewers: skip the phase. A reviewer re-reads the **whole PR**
each round: treat each round as a fresh read of the committed tree.

## The round

The block's `Trigger:` fixes how a round starts; the brand fixes nothing.

- **`on-request`**: `request-review <pr> --reviewer <name>`, then poll with no
  `--since`, since `poll-pr` asks the host when the request was made; on Azure
  DevOps, which records none, pass `--since` its `requested_at`. The block's
  `Request:` picks the transport (the host's own request call, or a PR comment
  of the phrase for a reviewer a comment triggers), and `request-review`
  confirms the request queued; a request it cannot confirm exits 1, and the
  reviewer is `not reviewed: never-queued`, with no poll.
  Under the host's own request call, round 1 first polls `--since` `open-pr`'s
  `created_at` at `--timeout 0`: a round the host opened unbidden with the PR (a
  Copilot ruleset, `review_on_push: false`) that has landed is round 1, and the
  request is sent only where none has.
- **`auto-once`**: nothing to request; poll once with `--since` `open-pr`'s
  `created_at`. That one round is all there is.
- **`on-push`**: every push earns a round; poll with no `--since`, which counts
  a round only on the current head.

Per round: start it, `poll-pr <pr> --reviewer <name> [--since <iso>] --brief`,
inline or in the background, resuming a `pending` answer with its `--cursor`
([mechanics.md](mechanics.md#waits-past-540-s) has the cap), triage what landed,
push the fixes once, reply. The next round starts only while the latest round's
fixes changed the tree, the **fix-only diff** (the commits pushed since the last
head a reviewer read) classifies `code` or `infra` under [phase 2's
classes](implement.md#phase-2-classify-then-implement-test-first), its
when-in-doubt rule included, and `Cap:` has rounds left. Here a fix-only diff
touching a file an agent executes as instructions (a skill's `SKILL.md` or its
reference files, `CLAUDE.md`, `AGENTS.md`, a rules file) is `code`, though phase
2 files it under `docs`: a fix to it changes behaviour. Any other `docs`
fix-only diff is **docs-only** and ends the host loop, because another round
would buy wording nits. The poll takes its bound from the block, and a window
that closed is the answer rather than a reason to re-poll, except after a
`conflict`, which says nothing about the reviewer: resolve it (phase 8) and poll
again.

**`Cap:`** is the budget on rounds ship starts: a number, or `None.` for an
uncapped loop; `auto-once` delivers one round whatever it reads, and under
`on-push` the number ends ship's engagement while the reviewer may carry on. A
round at the cap is dispositioned in full and ends the loop; say whether it was
still landing real findings, which tells the human whether the budget was
right. Small lane: at most one requested round. A lint or flake fix after the
loop ends earns no new request; an on-push reviewer re-reads it on its own, so
disposition that round, which opens no further one.

**The cap round and a docs-only round each get one local review before the merge
gate** when their fixes changed the tree: one `code-review` subagent, Standards
axis only, over the fix-only diff. Its findings take the dispositions above;
`Cap:` is unchanged and no host round is requested. Skip this review for a
primary capped with findings whose fallback then exited `reviewed`: the
fallback's host rounds read those fixes. The review goes to the last reviewer
that stopped at `cap`, primary or fallback, and to a capped primary whose
fallback exited `not reviewed`.

## Reading a round

`poll-pr` returns the reviewer's rounds, each graded `substantive`, threads with
resolved state, `landed_by` naming the rule that admitted a round, `refused_by`
a quota or rate-limit notice, or a native reviewer's reply, admitted in its
place, `reviewer_run` for a comment transport, `reviewer_blocked` a quota
notice the rule did not admit (cite it in a trailing clause, never as the
reason), and `not_reviewed`, the cause the poll observed where no round was
admitted.

- **`--brief` is how a round is read**: one `rounds[]` row per round, its body
  stripped of HTML and cut to the lead line, the `Findings:` line and the
  finding items, the ones inside a `<details>` block included, so a `Findings:
  None` round can still carry a `Previously missed` item; and one row per OPEN
  thread, the run's own replies dropped. `--brief --full open` returns every
  round and every open thread whole.
- **A body or `lead` ending `...[truncated]` has not been read.** Re-poll with
  `--brief --full <id>` for that round or thread before triage. Pass every id
  you will triage in one comma-separated `--full` list, so each thread is read
  whole.
- **Findings live in the body as well as in threads**, and a reviewer's reply to
  one thread posts as a bodiless review row that looks like the next round
  arriving: only `substantive: true` counts.
- **The since rule needs a timed round.** An Azure DevOps vote carries no time,
  so a reviewer whose only signal is a vote reads `silent` under it; its
  threads are stamped and land normally.
- **A comment transport's window is its status source**: the workflow run
  `Workflow:` names, held open while the run is going, or, under `Workflow:
  native codex`, Codex's own status on the PR, reported on `reviewer_run` in
  the run's shape. Run no `update-pr-title` between a workflow request and its
  poll: the run is matched by the PR's title. A clean Codex round is a landed
  round with no threads.
- **Write a comment-transport phrase without its `@` everywhere but the
  request.** A reply, a thread answer, a PR body or a reflect comment names
  `codex review`, not the phrase itself: the host delivers every mention, and a
  stray one starts a round or a Codex task no poll is waiting for (on an issue,
  Codex answered one with a request to create an environment).
- **A poll waits for the expected head**, the worktree's `HEAD` on the PR's
  branch or `--sha`. `not_reviewed: unreachable` with a `head_sha` that is not
  that head means the host never showed the push: confirm it landed, then poll
  again.

## Triage, fix, reply

- **Triage, don't apply**, at the judgment tier, with [phase 4's definition and
  its two rejection rails](implement.md#phase-4-triage-and-depth-checks). Check
  the reviewer's `Instructions:` file when a finding contradicts it, and cite it
  when declining. A valid finding outside the issue is an adjacent find.
- **Batch fixes into one push per round.** A fix to a rule goes to every copy of
  that rule in the same batch: grep the phrase before you push and read each
  hunk back.
- **Reply in the thread**: `reply-thread <pr> <thread> --body-file` for every
  `replied: false` thread, `fixed in <sha>` or the decline and its reason. Once
  every thread carries a reply, run the block's `Resolve:` per thread; `Resolve:
  None.` means the findings are answered with `comment-pr`, which also answers a
  body finding with no thread. A finding about the PR body is fixed through the
  writes [pr-body.md](pr-body.md) names.
- **Write each round to the Run file as you disposition it**, one line per
  finding with its disposition, one per round whose `reviewer_run.denied`
  is numeric, with the run URL, and the round's `Round:` line (formats under
  [The exit](#the-exit)): the exit's counts come from them.

## The exit

Each reviewer exits with one of:

- `reviewed`: at least one round landed and was dispositioned. A later round
  that did not land is a trailing clause, not a different exit.
- `not reviewed: <reason>`: no round landed, or one did and its threads could
  not be read (`unreachable`): triage that one off its body and answer it with
  `comment-pr`. The reason is `not_reviewed` off the last poll, or
  `never-queued` off `request-review`'s exit 1, and never one you infer: a human
  saying a reviewer "can't review" is a claim to check against the poll. The
  header of `scripts/poll-pr.sh` lists what each cause means. `still-running`
  is a run live at the ceiling: exit with it as read, with no further window,
  and the run's URL on `reviewer_run` goes to the merge gate. `stale-head` is
  Codex's completed round on an older commit with nothing delivered: exit with
  it as read, the status comment's URL to the merge gate.
- `not invoked: <primary> reviewed`: a fallback whose primary reviewed and was
  not capped with findings ([Fallbacks](#fallbacks)).

`not reviewed` proceeds to the merge gate on green CI and is reported there. A
`Gating: yes` reviewer holding the merge on a finding you declined still exits
`reviewed`; the merge summary cites the declined finding with its evidence as
the override the human decides on.

One line per reviewer goes in the PR body's `## Review` section and the merge
summary, in this shape:

```
- <reviewer>: <exit>, <n> rounds, <raised> findings: <accepted> accepted, <declined> declined, <filed> filed
- <fallback>: not invoked: <primary> reviewed
```

A `not reviewed` reviewer with no rounds states its exit alone. A trailing
clause is added only where the counts leave something out: a cap that ran out
mid-findings, `ended: docs-only fix` where a docs-only fix-only diff ended
the loop, the primary's reason on a fallback that ran, a round that did not land
after one that did, or `<N> denied calls (run <url>[, run <url>…])`, N
summing the numeric `reviewer_run.denied` over the rounds, each non-zero round's
run URL listed, and nothing added for a total of 0 or no numeric count.

Each reviewer's rounds and stop go in the Run file as lines, which `run-file
close 7` requires of every reviewer on the phase-7 row, one `Round:` per round
(`not reviewed` needs none) and one `Stop:`:

```
Round: <reviewer> <n>: <text>
Stop: <reviewer>: <reason>
```

`<text>` is the round's outcome in a line. When the last fully read and
dispositioned round changed no file, record `tree unchanged` even at `Cap:`, in
the small lane or for an `auto-once` trigger. Otherwise `<reason>` is one of
`cap` (`Cap:` spent, or a docs-only fix-only diff ended the loop: either way the
last fixes went unread by a host round), `tree unchanged` (a round's
dispositions changed no file, so a further round would read the same tree),
`small lane` (the lane's one requested round), `auto-once` (the reviewer fires
once, on PR open) or `not reviewed` (no round was dispositioned: none landed, a
fallback was not invoked because its primary reviewed, or a round landed whose
threads could not be read, which keeps its `Round:` line too). A declined
finding from a `Gating: yes` reviewer also leaves `Override: <reviewer>:
<finding>, <evidence>` in the Run file, so the clean-gate decision reads the
same override the human sees in the summary.

At exit, from the Run file and never from the body the write replaces, one
`update-pr-body <pr> --section <name> --body-file <path>` per section, each
file carrying its section rebuilt entire: `"Special things to note"` where the
rounds grew the deviations log, `"Needs attention"` where a round filed or
linked an issue or met a Ship defect, `"Change outline"` where `read-pr`'s
`outline_missing` is non-empty or the rounds changed the change's shape, and
`Review` last. Then the title is checked against the recorded `Grade:`, a wrong
type fixed with `update-pr-title`, and `read-pr` reads everything back at once.

## Fallbacks

A reviewer whose `Fallback-for:` names another stands in for it, on the runs
where that primary exits `not reviewed` or is **capped with findings** (ADR
0002); preflight holds it to on-request, since a reviewer that fires on every
push cannot be withheld. Drive every non-fallback reviewer to its exit first,
because a fallback's only input is how its primary exited.

- Primary `not reviewed: <any reason>`, or capped with findings: drive the
  fallback as an ordinary on-request reviewer under its own `Cap:`. Capped with
  findings is `Stop: <primary>: cap` after it spent `Cap:` (rounds == Cap) on a
  last round whose fixes changed the tree, so no host round read them. Its exit
  is its own, and its `## Review` line and merge-summary block both name the
  primary's reason (`fallback for codex: capped with findings`, say), the only
  record of why a second reviewer was paid for.
- Primary `reviewed` otherwise (it stopped on `tree unchanged`, a last round
  whose findings were all declined included, or on `small lane` or `auto-once`,
  or a docs-only fix-only diff ended its loop before `Cap:` was spent): do not
  request it; it exits `not invoked: <primary> reviewed`, so a reader sees the
  reviewer exists.
- Nothing is a fallback for a fallback: a chain is one deep.

## Worked examples

Brand-level detail lives in the host adapters; these show the mapping only.

- **GitHub Copilot as `on-request`**: requested under one login, reviewing under
  another, its check run under a third; the mechanics match each surface to its
  own name. The `copilot_code_review` rule's `review_on_push` fixes the trigger
  (`true` is `on-push`). Out of quota, the request still reads back, then the
  host answers with a quota notice in place of a round: `poll-pr` reads it as
  `refused_by` and the reviewer exits `not reviewed: blocked`, with no second
  request sent. `never-queued` is the cause only where the request does not
  read back.
- **CodeRabbit as `on-push`**: reviews every push; `Resolve:` is its resolve
  comment, posted once every thread carries a reply.
- **Claude Code on GitHub Actions as an `on-request` fallback**: a comment of
  its phrase starts a workflow run posting under `claude[bot]`; a run that
  failed reads `not reviewed: infra-error` with its URL on `reviewer_run`.
- **Native Codex review as an independent `on-request` reviewer**: a comment of
  its phrase, no workflow; `Workflow: native codex` has `poll-pr` read Codex's
  acknowledgement, status comment and clean comment off the PR, so a clean
  round lands as `reviewed`, a reply that is no round reads `not reviewed:
  blocked` with its text, and a completed status on another commit reads `not
  reviewed: stale-head`.
- **Claude Code on Azure Pipelines as `on-push`, `Gating: yes`**: a build
  validation policy that fails the build on a critical finding; a declined
  critical is `reviewed`, cited at the merge gate as the override needed.
