#!/usr/bin/env bash
# Resolve one review thread, after every thread carries a disposition.
#
#   resolve-thread <pr> <thread-id>     (ids come from poll-pr's threads[])
#
# stdout: {pr, thread, resolved}
# exit: 0 resolved · 1 not resolved · 2 usage
set -uo pipefail
source "$(dirname "${BASH_SOURCE[0]}")/_lib.sh" || { printf '{"error":"cannot source _lib.sh"}\n'; exit 2; }
usage='usage: resolve-thread <pr> <thread-id>'
ship_help "$usage" "$@"
[ -n "${1:-}" ] && [ -n "${2:-}" ] || ship_tooling "$usage"
pr=$1; thread=$2
case $pr in -*) ship_tooling "$usage" ;; esac
case $thread in -*) ship_tooling "$usage" ;; esac
[ $# -eq 2 ] || ship_tooling "unknown flag: $3"
ship_load_host
# A failed call keeps the adapter's reason where it named one ("no such
# thread"), in the same {error} shape a bare failure answers with.
out=$(host_pr_resolve_thread "$pr" "$thread") \
  || ship_fail "$(jq -r '.detail // empty' <<<"$out" 2>/dev/null | grep . || echo "resolve call failed")"
jq --argjson pr "$pr" --arg t "$thread" '{pr: $pr, thread: $t} + .' <<<"$out"
[ "$(jq -r .resolved <<<"$out")" = true ]
