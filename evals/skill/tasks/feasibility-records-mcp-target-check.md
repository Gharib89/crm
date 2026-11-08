---
id: feasibility-records-mcp-target-check
domain: records
tier: 2
source:
  type: firsthand
  url: null
# Dataverse MCP routing (#1131): a developer has Microsoft's Dataverse MCP server configured and
# asks for a small read. The skill's reference/dataverse-mcp.md says to prove the MCP target is
# the same org as the crm profile (crm connection whoami) before trusting its rows, and that the
# MCP user and the crm application user are different identities. Feasibility, not a do-task:
# the harness has no MCP server, so the graded signal is the agent naming the verification
# command, not a mutation. Agent-written, human-run (ADR 0030); never run in CI.
target: either
kind: feasibility
answer_key:
  cli_achievable: true
  required_commands:
    - connection whoami
evidence:
  - "reference/dataverse-mcp.md, 'Target evidence before trusting an MCP result': compare `crm connection whoami` (OrganizationId, meta.url host) with the org URL the MCP server was started with before relying on its rows."
  - "MS Learn (Dataverse MCP for non-Microsoft clients): the environment is selected by the org URL passed to `npx -y @microsoft/dataverse mcp <org url>`, so the command line is the evidence of which org MCP reads."
cleanup: []
---

You are assessing whether a task is achievable with the `crm` CLI. **Do not perform the
task or mutate the org.** Investigate the available commands (the `crm` skill, and
`crm --help` / `crm describe` as needed), then decide.

Task under assessment: *"I have Microsoft's Dataverse MCP server configured in this agent and
want to read the first few contact rows. Before I trust what it returns, how do I confirm it is
reading the same org my `crm` profile uses, using the `crm` CLI? Name the command."*

Write your answer as a single JSON object to a file named `feasibility.json` in your current
working directory, matching exactly this schema (no extra prose in the file):

```json
{
  "cli_achievable": true,
  "required_commands": ["<crm command>"],
  "rationale": "<one or two sentences>"
}
```

- `cli_achievable` (boolean): whether the CLI can supply the evidence you need.
- `required_commands` (list of strings): the `crm` command path(s) you would run.
- `rationale` (string): a brief justification, including what you compare it against.
