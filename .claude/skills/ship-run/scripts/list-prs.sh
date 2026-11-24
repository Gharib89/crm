#!/usr/bin/env bash
# Open PRs, for the PR cap in the unattended lane.
#
#   list-prs --open
#
# stdout: {count, prs: [{number, title, head_ref, author, url, created_at}]}
# exit: 0 · 2 usage or host failure
set -uo pipefail
source "$(dirname "${BASH_SOURCE[0]}")/_lib.sh" || { printf '{"error":"cannot source _lib.sh"}\n'; exit 2; }
usage='usage: list-prs --open'
ship_help "$usage" "$@"
[ "${1:-}" = --open ] && [ $# -eq 1 ] || ship_tooling "$usage"
ship_load_host
prs=$(host_prs_open) || ship_tooling "cannot list pull requests"
jq -n --argjson p "$prs" '{count: ($p | length), prs: $p}'
