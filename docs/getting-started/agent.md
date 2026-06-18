# Use /crm with a coding agent

Once you've [installed the skill](skill.md), a skill-aware agent (Claude Code,
Copilot CLI) can drive Dynamics 365 for you in plain language.

## How it triggers

The skill activates when your request mentions Dynamics 365, D365 CE, Dataverse, the
Web API, FetchXML, or on-prem CRM. The agent then runs `crm` commands with `--json`
and reads the structured output — no copy-pasting GUIDs.

## What it looks like

```text
You:  List the top 5 accounts by name from my D365 org.

Agent:  Running: crm query account --top 5 --select name --order-by name --json
        Here are the 5 accounts:
          1. Adventure Works
          2. Coho Vineyard
          3. Contoso Ltd
          4. Fabrikam, Inc.
          5. Northwind Traders

You:  Create a new account called "Tailspin Toys".

Agent:  Running: crm entity create account --data '{"name":"Tailspin Toys"}' --json
        Created account Tailspin Toys (id 8a1c…).
```

## Why `--json`

Every `crm` command emits a structured envelope under `--json`, so the agent reads
results deterministically instead of scraping human text. Use `--dry-run` to have the
agent preview a mutation before it runs.

Prerequisites: a working [profile](add-profile.md) (the agent uses your active
connection) and the [installed skill](skill.md).
