# Records — clone, `@odata.bind` payloads, upsert, ad-hoc actions

Clone a record, build create payloads that bind lookups, upsert by alternate
key, and call an OData function or action. Groups: `entity`, `action`.
Flags/choices: `crm <group> --help`. Query and plain CRUD: `reference/records.md`.

## Clone a record (`entity clone`)

```bash
crm --json entity clone accounts <guid>
```

Copies one record into a new one. The copied columns are the source's create-valid
attributes minus the **never-copy set**: every `Uniqueidentifier`-typed column (primary
id + `address1_addressid`-class child ids, dropped by type so no per-entity lists),
`statecode`/`statuscode`, `ownerid`, `overriddencreatedon`. The clone lands in the
server-default state, owned by the caller. Set lookups are rebound to the **same parent**
— resolution is annotation-driven (the source is retrieved with annotations; the
per-value `associatednavigationproperty` gives the exact case-sensitive nav and
`lookuplogicalname` gives the target set), so single-target and polymorphic lookups both
bind without guessing nav casing.

**Contract.** All lookup/`--unset` resolution is a **clone pre-flight** that runs before
the one create write: failures (an unresolvable lookup, an `--unset` of a non-attribute)
are batched into a single `{ok:false}` and the org is untouched — a lookup is never
silently dropped. `--dry-run` runs the same pre-flight and returns the resolved body
instead of writing:

```json
{"ok": true, "data": {"_dry_run": true, "would_create": {"entity_set": "accounts", "body": {...}}}, "meta": {"dry_run": true}}
```

Success matches `entity create` (`data` = created record with `_entity_id`/`_entity_id_url`, or `{_entity_id, _entity_id_url}` with `--no-return` — same normalized keys).

**Gotcha — status on clone.** The clone always lands in the server-default state; to set
a status, `entity update <set> <newid> --data '{"statuscode":N}'` *after* the clone.
On **on-prem**, a status in the create body is ignored, so passing it via `--override` is
a no-op there. Re-add any never-copy field (or override a cloned value) with
`--override`; its key passes raw, so an `@odata.bind` key works.

**`--with-children`.** Also clones the direct child rows of every *custom* 1:N
relationship where the record is the parent (one level, no recursion; custom = the
`IsCustomRelationship` metadata flag, so a custom lookup on a system entity counts).
Child rows follow the same never-copy/lookup rules, plus: **every** child lookup whose
value equals the source parent is repointed to the *new* parent (not just the
relationship's own attribute); other lookups copy as-is. `--override`/`--unset` hit the
parent only. Scope the sweep down with repeatable `--skip-child-entity`.

**Contract + gotcha — no rollback (ADR 0007).** Parent-create failure → clean
`{ok:false}`, nothing else done. A *child*-create failure neither aborts nor deletes
what's already made — it is recorded and the rest continue. The envelope then carries the
created ids in `meta.created` and the problems in `data.failures`, and `ok` flips to false:

```json
{"ok": false,
 "data": {"failures": [{"entity": "contoso_line", "source_id": "<src>", "reason": "..."}]},
 "meta": {"created": {"parent": "<new-id>", "children": {"contoso_invoice": ["<id>"]}}}}
```

Recover by cloning the named failed rows individually — **never re-run the whole verb**
(the parent already exists). `--dry-run` is read-only: the parent keeps `would_create`
and a `children` list reports per-entity counts (`{"entity","would_create":N}`), skipped
entities marked `{"entity","skipped":true}`.

## Record-create payloads (`@odata.bind`)

When constructing `entity create`/`update` payloads, lookup fields require an
`@odata.bind` suffix on the **single-valued navigation-property name**, **not** the
logical attribute. That name is metadata-defined and **case-sensitive** — it is NOT a
predictable transform of the attribute: custom lookups often surface a PascalCase
relationship name (e.g. `contoso_CustomerId@odata.bind`) while system lookups commonly
match the lowercase attribute (e.g. `primarycontactid@odata.bind`). Never guess it —
take it from `crm metadata describe <entity>` (`bind_key` per lookup) or
`crm metadata relationships <entity>` (`ReferencingEntityNavigationPropertyName`). A picklist
bound to a global option set binds through `GlobalOptionSet@odata.bind`, and **on-prem
9.1 requires the option set's `MetadataId` GUID there** (the `Name` alternate key is
rejected). `crm metadata describe <entity>` hands you the exact `bind_key` per lookup
and the `global_optionset_id` per global-bound picklist — see `reference/metadata.md`.

Add `--validate` to `entity create`/`entity update` to field-name-check the payload
before the write. It runs 1-3 read-only metadata GETs and blocks unknown fields with
`{ok:false, meta:{unknown_fields, did_you_mean}}`; valid `<nav>@odata.bind` keys are
not flagged. **`--validate` alone still writes** — it only adds the pre-flight
check; pair it with `--dry-run` to preview without writing. Scope is
field-**name** only — option-set values are not validated (look them up first; see
`reference/metadata.md`).

On **`entity create`** only, `--validate` additionally warns when the payload contains
the entity's primary id (e.g. `accountid`). The write still proceeds — explicit-GUID
creates are intentional — but the warning flags the copy-a-record footgun where
`metadata describe` carries the primary id into the payload:

```json
{"ok": true, "data": {...}, "meta": {"warnings": ["payload contains primary id 'accountid' — remove it unless you intend to create with an explicit GUID"]}}
```

Validate-first is the recommended default for unattended writes (SKILL.md); the
raw server noise for an unknown field reads like `Does not support untyped value
in non-open type` — the clean `unknown_fields`/`did_you_mean` envelope is what
`--validate` buys.

### Notes (annotations) — a polymorphic-lookup worked example

A note attaches to its record through the polymorphic `objectid` lookup; the nav
property is `objectid_<target-logical-name>` (singular logical name — the entity
is `annotation`, the set is `annotations`):

```bash
crm --json entity create annotations --data \
  '{"subject":"Call notes","notetext":"…","objectid_account@odata.bind":"accounts(<guid>)"}'
```

**Polymorphic `bind_key`:** a polymorphic lookup (`objectid`, `customerid`,
`ownerid`, …) has no single top-level `bind_key` — `metadata describe` reports one
per target under `targets[].bind_key`. Pick the entry whose `logical` matches the
record you're binding to (e.g. `objectid_contact@odata.bind` for a note on a
contact).

### Round-tripping a READ-shape lookup (no hand-`@odata.bind`)

You don't have to hand-write the `<nav>@odata.bind` above when the lookup value
already came from a read. `data import`, `entity create`, `entity update`, and
`entity upsert` auto-rewrite any lookup that arrives in the server's **READ shape**
`_<attr>_value` (the raw-GUID form that `entity get` / `data export` / `query
odata` emit) into the WRITE shape `<nav>@odata.bind: "/<set>(<guid>)"`,
resolving the nav property and target set from relationship metadata. So an
exported or retrieved row writes back unedited.
Details:

- A read-only lookup value (e.g. `_createdby_value`) is **dropped** — it can't be
  written. Read-only OData annotation keys (`@odata.etag`, `@odata.context`,
  `@OData.Community.Display.V1.FormattedValue`, per-value annotations) are
  stripped; a hand-written `<nav>@odata.bind` you supply is kept (null: below).
- crm's synthetic `_entity_id` and `_entity_id_url` keys are **dropped** on
  create/update/upsert, so an `entity get --json` payload can be edited and
  passed to `entity update` directly.
- A `null` `_<attr>_value` or `<nav>@odata.bind` **clears** the lookup: online
  by the null bind; on-premises (400 on it) by `DELETE <record>/<nav>/$ref` in
  the write's changeset (`create` drops it, `--dry-run` shows `clear_lookups`).

**Gotcha — polymorphic lookups (`customerid`, `ownerid`, …) need annotations.**
A Customer/Owner lookup binds to the concrete target named by its
`…@Microsoft.Dynamics.CRM.lookuplogicalname` annotation. When that annotation is
**absent** the lookup is **dropped** (not bound, not an error) so the rest of the
record still round-trips, matching `entity clone`'s never-copy-`ownerid` behavior.
`data import` reports the drop in `data.lookups_dropped`; `entity create`/`update`/
`upsert` drop it without a word. A plain `data export` carries **no** annotations
and `ownerid` is on every record, so **export `--format jsonl`** when polymorphic
lookups must be rebound.

This is lookup/envelope-only: non-lookup read-only / unique scalar fields are
**not** stripped, so a whole-record export may still be rejected on those (a
separate concern). No export format strips them.

## Upsert by alternate key (`entity upsert --key`)

When no primary GUID is known, match by a natural/alternate key. Omit the
positional `RECORD_ID` — key values are read from `--data`. Passing both a
GUID and `--key` is a usage error (exit 2).

**Body-stripping.** The key attribute(s) are automatically removed from the
request body before the PATCH. Dataverse identifies the record from the URL key
segment (`accounts(accountnumber='ACC-001')`) and ignores (or on create, copies
from the URL) those fields — sending a differing body value is rejected by the
server. You do not need to remove them yourself.

**Pre-flight validation.** The CLI calls `metadata keys` before the PATCH and
verifies the named attribute(s) match a **defined** alternate key on the entity.
An unknown or unregistered combination returns a clean error listing defined
keys — the PATCH is never issued. A not-yet-`Active` key index makes the server
404 — poll `metadata keys` until the index is `Active` (the index-status note in
`reference/metadata.md`).

**Composite keys.** Multiple attributes (comma-separated) must exactly match the
attribute set of one defined key — a subset or superset is rejected. The CLI
reorders the attributes to the **metadata's canonical order** regardless of the
order you listed them, so the URL path is stable across calls.

**Success envelope.** A 204 with the server's `OData-EntityId` header yields
`data._entity_id_url` — for an alternate-key upsert that URL is the key path
itself (`accounts(accountnumber='ACC-001')`). It has no bare `(<guid>)` segment,
so (unlike a primary-key write) there is **no** `_entity_id`:

```json
{"ok": true, "data": {"_entity_id_url": ".../accounts(accountnumber='ACC-001')"}}
```

Only when the server omits the header does it fall back to
`{"upserted": true, "key": "accountnumber='ACC-001'"}`.

**Create-only upsert (`--if-none-match`).** `entity upsert --if-none-match`
(GUID or `--key`) sends `If-None-Match: *`, so the write lands only if the record
does not yet exist; an existing record makes the server return **412** (the error
envelope carries `meta.status: 412`). It is the create-only complement to `entity
update --if-match` (update-only). The flag is on `entity upsert` only.

## Ad-hoc OData function / action

```bash
crm --json action function RetrieveCurrentOrganization \
    --params '{"AccessType":"Default"}'
```

`action function` issues a **GET** (read-only); `action invoke` issues a **POST**
(state change, journalled). Both run unbound by default; `action function` can
bind to a collection or a single record, while **`action invoke` binds only to a
single record** — a collection-only bind is inexpressible, so collection-bound
actions (`CreateMultiple`, `UpdateMultiple`, `DeleteMultiple`) are unreachable
through it; hand-author those via `crm batch` (see `reference/bulk.md`). Pick
`function` vs `invoke` by the operation's OData kind, not by whether it binds.
Run `crm describe action` for the exact bind flags. **Discover** what's
callable with `crm --json metadata list-actions` / `crm --json metadata list-functions`.

To pass a **record-reference** param to a function (e.g. `Target` on
`RetrievePrincipalAccess`/`CalculateRollupField`), give the value as a JSON object
`{"@odata.id": "<entityset>(<guid>)"}` — `action function` sends it as an OData
parameter alias carrying the `@odata.id`. Values with URL-reserved characters or
whitespace are aliased automatically; plain scalars stay inline.
