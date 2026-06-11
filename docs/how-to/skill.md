# How-to: skill

Common `crm skill` recipes. See the [CLI reference](../reference/cli.md) for every flag.

## Install the bundled agent skill

```bash
crm skill install --target claude
```
Copies the bundled skill tree (`SKILL.md` + `reference/*.md`) into the agent's skill directory; `--target` is `claude | copilot | cursor` (default `claude`). The destination is recorded in `${CRM_HOME:-~/.crm}/installed-skills.json` so `crm self-update` can keep it in sync (see [self-update](self-update.md)).

## Install to a custom directory

```bash
crm skill install --dest ./my-skills --force
```
`--dest` overrides `--target`. When a skill already exists at the destination and `--force` is not given, an interactive terminal prompts to overwrite (declining aborts, exit 1); under `--json` or a non-TTY (agent/CI) it errors with `… already exists. Use --force to overwrite.` instead of prompting. `--force` overwrites without asking.

## Show the bundled skill's path

```bash
crm skill path
```
Prints the path of the bundled skill directory shipped inside the installed package.

## Uninstall the skill

```bash
crm skill uninstall --target claude
```
Removes the installed skill (`SKILL.md` + `reference/`, and the directory if empty) for the given target.
