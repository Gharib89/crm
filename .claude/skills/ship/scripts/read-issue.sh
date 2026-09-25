#!/usr/bin/env bash
# ship phase 0: the issue in one normalized payload.
#
#   read-issue <issue>
#
# stdout: {number, title, body, state, is_pr, labels[], assignees[], created_at,
#          url, comments: [{author, body, created_at}], blockers: [n...] | "unavailable"}
#   blockers lists OPEN blockers only; "unavailable" means the host's blocker
#   query exists and failed (stop rather than guess order on it).
# exit: 0 · 2 the issue could not be read
set -uo pipefail
source "$(dirname "${BASH_SOURCE[0]}")/_lib.sh" || { printf '{"error":"cannot source _lib.sh"}\n'; exit 2; }
usage='usage: read-issue <issue>'
ship_help "$usage" "$@"
[ -n "${1:-}" ] || ship_tooling "$usage"
n=$1
case $n in -*) ship_tooling "$usage" ;; esac
[ $# -eq 1 ] || ship_tooling "unknown flag: $2"
ship_load_host

issue=$(host_issue_get "$n") || ship_tooling "cannot read issue #$n"
comments=$(host_issue_comments "$n") || comments='[]'
blockers=$(host_issue_blockers_open "$n") || blockers='"unavailable"'
jq -n --argjson i "$issue" --argjson c "$comments" --argjson b "$blockers" '$i + {comments: $c, blockers: $b}'
