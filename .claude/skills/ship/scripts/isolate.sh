#!/usr/bin/env bash
# ship phase 0: a fresh branch off origin's default, in a worktree under
# the main checkout's .claude/worktrees/ (attended) or in the current
# checkout (unattended, --in-place).
#
#   isolate <issue|none> <type> <slug> [--carry <file>...] [--in-place]
#   isolate <issue> <type> <slug> --rework [--carry <file>...]
#
# `none` as the issue argument is the task-spec run: it is the literal branch
# and worktree suffix, and every check runs unchanged. One trailing `-<n>` on
# the slug is dropped, so the number appears once, except under `--rework`,
# where the slug names the PR's own branch as given.
#
# Resolves the MAIN checkout through --git-common-dir, so a run started inside a
# worktree lands its worktree in that checkout's container rather than its own.
# Fetches first: refusing to branch off a possibly-stale default. Branches from
# origin/HEAD, whatever that branch is called. Refuses when the branch or the
# worktree already exists (preflight should have stopped this run), leaving both
# as it found them. Carried files are gitignored
# files copied IN one way; nothing is ever copied back.
#
# Run from a linked worktree a host made (not the main checkout, not one under
# .claude/worktrees/) on a branch ending `-<n>`, whatever its slug, isolate
# adopts it rather than refuse `existing branch`: it fast-forwards the branch to
# origin/HEAD and prints that worktree and branch with `reused: true`, with or
# without --in-place. --carry copies in as for a new worktree, since the host's
# checkout holds no gitignored file (and stays a usage error with --in-place).
# A dirty one is `launched worktree dirty: <path>`, one with commits of its own
# `launched branch <branch> has commits beyond <base>`: a run never adopts work
# it did not make. The folder and branch stay the host's; `cleanup` leaves both.
#
# `--rework` is the Rework run's form, attended only: the branch is the PR's,
# already on origin, and `base` is origin's tip of it, which the rework's own
# diff counts from. A `*-<n>` worktree on that branch, clean and at that tip, is
# reused (`reused: true`, nothing carried); any other one is `worktree exists`.
# With no such worktree, a new one checks the branch out. A local branch behind
# origin's is fast-forwarded first; one carrying commits origin lacks is refused
# (`existing branch: <branch> has commits origin lacks`), since the PR cannot
# show them.
#
# stdout: {worktree, branch, base, in_place, reused, carried[], missing[]}
# exit: 0 created or reused · 1 branch or worktree exists, no PR branch on origin for --rework, dirty tree for --in-place, or a launched worktree dirty or ahead of origin/HEAD · 2 git or usage failure
set -uo pipefail
source "$(dirname "${BASH_SOURCE[0]}")/_lib.sh" || { printf '{"error":"cannot source _lib.sh"}\n'; exit 2; }
usage='usage: isolate <issue|none> <type> <slug> [--carry <file>...] [--in-place] (from a host-launched worktree on a branch ending -<issue>: adopts it, reused true) | isolate <issue> <type> <slug> --rework [--carry <file>...]'
ship_help "$usage" "$@"
ship_args "$usage" "issue|none arg arg" "$@"
n=$1; type=$2; slug=$3; shift 3
carry=(); in_place=false rework=false
while [ $# -gt 0 ]; do
  case $1 in
    --carry) shift; while [ $# -gt 0 ] && [ "${1#--}" = "$1" ]; do carry+=("$1"); shift; done ;;
    --in-place) in_place=true; shift ;;
    --rework) rework=true; shift ;;
    *) ship_tooling "unknown flag: $1" ;;
  esac
done
{ $rework && { $in_place || [ "$n" = none ]; }; } && ship_tooling "$usage"
{ $in_place && [ "${#carry[@]}" -gt 0 ]; } && ship_tooling "--carry has no meaning with --in-place: the clone already holds its files"

# carry_into <dir>: copy each --carry file from the main checkout into <dir>,
# setting carried[] and missing[] for the JSON.
carry_into() {
  carried='[]'; missing='[]'
  local f
  for f in "${carry[@]+"${carry[@]}"}"; do
    if [ -f "$root/$f" ]; then
      mkdir -p "$1/$(dirname "$f")" && cp "$root/$f" "$1/$f" && carried=$(jq --arg f "$f" '. + [$f]' <<<"$carried")
    else
      missing=$(jq --arg f "$f" '. + [$f]' <<<"$missing")
    fi
  done
}

# A slug already ending in the issue number would double it in the branch and
# the worktree. `--rework` keeps the slug as given: it names the PR's own
# branch, which may already carry the doubled number.
$rework || slug=${slug%-"$n"}

root=$(ship_main_checkout) || ship_tooling "not inside a git checkout"
branch=$(ship_branch "$type" "$slug" "$n")
log=$(mktemp); trap 'rm -f "$log"' EXIT
git -C "$root" fetch origin >"$log" 2>&1 || { ship_tail40 "$log"; ship_tooling "git fetch origin failed"; }
if $rework; then
  base=$(git -C "$root" rev-parse -q --verify "refs/remotes/origin/$branch") || ship_fail "no PR branch on origin: $branch"
else
  base=$(cd "$root" && ship_base_ref) || ship_tooling "cannot resolve origin/HEAD"
  # A host-made linked worktree on a branch ending `-<n>` is adopted whatever its
  # slug: the git dir differs from the common dir only in a linked worktree, so
  # the main checkout never is, and one under the container is ship's own,
  # which preflight and cleanup already govern.
  here=$(git rev-parse --show-toplevel) && gd=$(git rev-parse --absolute-git-dir) \
    && common=$(git rev-parse --path-format=absolute --git-common-dir) \
    || ship_tooling "cannot read the current checkout"
  launched=$(git rev-parse --abbrev-ref HEAD 2>/dev/null) || launched=
  if [[ $launched == *-"$n" ]] && [ "$gd" != "$common" ] && [[ $here != "$(ship_worktree_container)"/* ]]; then
    st=$(git status --porcelain) || ship_tooling "cannot read the status of $here"
    [ -z "$st" ] || ship_fail "launched worktree dirty: $here"
    git merge-base --is-ancestor HEAD "$base"
    case $? in
      0) ;;
      1) ship_fail "launched branch $launched has commits beyond $base" ;;
      *) ship_tooling "cannot compare $launched with $base" ;;
    esac
    git merge -q --ff-only "$base" >"$log" 2>&1 || { ship_tail40 "$log"; ship_tooling "git merge --ff-only failed"; }
    carry_into "$here"
    jq -n --arg w "$here" --arg b "$launched" --arg base "$base" --argjson ip "$in_place" --argjson c "$carried" --argjson m "$missing" \
      '{worktree: $w, branch: $b, base: $base, in_place: $ip, reused: true, carried: $c, missing: $m}'
    exit 0
  fi
  if git -C "$root" show-ref --verify --quiet "refs/heads/$branch"; then
    ship_fail "existing branch: $branch"
  fi
fi

if $in_place; then
  here=$(git rev-parse --show-toplevel) || ship_tooling "not inside a git checkout"
  [ -z "$(git -C "$here" status --porcelain)" ] || ship_fail "working tree dirty: in-place isolation needs a clean clone"
  git -C "$here" switch -c "$branch" "$base" >"$log" 2>&1 || { ship_tail40 "$log"; ship_tooling "git switch -c failed"; }
  jq -n --arg w "$here" --arg b "$branch" --arg base "$base" \
    '{worktree: $w, branch: $b, base: $base, in_place: true, reused: false, carried: [], missing: []}'
  exit 0
fi

container=$(ship_worktree_container)
wt="$container/$slug-$n"
for existing in "$container"/*-"$n"/; do
  [ -d "$existing" ] || continue
  existing=${existing%/}
  if $rework && ship_worktree_at "$existing" "$branch" "$base"; then
    jq -n --arg w "$existing" --arg b "$branch" --arg base "$base" \
      '{worktree: $w, branch: $b, base: $base, in_place: false, reused: true, carried: [], missing: []}'
    exit 0
  fi
  ship_fail "worktree exists: $existing"
done
[ ! -e "$wt" ] || ship_fail "worktree exists: $wt"
mkdir -p "$container"
# The container sits inside the main checkout, so list it in the shared exclude
# file (once), where the consumer commits nothing and `git status` stays clean.
# A last line with no newline gets one first, or the entry would join it.
common=$(git -C "$root" rev-parse --path-format=absolute --git-common-dir) || ship_tooling "cannot resolve the git common dir"
exclude=$common/info/exclude
if ! grep -qxF '.claude/worktrees/' "$exclude" 2>/dev/null; then
  mkdir -p "$common/info" \
    && { [ ! -s "$exclude" ] || [ -z "$(tail -c1 "$exclude")" ] || printf '\n'; printf '.claude/worktrees/\n'; } >>"$exclude" \
    || ship_tooling "cannot write $exclude"
fi
if ! $rework; then
  add=(-b "$branch" "$base")
elif git -C "$root" show-ref --verify --quiet "refs/heads/$branch"; then
  git -C "$root" merge-base --is-ancestor "refs/heads/$branch" "$base"
  case $? in
    0) ;;
    1) ship_fail "existing branch: $branch has commits origin lacks" ;;
    *) ship_tooling "cannot compare $branch with origin/$branch" ;;
  esac
  git -C "$root" branch -f "$branch" "$base" >"$log" 2>&1 || { ship_tail40 "$log"; ship_tooling "git branch -f failed"; }
  add=("$branch")
else
  add=(--track -b "$branch" "origin/$branch")
fi
git -C "$root" worktree add "$wt" "${add[@]}" >"$log" 2>&1 || { ship_tail40 "$log"; ship_tooling "git worktree add failed"; }

carry_into "$wt"
jq -n --arg w "$wt" --arg b "$branch" --arg base "$base" --argjson c "$carried" --argjson m "$missing" \
  '{worktree: $w, branch: $b, base: $base, in_place: false, reused: false, carried: $c, missing: $m}'
