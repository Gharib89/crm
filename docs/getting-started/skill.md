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

## Without the CLI (`npx skills`)

If you don't have the `crm` CLI installed, pull the skill straight from the repo
with the [`skills`](https://github.com/vercel-labs/skills) tool:

```bash
npx skills add Gharib89/crm --skill crm -g -y
```

This installs **only** the `crm` skill (`-g` global, `-y` non-interactive) into
your agent skill dirs. Skills fetched this way are not tracked by
[`crm self-update`](update.md) — re-run the command to update. Prefer
`crm skill install` when you already have the CLI.

Install to a custom directory with `--dest ./my-skills` (overrides `--target`); add
`--force` to overwrite an existing skill. See
[how-to: skill](../how-to/skill.md) for `path`, `uninstall`, and every flag.

Next: [use `/crm` with a coding agent](agent.md).
