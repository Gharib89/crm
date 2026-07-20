#!/usr/bin/env bash
# Merge-gate step-6 exception: spend the ONE Copilot re-request — only when the
# gate significantly rewrote the PR. Guards both traps: refuses if the
# re-request was already spent (a second Copilot review exists) or one is
# pending, and verifies requested_reviewers actually populated afterwards
# (a bare HTTP 201 can silently no-op).
#
#   scripts/rerequest-copilot.sh <pr>
set -u
PR="${1:?usage: rerequest-copilot.sh <pr>}"
BOT="copilot-pull-request-reviewer[bot]"

api() { gh api "$@" || { sleep 2; gh api "$@"; }; }

COUNT=$(api "repos/{owner}/{repo}/pulls/$PR/reviews" --paginate \
  | jq -s --arg b "$BOT" '[add[]? | select(.user.login == $b)] | length')
if [ "$COUNT" -ge 2 ]; then
  echo "refused: $COUNT Copilot reviews exist — the one gate re-request was already spent" >&2
  exit 1
fi
if api "repos/{owner}/{repo}/pulls/$PR" \
   | jq -e --arg b "$BOT" '.requested_reviewers[]? | select(.login == $b)' >/dev/null; then
  echo "refused: a Copilot review is already pending" >&2
  exit 1
fi

echo "{\"reviewers\":[\"$BOT\"]}" \
  | api -X POST "repos/{owner}/{repo}/pulls/$PR/requested_reviewers" --input - >/dev/null

api "repos/{owner}/{repo}/pulls/$PR" \
  | jq -e --arg b "$BOT" '.requested_reviewers[]? | select(.login == $b)' >/dev/null \
  || { echo "FAILED: POST returned but requested_reviewers did not populate (the silent no-op trap)" >&2; exit 1; }
echo '{"requested": true}'
