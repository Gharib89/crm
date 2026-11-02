#!/usr/bin/env bash
# The claim: the assignee is the claim, in every repo.
#
#   manage-issue <issue> take [--fresh]       assign me; no-op if already me,
#                                             refused under --fresh
#   manage-issue <issue> release              unassign me
#   manage-issue <issue> handback "<reason>"  unassign, -ready-for-agent,
#                                             +ready-for-human, comment the reason
#   manage-issue <issue> close                close it; no-op if already closed
#
# take posts the fixed claim comment once. A false success here would let a
# concurrent run double-pick, so an issue held by someone else fails loudly and
# every write is re-read before it is reported. A read-back holding another
# identity is `contested`: two runs passed preflight together, so this one gives
# its own assignment back and fails. `--fresh` is a run that began at preflight,
# which read the issue unassigned, so finding it already this identity's means
# another run of this identity took it since: `held` is then a refusal. A rework
# or resumed run holds the claim already and calls take bare.
#
# A hand-back whose label edit did not land exits 1 even though the claim is
# gone: the caller is stopping and must say the issue is unlabelled rather than
# report a clean stop.
#
# close is the only verb that is not about the claim: it closes any open issue,
# claimed or not, which is how a verification disposes of the scratch issue it
# created. `merge` still owns closing the issue a merge resolved.
#
# stdout: {issue, identity, claim: taken|held|contested|released, others?,
#          error?, handed_back?, labels?}; `held` exits 0 bare and 1 with
#          `error` under --fresh, `contested` exits 1 with `others` and `error`.
#         {issue, identity, closed: true, already} for close
# exit: 0 done · 1 not done (JSON says which step) · 2 usage or tooling
set -uo pipefail
source "$(dirname "${BASH_SOURCE[0]}")/_lib.sh" || { printf '{"error":"cannot source _lib.sh"}\n'; exit 2; }
usage='usage: manage-issue <issue> take [--fresh]|release|handback "<reason>"|close'
ship_help "$usage" "$@"
ship_args "$usage" "issue arg" "$@"
n=$1; op=$2
# `ship_args` leaves $3 unchecked: handback's reason is free text, not an id.
reason=${3:-}
fresh=false
case $op in
  take) case $# in
          2) ;;
          3) [ "$3" = --fresh ] || ship_tooling "unknown flag: $3"; fresh=true ;;
          *) ship_tooling "take takes no argument beyond --fresh" ;;
        esac ;;
  release|close) [ $# -eq 2 ] || ship_tooling "$op takes no further argument" ;;
  handback) [ -n "$reason" ] || ship_tooling 'handback needs a "<reason>"' ;;
  *) ship_tooling "unknown subcommand: $op" ;;
esac
ship_load_host

# Both reads precede every verb's first write, so a failure here leaves the claim
# exactly as it was, and the error says so.
me=$(host_identity) || ship_tooling "cannot read the signed-in identity: nothing was written, so the claim is as it was"
issue=$(host_issue_get "$n") || ship_tooling "cannot read issue #$n: nothing was written, so the claim is as it was"
# Functions, not values: a verb that re-reads $issue after a write sees the
# new state through them.
assigned() { jq -e --arg m "$me" '.assignees | index($m)' <<<"$issue" >/dev/null; }
state()    { jq -r .state <<<"$issue"; }

case $op in
  take)
    if assigned; then
      if $fresh; then
        e="held: another run of this identity holds it"
        jq -n --argjson n "$n" --arg m "$me" --arg e "$e" '{issue: $n, identity: $m, claim: "held", error: $e}'
        echo "$e; this run yields, leaving the claim to it" >&2; exit 1
      fi
      jq -n --argjson n "$n" --arg m "$me" '{issue: $n, identity: $m, claim: "held"}'; exit 0
    fi
    others=$(jq -r '.assignees | join(", ")' <<<"$issue")
    [ -z "$others" ] || ship_fail "already claimed: $others"
    host_issue_assign "$n" "$me" || ship_fail "assign call failed"
    issue=$(host_issue_get "$n") || ship_fail "cannot re-read issue #$n after assigning"
    others=$(jq -c --arg m "$me" '[.assignees[] | select(. != $m)]' <<<"$issue") \
      || ship_fail "cannot read the assignees of issue #$n after assigning"
    if [ "$others" != '[]' ]; then
      # Only an assignment this run holds is given back: Azure DevOps' field
      # holds one identity, so an unassign there would clear the other's claim.
      if assigned; then
        host_issue_unassign "$n" "$me" || ship_fail "contested, and the unassign call failed: this run may still hold the issue beside $others"
      fi
      e="contested: $(jq -r 'join(", ")' <<<"$others")"
      jq -n --argjson n "$n" --arg m "$me" --argjson o "$others" --arg e "$e" '{issue: $n, identity: $m, claim: "contested", others: $o, error: $e}'
      echo "$e; this run gave its own assignment back" >&2; exit 1
    fi
    assigned || ship_fail "assignment did not land"
    host_issue_comment "$n" "$(ship_mark '🤖 Claimed by a ship run: implementation in progress.')" >/dev/null || echo "claim comment did not post; the assignee still holds the claim" >&2
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
      host_issue_unassign "$n" "$me" || ship_fail "unassign call failed: the claim may still be held; re-read the issue before reporting it"
      issue=$(host_issue_get "$n") || ship_fail "cannot re-read issue #$n after unassigning: the release is unconfirmed; re-read the issue before reporting it"
      ! assigned || ship_fail "unassign did not land: the claim is still held"
    fi
    if [ "$op" = release ]; then
      jq -n --argjson n "$n" --arg m "$me" --argjson a "$already" '{issue: $n, identity: $m, claim: "released", already: $a}'; exit 0
    fi
    rfa=$(ship_triage_label ready-for-agent); rfh=$(ship_triage_label ready-for-human)
    removed=false; added=false; commented=false
    host_issue_remove_label "$n" "$rfa" && ship_issue_lacks_label "$n" "$rfa" && removed=true
    host_issue_add_label "$n" "$rfh" && ship_issue_has_label "$n" "$rfh" && added=true
    host_issue_comment "$n" "$(ship_mark "🤖 Handed back by a ship run: $reason")" >/dev/null && commented=true
    handed=false; [ "$removed" = true ] && [ "$added" = true ] && handed=true
    jq -n --argjson n "$n" --arg m "$me" --argjson a "$already" --argjson h "$handed" \
      --argjson rm "$removed" --argjson ad "$added" --argjson c "$commented" --arg rfa "$rfa" --arg rfh "$rfh" \
      '{issue: $n, identity: $m, claim: "released", already: $a, handed_back: $h,
        labels: {removed: (if $rm then $rfa else null end), added: (if $ad then $rfh else null end)}, commented: $c}'
    $handed || echo "the claim is released but the hand-back is incomplete: report the labels left null under labels, not a clean stop" >&2
    $handed ;;
esac
