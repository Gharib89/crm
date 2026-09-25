# Phase 7: driving every reviewer to convergence

## Contents

- [Shared mechanics](#shared-mechanics)
- [By trigger](#by-trigger)
- [Fallbacks: the reviewer driven only when another one failed](#fallbacks-the-reviewer-driven-only-when-another-one-failed)
- [Degraded exits: fixed vocabulary, per reviewer](#degraded-exits-fixed-vocabulary-per-reviewer)
- [Gating reviewer with a declined finding](#gating-reviewer-with-a-declined-finding)
- [Worked examples](#worked-examples)

The profile's `## Reviewers` lists zero or more reviewers. Each has the
login(s) it posts under, a `Trigger:`, `Gating:`, a `Cap:`, a `Fallback-for:`,
an optional `Instructions:` file, and per trigger: `Request:` and the
`Workflow:` qualifying it (on-request), `Resolve:` (on-push and on-request;
auto-once converges on dispositioned threads and reads `None.`). The **trigger
fixes the loop and convergence**; the bot's brand fixes nothing. Preflight has
already parsed these blocks and refused the eight malformed shapes, and asked
the host whether a Copilot reviewer's `Trigger:` matches the ruleset driving
it, so what reaches this phase is a list you can drive. Zero reviewers: skip
this phase; the review gate is phase 4's self-review plus green CI (SKILL.md),
which reviewer rounds sit on top of.

A reviewer re-reads the **whole PR** each round: treat every round's output as
a fresh read of the committed tree, not a conversation.

## Shared mechanics

- **Poll with `poll-pr <pr> --reviewer <name>`**, `<name>` being the
  reviewer's `### <name>` heading ([mechanics.md](mechanics.md)), inline,
  bounded, foreground. It returns one JSON: head sha, mergeable, checks, the
  reviewer's rounds, each graded `substantive` by Ship rather than the host,
  threads with resolved state, `reviewer_blocked`, `landed_by` naming the rule
  that admitted the round, and
  `refused_by` naming the rule that admitted a refusal in its place, and
  `never_queued`, under `--free-round` alone, true where the host has no round
  queued for the reviewer since `--since`, with `degraded` naming the exit
  where `--review-on-push false` says that round was promised. `done: false` means the window closed first: re-run to extend it,
  in the foreground again, **unless `refused_by` is non-null or `never_queued`
  is true**. A non-null `refused_by` is the reviewer's quota or
  rate-limit notice answering this request, the window closed on it at once,
  and re-polling or re-requesting waits on a round that is not coming: the
  reviewer exits `degraded: blocked` there and then. A true `never_queued`
  closed the free-round poll on the host's own record of requests, and the
  loop proceeds to its first request, unless `degraded` is non-null, which is
  the reviewer's exit with no request. The poll is the landing
  signal only; before triage, read the round's review body and its threads
  from the same payload. The body sits on the row the reviewer's landing rule
  admitted: `reviews.on_head[].body` under the head rule, `reviews.all[].body`
  under the since rule, where the round may sit on an older head. A round
  whose findings live in the body rather than in threads is invisible from the
  thread list alone, and `infra-error` is a judgment about the body.
- **`--brief` projects that same poll** down to what this loop acts on: head,
  mergeable, `landed_by`, `refused_by`, `never_queued`, `degraded`, `reviewer_blocked`, one `rounds[]` row per round (id, `submitted_at`,
  `substantive`, and the body cut to its lead line and finding items) and one
  row per OPEN thread (id, `path`, `lead`, `resolved`, `replied`). Rounds come
  from the list the landing rule admitted and hold only the awaited reviewer's
  rows, the run's own replies dropped, so a round count is the reviewer's
  rounds and not ours. A thread row carries
  the file its finding sits on and the line that states it, so one thread is
  dispositioned off the brief without a second poll, unless that `lead` comes
  back marked `...[truncated]`: `--full` names rounds alone, and the comment is
  read whole from the full shape's `threads[]`.
- **A body ending `...[truncated]` has not been read.** Rounds are clipped past
  2000 characters so one poll cannot flood the window, and a reviewer that opens
  with a preamble (an overview, a per-file table) pushes its findings past that
  cap. Re-run the poll with `--brief --full <id>` for that row and it alone
  comes back whole, and that read comes before the triage: dispositioning a
  clipped round is converging on the findings you happened to see. `--full` is
  the brief's own flag and is refused without it: the full shape keeps its rounds
  under `reviews` and has no `rounds[]` for a run to read the lifted body off.
- **The trigger picks the landing rule, and the poll derives it from the
  block.** Under the **head** rule a round counts only on the current head:
  right for `on-push`, where every push earns a fresh review. Under the
  **since** rule (`--since <iso>`) a round counts wherever it sits, if it was
  submitted at or after that time: right for `on-request` and `auto-once`, which
  deliver one round per request and post it once, so a push between the request
  and the review leaves the round keyed to the older head, and the head rule
  then waits out the whole window for a review that has already landed
  elsewhere. The instant is the one thing the poll cannot derive: pass
  `request-review`'s `requested_at` or `open-pr`'s `created_at` as `--since`
  for every reviewer but an on-push one, which takes none. The
  since rule needs a timed round, so a reviewer whose only signal is an Azure
  DevOps vote, which the API leaves unstamped, exits `degraded: silent` under
  it; its threads, which carry anything actionable, are stamped and land
  normally.
- **A comment-transport reviewer's window is its workflow run.** Where the
  profile's `Request:` reads `comment <phrase>`, the round comes from a workflow
  that comment starts, and such a run is attached to the default branch's SHA:
  it lands no check on the PR head, so the run itself is the evidence that the
  reviewer is working, and reading it is what tells a round still being written
  from one that will not come. The poll awaits the run of the file that
  reviewer's block names on its `Workflow:` line, keyed by `--since`, derived
  from `--reviewer`. `--timeout` is then the floor of the window
  rather than its end: a run that has not finished keeps the poll going, to the
  ceiling `poll-pr --help` states, and one that concluded successfully buys one
  more interval for the row to appear. A run that concluded any other way closes
  the window there, with its URL. The
  run comes back on `reviewer_run`, in the full shape and in `--brief` alike,
  and it is what separates three of the degraded reasons below from each other.
  Where it reads `"unavailable"` the host refused the read itself, so it is
  evidence about the host rather than about the reviewer: that exit is
  `unreachable`.
- **A round is a review with a body, or a bodiless verdict.** A reviewer's
  reply to one thread posts as a review row of its own (current head, a
  bodiless `comment`), so answering round N manufactures rows that look like
  round N+1 arriving. Only `substantive: true` counts: a body that is not
  wholly a quota or rate-limit notice, or a bodiless `approved` or `changes`
  verdict. Hold any hand check to that bar.
- **Triage, don't apply**, at the judgment tier, with phase 4's definition:
  harden rather than rip out capability, verify nits against the pinned
  versions, reject known non-issues with a one-line reason. The two rejection
  rails apply: check repo-existence claims against `origin/HEAD`, and judge a
  claim separately from the evidence it cites. Check the reviewer's
  `Instructions:` file when a finding contradicts it (a host may truncate that
  file; cite it when declining). A valid finding outside the issue is an
  adjacent find, and phase 2's three dispositions decide it, inline conditions
  and `file-issue`'s candidate check included. The thread then carries that
  disposition with the link. That is also the honest answer to a gating
  reviewer.
- **Batch fixes into one push per round**, then answer every `replied: false`
  thread with `reply-thread <pr> <thread> --body-file`, one call per thread with
  the id `poll-pr` returns (`fixed in <sha>`, or the decline and its reason).
  `poll-pr` returns the PR's whole thread set, not the round's, and `replied` is
  true once this identity has answered in the thread: skip those, one finding,
  one disposition. The disposition belongs in the thread the reviewer opened,
  which is where the reviewer's next pass and a human reading the round both
  look; a round-level `comment-pr` logs the round, and the disposition itself
  stays in the thread. **A fix to a rule is propagated to every copy of that
  rule inside the same batch**: grep the phrase before you push, and read each
  fix's hunk back out of the file while you are there, because the reviewer
  re-reads the whole PR and a copy the fix missed, or a fix applied by half, is
  another round spent on a finding you already agreed with. `resolve-thread`
  posts no body and runs per thread only once every thread carries its reply.
  Every push spends review quota and CI minutes, and an on-push reviewer's
  round.
- **Cap** is the profile's `Cap:`, the bound on one reviewer's rounds: a number,
  or `None.` for an uncapped loop. On-request it is required with no default,
  where a round costs a request; on-push it is a number or `None.`, where a
  round costs a push, a wait on the new head, and a triage and a reply per
  thread; `auto-once` delivers one round and reads `None.`. It is a **budget**
  for the rounds ship drives, which is every round only where ship starts them:
  a reviewer the host re-runs on its own keeps posting past the number, so under
  on-push the budget ends ship's engagement while the reviewer carries on. Where
  a repo needs the number to bind, pick a trigger ship starts. A round at the
  cap that is still substantive means the budget ran out while the reviewer was
  still finding things: disposition it in full (push its batch, reply to every
  thread, resolve where the trigger resolves), exit `degraded: cap-hit` without
  waiting for another round, and say in that reviewer's block whether that last
  round was still landing real findings, which is what tells the human at the
  merge gate whether the budget was the right one.
- **Per-reviewer accountability.** Each reviewer gets its own block in the merge
  summary and its own line in the PR body's `## Review` section
  (`update-pr-body` at phase-7 exit), in one fixed shape:

  ```
  - <reviewer>: <exit word>, <n> rounds, <raised> findings: <accepted> accepted, <declined> declined, <filed> filed
  - <fallback>: not invoked: <primary> converged
  ```

  The exit word is `converged`, `converged, override needed`, `degraded:
  <reason>`, or, for a fallback whose primary converged, `not invoked:
  <primary> converged`, which takes the second form and states no counts,
  having none. A trailing clause is added only where the reader must know
  something the counts do not say (a cap that ran out mid-findings, the
  primary's degraded reason on a fallback that ran, the tool calls its rounds
  were refused). The denied-calls clause reads `<N> denied calls (run <url>[,
  run <url>…])`: N is the sum of the numeric `reviewer_run.denied` values over
  that reviewer's rounds, a null round adding nothing, followed by the run URL
  of each round whose count was non-zero, as in `- claude: converged, 3 rounds,
  4 findings: 3 accepted, 1 declined, 0 filed, 3 denied calls (run <url>, run
  <url>)`. A total of 0, or no numeric count at all (`denied` null from a live
  run or a failed read, or no `reviewer_run` at all), adds nothing, and a count
  never changes the exit word. The per-finding outcomes
  are the merge summary's block, which this line points at rather than repeats.
  **Write the round to the Run file as you disposition it**, one line per
  finding with its disposition, and one per round whose `reviewer_run.denied`
  is numeric, carrying it and the run URL, the way phase 2 writes a deviation:
  no command reproduces a round count, a finding outcome or a past round's run
  URL, so the file is the only thing
  a compaction leaves standing between here and the counts line at exit.
  [merge-gate.md](merge-gate.md) reads it back and does not write it.
- **The exit rewrites the two sections the rounds grew.** A round can force the
  same departure from the issue, brief or plan that phase 2 logs, and it can
  file an issue; an in-scope fix is no more a deviation here than anywhere
  else. So where the deviations log changed since phase 6, refold it by
  [pr-body.md](pr-body.md)'s rule and write it back with `update-pr-body <pr>
  --section "Special things to note" --body-file <path>`; where a round filed
  or linked an issue, or met a Ship defect, write `update-pr-body <pr>
  --section "Needs attention" --body-file <path>`. A `--section` write
  replaces the **whole** section, so each `--body-file` is that section
  rebuilt entire, from the Run file and never from the body the write is
  about to replace: reading the body back inherits whatever an earlier write
  got wrong and loses the per-entry detail the file holds. `Special things to
  note` is rebuilt from the phase-6 warnings, migrations and constraints plus
  the whole deviations log refolded; `Needs attention` from every issue the
  run has filed or linked and every Ship defect it has met, this round's
  included. Both go before the `Review` write, which stays last so `read-pr`
  reads all of them back at once. Skip them and the PR body ships the phase-6
  text while the merge summary carries the current one, and the human reads
  the two against each other.

**Every `update-pr-body --section <name> --body-file <path>` above takes the
section's CONTENT**, `Review`, `Special things to note` and `Needs attention`
alike: the mechanic writes the `## <name>` line itself, and a file that carries
it too leaves the heading twice over inside one section, which the write
collapses and which no other mechanic repairs. `--preamble` takes the whole
preamble the same way, which is how a reviewer's accepted objection to a
body's closing line is
answered by a write; an objection to the Change outline is a `--section
"Change outline"` write, that fence sitting in a section of its own.

## By trigger

### `auto-once`

Fires once on PR creation; nothing to request, and the one round it fires is
**all there is**. Wait for it to land under the **since** rule, with `open-pr`'s
`created_at`. If a round arrives before you poll, that is the round. Triage it
once, push the fixes, `reply-thread` on every `replied: false` thread.
**Converged** when every thread is dispositioned. A later push does not bring it
back; a lint or flake fix after convergence needs nothing from it.

### `on-push`

Re-reviews every push. This is the trigger the `Cap:` budget does not bind: the
host starts the rounds, so the number ends ship's engagement and the reviewer
keeps posting. After each push, wait for a review **landed on the current
head**, the **head** rule (no `--since`); a round on the head with nothing
actionable in it is the quiet this waits for, and no round on the head at all
keeps the poll running. Triage, batch-fix, push, `reply-thread` on every
`replied: false` thread. Once **every** thread carries a disposition, and only
then, use the reviewer's `Resolve:` mechanism (`resolve-thread`, or the comment
the profile names) to resolve them. **Converged** when a review has landed on
the current head with nothing actionable and every thread is dispositioned and
resolved. A fix pushed after convergence gets re-read on its own: wait for quiet
on the new head again. When `poll-pr` reports `threads: unavailable`, this
reviewer's exit is `degraded: unreachable` and the run proceeds; the other
triggers read reviews and comments, which stay readable.

### `on-request`

Nothing arrives until asked, with one exception the loop below opens on: a
**free round** the host delivers unbidden when the PR is created.
`request-review <pr> --reviewer <name>` issues the request and **reads it
back** from the host's own record (the mechanic knows that the login you
request and the login you read back can differ, and that an empty
requested-reviewers list proves nothing). The block's `Request:` picks the
transport: the host's own request call for a reviewer the host can add to the
PR, and a PR comment carrying the phrase where `Request:` reads `comment
<phrase>`, for a reviewer that is a comment-triggered workflow. That second
transport posts the phrase, reads the posted comment back, and reports the
host's creation time for it; there is no requested-reviewers list to read,
because the host has no reviewer to add. Either way the `requested_at` it
hands back is what `--since` takes. One request yields one round; the reviewer
does not re-review on push, so each round after the first is a new request
against the corrected tree.

A **free round** is one the host delivers without a request: a Copilot ruleset
with `review_on_push: false` still opens one when the PR does. Before the
run's **first** request to an on-request reviewer whose `Request:` is the
host's call, poll once for it, under the since rule with `open-pr`'s
`created_at` and `--free-round`, with no `--timeout`: the reviewer's default is
sized for its transport. Add `--review-on-push false` where preflight's
`reviewers[]` row for that reviewer read `false`, which the Run file recorded
at phase 0. A block whose `Request:` is `comment <phrase>` skips this poll and
requests directly: a comment-triggered workflow opens no round unbidden. No
poll after a request passes `--free-round`. A round already there **is** round
1 and counts against `Cap:`; nothing there and the loop proceeds to its first
request as written. A poll that closed on `never_queued` ends the window once
the settle `poll-pr --help` states has passed with no request for the reviewer
on the host's record. Where it also answers `degraded: "never-queued"`, the
ruleset promised the round the host never queued, which is a quota-out Copilot:
that is the reviewer's exit, `degraded: never-queued`, with no request sent.
Where `degraded` is null, without preflight's `false`, the loop proceeds to its
first request, which exits `never-queued` if the quota is out. A **refusal**
there (`refused_by` non-null: Copilot posts its quota notice as the opening
review) ends this reviewer before any request: its exit is `degraded: blocked`, and no request is issued, because
the quota the free round was refused on is the one every request draws from. A
reviewer that gets no free round pays that one poll, where skipping it spends
a round of a small cap re-asking for a review that had already landed.

Loop: **triage whatever round you are holding first**, then request the next
one. A free round the poll above found is a round in hand, so it is triaged,
batch-fixed, pushed, replied to on every `replied: false` thread and resolved
before any request is issued; requesting on top of it spends round 2 on a tree
the reviewer has not seen and burns the budget the free round just saved. With
nothing in hand: request, poll under the **since** rule with `request-review`'s
`requested_at`, triage, batch-fix, push, `reply-thread` on
every `replied: false` thread, and round the loop. Run no `update-pr-title`
between a comment-transport request and its poll: the run read matches the
PR's title, so a rewritten one matches nothing and reads as `never-queued`. A
poll that comes back with `refused_by` non-null ends the loop at `degraded: blocked`: no re-poll, no
further request, whatever `Cap:` has left. A round that opened threads
takes the reviewer's `Resolve:` once every one of them carries a
reply, exactly as an on-push round does; `Resolve: None.` means the reviewer
opens none and the findings are answered on the review with `comment-pr`.
**Converged** when the latest round has nothing actionable and every thread from
all rounds is dispositioned, and resolved where the reviewer resolves.
Small lane: at most one *requested* round, a free round with nothing
actionable ending the loop `converged` with no request. A lint or flake fix
after convergence earns no new request.

## Fallbacks: the reviewer driven only when another one failed

A reviewer whose `Fallback-for:` names another reviewer stands in for it, and
only on the runs where that primary exits degraded: a fallback is on-request and
conditional, so ship requests and drives it only once the primary is degraded.
Preflight enforces the trigger, because a reviewer that fires on every push
cannot be withheld.

**Drive every non-fallback reviewer to its exit first**, then the fallbacks,
because a fallback's only input is how its primary exited.

- The primary exited `degraded: <any reason>`: request the fallback **once**,
  then drive it as an ordinary on-request reviewer under its own `Cap:`, by
  the section above: a fallback reached through a comment transport skips the
  free-round poll and requests directly, and that transport is what makes every
  poll of it await its workflow run. Which degraded reason the primary hit changes
  nothing here; the human wanted a review on the PR and the reason is a
  footnote. Its exit is an ordinary one, `converged` or `degraded: <reason>`
  of its own.
- The primary exited `converged` or `converged, override needed`: **do not
  request it**. Its exit is `not invoked: <primary> converged`, which is not a
  degraded reason and not a stop; it is reported so a reader sees the reviewer
  exists rather than reading its absence as one nobody configured.
- A fallback's own degraded exit triggers nothing further. Nothing is a fallback
  for a fallback, and a chain is one deep.

Both exits are reported in both places: the PR body's `## Review` line and the
merge summary's block for that reviewer. Where the fallback ran, both name the
primary's degraded reason, which is the only record of why a second reviewer was
paid for.

## Degraded exits: fixed vocabulary, per reviewer

Degraded means the reviewer did not finish its job; it proceeds to the merge
gate on green CI and is reported there rather than handed back, in either lane.
The human reads the reason and decides.

| Reason | Detection |
|---|---|
| `never-queued` | on-request, two detections. The free-round poll answers `degraded: "never-queued"`: preflight read `review_on_push: false`, so the ruleset promised a round the host never queued, and no request is sent. Otherwise, no request event on the host's record after one retry. A quota-out Copilot reads here rather than as `blocked`: the host queues it nothing and shows the quota only as a PR-page banner, so there is no notice for `refused_by` to admit. Under a comment transport there is no second request to make, because the one post is verified as it is made and a second would draw a second round: `reviewer_run.status: "none"` is the answer on the first poll, the request landed and the host started no run for it. A run whose conclusion is `skipped` says the same thing from the other side: the workflow's own `if` declined the comment, so nothing ran for the request. Do not spend a second poll window on it. |
| `blocked` | a quota or rate-limit notice from the reviewer answering the request. The landing rule admits it as `refused_by` while `landed_by` stays null, because a notice-only row is not `substantive`: under the since rule a review or a PR comment at or after `--since`, under the head rule only a review on the head, since a comment is tied to no commit; a comment-only notice there shows as `reviewer_blocked` alone, and the window runs out before the exit is taken. Admitted, the poll closes the window on it at once, and the reviewer exits here without another poll or request: the refusal is the answer, and the quota does not come back inside a run. Either way the fallback, where one is configured, is what runs next. |
| `silent` | queued, no round admitted by the reviewer's landing rule within the bounded wait: under the head rule none on the current head, under the since rule none submitted after the timestamp on any head. Under a comment transport it takes the run read as well: `reviewer_run` concluded `success` and no round followed it. A run that has not finished is not silence, and the poll holds the window open on it to its ceiling. |
| `infra-error` | `reviewer_run.status` is `completed` with any conclusion but `success` or `skipped`, `cancelled` and `timed_out` among them: the run ended before it could post, and its `url` is where the human reads why. A run still unfinished in the returned `reviewer_run` says the same thing: it outlived the ceiling `poll-pr --help` states without delivering, and its `url` is where that is read. Also a review whose body is only an error notice with zero comments, twice, and the notice is not a quota or rate-limit one: that is `blocked`, which `reviewer_blocked` names for you. Not feedback. |
| `cap-hit` | `Cap:` reached with the latest round still substantive, that round dispositioned. The budget ran out; whether the reviewer had run out of findings is a separate question the block answers. |
| `unreachable` | no host path to the reviewer from this environment, thread state could not be read (`threads: unavailable`, which also leaves `reply-thread` with no id to answer), or, under a comment transport, `reviewer_run: "unavailable"`: the host refused the run read, so nothing here is evidence about the reviewer. |

## Gating reviewer with a declined finding

A `Gating: yes` reviewer can hold the merge on a finding you decline. Every
thread dispositioned, the declined finding cited with evidence, build still
red: the exit is `converged, override needed`. Not degraded, because the
reviewer ran; the human overrides the policy or accepts the finding at the
merge gate.

## Worked examples

Brand-level detail lives in the host adapters; these show the mapping only.

- **GitHub Copilot as `on-request`**: request per round, cap from the
  profile. Two identities behind one reviewer: the request names one login,
  the review posts under another, and the check run a third; the mechanics
  match each surface to its own name. The `copilot_code_review` rule's
  `review_on_push` fixes the trigger: `true` is `on-push`, and `false` is
  `auto-once` where the opening round is the only one wanted, or `on-request`
  where that same free round becomes the loop's round 1 and the cap buys the
  rest. Preflight reads the rule and refuses a `Trigger:` that disagrees with
  it, so the profile cannot drift from the setting that drives it.
- **CodeRabbit as `on-push`**: reviews every push; `Resolve:` is its resolve
  comment, posted once after every thread carries a reply.
- **Claude Code on GitHub Actions as `on-push`**: reviews every push through a
  workflow, posting under `claude[bot]`, the app its OAuth token authenticates,
  rather than the Actions identity the workflow otherwise runs as. It attaches its
  per-file findings as inline threads on the review, so `Resolve:` is
  `resolve-thread`; a finding that names no file stays on the review body and is
  answered with `comment-pr`.
- **Claude Code on Azure Pipelines as `on-push`, `Gating: yes`**: a build
  validation policy that fails the build on a critical finding; threads are
  Azure DevOps PR threads with `fixed | closed` status; a declined critical is
  `converged, override needed`.
