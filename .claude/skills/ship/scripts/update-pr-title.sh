#!/usr/bin/env bash
# Set the PR title on the host, then read it back and report it. The title is
# the squash subject `merge` commits, so a mistitled PR is retitled here.
#
#   update-pr-title <pr> --title "<subject>"
#
# stdout: {pr, title, changed}
# exit: 0 · 1 update failed, with the host's status where there was one · 2 usage
set -uo pipefail
source "$(dirname "${BASH_SOURCE[0]}")/_lib.sh" || { printf '{"error":"cannot source _lib.sh"}\n'; exit 2; }
usage='usage: update-pr-title <pr> --title "<subject>"'
ship_help "$usage" "$@"
[ -n "${1:-}" ] || ship_tooling "$usage"
pr=$1; shift
case $pr in -*) ship_tooling "$usage" ;; esac
title=""
while [ $# -gt 0 ]; do
  case $1 in
    --title) [ -n "${2:-}" ] || ship_tooling "$usage"; title=$2; shift 2 ;;
    *) ship_tooling "unknown flag: $1" ;;
  esac
done
[ -n "$title" ] || ship_tooling "$usage"
ship_load_host

before=$(host_pr_get "$pr" | jq -r .title) || ship_tooling "cannot read PR $pr"
if [ "$before" = "$title" ]; then
  jq -n --argjson pr "$pr" --arg t "$title" '{pr: $pr, title: $t, changed: false}'
  exit 0
fi
answer=$(host_pr_set_title "$pr" "$title") || ship_fail_host "PR title update failed" "$answer"
# The write is proven by the read-back rather than by the host call's exit code.
after=$(host_pr_get "$pr" | jq -r .title) || ship_tooling "cannot read PR $pr back"
[ "$after" = "$title" ] || ship_fail "PR title read back as: $after"
jq -n --argjson pr "$pr" --arg t "$title" '{pr: $pr, title: $t, changed: true}'
