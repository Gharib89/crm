#!/usr/bin/env bash
# The claim: the assignee is the claim, in every repo.
#
#   manage-issue <issue> take                 assign me; no-op if already me
#   manage-issue <issue> release              unassign me
#   manage-issue <issue> handback "<reason>"  unassign, -ready-for-agent,
#                                             +ready-for-human, comment the reason
#   manage-issue <issue> close                close it; no-op if already closed
#
# take posts the fixed claim comment once. A false success here would let a
# concurrent run double-pick, so an issue held by someone else fails loudly and
# every write is re-read before it is reported. A hand-back whose label edit did
# not land exits 1 even though the claim is gone: the caller is stopping and must
# say the issue is unlabelled rather than report a clean stop.
#
# close is the only verb that is not about the claim: it closes any open issue,
# claimed or not, which is how a verification disposes of the scratch issue it
# created. `merge` still owns closing the issue a merge resolved.
#
# stdout: {issue, identity, claim: taken|held|released, handed_back?, labels?}
#         {issue, identity, closed: true, already} for close
# exit: 0 done · 1 not done (JSON says which step) · 2 usage or tooling
set -uo pipefail
source "$(dirname "${BASH_SOURCE[0]}")/_lib.sh" || { printf '{"error":"cannot source _lib.sh"}\n'; exit 2; }
usage='usage: manage-issue <issue> take|release|handback "<reason>"|close'
ship_help "$usage" "$@"
[ -n "${1:-}" ] && [ -n "${2:-}" ] || ship_tooling "$usage"
n=$1; op=$2
case $n in -*) ship_tooling "$usage" ;; esac
case $op in -*) ship_tooling "$usage" ;; esac
# $3 is handback's reason, free text rather than an id: it takes no such guard.
reason=${3:-}
case $op in
  take|release|close) [ $# -eq 2 ] || ship_tooling "$op takes no further argument" ;;
  handback) [ -n "$reason" ] || ship_tooling 'handback needs a "<reason>"' ;;
  *) ship_tooling "unknown subcommand: $op" ;;
esac
ship_load_host

me=$(host_identity) || ship_tooling "cannot read the signed-in identity"
issue=$(host_issue_get "$n") || ship_tooling "cannot read issue #$n"
# Functions, not values: a verb that re-reads $issue after a write sees the
# new state through them.
assigned() { jq -e --arg m "$me" '.assignees | index($m)' <<<"$issue" >/dev/null; }
state()    { jq -r .state <<<"$issue"; }

case $op in
  take)
    if assigned; then
      jq -n --argjson n "$n" --arg m "$me" '{issue: $n, identity: $m, claim: "held"}'; exit 0
    fi
    others=$(jq -r '.assignees | join(", ")' <<<"$issue")
    [ -z "$others" ] || ship_fail "already claimed: $others"
    host_issue_assign "$n" "$me" || ship_fail "assign call failed"
    issue=$(host_issue_get "$n") || ship_fail "cannot re-read issue #$n after assigning"
    assigned || ship_fail "assignment did not land"
    host_issue_comment "$n" "$SHIP_CLAIM_COMMENT" >/dev/null || echo "claim comment did not post; the assignee still holds the claim" >&2
    jq -n --argjson n "$n" --arg m "$me" '{issue: $n, identity: $m, claim: "taken"}' ;;

  close)
    if [ "$(state)" = closed ]; then
      jq -n --argjson n "$n" --arg m "$me" '{issue: $n, identity: $m, closed: true, already: true}'; exit 0
    fi
    host_issue_close "$n" || ship_fail "close call failed"
    issue=$(host_issue_get "$n") || ship_fail "cannot re-read issue #$n after closing"
    [ "$(state)" = closed ] || ship_fail "close did not land"
    jq -n --argjson n "$n" --arg m "$me" '{issue: $n, identity: $m, closed: true, already: false}' ;;

  release|handback)
    already=true
    if assigned; then
      already=false
      host_issue_unassign "$n" "$me" || ship_fail "unassign call failed"
      issue=$(host_issue_get "$n") || ship_fail "cannot re-read issue #$n after unassigning"
      ! assigned || ship_fail "unassign did not land"
    fi
    if [ "$op" = release ]; then
      jq -n --argjson n "$n" --arg m "$me" --argjson a "$already" '{issue: $n, identity: $m, claim: "released", already: $a}'; exit 0
    fi
    rfa=$(ship_triage_label ready-for-agent); rfh=$(ship_triage_label ready-for-human)
    removed=false; added=false; commented=false
    host_issue_remove_label "$n" "$rfa" && ! host_issue_has_label "$n" "$rfa" && removed=true
    host_issue_add_label "$n" "$rfh" && host_issue_has_label "$n" "$rfh" && added=true
    host_issue_comment "$n" "🤖 Handed back by a ship run: $reason" >/dev/null && commented=true
    handed=false; [ "$removed" = true ] && [ "$added" = true ] && handed=true
    jq -n --argjson n "$n" --arg m "$me" --argjson a "$already" --argjson h "$handed" \
      --argjson rm "$removed" --argjson ad "$added" --argjson c "$commented" --arg rfa "$rfa" --arg rfh "$rfh" \
      '{issue: $n, identity: $m, claim: "released", already: $a, handed_back: $h,
        labels: {removed: (if $rm then $rfa else null end), added: (if $ad then $rfh else null end)}, commented: $c}'
    $handed ;;
esac
