#!/usr/bin/env bash
# ship phase 8: conflict check first (a conflicted PR has no merge ref, so its
# checks sit pending forever), then a bounded foreground wait until every check
# on the head has completed.
#
#   ci-wait <pr> [--timeout <s>, at least the no-checks grace the profile
#           leaves standing] [--interval <s>]
#
# stdout: {status: green | no-checks | conflict | checks-failed | timeout,
#          head_sha, checks: [{name, status}], failing: [names], waited_s}
#   Every check is listed by name so the run can hold it against the profile's
#   Legs:. no-checks is legal only where the profile's No-checks legal: says so.
# exit: 0 green or no-checks · 1 conflict, a failed check, or the window closed · 2 tooling
set -uo pipefail
source "$(dirname "${BASH_SOURCE[0]}")/_lib.sh" || { printf '{"error":"cannot source _lib.sh"}\n'; exit 2; }
# The no-checks grace: how long the host is given to register a check before an
# empty list is believed. A --timeout under it can only report `timeout` where
# this mechanic answers `no-checks` a minute later, so it is refused rather than
# honoured, and the usage line carries the number the refusal names. The profile
# can drop it to zero, below; the constant is what stands where it does not.
grace=120
usage="usage: ci-wait <pr> [--timeout <s>, at least the no-checks grace (${grace}s, 0 where the profile has Legs: None. and No-checks legal: yes)] [--interval <s>]"
ship_help "$usage" "$@"
[ -n "${1:-}" ] || ship_tooling "$usage"
pr=$1; shift
case $pr in -*) ship_tooling "$usage" ;; esac
timeout=1800; interval=30
while [ $# -gt 0 ]; do
  case $1 in
    --timeout) [ -n "${2:-}" ] || ship_tooling "$usage"; timeout=$2; shift 2 ;;
    --interval) [ -n "${2:-}" ] || ship_tooling "$usage"; interval=$2; shift 2 ;;
    *) ship_tooling "unknown flag: $1" ;;
  esac
done
case $timeout in ''|*[!0-9]*) ship_tooling "$usage" ;; esac
# A repo whose profile declares no legs and legal no-checks has already said an
# empty list is its answer, so there is nothing for the grace to wait out and no
# floor left to hold: the window the floor protects is the grace itself. No
# checkout, or no profile in it, leaves the constant standing.
profile=$(ship_profile_path)
if [ -n "$profile" ] && [ -f "$profile" ] \
   && ship_no_checks_expected "$(cat "$profile")"; then grace=0; fi
[ "$timeout" -ge "$grace" ] || ship_tooling \
  "--timeout below the ${grace}s no-checks grace: a shorter window reports timeout where this mechanic answers no-checks"
ship_load_host

emit() { # <status> <sha> <checks-json> <waited>
  jq -n --arg s "$1" --arg sha "$2" --argjson c "$3" --argjson w "$4" \
    '{status: $s, head_sha: $sha, checks: $c, failing: [$c[] | select(.status == "failure") | .name], waited_s: $w}'
}
start=$SECONDS
while :; do
  prj=$(host_pr_get "$pr") || ship_tooling "cannot read PR $pr"
  sha=$(jq -r .head_sha <<<"$prj")
  waited=$((SECONDS - start))
  if [ "$(jq -r .mergeable <<<"$prj")" = conflict ]; then
    emit conflict "$sha" '[]' "$waited"
    echo "PR $pr conflicts with its base: fetch, rebase, resolve, re-run base-fresh and the local gate, push." >&2
    exit 1
  fi
  checks=$(host_pr_checks "$pr" "$sha") || ship_tooling "cannot read checks"
  n=$(jq length <<<"$checks"); pending=$(jq '[.[] | select(.status == "pending")] | length' <<<"$checks")
  # A path-filtered repo can legitimately report no checks for a docs-only PR;
  # give the host a grace window to register them before believing that. A
  # profile that expects none set the grace to zero above, and the first empty
  # poll is the answer.
  if [ "$n" -eq 0 ] && [ "$waited" -ge "$grace" ]; then emit no-checks "$sha" "$checks" "$waited"; exit 0; fi
  if [ "$n" -gt 0 ] && [ "$pending" -eq 0 ]; then
    if jq -e 'any(.[]; .status == "failure")' <<<"$checks" >/dev/null; then
      emit checks-failed "$sha" "$checks" "$waited"; exit 1
    fi
    emit green "$sha" "$checks" "$waited"; exit 0
  fi
  if [ "$waited" -ge "$timeout" ]; then emit timeout "$sha" "$checks" "$waited"; exit 1; fi
  sleep "$interval"
done
