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
SCANNER=gitleaks
# Built from the Go proxy, which the cloud network admits, at the version the
# workstation runs: apt's gitleaks is 8.16.0, and the release download is blocked.
# shellcheck disable=SC2016 # expanded when the line below evals it
SCANNER_INSTALL='GOTOOLCHAIN=auto go install github.com/zricethezav/gitleaks/v8@v8.30.1 && sudo install -m 0755 "$(go env GOPATH)/bin/gitleaks" /usr/local/bin/gitleaks'
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

# Never echoes the secret. All connection values come from the routine's cloud
# environment, read per session rather than baked into a cached image; nothing
# org-specific is committed to this public repo. Without them there is no
# profile, and live e2e hands off rather than runs, so skip instead of failing.
missing=""
for v in D365_URL D365_CLIENT_ID D365_TENANT_ID D365_CLIENT_SECRET; do
  [ -n "${!v:-}" ] || missing="$missing $v"
done
if [ -n "$missing" ]; then
  echo "cloud-ship-bootstrap: unset:$missing; skipping the agent-cloud profile (live e2e hands off)"
  exit 0
fi

# Build + activate the agent-cloud profile (non-interactive; plaintext store, no
# OS keyring in the sandbox). WhoAmI-tests + activates; fails fast if cloud egress
# is blocked or the secret is wrong. --yes skips the overwrite-confirm so an
# in-session re-run (e.g. retry after a transient pip failure) overwrites cleanly
# instead of aborting on the no-TTY prompt.
.venv/bin/python -m crm profile add \
  --name agent-cloud \
  --url "$D365_URL" \
  --auth-scheme oauth \
  --client-id "$D365_CLIENT_ID" \
  --tenant-id "$D365_TENANT_ID" \
  --client-secret "$D365_CLIENT_SECRET" \
  --api-version v9.2 \
  --publisher-prefix ag \
  --store-password-plaintext \
  --yes

# Sanity: confirm the cloud org is reachable before /ship starts
.venv/bin/python -m crm --profile agent-cloud connection whoami
