#!/usr/bin/env bash
# Ship phase-1 claim: ready-for-agent → agent-working + a claim comment, so a
# concurrent run can't double-pick the issue. Idempotent — an existing
# agent-working label is a no-op success.
#
#   scripts/claim.sh <issue> [comment]
set -uo pipefail
N="${1:?usage: claim.sh <issue> [comment]}"
BODY="${2:-🤖 Claimed by a ship run — implementation in progress.}"

api() { gh api "$@" || { sleep 2; gh api "$@"; }; }

LABELS=$(api "repos/{owner}/{repo}/issues/$N") || exit 1
if jq -e '.labels[] | select(.name == "agent-working")' <<<"$LABELS" >/dev/null; then
  echo '{"claimed": true, "already": true}'
  exit 0
fi

# A false success here would let a concurrent run double-pick — fail loudly.
api -X POST "repos/{owner}/{repo}/issues/$N/labels" -f 'labels[]=agent-working' >/dev/null || exit 1
api -X DELETE "repos/{owner}/{repo}/issues/$N/labels/ready-for-agent" >/dev/null 2>&1 || true
api -X POST "repos/{owner}/{repo}/issues/$N/comments" -f body="$BODY" >/dev/null || exit 1
echo '{"claimed": true, "already": false}'
