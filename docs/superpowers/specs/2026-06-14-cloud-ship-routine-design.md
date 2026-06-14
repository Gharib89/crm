# Cloud ship routine — design

**Status:** draft for review · **Date:** 2026-06-14 · **Author:** Ahmed Gharib (with Claude)

## Goal

A scheduled **claude.ai cloud routine** that, on each fire, takes the oldest open
`ready-for-agent` issue in `Gharib89/crm` from nothing to a **merge-ready PR** by
running the `/ship` pipeline, then **stops at the human merge gate**. Many fires →
many merge-ready PRs waiting for human approval. Never auto-merges.

## Why cloud (and the honest cost)

Chosen so it runs unattended even while the local machine is off, with results
surfaced on claude.ai. The cost, accepted explicitly:

- A cloud sandbox is **not** "the same machine." It re-provisions everything per
  fire: installs `crm` from source, builds the `agent-cloud` profile from the
  environment's injected secret, and loads the `/ship` skill from the cloned repo.
  `gh` authenticates from `GH_TOKEN` in the environment.
- **On-prem is out of scope** — the sandbox cannot reach `internalcrm.contoso.local`
  (no VPN/egress). Only the cloud Dataverse org (`<your-org>.crm.dynamics.com`)
  is reachable. Issues that can only be verified on-prem must not be picked up.

**Capability confirmed** (see Capability findings): claude.ai Routines support repo
binding, repo-committed skills, environment-level secrets/env-vars, a setup script,
and cron — so cloud is viable. **Fallback** if research-preview limits bite (daily
run cap, 1-hour minimum interval, or network policy blocks Dataverse): a **durable
local `CronCreate`** routine on this machine (full parity, zero secret exposure).

## Scope

In scope:
1. Vendor the `/ship`, `tdd`, `review` skills into the repo so a fresh clone has them.
2. A repo bootstrap script the routine runs each fire to provision the sandbox.
3. A versioned copy of the routine prompt + wiring runbook in the repo.
4. Configuring the claude.ai cloud environment (network + secret env-vars) and the
   routine (prompt + bound repo + environment + schedule).

Out of scope (non-goals):
- Auto-merging. `/ship` always stops at the human merge gate; the routine inherits that.
- On-prem issues / `agent-on-prem`.
- Batch-processing many issues in one fire (one issue per fire — keeps runs bounded;
  a failure on issue N doesn't block N+1, which is just the next fire).
- Triage. The routine consumes `ready-for-agent` issues; it does not decide which
  issues are ready (that stays a human/`/triage` step). It does manage its own
  claim/handoff labels — `agent-working` and `ready-for-human` — to serialize work
  across fires (see Per-fire flow / Error handling & safety).

## Architecture

### Components

| Component | Where it lives | Responsibility |
|---|---|---|
| Vendored skills | repo `.claude/skills/{ship,tdd,review}/` | Make `/ship` loadable by the cloud clone |
| Bootstrap script | repo `scripts/cloud-ship-bootstrap.sh` | Install crm + build profile + whoami (run from the prompt) |
| Routine prompt + runbook | repo `docs/agents/cloud-ship-routine.md` | Versioned prompt + claude.ai wiring steps |
| Cloud environment | claude.ai (web UI) | Network policy, env vars **incl. secrets**, setup script |
| Routine | claude.ai (web UI / `/schedule` / `RemoteTrigger`) | Prompt + bound repo + environment + schedule |
| Secrets | environment variables on the cloud environment | `D365_CLIENT_SECRET`, `GH_TOKEN` — never committed |

### Per-fire flow

```
fire
 └─ sandbox clones Gharib89/crm
 └─ prompt step 1: bash scripts/cloud-ship-bootstrap.sh   (install crm, build profile, whoami)
 └─ pick issue:
      gh issue list --label ready-for-agent --state open \
        --json number --jq 'sort_by(.number)[0].number // empty'
      ├─ none → exit clean ("nothing ready"), no PR
      └─ N    → CLAIM: ready-for-agent → agent-working (so the next fire skips N)
                 └─ /ship N  (PR body: "Closes #N")
                     ├─ shipped  → STOP at merge gate; issue stays agent-working
                     │             until the human merge closes it
                     └─ blocked  → agent-working → ready-for-human + reason, STOP
```

### Bootstrap script contract (`scripts/cloud-ship-bootstrap.sh`)

**Runs from the prompt's first step, not the environment's cached setup-script
slot** — so it always reads the current connection values (rotation needs no
cache rebuild) and never bakes them into a cached image. Reads `$D365_URL`,
`$D365_CLIENT_ID`, `$D365_TENANT_ID`, and `$D365_CLIENT_SECRET` from the
environment (failing fast if any is unset); never echoes the secret:

1. `pip install -e ".[dev,docs]"` — crm CLI from source (not on PyPI).
2. Build + activate the `agent-cloud` profile non-interactively (`crm profile add
   --store-password-plaintext --yes`, no keyring in sandbox), passing the four
   `$D365_*` values above. Only non-identifying config is committed in the script —
   `auth-scheme=oauth`, `api-version=v9.2`, `publisher_prefix=ag_`,
   `default_solution=agsol`; the org URL and the client/tenant ids come from the
   environment. WhoAmI-tests + activates.
3. Sanity: `crm --profile agent-cloud connection whoami` (early signal if egress
   is blocked or the secret is wrong).

`gh` needs **no login step** — it auto-reads `GH_TOKEN` from the environment. The
`/ship` phase-3 vars are also **environment variables** (not script exports):
`D365_E2E=1`, `D365_E2E_PROFILE=agent-cloud` (target inferred = cloud; secret stays
only in the profile), `D365_E2E_ALLOW_HOST=<your-org>.crm.dynamics.com` (cloud
prod-host guard override).

### Secrets handling (decision: dedicated scoped token + app secret)

- Mint a **new fine-grained GitHub PAT** scoped to `Gharib89/crm` only, with
  Contents + Pull requests + Issues + Workflows write — **not** the personal
  `gho_` token. Set as the `GH_TOKEN` environment variable.
- Set the OAuth `client_secret` as the `D365_CLIENT_SECRET` environment variable.
- **Rotate the agent-cloud OAuth client secret** in Entra ID after wiring, so the
  value previously used interactively is retired.
- Secrets exist **only** as environment variables on the cloud environment, entered
  in the claude.ai UI. Never written to the repo, the bootstrap script, the prompt,
  or any committed file. The script references them by env-var name only.

### Skill vendoring (decision: vendor into repo)

Commit `~/.claude/skills/{ship,tdd,review}/` into the repo's `.claude/skills/`.
Verified clean: no secrets, no personal absolute paths, no GUIDs; they reference
each other by name so composition survives. This is distinct from `crm/skills/`
(the shipped CRM-operation skill) — these are workflow skills for the cloud agent.

> Note: this is the one in-repo `.claude/skills/` copy we intentionally keep. It
> does **not** violate the "never track an in-repo `.claude/skills/` copy" rule,
> which targets duplicating the **crm** skill (whose source of truth is
> `crm/skills/`). `ship/tdd/review` have no in-repo source of truth — vendoring is
> the only way the cloud clone gets them.

## Error handling & safety

- **Never merge** — inherited from `/ship`'s merge gate.
- **Never proceed on red** — `/ship` bounds self-fix-and-retry, then stops and reports.
- **No `ready-for-agent` issue** → clean no-op exit, no empty PR.
- **One issue per fire** — a failure is isolated to that fire.
- **No double-pick (must-set label)** — hourly fires + a long-lived unmerged PR mean
  a later fire could otherwise re-pick an in-progress / PR-open issue. The fire
  **claims** its issue first (`ready-for-agent` → `agent-working`), which removes it
  from the picker's `--label ready-for-agent` query; it stays claimed until the human
  merge closes it via `Closes #N`. A blocked run relabels `agent-working` →
  `ready-for-human` (not back to `ready-for-agent`, which would loop). A crashed run
  leaves a stale `agent-working` with no PR → manual requeue. Requires the
  `agent-working` label to exist and `GH_TOKEN` Issues:write.
- **Ambiguity stop** — `/ship` stops in phase 1 if the issue is underspecified;
  the routine surfaces that on claude.ai instead of building the wrong thing.
- **Host guard** — `D365_E2E_ALLOW_HOST` opts in only the one designated cloud test
  host; without it the suite refuses destructive runs against `*.dynamics.com`.
- **Secret hygiene** — bootstrap never echoes secret values; secrets only in the env.
- **Network policy (must-set)** — the Default env "Trusted" policy blocks Dataverse;
  set the env to **Custom** allowed domains: `login.microsoftonline.com` (OAuth token)
  + `<your-org>.crm.dynamics.com` (Web API), keeping the default package-manager list.
- **Branch push (must-set)** — routines push only `claude/*` by default; `/ship` pushes
  `feat/*`, so enable **"Allow unrestricted branch pushes"** for the repo.

## Capability findings (Task 1 — resolved)

Source: https://code.claude.com/docs/en/routines.md. Cloud is viable. Repo binding ✓,
repo-committed skills ✓ (vendoring required), environment-level secrets/env-vars ✓,
setup script ✓ (cached — so we run bootstrap from the prompt instead), cron ✓
(min 1-hour interval, per-account daily run cap), `gh` via `GH_TOKEN` env ✓. Two
must-set environment toggles (network domains, unrestricted push) above. Research
preview: API/limits may change; a green run *status* ≠ task success — read the run.

Remaining items to confirm during the first manual run (not blockers):
- `pip` / Python toolchain present in the sandbox to install `crm` from source.
- `crm profile add` succeeds non-interactively for a not-yet-existing profile.

Skill provenance/licensing — `ship/tdd/review` verified clean and ours to publish.

## Verification

- **Repo changes** (skills, script, prompt doc): the offline `pytest` suite + `mkdocs
  build --strict` still pass; bootstrap script passes `bash -n` and shellcheck.
- **Cloud wiring**: one manual **Run now** (not on schedule) against a known
  `ready-for-agent` issue → expect a real merge-ready PR + green CI, stopped at the
  merge gate. Read the run transcript (status ≠ success). Only then attach the schedule.
- **Cadence**: default weekday-daily off-peak (`17 6 * * 1-5` via `/schedule update`);
  tune after observing run cost/time and the daily run cap.

## Build order (for the plan)

1. Vendor skills + add bootstrap script + add routine-prompt doc → PR → merge.
2. Mint scoped PAT; create the `crm-ship` environment (network + env vars + secrets);
   rotate the OAuth secret.
3. Create the routine (prompt + repo + env + unrestricted push); **Run now** first,
   then attach the cron schedule.
