# How-to: skill

Common `crm skill` recipes. See the [CLI reference](../reference/cli.md) for every flag.

## Install the bundled agent skill

```bash
crm skill install --target claude
```
Copies the bundled skill tree (`SKILL.md` + `reference/*.md`) into `<target>/crm` in the agent's skill directory; `--target` is `claude | copilot | cursor` (default `claude`). The destination is recorded in `${CRM_HOME:-~/.crm}/installed-skills.json` so `crm self-update` can keep it in sync (see [self-update](self-update.md)).

## Install the migration skill

```bash
crm skill install --skill crm-migration
crm skill install --skill crm --skill crm-migration   # both at once
```
Two skills ship: `crm` (the default, operating the CLI) and `crm-migration` (assessing and verifying a CRM 2011 or on-prem to Dataverse online migration; it defers to `crm` for exact commands, so install both). `--skill` is repeatable, and each skill installs into its own `<target>/<skill name>` folder, so installing one never touches the other. With several `--skill` values the `--json` envelope lists one row per skill under `data.skills`. If one skill fails partway, the skills before it stay installed (or removed): the error names them, `meta.completed_skills` holds their rows and `meta.failed_skill` names the one that failed.

## Install without the CLI (`npx skills`)

```bash
npx skills add Gharib89/crm --skill crm -g -y
```
Pulls the `crm` skill directly from the repo using the [`skills`](https://github.com/vercel-labs/skills) tool — no `crm` install required. `--skill crm` selects only the shipped `crm` skill (`--skill crm-migration` the migration one; `-g` global, `-y` non-interactive). Unlike `crm skill install`, skills added this way are not registered for [`crm self-update`](self-update.md) sync; re-run the command to update.

## Install to a custom directory

```bash
crm skill install --dest ./my-skills --force
```
`--dest` overrides `--target` and names the skill's folder itself, so it takes one `--skill` (more is a usage error, exit 2). When a skill already exists at the destination and `--force` is not given, an interactive terminal prompts to overwrite (declining aborts, exit 1); under `--json` or a non-TTY (agent/CI) it errors with `… already exists. Use --force to overwrite.` instead of prompting. `--force` overwrites without asking.

## Show the bundled skill's path

```bash
crm skill path
```
Prints the path of the bundled skill directory shipped inside the installed package.

## Uninstall the skill

```bash
crm skill uninstall --target claude
```
Removes the installed skill (`SKILL.md` + `reference/`, and the directory if empty) for the given target. `--skill` picks which one, as for `install` (default `crm`).
