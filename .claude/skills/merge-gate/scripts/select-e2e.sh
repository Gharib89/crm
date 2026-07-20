#!/usr/bin/env bash
# Merge-gate step-4 selection: map this branch's changed files to command
# groups, then list each group's live e2e test files and their @covers strings —
# candidates for `D365_E2E=1 pytest -m e2e <files>`. The reader confirms the
# touched verbs appear in the @covers strings before trusting a run. Exit 0
# always.
#
#   scripts/select-e2e.sh [base]      default base: origin/HEAD
set -u
BASE="${1:-origin/HEAD}"

# NB: not "GROUPS" — that's a bash special variable (the user's group IDs)
# which mapfile silently fails to overwrite.
mapfile -t CMD_GROUPS < <(git diff --name-only --diff-filter=ACMR "$BASE"...HEAD \
  | sed -nE 's#^crm/(commands|core)/([a-z0-9_]+)\.py$#\2#p; s#^crm/tests/e2e/test_([a-z0-9_]+)\.py$#\1#p' \
  | sort -u)
if [ "${#CMD_GROUPS[@]}" -eq 0 ]; then
  echo "no D365-touching changes vs $BASE"
  exit 0
fi

for g in "${CMD_GROUPS[@]}"; do
  echo "== group: $g =="
  FOUND=0
  for f in crm/tests/e2e/test_"$g"*.py; do
    [ -e "$f" ] || continue
    FOUND=1
    echo "$f"
    grep -h '@covers' "$f" | sed 's/^[[:space:]]*/  /'
  done
  [ "$FOUND" -eq 0 ] && echo "  (no e2e test file — check E2E_SKIP in crm/tests/e2e/coverage.py)"
done
