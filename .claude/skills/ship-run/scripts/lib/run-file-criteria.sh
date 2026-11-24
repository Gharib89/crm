#!/usr/bin/env bash
# Run-file mechanic: reading an issue's criteria. Sourced by ../run-file.sh, never run.
# shellcheck disable=SC2154 # shares the globals of run-file.sh (SHIP_SCRIPTS)

# The unfenced checkbox lines of an issue's text as `- <n>: <text>` entries: what
# `init --criteria` records under `## Criteria`, and what the gate counts when
# none was recorded. A heading naming criteria whose section holds top-level list
# lines and no checkbox, an empty one included, gives those lines instead, up to
# the next heading at its level or above, since issues here often state criteria
# as plain bullets (#958). Where one does, each criteria section gives its own
# kind, checkboxes or plain lines, and a checkbox outside every criteria section
# (a task list) is no criterion.
criteria_entries() { # criteria_entries <text>
  ship_unfenced "$1" | awk '
    function rest(t) { t = substr($0, RLENGTH + 1); sub(/[[:space:]]+$/, "", t); return t }
    function close_section(i) {
      if (np && !boxes) { for (i = 1; i <= np; i++) out[++no] = plain[i]; found = 1 }
      level = np = boxes = 0 }
    match($0, /^[[:space:]]*([-*+]|[0-9]+[.)])[[:space:]]+\[[ xX]\]([[:space:]]+|$)/) {
      if (level) boxes++
      t = rest(); if (t != "") { box[++nb] = t; if (level) out[++no] = t }
      next }
    match($0, /^#+[[:space:]]/) {
      if (level && RLENGTH - 1 <= level) close_section()
      if (!level && tolower($0) ~ /criteria/) level = RLENGTH - 1
      next }
    level && match($0, /^([-*+]|[0-9]+[.)])[[:space:]]+/) { t = rest(); if (t != "") plain[++np] = t }
    END {
      close_section()
      if (found) for (i = 1; i <= no; i++) printf "- %d: %s\n", i, out[i]
      else for (i = 1; i <= nb; i++) printf "- %d: %s\n", i, box[i] }'
}

# How many criteria issue <n>'s body carries, by the rule above; fails where the issue cannot be read.
issue_criteria() { # issue_criteria <n>
  local body
  body=$("$SHIP_SCRIPTS/read-issue.sh" "$1" 2>/dev/null | jq -er '.body // ""') || return 1
  criteria_entries "$body" | awk 'END { print NR }'
}

# Whether the unfenced Run file text records a `## Criteria` section, empty or not.
criteria_recorded() { grep -q '^## Criteria[[:space:]]*$' <<<"$1"; }

# The clean gate's hold for criteria an issue states and the Run file never
# recorded, criteria nobody judged (#933), as a JSON array of at most one reason.
# A recorded `## Criteria`, even an empty one, is the run's own judgment, and a
# slug run has no issue to read.
criteria_unrecorded() { # criteria_unrecorded <unfenced Run file text>, with file already parsed
  local id count
  id=$(basename "$(dirname "$file")"); id=${id#ship-}
  if criteria_recorded "$1" || ! [[ $id =~ ^[1-9][0-9]*$ ]]; then echo '[]'; return 0; fi
  count=$(issue_criteria "$id") || ship_tooling "cannot read issue $id to count its criteria: run \`read-issue $id\` and fix what it reports, then re-run"
  [ "$count" -gt 0 ] || { echo '[]'; return 0; }
  jq -cn --arg h "criteria: the Run file has no ## Criteria but issue #$id has $count criteria; record each with \`run-file note Criteria <text>\`" '[$h]'
}
