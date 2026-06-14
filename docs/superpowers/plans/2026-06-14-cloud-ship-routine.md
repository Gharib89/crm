# Cloud ship routine — Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Stand up a scheduled claude.ai cloud routine that ships the oldest open `ready-for-agent` issue in `Gharib89/crm` to a merge-ready PR via `/ship`, stopping at the human merge gate.

**Architecture:** A cloud trigger (claude.ai `RemoteTrigger`) bound to the repo fires on cron. Each fire clones the repo, runs an in-repo bootstrap script to provision the sandbox (install crm from source, build the `agent-cloud` profile from injected secrets, auth `gh`), picks one ready issue, and runs the vendored `/ship` skill against it. Secrets live only in the trigger's secret store; the repo carries no credentials.

**Tech Stack:** claude.ai code-triggers API (`RemoteTrigger`), `gh` CLI, `crm` CLI (Python, source install), bash, GitHub fine-grained PAT, Entra ID OAuth client-credentials.

**Spec:** `docs/superpowers/specs/2026-06-14-cloud-ship-routine-design.md`

---

## Refinements applied since the spec

- e2e env vars are set in the **trigger env config**, not exported by the bootstrap script (`export` dies with the subshell). Script side effects are on-disk only.
- `/ship` phase-3 uses `D365_E2E_PROFILE=agent-cloud` (secret stays in the one profile), not flat `D365_*`.

## File structure

| Path | Action | Responsibility |
|---|---|---|
| `.claude/skills/ship/` (3 files) | Create | Vendored `/ship` skill (composes tdd + review) |
| `.claude/skills/tdd/` (6 files) | Create | Vendored `tdd` skill |
| `.claude/skills/review/SKILL.md` | Create | Vendored `review` skill |
| `scripts/cloud-ship-bootstrap.sh` | Create | Per-fire sandbox provisioning |
| `docs/agents/cloud-ship-routine.md` | Create | Versioned source of the trigger prompt + wiring runbook |

`docs/agents/` and `docs/superpowers/` are in `mkdocs.yml`'s `exclude_docs:` — new docs there don't affect `mkdocs --strict` and need no nav edits. `.claude/` is not gitignored (only `.claude/scheduled_tasks.lock` is).

---

### Task 1: Confirm cloud-routine capability (BUILD GATE)

The `RemoteTrigger` create-body schema is unknown and no triggers exist to copy. The whole cloud approach depends on the trigger supporting a per-trigger **secret store**, **repo binding**, and a **setup/bootstrap command**. Resolve this before touching the repo.

**Files:** none (investigation + decision).

- [ ] **Step 1: Find the code-triggers / routines config schema**

Dispatch a `claude-code-guide` subagent:
> "Find the claude.ai code-triggers API (`POST /v1/code/triggers`) / Claude Code 'routines' configuration schema. I need to know whether a trigger can: (a) bind to a specific GitHub repo, (b) store per-trigger secrets injected as env vars at run time, (c) run a setup/bootstrap shell command before the prompt, (d) set non-secret env vars. Return the exact create-body field names for each, with a source URL."

- [ ] **Step 2: Decision gate**

- All four supported → continue to Task 2.
- Secret store **absent** → STOP. Cloud is a non-starter; pivot to the durable-local-cron fallback (out of scope for this plan — open a new brainstorm/plan: `CronCreate durable:true` running the same loop on this machine, which already has crm/profiles/gh/skills and needs no secret injection). Report the pivot to the user.

Expected: confirmation that secrets + repo binding + setup command exist, with field names recorded for Task 6.

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

- [ ] **Step 2: Re-scan for secrets / personal paths / GUIDs (public repo gate)**

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
# Provision a fresh claude.ai cloud sandbox to run /ship against the agent-cloud
# Dataverse org. Reads $D365_CLIENT_SECRET and $GH_TOKEN from the trigger secret
# store (env); never echoes them. Public (non-secret) config is inlined.
set -euo pipefail

: "${D365_CLIENT_SECRET:?set D365_CLIENT_SECRET in the trigger secret store}"
: "${GH_TOKEN:?set GH_TOKEN in the trigger secret store}"

URL="https://orgd080ee1e.crm.dynamics.com"
CLIENT_ID="4e156fdd-7cfe-487d-8608-c6844dcaf9ed"
TENANT_ID="727f34ab-fb54-4512-a624-5ed673dd203b"

# 1. crm CLI from source (not published to PyPI)
pip install -e ".[dev,docs]"

# 2. Build + activate the agent-cloud profile (non-interactive; plaintext store,
#    no OS keyring in the sandbox). WhoAmI-tests and activates on success.
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

# 3. GitHub auth from the injected token
echo "$GH_TOKEN" | gh auth login --with-token

# 4. Sanity check (early signal if cloud egress is blocked)
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

Scheduled claude.ai routine: ships the oldest open `ready-for-agent` issue to a
merge-ready PR via `/ship`, then stops at the human merge gate. One issue per fire.

## Trigger prompt (self-contained — paste verbatim into the trigger)

```
Objective: produce ONE merge-ready PR for Gharib89/crm and then stop.

1. Provision the sandbox: run `bash scripts/cloud-ship-bootstrap.sh` from the repo
   root. It installs the crm CLI, builds the active `agent-cloud` profile, and
   authenticates gh. Abort and report if it exits non-zero.
2. Pick the work item:
   NUM=$(gh issue list --repo Gharib89/crm --label ready-for-agent --state open \
         --json number --jq 'sort_by(.number)[0].number // empty')
   If NUM is empty, report "nothing ready" and STOP — do not open a PR.
3. Run the /ship skill on issue $NUM. Pin the cloud profile (`--profile agent-cloud`)
   on any crm command. Follow the repo's CLAUDE.md for test/gate/docs-sync/commit
   rules. This is the cloud Dataverse org only — never attempt on-prem work; if the
   issue can only be verified on-prem, report that and STOP.
4. /ship stops at the merge gate by design. Do NOT merge. Post the PR link and the
   disposition summary, then STOP for human merge approval.
```

## Trigger env (non-secret)

- `D365_E2E=1`
- `D365_E2E_PROFILE=agent-cloud`
- `D365_E2E_ALLOW_HOST=orgd080ee1e.crm.dynamics.com`

## Trigger secrets (secret store only — never committed)

- `D365_CLIENT_SECRET` — agent-cloud OAuth client secret (rotate after wiring)
- `GH_TOKEN` — fine-grained PAT scoped to Gharib89/crm: Contents + Pull requests +
  Issues + Workflows (write)

## Schedule

Default `17 6 * * 1-5` (weekday 06:17 local, off-minute). Create with manual run
first; attach cron only after one manual run yields a green merge-ready PR.
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
Expected: `no secrets in diff`. (The OAuth secret and PAT never enter the repo — they live only in the trigger store.)

- [ ] **Step 4: Push + open PR**

```bash
git push -u origin feat/cloud-ship-routine
gh pr create --repo Gharib89/crm --base main --head feat/cloud-ship-routine \
  --title "feat(agents): cloud ship routine (vendored skills + bootstrap)" \
  --body "Stands up the cloud ship routine: vendors ship/tdd/review skills, adds the sandbox bootstrap script, and the routine prompt + wiring runbook. Cloud wiring (secrets + trigger) is done out-of-band per docs/agents/cloud-ship-routine.md. Spec: docs/superpowers/specs/2026-06-14-cloud-ship-routine-design.md"
```

- [ ] **Step 5: Drive Copilot review to the ceiling + green CI, then human-merge**

Copilot auto-reviews on PR creation (round 1, automatic). Triage findings, fix, re-request rounds 2+ via REST per CLAUDE.md (≤3-round ceiling). Wait for green CI. **Stop at the merge gate** — human approves the squash-merge. Squash subject must be a Conventional Commit so semantic-release bumps correctly.

---

### Task 6: Wire the cloud side (secrets + trigger)

Only after the repo PR is merged to `main` (the trigger clones `main`).

- [ ] **Step 1: Mint the scoped PAT (USER action)**

GitHub → Settings → Developer settings → Fine-grained tokens → only `Gharib89/crm` →
permissions: Contents (RW), Pull requests (RW), Issues (RW), Workflows (RW). Copy the
`github_pat_…` value. Do not paste it into any file.

- [ ] **Step 2: Create the trigger**

Use the field names recorded in Task 1. Indicative shape (adjust to the real schema):

```
RemoteTrigger action=create body={
  repo: "Gharib89/crm",
  prompt: <the verbatim prompt from docs/agents/cloud-ship-routine.md>,
  env: { D365_E2E: "1", D365_E2E_PROFILE: "agent-cloud",
         D365_E2E_ALLOW_HOST: "orgd080ee1e.crm.dynamics.com" },
  secrets: { D365_CLIENT_SECRET: <oauth secret>, GH_TOKEN: <pat> },
  setup: "bash scripts/cloud-ship-bootstrap.sh",
  schedule: <omit for now — manual run first>
}
```
Relay the returned claude.ai routine URL to the user.

- [ ] **Step 3: Manual run against a known ready issue**

Ensure at least one issue is labeled `ready-for-agent`, then `RemoteTrigger action=run trigger_id=<id>`. Watch on the returned claude.ai URL.
Expected: bootstrap green (`whoami` returns the cloud org), `/ship` produces a real PR with green CI, stopped at the merge gate.

- [ ] **Step 4: Rotate the OAuth client secret (USER action)**

In Entra ID, rotate the agent-cloud app's client secret; update `D365_CLIENT_SECRET` in the trigger store with the new value; confirm a second manual run still passes `whoami`. Retires the value used interactively before this work.

- [ ] **Step 5: Attach the schedule**

`RemoteTrigger action=update trigger_id=<id> body={ schedule: "17 6 * * 1-5" }`. Relay the parsed next-run time to the user.

---

## Self-review

- **Spec coverage:** per-fire flow (Task 4 prompt), bootstrap contract (Task 3), secrets decision + rotation (Tasks 1/6), skill vendoring + the "intentional copy" rationale (Task 2), one-issue-per-fire + merge gate + no-on-prem + host guard (Task 4 prompt + env), capability gate + local-cron fallback (Task 1), verification incl. manual-run-before-cron (Tasks 5/6). All present.
- **Placeholders:** none — the one deliberately indicative block is the `RemoteTrigger` create body, gated on Task 1 discovering the real schema (the unknown is acknowledged, not hidden).
- **Consistency:** profile name `agent-cloud`, host `orgd080ee1e.crm.dynamics.com`, the three env vars, and the two secret names are identical across spec, bootstrap script, prompt, and trigger config.
