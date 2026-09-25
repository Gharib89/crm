#!/usr/bin/env bash
# The one thing CI cannot answer: has this branch seen every commit on its base?
# CI tests the merge ref, so a branch that predates a merge still goes green,
# while every "does this already exist in the repo?" answer taken from this
# worktree was pre-merge. Pure git, host-neutral, run inline before the local
# gate and after every conflict resolution.
#
#   base-fresh
#
# stdout: {fresh, base, behind, ahead, fetched}   behind commits listed on stderr
# exit: 0 fresh · 1 behind (rebase, then re-run) · 2 the base could not be resolved
#   An unresolvable base fails: a check that could not ask its question must
#   not answer "fresh". Offline is fine (the last-known ref still compares).
set -uo pipefail
source "$(dirname "${BASH_SOURCE[0]}")/_lib.sh" || { printf '{"error":"cannot source _lib.sh"}\n'; exit 2; }
usage='usage: base-fresh'
ship_help "$usage" "$@"
[ $# -eq 0 ] || ship_tooling "base-fresh takes no arguments"
git rev-parse --is-inside-work-tree >/dev/null 2>&1 || ship_tooling "not inside a git checkout"

fetched=true
git fetch -q origin >/dev/null 2>&1 || { fetched=false; echo "fetch failed; comparing against the last-known base" >&2; }
base=$(ship_base_ref) || ship_tooling "cannot resolve origin/HEAD"
git rev-parse --verify -q "$base" >/dev/null || ship_tooling "base $base is not a known ref"

behind=$(git rev-list --count "HEAD..$base") || ship_tooling "cannot compare HEAD with $base"
ahead=$(git rev-list --count "$base..HEAD")
fresh=true; [ "$behind" -eq 0 ] || fresh=false
if ! $fresh; then
  { echo "branch has not seen these commits on $base; rebase onto it and re-run:"; git log --oneline "HEAD..$base"; } >&2
fi
jq -n --argjson f "$fresh" --arg b "$base" --argjson behind "$behind" --argjson ahead "$ahead" --argjson fe "$fetched" \
  '{fresh: $f, base: $b, behind: $behind, ahead: $ahead, fetched: $fe}'
$fresh
