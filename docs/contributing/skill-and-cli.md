# Keeping the agent skill in sync with the CLI

> **Status:** scaffold. Filled in by Plan 3.

The agent skill source of truth is `crm/skills/SKILL.md`. Installing it into an agent
copies that file:

```bash
crm skill install --target claude --force
```

This page documents the sync workflow and the bug loop used while building CRMWorx.
