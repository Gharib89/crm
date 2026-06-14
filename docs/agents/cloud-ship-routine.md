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
