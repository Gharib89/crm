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

## List roles assigned to a user

```bash
crm --json security list-user-roles 00000000-0000-0000-0000-000000000002
```

The positional argument `USER_ID` is the GUID of the system user
(`systemuser`). Returns the roles currently associated with that user.

## List roles assigned to a team

```bash
crm --json security list-team-roles 00000000-0000-0000-0000-000000000003
```

The positional argument `TEAM_ID` is the GUID of the team. Returns the roles
currently associated with that team.

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

