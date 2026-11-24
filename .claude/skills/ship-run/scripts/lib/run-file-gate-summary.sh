#!/usr/bin/env bash
# Run-file mechanic: the merge-gate summary. Sourced by run-file-verbs-gate.sh for
# `gate summary` alone, after ../run-file.sh has sourced gate_clean; never run.
# shellcheck disable=SC2154 # shares the globals of run-file.sh (file, head)

# The merge-gate summary's two gate-derived lines, from the clean gate's own
# evaluation of the PR's head: the final `ci-wait` JSON the run saved beside the
# Run file, and the head `read-pr` returns. Neither is the caller's to supply,
# so the summary cannot claim what the gate never read (#894).
gate_summary() { # gate_summary <pr>, with file already parsed
  local pr=$1 ci_file pull answer rc lines entries recorded=false
  ci_file="$(dirname "$file")/ci-wait.json"
  # Exit 2, not the exit 1 of a held gate: a read that failed is no verdict.
  [ -f "$ci_file" ] || ship_tooling "no saved ci-wait answer at $ci_file: run \`ci-wait $pr\` and save its output there, then re-run"
  pull=$("$SHIP_SCRIPTS/read-pr.sh" "$pr" 2>/dev/null) && head=$(jq -r '.head_sha // empty' <<<"$pull" 2>/dev/null) && [ -n "$head" ] \
    || ship_tooling "cannot read the head of PR $pr: run \`read-pr $pr\` and fix what it reports, then re-run"
  answer=$(gate_clean "$ci_file" 2>/dev/null)
  rc=$?
  [ "$rc" -le 1 ] || ship_tooling "$(jq -r '.error // "cannot evaluate clean gate evidence"' <<<"$answer")"
  # The same unfenced Run file text the gate read: a fenced example is no entry.
  lines=$(awk "$SHIP_AWK_FENCE"'
    { if (!ship_fence($0)) print }
    END { if (_fenced) exit 1 }' "$file") || ship_tooling "cannot read Run file evidence at $file"
  # With no `## Criteria` the gate read none, so "none" would be a claim it never
  # checked: the line says so (a numbered issue's criteria also hold the gate).
  criteria_recorded "$lines" && recorded=true
  # Each entry's text, so an unmet criterion reads as the requirement a human is
  # asked to waive and not only its number.
  entries=$(jq -Rn '[inputs | sub("[ \\t\\r]+$"; "")] | reduce .[] as $l ({in: false, m: {}};
      if ($l | startswith("## ")) then .in = ($l == "## Criteria")
      elif .in and ($l | test("^- [1-9][0-9]*: ")) then .m[$l | sub("^- "; "") | sub(":.*"; "")] = ($l | sub("^- [1-9][0-9]*: "; ""))
      else . end) | .m' <<<"$lines") || ship_tooling "cannot read the criteria entries"
  jq --arg head "$head" --argjson recorded "$recorded" --argjson entries "$entries" '
    ([.held_by[] | select(startswith("criterion "))
      | capture("^criterion (?<n>[0-9]+): (?<rest>.*)$") | "criterion " + .n + " (" + ($entries[.n] // "?") + "): " + .rest]) as $unmet
    | {clean, head: $head, held_by,
       criteria_recorded: $recorded,
       unmet_criteria: $unmet,
       unmet_criteria_line: (if $unmet != [] then ($unmet | join("; "))
         elif $recorded then "none"
         else "unverified: the Run file has no ## Criteria, so the gate read none" end),
       clean_gate_held_by_line: (if .clean then "none" else (.held_by | join("; ")) end)}' <<<"$answer"
  # What `gate clean` writes for a held gate: the reasons, on stderr.
  [ "$rc" -eq 0 ] || jq -r '.held_by[]' <<<"$answer" | tail -n 40 >&2
  return "$rc"
}
