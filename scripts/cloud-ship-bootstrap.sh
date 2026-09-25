#!/usr/bin/env bash
# Per-session provisioning for the cloud ship routine. Run by ship's `prepare`
# as the profile's `## Cloud lane` Bootstrap (NOT the environment's cached
# setup-script slot) so it always reads the current connection values from the
# environment and never bakes them into a cached image. Never echoes the secret.
# GitHub access is ship's own: `prepare` installs `gh` (`tooling --install`)
# before this runs, and every mechanic calls GitHub REST through it with
# GH_TOKEN, so nothing here calls the GitHub API (only the gitleaks and actionlint
# downloads below reach github.com).
set -euo pipefail

# Ship also runs the Bootstrap on a local `--unattended` run; everything below
# provisions a cloud sandbox, so a workstation skips it.
[ "${CLAUDE_CODE_REMOTE:-}" = true ] || { echo "cloud-ship-bootstrap: not in a cloud sandbox; nothing to do"; exit 0; }

# crm requires Python >= 3.13. The sandbox image's default `python`/`pip` can lag
# behind that floor (observed: default `python` = 3.11 with a usable 3.13 present at
# /usr/bin/python3.13), which makes the editable install below abort on the version
# pin before the profile is ever built. Don't trust the ambient default — pick the
# first interpreter that actually satisfies the floor, build the .venv from it, and
# drive every install *and* the crm CLI itself through `.venv/bin/python`, so the
# whole bootstrap is pinned to
# one interpreter regardless of what `python`/`pip`/`crm` resolve to on PATH. A
# CLOUD_SHIP_PYTHON override is tried first but validated the same way, so a stale
# value (missing, or < 3.13) just falls through to auto-detection instead of
# reintroducing the original install failure.
PY=""
for cand in ${CLOUD_SHIP_PYTHON:+"$CLOUD_SHIP_PYTHON"} python3.13 python3 python; do
  if command -v "$cand" >/dev/null 2>&1 \
     && "$cand" -c 'import sys; sys.exit(0 if sys.version_info >= (3, 13) else 1)'; then
    PY="$(command -v "$cand")"
    break
  fi
done
if [ -z "$PY" ]; then
  echo "cloud-ship-bootstrap: no Python >= 3.13 found (crm requires >=3.13); checked ${CLOUD_SHIP_PYTHON:+$CLOUD_SHIP_PYTHON, }python3.13, python3, python — the sandbox image lacks a compatible interpreter" >&2
  exit 1
fi
echo "cloud-ship-bootstrap: using $("$PY" --version) ($PY)"

# crm CLI from source (not published to PyPI), into this clone's .venv: the cloud
# run isolates in place, and scripts/local-gate.sh looks for `$PWD/.venv`. `uv`
# rides along because the gate's semgrep and zizmor run through `uvx`.
"$PY" -m venv .venv
.venv/bin/python -m pip install -e ".[dev,docs]" uv

# gitleaks and actionlint for the gate, pinned by version and checksum, into
# .venv/bin, where the gate looks when PATH has neither. actionlint's version is
# in lockstep with .pre-commit-config.yaml and CI.
fetch() {  # fetch <url> <sha256> <binary>
  local tgz; tgz=$(mktemp)
  curl -fsSL -o "$tgz" "$1"
  echo "$2  $tgz" | sha256sum -c --quiet -
  tar -xzf "$tgz" -C .venv/bin "$3"
  rm -f "$tgz"
}
fetch https://github.com/gitleaks/gitleaks/releases/download/v8.30.1/gitleaks_8.30.1_linux_x64.tar.gz \
  551f6fc83ea457d62a0d98237cbad105af8d557003051f41f3e7ca7b3f2470eb gitleaks
fetch https://github.com/rhysd/actionlint/releases/download/v1.7.12/actionlint_1.7.12_linux_amd64.tar.gz \
  8aca8db96f1b94770f1b0d72b6dddcb1ebb8123cb3712530b08cc387b349a3d8 actionlint

# All connection values come from the routine's cloud environment; nothing
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
