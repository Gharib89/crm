#!/usr/bin/env bash
# ship phases 7 and 8: a poll of a PR's head, checks, reviews and threads, then
# ONE JSON summary. No call holds the tool past SHIP_CALL_CAP seconds (540, under
# the harness's 600 s limit on one tool call): a window still open at the cap
# answers the snapshot it has with `status: "pending"` and a `cursor`, exit 1,
# and the caller resumes the SAME window by passing the cursor back.
#
#   poll-pr <pr> [--reviewer <name> [--since <iso>]] [--brief, or --brief --full <id>[,<id>]|open]
#           [--sha <sha>] [--timeout <s>] [--interval <s>] [--cursor <c>]
#
# `--cursor <c>` resumes the window a pending answer carried: its deadline, its
# landing rule's since instant, its start (so `waited_s` counts from the first
# call) and the head it waits for where --sha fixed one. The caller passes <pr>
# and its --reviewer, --brief and --full flags again, and no --since or
# --timeout, which the cursor already fixed: both are refused with it, and so is
# a cursor that does not read.
#
# The expected head is `--sha`, else the local HEAD of a checkout of the PR's head
# branch, read afresh on every pass so a push made while the poll runs moves it:
# while the host shows another head the poll reads nothing on it, so a round on
# the previous head never counts as one on this head, and a window that closes
# first answers `done: false` carrying the host's head_sha.
#
# `--reviewer <name>` names the `### <name>` block under the profile's
# `## Reviewers`, read before any host is reached; `ship_reviewer_derive` answers
# the login, the landing rule, the run to await and the default `--timeout`, which
# a `--timeout` given overrides. The derivation comes back on `reviewer`: {name,
# login, rule, await_run, timeout}, null without the flag.
#
# done when the PR is in conflict (merge-ref checks stay unstarted, so waiting is
# pointless), or every check on the head has completed and, with --reviewer, a
# SUBSTANTIVE review by that reviewer's login has landed. `substantive` is the landing
# signal: Ship's grade of each row, applied here above the host
# (`SHIP_SUBSTANTIVE` in _lib.sh defines it).
#
# A host read that fails with no HTTP status (a dropped connection, a TLS
# timeout) is no answer yet: the poll keeps its window and reads again, and ends
# at exit 2 naming the read on the third such iteration in a row, or once the
# window's deadline has passed while the reads still fail. An iteration whose reads
# all answered resets the count. A read the host refused WITH a status is a real
# refusal and is exit 2 at once. The reads that degrade (`threads`,
# `reviewer_blocked`, `reviewer_run`) keep degrading.
#
# Two landing rules, derived from the reviewer's profile `Trigger:`; `landed_by`
# names the one that matched.
#   head (on-push): the review sits on the current head. Every push
#     earns a new review, so a review on an older commit does not count.
#   since (on-request and auto-once, from --since): the review was submitted at or
#     after <iso>, on ANY head. Such a reviewer delivers one round per request
#     and posts it once, so a push between the request and the review leaves
#     the round keyed to the older head, where the head rule would wait out the
#     whole window. Pass `request-review`'s `requested_at` or `open-pr`'s
#     `created_at`; with `--reviewer` and no `--since` the poll asks the host
#     (`host_pr_requested_at`) for the latest instant that reviewer was requested
#     and uses it, and is refused only where the host has none. Matching by time
#     rather than by requesting login is deliberate: the login a request is made
#     under and the login the host records can differ. A row with a null
#     submitted_at is host state rather than a timed event (an Azure DevOps
#     vote, which the API leaves unstamped): it cannot answer a question about
#     time, so it satisfies the head rule only.
#     Counting it here would land round 2 instantly off round 1's stale vote.
#
# Each review row carries the round's own `body` and the `id` the host knows it
# by, clipped past 2000 characters and marked "...[truncated]" there: phase 7
# triages from the body, and a round whose findings are in it rather than in
# threads is invisible without it. `--brief --full` names the round or thread
# ids to return whole; every other row stays clipped, and an id matching no row
# changes nothing. `--full open` returns every open thread's lead and every
# round of the rule's list whole.
# `threads[]` rows carry the thread's first comment, which `reply-thread` answers,
# and `replied`, true once this identity has answered in that thread.
#
# Under a comment transport the poll awaits the workflow run named by the
# block's `Workflow:`, the run that reviewer's round comes from, before the
# window may report that reviewer silent.
# Such a run is attached to the default branch's SHA, so `checks` cannot see it,
# and the one signal left was the absence of a review. Awaiting it, the window
# is the RUN's lifetime and `--timeout` only its floor: a run that has not
# finished keeps the poll going, and the ceiling the usage line states is the
# bound on what the run may add, so a `--timeout` past it is the caller's own
# window, which the run then extends by nothing. The ceiling counts from the
# request the poll reads (`--since`, the cursor's, or the host's), else from the
# window's start, which a fresh call resets. So a fresh call with a since, on a
# run stuck past it, grades `still-running` once its `--timeout` is spent,
# rather than opening a new ceiling (#952). A run that concluded successfully buys one more
# interval for the row to appear; one that concluded any other way closes the
# window there and then, whatever `--timeout` had left, carrying its URL. A run
# still live when the ceiling closes is reported as it stands, status and URL:
# a run that outlived the ceiling delivered nothing either. The run is reported on
# `reviewer_run`: null where no run is awaited, `{status, conclusion, url, denied}`
# otherwise, with status `none` and the other three null where no run was created
# at all, and the string
# "unavailable" where the host could not answer the read, the way `threads`
# reports one it could not read. An unavailable read leaves the window at the
# constant. The run
# read is keyed by `--since`, the request the run should follow. Which event
# starts such a run is the host's word and the adapter's business: this
# mechanic names the workflow file and the instant, and nothing else. `denied`
# is the count of tool calls the round in a `completed` run was refused, read
# once as the poll returns, for that run alone; it is null for a run still live
# at the ceiling, where none was created, and where the host could not read the
# count, which leaves the rest of the run read as it was. The job posts its
# review before it raises the count, so a landed round whose run is still going
# holds the window until the run completes, to the ceiling (#295). The count
# never changes the verdict: the review loop carries it onto the Review line.
#
# A block whose `Workflow:` reads `native codex` has no run: Codex posts its
# round through its own GitHub integration. The poll then reads the round's
# status off the PR (`host_pr_native_activity`, graded by `SHIP_NATIVE_CODEX`),
# which answers on `reviewer_run` in the run's own shape, so the window holds
# while Codex shows the request in progress and closes on the same terms. Two
# conclusions are Ship's own words there: `refused` beside a reply that is no
# round, and `stale-head` for a completed status on another commit with nothing
# delivered. A Completed status whose commit this reader cannot parse is
# "unavailable", a failed read rather than a stale head. Its `url` is Codex's
# status comment, or the reply's where there is none, and `denied` stays null. A clean round, which
# Codex posts as a comment and a 👍 with no review, joins `reviews.all` as a row
# of its own and lands like any round. A reply by the login that is neither the
# status nor a clean round is reported as the blocked notice, its first prose
# line kept: the first non-blank line that is not a whole-line `<!-- ... -->`
# marker.
#
# `reviewer_blocked` is the awaited login's latest quota or rate-limit notice,
# read from its review bodies as well as its PR comments: a reviewer states a
# notice on either surface, and both are read. `refused_by` names the landing
# rule that admitted a notice while no round landed: a review on the head, or a
# review or PR comment at or after `--since`. That notice answers the request, no
# round follows it, and the window closes on it at once with done=false. A
# notice the rule does not admit, an older request's or a comment under the head
# rule, leaves the window to run as before.
#
# `not_reviewed` is the cause this poll observed for the awaited reviewer
# delivering no round, which the review loop reports as `not reviewed: <cause>`
# rather than naming one of its own. Null without --reviewer. Otherwise, first
# match wins:
#   unreachable   the window closed with the host still on another head than the
#                 expected one and no round landed, so the reviewer was never
#                 read on it; a round the since rule landed keeps its null
#   (null)        a conflict closed the window, which says nothing about the
#                 reviewer
#   unreachable   `threads` is "unavailable" (on GitHub, GraphQL and the REST
#                 routes a refusing proxy names both failed), a landed round's
#                 included, since its threads can be neither read nor answered
#   (null)        a round landed
#   unreachable   `reviewer_run` is "unavailable": a read that did not happen is
#                 no evidence about the reviewer
#   blocked       `refused_by` is non-null
#   never-queued  the awaited run's status is `none`, or its conclusion
#                 `skipped`: the request landed and nothing ran for it
#   still-running the awaited run was still live (in progress or queued) when the
#                 ceiling closed the window: it may yet post a round
#   stale-head    a native integration's status names a completed round on a
#                 commit other than the head, and nothing was delivered for it
#   infra-error   the awaited run concluded any other way but `success`
#   silent        the window closed on its bound with nothing admitted
#
# `--brief` projects that same JSON plus the poll's `stale`, from the same
# single fetch, down to what a review loop acts on: head, mergeable,
# `landed_by`, `done` with the `pending_checks` and `stale` that explain a false
# one beside a landed round, one row per reviewer round
# (id, submitted_at, substantive, and the body cut to its lead line, its
# `Findings:` line and its finding items, HTML comments and tags gone, the items
# of a `<details>` block lifted) and one
# row per OPEN thread (id, path, lead, resolved, replied). `--full` names the
# rows that come back whole, so `--brief --full <id>` is the summary with that
# one round verbatim, or that one thread's first comment whole as its `lead`.
# Rounds come from `all[]` under the --since rule and `on_head[]` under the head
# rule, the list that rule lands from. Under `--reviewer` only the awaited
# reviewer's rows are kept, and the run's own rows drop out: a thread reply of
# ours posts as a review row of its own, and a round count that counts it reads
# its own voice as the reviewer's. That drop needs the host identity, so
# `--brief` asks for it up front and exits 2 when the host cannot answer, rather
# than returning a list it cannot promise is the reviewer's alone. The full
# shape stays the default.
#
# stdout: {head_sha, mergeable, checks[], reviews: {on_head[], all[], total},
#          threads, reviewer, reviewer_blocked, reviewer_run, landed_by, refused_by, not_reviewed,
#          done, waited_s}
#   --brief: {head_sha, mergeable, reviewer, landed_by, refused_by, not_reviewed, done,
#             pending_checks[], stale, reviewer_blocked, reviewer_run, rounds[], threads}
#   either shape, at the call cap with the window still open, also carries
#   status: "pending" and cursor: "<opaque>", with done=false and not_reviewed
#   null: the window is open, so no cause is graded yet
# exit: 0 done · 1 window closed first (done=false; `not_reviewed` names why no
#       round landed, and null beside a landed round means a check on the head
#       was still pending, or the host still showed another head than the
#       expected one, which the brief names as `pending_checks` and `stale`;
#       the review loop takes every such exit 1 as the answer), or the call cap
#       reached first (status pending: resume with --cursor) · 2 tooling
set -uo pipefail
source "$(dirname "${BASH_SOURCE[0]}")/_lib.sh" || { printf '{"error":"cannot source _lib.sh"}\n'; exit 2; }
# The hard bound on waiting a run out, written once: the usage line is where a
# run reads it.
ceiling=1800
usage="usage: poll-pr <pr> [--reviewer <name> [--since <iso>, default the latest request of that reviewer on the host], whose workflow run or native status, under a comment transport, holds the window open past --timeout, to ${ceiling}s after that request, or after the window start without one] [--brief, or --brief --full <id>[,<id>]|open to read those rounds or threads whole] [--sha <sha>, the head to wait for, default the local HEAD when on the PR head branch, else none; a window closing first is done: false] [--timeout <s>] [--interval <s>] [--cursor <c>, the cursor a status: pending answer carried, to resume its window; no --since or --timeout; a call holds the tool at most ${SHIP_CALL_CAP}s]"
ship_help "$usage" "$@"
ship_args "$usage" pr "$@"
# shellcheck source=poll-pr.d/args.sh
source "$(dirname "${BASH_SOURCE[0]}")/poll-pr.d/args.sh"
ship_load_host
# shellcheck source=poll-pr.d/loop.sh
source "$(dirname "${BASH_SOURCE[0]}")/poll-pr.d/loop.sh"
