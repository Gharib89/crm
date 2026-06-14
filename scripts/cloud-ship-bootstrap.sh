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

# crm CLI from source (not published to PyPI)
pip install -e ".[dev,docs]"

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
