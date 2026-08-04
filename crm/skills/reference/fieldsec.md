# Field (column) security — profiles & column permissions

Create field security profiles, grant per-column read/create/update permissions,
and assign profiles to users or teams. Group: `fieldsec`. Flags/choices:
`crm fieldsec --help`.

```bash
crm --json fieldsec create-profile "Compensation" --description "Salary access" --solution ContosoCore
crm --json fieldsec add-permission "Compensation" account creditlimit --read --update --solution ContosoCore
crm --json fieldsec assign "Compensation" --user-id <user-guid>     # or --team <team-guid>
crm --json fieldsec list
crm --json fieldsec get "Compensation"
```

## Workflow & gotchas

**`add-permission` needs an already-secured column — find one first.** The server
rejects a `fieldpermission` for a column that is not field-secured
(`0x8004f508 … is NOT secured …`, a `validation` 400). The fast path is to target
a column where `IsSecured` is already `true`; enabling security on an additional
column (`IsSecured = true` + publish, via the `metadata` group) is the alternative
when no secured column fits.

**Find secured columns through the metadata path, not the list verb.** The
`metadata attributes <entity>` listing returns a reduced projection that silently
omits `IsSecured` and the `CanBeSecuredFor*` fields — filtering on them yields
empty results, not an error. Query the full metadata instead:

```bash
crm --json query odata "EntityDefinitions(LogicalName='account')/Attributes" \
  --filter "IsSecured eq true" --select LogicalName,CanBeSecuredForRead,CanBeSecuredForUpdate
```

**Check the grant direction before `add-permission`.** `IsSecured = true` does not
imply the column supports every direction — `CanBeSecuredForRead` /
`CanBeSecuredForCreate` / `CanBeSecuredForUpdate` are independent; verify the one
matching the `--read`/`--create`/`--update` grant you intend.

**`<profile>` is a name *or* id.** `add-permission`, `assign`, and `get` resolve a
profile passed by name via an exact-match lookup, or accept the
`fieldsecurityprofileid` GUID directly.

**`add-permission` needs at least one grant.** Pass one or more of
`--read` / `--create` / `--update`; none is a usage error. Each maps to the
fieldpermission `CanRead` / `CanCreate` / `CanUpdate` level — **`4` = Allowed**
when the flag is set, **`0` = Not Allowed** otherwise. These numeric values
surface verbatim in `get` output.

**`assign` takes exactly one principal.** Pass one of `--user-id` / `--team` (a
GUID); both or neither exits 2. Assignment is the N:N association
(`systemuserprofiles_association` / `teamprofiles_association`) — cumulative, like
a team/role membership.

**Solution-scoped:** `create-profile` and `add-permission` require `--solution`
(SKILL.md). `assign` takes no `--solution` — the N:N association isn't a solution
component. `--dry-run` echoes the would-be POST; `--solution` is validated before
the reads.

## JSON contract for `get`

`data` is the profile record plus a `permissions` array — one entry per column
permission the profile grants:

```json
{
  "fieldsecurityprofileid": "<guid>",
  "name": "Compensation",
  "description": "Salary access",
  "permissions": [
    {"fieldpermissionid": "<guid>", "entityname": "account",
     "attributelogicalname": "creditlimit", "canread": 4, "cancreate": 0, "canupdate": 4}
  ]
}
```
