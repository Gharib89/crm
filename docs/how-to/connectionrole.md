# How-to: connectionrole

Manage **connection roles** headlessly: create a role, restrict it to one or more
entity types, and pair two roles as reciprocal matching partners. See the
[CLI reference](../reference/cli.md) for every flag.

Connection roles (`connectionrole`) describe how two records are related via a
connection. A role can be **unrestricted** (usable with any entity) or **scoped**
to specific entity types (one `connectionroleobjecttypecode` per entity, created
with `scope`). Two roles are paired as reciprocal partners by linking them through
the self-referential `connectionroleassociation_association` N:N relationship —
that is what `match` does.

## The workflow

1. `connectionrole create` — create one or both roles.
2. `connectionrole scope` — (optional) restrict a role to one entity type; call
   repeatedly to add more entity types.
3. `connectionrole match` — pair the two roles as reciprocal/matching partners.

## Create a role

```bash
crm --json connectionrole create --name "Stakeholder" --category stakeholder
```

`--name` is required. `--category` maps a friendly name to the
`connectionrole_category` global choice set. `--description` adds a plain-text
description. `--solution` sets `MSCRM.SolutionUniqueName` to land the role in an
unmanaged solution. `--dry-run` previews the POST without issuing it.

Returns:

```json
{ "ok": true, "data": { "created": true, "connectionroleid": "<guid>", "name": "Stakeholder" } }
```

## Scope a role to an entity type

```bash
crm --json connectionrole scope "Stakeholder" --entity account
```

`ROLE` is a role **name or id**. `--entity` is the logical name of the entity
type to restrict the role to. Each call creates one
`connectionroleobjecttypecode` record. Call `scope` multiple times to allow the
role on several entity types. `--solution` is supported; `--dry-run` previews
the POST.

Returns:

```json
{ "ok": true, "data": { "created": true, "connectionroleobjecttypecodeid": "<guid>" } }
```

## Match two roles as reciprocal partners

```bash
crm --json connectionrole match "Stakeholder" "Vendor"
```

`ROLE_A` and `ROLE_B` are names or ids. `match` creates the link through the
`connectionroleassociation_association` N:N relationship, making the two roles
reciprocal — when one role is used on a connection, the other appears as its
counterpart.

> **No `--solution` for `match`.** The `connectionroleassociation_association`
> intersect table is not a solution component, so there is no
> `MSCRM.SolutionUniqueName` header to set. This is the same precedent as
> `fieldsec assign`, which associates via a non-solution-component N:N.

`--dry-run` previews the associate call without issuing it.

Returns:

```json
{ "ok": true, "data": { "matched": true, "role_a": "<guid>", "role_b": "<guid>" } }
```

## `--dry-run` and `--json` conventions

All write verbs (`create`, `scope`, `match`) honor `--dry-run` (previews the
would-be request, `meta.dry_run: true`; reads still run for real) and `--json`
(stable `{ ok, data, meta }` envelope). Pass `--json` from agent contexts.
