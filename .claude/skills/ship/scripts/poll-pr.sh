#!/usr/bin/env bash
# Poll a PR's checks + reviews (ship phases 7/8) in a bounded foreground loop,
# then print ONE JSON summary. REST only — gh's GraphQL path flakes 401
# mid-session; every call retries once.
#
#   scripts/poll-pr.sh <pr> [--timeout 480] [--interval 20] [--await-review <login>]
#
# JSON: {head_sha, mergeable_state, checks: [{name,status,conclusion}],
#        reviews: [{login,state}]  (reviews keyed to the CURRENT head sha —
#        a review on an older commit does not count),
#        review_count_total, done, waited_s}
#
# done=true (exit 0): all check runs completed (and the awaited review landed),
# or mergeable_state=dirty (conflicted — merge-ref checks will never start, so
# waiting is pointless; resolve the conflict instead).
# done=false (exit 3): the window closed first — re-run to keep waiting.
set -u
PR="${1:?usage: poll-pr.sh <pr> [--timeout s] [--interval s] [--await-review login]}"
shift
TIMEOUT=480; INTERVAL=20; AWAIT=""
while [ $# -gt 0 ]; do
  case "$1" in
    --timeout)      TIMEOUT="$2";  shift 2 ;;
    --interval)     INTERVAL="$2"; shift 2 ;;
    --await-review) AWAIT="$2";    shift 2 ;;
    *) echo "unknown arg: $1" >&2; exit 2 ;;
  esac
done

api() { gh api "$@" 2>/dev/null || { sleep 2; gh api "$@"; }; }

START=$SECONDS
while :; do
  PRJ=$(api "repos/{owner}/{repo}/pulls/$PR") || exit 1
  SHA=$(jq -r .head.sha <<<"$PRJ")
  MSTATE=$(jq -r '.mergeable_state // "unknown"' <<<"$PRJ")
  CHECKS=$(api "repos/{owner}/{repo}/commits/$SHA/check-runs" --paginate \
    | jq -s '[.[].check_runs[] | {name, status, conclusion}]')
  ALL_REVIEWS=$(api "repos/{owner}/{repo}/pulls/$PR/reviews" --paginate | jq -s 'add // []')
  REVIEWS=$(jq --arg sha "$SHA" \
    '[.[] | select(.commit_id == $sha) | {login: .user.login, state}]' <<<"$ALL_REVIEWS")

  N=$(jq length <<<"$CHECKS")
  PENDING=$(jq '[.[] | select(.status != "completed")] | length' <<<"$CHECKS")
  DONE=0
  if [ "$MSTATE" = "dirty" ]; then
    DONE=1
  elif [ "$N" -gt 0 ] && [ "$PENDING" -eq 0 ]; then
    if [ -z "$AWAIT" ] || jq -e --arg l "$AWAIT" 'any(.[]; .login == $l)' <<<"$REVIEWS" >/dev/null; then
      DONE=1
    fi
  fi

  WAITED=$((SECONDS - START))
  if [ "$DONE" -eq 1 ] || [ "$WAITED" -ge "$TIMEOUT" ]; then
    jq -n --arg sha "$SHA" --arg ms "$MSTATE" \
      --argjson checks "$CHECKS" --argjson reviews "$REVIEWS" \
      --argjson total "$(jq length <<<"$ALL_REVIEWS")" \
      --argjson finished "$([ "$DONE" -eq 1 ] && echo true || echo false)" \
      --argjson waited "$WAITED" \
      '{head_sha: $sha, mergeable_state: $ms, checks: $checks, reviews: $reviews,
        review_count_total: $total, done: $finished, waited_s: $waited}'
    [ "$DONE" -eq 1 ] && exit 0 || exit 3
  fi
  sleep "$INTERVAL"
done
