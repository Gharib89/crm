<!-- Describe the change and link any issue (e.g. "Closes #123"). -->

## Checklist

- [ ] Docs updated in the same change (README / `docs/how-to/<group>.md` / `docs/reference/cli.md` as applicable).
- [ ] New/changed D365-touching command has a live e2e test (`@covers`) under `crm/tests/e2e/`, or an `E2E_SKIP` entry with a reason in `crm/tests/e2e/coverage.py` — the offline coverage gate enforces this.
- [ ] Conventional Commit subject (squash subject for squash-merges) so `python-semantic-release` bumps/documents correctly.
