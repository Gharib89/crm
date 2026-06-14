#!/usr/bin/env bash
# Per-session provisioning for the cloud ship routine. Invoked as the FIRST step
# of the routine prompt (NOT the environment's cached setup-script slot) so it
# always reads the current $D365_CLIENT_SECRET and never bakes the plaintext
# secret into a cached image. Never echoes the secret. gh authenticates from
# $GH_TOKEN in the environment automatically — no `gh auth login` here.
set -euo pipefail

: "${D365_CLIENT_SECRET:?set D365_CLIENT_SECRET in the routine cloud environment}"

URL="https://orgd080ee1e.crm.dynamics.com"
CLIENT_ID="4e156fdd-7cfe-487d-8608-c6844dcaf9ed"
TENANT_ID="727f34ab-fb54-4512-a624-5ed673dd203b"

# crm CLI from source (not published to PyPI)
pip install -e ".[dev,docs]"

# Build + activate the agent-cloud profile (non-interactive; plaintext store, no
# OS keyring in the sandbox). WhoAmI-tests + activates; fails fast if cloud egress
# is blocked or the secret is wrong.
crm profile add \
  --name agent-cloud \
  --url "$URL" \
  --auth-scheme oauth \
  --client-id "$CLIENT_ID" \
  --tenant-id "$TENANT_ID" \
  --client-secret "$D365_CLIENT_SECRET" \
  --api-version v9.2 \
  --default-solution agsol \
  --publisher-prefix ag_ \
  --store-password-plaintext

# Sanity: confirm the cloud org is reachable before /ship starts
crm --profile agent-cloud connection whoami
