#!/usr/bin/env bash
# ship phase 9, after explicit "merge" or a clean opted-in gate: squash-merge
# with the PR title as the subject, re-verify merged, confirm the issue closed (close it
# explicitly if the link did not fire), delete the remote branch and PROVE the
# deletion, fast-forward the local base branch from the checkout that holds it,
# release the claim and strip ready-for-agent so a reopened issue goes back
# through triage instead of being refused forever. Worktree teardown is
# `cleanup`, run after this.
#
#   merge <pr> <issue|none> <head> [--worktree <path>, the feature checkout to
#     run the main-checkout lookup and the base freshness check from, default
#     the current directory; teardown is cleanup, run after this] [--run
#     <issue|slug>, the run whose Run file holds the admission record; default
#     the issue, required for `none`]
#
# `none` as the issue argument is the task-spec run: there is no issue to close
# or release, so those steps are skipped and their fields are absent from the
# JSON. The merge, the branch deletion and the base fast-forward run unchanged.
#
# `<head>` is the `head_sha` the merge gate read from `read-pr`: the merge lands
# that head or nothing. It goes to the host's merge call, which refuses a PR
# whose head has moved, so the binding holds at the host and not only here.
#
# Before any of that, three refusals. A PR that is neither open nor already merged
# is no longer the authorized PR: exit 1 `pr-closed: <state>`, with
# nothing merged, no issue closed and no branch deleted. The host's merge
# endpoint accepts a closed PR, so a run that comes back to a stale PR number
# would otherwise land a branch somebody deliberately closed. Then an open PR
# whose head is not `<head>` carries commits nobody read at the merge gate:
# exit 1 `head-moved: <current> != <gated>`. Then the branch is
# proven fresh against its base: the base can move between phase 5's
# `base-fresh` and merge authorization, and the squash would land a branch blind
# to those commits, which leaves the summary they approved written against a
# different tree than the one that would land. Refused as exit 1 `stale-base`.
#
# Then three more, each from the merge call's own reading, since a host's merge
# endpoint admits a token that bypasses the repo's required checks and a body edit
# after `ci-wait` re-fires a workflow on the same head (#1204). The sandbox
# marker `CLAUDE_CODE_REMOTE=true` with no `Sandbox merge: yes` under the
# profile's `## Cloud lane` is `cloud-sandbox`; every profile line here is read
# from the main checkout, not the feature branch under the gate. A head with no admission record
# in the Run file (`run-file gate admit`: `clean` when the clean gate read that
# head, `human` when the human said "merge") is `no-gate-record`. The record
# is read under the default record root only: a run started with `--scratchpad`
# keeps its record elsewhere and a human merges it. A head whose
# checks, read just now through the host seam, are not all settled and passing
# within the profile's `Legs:` and `No-checks legal:` is `checks-unsettled`.
# Each is exit 1 with nothing merged, closed or deleted. An already-merged PR
# skips all of them.
#
# The remote branch is deleted under a lease on the PR's head, so a branch
# recreated under the same name after the merge keeps its commits: it is noted
# on stderr and left, and `remote_branch_deleted` reads true, since no ref on
# origin still points at the PR's head.
#
# stdout: {merged, issue_closed, remote_branch_deleted, base_updated,
#          claim_released, ready_for_agent_removed}
#         `merge none` omits issue_closed, claim_released and ready_for_agent_removed.
# exit: 0 every step true · 1 a step is false (re-run merge: an already-merged
#       PR skips straight to the remaining steps), the PR is
#       neither open nor already merged (`{"error": "pr-closed: <state>"}`), its
#       head moved (`{"error": "head-moved: <current> != <gated>"}`), the base
#       moved
#       (`{"error": "stale-base: behind <n> on <base>"}`), or a #1204 refusal
#       (`cloud-sandbox: ...`, `no-gate-record: ...`, `checks-unsettled: ...`);
#       none merges anything
#       · 2 usage or tooling
set -uo pipefail
# No `set -e`: the steps below use explicit `|| flag=false`, and
# `git ls-remote --exit-code` returning non-zero is a SUCCESS signal.
source "$(dirname "${BASH_SOURCE[0]}")/_lib.sh" || { printf '{"error":"cannot source _lib.sh"}\n'; exit 2; }
usage='usage: merge <pr> <issue|none> <head> [--worktree <path>, the feature checkout to run the main-checkout lookup and the base freshness check from, default the current directory; teardown is cleanup, run after this] [--run <issue|slug>, the run whose Run file holds the admission record, default the issue, required for none]'
ship_help "$usage" "$@"
ship_args "$usage" "pr issue|none head" "$@"
pr=$1; issue=$2; gated=$3; shift 3
wt=""; run_id=""
while [ $# -gt 0 ]; do
  case $1 in
    --worktree) ship_flag_value "$usage" "${2:-}"; wt=$2; shift 2 ;;
    --run) ship_flag_value "$usage" "${2:-}"; run_id=$2; shift 2 ;;
    *) ship_tooling "unknown flag: $1" ;;
  esac
done
# The Run file the admission record lives in: the run's own issue or slug.
[ -n "$run_id" ] || run_id=$issue
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
# Before anything else the authorized PR itself is read: one that is closed
# no longer admits the merge. Ahead of the
# freshness check because a closed PR's branch has nothing to be fresh against,
# and a stale-base refusal there would name the wrong reason.
closed=$(ship_pr_state_reason "$state")
[ -z "$closed" ] || ship_fail "$closed"
# The title, branch, base and head decide what is merged, under which subject,
# and what is deleted, so a missing one is a tooling error, never `null`.
title=$(jq -er '.title // empty' <<<"$prj") || ship_tooling "PR $pr has no title"
branch=$(jq -er '.head_ref // empty' <<<"$prj") || ship_tooling "PR $pr has no head_ref"
base=$(jq -er '.base_ref // empty' <<<"$prj") || ship_tooling "PR $pr has no base_ref"
head=$(jq -er '.head_sha // empty' <<<"$prj") || ship_tooling "PR $pr has no head_sha"
[ "$branch" != "$base" ] || ship_tooling "PR head is the base branch; refusing"

# 1. Merge, then verify: the read-back is what says the call took.
if [ "$state" = merged ]; then merged=true
else
  [ "$head" = "$gated" ] || ship_fail "head-moved: $head != $gated"
  # The freshness check, in the checkout that holds the run's branch, before
  # anything is squashed: attended, merge the base in, re-run the local gate and
  # come back to the merge gate; unattended, hand back. A PR that is already
  # merged stops short of it: its branch is behind a base its own squash
  # advanced, and the run still owes the cleanup steps below.
  fresh=$(cd "${wt:-.}" && "$SHIP_SCRIPTS/base-fresh.sh" 2>/dev/null); rc=$?
  [ "$rc" -lt 2 ] || ship_tooling "cannot read base freshness: base-fresh exited $rc"
  stale=$(ship_stale_base_reason "$fresh")
  [ -z "$stale" ] || ship_fail "$stale"
  # The #1204 refusals, after every cheaper one and just before the host call.
  # The profile is the main checkout's, never the feature branch's: the PR under the
  # gate must not grant its own sandbox merge or drop a leg from `Legs:`.
  profile=""; [ -f "$main/docs/agents/ship.md" ] && profile=$(cat "$main/docs/agents/ship.md")
  if [ "${CLAUDE_CODE_REMOTE:-}" = true ] && [ "$(ship_sandbox_merge "$profile")" != yes ]; then
    ship_fail "cloud-sandbox: CLAUDE_CODE_REMOTE=true and the profile's Cloud lane has no 'Sandbox merge: yes'; this lane does not merge, a human merges"
  fi
  # A task-spec run has no issue to name the Run file, so `none` must say its slug.
  [ "$run_id" != none ] || ship_tooling "merge none needs --run <slug>, the Run file holding the admission record"
  root=$(cd "${wt:-.}" && ship_record_root) || ship_tooling "cannot locate the Run file root"
  # A fenced example in the Run file is not a record, so fenced lines come out first.
  record=$(ship_unfenced "$(cat "$root/ship-$run_id/run.md" 2>/dev/null)" | awk -v h="$gated" '/^## /{f = ($0 ~ /^## Merge admission[ \t\r]*$/)}
    f && /^- [0-9][0-9]:[0-9][0-9] [0-9a-f]+ (clean|human)[ \t\r]*$/ { s = $3; if (index(s, h) == 1 || index(h, s) == 1) hit = $4 }
    END { print hit }')
  [ -n "$record" ] || ship_fail "no-gate-record: no clean gate verdict or merge word recorded for $gated in $root/ship-$run_id/run.md"
  # A PR body or title edit re-fires `edited` workflows, and GitHub registered the new
  # run about 3.5s after the edit returned (measured, #1204), so a read straight after
  # an edit sees only the old success. The grace puts the read past that window.
  sleep "${SHIP_MERGE_CHECKS_GRACE:-6}"
  # Every row must carry a string name and status: a partial row is no verdict.
  cks=$(host_pr_checks "$pr" "$gated" 2>/dev/null) \
    && jq -e 'type == "array" and all(.[]; type == "object" and (.name | type) == "string" and (.status | type) == "string")' <<<"$cks" >/dev/null 2>&1 \
    || ship_tooling "cannot read the checks on $gated"
  unsettled=$(ship_unsettled_checks "$cks" "$profile") || ship_tooling "cannot grade the checks on $gated"
  unsettled=$(paste -sd, - <<<"$unsettled" | sed 's/,/, /g')
  [ -z "$unsettled" ] || ship_fail "checks-unsettled: $unsettled"
  host_pr_merge "$pr" "$title (#$pr)" "$gated" >/dev/null 2>&1 || echo "merge call failed; verifying state anyway" >&2
  # Azure DevOps completes asynchronously: the completion PATCH returns the
  # still-active PR and the merge lands a few seconds later, so poll for it.
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

# 3. Remote branch: delete under a lease on the PR's head, then prove. ls-remote
# --exit-code returns 2 only when no ref matches; 0 at another commit is a branch
# recreated under the name, kept; anything else (0 at the PR's head, 128 transient) is not proof.
git -C "$main" push --force-with-lease="refs/heads/$branch:$head" origin --delete "$branch" >/dev/null 2>&1 || true
at=$(git -C "$main" ls-remote --exit-code origin "refs/heads/$branch" 2>/dev/null); rc=$?
at=${at%%[[:space:]]*}
if [ "$rc" -eq 2 ]; then remote_deleted=true
elif [ "$rc" -eq 0 ] && [ "$at" != "$head" ]; then
  remote_deleted=true
  echo "origin/$branch is at $at, not the PR's head $head: recreated after the merge, so kept" >&2
fi

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
  ship_issue_lacks_label "$issue" "$rfa" && rfa_removed=true
fi

[ "$issue_closed" = true ] && [ "$remote_deleted" = true ] && [ "$base_updated" = true ] \
  && [ "$released" = true ] && [ "$rfa_removed" = true ] && finish 0
finish 1
