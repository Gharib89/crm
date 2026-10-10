#!/usr/bin/env bash
# File an adjacent find for triage and leave it alone, unless the tracker
# already carries it.
#
#   file-issue [--repo <owner>/<repo>] --title "<title>" --body-file <path>
#              --label <triage marker> [--distinct-from <n>[,<n>]]
#              [--outside-scope "<reason>"]
#
# --repo files at that GitHub repo instead of the origin's: a Ship defect, at
# the source repo, on the human's word (ADR 0004). Its host is probed first,
# and every exit 1 under it, the probe's or a refused write's, carries the
# command to run by hand as `command`.
#
# In-diff check. Without --repo, on a branch other than the base, a title or body
# citing a path the branch already changes (the diff from the merge base to the
# working tree) is the PR's own work, not an adjacent find: nothing is filed, and
# the answer lists the paths as `in_diff`. --outside-scope "<reason>" skips the
# check, for a find that is really another defect in the same file; the reason
# is the caller's own record and rides back as `outside_scope`, not into the
# issue. A path counts as cited as a whole token wherever it sits, a code span or
# a quote included (ship_paths_cited), so a prefix of a changed path is no
# citation. On the base branch, detached, or where the base does not resolve
# there is no diff to compare and no check. Under --repo the find is another
# repo's and the check is skipped.
#
# Candidate check. Before creating, the mechanic lists the host's open issues
# and compares titles: lowercased, punctuation as a separator, tokens under
# four characters and generic English stopwords dropped. An open issue sharing
# three or more tokens with the new title is a candidate, and with any
# candidate the mechanic files nothing, so consecutive runs meeting the same
# adjacent find cannot file it twice. `--distinct-from` names the numbers the
# caller has read and judged different, and files past them.
#
# The list is the host's own, and GitHub's serves a just-created issue a few
# seconds late, so two calls seconds apart can both file. The caller handles
# that one itself, and cheaply: a run already knows what it just filed. The
# check is for the same find met by a later run, minutes or days on.
#
# stdout: {"filed": true, "number": <n>, "url": "<url>", "outside_scope": "<reason>"}
#         (outside_scope only when --outside-scope was given)
#         {"filed": false, "candidates": [{number, title, url}]}
#         {"error": "...", "in_diff": [<path>]} on exit 1, the find cites a changed path
#         {"error": "...", "command": "<invocation>"} on any exit 1 under --repo
# exit: 0 filed, or a candidate found · 1 find cites a changed path, list or create failed, or --repo unreachable · 2 usage
set -uo pipefail
source "$(dirname "${BASH_SOURCE[0]}")/_lib.sh" || { printf '{"error":"cannot source _lib.sh"}\n'; exit 2; }

usage='usage: file-issue [--repo <owner>/<repo>] --title "<title>" --body-file <path> --label <marker> [--distinct-from <n>[,<n>]] [--outside-scope "<reason>"]'
ship_help "$usage" "$@"
ship_args "$usage" "" "$@"
argv=("$@")
title=""; file=""; label=""; exclude="[]"; repo=""; outside=""
while [ $# -gt 0 ]; do
  case $1 in
    --repo) ship_repo_arg "${2:-}" || ship_tooling "$usage"; repo=$2; shift 2 ;;
    --title) ship_flag_value "$usage" "${2:-}"; title=$2; shift 2 ;;
    --body-file) ship_flag_value "$usage" "${2:-}"; file=$2; shift 2 ;;
    --label) ship_flag_value "$usage" "${2:-}"; label=$2; shift 2 ;;
    --distinct-from)
      ship_flag_value "$usage" "${2:-}"
      exclude=$(jq -cn --arg n "$2" '$n | split(",") | map(tonumber)' 2>/dev/null) \
        || ship_tooling "--distinct-from takes issue numbers: $2"
      shift 2 ;;
    --outside-scope) ship_flag_value "$usage" "${2:-}"; outside=$2; shift 2 ;;
    *) ship_tooling "unknown flag: $1" ;;
  esac
done
[ -n "$title" ] && [ -n "$file" ] || ship_tooling "$usage"
if [ -z "$repo" ] && [ -z "$outside" ] \
   && branch=$(git symbolic-ref -q --short HEAD 2>/dev/null) && base=$(ship_base_ref --local) && [ "$branch" != "${base#origin/}" ] \
   && changed=$(git -c core.quotepath=off diff --name-only "$(git merge-base HEAD "$base")" 2>/dev/null) && [ -n "$changed" ]; then
  in_diff=$(ship_paths_cited "$title"$'\n'"$(cat "$file")" "$changed" | jq -R . | jq -sc .) \
    || ship_tooling "cannot read the find against the paths this branch changed"
  if [ "$in_diff" != "[]" ]; then
    msg=$(jq -r 'join(", ") | "the find cites a path this PR already changes: \(.); fix it in this PR or pass --outside-scope \"<reason>\""' <<<"$in_diff")
    jq -n --arg e "$msg" --argjson d "$in_diff" '{error: $e, in_diff: $d}'
    printf '%s\n' "$msg" >&2
    exit 1
  fi
fi
ship_load_host "$repo"
[ -z "$repo" ] || ship_reach_repo "$repo" "$SHIP_SCRIPTS/file-issue.sh" "${argv[@]}"

open=$(host_issues_open) || ship_fail "cannot list open issues"
candidates=$(ship_title_candidates "$title" "$open" "$exclude") || ship_fail "candidate check failed"
if [ "$candidates" != "[]" ]; then
  jq -n --argjson c "$candidates" '{filed: false, candidates: $c}'
  exit 0
fi

out=$(host_issue_create "$title" "$file" "$label") || ship_fail "issue create failed"
jq --arg o "$outside" '{filed: true} + . + (if $o == "" then {} else {outside_scope: $o} end)' <<<"$out"
