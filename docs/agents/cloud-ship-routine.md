# Cloud ship routine

A claude.ai **routine** (research preview) that ships the oldest open
`ready-for-agent` issue to a merge-ready PR via the **`cloud-ship` skill** (which
composes the **`ship`** skill), then stops at the merge gate without merging. One
issue per fire. Manage at https://claude.ai/code/routines or via `/schedule` in the CLI.

## Routine prompt (fixed — paste once; never re-paste on a behavior change)

The agent behavior lives in the repo-tracked **`cloud-ship` skill**
(`.claude/skills/cloud-ship/`), which the cloud sandbox gets via its clone of
`main`. So the routine's Instructions are a short, **fixed** pointer that only
*invokes* the skill — change what a fire does by editing the skill and merging to
`main` (the next clone picks it up), **not** by editing this prompt. Paste this
verbatim into the routine's Instructions once:

```
Objective: produce ONE merge-ready PR for Gharib89/crm and then stop.

Invoke the `cloud-ship` skill via the Skill tool and follow it exactly — do not
paraphrase or inline its steps. The skill is a sibling in the clone's
`.claude/skills/` (alongside `ship`, `tdd`, `code-review`); it bootstraps the sandbox,
picks the oldest open `ready-for-agent` issue, ships it via `ship`, and stops at
the merge gate without merging.

If the Skill tool cannot find `cloud-ship`, the repo clone is missing or stale —
report that and STOP rather than improvising the routine by hand.
```

The routine's model selector should be set to the strongest available coding model.

> **Why a skill, not an inline prompt.** Earlier the full routine logic lived in
> this prompt block, so every behavior tweak meant re-pasting it into the claude.ai
> routine config by hand. Moving it into a tracked skill (`.claude/skills/cloud-ship/`)
> makes the repo the single source of truth — version-controlled and reviewable —
> and keeps the pasted prompt fixed.

## Cloud environment config (claude.ai web UI — "Edit routine" → environment)

Configure a dedicated environment (e.g. `crm-ship`) and select it for the routine:

- **Network access → Custom**, Allowed domains (keep "include default package
  managers" checked, for pip/PyPI):
  - `login.microsoftonline.com`   (OAuth client-credentials token endpoint)
  - `<your-org>.crm.dynamics.com` (Dataverse Web API — your cloud org host)
  - `github.com`       (`git push`/fetch over HTTPS)
  - Note: all GitHub **API** access (issue picker, PR create/read, review
    re-request, comments) runs through the GitHub **MCP connector**, which is
    brokered through Anthropic and **exempt** from this policy — so no
    `api.github.com` entry is needed. `gh`'s repo/PR/issue REST endpoints are in
    fact **gated** by the sandbox egress proxy (403), which is why the fire uses
    MCP; see cloud-ship SKILL.md → "GitHub access in a fire". The only direct
    GitHub egress is `git` push/fetch over `github.com` (brokered credentials).
  - Note: other repo docs the fire follows still show literal `gh` commands —
    most of `docs/agents/issue-tracker.md`, and `/ship`'s phase-1 `agent-working`
    claim (label edit + comment). In a fire those `gh` calls **also** 403; the
    cloud-ship SKILL.md "GitHub access in a fire" mapping table translates every
    one (including the `gh issue` operations) to its `mcp__github__*` equivalent
    and outranks the literal `gh` in those docs for the duration of a fire.
- **Environment variables** (nothing org-specific is committed — the bootstrap
  reads every connection value from here, replacing `<…>` with your real values):
  - `D365_URL` = `https://<your-org>.crm.dynamics.com`
  - `D365_CLIENT_ID` = agent-cloud OAuth application (client) id
  - `D365_TENANT_ID` = Azure AD tenant id
  - `D365_CLIENT_SECRET` = agent-cloud OAuth client secret (rotate after wiring)
  - `GH_TOKEN` = fine-grained PAT, repo `Gharib89/crm` — now only a fallback
    credential for `git` push/fetch (Contents: write). The fire's GitHub **API**
    work (issues, PRs, reviews) goes through the brokered MCP connector, not this
    token; leave it set only if your environment's `git` isn't otherwise
    authenticated for `github.com`.
  - `D365_E2E` = `1`
  - `D365_E2E_PROFILE` = `agent-cloud`
  - `D365_E2E_ALLOW_HOST` = `<your-org>.crm.dynamics.com` (must match `D365_URL`'s host)
- **Setup script:** none required for GitHub — the fire drives GitHub through the
  MCP connector and `git` (both already available), so there is no `gh` to install.
  The per-fire `scripts/cloud-ship-bootstrap.sh` handles the rest (crm CLI, profile).
  It needs a **Python >= 3.13** somewhere on `PATH` (crm's floor) — it selects one
  itself rather than trusting the image's default `python` (which has shipped as an
  older 3.x while a usable 3.13 was present); override with `CLOUD_SHIP_PYTHON` if
  needed. If no 3.13 is found it fails fast before touching the profile.

## Permissions

- Enable **"Allow unrestricted branch pushes"** for `Gharib89/crm` — `/ship` pushes
  `feat/*` branches; without this, only `claude/*` pushes are allowed.
- Connectors: all your connected MCP servers are added by default; a routine can use
  every tool from an included connector without asking. **Keep Microsoft Learn and
  Context7** (and Exa, if connected) — MCP traffic is brokered through Anthropic, so
  these work under the Custom network policy above (no allowed-domain entry needed)
  and give the agent D365 / library docs during `/ship`. Remove only connectors the
  routine genuinely doesn't need. Note: the `ctx7` **CLI** (npx) is direct sandbox
  egress and is blocked by the Custom network policy — rely on the Context7
  **connector** instead. Connectors must be account-level
  (claude.ai/customize/connectors); local `claude mcp add` servers don't appear in
  routines.

## Concurrency & issue-label lifecycle

The routine fires hourly and a merge-ready PR can sit unmerged for a while, so each
fire must not re-pick an issue another fire already owns. The `agent-working` claim
label gives one owner per issue: `/ship` claims the issue itself (phase 1) and
comments the PR link (phase 6), so the routine only *picks* — it does not relabel at
the start. The one relabel the routine owns is the **blocked** hand-off (step 4:
`agent-working` → `ready-for-human`), since not-shippable is a routine policy, not a
`/ship` step. The claim convention lives in `CLAUDE.md` → "Triage labels".

Because a fire never waits at the merge gate (step 5), a merge-ready issue is left
`agent-working` with its open PR; later fires skip it (it no longer carries
`ready-for-agent`) until a human merges and `Closes #N` closes it. The relabel +
comments run through the MCP connector's issue-write tools, not `GH_TOKEN`.

Stale-claim recovery: if a fire dies after claiming but before opening a PR, the issue
sits `agent-working` with no PR and is not retried — relabel it `ready-for-agent` by
hand to requeue.

One-time setup — create the claim label once (idempotent; skip if it exists). This
is a human step run **outside** the fire (locally where `gh` works, or via the
GitHub web UI), not part of the routine:

```
gh label create agent-working --repo Gharib89/crm --color FBCA04 \
  --description "Claimed by /ship — in progress, or PR open awaiting human merge"
```

## Schedule

Min interval is 1 hour. Default to weekday-daily; for an exact off-minute cron use
`/schedule update` → `17 6 * * 1-5`. Create the routine, then **Run now** once
against a known `ready-for-agent` issue before relying on the schedule.
