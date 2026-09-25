#!/usr/bin/env bash
# Post a comment on an existing issue, so evidence a run finds for an issue
# already filed lands where a triager reads it rather than in the PR body. On
# Azure DevOps this is a discussion entry on the work item.
#
#   comment-issue <issue> --body-file <path>
#
# stdout: {issue, posted: true}, or on failure {issue, posted: false, error, status}
#         with the HTTP status of the last attempt, null where the host reported none
# exit: 0 · 1 post failed · 2 usage
set -uo pipefail
source "$(dirname "${BASH_SOURCE[0]}")/_lib.sh" || { printf '{"error":"cannot source _lib.sh"}\n'; exit 2; }
usage='usage: comment-issue <issue> --body-file <path>'
ship_help "$usage" "$@"
[ -n "${1:-}" ] || ship_tooling "$usage"
n=$1; shift
case $n in ""|*[!0-9]*) ship_tooling "$usage" ;; esac
[ "${1:-}" = --body-file ] && [ -f "${2:-}" ] && [ $# -eq 2 ] || ship_tooling "$usage"
ship_load_host
# host_issue_comment takes the body as a string, and `$( )` strips trailing
# newlines, so a sentinel carries them through and the post is the file's bytes.
body=$(cat "$2"; printf x); body=${body%x}
if ! answer=$(host_issue_comment "$n" "$body"); then
  # ship_fail_host exits, so it runs in the subshell and its verdict is extended.
  verdict=$(ship_fail_host "comment on issue #$n failed" "$answer")
  jq --argjson n "$n" '{issue: $n, posted: false} + .' <<<"$verdict"
  exit 1
fi
jq -n --argjson n "$n" '{issue: $n, posted: true}'
