# Cloud ship routine — Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Stand up a scheduled Claude Code **cloud routine** (claude.ai) that ships the oldest open `ready-for-agent` issue in `Gharib89/crm` to a merge-ready PR via `/ship`, stopping at the human merge gate.

**Architecture:** A claude.ai **routine** = a saved prompt + the bound repo + a **cloud environment** + a schedule trigger. Each fire clones the repo (default branch), the prompt's first step runs an in-repo bootstrap script to provision the sandbox (install crm from source, build the `agent-cloud` profile from the environment's injected secret), picks one ready issue, and runs the vendored `/ship` skill. Secrets and network policy live on the **environment** (configured in the claude.ai web UI); the repo carries no credentials.

**Tech Stack:** Claude Code Routines (research preview, claude.ai/code), `gh` CLI (`GH_TOKEN` env auth), `crm` CLI (Python, source install), bash, GitHub fine-grained PAT, Entra ID OAuth client-credentials.

**Spec:** `docs/superpowers/specs/2026-06-14-cloud-ship-routine-design.md`

---

## Capability findings (Task 1 — RESOLVED, cloud is viable)

Source: https://code.claude.com/docs/en/routines.md (+ Claude Code on the web cloud-environment docs).

- **Repo binding:** ✓ routines select one or more repos, cloned per run from the default branch.
- **Skills:** load only from skills **committed to the cloned repo** → vendoring (Task 2) is required.
- **Secrets / env vars:** configured on the **cloud environment** ("Environment variables"), injected at run time — *not* a per-trigger field.
- **Setup script:** an environment field, **cached** across sessions. We deliberately do NOT use it for the secret-bearing bootstrap (see corrections below).
- **Schedule:** presets (hourly/daily/weekdays/weekly) or custom cron via `/schedule update`; **minimum interval 1 hour**; per-account daily run cap.
- **Network:** Default env = "Trusted", which **blocks `*.crm.dynamics.com`** (403 `host_not_allowed`). Must set the env to **Custom** allowed domains.
- **Branch push:** default permits only `claude/*` branches; `/ship` pushes `feat/*` → must enable **"Allow unrestricted branch pushes"**.
- **GitHub auth:** `gh` auto-reads `GH_TOKEN` from env → no `gh auth login` needed in bootstrap. Repo clone/push uses the account's connected GitHub identity (`/web-setup`).
- **Research preview:** API/limits may change; a green run status ≠ task success (read the transcript).

## Corrections applied since the spec

- Config lives on a **cloud environment** (web-UI), not a single `RemoteTrigger` create body.
- Bootstrap runs **from the prompt's first step**, not the cached setup-script slot — so secret rotation needs no cache rebuild and the plaintext secret is never baked into a cached image.
- Bootstrap **drops `gh auth login`** (gh uses `GH_TOKEN` env).
- e2e env (`D365_E2E`, `D365_E2E_PROFILE=agent-cloud`, `D365_E2E_ALLOW_HOST`) = environment variables, not script exports.
- Two must-set environment toggles: **Custom network domains** + **unrestricted branch pushes**.

## File structure

| Path | Action | Responsibility |
|---|---|---|
| `.claude/skills/ship/` (3 files) | Create | Vendored `/ship` skill (composes tdd + review) |
| `.claude/skills/tdd/` (6 files) | Create | Vendored `tdd` skill |
| `.claude/skills/review/SKILL.md` | Create | Vendored `review` skill |
| `scripts/cloud-ship-bootstrap.sh` | Create | Per-session sandbox provisioning (run from the prompt) |
| `docs/agents/cloud-ship-routine.md` | Create | Versioned trigger prompt + claude.ai wiring runbook |

`docs/agents/` and `docs/superpowers/` are in `mkdocs.yml`'s `exclude_docs:` → new docs there don't affect `mkdocs --strict` and need no nav edits. `.claude/` is not gitignored (only `.claude/scheduled_tasks.lock` is).

---

### Task 2: Vendor the ship/tdd/review skills

**Files:**
- Create: `.claude/skills/ship/SKILL.md`, `.claude/skills/ship/reference/merge-gate.md`, `.claude/skills/ship/reference/copilot-loop.md`
- Create: `.claude/skills/tdd/SKILL.md`, `.claude/skills/tdd/{interface-design,deep-modules,tests,mocking,refactoring}.md`
- Create: `.claude/skills/review/SKILL.md`

- [ ] **Step 1: Copy the skill trees into the repo**

```bash
mkdir -p .claude/skills
cp -R ~/.claude/skills/ship ~/.claude/skills/tdd ~/.claude/skills/review .claude/skills/
```

- [ ] **Step 2: Re-scan for secrets / personal paths / GUIDs (public-repo gate)**

```bash
grep -rIn -E '/home/[a-z]+|gho_|github_pat_|client_secret|[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}|BEGIN .*PRIVATE KEY' .claude/skills/ && echo "FOUND — do not commit" || echo "clean"
```
Expected: `clean`. If anything is found, scrub it before committing.

- [ ] **Step 3: Verify structure landed**

```bash
find .claude/skills -name SKILL.md | sort
```
Expected: exactly `.claude/skills/{review,ship,tdd}/SKILL.md`.

- [ ] **Step 4: Commit**

```bash
git add .claude/skills
git commit -m "chore(agents): vendor ship/tdd/review skills for the cloud routine

Co-Authored-By: Claude Opus 4.8 (1M context) <noreply@anthropic.com>"
```

---

### Task 3: Bootstrap script

**Files:**
- Create: `scripts/cloud-ship-bootstrap.sh`

- [ ] **Step 1: Write the script**

```bash
#!/usr/bin/env bash
# Per-session provisioning for the cloud ship routine. Invoked as the FIRST step
# of the routine prompt (NOT the environment's cached setup-script slot) so it
# always reads the current $D365_CLIENT_SECRET and never bakes the plaintext
# secret into a cached image. Never echoes the secret. gh authenticates from
# $GH_TOKEN in the environment automatically — no `gh auth login` here.
set -euo pipefail

: "${D365_CLIENT_SECRET:?set D365_CLIENT_SECRET in the routine's cloud environment}"

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
```

- [ ] **Step 2: Make executable + syntax-check**

```bash
chmod +x scripts/cloud-ship-bootstrap.sh
bash -n scripts/cloud-ship-bootstrap.sh && echo "syntax ok"
```
Expected: `syntax ok`.

- [ ] **Step 3: Lint (shellcheck if available)**

```bash
command -v shellcheck >/dev/null && shellcheck scripts/cloud-ship-bootstrap.sh || echo "shellcheck not installed — skipped"
```
Expected: no findings (or skip note). Fix any SC warnings.

- [ ] **Step 4: Commit**

```bash
git add scripts/cloud-ship-bootstrap.sh
git commit -m "feat(agents): cloud-ship sandbox bootstrap script

Co-Authored-By: Claude Opus 4.8 (1M context) <noreply@anthropic.com>"
```

---

### Task 4: Routine-prompt doc + wiring runbook

**Files:**
- Create: `docs/agents/cloud-ship-routine.md`

- [ ] **Step 1: Write the doc**

````markdown
# Cloud ship routine

A claude.ai **routine** (research preview) that ships the oldest open
`ready-for-agent` issue to a merge-ready PR via `/ship`, then stops at the human
merge gate. One issue per fire. Manage at https://claude.ai/code/routines or via
`/schedule` in the CLI.

## Routine prompt (self-contained — paste verbatim into the routine's Instructions)

```
Objective: produce ONE merge-ready PR for Gharib89/crm and then stop.

1. Provision the sandbox: from the repo root run `bash scripts/cloud-ship-bootstrap.sh`.
   It installs the crm CLI, builds the active `agent-cloud` profile from the
   environment's $D365_CLIENT_SECRET, and confirms the cloud org via whoami. If it
   exits non-zero, report the failure and STOP.
2. Pick the work item (gh reads GH_TOKEN from the environment):
   NUM=$(gh issue list --repo Gharib89/crm --label ready-for-agent --state open \
         --json number --jq 'sort_by(.number)[0].number // empty')
   If NUM is empty, report "nothing ready" and STOP — do not open a PR.
3. Run the /ship skill on issue $NUM. Pin `--profile agent-cloud` on every crm
   command. Follow the repo CLAUDE.md for test/gate/docs-sync/commit rules. This is
   the cloud Dataverse org ONLY — never attempt on-prem work; if the issue can only
   be verified on-prem, report that and STOP.
4. /ship stops at the merge gate by design. Do NOT merge. Post the PR link and the
   disposition summary, then STOP for human merge approval.
```

The routine's model selector should be set to the strongest available coding model.

## Cloud environment config (claude.ai web UI — "Edit routine" → environment)

Configure a dedicated environment (e.g. `crm-ship`) and select it for the routine:

- **Network access → Custom**, Allowed domains (keep "include default package
  managers" checked, for pip/PyPI):
  - `login.microsoftonline.com`   (OAuth client-credentials token endpoint)
  - `orgd080ee1e.crm.dynamics.com` (Dataverse Web API)
- **Environment variables:**
  - `D365_CLIENT_SECRET` = agent-cloud OAuth client secret (rotate after wiring)
  - `GH_TOKEN` = fine-grained PAT, repo `Gharib89/crm`: Contents + Pull requests +
    Issues + Workflows (write)
  - `D365_E2E` = `1`
  - `D365_E2E_PROFILE` = `agent-cloud`
  - `D365_E2E_ALLOW_HOST` = `orgd080ee1e.crm.dynamics.com`
- **Setup script:** leave empty (bootstrap runs from the prompt, see above).

## Permissions

- Enable **"Allow unrestricted branch pushes"** for `Gharib89/crm` — `/ship` pushes
  `feat/*` branches; without this, only `claude/*` pushes are allowed.
- Remove connectors the routine doesn't need (all connected MCP servers are added
  by default; routines can use every tool from an included connector without asking).

## Schedule

Min interval is 1 hour. Default to weekday-daily; for an exact off-minute cron use
`/schedule update` → `17 6 * * 1-5`. Create the routine, then **Run now** once
against a known `ready-for-agent` issue before relying on the schedule.
````

- [ ] **Step 2: Commit**

```bash
git add docs/agents/cloud-ship-routine.md docs/superpowers/specs/2026-06-14-cloud-ship-routine-design.md
git commit -m "docs(agents): cloud ship routine prompt + wiring runbook

Co-Authored-By: Claude Opus 4.8 (1M context) <noreply@anthropic.com>"
```

---

### Task 5: Local gate + PR for the repo changes

Mirror the full CI before pushing (CLAUDE.md: pytest + mkdocs --strict + secret scan).

- [ ] **Step 1: Offline test suite**

```bash
WT=$(pwd); PYTHONPATH=$WT ~/wip/projects/crm/.venv/bin/python -m pytest -q
```
Expected: pass (vendoring skills + adding a script/docs touches no `crm/` code, so the suite is unaffected — this confirms no incidental breakage).

- [ ] **Step 2: Docs build (strict)**

```bash
mkdocs build --strict 2>&1 | tail -5
```
Expected: clean build (new docs are under `exclude_docs:` dirs).

- [ ] **Step 3: Secret scan of the whole diff vs main**

```bash
git diff origin/main... | grep -nE 'gho_|github_pat_|BEGIN .*PRIVATE KEY' && echo "SECRET LEAK — stop" || echo "no secrets in diff"
```
Expected: `no secrets in diff`. (The OAuth secret and PAT never enter the repo — they live only in the environment's variables.)

- [ ] **Step 4: Push + open PR**

```bash
git push -u origin feat/cloud-ship-routine
gh pr create --repo Gharib89/crm --base main --head feat/cloud-ship-routine \
  --title "feat(agents): cloud ship routine (vendored skills + bootstrap)" \
  --body "Stands up the cloud ship routine: vendors ship/tdd/review skills (so the cloud clone can load /ship), adds the per-session sandbox bootstrap script, and the routine prompt + claude.ai wiring runbook. Cloud wiring (environment, secrets, routine) is done in the claude.ai UI per docs/agents/cloud-ship-routine.md. Spec: docs/superpowers/specs/2026-06-14-cloud-ship-routine-design.md"
```

- [ ] **Step 5: Drive Copilot review to the ceiling + green CI, then human-merge**

Copilot auto-reviews on PR creation (round 1, automatic). Triage findings, fix, re-request rounds 2+ via REST per CLAUDE.md (≤3-round ceiling). Wait for green CI. **Stop at the merge gate** — human approves the squash-merge. Squash subject must be a Conventional Commit so semantic-release bumps correctly.

---

### Task 6: Wire the cloud routine (claude.ai)

Only after the repo PR is merged to `main` (routines clone the default branch).

- [ ] **Step 1: Mint the scoped PAT (USER action)**

GitHub → Settings → Developer settings → Fine-grained tokens → repository access
`Gharib89/crm` only → permissions: Contents (RW), Pull requests (RW), Issues (RW),
Workflows (RW). Copy the `github_pat_…` value; do not paste it into any file.

- [ ] **Step 2: Ensure cloud GitHub access (USER action)**

If not already done, run `/web-setup` in a CLI session (grants the cloud account
clone/push access to your repos). Confirm `Gharib89/crm` is reachable from
claude.ai/code.

- [ ] **Step 3: Create + configure the environment (USER action, claude.ai UI)**

At https://claude.ai/code/routines (or Desktop → Routines), create environment
`crm-ship` with the Network (Custom + the two domains + default package managers),
Environment variables (the five vars), and an empty Setup script — exactly per the
"Cloud environment config" section of `docs/agents/cloud-ship-routine.md`.

- [ ] **Step 4: Create the routine**

`/schedule daily cloud ship of the oldest ready-for-agent issue` (or the web "New
routine" form). Set: Instructions = the verbatim prompt from the runbook; Repository
= `Gharib89/crm` with **Allow unrestricted branch pushes** enabled; Environment =
`crm-ship`; model = strongest coding model. Do not attach the recurring schedule yet.

- [ ] **Step 5: Manual run against a known ready issue**

Label one issue `ready-for-agent`, then **Run now** (or `RemoteTrigger action=run` /
`/schedule run`). Open the run's session URL and verify end-to-end:
- bootstrap green (`whoami` returns the cloud org — confirms egress + secret),
- `/ship` opens a real PR with green CI, **stopped at the merge gate**.
A green run *status* ≠ success — read the transcript. If egress fails (`403
host_not_allowed`) revisit Step 3 domains; if push fails revisit unrestricted pushes.

- [ ] **Step 6: Rotate the OAuth client secret (USER action)**

In Entra ID, rotate the agent-cloud app's client secret; update `D365_CLIENT_SECRET`
in the `crm-ship` environment; re-run once to confirm `whoami` still passes. (Because
bootstrap reads the secret at run time, no cache rebuild is needed.) Retires the
value used interactively before this work.

- [ ] **Step 7: Attach the schedule**

Set weekday-daily; for the off-minute cron use `/schedule update` → `17 6 * * 1-5`.
Note the per-account daily run cap and the 1-hour minimum interval.

---

## Self-review

- **Spec coverage:** per-fire flow (Task 4 prompt), bootstrap contract (Task 3), secrets decision + rotation (Tasks 1/6), skill vendoring + "intentional copy" rationale (Task 2), one-issue-per-fire + merge gate + no-on-prem + host guard (Task 4), capability gate resolved with the network/branch-push corrections (findings section + Task 6), verification incl. manual-run-before-cron (Tasks 5/6). All present.
- **Placeholders:** none. The only credential values are entered by the user in the claude.ai UI (Tasks 6.1/6.3), never written to the repo.
- **Consistency:** profile name `agent-cloud`, host `orgd080ee1e.crm.dynamics.com`, the OAuth token domain `login.microsoftonline.com`, the five env vars, and the two secret names are identical across spec, bootstrap script, prompt, and environment config.
