#!/usr/bin/env bash
# Merge-gate sweep queue: every open, non-draft PR carrying neither gate-passed
# nor gate-failed, oldest first. Prints JSON [{number, title, created_at, author}].
#
#   scripts/sweep-list.sh
set -uo pipefail

api() { gh api "$@" || { sleep 2; gh api "$@"; }; }

api "repos/{owner}/{repo}/pulls?state=open&per_page=100" --paginate | jq -s '
  add
  | map(select(.draft == false))
  | map(select([.labels[].name] | any(. == "gate-passed" or . == "gate-failed") | not))
  | sort_by(.created_at)
  | map({number, title, created_at, author: .user.login})'
