#!/usr/bin/env bash
# Per-session provisioning for the cloud ship routine. Invoked as the FIRST step
# of the routine prompt (NOT the environment's cached setup-script slot) so it
# always reads the current connection values from the environment and never bakes
# them into a cached image. Never echoes the secret. GitHub reads/writes go
# through the brokered GitHub MCP connector (see cloud-ship SKILL.md → "GitHub
# access in a fire"); only `git` (push/fetch over github.com) hits GitHub
# directly, so no `gh` install or `gh auth login` is needed here.
set -euo pipefail

# All connection values come from the routine's cloud environment — nothing
# org-specific is committed to this public repo. Fail fast if any is missing.
: "${D365_URL:?set D365_URL in the routine cloud environment}"
: "${D365_CLIENT_ID:?set D365_CLIENT_ID in the routine cloud environment}"
: "${D365_TENANT_ID:?set D365_TENANT_ID in the routine cloud environment}"
: "${D365_CLIENT_SECRET:?set D365_CLIENT_SECRET in the routine cloud environment}"

# GitHub API access (issue picker, PR create/read, review re-request) runs through
# the GitHub MCP connector, brokered through Anthropic and exempt from the sandbox
# network policy — no `gh` needed. The one direct-egress GitHub dependency is `git`
# push/fetch over github.com, so the Custom network policy must allow github.com
# (see docs/agents/cloud-ship-routine.md).

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
  --publisher-prefix ag \
  --store-password-plaintext \
  --yes

# Sanity: confirm the cloud org is reachable before /ship starts
crm --profile agent-cloud connection whoami
