#!/usr/bin/env bash
# Local gate (ship phase 5): run the checks CI runs, print one line per check
# plus the failing output only. Exit 0 = all green.
#
#   scripts/local-gate.sh                      # full gate
#   scripts/local-gate.sh --small <node-id>    # small lane: security scan + the
#                                              # one proving pytest node
#
# Works from the main checkout or a worktree: uses ./.venv if present, else set
# PYTHON to the main venv's python (PYTHONPATH is exported for you — a worktree
# has no .venv, and the main venv's editable install points at the main
# checkout otherwise).
set -u

ROOT=$(git rev-parse --show-toplevel) || exit 1
cd "$ROOT" || exit 1

if [ -x .venv/bin/python ]; then
  PY="$ROOT/.venv/bin/python"
else
  PY="${PYTHON:?this checkout has no .venv — set PYTHON to the main venv python}"
  export PYTHONPATH="$ROOT"
fi

SMALL_NODE=""
if [ "${1:-}" = "--small" ]; then
  SMALL_NODE="${2:?--small needs a pytest node id}"
fi

# The pyright floor comes from pyrightconfig.json, never hardcoded — a newer
# version would mask runtime ImportErrors.
PYVER=$("$PY" -c "import json; print(json.load(open('pyrightconfig.json'))['pythonVersion'])")

NAMES=(); RCS=(); LOGS=()
run() { # run <name> <cmd...>
  local name="$1"; shift
  local log rc
  log=$(mktemp)
  "$@" >"$log" 2>&1
  rc=$?
  NAMES+=("$name"); RCS+=("$rc"); LOGS+=("$log")
  if [ "$rc" -eq 0 ]; then echo "PASS $name"; else echo "FAIL $name (exit $rc)"; fi
}

if [ -n "$SMALL_NODE" ]; then
  run "semgrep" uvx semgrep scan --config ci/semgrep-rules.yml --error --metrics off
  run "pytest node" "$PY" -m pytest -q "$SMALL_NODE"
else
  run "pytest" "$PY" -m pytest -q
  run "ruff check" "$PY" -m ruff check .
  run "ruff format" "$PY" -m ruff format --check .
  run "pyright" pyright --pythonpath "$PY" --pythonversion "$PYVER"
  run "semgrep" uvx semgrep scan --config ci/semgrep-rules.yml --error --metrics off
  run "mkdocs" "$PY" -m mkdocs build --strict
  # Workflow linters only when workflows changed (mirrors CI's lint job scope).
  BASE=$(git symbolic-ref -q --short refs/remotes/origin/HEAD || echo origin/main)
  if ! git diff --quiet "$BASE"...HEAD -- .github/ 2>/dev/null; then
    run "actionlint" actionlint            # needs shellcheck on PATH for run-block checks
    run "zizmor" uvx zizmor==1.26.1 .      # version lockstep with CI + pre-commit
  fi
fi

FAIL=0
for i in "${!NAMES[@]}"; do
  if [ "${RCS[$i]}" -ne 0 ]; then
    FAIL=1
    echo
    echo "---- ${NAMES[$i]} — last 40 lines ----"
    tail -n 40 "${LOGS[$i]}"
  fi
  rm -f "${LOGS[$i]}"
done
exit "$FAIL"
