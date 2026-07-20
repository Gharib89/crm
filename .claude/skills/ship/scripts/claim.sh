#!/usr/bin/env bash
# Ship phase-1 claim: ready-for-agent → agent-working + a claim comment, so a
# concurrent run can't double-pick the issue. Idempotent — an existing
# agent-working label is a no-op success.
#
#   scripts/claim.sh <issue> [comment]
set -u
N="${1:?usage: claim.sh <issue> [comment]}"
BODY="${2:-🤖 Claimed by a ship run — implementation in progress.}"

api() { gh api "$@" || { sleep 2; gh api "$@"; }; }

if api "repos/{owner}/{repo}/issues/$N" \
   | jq -e '.labels[] | select(.name == "agent-working")' >/dev/null; then
  echo '{"claimed": true, "already": true}'
  exit 0
fi

api -X POST "repos/{owner}/{repo}/issues/$N/labels" -f 'labels[]=agent-working' >/dev/null
api -X DELETE "repos/{owner}/{repo}/issues/$N/labels/ready-for-agent" >/dev/null 2>&1 || true
api -X POST "repos/{owner}/{repo}/issues/$N/comments" -f body="$BODY" >/dev/null
echo '{"claimed": true, "already": false}'
