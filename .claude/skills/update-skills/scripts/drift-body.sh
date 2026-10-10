#!/usr/bin/env bash
# The `## Drift` table of the source repo's upstream-drift issue, built from a
# `plan` answer, so step 7 and the upstream-drift workflow write the same
# table and a re-run that finds nothing new can tell.
#
#   drift-body <plan-file> [--current <body-file>]
#
# One row per `drift` entry and, where the plan's mode is `source`, per
# `others` entry, sorted by skill: `| <skill> | <pinned> | <upstream head> |`,
# the pinned ref a composed skill's pin or another skill's old_ref (`unpinned`
# where null). A consumer's `others` are its own installs, which the public
# source repo's issue never carries.
#
# --current reads an issue body and compares its `## Drift` section's table
# lines with the new table's, each side's trailing blanks and carriage
# returns stripped, a `<details>` record in the section left out: the record is
# history the section surgery carries along, not the current table.
#
# stdout: {"rows": <n>, "table": "<markdown, empty with no rows>", "changed": true|false}
#   changed: true without --current, or where the tables' lines differ
# exit: 0 · 1 an unreadable plan (a row with no string skill or head included)
#       or body file · 2 usage
set -uo pipefail
source "$(dirname "${BASH_SOURCE[0]}")/../../ship/scripts/_lib.sh" || { printf '{"error":"cannot source _lib.sh"}\n'; exit 2; }

usage='usage: drift-body <plan-file> [--current <body-file>]'
ship_help "$usage" "$@"
[ -n "${1:-}" ] || ship_tooling "$usage"
planf=$1; shift
case $planf in -*) ship_tooling "$usage" ;; esac
current=""
while [ $# -gt 0 ]; do
  case $1 in
    --current) case ${2:-} in ''|-*) ship_tooling "$usage" ;; esac; current=$2; shift 2 ;;
    *) ship_tooling "unknown flag: $1" ;;
  esac
done

# A row the table cannot name, a skill or head that is no string, is an
# unreadable plan rather than a `null` cell.
plan=$(jq -ce 'def rows: type == "array" and all(.[]; (.skill | type) == "string" and (.head | type) == "string");
  select(type == "object" and (.drift | rows) and (.others // [] | rows))' "$planf" 2>/dev/null) || ship_fail "cannot read plan: $planf"
rows=$(jq -c '[(.drift[] | {skill, pinned: .pin, head}),
  (if .mode == "source" then (.others // [])[] | {skill, pinned: .old_ref, head} else empty end)] | sort_by(.skill)' <<<"$plan") \
  || ship_fail "cannot read plan: $planf"
table=$(jq -r 'if length == 0 then empty else
  "| Skill | Pinned | Upstream head |", "|---|---|---|",
  (.[] | "| \(.skill) | \(if .pinned == null then "unpinned" else "`\(.pinned)`" end) | `\(.head)` |") end' <<<"$rows") \
  || ship_fail "cannot read plan: $planf"
n=$(jq length <<<"$rows") || ship_fail "cannot read plan: $planf"

# table_lines: a body's `## Drift` table lines, normalised. The heading is
# matched whole at column 0, so `## Drifted` or an indented one is no section.
table_lines() {
  tr -d '\r' | awk '/^## / { s = ($0 ~ /^## Drift[ \t]*$/) ; next }
    s && /^<details[ >]/ { r = 1 } s && !r && /^\|/ { sub(/[ \t]+$/, ""); print } r && /^<\/details>/ { r = 0 }'
}

changed=true
if [ -n "$current" ]; then
  old=$(cat "$current" 2>/dev/null) || ship_fail "cannot read body: $current"
  new_lines=$(printf '## Drift\n%s\n' "$table" | table_lines) || ship_fail "cannot read plan: $planf"
  old_lines=$(printf '%s\n' "$old" | table_lines) || ship_fail "cannot read body: $current"
  [ "$new_lines" != "$old_lines" ] || changed=false
fi
jq -cn --argjson n "$n" --arg t "$table" --argjson c "$changed" '{rows: $n, table: $t, changed: $c}'
