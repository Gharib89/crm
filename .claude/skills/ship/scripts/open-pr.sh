#!/usr/bin/env bash
# ship phase 6: push the branch and open a NON-DRAFT PR linked so the merge
# closes the issue (drafts may not trigger a reviewer).
#
#   open-pr <issue|none> --title "<conventional-commit subject>" --body-file <path>
#   open-pr <issue> --rework
#
# Run from the run's branch. `none` for the issue is a task-spec run: no
# closing link, no branch-suffix check. The mechanic adds the host's closing
# link when the body lacks one aimed at this issue. Re-running after a flake
# returns the PR the first call created.
#
# `--rework` is the Rework run's form: the PR is already open on the branch, so
# the mechanic finds it before pushing, refuses where there is none, pushes, and
# answers with it, creating nothing. Its title and body are rewritten through
# update-pr-title and update-pr-body, so no --title or --body-file is taken, and
# `created_at` is null: the PR was opened by an earlier run.
#
# Grade check. Where the issue's Run file records a `Grade: minor` or `Grade:
# breaking`, a title whose type grades patch (anything but `feat` or a `!`) is
# refused before the push, naming the recorded grade and the type to use. No
# Run file, no Grade line, `none` or a title that is no Conventional Commit is no
# check; a record root or Run file that cannot be read is refused, exit 2. The Run file is read at
# `${XDG_STATE_HOME:-~/.local/state}/ship/<repo key>/ship-<issue>/run.md`, so a
# run started with `run-file --scratchpad <dir>` keeps its Run file elsewhere and
# is not checked.
#
# Heading check. A body file lacking a heading of the profile's `## PR`
# `Template:` is refused before the push, naming each one, by the rule read-pr
# reports `missing` with (ship_template_missing): once the PR is open, no
# mechanic renames a reworded section back. No profile or `Template: None.` is
# no check; a profile or template file that cannot be read is refused, exit 2.
#
# Body check. The repo's own body checker, the profile's `## PR` `Body-check:`
# line, then runs on the body file before the push, as update-pr-body runs it
# before a write (ship_require_body_check). A checker exit 1 refuses the call
# with the checker's stdout, so a body it rejects is fixed before any PR exists,
# not at the first phase-7 body write. Any other checker exit is tooling, exit
# 2. No profile, no line or `None.` is no check. `--rework` takes no body file
# and runs none.
#
# `created_at` is the PR's creation time, which is when an `auto-once` reviewer
# fires: phase 7 passes it to `poll-pr --since` so that one round counts on
# whatever head it lands on.
#
# stderr is empty on a green call: the push and the create both log to a file
# shown only when they fail, so anything on stderr from this mechanic is a
# failure.
#
# stdout: {number, url, created_at, branch, base}
# exit: 0 · 1 title grades below the recorded Grade, body lacks a template heading, the body checker refused the body, push or create failed, or for --rework no open PR on the branch · 2 usage, wrong branch, the recorded grade, the profile or its PR template or (--rework) the PR could not be read, or the body checker could not answer
set -uo pipefail
source "$(dirname "${BASH_SOURCE[0]}")/_lib.sh" || { printf '{"error":"cannot source _lib.sh"}\n'; exit 2; }
usage='usage: open-pr <issue|none> --title "<subject>" --body-file <path> | open-pr <issue> --rework'
ship_help "$usage" "$@"
ship_args "$usage" "issue|none" "$@"
n=$1; shift
title=""; file=""; rework=false
while [ $# -gt 0 ]; do
  case $1 in
    --title) ship_flag_value "$usage" "${2:-}"; title=$2; shift 2 ;;
    --body-file) ship_flag_value "$usage" "${2:-}"; file=$2; shift 2 ;;
    --rework) rework=true; shift ;;
    *) ship_tooling "unknown flag: $1" ;;
  esac
done
if $rework; then
  [ -z "$title$file" ] && [ "$n" != none ] || ship_tooling "$usage"
else
  [ -n "$title" ] && [ -n "$file" ] || ship_tooling "$usage"
fi
ship_load_host

branch=$(git rev-parse --abbrev-ref HEAD) || ship_tooling "not inside a git checkout"
[ "$branch" != HEAD ] || ship_tooling "detached HEAD; check out the run's branch first"
if [ "$n" != none ]; then
  [[ $branch == *-"$n" ]] || ship_tooling "branch $branch does not end in -$n; is this the run's worktree?"
fi
base=$(ship_base_ref) || ship_tooling "cannot resolve origin/HEAD"
base=${base#origin/}
[ "$branch" != "$base" ] || ship_tooling "refusing to open a PR from the base branch $base"
$rework || [ "$n" = none ] || ship_require_grade "$n" "$title"
if ! $rework; then
  lacks=$(ship_template_missing "$(cat "$file")") || ship_tooling "cannot read $lacks"
  [ "$lacks" = '[]' ] || ship_fail "body lacks the template headings: $(jq -r 'join(", ")' <<<"$lacks")"
  ship_require_body_check "$file"
fi

if $rework; then
  found=$(host_pr_for_branch "$branch") && state=$(jq -er '.state // "none"' <<<"$found") \
    || ship_tooling "cannot read the PR on $branch"
  [ "$state" = open ] || ship_fail "no open PR on $branch to rework"
  num=$(jq -r .number <<<"$found")
  answer=$(host_pr_get "$num" \
    | jq -e --arg b "$branch" --arg base "$base" 'select(.url) | {number, url, created_at: null, branch: $b, base: $base}') \
    || ship_tooling "cannot read PR #$num"
fi

log=$(mktemp); trap 'rm -f "$log"' EXIT
git push -u origin "HEAD:refs/heads/$branch" >"$log" 2>&1 || { ship_tail40 "$log"; ship_fail "git push failed"; }
if $rework; then
  printf '%s\n' "$answer"
  exit 0
fi

issue_arg=$n; [ "$n" = none ] && issue_arg=""
pr=$(host_pr_create "$branch" "$base" "$title" "$file" "$issue_arg" 2>"$log") \
  || { ship_tail40 "$log"; ship_fail "PR create failed"; }
jq --arg b "$branch" --arg base "$base" '. + {branch: $b, base: $base}' <<<"$pr"
