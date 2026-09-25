#!/usr/bin/env bash
# Phase 7: post one disposition inside one review thread, before resolving it.
#
#   reply-thread <pr> <thread-id> --body-file <path>   (ids from poll-pr's threads[])
#
# One call per thread, matching resolve-thread: the thread a reviewer opened is
# where the reviewer, and a human reading the round, look for the answer.
#
# stdout: {pr, thread, replied, url}; a failure adds `status`, the host's
#         status for the failed post, and `detail` where the adapter named one
# exit: 0 replied · 1 not replied (a thread the host cannot reach, detail
#       "unavailable", or an id no thread carries, detail "no such thread") · 2 usage
set -uo pipefail
source "$(dirname "${BASH_SOURCE[0]}")/_lib.sh" || { printf '{"error":"cannot source _lib.sh"}\n'; exit 2; }
usage='usage: reply-thread <pr> <thread-id> --body-file <path>'
ship_help "$usage" "$@"
[ -n "${1:-}" ] && [ -n "${2:-}" ] || ship_tooling "$usage"
pr=$1 thread=$2; shift 2
case $pr in -*) ship_tooling "$usage" ;; esac
case $thread in -*) ship_tooling "$usage" ;; esac
[ "${1:-}" = --body-file ] && [ -f "${2:-}" ] && [ -r "${2:-}" ] && [ $# -eq 2 ] || ship_tooling "$usage"
ship_load_host
# A failed call still says why: the adapter prints its own {replied:false, detail}
# and this keeps it, so "unavailable" reaches the run rather than a bare exit 1.
if out=$(host_pr_reply_thread "$pr" "$thread" "$2"); then
  jq --argjson pr "$pr" --arg t "$thread" '{pr: $pr, thread: $t} + .' <<<"$out"
else
  detail=$(jq -c . <<<"$out" 2>/dev/null) || detail=null
  jq -n --argjson pr "$pr" --arg t "$thread" --argjson d "${detail:-null}" \
    '{pr: $pr, thread: $t, replied: false, url: null, status: null} + ($d // {})'
  exit 1
fi
