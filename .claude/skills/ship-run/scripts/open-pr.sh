#!/usr/bin/env bash
# ship phase 6: push the branch and prepare a NON-DRAFT PR linked so the merge
# closes the issue (drafts may not trigger a reviewer). The mechanic does not
# create the PR: it prints the host's create command, which the agent runs as
# its own Bash call, then `--verify` reads the PR back. Claude Code counts a PR
# in `claude_code.pull_request.count` only when the create command is the Bash
# command text itself; a create run inside this script counted 0 (#1239).
#
#   open-pr <issue|none> --title "<conventional-commit subject>" --body-file <path>
#   open-pr <issue|none> --verify
#   open-pr <issue> --rework
#
# Run from the run's branch. `none` for the issue is a task-spec run: no
# closing link, no branch-suffix check.
#
# The pre-create form (the first) runs the checks below, looks up the branch,
# pushes, and writes the body the PR opens with, the host's closing link added
# where the body lacks one aimed at this issue, to `<body-file minus .md>.create.md`
# beside the given file. It prints `{create, body_file, branch, base}`: `create`
# is the one command line the agent runs as given, as its own Bash call, never
# wrapped in a script. Where an open PR is already on the branch (a re-run after
# a flake, or after the create ran but the verify did not), it prints that PR as
# `{number, url, created_at, branch, base}` instead, with no `create`, and the
# agent skips to the verify. A create that fails is retried by re-running this
# form, which finds a PR the failed attempt did open, so no flake opens two.
#
# `--verify` finds the open PR on the branch and confirms it is no draft, its
# base is the default branch, and, for an issue, its body carries the host's
# closing link (the host's create body leaves it unchanged). On Azure DevOps the
# work item links the PR and the body carries nothing, so no link is checked. No open PR there,
# or one failing a check, is exit 1. It pushes nothing and takes no title or
# body file.
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
# stderr is empty on a green call: the push logs to a file shown only when it
# fails, so anything on stderr from this mechanic is a failure.
#
# stdout: pre-create {create, body_file, branch, base}, or {number, url, created_at, branch, base} for the PR already open on the branch; --verify and --rework {number, url, created_at, branch, base}
# exit: 0 · 1 title grades below the recorded Grade, body lacks a template heading, the body checker refused the body, push failed, or for --rework or --verify no open PR on the branch, or for --verify a draft, another base or no closing link · 2 usage, wrong branch, the recorded grade, the profile or its PR template, the branch's PR or the host's create body or command could not be read, or the body checker could not answer
set -uo pipefail
source "$(dirname "${BASH_SOURCE[0]}")/_lib.sh" || { printf '{"error":"cannot source _lib.sh"}\n'; exit 2; }
usage='usage: open-pr <issue|none> --title "<subject>" --body-file <path> | open-pr <issue|none> --verify | open-pr <issue> --rework'
ship_help "$usage" "$@"
ship_args "$usage" "issue|none" "$@"
n=$1; shift
title=""; file=""; mode=create
while [ $# -gt 0 ]; do
  case $1 in
    --title) ship_flag_value "$usage" "${2:-}"; title=$2; shift 2 ;;
    --body-file) ship_flag_value "$usage" "${2:-}"; file=$2; shift 2 ;;
    --rework|--verify) [ "$mode" = create ] || ship_tooling "$usage"; mode=${1#--}; shift ;;
    *) ship_tooling "unknown flag: $1" ;;
  esac
done
case $mode in
  rework) [ -z "$title$file" ] && [ "$n" != none ] || ship_tooling "$usage" ;;
  verify) [ -z "$title$file" ] || ship_tooling "$usage" ;;
  create) [ -n "$title" ] && [ -n "$file" ] || ship_tooling "$usage" ;;
esac
ship_load_host

branch=$(git rev-parse --abbrev-ref HEAD) || ship_tooling "not inside a git checkout"
[ "$branch" != HEAD ] || ship_tooling "detached HEAD; check out the run's branch first"
if [ "$n" != none ]; then
  [[ $branch == *-"$n" ]] || ship_tooling "branch $branch does not end in -$n; is this the run's worktree?"
fi
base=$(ship_base_ref) || ship_tooling "cannot resolve origin/HEAD"
base=${base#origin/}
[ "$branch" != "$base" ] || ship_tooling "refusing to open a PR from the base branch $base"
issue_arg=$n; [ "$n" = none ] && issue_arg=""
if [ "$mode" = create ]; then
  [ "$n" = none ] || ship_require_grade "$n" "$title"
  lacks=$(ship_template_missing "$(cat "$file")") || ship_tooling "cannot read $lacks"
  [ "$lacks" = '[]' ] || ship_fail "body lacks the template headings: $(jq -r 'join(", ")' <<<"$lacks")"
  ship_require_body_check "$file"
fi

# The open PR on the branch, read in full: its number, or empty where the
# branch's newest PR is not open.
found=$(host_pr_for_branch "$branch") && state=$(jq -er '.state // "none"' <<<"$found") \
  || ship_tooling "cannot read the PR on $branch"
num=""; [ "$state" = open ] && num=$(jq -r .number <<<"$found")
if [ -n "$num" ]; then
  pr=$(host_pr_get "$num" | jq -ce 'select(.url)') || ship_tooling "cannot read PR #$num"
  # A Rework run's PR was opened by an earlier run, so it carries no creation
  # time for phase 7's `auto-once` window.
  answer=$(jq -c --arg b "$branch" --arg base "$base" --arg mode "$mode" \
    '{number, url, created_at: (if $mode == "rework" then null else .created_at end), branch: $b, base: $base}' <<<"$pr")
fi

if [ "$mode" = verify ]; then
  [ -n "$num" ] || ship_fail "no open PR on $branch"
  [ "$(jq -r .draft <<<"$pr")" != true ] || ship_fail "PR #$num is a draft"
  pbase=$(jq -r .base_ref <<<"$pr")
  [ "$pbase" = "$base" ] || ship_fail "PR #$num targets $pbase, not $base"
  if [ -n "$issue_arg" ]; then
    body=$(jq -r .body <<<"$pr")
    want=$(host_pr_create_body <(printf '%s' "$body") "$issue_arg" | jq -er .body) \
      || ship_tooling "cannot read the host's create body"
    [ "$want" = "$body" ] || ship_fail "PR #$num body does not close #$n"
  fi
  printf '%s\n' "$answer"
  exit 0
fi
[ "$mode" != rework ] || [ -n "$num" ] || ship_fail "no open PR on $branch to rework"

log=$(mktemp); trap 'rm -f "$log"' EXIT
git push -u origin "HEAD:refs/heads/$branch" >"$log" 2>&1 || { ship_tail40 "$log"; ship_fail "git push failed"; }
if [ -n "$num" ]; then
  printf '%s\n' "$answer"
  exit 0
fi

# Absolute, since the agent's Bash call runs the command from wherever it stands.
out=$(CDPATH='' cd -- "$(dirname -- "$file")" && pwd)/$(basename "${file%.md}").create.md
host_pr_create_body "$file" "$issue_arg" | jq -jer '.body + "\n"' > "$out" \
  || ship_tooling "cannot write the create body to $out"
cmd=$(host_pr_create_cmd "$branch" "$base" "$title" "$out" "$issue_arg" | jq -er '.command | select(. != "")') \
  || ship_tooling "the host built no create command"
jq -n --arg c "$cmd" --arg f "$out" --arg b "$branch" --arg base "$base" \
  '{create: $c, body_file: $f, branch: $b, base: $base}'
