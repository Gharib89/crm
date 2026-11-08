---
id: feasibility-automation-mcp-invoke-api-retry
domain: automation
tier: 3
source:
  type: firsthand
  url: null
# Dataverse MCP routing (#1131): a sandbox Custom API call through the MCP tool `invoke_api`
# timed out. The trap is retrying through a second tool (`crm action invoke`) while the first
# call may have succeeded, doubling a mutation. reference/dataverse-mcp.md says to read the
# outcome back (`crm query odata`) before any retry. Feasibility on purpose: a do-task would
# need a live Custom API and an MCP server the harness does not have. Agent-written, human-run
# (ADR 0030); never run in CI.
target: either
kind: feasibility
answer_key:
  cli_achievable: true
  required_commands:
    - query odata
evidence:
  - "reference/dataverse-mcp.md, '`invoke_api` may mutate': read the outcome back (`crm query` or MCP `read_query`) before any retry and never retry through a second tool until the read shows the first call did nothing."
  - "MS Learn (Dataverse MCP server tools): `invoke_api` is described only as 'Invoke Dataverse Custom API'; the page does not say whether a given API reads or writes, so a call must be treated as a possible write."
cleanup: []
---

You are assessing whether a task is achievable with the `crm` CLI. **Do not perform the
task or mutate the org.** Investigate the available commands (the `crm` skill, and
`crm --help` / `crm describe` as needed), then decide.

Task under assessment: *"In a sandbox org I called a Custom API through the Dataverse MCP
server's `invoke_api` tool and it timed out with no result. The API creates a row in a custom
table. Before I retry the call (possibly with `crm action invoke`), how do I use the `crm` CLI
to find out whether the first call already took effect? Name the command."*

Write your answer as a single JSON object to a file named `feasibility.json` in your current
working directory, matching exactly this schema (no extra prose in the file):

```json
{
  "cli_achievable": true,
  "required_commands": ["<crm command>"],
  "rationale": "<one or two sentences>"
}
```

- `cli_achievable` (boolean): whether the CLI can answer the question.
- `required_commands` (list of strings): the `crm` command path(s) you would run first.
- `rationale` (string): a brief justification, including whether you would retry yet.
