#!/usr/bin/env bash
# ship phase 6: push the branch and open a NON-DRAFT PR linked so the merge
# closes the issue (drafts may not trigger a reviewer).
#
#   open-pr <issue|none> --title "<conventional-commit subject>" --body-file <path>
#
# Run from the run's branch. `none` for the issue is a task-spec run: no
# closing link, no branch-suffix check. The mechanic adds the host's closing
# link when the body lacks one aimed at this issue. Re-running after a flake
# returns the PR the first call created.
#
# Grade check. Where the issue's Run file records a `Grade: minor` or `Grade:
# breaking`, a title whose type grades patch (anything but `feat` or a `!`) is
# refused before the push, naming the recorded grade and the type to use. No
# Run file, no Grade line, `none` or a title that is no Conventional Commit is no
# check. The Run file is read at `<git common dir>/ship/ship-<issue>/run.md`, so a
# run started with `run-file --scratchpad <dir>` keeps its Run file elsewhere and
# is not checked.
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
# exit: 0 · 1 title grades below the recorded Grade, or push or create failed · 2 usage or wrong branch
set -uo pipefail
source "$(dirname "${BASH_SOURCE[0]}")/_lib.sh" || { printf '{"error":"cannot source _lib.sh"}\n'; exit 2; }
usage='usage: open-pr <issue|none> --title "<subject>" --body-file <path>'
ship_help "$usage" "$@"
ship_args "$usage" "issue|none" "$@"
n=$1; shift
title=""; file=""
while [ $# -gt 0 ]; do
  case $1 in
    --title) ship_flag_value "$usage" "${2:-}"; title=$2; shift 2 ;;
    --body-file) ship_flag_value "$usage" "${2:-}"; file=$2; shift 2 ;;
    *) ship_tooling "unknown flag: $1" ;;
  esac
done
[ -n "$title" ] && [ -n "$file" ] || ship_tooling "$usage"
ship_load_host

branch=$(git rev-parse --abbrev-ref HEAD) || ship_tooling "not inside a git checkout"
[ "$branch" != HEAD ] || ship_tooling "detached HEAD; check out the run's branch first"
if [ "$n" != none ]; then
  [[ $branch == *-"$n" ]] || ship_tooling "branch $branch does not end in -$n; is this the run's worktree?"
fi
base=$(ship_base_ref) || ship_tooling "cannot resolve origin/HEAD"
base=${base#origin/}
[ "$branch" != "$base" ] || ship_tooling "refusing to open a PR from the base branch $base"
[ "$n" = none ] || ship_require_grade "$n" "$title"

log=$(mktemp); trap 'rm -f "$log"' EXIT
git push -u origin "HEAD:refs/heads/$branch" >"$log" 2>&1 || { ship_tail40 "$log"; ship_fail "git push failed"; }

issue_arg=$n; [ "$n" = none ] && issue_arg=""
pr=$(host_pr_create "$branch" "$base" "$title" "$file" "$issue_arg" 2>"$log") \
  || { ship_tail40 "$log"; ship_fail "PR create failed"; }
jq --arg b "$branch" --arg base "$base" '. + {branch: $b, base: $base}' <<<"$pr"
