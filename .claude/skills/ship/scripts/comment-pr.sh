#!/usr/bin/env bash
# Post a PR comment (the merge summary in the unattended lane, a round log).
# On Azure DevOps this is a thread with status closed, so a comment-resolution
# policy reads it as settled.
#
#   comment-pr <pr> --body-file <path>
#
# stdout: {id, url, created_at}  (created_at null where the host records none)
# exit: 0 · 1 post failed, with the host's status where there was one · 2 usage
set -uo pipefail
source "$(dirname "${BASH_SOURCE[0]}")/_lib.sh" || { printf '{"error":"cannot source _lib.sh"}\n'; exit 2; }
usage='usage: comment-pr <pr> --body-file <path>'
ship_help "$usage" "$@"
ship_args "$usage" pr "$@"
pr=$1; shift
[ "${1:-}" = --body-file ] && [ $# -eq 2 ] || ship_tooling "$usage"
ship_load_host
f=$(mktemp) || ship_tooling "cannot write the comment"
trap 'rm -f "$f"' EXIT
ship_mark_file "$2" "$f" || ship_tooling "cannot read $2"
out=$(host_pr_comment "$pr" "$f") || ship_fail "comment failed" "$out"
printf '%s\n' "$out"
