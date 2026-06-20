# Connection roles

Create connection roles, restrict them to entity types, and pair them as
reciprocal partners. Group: `connectionrole`. Flags/choices:
`crm connectionrole --help`.

```bash
crm --json connectionrole create --name "Stakeholder" --category stakeholder
crm --json connectionrole scope "Stakeholder" --entity account
crm --json connectionrole match "Stakeholder" "Vendor"
```

## Workflow & gotchas

**Order: create → scope (optional) → match.** A freshly created role is
unrestricted (usable with any entity type). Call `scope` once per entity type to
restrict it; call it repeatedly for multiple entity types. `match` can be called
as soon as both roles exist — scope is not required first.

**`<role>` is a name *or* id** on `scope` and `match` — a name is resolved by
exact match; or pass the `connectionroleid` GUID directly.

**No `--solution` on `match`.** The `connectionroleassociation_association`
intersect table is not a solution component, so there is no solution-scoping
header. This is the same precedent as `fieldsec assign`. Use `--solution` on
`create` and `scope` to land those components in an unmanaged solution.

**`--dry-run` / `--solution` on writes.** `create` and `scope` honor both.
`match` honors `--dry-run` (the previewed write returns `data._dry_run: true`
with a `would_*` flag; name lookups still run live) but not `--solution`.

## JSON contract

**`create`** → `data` carries `created`, `connectionroleid`, and `name`:

```json
{"created": true, "connectionroleid": "<guid>", "name": "Stakeholder"}
```

**`scope`** → `data` carries `created` and `connectionroleobjecttypecodeid`:

```json
{"created": true, "connectionroleobjecttypecodeid": "<guid>"}
```

**`match`** → `data` carries `matched`, `role_a`, and `role_b`:

```json
{"matched": true, "role_a": "<guid>", "role_b": "<guid>"}
```
