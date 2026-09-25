#!/usr/bin/env bash
# ship phase 9, ONLY after the human said "merge": squash-merge with the PR
# title as the subject, re-verify merged, confirm the issue closed (close it
# explicitly if the link did not fire), delete the remote branch and PROVE the
# deletion, fast-forward the local base branch from the checkout that holds it,
# release the claim and strip ready-for-agent so a reopened issue goes back
# through triage instead of being refused forever. Worktree teardown is
# `cleanup`, run after this.
#
#   merge <pr> <issue|none> [--worktree <path>]
#
# `none` as the issue argument is the task-spec run: there is no issue to close
# or release, so those steps are skipped and their fields are absent from the
# JSON. The merge, the branch deletion and the base fast-forward run unchanged.
#
# Before any of that, two refusals. A PR that is neither open nor already merged
# is not the PR the human said "merge" about: exit 1 `pr-closed: <state>`, with
# nothing merged, no issue closed and no branch deleted. The host's merge
# endpoint accepts a closed PR, so a run that comes back to a stale PR number
# would otherwise land a branch somebody deliberately closed. Then the branch is
# proven fresh against its base: the base can move between phase 5's
# `base-fresh` and the human's "merge", and the squash would land a branch blind
# to those commits, which leaves the summary they approved written against a
# different tree than the one that would land. Refused as exit 1 `stale-base`.
#
# stdout: {merged, issue_closed, remote_branch_deleted, base_updated,
#          claim_released, ready_for_agent_removed}
#         `merge none` omits issue_closed, claim_released and ready_for_agent_removed.
# exit: 0 every step true · 1 a step is false (re-run merge: an already-merged
#       PR skips straight to the remaining steps), the PR is
#       neither open nor already merged (`{"error": "pr-closed: <state>"}`), or
#       the base moved
#       (`{"error": "stale-base: behind <n> on <base>"}`); neither merges anything
#       · 2 usage or tooling
set -uo pipefail
# No `set -e`: the steps below use explicit `|| flag=false`, and
# `git ls-remote --exit-code` returning non-zero is a SUCCESS signal.
source "$(dirname "${BASH_SOURCE[0]}")/_lib.sh" || { printf '{"error":"cannot source _lib.sh"}\n'; exit 2; }
usage='usage: merge <pr> <issue|none> [--worktree <path>]'
ship_help "$usage" "$@"
[ -n "${1:-}" ] && [ -n "${2:-}" ] || ship_tooling "$usage"
pr=$1; issue=$2; shift 2
case $pr in -*) ship_tooling "$usage" ;; esac
case $issue in -*) ship_tooling "$usage" ;; esac
wt=""
while [ $# -gt 0 ]; do
  case $1 in
    --worktree) [ -n "${2:-}" ] || ship_tooling "$usage"; wt=$2; shift 2 ;;
    *) ship_tooling "unknown flag: $1" ;;
  esac
done
ship_load_host
main=$(cd "${wt:-.}" && ship_main_checkout) || ship_tooling "not inside a git checkout"

merged=false; issue_closed=false; remote_deleted=false; base_updated=false; released=false; rfa_removed=false
# A task-spec run has no issue: its steps are skipped, their flags stand true so
# the exit test below reads only the steps that ran, and `finish` drops them.
[ "$issue" != none ] || { issue_closed=true; released=true; rfa_removed=true; }
finish() {
  jq -n --argjson m "$merged" --argjson i "$issue_closed" --argjson r "$remote_deleted" \
    --argjson b "$base_updated" --argjson c "$released" --argjson l "$rfa_removed" --arg n "$issue" \
    '{merged: $m, issue_closed: $i, remote_branch_deleted: $r, base_updated: $b, claim_released: $c, ready_for_agent_removed: $l}
     | if $n == "none" then del(.issue_closed, .claim_released, .ready_for_agent_removed) else . end'
  exit "$1"
}

prj=$(host_pr_get "$pr") || ship_tooling "cannot read PR $pr"
state=$(jq -r '.state // ""' <<<"$prj")
# Before anything else the PR itself is read: a human said "merge" about a PR,
# and one that is closed is not the PR they said it about. Ahead of the
# freshness check because a closed PR's branch has nothing to be fresh against,
# and a stale-base refusal there would name the wrong reason.
closed=$(ship_pr_state_reason "$state")
[ -z "$closed" ] || ship_fail "$closed"
title=$(jq -r .title <<<"$prj"); branch=$(jq -r .head_ref <<<"$prj"); base=$(jq -r .base_ref <<<"$prj")
[ "$branch" != "$base" ] || ship_tooling "PR head is the base branch; refusing"

# 1. Merge, then verify: the read-back is what says the call took.
if [ "$state" = merged ]; then merged=true
else
  # The freshness check, in the checkout that holds the run's branch, before
  # anything is squashed: attended, rebase, re-run the local gate and come back
  # to the merge gate; unattended, hand back. A PR that is already merged stops
  # short of it: its branch is behind a base its own squash advanced, and the
  # run still owes the cleanup steps below.
  fresh=$(cd "${wt:-.}" && "$SHIP_SCRIPTS/base-fresh.sh" 2>/dev/null); rc=$?
  [ "$rc" -lt 2 ] || ship_tooling "cannot read base freshness: base-fresh exited $rc"
  stale=$(ship_stale_base_reason "$fresh")
  [ -z "$stale" ] || ship_fail "$stale"
  host_pr_merge "$pr" "$title (#$pr)" >/dev/null 2>&1 || echo "merge call failed; verifying state anyway" >&2
  # Azure DevOps completes asynchronously: `pr update --status completed` returns
  # the still-active PR and the merge lands a few seconds later, so poll for it.
  for _ in $(seq 1 10); do
    [ "$(host_pr_get "$pr" | jq -r .state)" = merged ] && merged=true && break
    sleep 3
  done
fi
[ "$merged" = true ] || { echo "PR $pr did not reach merged; stopping before any cleanup" >&2; finish 1; }

# 2. The linked issue: give the host's automation a beat, then close explicitly.
if [ "$issue" != none ]; then
  for _ in 1 2 3; do
    [ "$(host_issue_get "$issue" | jq -r .state)" = closed ] && issue_closed=true && break
    sleep 2
  done
  if [ "$issue_closed" = false ]; then
    host_issue_close "$issue" >/dev/null 2>&1
    [ "$(host_issue_get "$issue" | jq -r .state)" = closed ] && issue_closed=true
  fi
fi

# 3. Remote branch: delete, then prove. ls-remote --exit-code returns 2 only when
# no ref matches; anything else (0 present, 128 transient) is not proof.
git -C "$main" push origin --delete "$branch" >/dev/null 2>&1 || true
git -C "$main" ls-remote --exit-code --heads origin "$branch" >/dev/null 2>&1
[ $? -eq 2 ] && remote_deleted=true

# 4. Fast-forward the local base from the checkout that holds it. A plain pull
# from the feature worktree would pull the base INTO the feature branch. A
# transient index.lock from a concurrent `git status` is retried and left in
# place (deleting one is only safe with no git process running, which this script
# cannot prove). A diverged local base is reported and kept.
git -C "$main" fetch origin >/dev/null 2>&1
holder=$(git -C "$main" worktree list --porcelain | awk -v b="refs/heads/$base" '$1=="worktree"{w=$2} $1=="branch" && $2==b {print w}' | head -1)
if [ -n "$holder" ]; then
  fflog=$(mktemp); trap 'rm -f "$fflog"' EXIT
  for attempt in 1 2 3; do
    git -C "$holder" pull --ff-only origin "$base" >"$fflog" 2>&1 && base_updated=true && break
    grep -q 'index.lock' "$fflog" || break   # only a lock is worth retrying
    sleep "$attempt"
  done
  [ "$base_updated" = true ] || { echo "local $base in $holder could not fast-forward; left untouched:" >&2; ship_tail40 "$fflog"; }
  rm -f "$fflog"
elif git -C "$main" show-ref --verify --quiet "refs/heads/$base"; then
  git -C "$main" fetch origin "$base:$base" >/dev/null 2>&1 && base_updated=true \
    || echo "local $base has diverged from origin/$base; left untouched" >&2
else
  base_updated=true  # no local base branch to update
fi

# 5. Release the claim and strip ready-for-agent.
if [ "$issue" != none ]; then
  me=$(host_identity) || me=""
  if [ -n "$me" ]; then
    if host_issue_get "$issue" | jq -e --arg m "$me" '.assignees | index($m)' >/dev/null; then
      host_issue_unassign "$issue" "$me" >/dev/null 2>&1
    fi
    host_issue_get "$issue" | jq -e --arg m "$me" '.assignees | index($m) | not' >/dev/null && released=true
  fi
  rfa=$(ship_triage_label ready-for-agent)
  host_issue_remove_label "$issue" "$rfa" >/dev/null 2>&1
  host_issue_has_label "$issue" "$rfa" || rfa_removed=true
fi

[ "$issue_closed" = true ] && [ "$remote_deleted" = true ] && [ "$base_updated" = true ] \
  && [ "$released" = true ] && [ "$rfa_removed" = true ] && finish 0
finish 1
