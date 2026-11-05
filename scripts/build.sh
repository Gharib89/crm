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
