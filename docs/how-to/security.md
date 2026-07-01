# How-to: security

List security roles and assign them to system users or teams. See the
[CLI reference](../reference/cli.md) for every flag.

Security roles in Dynamics 365 are **business-unit-scoped**: each role belongs
to exactly one business unit, and it can only be assigned to principals
(users or teams) within the same business unit.

## List all security roles

```bash
crm --json security list-roles
```

Returns all security roles in the organization. Each role record includes the
role name, role id, and its owning business unit.

## Filter roles by business unit

```bash
crm --json security list-roles --business-unit 00000000-0000-0000-0000-000000000001
```

`--business-unit GUID` scopes the result to roles belonging to that business
unit only.

## Filter roles by name

```bash
crm --json security list-roles --name-contains Sales
```

`--name-contains TEXT` filters server-side with an OData `contains(name,'…')`
clause. Composes with `--business-unit` (both are AND-joined):

```bash
crm --json security list-roles --name-contains Admin \
    --business-unit 00000000-0000-0000-0000-000000000001
```

## List roles assigned to a user

```bash
crm --json security list-user-roles 00000000-0000-0000-0000-000000000002
```

The positional argument `USER_ID` is the GUID of the system user
(`systemuser`). Returns the roles **directly assigned** to that user — roles
inherited through team membership are not included.

> **Tip — team-inherited roles:** use `crm --json security user-privileges
> <USER_ID>` to see the full effective privilege set, which includes
> privileges from both direct roles and team-inherited roles.

## List roles assigned to a team

```bash
crm --json security list-team-roles 00000000-0000-0000-0000-000000000003
```

The positional argument `TEAM_ID` is the GUID of the team. Returns the roles
currently associated with that team.

## Show a user's effective privileges

```bash
crm --json security user-privileges 00000000-0000-0000-0000-000000000002
```

The positional argument `USER_ID` is the GUID of the system user
(`systemuser`). Unlike `list-user-roles` (which lists the *roles* on the user),
this resolves the user's **effective privilege set** via the
`RetrieveUserPrivileges` Web API function: every privilege granted by the
user's own security roles **plus** those inherited from team membership, with
each privilege collapsed to its highest-applicable depth.

Each privilege carries its access depth — `Basic` (user), `Local` (business
unit), `Deep` (BU + child BUs), or `Global` (organization).

**Caveat — team-inherited privileges are reported at `Basic` depth only.** This
is a limitation of the `RetrieveUserPrivileges` contract, not of this command:
the resolved set understates the true depth of a privilege that the user holds
solely through team membership. Resolving the full inherited depth requires the
per-privilege `RetrieveUserPrivilegeByPrivilegeId`/`-Name` messages, which this
CLI does not implement.

## Create a security role

```bash
crm --json security create-role "Agent Read-Only" --solution cwx_mysolution --yes
```

`--solution` is required — a role created without an explicit target solution
would otherwise land only in the system Default Solution. Pass
`--solution Default` for a deliberate Default-Solution-only write.
`create-role` is also confirmation-gated, so pass `--yes` in non-interactive /
automation contexts (under `--json`, a missing `--yes` aborts on a non-TTY).
Creates a new security role with the given display name. The role is created in
the caller's business unit by default (resolved from `WhoAmI`). Pass
`--business-unit GUID` to target a different business unit.

The role starts with **no privileges**. Grant privileges immediately after with
`security set-role-privileges`.

```bash
# Skip if a same-name role already exists in the same BU (returns the existing role id)
crm --json security create-role "Agent Read-Only" --solution cwx_mysolution --if-exists skip --yes
```

`--if-exists error` (default) raises an error if a role with the same name
already exists in the same business unit. `--if-exists skip` returns the
existing role instead — `{roleid, name, businessunitid, existed: true}` (note
the extra `existed: true` field) — making the call idempotent.

A real run returns `{roleid, name, businessunitid}`. Pass `--dry-run` to
preview the creation without writing — it returns a `{_dry_run, would_create}`
preview instead (no role is created, so there is no `roleid`). `--solution` is
still required under `--dry-run` — it is validated before any backend call.

### Add the role to a solution

`--solution` sets `MSCRM.SolutionUniqueName` to place the new role in an
unmanaged solution as a component.

> **Note — `--if-exists skip` does not add to `--solution`.** When
> `--if-exists skip` returns an existing same-name role, that role is reused
> as-is and is **not** added to `--solution`. Solution membership applies
> only to a newly created role.

## Grant privileges to a security role

Use `security set-role-privileges` to populate a role with privileges after
creating it, or to update the privileges on any existing role.

### Grant read access to all entities at org depth

```bash
crm security set-role-privileges <roleid> \
    --access read --all-entities --depth organization --replace --yes
```

`--all-entities` applies `--access` across every entity in the org (resolved
live from metadata). `--depth organization` is the broadest depth. `--replace`
wipes any privileges already on the role and sets exactly the resolved set —
useful when initialising a freshly created role.

### Layer additional access on specific entities

```bash
crm security set-role-privileges <roleid> \
    --access read,write,create --entities account,contact,incident \
    --depth organization --add --yes
```

`--add` (the default) merges the resolved privileges into the role without
removing any existing ones. Use it to layer access incrementally.

### Grant explicit (non-entity) privileges

```bash
crm security set-role-privileges <roleid> \
    --privilege prvCreateEntity,prvWriteEntity,prvCreateAttribute,prvPublishCustomization \
    --depth global --add --yes
```

The `--privilege` selector is the escape hatch for privileges that are not
entity-based (customization, UI-related, etc.). Privilege names are
PascalCase strings like `prvCreateEntity`. `--depth` is still required and
is automatically clamped to the highest level the privilege supports
(e.g. customization privileges are Global-only; requesting a lower depth
emits a `meta.warnings[]` entry and the privilege is granted at Global).

### Preview without writing

```bash
crm --dry-run --json security set-role-privileges <roleid> \
    --access read --all-entities --depth organization --replace --yes
```

`--dry-run` resolves the full privilege set and returns it in the JSON response
(`data.privileges`) without issuing any writes. Reads (metadata lookups) still
run for real.

### Full worked example — agent read-only role

```bash
# 1. Create the role
crm security create-role "Agent Read-Only" --solution cwx_mysolution --yes
# 2. Grant read on everything at org depth (replace — fresh role, nothing to preserve)
crm security set-role-privileges <roleid> \
    --access read --all-entities --depth organization --replace --yes
```

### JSON contract for set-role-privileges

```json
{
  "ok": true,
  "data": {
    "roleid": "<guid>",
    "mode": "add",
    "depth": "Global",
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

`meta.warnings[]` lists any depth-clamp advisories and skipped
access×entity combos (e.g. `assign` on an org-owned entity that has no assign
privilege). These are non-fatal — the remaining privileges are still granted.

## Assign a security role

```bash
crm --json security assign-role 00000000-0000-0000-0000-000000000004 \
    --to-user 00000000-0000-0000-0000-000000000002 --yes
```

`ROLE_ID` is the GUID of the security role to assign. Exactly one of
`--to-user GUID` or `--to-team GUID` must be provided.

```bash
crm --json security assign-role 00000000-0000-0000-0000-000000000004 \
    --to-team 00000000-0000-0000-0000-000000000003 --yes
```

Role assignment is cumulative (a principal can hold multiple roles) and is not
cleanly reversible through this command, so `assign-role` is gated by an
interactive confirmation prompt. Pass `--yes` to skip the prompt in
non-interactive contexts (agents, CI). Omitting `--yes` in a non-TTY context
aborts safely with `{"ok": false, "error": "aborted by user"}` (exit 1).

## Admin-header options on assign-role

`assign-role` accepts the standard admin-header options:

| Flag | Effect |
|---|---|
| `--as-user GUID` | Impersonate a system user via `MSCRMCallerID` (mutually exclusive with `--as-user-object-id`) |
| `--as-user-object-id GUID` | Impersonate by Entra ID object id via `CallerObjectId` (cloud only; mutually exclusive with `--as-user`) |
| `--suppress-dup-detection` | Send `MSCRM.SuppressDuplicateDetection: true` |
| `--bypass-plugins` | Send `MSCRM.BypassCustomPluginExecution: true` (requires `prvBypassCustomPluginExecution`) |

## 403 errors on assign-role

A 403 (`forbidden`) from `assign-role` means either:

- The caller's application user or security role does not have the privilege
  to assign security roles in the target organization, **or**
- The role's business unit differs from the target user's or team's business
  unit. Roles are BU-scoped — assign a role from the same business unit as the
  principal, or use `list-roles --business-unit <bu-guid>` to find roles in
  the correct business unit.

---

## Record sharing (POA)

Dynamics 365 lets you share individual records with principals outside normal
role-based access. These verbs wrap the Dataverse **Principal Object Access
(POA)** model: `GrantAccess`, `RevokeAccess`, and
`RetrieveSharedPrincipalsAndAccess`.

### Principal `<type>:<guid>` form

All three verbs that name a principal accept the argument as
`<type>:<guid>`, where `<type>` is one of `user`, `team`, or `org`:

```
user:00000000-0000-0000-0000-000000000002
team:00000000-0000-0000-0000-000000000003
org:00000000-0000-0000-0000-000000000004
```

A malformed value (missing colon, empty type, empty GUID) is rejected as a
usage error (exit 2) before any backend call is made.

### Friendly access-right names

`--rights` accepts a comma-separated list of these friendly names
(case-insensitive):

`Read`, `Write`, `Append`, `AppendTo`, `Create`, `Delete`, `Share`, `Assign`

### Share a record with a principal

```bash
crm --json security grant accounts 00000000-0000-0000-0000-000000000001 \
    --to user:00000000-0000-0000-0000-000000000002 \
    --rights Read,Write --yes
```

`ENTITY_SET` is the OData entity-set name (`accounts`, `contacts`, …);
`RECORD_ID` is the record GUID. `--rights` is required. Like `assign-role`,
`grant` is confirmation-gated — pass `--yes` in non-interactive contexts.

Use `--dry-run` to preview the POST without executing it:

```bash
crm --dry-run --json security grant accounts 00000000-0000-0000-0000-000000000001 \
    --to user:00000000-0000-0000-0000-000000000002 \
    --rights Read,Write --yes
```

Sharing a record that was already shared with the same principal at different
rights **replaces** those rights (D365 POA semantics — one POA row per
principal per record).

### Revoke a principal's shared access

```bash
crm --json security revoke accounts 00000000-0000-0000-0000-000000000001 \
    --from user:00000000-0000-0000-0000-000000000002 --yes
```

**Revoke is all-or-nothing per principal.** There is no `--rights` flag —
`RevokeAccess` removes all of the principal's shared rights on that record in
one call. Confirmation-gated; pass `--yes` non-interactively.

### List who a record is shared with

```bash
crm --json security list-access accounts 00000000-0000-0000-0000-000000000001
```

Read-only. Calls `RetrieveSharedPrincipalsAndAccess`. Returns the principals
the record is currently shared with and their access masks. Human output is a
table with columns `principalType` / `principalId` / `accessMask`; JSON `data`
is a list of objects:

```json
[
  {
    "principalType": "systemuser",
    "principalId": "00000000-0000-0000-0000-000000000002",
    "accessMask": "ReadAccess, WriteAccess"
  }
]
```

`principalType` values are `systemuser`, `team`, or `organization`.
`accessMask` is a comma-separated string of the active access rights (as
returned by the Web API).

> **Note:** `ModifyAccess` (changing rights on an existing share without a full
> revoke+re-grant) is not currently implemented.

