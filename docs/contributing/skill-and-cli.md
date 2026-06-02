# Keeping the agent skill in sync with the CLI

## Source of truth

`crm/skills/SKILL.md` is the canonical agent skill. The package ships it
(`package_data` in `setup.py`) and `crm skill install` copies it into an agent's
skill directory:

| Target | Destination |
| --- | --- |
| `claude` | `~/.claude/skills/crm/SKILL.md` |
| `copilot` | `~/.copilot/skills/crm/SKILL.md` |
| `cursor` | `~/.cursor/rules/crm/SKILL.md` |

```bash
crm skill install --target claude --force
```

## When the CLI changes

After adding or changing a command, update `crm/skills/SKILL.md`, then reinstall with
`--force`. To detect drift:

```bash
diff crm/skills/SKILL.md ~/.claude/skills/crm/SKILL.md
```

## The bug loop used to build CRMWorx

While building the walkthrough, defects were handled with a hybrid policy:

- **Trivial / single-function:** write a failing test in `crm/tests/`, fix in `crm/`,
  re-run, continue — the fix lands in the same session.
- **Larger:** file a triaged GitHub issue with a minimal repro, work around, continue.

Issues filed during the run are tracked in
[GitHub Issues](https://github.com/Gharib89/crm/issues).
