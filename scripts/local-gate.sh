#!/usr/bin/env bash
# Local gate: every check this repo's CI runs, run locally before a PR opens.
# Written by setup-skills; owned by the repo, which is who edits it from here.
#
#   scripts/local-gate.sh [--small <node>] [--base <ref>]
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
#
# --small takes a pytest node id (crm/tests/test_x.py::test_y), or, for a
# docs-class change, the path of the changed document, which runs `docs`.
set -uo pipefail

small="" base=""
while [ $# -gt 0 ]; do
  case $1 in
    --small) [ $# -ge 2 ] || { printf '{"error":"--small needs a test node"}\n'; exit 2; }; small=$2; shift 2 ;;
    --base)  [ $# -ge 2 ] || { printf '{"error":"--base needs a ref"}\n'; exit 2; }; base=$2; shift 2 ;;
    *) printf '{"error":"unknown flag: %s"}\n' "$1"; exit 2 ;;
  esac
done
[ "${BASH_VERSINFO[0]}" -ge 4 ] || { echo '{"error":"bash 4+ required (associative arrays); macOS: brew install bash"}'; exit 2; }
command -v jq >/dev/null || { echo '{"error":"jq not installed"}'; exit 2; }
cd "$(git rev-parse --show-toplevel 2>/dev/null)" || { echo '{"error":"not inside a git checkout"}'; exit 2; }
if [ -z "$base" ]; then
  base=$(git symbolic-ref -q refs/remotes/origin/HEAD 2>/dev/null) \
    || { echo '{"error":"cannot resolve origin/HEAD; run git remote set-head origin -a or pass --base"}'; exit 2; }
  base=${base#refs/remotes/}
fi
lane=full; [ -z "$small" ] || lane=small

declare -A gates
log=$(mktemp); trap 'rm -f "$log"' EXIT
run()  { local name=$1; shift; if "$@" >"$log" 2>&1; then gates[$name]=pass; else gates[$name]=fail; tail -n 40 "$log" >&2; fi; }
mark() { gates[$1]=$2; }   # mark <name> deferred-to-ci|unavailable

# --- gates ---------------------------------------------------------------------
# Gate names are the tools of each CI leg in `## CI` (ci.yml `lint` runs ruff,
# ruff-format, pyright, semgrep, actionlint and zizmor; `test` runs pytest and
# the pac e2e; docs.yml runs `docs`). The windows-latest matrix halves of `test`
# and `package` have no local mirror; CI proves them.

# secrets: required in every lane.
if command -v gitleaks >/dev/null; then
  run secrets gitleaks detect --no-banner --redact --log-opts="$base..HEAD"
else
  mark secrets unavailable
fi

# deps: a sibling worktree has no .venv, and installing one there would repoint
# the shared editable install, so use this checkout's .venv, else the main
# checkout's (the parent of the common git dir), with PYTHONPATH on this tree.
venv=""
main=$(cd "$(git rev-parse --git-common-dir)/.." && pwd)
for d in "$PWD/.venv" "$main/.venv"; do
  [ -x "$d/bin/python" ] && { venv=$d; break; }
done
py="$venv/bin/python"
export PYTHONPATH=$PWD
# A small node that is not a pytest node is a docs-class change's document.
small_gate="test"
case $small in ''|*::*|*/test_*.py) ;; *) small_gate=docs ;; esac
venv_gates=(ruff ruff-format pyright test docs)
[ "$lane" = small ] && venv_gates=("$small_gate")
if [ -z "$venv" ]; then
  echo "deps: no .venv here or at $main; create it there: python3.13 -m venv .venv && .venv/bin/pip install -e '.[dev,docs]'" >&2
  mark deps unavailable
  for g in "${venv_gates[@]}"; do mark "$g" unavailable; done
else
  run deps "$py" -c 'import crm, pytest, ruff, mkdocs'
fi

# semgrep: house-convention rules, pinned like CI's lint job (kept out of the venv).
run semgrep uvx semgrep==1.169.0 scan --config ci/semgrep-rules.yml --error --metrics off

if [ "$lane" = small ]; then
  if [ -n "$venv" ]; then
    if [ "$small_gate" = test ]; then
      run test "$py" -m pytest -q "$small"
    else
      run docs "$py" -m mkdocs build --strict
    fi
  fi
else
  if [ -n "$venv" ]; then
    # The pyright floor comes from pyrightconfig.json, never hardcoded.
    pyver=$("$py" -c "import json; print(json.load(open('pyrightconfig.json'))['pythonVersion'])")
    run ruff        "$py" -m ruff check .
    run ruff-format "$py" -m ruff format --check .
    run pyright     "$venv/bin/pyright" --pythonpath "$py" --pythonversion "$pyver"
    run test        "$py" -m pytest -q
    run docs        "$py" -m mkdocs build --strict
  fi
  # The pac solution pack/extract e2e (CI `test` leg); CI provisions pac.
  if command -v pac >/dev/null && [ -n "$venv" ]; then
    run pac-e2e "$py" -m pytest -q -m e2e crm/tests/e2e/test_solution_packager_e2e.py
  else
    mark pac-e2e deferred-to-ci
  fi
  # Workflow linters only when .github/ changed (CI's lint job runs them always).
  if ! git diff --quiet "$base"...HEAD -- .github/ 2>/dev/null; then
    if command -v actionlint >/dev/null; then run actionlint actionlint; else mark actionlint unavailable; fi
    run zizmor uvx zizmor==1.26.1 .       # version lockstep with CI + pre-commit
  fi
  mark package deferred-to-ci             # PyInstaller build + smoke, ubuntu + windows
  mark bump-guard deferred-to-ci          # reads the PR title, which exists only once the PR does
fi
# --- end gates -----------------------------------------------------------------

verdict=pass
for s in "${gates[@]}"; do
  case $s in
    fail) verdict=fail ;;
    unavailable) [ "$verdict" = fail ] || verdict=unavailable ;;
  esac
done
case $verdict in pass) rc=0 ;; fail) rc=1 ;; *) rc=2 ;; esac

for k in "${!gates[@]}"; do printf '%s\t%s\n' "$k" "${gates[$k]}"; done \
  | jq -Rs --arg v "$verdict" --arg b "$base" --arg l "$lane" \
      '{verdict: $v, base: $b, lane: $l,
        gates: (split("\n") | map(select(. != "") | split("\t") | {(.[0]): .[1]}) | add // {})}'
exit $rc
