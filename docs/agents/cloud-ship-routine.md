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
   environment's D365_* connection variables, and confirms the cloud org via whoami.
   If it exits non-zero, report the failure and STOP.
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

Working standards (the operator's global coding philosophy, not in the cloned repo;
the repo's own CLAUDE.md and /ship already cover tests, gates, and the merge flow):
- Don't build the wrong thing. Surface tradeoffs; if the issue is ambiguous or
  underspecified, STOP and report rather than guessing.
- Simplicity first. Minimum code that solves the problem — no features beyond what
  was asked, no abstractions for single-use code, no configurability that wasn't
  requested, no error handling for impossible cases. "Would a senior engineer call
  this overcomplicated?"
- Surgical changes. Touch only what you must. Read a file before editing it and grep
  for callers before changing a function. Don't improve/refactor adjacent code; match
  existing style. Remove only orphans your change created; mention unrelated dead
  code, don't delete it. Every changed line must trace to the issue.
- Plan first on multi-step or architectural work; delegate research/parallel analysis
  to subagents to protect context. Find root causes — no symptom patches, no
  TODO-as-excuse, no commented-out blocks, no swallowed errors. Run an elegance pass
  on your own non-trivial new code (not adjacent existing code).
- Comments: document public APIs and non-obvious WHY (constraints, invariants,
  workarounds); skip narrative comments on internals.
- Keep the PR and disposition summary concise, but always include the WHY.
```

The routine's model selector should be set to the strongest available coding model.

## Cloud environment config (claude.ai web UI — "Edit routine" → environment)

Configure a dedicated environment (e.g. `crm-ship`) and select it for the routine:

- **Network access → Custom**, Allowed domains (keep "include default package
  managers" checked, for pip/PyPI):
  - `login.microsoftonline.com`   (OAuth client-credentials token endpoint)
  - `<your-org>.crm.dynamics.com` (Dataverse Web API — your cloud org host)
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
- **Setup script:** leave empty (bootstrap runs from the prompt, see above).

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

## Schedule

Min interval is 1 hour. Default to weekday-daily; for an exact off-minute cron use
`/schedule update` → `17 6 * * 1-5`. Create the routine, then **Run now** once
against a known `ready-for-agent` issue before relying on the schedule.
