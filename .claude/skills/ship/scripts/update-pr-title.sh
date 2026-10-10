#!/usr/bin/env bash
# Set the PR title on the host, then read it back and report it. The title is
# the squash subject `merge` commits, so a mistitled PR is retitled here.
#
#   update-pr-title <pr> --title "<subject>"
#
# Grade check. When the PR's head branch ends in `-<issue>` and that issue's Run
# file records a `Grade: minor` or `Grade: breaking`, a title whose type grades
# patch (anything but `feat` or a `!`) is refused before any host write, even one
# equal to the current title. No such suffix, Run file or Grade line is no check.
# The Run file is read at `<git common dir>/ship/ship-<issue>/run.md`, so a run
# started with `run-file --scratchpad <dir>` keeps its Run file elsewhere and is
# not checked.
#
# stdout: {pr, title, changed}
# exit: 0 · 1 title grades below the recorded Grade, or update failed (with the host's status where there was one) · 2 usage
set -uo pipefail
source "$(dirname "${BASH_SOURCE[0]}")/_lib.sh" || { printf '{"error":"cannot source _lib.sh"}\n'; exit 2; }
usage='usage: update-pr-title <pr> --title "<subject>"'
ship_help "$usage" "$@"
ship_args "$usage" pr "$@"
pr=$1; shift
title=""
while [ $# -gt 0 ]; do
  case $1 in
    --title) ship_flag_value "$usage" "${2:-}"; title=$2; shift 2 ;;
    *) ship_tooling "unknown flag: $1" ;;
  esac
done
[ -n "$title" ] || ship_tooling "$usage"
ship_load_host

pull=$(host_pr_get "$pr") || ship_tooling "cannot read PR $pr"
before=$(jq -r .title <<<"$pull") || ship_tooling "cannot read PR $pr"
head=$(jq -r '.head_ref // ""' <<<"$pull") || ship_tooling "cannot read PR $pr"
[[ $head =~ -([0-9]+)$ ]] && ship_require_grade "${BASH_REMATCH[1]}" "$title"
if [ "$before" = "$title" ]; then
  jq -n --argjson pr "$pr" --arg t "$title" '{pr: $pr, title: $t, changed: false}'
  exit 0
fi
answer=$(host_pr_set_title "$pr" "$title") || ship_fail "PR title update failed" "$answer"
# The write is proven by the read-back rather than by the host call's exit code.
after=$(host_pr_get "$pr" | jq -r .title) || ship_tooling "cannot read PR $pr back"
[ "$after" = "$title" ] || ship_fail "PR title read back as: $after"
jq -n --argjson pr "$pr" --arg t "$title" '{pr: $pr, title: $t, changed: true}'
