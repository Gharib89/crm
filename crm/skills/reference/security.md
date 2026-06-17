# Security — roles and role assignment

List security roles and assign them to users or teams. Group: `security`.
Flags/choices: `crm security --help`.

```bash
crm --json security list-roles
crm --json security list-roles --business-unit <bu-guid>     # scope to one BU

crm --json security list-user-roles <user-guid>              # roles on a user
crm --json security list-team-roles <team-guid>              # roles on a team

crm --json security user-privileges <user-guid>             # effective privileges on a user

# assign-role requires exactly one of --to-user / --to-team, and --yes non-interactively
crm --json security assign-role <role-guid> --to-user <user-guid> --yes
crm --json security assign-role <role-guid> --to-team <team-guid> --yes
```

**Key gotcha — roles are business-unit-scoped.** A role belongs to exactly one
business unit and can only be assigned to users or teams **within that same business
unit.** Assigning a role whose BU differs from the principal's BU fails with a
`forbidden` (403). Pick a role from the same business unit as the target user/team
(see the `forbidden` row in `reference/troubleshooting.md`).

**`user-privileges` is the only way to get a user's *effective* privileges** —
the resolved privilege set from the user's own roles **plus** team-inherited
ones (collapsed per privilege to its highest depth). The `list-*-roles` verbs
list *roles*, not privileges, and never resolve team inheritance. Each item is a
`RolePrivilege` with PascalCase keys (`PrivilegeName`, `Depth`, `PrivilegeId`,
`BusinessUnitId`, `RecordFilterId`, `RecordFilterUniqueName`); `--json` returns
them as a list.

**Gotcha — team-inherited privileges report at `Basic` depth only**, an
upstream `RetrieveUserPrivileges` limitation: a privilege the user holds solely
via a team understates its true depth here. Full inherited depth needs the
per-privilege messages, which this CLI does not wrap.

Role assignment is **cumulative and not cleanly reversible** — omitting `--yes` in a
non-interactive context aborts (exit 1). The command also carries the standard
admin-header options (`--as-user`, `--as-user-object-id`, `--suppress-dup-detection`,
`--bypass-plugins`).
