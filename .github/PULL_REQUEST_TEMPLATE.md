<!-- Title MUST be a Conventional-Commit subject — it becomes the squash-merge
     subject that python-semantic-release reads to bump the version.
     feat: → minor, fix:/perf: → patch, ! or BREAKING CHANGE: → major. -->

Closes #

## Why the change

<!-- Exactly one sentence: the problem, and what becomes possible now. The reader has the diff for the how. -->

## Change outline

<!-- One behavioural `diff` fence: call tree, control flow, pseudocode or component tree, text only (no mermaid, no HTML), about 15 lines or fewer. Every node a real symbol, each tree's root carrying its file path, no line numbers. A carrier file tree may follow it, only when the same edit lands in more than two files. `Shape: none, mechanical (<kind>).` replaces the fence only when the reviewer's question is "did the text change correctly", never when it is "what does X now do". -->

## Special things to note

<!-- First bullet, always: `- Door: <one-way|two-way>. Blast radius: <one clause>.` One-way is a merge nobody can walk back; the blast radius names who else feels it. Then reviewer warnings, migrations, compatibility constraints, deliberate omissions, and any deviation from the plan that would change how the reviewer reads the diff: at most five of those, one sentence each, grouped by the claim they share, with the Door line outside that count. No `None.` form, the Door line is always there; the full deviations log stays in the merge summary. -->

## Needs attention

<!-- One line per issue this run filed or linked (`- #<n> <title>: <why it matters>`) and one per Ship defect met (`- Ship defect: <what>`). `None.` when empty; an adjacent find is filed or fixed inline, never left here as an observation. -->

## Verification

<!-- One line per applicable verification the ship profile names, in the merge summary's row format: `- <name>: <phase-3 result>   <what ran>`; ship fills these from the phase-3 results. `None applicable: <reason>` when none was. -->

## Review

<!-- One line per reviewer the ship profile names, in the fixed shape `- <reviewer>: <exit word>, <n> rounds, <raised> findings: <accepted> accepted, <declined> declined, <filed> filed`, with one trailing clause only when the reader must know. A fallback whose primary reviewed takes the second form, `- <fallback>: not invoked: <primary> reviewed`, and states no counts, having none. Ship fills these. `None.` when the repo names no reviewer. -->

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

## Attribution

<!-- The environment's footer for pull request descriptions, verbatim. It ends the body, below every section a later phase rewrites, which is what keeps a section rewrite from swallowing it. -->
