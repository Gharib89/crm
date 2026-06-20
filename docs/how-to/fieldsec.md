# How-to: fieldsec

Manage **column-level (field) security** headlessly: create field security
profiles, grant per-column read/create/update permissions, and assign profiles
to users or teams. See the [CLI reference](../reference/cli.md) for every flag.

Field security in Dynamics 365 is enforced by **field security profiles**. A
profile holds a set of **field permissions** (one per secured column) and is
attached to **users and/or teams**. A user's effective access to a secured
column is the union of the permissions granted by every profile they hold
(directly or through a team).

## The workflow

1. **Secure the column first.** A field permission can only be created for a
   column that is already field-secured (`IsSecured = true`) and published — see
   the caveat below.
2. `fieldsec create-profile` — create a profile.
3. `fieldsec add-permission` — grant read/create/update on a secured column.
4. `fieldsec assign` — attach the profile to a user or a team.

## Create a field security profile

```bash
crm --json fieldsec create-profile "Compensation" --description "Salary access"
```

`NAME` is required; `--description` is optional. Returns the new
`fieldsecurityprofileid`. Supports `--solution <unique_name>` to land the
profile in a specific unmanaged solution, and `--dry-run` to preview the POST.

## Grant a column permission

```bash
crm --json fieldsec add-permission "Compensation" account creditlimit \
    --read --update
```

`PROFILE` is a profile **name or id**; `ENTITY` is the table logical name;
`ATTRIBUTE` is the column logical name. Pass at least one of `--read` /
`--create` / `--update` — each maps to the corresponding field-permission level
(`CanRead` / `CanCreate` / `CanUpdate`), set to **Allowed** when the flag is
present and **Not Allowed** otherwise. Supports `--solution` and `--dry-run`.

> **Caveat — the column must be secured first.** The server rejects a field
> permission for a column that is not field-secured, with
> `0x8004f508 … is NOT secured …`. Enable field security on the attribute
> (`IsSecured = true`) and publish it before calling `add-permission`.

## Assign a profile to a user or team

```bash
crm --json fieldsec assign "Compensation" --user 00000000-0000-0000-0000-000000000002
crm --json fieldsec assign "Compensation" --team 00000000-0000-0000-0000-000000000003
```

`PROFILE` is a name or id. Pass **exactly one** of `--user` / `--team` (a GUID);
passing both or neither is a usage error (exit 2). Assignment is the N:N
association via `systemuserprofiles_association` / `teamprofiles_association`.

## List and inspect profiles

```bash
crm --json fieldsec list
crm --json fieldsec get "Compensation"
```

`list` returns every profile (id, name, description). `get` takes a profile
name or id and returns the profile fields plus a `permissions` list of the
column permissions it grants:

```json
{
  "fieldsecurityprofileid": "...",
  "name": "Compensation",
  "description": "Salary access",
  "permissions": [
    {
      "fieldpermissionid": "...",
      "entityname": "account",
      "attributelogicalname": "creditlimit",
      "canread": 4,
      "cancreate": 0,
      "canupdate": 4
    }
  ]
}
```

The permission-level values come from the `field_security_permission_type`
choice: **`0` = Not Allowed**, **`4` = Allowed**.
