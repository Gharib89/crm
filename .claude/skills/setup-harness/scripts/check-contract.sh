#!/usr/bin/env bash
# A repo's check entry point held to the contract the hooks and Ship's local
# gate parse: stdout is one JSON line {"rung","verdict","checks":{<name>:<status>}},
# rung the one asked, verdict and every status in the vocabulary, and the exit
# code the verdict's (0 pass or skipped, 1 fail, 2 unavailable, 3 over-budget).
# A failing check is the repo's code, not a violation. setup-harness runs this
# on a re-run, because a hand-edit that breaks this parsing breaks every hook
# silently.
#
#   check-contract.sh <entry point> <rung> [<file>...]
#
# stdout: the entry point's own line when the contract holds, else one line
#         per violation
# stderr: the entry point's, passed through
# exit: 0 holds · 1 a violation · 2 usage, no python3, the entry point cannot
#       run, or it answered no line with exit 2 (its own tooling failing,
#       which the contract admits)
set -uo pipefail
usage="usage: check-contract.sh <entry point> <rung> [<file>...]"
case ${1:-} in
  -h | --help) echo "$usage"; exit 0 ;;
  '') echo "$usage" >&2; exit 2 ;;
esac
[ -n "${2:-}" ] || { echo "$usage" >&2; exit 2; }
[ -x "$1" ] || { echo "cannot run $1" >&2; exit 2; }
command -v python3 >/dev/null || { echo "check-contract.sh: needs python3" >&2; exit 2; }
out=$(mktemp) || exit 2
trap 'rm -f "$out"' EXIT
"$@" > "$out"
code=$?
[ "$code" = 2 ] && [ ! -s "$out" ] && exit 2

python3 - "$2" "$code" "$out" <<'EOF'
import json, sys
rung, code, path = sys.argv[1], int(sys.argv[2]), sys.argv[3]
statuses = ["pass", "fail", "unavailable", "skipped", "over-budget"]
want = " | ".join(statuses)
exits = {"pass": 0, "skipped": 0, "fail": 1, "unavailable": 2, "over-budget": 3}
bad = []
with open(path, encoding="utf-8", errors="replace") as f:
    lines = f.read().splitlines()
if len(lines) != 1:
    print(f"stdout: want one JSON line, got {len(lines)} lines")
    sys.exit(1)
try:
    answer = json.loads(lines[0])
    assert isinstance(answer, dict)
except (ValueError, AssertionError):
    print(f"stdout: not a JSON object: {lines[0]}")
    sys.exit(1)
if answer.get("rung") != rung:
    bad.append(f"rung: want {rung}, got {answer.get('rung', 'nothing')}")
verdict = answer.get("verdict", "nothing")
if verdict not in statuses:
    bad.append(f"verdict: want {want}, got {verdict}")
elif exits[verdict] != code:
    bad.append(f"exit: verdict {verdict} wants {exits[verdict]}, got {code}")
checks = answer.get("checks")
if not isinstance(checks, dict):
    bad.append("checks: want an object of <name>: <status>")
else:
    bad += [f"checks.{n}: want {want}, got {s}" for n, s in checks.items() if s not in statuses]
print("\n".join(bad) if bad else lines[0])
sys.exit(1 if bad else 0)
EOF
