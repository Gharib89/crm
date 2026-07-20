#!/usr/bin/env bash
# Drift-checklist item 6 evidence: org-fingerprint scan over the files this
# branch changed. Prints candidate hits for the reader to CLASSIFY — platform
# constants (FormXml classids) are legal and stay; org-fingerprint GUIDs, real
# hostnames, or tenant IDs are fails. Exit 0 always (hits are not failures
# until classified).
#
#   scripts/genericity-scan.sh [base]      default base: origin/HEAD
set -u
BASE="${1:-origin/HEAD}"

mapfile -t FILES < <(git diff --name-only --diff-filter=ACMR "$BASE"...HEAD)
if [ "${#FILES[@]}" -eq 0 ]; then
  echo "no changed files vs $BASE"
  exit 0
fi

echo "== moce =="
git grep -ni moce -- "${FILES[@]}" || echo "(none)"
echo
echo "== GUID-shaped =="
grep -EniH '[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}' \
  -- "${FILES[@]}" 2>/dev/null || echo "(none)"
