#!/usr/bin/env bash
# ship phase 0: a fresh branch off origin's default, in a sibling worktree
# (attended) or in the current checkout (unattended, --in-place).
#
#   isolate <issue|none> <type> <slug> [--carry <file>...] [--in-place]
#
# `none` as the issue argument is the task-spec run: it is the literal branch
# and worktree suffix, and every check runs unchanged.
#
# Resolves the MAIN checkout through --git-common-dir, so a run started inside a
# worktree lands its worktree beside that checkout rather than inside itself.
# Fetches first: refusing to branch off a possibly-stale default. Branches from
# origin/HEAD, whatever that branch is called. Refuses when the branch or the
# worktree already exists (preflight should have stopped this run), leaving both
# as it found them. Carried files are gitignored
# files copied IN one way; nothing is ever copied back.
#
# stdout: {worktree, branch, base, in_place, carried[], missing[]}
# exit: 0 created · 1 branch or worktree exists, or dirty tree for --in-place · 2 git or usage failure
set -uo pipefail
source "$(dirname "${BASH_SOURCE[0]}")/_lib.sh" || { printf '{"error":"cannot source _lib.sh"}\n'; exit 2; }
usage='usage: isolate <issue|none> <type> <slug> [--carry <file>...] [--in-place]'
ship_help "$usage" "$@"
[ -n "${1:-}" ] && [ -n "${2:-}" ] && [ -n "${3:-}" ] || ship_tooling "$usage"
n=$1; type=$2; slug=$3; shift 3
case $n in -*) ship_tooling "$usage" ;; esac
case $type in -*) ship_tooling "$usage" ;; esac
case $slug in -*) ship_tooling "$usage" ;; esac
carry=(); in_place=false
while [ $# -gt 0 ]; do
  case $1 in
    --carry) shift; while [ $# -gt 0 ] && [ "${1#--}" = "$1" ]; do carry+=("$1"); shift; done ;;
    --in-place) in_place=true; shift ;;
    *) ship_tooling "unknown flag: $1" ;;
  esac
done

root=$(ship_main_checkout) || ship_tooling "not inside a git checkout"
branch=$(ship_branch "$type" "$slug" "$n")
log=$(mktemp); trap 'rm -f "$log"' EXIT
git -C "$root" fetch origin >"$log" 2>&1 || { ship_tail40 "$log"; ship_tooling "git fetch origin failed"; }
base=$(cd "$root" && ship_base_ref) || ship_tooling "cannot resolve origin/HEAD"

if git -C "$root" show-ref --verify --quiet "refs/heads/$branch"; then
  ship_fail "existing branch: $branch"
fi

if $in_place; then
  [ "${#carry[@]}" -eq 0 ] || ship_tooling "--carry has no meaning with --in-place: the clone already holds its files"
  here=$(git rev-parse --show-toplevel) || ship_tooling "not inside a git checkout"
  [ -z "$(git -C "$here" status --porcelain)" ] || ship_fail "working tree dirty: in-place isolation needs a clean clone"
  git -C "$here" switch -c "$branch" "$base" >"$log" 2>&1 || { ship_tail40 "$log"; ship_tooling "git switch -c failed"; }
  jq -n --arg w "$here" --arg b "$branch" --arg base "$base" \
    '{worktree: $w, branch: $b, base: $base, in_place: true, carried: [], missing: []}'
  exit 0
fi

container=$(ship_worktree_container)
wt="$container/$slug-$n"
[ ! -e "$wt" ] || ship_fail "worktree exists: $wt"
for existing in "$container"/*-"$n"/; do
  [ -d "$existing" ] && ship_fail "worktree exists: ${existing%/}"
done
mkdir -p "$container"
git -C "$root" worktree add "$wt" -b "$branch" "$base" >"$log" 2>&1 || { ship_tail40 "$log"; ship_tooling "git worktree add failed"; }

carried='[]'; missing='[]'
for f in "${carry[@]+"${carry[@]}"}"; do
  if [ -f "$root/$f" ]; then
    mkdir -p "$wt/$(dirname "$f")" && cp "$root/$f" "$wt/$f" && carried=$(jq --arg f "$f" '. + [$f]' <<<"$carried")
  else
    missing=$(jq --arg f "$f" '. + [$f]' <<<"$missing")
  fi
done
jq -n --arg w "$wt" --arg b "$branch" --arg base "$base" --argjson c "$carried" --argjson m "$missing" \
  '{worktree: $w, branch: $b, base: $base, in_place: false, carried: $c, missing: $m}'
