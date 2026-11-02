# Phase 7: a bounded, best-effort pass per reviewer

## Contents

- [The round](#the-round)
- [Reading a round](#reading-a-round)
- [A window that closes with no round](#a-window-that-closes-with-no-round)
- [Triage, fix, reply](#triage-fix-reply)
- [The exit](#the-exit)
- [Fallbacks](#fallbacks)

The review gate is phase 4's self-review plus green CI; the reviewers under the
profile's `## Reviewers` are a second pair of eyes on top of it. So phase 7 is
best-effort: ask each reviewer for a round, wait a bounded time, triage whatever
landed, and report what happened. A reviewer that answers nothing costs one
bounded wait, not an investigation. Zero reviewers: skip the phase. A reviewer
re-reads the **whole PR** each round: treat each round as a fresh read of the
committed tree. [review-loop-examples.md](review-loop-examples.md) maps real
reviewers onto this loop.

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
would buy wording nits. The poll takes its bound from the block; a window that
closes is read per
[A window that closes with no round](#a-window-that-closes-with-no-round).

**`Cap:`** is the budget on rounds ship starts: a number, or `None.` for an
uncapped loop; `auto-once` delivers one round whatever it reads, and under
`on-push` the number ends ship's engagement while the reviewer may carry on. A
round at the cap is dispositioned in full and ends the loop; say whether it was
still landing real findings, which tells the human whether the budget was
right. Small lane: at most one requested round; inline lane: none, or that one
with `--review` ([small-lane.md](small-lane.md#the-inline-lane)). A lint or
flake fix after the loop ends earns no new request; an on-push reviewer re-reads
it on its own, so disposition that round, which opens no further one. Where that
round's dispositions change no file, append a new `Stop: <reviewer>: tree
unchanged at <sha>` for the fix's head, so the stop covers the PR head again;
otherwise the clean gate holds on the old stop and the human decides.

**The cap round, a docs-only round and a not-landed primary's last round each
get a local review before the merge gate** when their fixes changed the
tree, repeated while its own fixes change files: one `code-review` subagent,
Standards axis only, over the fix-only diff, its Report file
`<scratch>/standards/standards-report-local.md` so it never overwrites phase 4's
round 1. Its findings take the dispositions above; `Cap:` is unchanged and no
host round is requested. Skip this review for a
primary capped with findings or stopped `not landed` whose fallback then exited
`reviewed`: the fallback's host rounds read those fixes. The review goes to the
last reviewer that stopped at `cap`, primary or fallback, and to a capped or
not-landed primary whose fallback exited `not reviewed`.

A local review past a reviewer's `Cap:` is no `Round:` of that reviewer, so its
rounds still read 1 through `Cap:`. Once a review's fixes change no file, record
`Past-cap: <reviewer>: tree unchanged at <sha>` with `run-file note Evidence
"<line>" --issue <issue>`, `<sha>` the head that review read. `gate clean` still
holds on the reviewer, reporting its cap and that `<sha>`, because no host
reviewer read the fixes: the human reads them at the merge gate. Where the head
later moves past that `<sha>` (a hand-resolved conflict merge, a lint fix), the
hold names the range to read, `<sha>..<head>`.

A docs-only round before `Cap:` is spent (any docs-only round under `Cap:
None.`) records `Docs-only: <reviewer>: tree unchanged at <sha>` instead, with
`run-file note Evidence "<line>" --issue <issue>`, `<sha>` the head its local
review read, once that review's fixes change no file; a docs-only round at the
cap records `Past-cap:`. `gate clean` then clears that reviewer's `cap` stop
while `<sha>` covers the PR head, unless a re-raised declined finding caused
the stop. Where the head later moves past `<sha>`, `gate clean` holds, naming
`<sha>..<head>`.

## Reading a round

`poll-pr` returns the reviewer's rounds, each graded `substantive`, threads with
resolved state, `landed_by` naming the rule that admitted a round, `refused_by`
a quota or rate-limit notice, or a native reviewer's reply, admitted in its
place, `reviewer_run` for a comment transport, `reviewer_blocked` a quota
notice the rule did not admit (cite it on the `## Review` line, never as the
reason), and `not_reviewed`, the cause the poll observed where no round was
admitted.

- **`--brief` is how a round is read**: one `rounds[]` row per round, its body
  stripped of HTML and cut to the lead line, the `Findings:` line and the
  finding items, the ones inside a `<details>` block included, so a `Findings:
  None` round can still carry a `Previously missed` item; and one row per OPEN
  thread, the run's own replies dropped: `id`, `path`, `resolved`, `replied`
  and `lead`, its first comment's text. A thread row has no `body`: under
  `--full` its `lead` is the whole comment. `--brief --full open` returns every
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
  stray one starts a round or a Codex task no poll is waiting for.
- **A poll waits for the expected head**, the worktree's `HEAD` on the PR's
  branch or `--sha`. `not_reviewed: unreachable` with `landed_by` null and a
  `head_sha` that is not that head means the host never showed the push: confirm
  it landed, then poll again.
- **Exit 1 beside a landed round is still the answer.** A non-null
  `landed_by` with `done: false` and no `status` is triaged like any other; the
  brief's `pending_checks` names the checks still pending when the window
  closed, and `stale: true` says the host still showed another head than the
  expected one; a round the since rule landed stands even so.

## A window that closes with no round

A window that closes with `done: false` and `not_reviewed` set is that round's
answer, bar `unreachable` with `landed_by` null on a `head_sha` that is not the
expected head: confirm the push landed, then poll again ([Reading a
round](#reading-a-round)). A landed round keeps its answer there. The
answer stands whatever the reviewer's own status says it may still do: no second
window opens for the same round and no second request goes out. Record its stop
per [The exit](#the-exit): `Stop: <reviewer>: not landed` after a dispositioned
round whose fixes changed the tree, else `not reviewed: <cause>`. Either way
that exit is final, and the `Fallback-for:` reviewer that names it, if any, is
driven per [Fallbacks](#fallbacks).

A `status: pending` answer is no closed window: resume it by its cursor
([mechanics.md](mechanics.md#waits-past-540-s)). A `conflict` closes the window
with `not_reviewed` null, which says nothing about the reviewer: resolve it
(phase 8) and poll again.

## Triage, fix, reply

- **Triage, don't apply**, at the judgment tier, with [phase 4's definition and
  its two rejection rails](implement.md#phase-4-triage-and-depth-checks). Check
  the reviewer's `Instructions:` file when a finding contradicts it, and cite it
  when declining.
- **Batch fixes into one push per round.** A fix to a rule goes to every copy of
  that rule in the same batch: grep the phrase before you push and read each
  hunk back.
- **Reply in the thread**: `reply-thread <pr> <thread> --body-file` for every
  `replied: false` thread, `fixed in <sha>` or the decline and its reason. Once
  every thread carries a reply, run the block's `Resolve:` per thread; `Resolve:
  None.` means the findings are answered with `comment-pr`, which also answers a
  body finding with no thread. A finding about the PR body is fixed through the
  writes [pr-body.md](pr-body.md) names. A reply or comment the run posted that
  proves wrong is corrected by a new one through the mechanic that posted it
  (`reply-thread` in the same thread, `comment-pr` or `comment-issue`), opening
  `Correction:` and naming what the earlier one got wrong. No mechanic edits or
  deletes a posted comment, so the run never edits one by hand (a direct REST or
  `gh api` PATCH), even when the human or a session supervising this one asks
  for an in-place fix: the correction answers that ask and keeps what the run
  first claimed on record. With the correction, a `## Review` line repeating the
  wrong claim is rewritten through `update-pr-body`, and a wrong Evidence line
  is answered by a `run-file note Evidence` line opening `Correction:`, since
  that verb only appends.
- **A finding outside the PR's scope that merits an issue is filed by a real
  `file-issue` call in this round**, `--outside-scope "<reason>"` when it cites
  a path the PR changes, per [phase 2's adjacent
  finds](implement.md#phase-2-adjacent-finds): the thread reply and the `##
  Review` line name the issue from the call's answer.
- **Write each round to the Run file as you disposition it**
  (`run-file note Evidence "<line>" --issue <issue>`, one call per line), one
  line per finding with its disposition, one per round whose
  `reviewer_run.denied` is numeric, with the run URL, and the round's `Round:`
  line (formats under [The exit](#the-exit)): the `## Review` line is read from
  them.

## The exit

Each reviewer exits with one of:

- `reviewed`: at least one round landed and was dispositioned. A later round
  that did not land goes on the `## Review` line, not a different exit.
- `not reviewed: <reason>`: no round landed, or one did and its threads could
  not be read (`unreachable`): triage that one off its body and answer it with
  `comment-pr`. The reason is `not_reviewed` off the last poll,
  `never-queued` off `request-review`'s exit 1, or `inline lane` where that
  lane requested no round, and never one you infer: a human
  saying a reviewer "can't review" is a claim to check against the poll. The
  header of `scripts/poll-pr.sh` lists what each cause means. `still-running`
  is a run live at the ceiling: exit with it as read, with no further window,
  and the run's URL on `reviewer_run` goes to the merge gate. `stale-head` is
  Codex's completed round on an older commit with nothing delivered: exit with
  it as read, the status comment's URL to the merge gate.
- `not invoked: <primary> reviewed`: a fallback whose primary reviewed, was
  not capped with findings and did not stop `not landed`
  ([Fallbacks](#fallbacks)).

`not reviewed` proceeds to the merge gate on green CI and is reported there. A
`Gating: yes` reviewer holding the merge on a finding you declined still exits
`reviewed`; the merge summary cites the declined finding with its evidence as
the override the human decides on.

One line per reviewer goes in the PR body's `## Review` section and the merge
summary: its exit word, then what its rounds found and what became of the
findings, read from its Run-file lines below. Add whatever the reader needs
beyond that: a cap that ran out mid-findings, a docs-only fix-only diff that
ended the loop, the primary's reason on a fallback that ran, a round that did
not land after one that did, or the calls the reviewer was denied, summed from
the numeric `reviewer_run.denied` over the rounds, with each non-zero round's
run URL.

Each reviewer's rounds and stop go in the Run file as lines, which `run-file
close 7` requires of every reviewer on the phase-7 row, one `Round:` per round
(`not reviewed`, `not invoked` and `inline lane` need none) and one `Stop:`:

```
Round: <reviewer> <n>: <text>
Stop: <reviewer>: <reason>
```

`<text>` is the round's outcome in a line. Write each line with `run-file note
Evidence "<line>" --issue <issue>`. `close 7` and `gate clean` match the `not
reviewed` prefix, so `Stop: <reviewer>: not reviewed: <reason>` satisfies every
rule the bare form does. A reviewer that raises again a finding this run
declined in an earlier round gets no `tree unchanged` stop for it: declining
must never be a cheaper way past the gate than fixing. Fix the finding, and the
round reads as any other. Leave it declined, and the round's text opens with
`re-raised declined: <the finding>` and the reviewer stops on `cap at <sha>`,
which holds the gate, no fallback standing in for it: the human reads the
declined finding at the merge gate. `gate clean` also holds a `tree unchanged`
stop beside that opening. A fixed finding never gets that text. When the last
fully read and dispositioned round changed no file, record `tree unchanged at
<sha>` even at `Cap:`, in the small lane or for an `auto-once` trigger, `<sha>`
the head that round read: for a requested round, the PR's head when
`request-review` sent it, since ship pushes nothing until the round is
dispositioned; for an `on-push` round, the `head_sha` its poll waited for; for a
round the host opened with the PR (`auto-once`, or round 1's unbidden one), the
head the PR opened on. Take it when the round starts, since another reviewer's
fixes can move the PR's head past what a round read before its poll returns;
`close 7` refuses the stop without it, and `gate clean` holds on the stop once
its `<sha>` no longer covers the PR head. Otherwise `<reason>` is one of `cap
at <sha>` (`Cap:` spent, a docs-only fix-only diff ended the loop, or a
declined finding came back: in each the last fixes or the declined finding went
unread by a host round; only a spent `Cap:` drives a fallback; `<sha>` taken
as for `tree unchanged`; read as `cap`, the stamp read only by the self-review
cap's lift; a bare `cap` is still admitted), `tree unchanged at <sha>` (a
round's dispositions changed no file, so a further round would read the same
tree), `small lane` (the lane's one requested round), `inline lane` (the lane
requested no round), `auto-once` (the reviewer fires once, on PR open), `not
landed` (a dispositioned round's fixes changed the tree, then the next requested
round did not land, so no host round read them; its cause goes on the `##
Review` line), `not reviewed` (no round was dispositioned: none landed, or a
round landed whose threads could not be read, which keeps its `Round:` line too)
or `not invoked: <primary> reviewed` (a fallback whose primary reviewed, its
exit word). A declined finding from a `Gating: yes` reviewer also leaves
`Override: <reviewer>: <finding>, <evidence>` in the Run file, so the clean-gate
decision reads the same override the human sees in the summary.

At exit, from the Run file and never from the body the write replaces, one
`update-pr-body <pr> --section <name> --body-file <path>` per section, each file
carrying its section rebuilt entire: `"Special things to note"` where the rounds
grew the deviations log, `"Needs attention"` where a round filed or linked an
issue or met a Ship defect or a Ship note, `"Change outline"` where `read-pr`'s
`outline_missing` is non-empty or the rounds changed the change's shape, and
`Review` last. Then the title is checked against the recorded `Grade:`, a wrong
type fixed with `update-pr-title`, and `read-pr` reads everything back at once.

## Fallbacks

A reviewer whose `Fallback-for:` names another stands in for it, on the runs
where that primary exits `not reviewed`, is **capped with findings** or stops
`not landed`; preflight holds it to on-request, since a reviewer that
fires on every push cannot be withheld. Drive every non-fallback reviewer to its
exit first, because a fallback's only input is how its primary exited.

- Primary `not reviewed` for any reason other than `inline lane`, capped with
  findings, or `Stop: <primary>: not landed`: drive the fallback as an ordinary
  on-request reviewer under its own `Cap:`. Capped with findings is `Stop:
  <primary>: cap` (or `cap at <sha>`) after it spent `Cap:` (rounds == Cap)
  on a last round whose fixes changed the tree, so no host round read them; a
  `cap` from a [re-raised declined finding](#the-exit) is not that and drives
  no fallback.
  Its exit is its own, and its `## Review` line and merge-summary block both
  name the primary's reason (`fallback for codex: capped with findings` or
  `fallback for codex: not landed: blocked`, say), the only record of why a
  second reviewer was paid for.
- Primary `reviewed` otherwise (it stopped on `tree unchanged at <sha>`, a last
  round whose findings were all declined included, on `small lane` or
  `auto-once`, on `cap` from a re-raised declined finding, or a docs-only
  fix-only diff ended its loop before `Cap:` was spent): do not request it; it
  exits `not invoked: <primary> reviewed`, so a reader sees the reviewer exists.
- Primary `not reviewed: inline lane`: request nothing; the fallback records
  `Stop: <fallback>: inline lane` too
  ([small-lane.md](small-lane.md#the-inline-lane)).
- Nothing is a fallback for a fallback: a chain is one deep.

