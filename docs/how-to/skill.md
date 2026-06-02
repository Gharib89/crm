# How-to: skill

Common `crm skill` recipes. See the [CLI reference](../reference/cli.md) for every flag.

## Install the bundled agent skill

```bash
crm skill install --target claude
```
Copies the bundled `SKILL.md` into the agent's skill directory; `--target` is `claude | copilot | cursor` (default `copilot`).

## Install to a custom directory

```bash
crm skill install --dest ./my-skills --force
```
`--dest` overrides `--target`; `--force` overwrites an existing `SKILL.md` at the destination.

## Show the bundled skill's path

```bash
crm skill path
```
Prints the path of the `SKILL.md` shipped inside the installed package.

## Uninstall the skill

```bash
crm skill uninstall --target claude
```
Removes the installed `SKILL.md` (and its directory if empty) for the given target.
