#!/usr/bin/env bash
# Build the crm onedir bundle on Linux/macOS.
# Usage: ./scripts/build.sh
set -euo pipefail

REPO_ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
cd "$REPO_ROOT"

if [ ! -d ".venv" ]; then
    python3 -m venv .venv
fi
# shellcheck disable=SC1091
source .venv/bin/activate

pip install --quiet --upgrade pip
pip install --quiet -e ".[dev]"

rm -rf build dist

pyinstaller crm.spec

echo
echo "Built: $REPO_ROOT/dist/crm/  (onedir bundle; launcher: dist/crm/crm)"
"$REPO_ROOT/dist/crm/crm" --version
# Both shipped skills ride in the bundle (crm.spec datas, #1062).
for skill in skills migration-skill; do
  test -f "$REPO_ROOT/dist/crm/_internal/crm/$skill/SKILL.md" || { echo "missing bundled $skill" >&2; exit 1; }
done
# tree-sitter and its grammars, the engine of `crm inventory convert` (ADR 0034).
convert_out="$(mktemp -d)"
"$REPO_ROOT/dist/crm/crm" --json inventory convert "$REPO_ROOT/crm/tests/fixtures/inventory/convert-smoke/inventory" --findings "$REPO_ROOT/crm/tests/fixtures/inventory/convert-smoke/findings.json" --packages "$REPO_ROOT/crm/tests/fixtures/inventory/convert-smoke/packages" --out "$convert_out/out" >/dev/null || { echo "bundled convert failed" >&2; exit 1; }
grep -rq 'Xrm.Utility.getGlobalContext().getClientUrl()' "$convert_out/out" || { echo "bundled convert wrote no patch" >&2; exit 1; }
rm -rf "$convert_out"
# The compatibility rules `crm inventory assess` loads (#1063).
test -f "$REPO_ROOT/dist/crm/_internal/crm/rules/compatibility-rules.json" || { echo "missing bundled rules" >&2; exit 1; }
