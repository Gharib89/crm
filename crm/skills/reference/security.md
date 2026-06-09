# Security — roles and role assignment

List security roles and assign them to users or teams. Group: `security`.
Flags/choices: `crm security --help`.

```bash
crm --json security list-roles
crm --json security list-roles --business-unit <bu-guid>     # scope to one BU

crm --json security list-user-roles <user-guid>              # roles on a user
crm --json security list-team-roles <team-guid>              # roles on a team

# assign-role requires exactly one of --to-user / --to-team, and --yes non-interactively
crm --json security assign-role <role-guid> --to-user <user-guid> --yes
crm --json security assign-role <role-guid> --to-team <team-guid> --yes
```

**Key gotcha — roles are business-unit-scoped.** A role belongs to exactly one
business unit and can only be assigned to users or teams **within that same business
unit.** Assigning a role whose BU differs from the principal's BU fails with a
`forbidden` (403). Pick a role from the same business unit as the target user/team
(see the `forbidden` row in `reference/troubleshooting.md`).

Role assignment is **cumulative and not cleanly reversible** — omitting `--yes` in a
non-interactive context aborts (exit 1). The command also carries the standard
admin-header options (`--as-user`, `--as-user-object-id`, `--suppress-dup-detection`,
`--bypass-plugins`).
