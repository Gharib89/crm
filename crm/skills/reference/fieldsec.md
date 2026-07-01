# Field (column) security — profiles & column permissions

Create field security profiles, grant per-column read/create/update permissions,
and assign profiles to users or teams. Group: `fieldsec`. Flags/choices:
`crm fieldsec --help`.

```bash
crm --json fieldsec create-profile "Compensation" --description "Salary access" --solution cwx_crmworx
crm --json fieldsec add-permission "Compensation" account creditlimit --read --update --solution cwx_crmworx
crm --json fieldsec assign "Compensation" --user <user-guid>     # or --team <team-guid>
crm --json fieldsec list
crm --json fieldsec get "Compensation"
```

## Workflow & gotchas

**Secure the column before granting a permission.** `add-permission` POSTs a
`fieldpermission`, and the server **rejects** it for a column that is not
field-secured: `0x8004f508 … is NOT secured …`. Enable field security on the
attribute (`IsSecured = true`) and publish it *first* — otherwise `add-permission`
fails with a `validation` (400) envelope. The metadata write that secures an
attribute is a separate step (the `metadata` group / customizer).

**`<profile>` is a name *or* id.** `add-permission`, `assign`, and `get` resolve a
profile passed by name via an exact-match lookup, or accept the
`fieldsecurityprofileid` GUID directly.

**`add-permission` needs at least one grant.** Pass one or more of
`--read` / `--create` / `--update`; none is a usage error. Each maps to the
fieldpermission `CanRead` / `CanCreate` / `CanUpdate` level — **`4` = Allowed**
when the flag is set, **`0` = Not Allowed** otherwise. These numeric values
surface verbatim in `get` output.

**`assign` takes exactly one principal.** Pass one of `--user` / `--team` (a
GUID); both or neither exits 2. Assignment is the N:N association
(`systemuserprofiles_association` / `teamprofiles_association`) — cumulative, like
a team/role membership.

**`--solution` is required, `--dry-run` also honored.** `create-profile` and
`add-permission` require `--solution <unique_name>` (sets
`MSCRM.SolutionUniqueName`; no profile default, no opt-out — `--solution Default`
for a deliberate Default-Solution-only write) and honor `--dry-run` (echoes the
would-be POST, `meta.dry_run: true`; reads still run for real, `--solution` is
validated before them). `assign` takes no `--solution` (the N:N association isn't
a solution component).

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
