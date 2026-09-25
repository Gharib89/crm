#!/usr/bin/env bash
# Prepare the cloud sandbox before anything can be claimed: the host's own tool
# through `tooling --install`, then the profile's `## Cloud lane` `Bootstrap:`,
# in that order. The sandbox decides preparation and the lane decides admission,
# so this runs in every run inside a cloud sandbox, attended or unattended; the
# sandbox marks itself with CLAUDE_CODE_REMOTE=true. `--unattended` runs it off
# the sandbox too, so a local `ship --unattended` reproduces a fire. Outside
# both it is a no-op, so an attended run on a workstation is unchanged. The
# bootstrap runs from the checkout root whatever directory the run is in.
#
#   prepare [--unattended]
#
# stdout: {sandbox, steps[{step, status}], missing[], failed, ok}
#   step is tooling or bootstrap; status ran|skipped|failed. The bootstrap is
#   skipped where the profile names `None.` or there is no profile to read,
#   which preflight then stops on. failed names the step that failed, else null;
#   missing is `tooling`'s list of host tools still absent.
# stderr: the failing step's last 40 lines
# exit: 0 prepared or nothing to prepare · 1 a step failed · 2 usage
set -uo pipefail
source "$(dirname "${BASH_SOURCE[0]}")/_lib.sh" || { printf '{"error":"cannot source _lib.sh"}\n'; exit 2; }

usage='usage: prepare [--unattended]'
ship_help "$usage" "$@"

unattended=false
while [ $# -gt 0 ]; do
  case $1 in
    --unattended) unattended=true; shift ;;
    *) ship_tooling "$usage" ;;
  esac
done

sandbox=false; [ "${CLAUDE_CODE_REMOTE:-}" = true ] && sandbox=true
steps='[]'; missing='[]'; failed=null

emit() {
  jq -n --argjson sb "$sandbox" --argjson st "$steps" --argjson m "$missing" --argjson f "$failed" \
    '{sandbox: $sb, steps: $st, missing: $m, failed: $f, ok: ($f == null)}'
  [ "$failed" = null ] || exit 1
  exit 0
}
step() { steps=$(jq -c --arg s "$1" --arg v "$2" '. + [{step: $s, status: $v}]' <<<"$steps"); }

{ [ "$sandbox" = true ] || [ "$unattended" = true ]; } || emit

log=$(mktemp); trap 'rm -f "$log"' EXIT
out=$(bash "$SHIP_SCRIPTS/tooling.sh" --install 2>"$log"); rc=$?
missing=$(jq -c '.missing // []' <<<"$out" 2>/dev/null) || missing='[]'
[ -n "$missing" ] || missing='[]'
if [ "$rc" -ne 0 ]; then
  step tooling failed; failed='"tooling"'
  ship_tail40 "$log"
  emit
fi
step tooling ran

bootstrap=""
root=$(git rev-parse --show-toplevel 2>/dev/null) && profile=$(ship_profile_path) && [ -f "$profile" ] \
  && bootstrap=$(awk '/^## /{f = ($0 ~ /^## Cloud lane[ \t\r]*$/)} f && /^Bootstrap:/{sub(/^Bootstrap:[ \t]*/, ""); sub(/[ \t\r]+$/, ""); gsub(/`/, ""); print; exit}' "$profile")
case $bootstrap in
  ""|None.) step bootstrap skipped; emit ;;
esac
if ( cd "$root" && bash -c "$bootstrap" ) >"$log" 2>&1; then
  step bootstrap ran
else
  step bootstrap failed; failed='"bootstrap"'
  ship_tail40 "$log"
fi
emit
