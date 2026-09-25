#!/usr/bin/env bash
# The host's own tool, present before anything else runs. `prepare` runs it
# first in a cloud sandbox and in any `--unattended` run: the sandbox image
# ships without `gh` (measured in "Probe the cloud sandbox proxy with ship's
# GitHub REST calls"), and only the host adapter knows what its tool is and how
# to install it, so this is core, which is why it lives here rather than on a
# profile Bootstrap: line every repo repeats.
#
#   tooling [--install]
#
# stdout: {host, missing[], installed, ok}
#   missing lists the adapter's tooling reasons still true after any install;
#   installed is true when this run installed the host CLI.
# exit: 0 all present · 2 missing (host-unreachable) or usage
set -uo pipefail
source "$(dirname "${BASH_SOURCE[0]}")/_lib.sh" || { printf '{"error":"cannot source _lib.sh"}\n'; exit 2; }

usage='usage: tooling [--install]'
ship_help "$usage" "$@"

install=false
while [ $# -gt 0 ]; do
  case $1 in
    --install) install=true; shift ;;
    *) ship_tooling "$usage" ;;
  esac
done

ship_load_host
installed=false
missing=$(host_tooling_reasons)
if [ -n "$missing" ] && [ "$install" = true ]; then
  log=$(mktemp); trap 'rm -f "$log"' EXIT
  if host_tooling_install >"$log" 2>&1; then installed=true; else ship_tail40 "$log"; fi
  rm -f "$log"
  missing=$(host_tooling_reasons)
fi

ok=true; [ -z "$missing" ] || ok=false
printf '%s\n' "$missing" | jq -Rs --arg h "$SHIP_HOST" --argjson i "$installed" --argjson ok "$ok" \
  '{host: $h, missing: (split("\n") | map(select(. != ""))), installed: $i, ok: $ok}'
$ok || exit 2
