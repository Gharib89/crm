#!/usr/bin/env bash
# Local gate over the harness: the repo's `check.sh full`, plus the gates only
# Ship needs. Written by setup-skills where the repo has a harness profile
# (docs/agents/harness.md); owned by the repo from here.
#
#   scripts/local-gate.sh [--small <node>] [--base <ref>]
#
# Contract (ship's local-gate contract, the same in every repo):
#   stdout: one JSON object, {"verdict","base","lane","gates":{<name>:<status>}}
#   stderr: a failing gate's last 40 log lines, and the last 40 lines of
#           check.sh's own stderr unless check.sh answered a clean pass
#   exit:   0 every gate passed · 1 a gate failed · 2 tooling
#   gate status: pass | fail | deferred-to-ci | unavailable
#   verdict: pass | fail | unavailable; fail wins over unavailable
#   `secrets` is required in every lane. Base defaults to origin/HEAD.
#
# check.sh owns every check it runs, each one a gate of the same name here. This
# file owns only what check.sh cannot know: `secrets` over base..HEAD, `deps`,
# checks relative to the base, the small-lane node and `deferred-to-ci` marks.
# Bash 3.2 plus jq, so it runs on a stock macOS bash.
set -uo pipefail

small="" base=""
while [ $# -gt 0 ]; do
  case $1 in
    --small) [ $# -ge 2 ] || { printf '{"error":"--small needs a test node"}\n'; exit 2; }; small=$2; shift 2 ;;
    --base)  [ $# -ge 2 ] || { printf '{"error":"--base needs a ref"}\n'; exit 2; }; base=$2; shift 2 ;;
    *) printf '{"error":"unknown flag: %s"}\n' "$1"; exit 2 ;;
  esac
done
command -v jq >/dev/null || { echo '{"error":"jq not installed"}'; exit 2; }
cd "$(git rev-parse --show-toplevel 2>/dev/null)" || { echo '{"error":"not inside a git checkout"}'; exit 2; }
if [ -z "$base" ]; then
  base=$(git symbolic-ref -q refs/remotes/origin/HEAD 2>/dev/null) \
    || { echo '{"error":"cannot resolve origin/HEAD; run git remote set-head origin -a or pass --base"}'; exit 2; }
  base=${base#refs/remotes/}
fi
# gitleaks given a range it cannot resolve scans nothing and exits 0, so an
# unresolvable base would read as a `secrets` pass.
git rev-parse --verify -q "$base^{commit}" >/dev/null \
  || { jq -cn --arg b "$base" '{error: "base \($b) is not a commit; fetch it or pass --base <ref>"}'; exit 2; }
lane=full; [ -z "$small" ] || lane=small

gates='{}' checks='{}'
log=$(mktemp) err=$(mktemp); trap 'rm -f "$log" "$err"' EXIT
put()  { gates=$(jq -c --arg k "$1" --arg v "$2" '. + {($k): $v}' <<<"$gates"); }
run()  { local name=$1; shift; if "$@" >"$log" 2>&1; then put "$name" pass; else put "$name" fail; tail -n 40 "$log" >&2; fi; }
mark() { put "$1" "$2"; }   # mark <name> deferred-to-ci|unavailable

# --- gates ---------------------------------------------------------------------
# secrets: required in every lane. Wired to the scanner setup-skills detected;
# `unavailable` here stops the first attended run and names what to add.
if command -v gitleaks >/dev/null; then
  run secrets gitleaks detect --no-banner --redact --log-opts="$base..HEAD"
else
  mark secrets unavailable
fi

run deps  __DEPS__                       # every lane: a fresh worktree has no dependencies yet
if [ "$lane" = small ]; then
  run tests __RUNNER__ "$small"          # the one node, run directly: check.sh has no rung for it
else
  # No CHECK_DEADLINE: `full` is measured only, and a deadline would have
  # check.sh skip whatever it had not reached.
  (unset CHECK_DEADLINE; exec __CHECK__ full) >"$log" 2>"$err"; rc=$?
  # 0 to 3 all carry the one JSON line (2 is a check unavailable, 3 over
  # budget), so an unavailable tool still names its own check; a stdout outside
  # the contract (the usage path, not a git repo) leaves nothing to map, which
  # the jq guard catches. A status outside the gate vocabulary reads as
  # unavailable.
  if [ "$rc" -le 3 ] && parsed=$(jq -sce 'select(length == 1) | .[0].checks | objects
      | map_values(if . == "skipped" then "pass" elif . == "pass" or . == "fail" or . == "unavailable" then . else "unavailable" end)' \
      "$log" 2>/dev/null); then
    checks=$parsed
    [ "$rc" -eq 0 ] || tail -n 40 "$err" >&2
  else
    mark check unavailable
    tail -n 40 "$err" >&2
  fi
fi

# The repo's Ship-only gates go here: checks relative to "$base", and
# `mark <CI leg> deferred-to-ci` for what only CI can prove.
# --- end gates -----------------------------------------------------------------

# A name both report keeps the worse status, so neither side can mask a failure.
gates=$(jq -c --argjson c "$checks" '
  def rank: {"pass": 0, "deferred-to-ci": 1, "unavailable": 2, "fail": 3}[.];
  reduce ($c | to_entries[]) as $e (.; .[$e.key] = ([.[$e.key] // "pass", $e.value] | max_by(rank)))' <<<"$gates")
verdict=$(jq -r 'if any(.[]; . == "fail") then "fail" elif any(.[]; . == "unavailable") then "unavailable" else "pass" end' <<<"$gates")
case $verdict in pass) rc=0 ;; fail) rc=1 ;; *) rc=2 ;; esac
jq -cn --arg v "$verdict" --arg b "$base" --arg l "$lane" --argjson g "$gates" \
  '{verdict: $v, base: $b, lane: $l, gates: $g}'
exit $rc
