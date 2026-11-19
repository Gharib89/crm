---
name: setup-skills
description: "Configure this repo for the Gharib89/skills engineering skills: draft its ship profile, local gate, PR template, coding-standards doc and reviewer scaffolding, and check the host tooling. Run once after /setup-matt-pocock-skills, before the first /ship."
disable-model-invocation: true
metadata:
  version: 0.31.0
  composes: mattpocock/skills#d81f3a183412e71a5b1e84ca21bc1a35eea03a60:triage
---

# Setup skills

Draft the per-repo documents the `ship` skill reads, confirming with the human before every write. This skill does only what its parent, `setup-matt-pocock-skills`, leaves undone and ship needs: it takes the parent's answers on tracker, triage vocabulary and domain-doc layout as given, and asks only about what the parent left open.

Every document you write here is read by an agent: apply `writing-for-agents` to its prose.

## Process

### 1. Preconditions: instruct and stop

Check all three before exploring. On any failure print the exact command, then "then rerun `/setup-skills`", and stop.

1. **Parent docs.** `docs/agents/issue-tracker.md`, `docs/agents/triage-labels.md` and `docs/agents/domain.md` exist. Else: `/setup-matt-pocock-skills`. One exception: when the host (step 2) is Azure DevOps and `issue-tracker.md` is missing or is the parent's freeform "Other" page, offer to write the vendored [issue-tracker-ado.md](./issue-tracker-ado.md) in its place (confirm first), then continue. The parent ships GitHub, GitLab and local templates only.
2. **Composed skills as derived copies.** `.claude/skills/` holds `code-review`, `tdd`, `codebase-design`, `writing-for-agents`, `triage`, `find-docs` and `show-me`, each recorded in the repo's `skills-lock.json` (the skills CLI writes it at install) with the `ref` its line below pins, the upstream commit the source repo tested. A copy the lock records at another `ref`, or none, is off its pin: print its line. A copy present but absent from the lock is hand-maintained: report it as "will be replaced by the derived copy", confirm, then refresh it with the same line. A global copy under `~/.claude/skills` leaves this check unmet: a personal skill silently shadows a repo's, so ship's composed skills must live in the repo. Else print:

   ```sh
   npx skills@1.8.1 add mattpocock/skills#a496f5513d4a4cf889bc48e189e579f4b0bdf6aa --skill code-review --skill codebase-design --agent claude-code -y
   npx skills@1.8.1 add mattpocock/skills#f3fc5632f401156837ee3872f14fe33ccf1024ea --skill tdd --agent claude-code -y
   npx skills@1.8.1 add mattpocock/skills#c55ee46073ed923f86ce59a5eb3b6d895095d1b7 --skill writing-for-agents --agent claude-code -y
   npx skills@1.8.1 add mattpocock/skills#d81f3a183412e71a5b1e84ca21bc1a35eea03a60 --skill triage --agent claude-code -y
   npx skills@1.8.1 add upstash/context7#681554c5d3a688c67879d02d4f01d79ffd15df13 --skill find-docs --agent claude-code -y
   npx skills@1.8.1 add humanlayer/skills#ca7c8088db69e315a8b2deea43820270457f8f3c --skill show-me --agent claude-code -y
   ```

3. **`ship`, `cloud-ship` and `setup-skills`.** `.claude/skills/ship`, `.claude/skills/cloud-ship` and `.claude/skills/setup-skills` exist and are in the lock. A `ship` folder with no `metadata.version` in its frontmatter is a hand-maintained copy: report "will be replaced by the derived copy", confirm, refresh. `setup-skills` gets step 1.2's treatment; the copy running this check is not evidence, so read `skills-lock.json`. Else print:

   ```sh
   npx skills@1.8.1 add Gharib89/skills --skill ship --skill cloud-ship --skill setup-skills --skill update-skills --agent claude-code -y
   ```

   `update-skills` is no precondition: nothing refuses without it. When it is absent from the lock, report that, print the same line, which installs it, and go on.

   Project scope always, which is the install line without `-g`.

### 2. Host

From `git remote get-url origin`: `github.com` is `github`; `dev.azure.com` or `visualstudio.com` is `ado`; anything else stops as out of scope. Cross-check against `docs/agents/issue-tracker.md`'s title line; a mismatch stops and names both.

Then prove the host tooling with ship's own preflight, the one host check that exists. Call it issueless, so the verdict is about the host and the profile alone:

```sh
.claude/skills/ship/scripts/preflight.sh none
```

Exit 2 with a `host-unreachable` reason (CLI missing, extension missing, not signed in, no push permission) stops here with that reason: setup is the one moment a human is present to fix auth. Exit 1 with `profile missing` is the expected answer at this point; continue.

Preflight cannot prove the git remote itself, so also run `git ls-remote --heads origin`. A failure here surfaces later only as `existing branch: remote unreadable`, and every push in a run would fail. On Azure DevOps with an Entra `az login` and no PAT, offer the repo-local credential helper from the tracker doc's Auth line, which mints the git password from the same token.

### 3. Explore

Read the repo once, every section, before saying anything. The right-hand column says whether exploration settles the section or the walk (step 4) must.

**The harness first**, because it decides the Local gate and Cloud lane rows. Present when `docs/agents/harness.md` exists: probe it as [reference/harness.md](./reference/harness.md#probe) says. A non-conforming entry point stops **Local gate over the harness** there; absent, step 4 opens with the no-harness question.

| Section | Read | Settled by exploration? |
|---|---|---|
| Host | step 2 | yes |
| Worktree | `Carry:` from `git status --ignored --short`, kept to small dotfiles (`.env*`, `*.local`); `Bootstrap:` `None.` unless a setup script exists that the local gate cannot own | yes, confirm the list |
| Local gate | with a harness, the gate step 5 writes over the harness profile's `Location:`; otherwise `scripts/local-gate.sh`, else `.claude/skills/ship/scripts/local-gate.sh`, else `scripts/ship/local-gate.sh`; the lockfile fixes the runner (`package-lock.json` npm, `pnpm-lock.yaml` pnpm, `uv.lock` `uv run pytest`, `pyproject.toml` alone `pytest`); `Small node:` syntax follows the runner, and a docs-class line giving the path of the changed document | location yes; node syntax walked |
| CI | `Legs:` every job of every workflow with a `pull_request` trigger (`.github/workflows/*.yml`; on ADO the pipelines named by build-validation policies); other workflows named in prose as non-PR; `No-checks legal: yes` iff any PR workflow carries a `paths:` filter, or there is no PR workflow at all, which with `Legs: None.` is the pair that drops `ci-wait`'s no-checks grace to zero; `Push policy:` | legs yes; push policy walked |
| Reviewers | one list per reviewer kind, [### Reviewers](#reviewers) below | walked |
| Coding standards | a path CLAUDE.md names, `CODING_STANDARDS.md`, `CONTRIBUTING.md`, `docs/contributing/*` | yes, or stub |
| Verification | not discoverable; seed from test markers (`e2e`, `integration`), Docker use, browser-test scripts | walked |
| Versioning and changelog | semantic-release config, changesets, `version-gate` or bump scripts, `CHANGELOG.md`; `In-PR requirement:` | walked |
| PR | `.github/pull_request_template.md` or `.azuredevops/pull_request_template.md`: presence, headings, and where any closing reference sits relative to the first `## ` heading | yes |
| Public surface | `Default.` proposed | walked |
| Triage | copied from the `needs-triage` row's right-hand column in `triage-labels.md` | yes |
| Docs sync | `README.md`, `docs/`, `GLOSSARY.md`, skills the repo ships; `Agent-facing:` `docs/agents/`, `.claude/skills/` | yes, confirm |
| Current docs | context7 always; Microsoft Learn when a Microsoft stack shows (D365, Azure, Power BI, .NET); `Pinned:` | walked |
| Cloud lane | `PR cap: 3`; `Sandbox merge: no`; `Bootstrap:` `scripts/cloud-ship-bootstrap.sh` with a harness, which step 5's **Cloud bootstrap** writes; without one, that path when the file exists, else `None.` | yes |

Also record, for step 5, three things:

- The per-repo ship script the generic mechanics supersede: `_lib` under `scripts/ship/`.
- Which of `triage-labels.md`'s five role labels and [dimension-labels.md](./dimension-labels.md)'s fourteen dimension labels the host already carries, from `gh api --paginate "repos/{owner}/{repo}/labels" --jq '.[].name'`. Read it paginated rather than through `gh label list`, which fetches 30 by default and silently truncates. Match **case-insensitively**, because GitHub label names are: a label in another casing is recorded as a rename and not as a gap. On ADO a tag exists once it is used, so there is nothing to read.
- Whether `docs/agents/triage-labels.md` already carries a `## Dimension labels` heading.

### Reviewers

Detect each reviewer kind and settle its `Resolve:`, `Workflow:` and `Cap:` lines as [reference/reviewers.md](./reference/reviewers.md#detect) says.

### 4. Present, then walk

Present the whole exploration once: what each section will read, one line each. Then walk **only the walked rows**, one section, one answer, each led by the recommended answer so the user can accept in a word. A one-line explainer only where the choice genuinely branches. Sections exploration settled are shown in the draft; the walk skips them.

Walk order and the recommendation to lead with:

- **No harness**, asked first and only where step 3 found no harness profile: "no harness found: run `/setup-harness` first (recommended), or continue without". The first ends the run with nothing written, printing "run `/setup-harness`, then rerun `/setup-skills`", with its install line `npx skills@1.8.1 add Gharib89/skills --skill setup-harness --agent claude-code -y` for a repo that lacks it. Continuing takes the **Self-contained local gate** item and skips **Cloud bootstrap**, so the bootstrap stays as step 3 found it. The answer is recorded nowhere, so every run asks it again.
- **Local gate, small node**: the runner's own node syntax with one example from the repo's tests, and the docs-class value, the path of the changed document, with one example.
- **CI, push policy**: `one push per review round` on metered minutes (private repos, ADO parallel jobs); `Default.` otherwise.
- **Reviewers**: walk as [reference/reviewers.md](./reference/reviewers.md#walk) says, which ends on `Self-review cap:` (recommend 3).
- **PR**: `Body-check:`, the repo's own PR body checker, an executable path that takes the body file and exits 0 to pass or 1 to refuse. Offer it where a script under `scripts/` or the PR template names one; default `None.`, which publishes unchecked. `Merge:` is not offered: it stays absent unless the human asks for it ([profile-schema.md](./profile-schema.md)).
- **Verification**: one block per seed, or `None.` when nothing in the repo talks to a real system. Per block, offer `Timebox: <n> minutes` to bound a run that can hang (default: absent, no bound) and `Paths: <glob>[, <glob>...]`, the files its result depends on (default: absent, so a later head stays proven only through conflict-free merges of the base).
- **Versioning**: what exploration found, then `In-PR requirement:`, the one line that changes what ship does.
- **Public surface**: `Default.` unless the repo publishes more than an API (gate rules, palettes, bundle inputs).
- **Current docs, pinned**: the libraries whose major version the repo's manifest pins and whose API moved recently.

### 5. Confirm and edit

Show the full draft of everything below, then let the user edit before writing. Field-level validation happens here, the one moment a human is present to fix it, except the reviewer shapes, which step 6's preflight proves. Every reviewer block carries every `Label:` line the template lists, `Cap:` included, and reviewer shapes are proven by ship's preflight in step 6 ([reference/reviewers.md](./reference/reviewers.md#validate)).

Then: `Self-review cap:` is a whole number of 1 or more; every `Also proven by CI:` names a leg defined in `## CI`; `defer-to-ci` appears only with such a leg; `Host:` matches step 2; fourteen headings in order; the `Schema:` line equals ship's `metadata.profile-schema`.

**`docs/agents/ship.md`** from [ship-profile.md](./ship-profile.md): all fourteen headings, `None.` or `Default.` where an axis is defaulted, template comments removed.

**The `### Ship` sub-block**, from [ship-block.md](./ship-block.md) with its template comment removed, inside the existing `## Agent skills` block of whichever of `CLAUDE.md` / `AGENTS.md` the parent chose (the file that has the block). Updated in place when present, so the file ends with exactly one.

**Local gate over the harness**, when step 3's probe conformed. `check.sh full` supplies every gate it runs, one per check under the check's own name; the local gate adds only what Ship needs.

- Absent: write `scripts/local-gate.sh` from [local-gate-harness.sh](./local-gate-harness.sh): `__CHECK__` the harness profile's `Location:` path, `__DEPS__` and `__RUNNER__` from step 3's Local gate row, `secrets` wired as the self-contained item below wires it, its missing-scanner line naming the wired scanner, and the marked slot holding the Ship-only gates: the checks relative to the base, and a `mark <leg> deferred-to-ci` for each CI leg only CI can prove. Gates here carry check names rather than leg names, so the self-contained item's one-gate-per-leg rule does not apply; each `deferred-to-ci` mark still names its leg as `## CI` names it.
- Present: propose a rewrite onto that template. Drop each gate `check.sh full` already runs, matched by the command it runs rather than by its name; the template's own `secrets`, `deps` and small-lane `tests` replace the old gate's. Keep each gate that needs Ship (one relative to the base, a `deferred-to-ci` mark) in the Ship-only slot, and list for the human each one that neither matches a check nor needs Ship, and keep or drop each on their answer. Show the diff, write on confirm.

**Self-contained local gate**, without a harness:

- Absent: write `scripts/local-gate.sh` from [local-gate.sh](./local-gate.sh), one gate per CI leg named as the leg is named in `## CI`, run locally where the runner is obvious and `deferred-to-ci` where it is not; `secrets` wired to a detected scanner (`gitleaks`, `ggshield`, `detect-secrets`, `trufflehog`) and `__SCANNER_INSTALL__` filled, as the template's `secrets` comment says.
- Present and conforming (JSON verdict with `verdict`, `gates`, a `secrets` gate, `--small` and `--base` flags): keep it; `Location:` records where it is.
- Present but non-conforming (prints text, positional `--small`, no `unavailable` status): propose a rewrite to the contract that keeps every check the old gate ran, show the diff, write on confirm. `Location:` moves to `scripts/local-gate.sh` only if the human agrees.

Whichever local gate item ran, or the existing gate a non-conforming probe left in place, **run the gate it wrote or kept once** (`--small` with the example node) and check the verdict shape: a JSON object with `verdict`, `base`, `lane`, `gates.secrets`.

**Cloud bootstrap**, with a harness; skipped without one: write it as [reference/harness.md](./reference/harness.md#cloud-bootstrap) says.

**PR template, coding standards, triage labels and the `## Dimension labels` section**: follow [reference/templates.md](./reference/templates.md). Each is confirmed in the draft before it is written.

**Reviewer scaffolding**, for each reviewer the user named that is not installed, and in a setup section re-run that names `reviewer-scaffolding`: follow [reference/reviewers.md](./reference/reviewers.md#scaffold).

**Superseded ship scripts** (migrating repos): list what step 3 recorded, propose deletion, delete on confirm. Leave `local-gate.sh`, `live-e2e`, `copilot-pr-review-loop` and `cloud-ship-bootstrap.sh` (which is `## Cloud lane`'s `Bootstrap:`) in place.

### 6. Write, then prove

Write every confirmed file. Then run ship's preflight against the new profile:

```sh
.claude/skills/ship/scripts/preflight.sh none
```

Report its `reasons`; a `profile invalid:` reason on a reviewer block is shown to the human as a proposed edit and rewritten once they confirm. The issueless call raises none of its own, so the list should be empty; any `profile missing`, `profile invalid`, `skill missing`, `skill off pin`, `skills lock unreadable` or `composes pin invalid` reason is yours to fix before finishing, the last by re-running the refresh line, since it means the installed `ship` copy is malformed. A `skill missing` or `skill off pin` reason names a composed skill step 1 left uninstalled or off its pin and carries the line that installs it: run that line. A `skills lock unreadable` reason comes before any install line: the skills CLI rewrites a lock it cannot parse holding only the new entry, so repair its JSON first.

Ship calls `merge` by the relative path an allow rule matches, so tell them to add `Bash(.claude/skills/ship/scripts/merge.sh:*)` under `permissions.allow` in the repo's `.claude/settings.local.json`; without the rule auto mode may refuse the merge.

### 7. Done

Tell the user: the profile is at `docs/agents/ship.md`, the first `/ship <issue>` can run, and `cloud-ship` is installed: a cloud routine on this repo fires it with the one-line prompt `Run the cloud-ship skill.` (it selects its own issue; the prompt names no repo). The routine itself is one operator's schedule and not written here. They can edit `docs/agents/ship.md` directly later.

## Re-run

**The profile re-run**, when the profile exists, and **a setup section re-run**, when a refresh names setup sections whose templates moved: each is a paragraph of [reference/re-run.md](./reference/re-run.md); take each whose case holds, the profile re-run first when both do.
