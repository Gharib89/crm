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

- **`on-request`**: `request-review <pr> --reviewer <name>`, then poll with
  `--since` its `requested_at`. The block's `Request:` picks the transport (the
  host's own request call, or a PR comment of the phrase for a comment-triggered
  workflow), and the mechanic reads the request back off the host; one that does
  not read back exits 1, and the reviewer is `not reviewed: never-queued`, with
  no poll. Under the host's own request call, round 1 first polls `--since`
  `open-pr`'s `created_at` at `--timeout 0`: a round the host opened unbidden
  with the PR (a Copilot ruleset, `review_on_push: false`) that has landed is
  round 1, and the request is sent only where none has.
- **`auto-once`**: nothing to request; poll once with `--since` `open-pr`'s
  `created_at`. That one round is all there is.
- **`on-push`**: every push earns a round; poll with no `--since`, which counts
  a round only on the current head.

Per round: start it, `poll-pr <pr> --reviewer <name> [--since <iso>] --brief`
inline, triage what landed, push the fixes once, reply. The next round starts
only while the latest round's fixes changed the tree and `Cap:` has rounds left.
The poll takes its bound from the block, and a window that closed is the answer
rather than a reason to re-poll, except after a `conflict`, which says nothing
about the reviewer: resolve it (phase 8) and poll again.

**`Cap:`** is the budget on rounds ship starts: a number, or `None.` for an
uncapped loop; `auto-once` delivers one round whatever it reads, and under
`on-push` the number ends ship's engagement while the reviewer may carry on. A
round at the cap is dispositioned in full and ends the loop; say whether it was
still landing real findings, which tells the human whether the budget was
right. Small lane: at most one requested round. A lint or flake fix after the
loop ends earns no new request; an on-push reviewer re-reads it on its own, so
disposition that round, which opens no further one.

## Reading a round

`poll-pr` returns the reviewer's rounds, each graded `substantive`, threads with
resolved state, `landed_by` naming the rule that admitted a round, `refused_by`
a quota or rate-limit notice admitted in its place, `reviewer_run` for a comment
transport, `reviewer_blocked` a quota notice the rule did not admit (cite it in
a trailing clause, never as the reason), and `not_reviewed`, the cause the poll
observed where no round was admitted.

- **`--brief` is how a round is read**: one `rounds[]` row per round, its body
  cut to the lead line and finding items, and one row per OPEN thread, the run's
  own replies dropped.
- **A body or `lead` ending `...[truncated]` has not been read.** Re-poll with
  `--brief --full <id>` for that round before triage.
- **Findings live in the body as well as in threads**, and a reviewer's reply to
  one thread posts as a bodiless review row that looks like the next round
  arriving: only `substantive: true` counts.
- **The since rule needs a timed round.** An Azure DevOps vote carries no time,
  so a reviewer whose only signal is a vote reads `silent` under it; its
  threads are stamped and land normally.
- **A comment transport's window is its workflow run**, the one `Workflow:`
  names, held open while the run is going. Run no `update-pr-title` between that
  request and its poll: the run is matched by the PR's title.

## Triage, fix, reply

- **Triage, don't apply**, at the judgment tier, with phase 4's definition and
  its two rejection rails. Check the reviewer's `Instructions:` file when a
  finding contradicts it, and cite it when declining. A valid finding outside
  the issue is an adjacent find.
- **Batch fixes into one push per round.** A fix to a rule goes to every copy of
  that rule in the same batch: grep the phrase before you push and read each hunk
  back.
- **Reply in the thread**: `reply-thread <pr> <thread> --body-file` for every
  `replied: false` thread, `fixed in <sha>` or the decline and its reason. Once
  every thread carries a reply, run the block's `Resolve:` per thread; `Resolve:
  None.` means the findings are answered with `comment-pr`, which also answers a
  body finding with no thread. A finding about the PR body is fixed through the
  writes [pr-body.md](pr-body.md) names.
- **Write each round to the Run file as you disposition it**, one line per
  finding with its disposition, and one per round whose `reviewer_run.denied`
  is numeric, with the run URL: the exit's counts come from them.

## The exit

Each reviewer exits with one of:

- `reviewed`: at least one round landed and was dispositioned. A later round
  that did not land is a trailing clause, not a different exit.
- `not reviewed: <reason>`: no round landed, or one did and its threads could
  not be read (`unreachable`): triage that one off its body and answer it with
  `comment-pr`. The reason is `not_reviewed` off the last poll, or
  `never-queued` off `request-review`'s exit 1, and never one you infer: a human
  saying a reviewer "can't review" is a claim to check against the poll. The
  header of `scripts/poll-pr.sh` lists what each cause means.
- `not invoked: <primary> reviewed`: a fallback whose primary reviewed.

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
mid-findings, the primary's reason on a fallback that ran, a round that did not
land after one that did, or `<N> denied calls (run <url>[, run <url>…])`, N
summing the numeric `reviewer_run.denied` over the rounds, each non-zero round's
run URL listed, and nothing added for a total of 0 or no numeric count.

At exit, from the Run file and never from the body the write replaces, one
`update-pr-body <pr> --section <name> --body-file <path>` per section, each
file carrying its section rebuilt entire: `"Special things to note"` where the
rounds grew the deviations log, `"Needs attention"` where a round filed or
linked an issue or met a Ship defect, and `Review` last, so `read-pr` reads all
three back at once.

## Fallbacks

A reviewer whose `Fallback-for:` names another stands in for it, on the runs
where that primary exits `not reviewed` (ADR 0002); preflight holds it to
on-request, since a reviewer that fires on every push cannot be withheld. Drive
every non-fallback reviewer to its exit first, because a fallback's only input
is how its primary exited.

- Primary `not reviewed: <any reason>`: drive the fallback as an ordinary
  on-request reviewer under its own `Cap:`. Its exit is its own, and its `##
  Review` line and merge-summary block both name the primary's reason, the only
  record of why a second reviewer was paid for.
- Primary `reviewed`: do not request it; it exits `not invoked: <primary>
  reviewed`, so a reader sees the reviewer exists.
- Nothing is a fallback for a fallback: a chain is one deep.

## Worked examples

Brand-level detail lives in the host adapters; these show the mapping only.

- **GitHub Copilot as `on-request`**: requested under one login, reviewing under
  another, its check run under a third; the mechanics match each surface to its
  own name. The `copilot_code_review` rule's `review_on_push` fixes the trigger
  (`true` is `on-push`). Out of quota, the host queues nothing and the request
  does not read back: `not reviewed: never-queued`.
- **CodeRabbit as `on-push`**: reviews every push; `Resolve:` is its resolve
  comment, posted once every thread carries a reply.
- **Claude Code on GitHub Actions as an `on-request` fallback**: a comment of
  its phrase starts a workflow run posting under `claude[bot]`; a run that
  failed reads `not reviewed: infra-error` with its URL on `reviewer_run`.
- **Claude Code on Azure Pipelines as `on-push`, `Gating: yes`**: a build
  validation policy that fails the build on a critical finding; a declined
  critical is `reviewed`, cited at the merge gate as the override needed.
