#!/usr/bin/env bash
# Replace one `## <section>` of an issue body and leave every other line
# untouched, creating the section at the end when the body has none: the issue
# side of `update-pr-body --section`, through the same surgery
# (ship_body_replace_section) and the same body-file fence refusal.
#
#   update-issue-body <issue> --section <name> --body-file <path>
#
# Section-only by design: no whole-body mode and no preamble, so two runs
# editing different sections of one issue cannot clobber each other. Phase 9
# runs it after `merge` has verified the merge, so nothing reaches the issue for
# code that has not landed.
#
# Trailing newlines are normalized: the body is read without them and written
# ending in exactly one, so a byte diff of a read-back against the body before
# the write can differ there, and outside the section nowhere else.
#
# On Azure DevOps the body is the work item's description, which the adapter
# unwraps and wraps again; a description that is not the one `<pre>` block ship
# writes is refused, exit 1, with the adapter's reason.
#
# stdout: {issue, section, replaced, created, sections[]}
#   sections[]: the `## ` headings of the body AFTER the write.
# exit: 0 · 1 update failed or the body is not one ship edits, with the host's status where there was one
#       · 2 usage, or the issue could not be read
set -uo pipefail
source "$(dirname "${BASH_SOURCE[0]}")/_lib.sh" || { printf '{"error":"cannot source _lib.sh"}\n'; exit 2; }
usage='usage: update-issue-body <issue> --section <name> --body-file <path>'
ship_help "$usage" "$@"
[ -n "${1:-}" ] || ship_tooling "$usage"
issue=$1; shift
case $issue in -*) ship_tooling "$usage" ;; esac
section=""; file=""
while [ $# -gt 0 ]; do
  case $1 in
    --section) case ${2:-} in ""|-*) ship_tooling "$usage" ;; esac; section=$2; shift 2 ;;
    --body-file) case ${2:-} in ""|-*) ship_tooling "$usage" ;; esac; file=$2; shift 2 ;;
    *) ship_tooling "unknown flag: $1" ;;
  esac
done
[ -n "$section" ] && [ -f "$file" ] || ship_tooling "$usage"
content=$(cat "$file") || ship_tooling "cannot read $file"
unclosed=$(ship_fence_unclosed "$content")
[ -z "$unclosed" ] || ship_tooling "body file ends inside an unclosed fence ($unclosed)"
ship_load_host

if ! answer=$(host_issue_body "$issue"); then
  reason=$(jq -r '.reason // empty' <<<"$answer" 2>/dev/null)
  [ -n "$reason" ] || ship_tooling "cannot read issue $issue"
  ship_fail_host "issue $issue: $reason" ""
fi
body=$(jq -r .body <<<"$answer")
new=$(mktemp); trap 'rm -f "$new"' EXIT
if out=$(ship_body_replace_section "$body" "$section" "$file"); then
  replaced=true; created=false
else
  replaced=false; created=true
fi
printf '%s\n' "$out" > "$new"
answer=$(host_issue_set_body "$issue" "$new") || ship_fail_host "issue body update failed" "$answer"
sections=$(ship_body_headings "$(cat "$new")")
jq -n --argjson i "$issue" --arg s "$section" --argjson r "$replaced" --argjson c "$created" --arg h "$sections" \
  '{issue: $i, section: $s, replaced: $r, created: $c,
    sections: ($h | split("\n") | map(select(. != "")))}'
