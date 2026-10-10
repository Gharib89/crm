#!/usr/bin/env bash
# Local gate: every check this repo's CI runs, run locally before a PR opens.
# Written by setup-skills; owned by the repo, which is who edits it from here.
#
#   scripts/local-gate.sh [--small <node>] [--base <ref>]
#   --help or -h prints that usage line and exits 0, before any check runs.
#
# Contract (ship's local-gate contract, the same in every repo):
#   stdout: one JSON object, {"verdict","base","lane","gates":{<name>:<status>}}
#   stderr: a failing gate's last 40 log lines
#   exit:   0 every gate passed · 1 a gate failed · 2 tooling
#   gate status: pass | fail | deferred-to-ci | unavailable
#     deferred-to-ci: planned, CI proves this gate (Docker absent, other-OS leg)
#     unavailable:    unexpected, the gate could not ask its question (tool missing)
#   verdict: pass | fail | unavailable; fail wins over unavailable
#   `secrets` is required in every lane. Base defaults to origin/HEAD.
set -uo pipefail

small="" base=""
while [ $# -gt 0 ]; do
  case $1 in
    --small) [ $# -ge 2 ] || { printf '{"error":"--small needs a test node"}\n'; exit 2; }; small=$2; shift 2 ;;
    --base)  [ $# -ge 2 ] || { printf '{"error":"--base needs a ref"}\n'; exit 2; }; base=$2; shift 2 ;;
    -h|--help) echo 'usage: scripts/local-gate.sh [--small <node>] [--base <ref>]'; exit 0 ;;
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

gates='{}'
log=$(mktemp); trap 'rm -f "$log"' EXIT
# A gate written twice keeps the worse status, so no later write can mask a failure.
# shellcheck disable=SC2016 # jq's own $a and $b
worse='def worse($a; $b): [$a // "pass", $b] | max_by({"pass": 0, "deferred-to-ci": 1, "unavailable": 2, "fail": 3}[.]);'
put()  { gates=$(jq -c --arg k "$1" --arg v "$2" "$worse"' .[$k] = worse(.[$k]; $v)' <<<"$gates"); }
run()  { local name=$1; shift; if "$@" >"$log" 2>&1; then put "$name" pass; else put "$name" fail; tail -n 40 "$log" >&2; fi; }
mark() { put "$1" "$2"; }   # mark <name> deferred-to-ci|unavailable

# --- gates ---------------------------------------------------------------------
# One gate per CI leg, named as the leg is named in the profile's `## CI`.
# setup-skills fills this block from the repo's workflows; edit freely, keep the
# names in step with the profile.

# secrets: required in every lane. Wired to the scanner setup-skills detected;
# `unavailable` here stops the first attended run and names what to add.
if command -v gitleaks >/dev/null; then
  run secrets gitleaks detect --no-banner --redact --log-opts="$base..HEAD"
else
  mark secrets unavailable
fi

run deps  __DEPS__                       # every lane: a fresh worktree has no dependencies yet
if [ "$lane" = small ]; then
  run tests __RUNNER__ "$small"          # the one regression test proving the change
else
  run tests __RUNNER__
fi
# --- end gates -----------------------------------------------------------------

verdict=$(jq -r 'if any(.[]; . == "fail") then "fail" elif any(.[]; . == "unavailable") then "unavailable" else "pass" end' <<<"$gates")
case $verdict in pass) rc=0 ;; fail) rc=1 ;; *) rc=2 ;; esac
jq -cn --arg v "$verdict" --arg b "$base" --arg l "$lane" --argjson g "$gates" \
  '{verdict: $v, base: $b, lane: $l, gates: $g}'
exit $rc
