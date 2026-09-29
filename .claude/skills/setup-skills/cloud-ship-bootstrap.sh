#!/usr/bin/env bash
# The ship profile's `## Cloud lane` `Bootstrap:`, written by setup-skills where
# the repo has a harness profile (docs/agents/harness.md); owned by the repo
# from here. Ship's `prepare` runs it before a cloud run takes its claim, so the
# local gate finds every tool it runs.
#
#   scripts/cloud-ship-bootstrap.sh
#
# exit: 0 done, or not a cloud session · non-zero a step failed, which ship
# reads as `bootstrap-failed`
#
# The harness cloud setup installs what check.sh runs. This file adds only what
# Ship alone needs: the secrets scanner the local gate runs, and the repo's own
# Ship-only steps below. A step belongs to the harness cloud setup unless it
# needs something only Ship has. Bash 3.2.
set -euo pipefail
[ "${CLAUDE_CODE_REMOTE:-}" = true ] || exit 0
cd "$(git rev-parse --show-toplevel)"

# >>> setup-skills configuration
# SCANNER: the executable name of the scanner the local gate's `secrets` gate
# runs (`gitleaks`), looked up with `command -v`. SCANNER_INSTALL: how to
# install it when missing, `apt_install <package>` or a registry's own install
# (`pipx install detect-secrets`).
SCANNER=__SCANNER__
SCANNER_INSTALL='__SCANNER_INSTALL__'
# <<< setup-skills configuration

# apt_install <package>...: sudo unless root; a fresh image may carry no
# package lists yet, so a failed install refreshes them once and retries.
apt_install() {
  local sudo=""; [ "$(id -u)" -eq 0 ] || sudo=sudo
  $sudo apt-get install -y "$@" || { $sudo apt-get update && $sudo apt-get install -y "$@"; }
}

# The harness cloud setup, at the harness profile's `Setup:` path, read here
# rather than copied so a re-run of setup-harness that moves it moves this too.
setup=$(awk '{ sub(/\r$/, "") } /^## / { c = ($0 == "## Cloud"); next } c && sub(/^Setup: /, "") { print; exit }' docs/agents/harness.md)
[ -n "$setup" ] || { echo "cloud-ship-bootstrap: no Setup: line under ## Cloud in docs/agents/harness.md" >&2; exit 1; }
[ "$setup" = None. ] || bash "$setup"

command -v "$SCANNER" >/dev/null || { eval "$SCANNER_INSTALL"; command -v "$SCANNER" >/dev/null; }

# The repo's Ship-only steps go here, each failing the bootstrap on non-zero:
# what needs something only Ship has, such as live-e2e credentials or profiles.
