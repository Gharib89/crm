#!/usr/bin/env bash
# .claude/hooks/check-hook.sh: Claude Code hooks over the check entry point,
# written by setup-harness.
#
#   check-hook.sh edit   PostToolUse, matcher Edit|Write: check.sh edit <file>
#   check-hook.sh turn   Stop: check.sh turn, skipped when the working tree has
#                        not changed since the last check
#
# Claude Code sees only exit 2, so a failing check (check.sh exit 1) becomes
# exit 2 with its output on stderr, capped under the 10k-character limit past
# which Claude Code swaps it for a file path. A check that is unavailable
# (exit 2) or over budget (exit 3) never blocks: it is a systemMessage to the
# human, and the next rung still catches defects.
#
# Bash 3.2 and no jq. The budgets below are written from the harness profile's
# `## Budgets`; each hook entry's `timeout` in .claude/settings.json is the
# budget plus max(10 s, budget / 4), so this wrapper reports before Claude
# Code discards a timed-out hook's output.
set -uo pipefail

# >>> setup-harness configuration
CHECK=scripts/check.sh
EDIT_BUDGET=5
TURN_BUDGET=60
# <<< setup-harness configuration

rung=${1:-}
case $rung in
  edit) budget=$EDIT_BUDGET ;;
  turn) budget=$TURN_BUDGET ;;
  *) echo "usage: check-hook.sh edit | turn" >&2; exit 2 ;;
esac
input=$(cat)
root=$(git rev-parse --show-toplevel 2>/dev/null) || exit 0
cd "$root" || exit 0
tmp='' errf=''
trap 'rm -f "$tmp" "$errf"' EXIT

say() { # <message>: a systemMessage for the human; never blocks
  local m=${1//\\/\\\\}
  printf '{"systemMessage":"%s"}\n' "${m//\"/\\\"}"
}

# The names of the checks carrying <status> in a check.sh JSON line.
named() { printf '%s' "$2" | sed 's/.*"checks":{//' | grep -o "\"[^\"]*\":\"$1\"" | sed 's/":".*//; s/^"//' | paste -sd, - | sed 's/,/, /g'; }

if [ "$rung" = edit ]; then
  # The one string field Claude Code sends that ends in `file_path`; JSON
  # escapes in a path are rare enough that only \" and \\ are undone.
  file=$(printf '%s' "$input" | sed -nE 's/.*"file_path"[[:space:]]*:[[:space:]]*"(([^"\\]|\\.)*)".*/\1/p' | sed 's/\\"/"/g; s/\\\\/\\/g')
  [ -n "$file" ] || exit 0
  set -- "$file"
else
  # A throwaway index seeded from the real one, so the fingerprint costs
  # milliseconds and the real index is never touched. `git add` still writes
  # each changed file's blob to the object store, where gc reclaims it.
  state=$(git rev-parse --git-path setup-harness-turn)
  tmp=$(mktemp) || exit 0
  cp "$(git rev-parse --git-path index)" "$tmp" 2>/dev/null || rm -f "$tmp"
  fp=$(GIT_INDEX_FILE=$tmp git add -A 2>/dev/null && GIT_INDEX_FILE=$tmp git write-tree 2>/dev/null)
  last=$(cat "$state" 2>/dev/null)
  # Unchanged since the last check, continuation or not: re-running would
  # only repeat its answer, and blocking again would loop Claude on a failure
  # it has not touched.
  if [ -n "$fp" ] && [ "${last%% *}" = "$fp" ]; then
    [ "${last#* }" = pass ] || say "harness: turn rung still ${last#* }, unchanged since the last check"
    exit 0
  fi
  set --
fi

errf=$(mktemp) || exit 0
out=$(CHECK_DEADLINE=$(( $(date +%s) + budget )) "./$CHECK" "$rung" "$@" 2>"$errf")
code=$?
# Only a pass or a failure is the tree's answer: an over-budget or unavailable
# rung may answer differently on the same tree, so it is checked again.
if [ "$rung" = turn ] && [ -n "$fp" ]; then
  case $code in
    0) printf '%s pass\n' "$fp" > "$state" ;;
    1) printf '%s failing\n' "$fp" > "$state" ;;
  esac
fi
case $code in
  0) exit 0 ;;
  1) head -c 9000 "$errf" >&2; exit 2 ;;
  3) n=$(named over-budget "$out")
     if [ -n "$n" ]; then say "harness: $rung rung over its $budget s budget: $n"
     else say "harness: $rung rung over its $budget s budget before $(named skipped "$out") started"; fi ;;
  *) n=$(named unavailable "$out"); say "harness: $rung rung unavailable: ${n:-check.sh exited $code}" ;;
esac
exit 0
