# Cloud ship routine

A claude.ai **routine** (research preview) that ships the frontier issue (oldest
open `ready-for-agent`, unassigned, no open blockers) to a merge-ready PR via the
**`cloud-ship` skill**, which invokes `ship --unattended`, then stops at the merge
gate without merging. One issue per fire, at most `## Cloud lane`'s `PR cap:` open
PRs at a time (3, `docs/agents/ship.md`). Manage at https://claude.ai/code/routines
or via `/schedule` in the CLI.

## Routine prompt (fixed — paste once; never re-paste on a behavior change)

The agent behavior lives in the lock-recorded **`cloud-ship`** and **`ship`**
derived copies under `.claude/skills/`, which the cloud sandbox gets via its clone
of `main`, and in the ship profile `docs/agents/ship.md`. So the routine's
Instructions are one fixed sentence, identical in every repo that installs
`cloud-ship` (ship derives the repo from the clone's remote). Change what a fire
does by refreshing the skills or editing the profile and merging to `main`,
**not** by editing this prompt. Paste this verbatim into the routine's
Instructions once:

```
Run the cloud-ship skill.
```

The routine's model selector should be set to the strongest available coding model.

## Cloud environment config (claude.ai web UI — "Edit routine" → environment)

Configure a dedicated environment (e.g. `crm-ship`) and select it for the routine:

- **Network access → Custom**, Allowed domains (keep "include default package
  managers" checked, for pip/PyPI):
  - `login.microsoftonline.com`   (OAuth client-credentials token endpoint)
  - `<your-org>.crm.dynamics.com` (Dataverse Web API — your cloud org host)
  - `github.com`       (`git push`/fetch over HTTPS, and the bootstrap's gitleaks and actionlint downloads)
  - `release-assets.githubusercontent.com` (where those release downloads redirect)
  - Ship's `prepare` installs `gh` with `apt-get` (`tooling --install`, the one
    route the sandbox proxy passes) and every mechanic calls GitHub REST through
    it; the proxy refuses GraphQL and ship's GitHub adapter falls back to REST on
    that refusal (`.claude/skills/ship/reference/unattended.md`).
- **Environment variables** (nothing org-specific is committed — the bootstrap
  reads every connection value from here, replacing `<…>` with your real values):
  - `D365_URL` = `https://<your-org>.crm.dynamics.com`
  - `D365_CLIENT_ID` = agent-cloud OAuth application (client) id
  - `D365_TENANT_ID` = Azure AD tenant id
  - `D365_CLIENT_SECRET` = agent-cloud OAuth client secret (rotate after wiring)
  - `GH_TOKEN` = fine-grained PAT, repo `Gharib89/crm`, Contents: write,
    Issues: write, Pull requests: write: the credential `gh` and `git` use in
    the fire. Ship's preflight proves it with its `user` and repo reads;
    `gh auth status` reports a working token as invalid behind the proxy.
  - `D365_E2E` = `1`
  - `D365_E2E_PROFILE` = `agent-cloud`
  - `D365_E2E_ALLOW_HOST` = `<your-org>.crm.dynamics.com` (must match `D365_URL`'s host)
- **Setup script:** none. Ship's `prepare` repairs the image (`tooling
  --install`), then runs the profile's `## Cloud lane` `Bootstrap:`,
  `scripts/cloud-ship-bootstrap.sh`; outside a cloud sandbox (`CLAUDE_CODE_REMOTE`
  unset) the bootstrap exits 0 and does nothing. In the sandbox it creates `.venv`
  in the clone (where `scripts/local-gate.sh` looks for it, since the cloud run
  isolates in place) and installs crm `.[dev,docs]` plus `uv` into it, puts
  gitleaks and actionlint (version and sha256 pinned in the script) in
  `.venv/bin`, where the gate finds them without relying on the sandbox's `PATH`, then
  builds and tests the `agent-cloud` profile through `.venv/bin/python`. With any
  `D365_*` connection variable unset it skips the profile and exits 0: live e2e
  then hands off rather than runs.
  It needs a **Python >= 3.13** somewhere on `PATH` (crm's floor): it selects the
  first interpreter that satisfies the floor rather than trusting the image's default
  `python` (which has shipped as an older 3.x while a usable 3.13 was present), and
  builds the `.venv` from it. Set `CLOUD_SHIP_PYTHON` to force a
  specific interpreter; it must itself be >= 3.13 (a stale override is ignored and
  auto-detection resumes). If no >= 3.13 interpreter is found it fails fast before
  touching the profile.

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

## Concurrency & claim lifecycle

The routine fires on a schedule and a merge-ready PR can sit unmerged for a
while, so each fire must not re-pick an issue another fire already owns. Ship
claims by **assignee**: phase 1's `manage-issue <issue> take` assigns the run's
identity and comments, and selection skips any assigned issue. A fire never waits
at the merge gate, so a merge-ready issue is left assigned with its open PR until
a human merges and `Closes #N` closes it. Two backstops:

- **PR cap:** a fire stops `pr-queue-full` when `PR cap:` (3) PRs are already
  open — the operator's merge queue, not the backlog, is the bottleneck.
- **Stale-claim recovery:** a fire that dies after claiming but before opening a
  PR leaves the issue assigned with no PR and it is not retried — unassign it by
  hand to requeue. The blocked hand-off (label `ready-for-human` + unassign) is
  ship's own, not yours.

## Schedule

Min interval is 1 hour. Default to weekday-daily; for an exact off-minute cron use
`/schedule update` → `17 6 * * 1-5`. Create the routine, then **Run now** once
against a known `ready-for-agent` issue before relying on the schedule.
