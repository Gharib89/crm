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
[ -n "${1:-}" ] || ship_tooling "$usage"
pr=$1; shift
case $pr in -*) ship_tooling "$usage" ;; esac
[ "${1:-}" = --body-file ] && [ -f "${2:-}" ] && [ $# -eq 2 ] || ship_tooling "$usage"
ship_load_host
out=$(host_pr_comment "$pr" "$2") || ship_fail_host "comment failed" "$out"
printf '%s\n' "$out"
