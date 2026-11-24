#!/usr/bin/env bash
# After the merge: remove the run's record, its worktree and force-delete its
# local branch (a squash-merged branch is not an ancestor of the default
# branch). Only the run's own worktree and branch come off, found by the branch
# a worktree has checked out, whatever its folder is named: a host that made the
# worktree (T3 names the folder `<slug>-<hex>`) and isolate adopted it is
# removed too.
#
#   cleanup <issue> | cleanup none <branch>
#
# A tracked issue's branch is held to isolate's `<type>/<slug>-<issue>` shape
# (a hand-made branch that merely ends in `-<n>` is never ship's). More than one
# worktree or bare branch of that shape is a refusal naming each, removing
# nothing: which one is the run's is not this mechanic's to guess. A worktree on
# a branch without that shape is not found and stays; isolate adopts none.
#
# `none` is the task-spec run, which has no issue and whose branch need not carry
# its slug (two such runs can coexist, and an adopted host worktree keeps the
# host's branch name). It takes the exact branch the PR was opened from, the
# `head_ref` that `read-pr` returns, and a missing branch is a usage error.
# No record is removed for it: nothing says which run a `none` record belongs to.
#
# Carried files stay in the worktree this removes. The record is the
# `ship-<issue>` Run file and `scratch-<issue>` Scratch directory under
# `${XDG_STATE_HOME:-~/.local/state}/ship/<repo key>` that `run-file init`
# made; it goes before the worktree, so a worktree that will not come off still
# leaves the run's own files cleaned.
#
# stdout: {worktree, branch, worktree_removed, branch_deleted, record}
#   worktree_removed: true when a worktree was found and removed, false when it
#     would not come off, null when the run had no worktree to remove (an
#     in-place run, or one already gone): never true for a worktree not touched
#   record: "removed" when the run's record and scratch directory came off,
#     "none" when there was nothing to remove (the issue is `none`, or no record
#     existed), "failed" when an rm failed or the record root could not be
#     named (cksum failed)
#   an ambiguous match answers {error} naming each match, on exit 1, removing nothing
# exit: 0 clean · 1 a step failed or the match was ambiguous (JSON says which) · 2 usage or tooling
set -uo pipefail
source "$(dirname "${BASH_SOURCE[0]}")/_lib.sh" || { printf '{"error":"cannot source _lib.sh"}\n'; exit 2; }
usage='usage: cleanup <issue> | cleanup none <branch>'
ship_help "$usage" "$@"
if [ "${1:-}" = none ]; then
  ship_args "$usage" "issue|none arg" "$@"
  [ $# -eq 2 ] || ship_tooling "$usage"
  want=$2
else
  ship_args "$usage" "issue" "$@"
  [ $# -eq 1 ] || ship_tooling "$usage"
  want=
fi
n=$1
root=$(ship_main_checkout) || ship_tooling "not inside a git checkout"

wt=null; branch=null; wt_removed=null; br_deleted=true
# Prune first: a registered worktree whose folder is already gone would fail
# `worktree remove`, and the branch it held is then found as a bare branch.
git -C "$root" worktree prune >/dev/null 2>&1
list=$(git -C "$root" worktree list --porcelain) || ship_tooling "git worktree list failed"
# Every candidate as `<worktree path>\t<branch>`, the path empty for a bare
# branch. The main checkout is never a worktree candidate; the branch it holds
# is a bare one. Worktrees are read with awk off the porcelain list, since the
# folder name is the host's and says nothing about the run; bare branches with
# for-each-ref, whose `%(worktreepath)` is empty or the main checkout's.
cands=$({
  awk -v r="$root" '
    /^worktree / { p = substr($0, 10) }
    /^branch refs\/heads\// { if (p != r) print p "\t" substr($0, 19) }' <<<"$list"
  git -C "$root" for-each-ref --format='%(refname:short)%09%(worktreepath)' refs/heads/ \
    | awk -F'\t' -v r="$root" '$2 == "" || $2 == r { print "\t" $1 }'
})
# The run's own: the exact branch for `none`, else isolate's branch shape, which
# is what keeps `branch -D` off a hand-made branch that merely ends in `-<n>`.
if [ -n "$want" ]; then
  found=$(awk -F'\t' -v w="$want" '$2 == w' <<<"$cands")
else
  found=$(awk -F'\t' -v pat="$(ship_branch_shape "$n")" '$2 ~ pat' <<<"$cands")
fi
if [ -n "$found" ] && [ "$(wc -l <<<"$found")" -gt 1 ]; then
  ship_fail "ambiguous: $(wc -l <<<"$found" | tr -d ' ') worktrees or branches match run $n, removed nothing: $(awk -F'\t' '{ printf "%s%s%s", sep, $2, ($1 == "" ? "" : " (" $1 ")"); sep = ", " }' <<<"$found")"
fi

record=none
if [ "$n" != none ]; then
  if records=$(ship_record_root); then
    for r in "$records/ship-$n" "$records/scratch-$n"; do
      [ -e "$r" ] || continue
      rm -rf "$r" && record=removed || { record=failed; break; }
    done
  else record=failed
  fi
fi

if [ -n "$found" ]; then
  wt=${found%%$'\t'*}; branch=${found#*$'\t'}
  [ -n "$wt" ] || wt=null
  if [ "$wt" != null ]; then
    git -C "$root" worktree remove --force "$wt" >/dev/null 2>&1 && wt_removed=true || wt_removed=false
  fi
fi
if [ "$branch" != null ]; then
  if [ "$(git -C "$root" rev-parse --abbrev-ref HEAD)" = "$branch" ]; then
    br_deleted=false; echo "branch $branch is checked out in the main checkout; switch off it first" >&2
  else
    git -C "$root" branch -D "$branch" >/dev/null 2>&1 || br_deleted=false
  fi
fi
jq -n --arg w "$wt" --arg b "$branch" --argjson wr "$wt_removed" --argjson bd "$br_deleted" --arg rec "$record" \
  '{worktree: (if $w == "null" then null else $w end), branch: (if $b == "null" then null else $b end),
    worktree_removed: $wr, branch_deleted: $bd, record: $rec}'
[ "$wt_removed" != false ] && [ "$br_deleted" = true ] && [ "$record" != failed ]
