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
# window, which the run then extends by nothing. A run that concluded successfully buys one more
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
# status nor a clean round is reported as the blocked notice, its first line
# kept.
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
# `--brief` projects that same JSON, from the same single fetch, down to what a
# review loop acts on: head, mergeable, `landed_by`, one row per reviewer round
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
#   --brief: {head_sha, mergeable, reviewer, landed_by, refused_by, not_reviewed, reviewer_blocked,
#             reviewer_run, rounds[], threads}
#   either shape, at the call cap with the window still open, also carries
#   status: "pending" and cursor: "<opaque>", with done=false and not_reviewed
#   null: the window is open, so no cause is graded yet
# exit: 0 done · 1 window closed first (done=false; `not_reviewed` names why no
#       round landed, and null beside a landed round means only checks were still
#       pending; the review loop takes either as the answer), or the call cap
#       reached first (status pending: resume with --cursor) · 2 tooling
set -uo pipefail
source "$(dirname "${BASH_SOURCE[0]}")/_lib.sh" || { printf '{"error":"cannot source _lib.sh"}\n'; exit 2; }
# The hard bound on waiting a run out, written once: the usage line is where a
# run reads it.
ceiling=1800
usage="usage: poll-pr <pr> [--reviewer <name> [--since <iso>, default the latest request of that reviewer on the host], whose workflow run or native status, under a comment transport, holds the window open past --timeout, to ${ceiling}s] [--brief, or --brief --full <id>[,<id>]|open to read those rounds or threads whole] [--sha <sha>, the head to wait for, default the local HEAD when on the PR head branch, else none; a window closing first is done: false] [--timeout <s>] [--interval <s>] [--cursor <c>, the cursor a status: pending answer carried, to resume its window; no --since or --timeout; a call holds the tool at most ${SHIP_CALL_CAP}s]"
ship_help "$usage" "$@"
ship_args "$usage" pr "$@"
pr=$1; shift
timeout=""; interval=20; name=""; since=""; full='[]'; brief=false; after_run=0; want=""; cursor=""
while [ $# -gt 0 ]; do
  case $1 in
    --brief) brief=true; shift ;;
    --reviewer) case ${2:-} in ''|-*) ship_tooling "$usage" ;; esac; name=$2; shift 2 ;;
    --since) ship_flag_value "$usage" "${2:-}"; since=$2; shift 2 ;;
    # Ids stay strings: GitHub numbers a review and Azure DevOps numbers a
    # thread, and the adapters compare `.id | tostring` against this list.
    --full)
      ship_flag_value "$usage" "${2:-}"
      full=$(ship_id_list "$2") || ship_tooling "$usage"
      shift 2 ;;
    --timeout) ship_flag_value "$usage" "${2:-}"; timeout=$2; shift 2 ;;
    --interval) ship_flag_value "$usage" "${2:-}"; interval=$2; shift 2 ;;
    --sha) case ${2:-} in ''|-*) ship_tooling "$usage" ;; esac; want=$2; shift 2 ;;
    --cursor) ship_flag_value "$usage" "${2:-}"; cursor=$2; shift 2 ;;
    *) ship_tooling "unknown flag: $1" ;;
  esac
done
# `--full` is a --brief modifier and nothing else. It lifted the adapter's clip
# in the full shape too, but that shape keeps its rounds under `reviews` and has
# no `rounds[]` to read the lifted body off: /ship 205 asked three times and got
# null each time, with no error to say the pair was wrong (#218).
[ "$full" = '[]' ] || $brief || ship_tooling "--full needs --brief; $usage"
[ -z "$since" ] || [ -n "$name" ] || ship_tooling "--since needs --reviewer"
# The cursor fixed the window's deadline and its landing rule's instant already,
# so a --since or --timeout beside it would be a second, silent opinion.
start=""; deadline=""
if [ -n "$cursor" ]; then
  [ -z "$since$timeout" ] || ship_tooling "--cursor resumes its own window: drop --since and --timeout"
  c=$(ship_cursor_read "$cursor") || c=null
  jq -e '(.start | type) == "number" and (.deadline | type) == "number"' <<<"$c" >/dev/null \
    || ship_tooling "--cursor does not read: pass the cursor a status: pending answer carried"
  start=$(jq -r .start <<<"$c"); deadline=$(jq -r .deadline <<<"$c")
  since=$(jq -r '.since // ""' <<<"$c"); after_run=$(jq -r '.after_run // 0' <<<"$c")
  [ -n "$want" ] || want=$(jq -r '.want // ""' <<<"$c")
  timeout=$((deadline - start))
  [ -z "$since" ] || [ -n "$name" ] || ship_tooling "--cursor holds a --since: pass its --reviewer again"
fi
# --since is compared as a string against submitted_at, which every adapter
# emits as UTC "YYYY-MM-DDTHH:MM:SSZ". Accept only what normalises to that, so
# an offset this cannot convert (+05:00) is refused outright rather than
# silently sorting wrong. Every mechanic that reports a timestamp
# (request-review's requested_at, open-pr's created_at) already emits it.
if [ -n "$since" ]; then
  case $since in
    [0-9][0-9][0-9][0-9]-[0-9][0-9]-[0-9][0-9]T[0-9][0-9]:[0-9][0-9]:[0-9][0-9]Z) ;;
    [0-9][0-9][0-9][0-9]-[0-9][0-9]-[0-9][0-9]T[0-9][0-9]:[0-9][0-9]:[0-9][0-9].*Z|\
    [0-9][0-9][0-9][0-9]-[0-9][0-9]-[0-9][0-9]T[0-9][0-9]:[0-9][0-9]:[0-9][0-9]*+00:00)
      since=$(printf '%s' "$since" | sed 's/\.[0-9]*//; s/+00:00$/Z/') ;;
    *) ship_tooling "--since must be UTC ISO-8601 (YYYY-MM-DDTHH:MM:SSZ), got: $since" ;;
  esac
fi
# The reviewer is derived from the profile before any host is reached, so a
# mistyped name or a --since the landing rule refuses costs no host call. The one
# refusal held back is a since-rule reviewer with no --since: the host may know
# the instant (`host_pr_requested_at`), and only once it is loaded.
reviewer=null; await=""; await_run=""; phrase=""; refusal=""; need_since=false
if [ -n "$name" ]; then
  row=$(ship_reviewer_by_name "$name") || ship_tooling "$row"
  d=$(ship_reviewer_derive "$row" "$since")
  refusal=$(jq -r '.refusal // empty' <<<"$d")
  if [ -n "$refusal" ] && [ -z "$since" ] && [ "$(jq -r .rule <<<"$d")" = since ]; then need_since=true
  elif [ -n "$refusal" ]; then ship_tooling "$refusal"; fi
  phrase=$(jq -r '.phrase // empty' <<<"$d")
  await=$(jq -r '.login // empty' <<<"$d")
  [ -n "$await" ] || ship_tooling "$name has no Login: to await"
  await_run=$(jq -r '.await_run // empty' <<<"$d")
  [ -n "$timeout" ] || timeout=$(jq -r .timeout <<<"$d")
  reviewer=$(jq -c --arg t "$timeout" '{name, login, rule, await_run, timeout: ($t | tonumber? // $t)}' <<<"$d")
fi
[ -n "$timeout" ] || timeout=480
ship_load_host

# The host's own word for when this reviewer was last asked, so a caller that
# lost the `requested_at` of its request does not have to guess one. Only an
# instant in the one UTC spelling the landing rule compares as a string is taken.
if $need_since; then
  since=$(host_pr_requested_at "$pr" "$await" ${phrase:+"$phrase"}) || since=""
  case $since in
    [0-9][0-9][0-9][0-9]-[0-9][0-9]-[0-9][0-9]T[0-9][0-9]:[0-9][0-9]:[0-9][0-9]Z) ;;
    *) ship_tooling "$refusal" ;;
  esac
fi

# --brief promises the run's own rows are gone, and only the identity can tell
# them apart. Asked before the loop, so an unauthenticated host answers now
# rather than after the timeout; the full shape needs no identity and runs on.
me=""
if $brief; then
  me=$(host_identity) || ship_tooling "cannot read the host identity; --brief cannot drop the run's own rows"
fi

# A call's own clock, apart from the window's: the cap bounds this call, the
# window's `start` and `deadline` span every call of a cursor chain.
call_start=$(date +%s)
[ -n "$start" ] || { start=$call_start; deadline=$((start + timeout)); }
key=on_head; [ -z "$since" ] || key=all
readf=$(mktemp); SHIP_HTTP_STATUS_FILE=$(mktemp)
trap 'rm -f "$readf" "$SHIP_HTTP_STATUS_FILE"' EXIT
fails=0; lost=""; ans=""; sha=""; mergeable=unknown

# One of the loop's host reads, through `ship_poll_read`: 0 answered (the answer
# in $ans), 3 no answer yet, which `miss` counts; a refusal that carries an HTTP
# status is a real one and ends the poll here, as it always did.
rd() { # <what> <fn> <args...>
  local what=$1; shift
  ship_poll_read "$readf" "$@"
  case $? in
    0) ans=$(cat "$readf") ;;
    1) ship_tooling "cannot read $what" ;;
    *) lost=$what; return 3 ;;
  esac
}
# The answer so far, as `out`: the one shape a closed window and a pending one
# are both built from.
snapshot() {
  out=$(jq -n --arg sha "$sha" --arg m "$mergeable" --argjson c "$checks" --argjson r "$reviews" \
    --argjson t "$threads" --argjson rv "$reviewer" --argjson b "$blocked" --argjson rr "$reviewer_run" \
    --argjson lb "$landed_by" --argjson rf "$refused_by" --argjson d "$done" --argjson w "$waited" \
    --arg aw "$await" --argjson st "$stale" \
    '{head_sha: $sha, mergeable: $m, checks: $c, reviews: $r, threads: $t, reviewer: $rv, reviewer_blocked: ($b.line? // null),
      reviewer_run: $rr, landed_by: $lb, refused_by: $rf, done: $d, waited_s: $w}
     | .not_reviewed = (
         if $aw == "" then null
         elif $st and $lb == null then "unreachable"
         elif $m == "conflict" then null
         elif $t == "unavailable" then "unreachable"
         elif $lb != null then null
         elif $rr == "unavailable" then "unreachable"
         elif $rf != null then "blocked"
         elif ($rr | type) == "object" and ($rr.status == "none" or $rr.conclusion == "skipped") then "never-queued"
         elif ($rr | type) == "object" and $rr.status != "completed" then "still-running"
         elif ($rr | type) == "object" and $rr.conclusion == "stale-head" then "stale-head"
         elif ($rr | type) == "object" and $rr.conclusion != "success" then "infra-error"
         else "silent" end)')
}
render() {
  if $brief; then ship_brief "$out" "$me" "$key" "$full"; else printf '%s\n' "$out"; fi
}
# The window is still open and this call has run out of tool time: the snapshot
# it has, `done` false whatever the pass found since the window stays open,
# `not_reviewed` null since a cause is graded only where the window closes (a run
# still going is no infra-error), and the cursor that resumes the same window.
pend() {
  local cur
  waited=$(($(date +%s) - start)); done=false; snapshot
  out=$(jq -c '.not_reviewed = null' <<<"$out")
  cur=$(ship_cursor_make "$(jq -cn --argjson s "$start" --argjson d "$deadline" --arg since "$since" \
    --arg w "$want" --argjson a "$after_run" '{start: $s, deadline: $d, since: $since, want: $w, after_run: $a}')")
  render | jq -c --arg c "$cur" '. + {status: "pending", cursor: $c}'
  exit 1
}
# Sleep one interval, but never past the call cap: at the cap the call answers
# pending instead of sleeping on.
nap() {
  local left=$((SHIP_CALL_CAP - ($(date +%s) - call_start)))
  [ "$left" -gt 0 ] || pend
  if [ "$interval" -lt "$left" ]; then sleep "$interval"; else sleep "$left"; fi
}
# A read got no answer: the third in a row, or one past the window's deadline,
# is exit 2 naming it; otherwise the loop reads again after a nap.
miss() {
  fails=$((fails + 1))
  if [ "$fails" -ge 3 ] || [ "$(date +%s)" -ge "$deadline" ]; then
    ship_tooling "cannot read $lost: the host gave no answer ($fails in a row)"
  fi
  nap
}

while :; do
  # What a pending answer reports when the pass is cut short before a read.
  checks='[]'; reviews='{"on_head":[],"all":[],"total":0}'; threads=null; blocked=null
  reviewer_run=null; landed_by=null; refused_by=null; done=false; stale=false
  waited=$(($(date +%s) - start))
  rd "PR $pr" host_pr_get "$pr" || { miss; continue; }
  prj=$ans
  sha=$(jq -r .head_sha <<<"$prj"); mergeable=$(jq -r .mergeable <<<"$prj")
  if ship_head_stale "$prj" "$want"; then
    [ "$waited" -ge "$timeout" ] || { fails=0; nap; continue; }
    stale=true
  fi
  rd "checks" host_pr_checks "$pr" "$sha" || { miss; continue; }
  checks=$ans
  rd "reviews" host_pr_reviews "$pr" "$sha" "$full" || { miss; continue; }
  reviews=$ans
  # A native integration's round status, read beside its rounds: a clean round
  # it delivers as no review joins them here, before the grade reads a row. A
  # read that fails, or answers what the reader cannot walk, degrades to an
  # "unavailable" run the way a workflow-run read does.
  native=null; native_lost=false
  if [ "$await_run" = "native codex" ]; then
    if act=$(host_pr_native_activity "$pr" "$since" "$phrase") \
       && native=$(jq -ce --arg l "$await" --arg s "$since" --arg h "$sha" "$SHIP_NATIVE_CODEX" <<<"$act" 2>/dev/null); then
      reviews=$(jq -c --argjson n "$native" '.all += $n.rounds | .total += ($n.rounds | length)' <<<"$reviews")
    else native=null; native_lost=true; fi
  fi
  fails=0
  # The previous head's rounds are on no head the run is waiting for.
  ! $stale || reviews=$(jq -c '.on_head = []' <<<"$reviews")
  # Ship's grade (`SHIP_SUBSTANTIVE`), before the landing rule, the refusal
  # rule or the brief reads a row.
  reviews=$(jq -c "$SHIP_SUBSTANTIVE" <<<"$reviews") || ship_tooling "cannot grade reviews"
  threads=$(host_pr_threads "$pr") || threads='"unavailable"'
  blocked=null
  [ -z "$await" ] || blocked=$(host_pr_reviewer_blocked "$pr" "$await") || blocked=null
  # A native reply that is no round is that reviewer's refusal of this request,
  # in words no notice pattern knows, so it outranks an older notice.
  if [ "$native" != null ] && [ "$(jq -c .notice <<<"$native")" != null ]; then
    blocked=$(jq -c .notice <<<"$native")
  fi
  # A read the host refused, an outage included, is "unavailable" rather than a
  # missing run: both leave the window at the constant, and only one of them is
  # evidence about the reviewer. A read that answered with something the filter
  # cannot walk is the same kind of nothing, and saying so keeps the emit below
  # holding one JSON object rather than a `--argjson` that will not parse.
  reviewer_run=null
  if $native_lost; then
    reviewer_run='"unavailable"'
  elif [ "$native" != null ]; then
    reviewer_run=$(jq -c .run <<<"$native")
  elif [ -n "$await_run" ]; then
    if runs=$(host_workflow_runs "$await_run" "$since"); then
      reviewer_run=$(jq -c --arg t "$(jq -r .title <<<"$prj")" "$SHIP_REVIEWER_RUN" <<<"$runs") \
        || reviewer_run='"unavailable"'
    else
      reviewer_run='"unavailable"'
    fi
  fi

  pending=$(jq '[.[] | select(.status == "pending")] | length' <<<"$checks")
  landed=true; landed_by=null; refused_by=null
  if [ -n "$await" ]; then
    # Both sides are now fixed-width UTC, where a string compare is a
    # chronological one.
    landed_by=$(jq -c --arg l "$await" --arg s "$since" "$SHIP_ROUND_BY"' round_by(.substantive)' <<<"$reviews")
    [ "$landed_by" != null ] || landed=false
    # A refusal the landing rule admits is the answer to that request: no round
    # follows it, so the window closes on it rather than on the clock.
    if [ "$landed" = false ]; then
      refused_by=$(jq -c --arg l "$await" --arg s "$since" "$SHIP_ROUND_BY"' round_by((.substantive | not) and (.body // "") != "")' <<<"$reviews")
      # A comment notice has no review row: `host_pr_reviewer_blocked`'s `at`
      # is what the since rule reads, and only that rule (#256).
      if [ "$refused_by" = null ] && [ -n "$since" ]; then
        refused_by=$(jq -c --arg s "$since" \
          '(.at? // "") as $at | if $at != "" and $at >= $s then "since" else null end' <<<"$blocked")
      fi
    fi
  fi
  done=false
  if [ "$mergeable" = conflict ]; then done=true
  elif [ "$pending" -eq 0 ] && [ "$landed" = true ]; then done=true
  fi
  # A window that closed on the previous head answered nothing on this one.
  ! $stale || done=false
  waited=$(($(date +%s) - start))
  run_status=$(jq -r 'if type == "object" then .status else "" end' <<<"$reviewer_run")
  # A run that ended any way but successfully ends the window with it, wherever
  # `--timeout` stands: no round is coming out of it, and the minutes left on the
  # clock would be spent waiting for one.
  dead_run=false
  if [ "$landed" = false ] && [ "$run_status" = completed ] \
     && [ "$(jq -r '.conclusion // ""' <<<"$reviewer_run")" != success ]; then dead_run=true; fi
  # A refused round ends the window the same way, and for the same reason.
  [ "$refused_by" = null ] || dead_run=true
  # The run outranks the constant: a round still being written is not a silent
  # reviewer, and the ceiling is what keeps that from being unbounded.
  if ! $done && ! $dead_run && [ "$landed" = false ] && [ "$waited" -ge "$timeout" ] && [ "$waited" -lt "$ceiling" ]; then
    case $run_status in
      # No run to wait on: no flag, a read the host refused, or no run created.
      ''|none) ;;
      # Only a successful run reaches here, a failed one having closed the
      # window above. It buys one more interval for the row to appear, and one
      # only: waiting on a run that is over is waiting on nothing.
      completed)
        if [ "$after_run" -lt 1 ]; then after_run=1; nap; continue; fi ;;
      # Every other status is a run that has not finished, the host's approval
      # states included, and a round can still come out of it.
      *) nap; continue ;;
    esac
  fi
  # A landed round's run is still going: the job posts its review before its
  # denial step raises the count, so the window stays open until the run ends,
  # to the ceiling, and the count below is read from a completed run (#295).
  if $done && [ "$landed" = true ] && [ "$mergeable" != conflict ] && [ "$waited" -lt "$ceiling" ]; then
    case $run_status in ''|none|completed) ;; *) nap; continue ;; esac
  fi
  if $done || $dead_run || [ "$waited" -ge "$timeout" ]; then
    # Read here rather than per pass, so a poll that waited out the run reads
    # its count once, and only the run it reports.
    if [ "$run_status" = completed ]; then
      denied=null
      if denials=$(host_run_denials "$(jq -r .url <<<"$reviewer_run")"); then
        # An answer that is not one object carrying a count is no count, like a
        # read that failed, and keeps the `--argjson` below to one value.
        denied=$(jq -cs 'if length == 1 then (.[0].denied? | numbers) // null else null end' \
          <<<"$denials" 2>/dev/null) || denied=null
        [ -n "$denied" ] || denied=null
      fi
      reviewer_run=$(jq -c --argjson d "$denied" '.denied = $d' <<<"$reviewer_run")
    fi
    snapshot
    render
    $done
    exit
  fi
  nap
done
