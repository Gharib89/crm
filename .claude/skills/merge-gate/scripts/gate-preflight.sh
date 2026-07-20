#!/usr/bin/env bash
# Merge-gate step-1 evidence gather for one PR — ONE JSON blob: PR meta + body,
# labels, linked issues, CI check runs, a pushed-last-15-min flag, and the
# CodeRabbit threads left with NO disposition (unresolved + no non-CodeRabbit
# reply). Judgment stays with the reader; this only gathers.
#
#   scripts/gate-preflight.sh <pr>
#
# reviewThreads is GraphQL-only; gh's GraphQL can flake 401 once mid-session,
# so every call retries once.
set -uo pipefail
PR="${1:?usage: gate-preflight.sh <pr>}"

api() { gh api "$@" || { sleep 2; gh api "$@"; }; }

PRJ=$(api "repos/{owner}/{repo}/pulls/$PR") || exit 1
SHA=$(jq -r .head.sha <<<"$PRJ")
REPO=$(api "repos/{owner}/{repo}" | jq -r .full_name) || exit 1
OWNER=${REPO%/*}; NAME=${REPO#*/}

HEAD_DATE=$(api "repos/{owner}/{repo}/commits/$SHA" | jq -r .commit.committer.date) || exit 1
NOW=$(date -u +%s); PUSHED=$(date -d "$HEAD_DATE" +%s)
RECENT=$((NOW - PUSHED < 900))

CHECKS=$(api "repos/{owner}/{repo}/commits/$SHA/check-runs" --paginate \
  | jq -s '[.[].check_runs[] | {name, status, conclusion}]') || exit 1

# shellcheck disable=SC2016  # the $vars are GraphQL variables, not shell
THREADS=$(api graphql -f query='
  query($owner: String!, $name: String!, $pr: Int!) {
    repository(owner: $owner, name: $name) { pullRequest(number: $pr) {
      reviewThreads(first: 100) { nodes {
        isResolved path
        comments(first: 50) { nodes { author { login } body } } } } } } }' \
  -f owner="$OWNER" -f name="$NAME" -F pr="$PR" \
  | jq '[.data.repository.pullRequest.reviewThreads.nodes[]
    | select(.isResolved | not)
    | select(.comments.nodes[0].author.login == "coderabbitai")
    | select([.comments.nodes[1:][] | .author.login] | any(. != "coderabbitai") | not)
    | {path, excerpt: (.comments.nodes[0].body | split("\n")[0][0:120])}]') || exit 1

jq -n \
  --argjson pr "$(jq '{number, title, draft, mergeable_state,
      labels: [.labels[].name], head_sha: .head.sha, head_ref: .head.ref,
      base_ref: .base.ref, changed_files, body}' <<<"$PRJ")" \
  --argjson checks "$CHECKS" \
  --argjson recent "$([ "$RECENT" -eq 1 ] && echo true || echo false)" \
  --argjson undispositioned "$THREADS" \
  '{pr: $pr, pushed_last_15min: $recent, checks: $checks,
    coderabbit_undispositioned: $undispositioned,
    linked_issues: [$pr.body // "" | scan("(?i)closes #([0-9]+)") | .[0]]}'
