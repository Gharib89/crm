# Install the skill

`crm` ships an agent skill that teaches a coding agent how to drive Dynamics 365.
Install it into your agent's skill directory:

```bash
crm skill install --target claude
```

`--target` is `claude | copilot | cursor` (default `claude`). This copies the
bundled skill tree (`SKILL.md` + `reference/*.md`) into the agent's skill directory
and records the destination so [`crm self-update`](update.md) keeps it in sync as the
CLI upgrades.

Install to a custom directory with `--dest ./my-skills` (overrides `--target`); add
`--force` to overwrite an existing skill. See
[how-to: skill](../how-to/skill.md) for `path`, `uninstall`, and every flag.

Next: [use `/crm` with a coding agent](agent.md).
