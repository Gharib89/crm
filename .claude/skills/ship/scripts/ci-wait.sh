#!/usr/bin/env bash
# ship phase 8: the expected head, then the conflict check (a conflicted PR has
# no merge ref, so its checks sit pending forever), then a wait until every leg
# check on the head has completed. The wait is bounded twice: by --timeout, the
# window, and by SHIP_CALL_CAP seconds (540), the longest one call holds the
# tool. A window longer than the cap is a chain of calls: the call that would
# pass the cap answers `pending` with a cursor, and `--cursor` resumes it. The
# cursor carries the window's deadline, the expected head only where --sha fixed
# it, and the no-checks grace's clock with the head it started for: a resumed
# call, like any pass, that finds another head starts that clock over.
#
#   ci-wait <pr> [--sha <sha>] [--timeout <s>, at least the no-checks grace the
#           profile leaves standing] [--interval <s>] [--rerun-failed]
#           [--cursor <c>, not with --timeout]
#
# The expected head is `--sha`, else the local HEAD of a checkout of the PR's head
# branch, read afresh on every pass so a push made while the wait runs moves it:
# while the host shows another head the wait grades nothing, and a window that
# closes first is `timeout` carrying the host's head_sha.
#
# Only the profile's Legs: grade the PR. A check belongs to a leg when its name
# is the leg's or starts `<leg> (`, a matrix job. Checks no leg names are
# listed, and one that is red is named, but neither holds the wait nor fails it.
# A profile with no Legs: line leaves every check a leg.
#
# stdout: {status: green | no-checks | conflict | checks-failed | timeout | pending,
#          head_sha, checks: [{name, status}], legs: [names] | null,
#          missing_legs: [leg names no check matches],
#          failing: [leg check names], non_leg_failing: [names], unlisted: [names],
#          waited_s, cursor (pending only), rerun (--rerun-failed on checks-failed)}
#   rerun: [{name, rerun: bool, attempt, log_tail}] or "unavailable". A leg check
#   whose job ran once (attempt 1) is re-run, once; a later attempt is the re-run
#   already made on this head, so it is not repeated and the run reads log_tail.
#   no-checks is legal only where the profile's No-checks legal: says so.
# exit: 0 green or no-checks · 1 conflict, a failed check, the window closed, or
#       pending (resume with the cursor) · 2 tooling
set -uo pipefail
source "$(dirname "${BASH_SOURCE[0]}")/_lib.sh" || { printf '{"error":"cannot source _lib.sh"}\n'; exit 2; }
# The no-checks grace: how long the host is given to register a check before an
# empty list is believed. A --timeout under it can only report `timeout` where
# this mechanic answers `no-checks` a minute later, so it is refused rather than
# honoured, and the usage line carries the number the refusal names. The profile
# can drop it to zero, below; the constant is what stands where it does not.
# SHIP_CI_WAIT_GRACE replaces the constant for the test suite, which proves the
# grace's start in seconds rather than minutes; no run sets it.
grace=${SHIP_CI_WAIT_GRACE:-120}
usage="usage: ci-wait <pr> [--sha <sha>, the head to wait for, default the local HEAD when on the PR head branch, else none; a window closing first is timeout] [--timeout <s>, at least the no-checks grace (${grace}s, 0 where the profile has Legs: None. and No-checks legal: yes)] [--interval <s>] [--rerun-failed, re-run each failing leg check once] [--cursor <c>, the cursor a pending answer carried, to resume that window; not with --timeout]"
ship_help "$usage" "$@"
ship_args "$usage" pr "$@"
pr=$1; shift
timeout=1800; interval=30; want=""; cursor=""; rerun=0; timeout_set=0
while [ $# -gt 0 ]; do
  case $1 in
    --timeout) ship_flag_value "$usage" "${2:-}"; timeout=$2; timeout_set=1; shift 2 ;;
    --interval) ship_flag_value "$usage" "${2:-}"; interval=$2; shift 2 ;;
    --sha) case ${2:-} in ''|-*) ship_tooling "$usage" ;; esac; want=$2; shift 2 ;;
    --cursor) ship_flag_value "$usage" "${2:-}"; cursor=$2; shift 2 ;;
    --rerun-failed) rerun=1; shift ;;
    *) ship_tooling "unknown flag: $1" ;;
  esac
done
case $timeout in ''|*[!0-9]*) ship_tooling "$usage" ;; esac
# Taken before the window's own start, so a fresh call's wait never exceeds it.
call_end=$(( $(date +%s) + SHIP_CALL_CAP ))
# A repo whose profile declares no legs and legal no-checks has already said an
# empty list is its answer, so there is nothing for the grace to wait out and no
# floor left to hold: the window the floor protects is the grace itself. No
# checkout, or no profile in it, leaves the constant standing.
profile=$(ship_profile_path)
body=""; [ -n "$profile" ] && [ -f "$profile" ] && body=$(cat "$profile")
if [ -n "$body" ] && ship_no_checks_expected "$body"; then grace=0; fi
# The legs the profile names: a JSON array, or null where it names none to read
# (no profile, or no Legs: line), which leaves every check a leg. A missing leg
# stops holding the wait only where the profile calls an empty list legal, so a
# path-filtered leg cannot stall a PR the filter skips.
legs_json=null; legal_yes=0
if [ -n "$body" ]; then
  if names=$(ship_profile_legs "$body"); then legs_json=$(jq -Rn '[inputs | select(. != "")]' <<<"$names"); fi
  ship_no_checks_legal "$body" && legal_yes=1
fi
if [ -n "$cursor" ]; then
  # The cursor holds the window's own deadline, so a flag that sets another
  # window beside it has no answer to give.
  [ "$timeout_set" -eq 0 ] || ship_tooling "--cursor resumes its own window: not with --timeout"
  cur=$(ship_cursor_read "$cursor") \
    && jq -e '(.deadline | type) == "number" and (.start | type) == "number"' <<<"$cur" >/dev/null \
    || ship_tooling "--cursor does not read: pass the cursor a pending answer carried"
  deadline=$(jq -r .deadline <<<"$cur"); start=$(jq -r .start <<<"$cur")
  head_at=$(jq -r '.head_at // ""' <<<"$cur"); head_seen=$(jq -r '.head_sha // ""' <<<"$cur")
  [ -n "$want" ] || want=$(jq -r '.want // ""' <<<"$cur")
else
  [ "$timeout" -ge "$grace" ] || ship_tooling \
    "--timeout below the ${grace}s no-checks grace: a shorter window reports timeout where this mechanic answers no-checks"
  start=$(date +%s); deadline=$((start + timeout)); head_at=""; head_seen=""
fi
ship_load_host

# The poll's reads go through ship_poll_read, which tells a host that refused
# (an HTTP status: exit 2 at once) from one that gave no answer (a dropped
# connection: poll again). The status file is per run, so one call's refusal
# cannot read as the next call's.
SHIP_HTTP_STATUS_FILE=$(mktemp); prf=$(mktemp); ckf=$(mktemp)
trap 'rm -f "$SHIP_HTTP_STATUS_FILE" "$prf" "$ckf"' EXIT
sha=""; checks='[]'; waited=0; fails=0; rerun_json=""

# view: the legs' reading of $checks, in $view.
view() {
  view=$(jq -nc --argjson c "$checks" --argjson legs "$legs_json" '
    def named($l): .name == $l or (.name | startswith($l + " ("));
    def isleg: if $legs == null then true else . as $c | any($legs[]; . as $l | $c | named($l)) end;
    ($c | map(select(isleg))) as $lc | ($c | map(select(isleg | not))) as $nl
    | {checks: $c, legs: $legs,
       missing_legs: (if $legs == null then []
         else [$legs[] | . as $l | select(any($c[]; named($l)) | not)] end),
       failing: [$lc[] | select(.status == "failure") | .name],
       non_leg_failing: [$nl[] | select(.status == "failure") | .name],
       unlisted: [$nl[].name],
       leg_n: ($lc | length), leg_pending: ([$lc[] | select(.status == "pending")] | length)}')
}
emit() { # <status> [<cursor>]
  jq -n --arg s "$1" --arg sha "$sha" --argjson v "$view" --argjson w "$waited" --arg cur "${2:-}" --arg rr "$rerun_json" '
    {status: $s, head_sha: $sha} + ($v | del(.leg_n, .leg_pending)) + {waited_s: $w}
    + (if $cur != "" then {cursor: $cur} else {} end)
    + (if $rr != "" then {rerun: ($rr | fromjson)} else {} end)'
}
# The window is open and nothing is final. Sleep, unless the sleep would pass
# this call's cap: then the answer is `pending` with the cursor, which carries
# the deadline and the grace's start so the next call is the same window.
again() {
  local now step
  now=$(date +%s); waited=$((now - start))
  if [ "$now" -ge "$deadline" ]; then view; emit timeout; exit 1; fi
  step=$((deadline - now)); [ "$step" -le "$interval" ] || step=$interval
  if [ $((now + step)) -gt "$call_end" ]; then
    view
    emit pending "$(ship_cursor_make "$(jq -cn --argjson d "$deadline" --argjson s "$start" --arg h "$head_at" --arg hs "$head_seen" --arg w "$want" \
      '{deadline: $d, start: $s, head_at: (if $h == "" then null else ($h | tonumber) end), head_sha: $hs, want: $w}')")"
    exit 1
  fi
  sleep "$step"
}
# A read with no answer: three in a row, or the window closing on one, is the
# host being down, which is a tooling error and not a status of the PR.
unread() { # <what>
  fails=$((fails + 1))
  if [ "$fails" -ge 3 ] || [ "$(date +%s)" -ge "$deadline" ]; then ship_tooling "cannot read $1"; fi
  again
}
# --rerun-failed: each failing leg check's job is read first, so a host that
# cannot answer for one writes nothing for any. A job on its first attempt is
# re-run once; a later attempt is that re-run already made on this head.
rerun_failed() {
  local name job jobs='[]' row did
  while IFS= read -r name; do
    [ -n "$name" ] || continue
    job=$(host_check_job "$pr" "$sha" "$name" 2>/dev/null </dev/null) && jq -e 'type == "object"' <<<"$job" >/dev/null \
      || { rerun_json='"unavailable"'; return; }
    jobs=$(jq -c --arg n "$name" --argjson j "$job" '. + [$j + {name: $n}]' <<<"$jobs")
  done < <(jq -r '.failing[]' <<<"$view")
  rerun_json='[]'
  while IFS= read -r row; do
    did=false
    if jq -e '.attempt == 1 and .job_id != null' <<<"$row" >/dev/null; then
      host_check_rerun "$(jq -r .job_id <<<"$row")" >/dev/null 2>&1 </dev/null && did=true
    fi
    rerun_json=$(jq -c --argjson r "$row" --argjson d "$did" \
      '. + [{name: $r.name, rerun: $d, attempt: $r.attempt, log_tail: $r.log_tail}]' <<<"$rerun_json")
  done < <(jq -c '.[]' <<<"$jobs")
}
while :; do
  ship_poll_read "$prf" host_pr_get "$pr"; rc=$?
  [ "$rc" -ne 1 ] || ship_tooling "cannot read PR $pr"
  [ "$rc" -ne 3 ] || { unread "PR $pr"; continue; }
  prj=$(<"$prf"); sha=$(jq -r .head_sha <<<"$prj")
  waited=$(( $(date +%s) - start ))
  if ship_head_stale "$prj" "$want"; then checks='[]'; fails=0; again; continue; fi
  # The grace counts from the expected head's arrival, so a late push cannot
  # spend it on the previous head: a head other than the one the clock was
  # stamped for, a cursor's included, restarts it.
  if [ -z "$head_at" ] || [ "$sha" != "$head_seen" ]; then head_at=$(date +%s); head_seen=$sha; fi
  if [ "$(jq -r .mergeable <<<"$prj")" = conflict ]; then
    checks='[]'; view; emit conflict
    echo "PR $pr conflicts with its base: fetch, merge the base in, resolve, re-run base-fresh and the local gate, push." >&2
    exit 1
  fi
  ship_poll_read "$ckf" host_pr_checks "$pr" "$sha"; rc=$?
  [ "$rc" -ne 1 ] || ship_tooling "cannot read checks"
  [ "$rc" -ne 3 ] || { unread checks; continue; }
  checks=$(jq -c . "$ckf") || ship_tooling "cannot read checks"
  fails=0
  view
  read -r ln lp nf nm <<<"$(jq -r '[.leg_n, .leg_pending, (.failing | length), (.missing_legs | length)] | @tsv' <<<"$view")"
  now=$(date +%s); waited=$((now - start))
  # A path-filtered repo can legitimately report no checks for a docs-only PR;
  # give the host a grace window to register them before believing that. A
  # profile that expects none set the grace to zero above, and the first empty
  # poll is the answer.
  passed=0; [ $((now - head_at)) -ge "$grace" ] && passed=1
  held=0; [ "$nm" -gt 0 ] && [ "$legal_yes$passed" != 11 ] && held=1
  if [ "$lp" -eq 0 ]; then
    if [ "$nf" -gt 0 ]; then
      [ "$rerun" -eq 0 ] || rerun_failed
      emit checks-failed; exit 1
    fi
    if [ "$held" -eq 0 ]; then
      if [ "$ln" -gt 0 ]; then emit green; exit 0; fi
      if [ "$passed" -eq 1 ]; then emit no-checks; exit 0; fi
    fi
  fi
  again
done
