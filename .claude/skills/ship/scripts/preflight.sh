#!/usr/bin/env bash
# Ship phase-0 pre-flight: is this issue actionable, or already in flight?
# Catches a manual re-run or an already-shipped issue: closed issue, existing
# open/merged PR, existing remote branch, or an agent-working claim.
#
#   scripts/preflight.sh <issue>
#
# Prints JSON {actionable, reasons: []}. Exit 0 = actionable, 1 = stop and report.
set -u
N="${1:?usage: preflight.sh <issue>}"

api() { gh api "$@" || { sleep 2; gh api "$@"; }; }

ISS=$(api "repos/{owner}/{repo}/issues/$N") || exit 1
REASONS=()
STATE=$(jq -r .state <<<"$ISS")
[ "$STATE" = "open" ] || REASONS+=("issue is $STATE")
jq -e '.pull_request' <<<"$ISS" >/dev/null && REASONS+=("#$N is a PR, not an issue")
jq -e '.labels[] | select(.name == "agent-working")' <<<"$ISS" >/dev/null \
  && REASONS+=("already claimed (agent-working)")

# PRs referencing the issue, via cross-referenced timeline events.
PRS=$(api "repos/{owner}/{repo}/issues/$N/timeline" --paginate \
  | jq -s '[.[][] | select(.event == "cross-referenced")
            | .source.issue | select(.pull_request != null)
            | {number, state, merged: (.pull_request.merged_at != null)}]
           | unique_by(.number)')
LIVE=$(jq -c '[.[] | select(.state == "open" or .merged) | .number]' <<<"$PRS")
[ "$LIVE" != "[]" ] && REASONS+=("existing open/merged PR(s): $LIVE")

# Remote branch already pushed for this issue (…-<issue> naming convention).
BR=$(git ls-remote --heads origin "*-$N" | awk '{print $2}' | sed 's|refs/heads/||' | paste -sd, -)
[ -n "$BR" ] && REASONS+=("remote branch exists: $BR")

if [ "${#REASONS[@]}" -eq 0 ]; then
  jq -n '{actionable: true, reasons: []}'
else
  printf '%s\n' "${REASONS[@]}" | jq -Rs '{actionable: false, reasons: split("\n")[:-1]}'
  exit 1
fi
