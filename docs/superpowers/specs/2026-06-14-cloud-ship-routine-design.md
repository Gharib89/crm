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
  fire: installs `crm` from source, rebuilds the `agent-cloud` profile from
  injected secrets, supplies the `/ship` skill (vendored into the repo), and
  authenticates `gh` from an injected token.
- **On-prem is out of scope** — the sandbox cannot reach `internalcrm.moce.local`
  (no VPN/egress). Only the cloud Dataverse org (`orgd080ee1e.crm.dynamics.com`)
  is reachable. Issues that can only be verified on-prem must not be picked up.

**Fallback:** if the `RemoteTrigger` config has no secret store (unverified — see
Open Risks), cloud is a non-starter and we fall back to a **durable local
`CronCreate`** routine on this machine (full parity, zero secret exposure).

## Scope

In scope:
1. Vendor the `/ship`, `tdd`, `review` skills into the repo so a fresh clone has them.
2. A repo bootstrap script the routine runs each fire to provision the sandbox.
3. A versioned copy of the routine prompt in the repo (source of truth for the trigger).
4. Creating the cloud trigger (`RemoteTrigger`) + storing secrets in its secret store.

Out of scope (non-goals):
- Auto-merging. `/ship` always stops at the human merge gate; the routine inherits that.
- On-prem issues / `agent-on-prem`.
- Batch-processing many issues in one fire (one issue per fire — keeps runs bounded;
  a failure on issue N doesn't block N+1, which is just the next fire).
- Triage. The routine consumes `ready-for-agent` issues; it does not label them.
  Labeling stays a human/`/triage` step.

## Architecture

### Components

| Component | Where it lives | Responsibility |
|---|---|---|
| Vendored skills | repo `.claude/skills/{ship,tdd,review}/` | Make `/ship` available to the cloud clone |
| Bootstrap script | repo `scripts/cloud-ship-bootstrap.sh` | Install crm, build profile, auth gh, export e2e env |
| Routine prompt | repo `docs/agents/cloud-ship-routine.md` | Versioned source of the self-contained trigger prompt |
| Cloud trigger | claude.ai (via `RemoteTrigger`) | Cron schedule + prompt + repo binding + secret refs |
| Secrets | claude.ai routine secret store | `D365_CLIENT_SECRET`, `GH_TOKEN` — never committed |

### Per-fire flow

```
fire
 └─ sandbox clones Gharib89/crm
 └─ run scripts/cloud-ship-bootstrap.sh   (provision: crm, profile, gh, env)
 └─ pick issue:
      gh issue list --label ready-for-agent --state open \
        --json number --jq 'sort_by(.number)[0].number'
      ├─ none → exit clean ("nothing ready"), no PR
      └─ N    → /ship N
                 ├─ phase 0–7: worktree, TDD, e2e (cloud), self-review, PR,
                 │             Copilot loop, green CI
                 └─ STOP at merge gate → merge-ready PR on claude.ai
```

### Bootstrap script contract (`scripts/cloud-ship-bootstrap.sh`)

Idempotent; reads secrets from env (injected by the trigger), never echoes them:

1. `pip install -e ".[dev,docs]"` — crm CLI from source (not on PyPI).
2. Build the `agent-cloud` profile non-interactively (flag-driven `crm profile add`),
   passing `--client-secret "$D365_CLIENT_SECRET"`. Public config baked into the
   script: `auth=oauth`, `client_id=4e156fdd-7cfe-487d-8608-c6844dcaf9ed`,
   `tenant_id=727f34ab-fb54-4512-a624-5ed673dd203b`,
   `url=https://orgd080ee1e.crm.dynamics.com`, `publisher_prefix=ag_`,
   `default_solution=agsol`. WhoAmI-tests + activates.
3. Export e2e env for `/ship` phase 3:
   `D365_E2E=1`, flat `D365_URL/D365_AUTH=oauth/D365_CLIENT_ID/D365_TENANT_ID/D365_CLIENT_SECRET`,
   and `D365_E2E_ALLOW_HOST=orgd080ee1e.crm.dynamics.com` (cloud prod-host guard override).
4. `gh auth login --with-token <<<"$GH_TOKEN"` (or rely on `GH_TOKEN` in env).

### Secrets handling (decision: dedicated scoped token + app secret)

- Mint a **new fine-grained GitHub PAT** scoped to `Gharib89/crm` only, with
  Contents + Pull requests + Issues + Workflows write — **not** the personal
  `gho_` token. Store as `GH_TOKEN` in the trigger secret store.
- Store the OAuth `client_secret` as `D365_CLIENT_SECRET` in the trigger secret store.
- **Rotate the agent-cloud OAuth client secret** in Entra ID after wiring, so the
  value previously used interactively is retired.
- Secrets exist **only** in the claude.ai routine secret store. Never written to
  the repo, the bootstrap script, the prompt, or any committed file. The bootstrap
  script references them by env-var name only.

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
- **Ambiguity stop** — `/ship` stops in phase 1 if the issue is underspecified;
  the routine surfaces that on claude.ai instead of building the wrong thing.
- **Host guard** — `D365_E2E_ALLOW_HOST` opts in only the one designated cloud test
  host; without it the suite refuses destructive runs against `*.dynamics.com`.
- **Secret hygiene** — bootstrap never echoes secret values; secrets only in the store.

## Open risks (resolve at build time)

1. **`RemoteTrigger` secret store + repo binding schema is unverified.** The create
   body is freeform and no triggers exist yet to copy. Before relying on cloud,
   confirm the trigger supports (a) binding to `Gharib89/crm`, (b) a per-trigger
   secret store, (c) a pre-run/setup command. **If any is missing → fall back to
   durable local `CronCreate`.** This is the first build step.
2. **Sandbox egress to `*.crm.dynamics.com`.** Assumed reachable (public internet);
   confirm with a `crm connection whoami` in a first manual `RemoteTrigger run`.
3. **`pip` availability / Python version in the sandbox.** `crm` pins py3.9+ behavior;
   confirm the sandbox toolchain during the first manual run.
4. **Skill provenance/licensing.** `ship/tdd/review` are personal skills; confirm
   they're ours to publish before committing to a public repo.

## Verification

- **Repo changes** (skills, script, prompt doc): the offline `pytest` suite + `mkdocs
  build --strict` still pass; bootstrap script passes `bash -n` and shellcheck.
- **Cloud wiring**: one manual `RemoteTrigger run` (not on schedule) against a known
  `ready-for-agent` issue → expect a real merge-ready PR + green CI, stopped at the
  merge gate. Only after that passes do we attach the cron schedule.
- **Cadence**: default to a single daily off-peak fire (e.g. `17 6 * * 1-5`); tune
  after observing run cost/time.

## Build order (for the plan)

1. Verify `RemoteTrigger` capabilities (gate; fallback to local cron if unmet).
2. Vendor skills + add bootstrap script + add routine-prompt doc → PR → merge.
3. Mint scoped PAT, set trigger secrets, rotate OAuth secret.
4. Create trigger (manual-run first, then attach cron).
