<!-- Title MUST be a Conventional-Commit subject — it becomes the squash-merge
     subject that python-semantic-release reads to bump the version.
     feat: → minor, fix:/perf: → patch, ! or BREAKING CHANGE: → major. -->

## Summary

<!-- What changed and why, in 1-3 lines. Link the issue so it auto-closes: -->
Closes #

## Type

- [ ] `feat` — new command / query mode / materially new capability (minor bump)
- [ ] `fix` / `perf` — bugfix or small enhancement (patch bump)
- [ ] `docs` — docs / comments only
- [ ] `refactor` / `chore` / `test` — no user-visible behavior change

## Docs & skill sync — ship in the *same* change

<!-- Tick what applies; strike through (~~...~~) what doesn't. -->

- [ ] **README.md** updated (user-facing capability / install change).
- [ ] **docs/** updated — `docs/how-to/<group>.md` and `docs/reference/cli.md`.
- [ ] **`crm/skills/`** updated if the CLI surface changed (command/flag/choice/default/output/JSON contract). The shipped skill is self-contained — no repo-path links.
- [ ] **`crm/tests/TEST.md`** updated if a `@requires_cloud` / `@requires_onprem` gate changed; **`crm/tests/e2e/DISCOVERED_BUGS.md`** updated if a tracked defect was fixed/reclassified.
- [ ] N/A — nothing user-visible changed (internal refactor, behavior-restoring bugfix, test/build/comments only).

## Tests

- [ ] New/changed D365-touching command has a live e2e test (`@covers("<group> <verb>")`) under `crm/tests/e2e/`, **or** an `E2E_SKIP` entry with a reason in `crm/tests/e2e/coverage.py` (offline coverage gate enforces this).
- [ ] Live target verified — [ ] cloud · [ ] on-prem · [ ] both · [ ] N/A. <!-- A target-specific bug must be verified on THAT target: cloud-green ≠ on-prem fixed. -->

## Local gate — mirrors CI, all green

- [ ] `pytest`
- [ ] `pyright --pythonpath .venv/bin/python`
- [ ] `mkdocs build --strict` (if `crm/**`, `docs/**`, `setup.py`, or `mkdocs.yml` touched)
- [ ] Secret/credential scan clean (no real org GUIDs / fingerprints / secrets — public repo).
