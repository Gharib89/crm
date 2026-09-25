#!/usr/bin/env bash
# Reflect the PR on its issue, so a human reading the issue sees the PR.
#
#   reflect <issue> <pr>
#
# stdout: {issue, pr, url, posted}   posted=false when the same line already exists
# exit: 0 · 1 comment failed · 2 usage or tooling
set -uo pipefail
source "$(dirname "${BASH_SOURCE[0]}")/_lib.sh" || { printf '{"error":"cannot source _lib.sh"}\n'; exit 2; }
usage='usage: reflect <issue> <pr>'
ship_help "$usage" "$@"
[ -n "${1:-}" ] && [ -n "${2:-}" ] || ship_tooling "$usage"
n=$1; pr=$2
case $n in -*) ship_tooling "$usage" ;; esac
case $pr in -*) ship_tooling "$usage" ;; esac
[ $# -eq 2 ] || ship_tooling "unknown flag: $3"
ship_load_host
url=$(host_pr_get "$pr" | jq -r .url) || ship_tooling "cannot read PR $pr"
[ -n "$url" ] && [ "$url" != null ] || ship_tooling "cannot read PR $pr"
line="PR: $url"
posted=false
if ! host_issue_comments "$n" | jq -e --arg l "$line" 'any(.[]; .body == $l)' >/dev/null; then
  host_issue_comment "$n" "$line" >/dev/null || ship_fail "comment on issue #$n failed"
  posted=true
fi
jq -n --argjson n "$n" --argjson pr "$pr" --arg u "$url" --argjson p "$posted" '{issue: $n, pr: $pr, url: $u, posted: $p}'
