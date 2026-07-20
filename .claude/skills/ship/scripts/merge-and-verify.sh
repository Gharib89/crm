#!/usr/bin/env bash
# Merge mechanics (ship phase 9 — run only AFTER the human says "merge"):
# squash-merge via REST, re-verify merged state, delete the remote branch, and
# confirm the linked issue closed (closing it if the Closes-keyword didn't).
# REST throughout — gh's GraphQL merge path flakes 401; every call retries once.
#
#   scripts/merge-and-verify.sh <pr> [issue]
#
# The squash subject is "<PR title> (#<pr>)" — release tooling reads it, so the
# PR title must already be the Conventional-Commit line.
set -u
PR="${1:?usage: merge-and-verify.sh <pr> [issue]}"
ISSUE="${2:-}"

api() { gh api "$@" || { sleep 2; gh api "$@"; }; }

PRJ=$(api "repos/{owner}/{repo}/pulls/$PR") || exit 1
TITLE=$(jq -r .title <<<"$PRJ")
BRANCH=$(jq -r .head.ref <<<"$PRJ")

api -X PUT "repos/{owner}/{repo}/pulls/$PR/merge" \
  -f merge_method=squash -f commit_title="$TITLE (#$PR)" >/dev/null \
  || { echo '{"merged": false, "error": "merge call failed"}'; exit 1; }

# Never assume the command took — verify merged state before reporting done.
MERGED=$(api "repos/{owner}/{repo}/pulls/$PR" | jq -r .merged)
[ "$MERGED" = "true" ] \
  || { echo '{"merged": false, "error": "post-merge verify failed: PR not merged"}'; exit 1; }

api -X DELETE "repos/{owner}/{repo}/git/refs/heads/$BRANCH" >/dev/null 2>&1 || true

ISSUE_STATE=null
if [ -n "$ISSUE" ]; then
  sleep 3  # give the Closes-keyword automation a beat
  STATE=$(api "repos/{owner}/{repo}/issues/$ISSUE" | jq -r .state)
  if [ "$STATE" = "open" ]; then
    api -X PATCH "repos/{owner}/{repo}/issues/$ISSUE" \
      -f state=closed -f state_reason=completed >/dev/null
    STATE=$(api "repos/{owner}/{repo}/issues/$ISSUE" | jq -r .state)
  fi
  ISSUE_STATE="\"$STATE\""
fi

jq -n --arg subject "$TITLE (#$PR)" --arg branch "$BRANCH" --argjson issue_state "$ISSUE_STATE" \
  '{merged: true, squash_subject: $subject, remote_branch_deleted: $branch, issue_state: $issue_state}'
