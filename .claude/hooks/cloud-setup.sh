#!/usr/bin/env bash
# .claude/hooks/cloud-setup.sh: this repo's cloud setup, written by
# setup-harness. A `SessionStart` hook in .claude/settings.json runs it in every
# Claude Code on the web session, on start and on resume, so every tool
# scripts/check.sh runs is installed before the session's first edit.
#
# stdout: one line, `harness cloud setup: ok` or `harness cloud setup: FAILED
#         <step>`, because a failed or timed-out SessionStart never stops the
#         session and this line is the session's only sign of it
# stderr: each step's own output
# exit:   0 ok, or not a cloud session · 1 a step failed
#
# Idempotent: a resumed session keeps its disk and runs this again, so a step
# whose done test passes is skipped and the second run is a fast no-op. Only
# routes the cloud sandbox's network passes belong here (apt, the package
# registries), or a host the environment's Custom allowlist admits (the harness
# profile's `Allowlist:`).
set -uo pipefail
[ "${CLAUDE_CODE_REMOTE:-}" = true ] || exit 0

# shellcheck disable=SC2016 # each step's own bash -c expands STEPS below
# >>> setup-harness configuration
# One step per line, run in order from the repo root:
#   <name>|<done test>|<command>
# The name and the done test carry no `|`; the command may. A step whose done
# test passes is skipped; one with no done test always runs, so its command
# must itself be a fast no-op when there is nothing to do (`uv sync --frozen`).
# After the command runs the done test must pass.
STEPS='shellcheck|command -v shellcheck|sudo apt-get -o DPkg::Lock::Timeout=120 install -y shellcheck
deps||if [ ! -x .venv/bin/python ]; then for p in python3.13 python3; do "$p" -c "import sys; sys.exit(sys.version_info < (3, 13))" 2>/dev/null && break; p=; done; [ -n "$p" ] && "$p" -m venv .venv; fi && .venv/bin/pip install -q -e ".[dev]"
pre-commit||.venv/bin/pre-commit install --install-hooks'
# <<< setup-harness configuration
: "${STEPS=}"

root=$(git -C "$(dirname "$0")" rev-parse --show-toplevel 2>/dev/null) \
  || { echo "harness cloud setup: FAILED repo root"; exit 1; }
cd "$root" || { echo "harness cloud setup: FAILED repo root"; exit 1; }

while IFS='|' read -r name done_test cmd; do
  [ -n "$name" ] || continue
  if [ -n "$done_test" ] && bash -c "$done_test" >/dev/null 2>&1 </dev/null; then continue; fi
  if ! bash -c "$cmd" >&2 </dev/null \
    || { [ -n "$done_test" ] && ! bash -c "$done_test" >/dev/null 2>&1 </dev/null; }; then
    echo "harness cloud setup: FAILED $name"
    exit 1
  fi
done <<EOF
$STEPS
EOF
echo "harness cloud setup: ok"
