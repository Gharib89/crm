#!/usr/bin/env bash
# ship phase 9: what the host itself enforces on the PR's base branch, read-only,
# so the human who answers the merge word sees the controls Ship does not own.
# No script reads it and it adds no stop, prompt or host write: it is evidence
# for the merge word that already exists.
#
#   protections <pr>
#
# stdout: {branch, protected, required_checks, required_reviews, bypass, line}
#   The adapter's `host_branch_protections` answer for the PR's base branch.
#   protected: yes | no | unknown. required_checks: names, or `unknown`.
#   required_reviews: a count, or `unknown`. bypass: yes | no | n/a | unknown.
#   A read the host refused (403, missing scope) is `unknown`, never none.
#   line: the summary line, verbatim.
# exit: 0 · 2 usage or tooling: the PR could not be read, the adapter gave no
#       answer, or a flag is unknown (an unreadable protection is exit 0 with
#       `unknown` fields, never a failure)
set -uo pipefail
source "$(dirname "${BASH_SOURCE[0]}")/_lib.sh" || { printf '{"error":"cannot source _lib.sh"}\n'; exit 2; }
usage='usage: protections <pr>'
ship_help "$usage" "$@"
ship_args "$usage" pr "$@"
pr=$1
[ $# -eq 1 ] || ship_tooling "unknown flag: $2"
ship_load_host

base=$(host_pr_get "$pr" | jq -er '.base_ref // empty') || ship_tooling "cannot read PR $pr"
host_branch_protections "$base" | jq -e '
  def list: if type == "array" then (if length == 0 then "none" else join(", ") end) else tostring end;
  . + {line: ("Host protections on \(.branch): protected \(.protected)"
    + (if .protected == "no" then " (Ship'"'"'s own controls are the only merge controls)"
       else "; required checks: \(.required_checks | list); required reviews: \(.required_reviews); admin bypass: \(.bypass)" end))}' \
  || ship_tooling "cannot read the protections of $base"
