---
status: accepted
---

# pyright and ruff run at one exact version everywhere

pyright and ruff decide whether a change passes: one reports type errors, the
other rewrites formatting. Both change their verdicts between releases, so two
machines on different versions disagree about the same tree. An open pyright
floor once let a local run report errors CI did not (#632).

## Decision

- **pyright is not a Python dependency.** Every caller runs Microsoft's npm
  package at one exact version through `npx --yes --package=pyright@<version>`:
  CI's `lint` job, the turn rung of `scripts/check.sh`, the strict-check hook
  `.claude/hooks/pyright_strict_check.py` and the vendored pyright-lsp plugin.
  A bump changes every `pyright@<version>` in the tree in the same commit,
  including the docs that quote the command (`rg 'pyright@'` lists them).
- **ruff is pinned exact in `setup.py`'s `dev` extra**, not as a floor, since its
  formatter output drifts between versions. The `ruff-pre-commit` rev in
  `.pre-commit-config.yaml` moves in lockstep with that pin, so the commit hook
  and CI format the same way.

## Considered options

- **Version floors.** Rejected: a floor is what let machines disagree in #632.
