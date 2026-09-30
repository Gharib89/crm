# Harness profile

Schema: 3

Written by `/setup-harness`, which reads it back on a re-run. Facts sit on `Label:` lines; prose under a heading is yours and nothing parses it. A budget override reads `override <N>s: <reason>`.

## Claude Code

Floor: 2.1.277

## Check entry point

Location: scripts/check.sh

## Budgets

Edit: default
Turn: default
Commit: default
Full: default
Cloud setup: default

## Cloud

Verdict: cloud-first
Setup: .claude/hooks/cloud-setup.sh
Allowlist: None.
Proof: 1d36d6e7bf7b3d4d19087c1bf8b0a37b57cbc4ce

## Excluded

Excluded: crm/tests/fixtures/: byte-exact base64 ribbon input
Excluded: crm/tests/e2e/fixtures/: solution XML the pac e2e packs byte-exact

## Roots

Root: pyproject.toml: pip/setuptools project; dev pins live in setup.py [dev]; no lockfile by design

## Local-only

Local-only: None.

## Declined

Declined: crm/tests/e2e/plugin_src/NoOpPlugin.csproj as a root: e2e test plug-in, built only by the live plugin tests
Declined: shfmt: no Go toolchain on the maintainer's machine, so the hook would fail on every .sh edit
Declined: markdownlint-cli2: 10,911 findings on the 261 tracked Markdown files (7,309 of them MD013)
Declined: Prettier: would rewrite 215 files, has no JS stack to pin in, and puts mkdocs-material syntax at risk
Declined: ruff 0.16.8: held in lockstep with CI, and 0.16 changes formatter output; bump in its own PR
Declined: zizmor 1.30.1: held in lockstep with CI and scripts/local-gate.sh; bump in its own PR
Declined: run recipe: the CLI is driven by the live-e2e skill and crm describe
