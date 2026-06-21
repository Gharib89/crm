# Security — roles, role assignment, and record sharing (POA)

List security roles and assign them to users or teams; share individual records
with principals via the Dataverse POA (Principal Object Access) model. Group:
`security`. Flags/choices: `crm security --help`.

```bash
crm --json security list-roles
crm --json security list-roles --business-unit <bu-guid>     # scope to one BU
crm --json security list-roles --name-contains Sales         # server-side name filter

crm --json security list-user-roles <user-guid>              # direct roles only
crm --json security list-team-roles <team-guid>              # roles on a team

crm --json security user-privileges <user-guid>             # effective privileges on a user

# role authoring: create-role then set-role-privileges
crm --json security create-role "My Role"                    # defaults to caller's BU
crm --json security create-role "My Role" --if-exists skip   # idempotent
crm --json security set-role-privileges <role> --access read --all-entities --depth organization --replace --yes
crm --json security set-role-privileges <role> --access read,write,create --entities account,contact --depth organization --add --yes
crm --json security set-role-privileges <role> --privilege prvCreateEntity,prvPublishCustomization --depth global --add --yes
crm --dry-run --json security set-role-privileges <role> --access read --all-entities --depth organization --replace --yes

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

## Role authoring: create-role + set-role-privileges

### Workflow

1. `security create-role` — creates the role (no privileges). Returns `{roleid, name, businessunitid}`. The role belongs to the caller's business unit unless `--business-unit` is provided.
2. `security set-role-privileges` — populates or updates the role's privileges.

**Agent discovery pattern — read-only access to everything:**

```bash
crm security create-role "Agent Read-Only"
crm security set-role-privileges <roleid> --access read --all-entities --depth organization --replace --yes
```

**Layer write access onto specific entities without disturbing existing privileges:**

```bash
crm security set-role-privileges <roleid> --access read,write,create --entities account,contact,incident --depth organization --add --yes
```

**Customization privileges (escape hatch for non-entity privileges):**

```bash
crm security set-role-privileges <roleid> --privilege prvCreateEntity,prvWriteEntity,prvCreateAttribute,prvPublishCustomization --depth global --add --yes
```

For "let the agent customize", prefer assigning the OOB `System Customizer` role via `security assign-role` rather than hand-assembling customization privileges — it is simpler and more future-proof.

### Gotchas

**`--replace` is destructive.** It wipes every privilege not in the resolved set. Use `--add` when layering; reserve `--replace` for a full reset (e.g. freshly created roles).

**Privilege counts are org-specific and resolved live.** Never hardcode how many privileges an `--all-entities` call will produce — the count varies by org.

**Entity privilege names embed PascalCase schema names** (e.g. `prvReadAccount`, `prvWriteContact`). Resolution is automatic via metadata — you supply `--access` + `--entities`, the CLI resolves the privilege names for you.

**Depth is clamped per privilege.** When a privilege only supports a subset of depths (e.g. customization privileges are Global-only), the CLI clamps silently and reports each clamp in `meta.warnings[]`. Check warnings when the granted count differs from what you expect.

**Missing access×entity combos are skipped, not fatal.** Some entities are org-owned and have no `assign` or `share` privilege — those combos are silently omitted with a warning, and the rest of the batch still applies.

### JSON contract for set-role-privileges

```json
{
  "ok": true,
  "data": {
    "roleid": "<guid>",
    "mode": "add",
    "depth": "global",
    "privileges": [
      {"name": "prvReadAccount", "privilegeid": "<guid>", "depth": "Global"}
    ],
    "count": 42
  },
  "meta": {
    "warnings": ["Depth clamped to Global for prvPublishCustomization"]
  }
}
```

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
