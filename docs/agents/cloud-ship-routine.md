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
   Do NOT claim it here — /ship claims it in its phase 1 (removes ready-for-agent,
   adds agent-working, comments). Since the claim drops ready-for-agent, this picker
   never returns an issue that is already claimed or has an open PR.
3. Rename the working branch off the sandbox default. A cloud fire starts you on an
   auto `claude/<random>` branch; the repo convention (and /ship's) is
   `<type>/<slug>-$NUM` — `<type>` = `fix` for a bug, `feat` otherwise, `<slug>` a
   short kebab summary of the issue title. Switch before any commit so the PR branch
   is semantic, not the `claude/...` name:
   git switch -c fix/<slug>-$NUM   # or feat/<slug>-$NUM
   Then run /ship on issue $NUM by INVOKING THE SKILL TOOL (skill `ship`) — do not
   paraphrase or inline its steps from this prompt. /ship composes the `tdd` and
   `review` skills (all three ship as sibling skills in the clone's
   `.claude/skills/`): when /ship reaches those phases, invoke `tdd` / `review` via
   the Skill tool too — never hand-roll their logic inline. /ship's first action is
   to create its phase task list; if the Task tools (`TaskCreate`/`TaskUpdate`/
   `TaskList`) aren't loaded, run `ToolSearch` (`select:TaskCreate,TaskUpdate,TaskList`)
   before concluding they're unavailable — only if that returns nothing, track the
   ten phases in a markdown checklist instead and keep going (the list is a
   progress/resume aid, not a gate). Pin `--profile agent-cloud` on every crm
   command. Put "Closes #$NUM" in the PR body so merging auto-closes the issue and
   drops it from the queue. Follow the repo CLAUDE.md for test/gate/docs-sync/commit
   rules. Cloud Dataverse org ONLY — never on-prem; an issue that can only be verified
   on-prem counts as blocked (step 4).
4. If /ship CANNOT produce a merge-ready PR (ambiguous/underspecified, on-prem-only,
   or CI cannot be made green): do not leave it stuck as agent-working and do not
   return it to ready-for-agent (that would loop it forever). Hand it to a human:
   gh issue edit "$NUM" --repo Gharib89/crm --remove-label agent-working --add-label ready-for-human
   gh issue comment "$NUM" --repo Gharib89/crm --body "<one-line reason it is blocked>"
   then STOP.
5. On success /ship reaches the merge gate. The gate's instruction is to "wait" for
   the user to reply "merge" — that is written for an interactive CLI session. This is
   a one-shot hourly fire with NO in-session human, so do NOT wait, do NOT poll, and
   do NOT merge: the moment the PR is merge-ready (CI green, Copilot review addressed
   within the ≤3-round ceiling, mergeable), post the PR link + disposition summary and
   END the fire. A human merges out of band later; the squash "Closes #$NUM" closes
   the issue then. Leave the issue labeled agent-working — it has the open PR, so later
   fires skip it until the merge closes it.

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
