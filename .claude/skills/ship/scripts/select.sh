#!/usr/bin/env bash
# The unattended lane's selection rule, the same in every repo: the oldest-created
# open issue labelled ready-for-agent, with no assignee and no open blocker,
# walking candidates ascending until one passes.
#
#   select
#
# stdout: {selected: n, title, url}                        exit 0
#         {selected: null, reason: "nothing-ready"}         exit 1
#         {selected: null, reason: "blockers-unavailable", candidate: n}  exit 1
#   Both hosts have a blocker query, so a failed query STOPS: shipping a
#   dependent issue out of order builds a PR on unmerged work.
set -uo pipefail
source "$(dirname "${BASH_SOURCE[0]}")/_lib.sh" || { printf '{"error":"cannot source _lib.sh"}\n'; exit 2; }
usage='usage: select'
ship_help "$usage" "$@"
[ $# -eq 0 ] || ship_tooling "select takes no arguments"
ship_load_host
label=$(ship_triage_label ready-for-agent)
candidates=$(host_issues_ready "$label") || ship_tooling "cannot list ready issues"
while IFS= read -r c; do
  [ -n "$c" ] || continue
  n=$(jq -r .number <<<"$c")
  blockers=$(host_issue_blockers_open "$n") || {
    jq -n --argjson n "$n" '{selected: null, reason: "blockers-unavailable", candidate: $n}'; exit 1; }
  [ "$(jq length <<<"$blockers")" -eq 0 ] || continue
  issue=$(host_issue_get "$n") || continue
  # The list query already excluded assignees; re-read once so a claim landing
  # between the two calls is not double-picked.
  [ "$(jq '.assignees | length' <<<"$issue")" -eq 0 ] || continue
  jq -n --argjson n "$n" --argjson i "$issue" '{selected: $n, title: $i.title, url: $i.url}'; exit 0
done < <(jq -c '.[]' <<<"$candidates")
jq -n '{selected: null, reason: "nothing-ready"}'; exit 1
