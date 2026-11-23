---
status: accepted
---

# pyright and ruff run at one exact version everywhere

pyright and ruff decide whether a change passes: one reports type errors, the
other rewrites formatting. Both change their verdicts between releases, so two
machines on different versions disagree about the same tree. An open pyright
floor once let a local run pass what CI failed (#632).

## Decision

- **pyright is not a Python dependency.** Every caller runs Microsoft's npm
  package at one exact version through `npx --yes --package=pyright@<version>`:
  CI's `lint` job, `scripts/check.sh` (the harness turn and full rungs), the
  strict-check hook `.claude/hooks/pyright_strict_check.py` and the vendored
  pyright-lsp plugin. A bump changes every one of those sites in the same
  commit.
- **ruff is pinned exact in `setup.py`'s `dev` extra**, not as a floor, since its
  formatter output drifts between versions. The `ruff-pre-commit` rev in
  `.pre-commit-config.yaml` moves in lockstep with that pin, so the commit hook
  and CI format the same way.

## Considered options

- **Version floors.** Rejected: a floor is what let machines disagree in #632.
