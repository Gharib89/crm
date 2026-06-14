#!/usr/bin/env bash
# Per-session provisioning for the cloud ship routine. Invoked as the FIRST step
# of the routine prompt (NOT the environment's cached setup-script slot) so it
# always reads the current connection values from the environment and never bakes
# them into a cached image. Never echoes the secret. gh authenticates from
# $GH_TOKEN in the environment automatically — no `gh auth login` here.
set -euo pipefail

# All connection values come from the routine's cloud environment — nothing
# org-specific is committed to this public repo. Fail fast if any is missing.
: "${D365_URL:?set D365_URL in the routine cloud environment}"
: "${D365_CLIENT_ID:?set D365_CLIENT_ID in the routine cloud environment}"
: "${D365_TENANT_ID:?set D365_TENANT_ID in the routine cloud environment}"
: "${D365_CLIENT_SECRET:?set D365_CLIENT_SECRET in the routine cloud environment}"

# gh CLI is not in the sandbox image, but /ship + the issue-claim state machine are
# gh-native. Install the static binary if absent (no-op when the environment's cached
# setup slot already provides it — see docs/agents/cloud-ship-routine.md). gh and git
# reach GitHub directly, so the Custom network policy must allow api.github.com +
# github.com (+ release-assets.githubusercontent.com for this download). gh auto-auths
# from $GH_TOKEN — no `gh auth login`.
if ! command -v gh >/dev/null 2>&1; then
  GH_VERSION=2.94.0
  curl -fsSL "https://github.com/cli/cli/releases/download/v${GH_VERSION}/gh_${GH_VERSION}_linux_amd64.tar.gz" \
    | tar -xz -C /tmp
  SUDO=""; [ "$(id -u)" -ne 0 ] && command -v sudo >/dev/null 2>&1 && SUDO=sudo
  $SUDO install -m 0755 "/tmp/gh_${GH_VERSION}_linux_amd64/bin/gh" /usr/local/bin/gh
fi
gh --version

# crm CLI from source (not published to PyPI)
pip install -e ".[dev,docs]"

# Self-heal the sandbox image's cryptography backend before any test run. The
# image's Debian-packaged cryptography can't load its CFFI runtime (_cffi_backend);
# pip reports it "already satisfied" and never repairs it, so importing the NTLM
# stack (requests_ntlm -> pyspnego -> cryptography ciphers) panics at pytest
# collection ("No module named '_cffi_backend'" / pyo3 PanicException), producing
# ~900 spurious collection errors. Reinstalling cffi restores the backend. CI is
# unaffected — GitHub Actions installs fresh PyPI cryptography wheels.
python -m pip install --force-reinstall cffi

# Build + activate the agent-cloud profile (non-interactive; plaintext store, no
# OS keyring in the sandbox). WhoAmI-tests + activates; fails fast if cloud egress
# is blocked or the secret is wrong. --yes skips the overwrite-confirm so an
# in-session re-run (e.g. retry after a transient pip failure) overwrites cleanly
# instead of aborting on the no-TTY prompt.
crm profile add \
  --name agent-cloud \
  --url "$D365_URL" \
  --auth-scheme oauth \
  --client-id "$D365_CLIENT_ID" \
  --tenant-id "$D365_TENANT_ID" \
  --client-secret "$D365_CLIENT_SECRET" \
  --api-version v9.2 \
  --default-solution agsol \
  --publisher-prefix ag_ \
  --store-password-plaintext \
  --yes

# Sanity: confirm the cloud org is reachable before /ship starts
crm --profile agent-cloud connection whoami
