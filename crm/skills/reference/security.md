# Security — roles, role assignment, and record sharing (POA)

List security roles and assign them to users or teams; share individual records
with principals via the Dataverse POA (Principal Object Access) model. Group:
`security`. Flags/choices: `crm security --help`.

```bash
crm --json security list-roles
crm --json security list-roles --business-unit <bu-guid>     # scope to one BU

crm --json security list-user-roles <user-guid>              # roles on a user
crm --json security list-team-roles <team-guid>              # roles on a team

crm --json security user-privileges <user-guid>             # effective privileges on a user

# assign-role requires exactly one of --to-user / --to-team, and --yes non-interactively
crm --json security assign-role <role-guid> --to-user <user-guid> --yes
crm --json security assign-role <role-guid> --to-team <team-guid> --yes

# record sharing (POA)
crm --json security grant <entity-set> <record-id> --to <type>:<guid> --rights <rights> --yes
crm --json security revoke <entity-set> <record-id> --from <type>:<guid> --yes
crm --json security list-access <entity-set> <record-id>
```

## Role-assignment gotchas

**Roles are business-unit-scoped.** A role belongs to exactly one business unit
and can only be assigned to users or teams **within that same business unit.**
Assigning a role whose BU differs from the principal's BU fails with a
`forbidden` (403). Pick a role from the same business unit as the target
user/team (see the `forbidden` row in `reference/troubleshooting.md`).

**`user-privileges` is the only way to get a user's *effective* privileges** —
the resolved privilege set from the user's own roles **plus** team-inherited
ones (collapsed per privilege to its highest depth). The `list-*-roles` verbs
list *roles*, not privileges, and never resolve team inheritance. Each item is a
`RolePrivilege` with PascalCase keys (`PrivilegeName`, `Depth`, `PrivilegeId`,
`BusinessUnitId`, `RecordFilterId`, `RecordFilterUniqueName`); `--json` returns
them as a list.

**Team-inherited privileges report at `Basic` depth only** — an upstream
`RetrieveUserPrivileges` limitation: a privilege the user holds solely via a
team understates its true depth here. Full inherited depth needs the
per-privilege messages, which this CLI does not wrap.

Role assignment is **cumulative and not cleanly reversible** — omitting `--yes` in a
non-interactive context aborts (exit 1). The command also carries the standard
admin-header options (`--as-user`, `--as-user-object-id`, `--suppress-dup-detection`,
`--bypass-plugins`).

## Record sharing (POA)

The `grant` / `revoke` / `list-access` verbs wrap the Dataverse
**Principal Object Access** model:

| verb | Web API action | mutating? |
|---|---|---|
| `grant` | `GrantAccess` | yes — confirmation-gated, `--yes` required non-interactively |
| `revoke` | `RevokeAccess` | yes — confirmation-gated |
| `list-access` | `RetrieveSharedPrincipalsAndAccess` | read-only |

### Principal `<type>:<guid>` form

`--to` (grant) and `--from` (revoke) take the principal as a single
`<type>:<guid>` token (the valid types are in `--help`). The shape is validated
before any backend call — a malformed value exits 2 immediately.

### Access rights

`--rights` (on `grant`) is a comma-separated, case-insensitive list of friendly
names (the set is in `crm security grant --help`). Sharing a record already
shared with the same principal **replaces** its rights — one POA row per
principal per record (D365 server semantics).

### Revoke is all-or-nothing

`revoke` removes **all** of the principal's shared rights in a single
`RevokeAccess` call. There is no per-right revoke.

### `--dry-run` on grant

`--dry-run` echoes the would-be POST body without executing it; `meta.dry_run:
true` appears in the envelope. Reads still run for real under `--dry-run`.

### JSON contract for `list-access`

`--json` `data` is a bare array of objects:

```json
[
  {
    "principalType": "systemuser",
    "principalId": "<guid>",
    "accessMask": "ReadAccess, WriteAccess"
  }
]
```

`principalType` is one of `systemuser`, `team`, `organization`. `accessMask`
is a comma-separated string of active access rights as returned by the Web API.

### Out of scope

`ModifyAccess` (changing rights on an existing share without a full
revoke+re-grant) is not currently implemented.
