#!/usr/bin/env bash
# After the merge: remove the issue's run record, its worktree and force-delete
# its local branch (a squash-merged branch is not an ancestor of the default
# branch). Carried files stay in the worktree this removes. The record is the
# `ship-<issue>` Run file and `scratch-<issue>` Scratch directory under
# `<git common dir>/ship` that `run-file init` made; it goes first, so a worktree
# that will not come off still leaves the run's own files cleaned.
#
#   cleanup <issue|none>
#
# `none` as the issue argument is the task-spec run: it is the literal worktree
# and branch suffix the run used, and the teardown is unchanged, except that no
# record is removed for it: nothing says which run a `none` record belongs to.
#
# stdout: {worktree, branch, worktree_removed, branch_deleted, record}
#   record: "removed" when the run's record and scratch directory came off,
#     "none" when there was nothing to remove (the issue is `none`, or no record
#     existed), "failed" when an rm failed
# exit: 0 clean · 1 a step failed (JSON says which) · 2 usage or tooling
set -uo pipefail
source "$(dirname "${BASH_SOURCE[0]}")/_lib.sh" || { printf '{"error":"cannot source _lib.sh"}\n'; exit 2; }
usage='usage: cleanup <issue|none>'
ship_help "$usage" "$@"
ship_args "$usage" "issue|none" "$@"
n=$1
[ $# -eq 1 ] || ship_tooling "unknown flag: $2"
root=$(ship_main_checkout) || ship_tooling "not inside a git checkout"
container=$(ship_worktree_container)

record=none
if [ "$n" != none ] && records=$(ship_record_root); then
  for r in "$records/ship-$n" "$records/scratch-$n"; do
    [ -e "$r" ] || continue
    rm -rf "$r" && record=removed || { record=failed; break; }
  done
fi

wt=null; branch=null; wt_removed=true; br_deleted=true
for d in "$container"/*-"$n"/; do
  [ -d "$d" ] || continue
  d=${d%/}; wt=$d
  branch=$(git -C "$d" rev-parse --abbrev-ref HEAD 2>/dev/null) || branch=null
  git -C "$root" worktree remove --force "$d" >/dev/null 2>&1 || wt_removed=false
  break
done
# An in-place run has no worktree; its branch still matches the suffix.
if [ "$branch" = null ]; then
  branch=$(git -C "$root" for-each-ref --format='%(refname:short)' "refs/heads/*-$n" | head -1)
  [ -n "$branch" ] || branch=null
fi
if [ "$branch" != null ]; then
  if [ "$(git -C "$root" rev-parse --abbrev-ref HEAD)" = "$branch" ]; then
    br_deleted=false; echo "branch $branch is checked out in the main checkout; switch off it first" >&2
  else
    git -C "$root" branch -D "$branch" >/dev/null 2>&1 || br_deleted=false
  fi
fi
git -C "$root" worktree prune >/dev/null 2>&1
jq -n --arg w "$wt" --arg b "$branch" --argjson wr "$wt_removed" --argjson bd "$br_deleted" --arg rec "$record" \
  '{worktree: (if $w == "null" then null else $w end), branch: (if $b == "null" then null else $b end),
    worktree_removed: $wr, branch_deleted: $bd, record: $rec}'
[ "$wt_removed" = true ] && [ "$br_deleted" = true ] && [ "$record" != failed ]
