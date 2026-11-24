#!/usr/bin/env bash
# poll-pr: the host-dependent setup, the read helpers and the poll loop. Sourced by ../poll-pr.sh, never run.
# shellcheck disable=SC2034,SC2154 # variables the sourcing script sets and reads

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
# The ceiling bounds what a run may add to its request, so it counts from the
# since instant where there is one, else from the window's start: counted from
# the window's start alone, which a fresh call resets, every fresh call on a
# stuck run opened a new ceiling (#952).
anchor=$start
if [ -n "$since" ]; then
  anchor=$(jq -rn --arg s "$since" '$s | fromdateiso8601') || ship_tooling "cannot read the since instant: $since"
fi
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
  if $brief; then ship_brief "$(jq -c --argjson st "$stale" '.stale = $st' <<<"$out")" "$me" "$key" "$full"
  else printf '%s\n' "$out"; fi
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
  now=$(date +%s); waited=$((now - start)); aged=$((now - anchor))
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
  if ! $done && ! $dead_run && [ "$landed" = false ] && [ "$waited" -ge "$timeout" ] && [ "$aged" -lt "$ceiling" ]; then
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
  if $done && [ "$landed" = true ] && [ "$mergeable" != conflict ] && [ "$aged" -lt "$ceiling" ]; then
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
