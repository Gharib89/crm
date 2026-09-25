#!/usr/bin/env bash
# ship phases 6 and 7: the PR in one normalized payload, the read-back after a
# title or body write.
#
#   read-pr <pr>
#
# stdout: {number, url, title, body, head_sha, head_ref, base_ref, state,
#          mergeable}
#   The adapter's PR object unchanged: the same fields on both hosts. Comments,
#   review threads and checks come from poll-pr.
# exit: 0 · 2 the PR could not be read
set -uo pipefail
source "$(dirname "${BASH_SOURCE[0]}")/_lib.sh" || { printf '{"error":"cannot source _lib.sh"}\n'; exit 2; }
usage='usage: read-pr <pr>'
ship_help "$usage" "$@"
[ -n "${1:-}" ] || ship_tooling "$usage"
pr=$1
case $pr in -*) ship_tooling "$usage" ;; esac
[ $# -eq 1 ] || ship_tooling "unknown flag: $2"
ship_load_host

pull=$(host_pr_get "$pr") || ship_tooling "cannot read PR $pr"
printf '%s\n' "$pull"
