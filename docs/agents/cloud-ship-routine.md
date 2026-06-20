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
`.claude/skills/` (alongside `ship`, `tdd`, `review`); it bootstraps the sandbox,
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
  - `api.github.com`   (gh: pr/issue/label/copilot-rerequest REST calls)
  - `github.com`       (`git push`/fetch over HTTPS + gh git ops)
  - `release-assets.githubusercontent.com` (gh binary download — setup only)
  - Note: the GitHub **MCP connector** is brokered through Anthropic and is exempt
    from this policy, but `gh` and `git push` hit GitHub **directly**, so these
    three are required — the routine's issue-claim state machine and `/ship`'s
    PR/CI/merge steps are all `gh`-native (gh auto-auths from `GH_TOKEN`).
- **Environment variables** (nothing org-specific is committed — the bootstrap
  reads every connection value from here, replacing `<…>` with your real values):
  - `D365_URL` = `https://<your-org>.crm.dynamics.com`
  - `D365_CLIENT_ID` = agent-cloud OAuth application (client) id
  - `D365_TENANT_ID` = Azure AD tenant id
  - `D365_CLIENT_SECRET` = agent-cloud OAuth client secret (rotate after wiring)
  - `GH_TOKEN` = fine-grained PAT, repo `Gharib89/crm`: Contents + Pull requests +
    Issues + Workflows (write)
  - `D365_E2E` = `1`
  - `D365_E2E_PROFILE` = `agent-cloud`
  - `D365_E2E_ALLOW_HOST` = `<your-org>.crm.dynamics.com` (must match `D365_URL`'s host)
- **Setup script:** install `gh` here once (cached into the image — it's a static,
  secret-free tool, so it belongs in the cached slot, not the per-fire bootstrap).
  The sandbox image ships without `gh`, and `/ship` + the claim state machine depend
  on it:
  ```
  GH_VERSION=2.94.0
  curl -fsSL "https://github.com/cli/cli/releases/download/v${GH_VERSION}/gh_${GH_VERSION}_linux_amd64.tar.gz" | tar -xz -C /tmp
  sudo install -m 0755 "/tmp/gh_${GH_VERSION}_linux_amd64/bin/gh" /usr/local/bin/gh
  gh --version
  ```
  (The per-fire bootstrap also installs `gh` if absent, so this slot is an
  optimization — it keeps the download out of every hourly fire.)

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
`ready-for-agent`) until a human merges and `Closes #N` closes it. `GH_TOKEN` needs
Issues:write (already in the env config) for `/ship`'s relabel + comments.

Stale-claim recovery: if a fire dies after claiming but before opening a PR, the issue
sits `agent-working` with no PR and is not retried — relabel it `ready-for-agent` by
hand to requeue.

One-time setup — create the claim label once (idempotent; skip if it exists):

```
gh label create agent-working --repo Gharib89/crm --color FBCA04 \
  --description "Claimed by /ship — in progress, or PR open awaiting human merge"
```

## Schedule

Min interval is 1 hour. Default to weekday-daily; for an exact off-minute cron use
`/schedule update` → `17 6 * * 1-5`. Create the routine, then **Run now** once
against a known `ready-for-agent` issue before relying on the schedule.
