#!/usr/bin/env bash
# Local gate over the harness: the repo's `check.sh full`, plus the gates only
# Ship needs. Written by setup-skills where the repo has a harness profile
# (docs/agents/harness.md); owned by the repo from here.
#
#   scripts/local-gate.sh [--small <node>] [--base <ref>]
#   --help or -h prints that usage line and exits 0, before any check runs.
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
#
# --small takes a pytest node id (crm/tests/test_x.py::test_y), or, for a
# docs-class change, the path of the changed document, which runs `docs`.
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
# A small node is a pytest node, or a docs-class change's existing document.
small_gate=""
case $small in
  '') ;;
  *::*|*/test_*.py) small_gate=tests ;;
  *.md|docs/*|mkdocs.yml) [ -e "$small" ] && small_gate=docs ;;
esac
[ -z "$small" ] || [ -n "$small_gate" ] \
  || { printf '{"error":"--small %s is neither a pytest node nor a doc"}\n' "$small"; exit 2; }

gates='{}' checks='{}'
log=$(mktemp) err=$(mktemp); trap 'rm -f "$log" "$err"' EXIT
put()  { gates=$(jq -c --arg k "$1" --arg v "$2" '. + {($k): $v}' <<<"$gates"); }
run()  { local name=$1; shift; if "$@" >"$log" 2>&1; then put "$name" pass; else put "$name" fail; tail -n 40 "$log" >&2; fi; }
mark() { put "$1" "$2"; }   # mark <name> deferred-to-ci|unavailable

# A worktree's own .venv is optional: use this checkout's, else the main
# checkout's (the parent of the common git dir), with PYTHONPATH on this tree,
# the same resolution check.sh makes.
venv=""
main=$(cd "$(git rev-parse --git-common-dir)/.." && pwd)
for d in "$PWD/.venv" "$main/.venv"; do
  [ -x "$d/bin/python" ] && { venv=$d; break; }
done
py="$venv/bin/python"
export PYTHONPATH=$PWD

# --- gates ---------------------------------------------------------------------
# secrets: required in every lane. gitleaks from PATH, else the venv's, where
# the cloud bootstrap installs it.
gitleaks=$(command -v gitleaks || { [ -x "$venv/bin/gitleaks" ] && echo "$venv/bin/gitleaks"; })
if [ -n "$gitleaks" ]; then
  run secrets "$gitleaks" detect --no-banner --redact --log-opts="$base..HEAD"
else
  mark secrets unavailable
fi

if [ -z "$venv" ]; then                  # every lane: a fresh worktree has no dependencies yet
  echo "deps: no .venv here or at $main; create it there: python3.13 -m venv .venv && .venv/bin/pip install -e '.[dev,docs]'" >&2
  mark deps unavailable
  [ "$lane" = full ] || mark "$small_gate" unavailable
else
  run deps "$py" -c 'import crm, pytest, ruff, mkdocs'
fi
if [ "$lane" = small ]; then
  # The one node, run directly: check.sh has no rung for it.
  if [ -n "$venv" ]; then
    if [ "$small_gate" = tests ]; then
      run tests "$py" -m pytest -q "$small"
    else
      run docs "$py" -m mkdocs build --strict
    fi
  fi
else
  # No CHECK_DEADLINE: `full` is measured only, and a deadline would have
  # check.sh skip whatever it had not reached. codespell is a pre-commit-only
  # typo check that must never block a merge, so the runner skips it here; the
  # commit rung and a plain `check.sh full` still run it.
  (unset CHECK_DEADLINE; SKIP=codespell exec scripts/check.sh full) >"$log" 2>"$err"; rc=$?
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

  # The pac solution pack/extract e2e (CI `test` leg); CI provisions pac.
  if command -v pac >/dev/null && [ -n "$venv" ]; then
    run pac-e2e "$py" -m pytest -q -m e2e crm/tests/e2e/test_solution_packager_e2e.py
  else
    mark pac-e2e deferred-to-ci
  fi
  mark package deferred-to-ci             # PyInstaller build + smoke, ubuntu + windows
  mark bump-guard deferred-to-ci          # reads the PR title, which exists only once the PR does
fi
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
