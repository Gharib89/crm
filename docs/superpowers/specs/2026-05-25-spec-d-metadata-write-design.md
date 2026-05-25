# Spec D — Metadata write API

**Date:** 2026-05-25
**Status:** Approved (pending user review of written spec)
**Target version:** 0.5.0
**Tracking issue:** [#6](https://github.com/Gharib89/crm/issues/6)
**Predecessor:** [Spec C — Throughput + admin surface](./2026-05-24-spec-c-throughput-admin-design.md) (shipped as 0.4.0)

---

## 1. Goals + non-goals

### Goals

- `metadata add-attribute <entity> --kind <kind>` covering 14 attribute casts: `string`, `memo`, `integer`, `bigint`, `decimal`, `double`, `money`, `boolean`, `datetime`, `picklist`, `multiselect`, `lookup`, `image`, `file`. Per-kind validation table (§3) rejects forbidden / missing flags before any HTTP call.
- `metadata create-one-to-many` + `metadata create-many-to-many` via the unbound `CreateOneToManyRequest` / `CreateManyToManyRequest` actions. Full cascade-config flags on 1:N; intersect-entity + per-side menu config on N:N.
- Global option set CRUD: `metadata list-optionsets`, `get-optionset`, `create-optionset`, `update-optionset`, `delete-optionset`. `update-optionset` is **granular**: `--insert-option` / `--update-option` / `--delete-option` / `--reorder` map to `InsertOptionValue` / `UpdateOptionValue` / `DeleteOptionValue` / `OrderOption` bound actions.
- `metadata delete-entity <logical-name>`: `click.confirm` interactive prompt + `--yes` to skip, **plus** client-side `IsCustomEntity=true` + `IsManaged=false` pre-flight guard. Server enforces dependency checks.
- All new write commands accept `--solution <uniquename>` (header `MSCRM.SolutionUniqueName`) and `--publish/--no-publish` (default **ON**), matching `metadata create-entity`. Delete commands skip publish.
- Read-back: every write verb whose server response carries `OData-EntityId` re-GETs the canonical record and surfaces server-truth fields in the result envelope. Read-back failure is **non-fatal** — populates a `*_lookup_error` field, never blocks the success path. Matches the §3.3 precedent from Spec A.
- Bump to **0.5.0**. All additions are additive flags / helpers / new commands.

### Non-goals

- **State / Status attributes.** System-managed; not write-customizable via the standard `Attributes` endpoint.
- **EntityKey (alternate-key) CRUD.** Different endpoint (`Keys`). Defer to a future spec.
- **Form / view / chart metadata.** Out of scope.
- **Calculated / Rollup attributes.** Require `CreateCalculatedFieldRequest` / `CreateRollupFieldRequest` plus FormulaDefinition parsing. Spec E candidate.
- **Rename entities / attributes.** PATCH on `EntityMetadata.DisplayName` requires whole-object PUT semantics in Dataverse. Tracked separately.
- **Auto-migration of dependent rows on optionset value delete.** Caller responsibility; orphans surface as row-level integrity issues, not metadata.
- **`$batch` rollback wrapping of multi-stage `update-optionset`.** Doable in follow-up using Spec C primitives (`D365Backend.batch()`); not in this slice.
- **Transparent dependency pre-listing on `delete-entity`** (`RetrieveDependenciesForDeleteRequest`). Server-side dependency check on DELETE is sufficient. Add later as `delete-entity --check-dependencies` if needed.

### Breaking changes

None. Pure additive surface — new commands, new helper modules, no existing CLI command or return shape changes.

---

## 2. Architecture

### 2.1 Module layout

```
crm/core/
  metadata.py          — existing read helpers + create_entity + NEW delete_entity
  metadata_attrs.py    — NEW: add_attribute(backend, ...) + 14 typed builders
                         (_string_attr, _memo_attr, _int_attr, _bigint_attr,
                          _decimal_attr, _double_attr, _money_attr, _bool_attr,
                          _datetime_attr, _picklist_attr, _multiselect_attr,
                          _lookup_attr, _image_attr, _file_attr)
  relationships.py     — NEW: create_one_to_many, create_many_to_many; existing
                         list_relationships MOVED here from metadata.py
  optionsets.py        — NEW: list_optionsets, get_optionset, create_optionset,
                         update_optionset (dispatches to insert/update/delete/order),
                         delete_optionset
crm/utils/
  d365_types.py        — NEW TypedDicts: AttributeKind (Literal), AddAttributeResult,
                         CreateRelationshipResult, OptionSetRow, OptionSetCreateResult
crm/
  cli.py               — NEW commands under existing `metadata` group:
                         add-attribute, create-one-to-many, create-many-to-many,
                         list-optionsets, get-optionset, create-optionset,
                         update-optionset, delete-optionset, delete-entity
setup.py               — bump to 0.5.0
CHANGELOG.md           — 0.5.0 section
```

All new files in `crm/core/*` and `crm/utils/d365_types.py` are in the **pyright strict** zone (Spec A §2 rule). `cli.py` stays basic.

### 2.2 Backend reuse — no new kwargs

All new write helpers reuse the existing `D365Backend.post/patch/delete` signatures from Spec C. `solution=...` is threaded through `extra_headers={"MSCRM.SolutionUniqueName": solution}`. Admin headers (`caller_id`, `suppress_duplicate_detection`, `bypass_custom_plugin_execution`) and `etag=` are automatically available on all new verbs because the backend handles them generically.

### 2.3 Dry-run

Backend already returns a `_dry_run` envelope when `CRM_DRY_RUN=1`. All new write helpers honor it the same way `create_entity` does: return the envelope, skip read-back, skip publish.

### 2.4 Read-back pattern

| Verb | OData-EntityId target | Read-back `$select` | Surfaced field |
|---|---|---|---|
| `add_attribute` | `EntityDefinitions(...)/Attributes(<id>)` | `LogicalName,SchemaName,AttributeType` | `attribute_logical_name`, `attribute_type` |
| `create_one_to_many` | `RelationshipDefinitions(<id>)` | `SchemaName,ReferencingEntity,ReferencingAttribute` | `schema_name`, `referencing_attribute` |
| `create_many_to_many` | `RelationshipDefinitions(<id>)` | `SchemaName,IntersectEntityName` | `schema_name`, `intersect_entity` |
| `create_optionset` | `GlobalOptionSetDefinitions(<id>)` | `Name,IsCustomOptionSet` | `name` |
| `delete_entity` | — | — | `deleted: True` |

Read-back failure → populates `*_lookup_error` field on the result. Matches `create_entity` precedent exactly.

### 2.5 Publish dispatch

A single private helper `_maybe_publish(backend, info, publish) -> dict` lives in `crm/core/metadata.py` and is imported by every write verb. Same `publish_all(backend)` call site as `create-entity`. Skipped when any of: `_dry_run`, `publish=False`, verb is destructive (`delete-entity`, `delete-optionset`).

### 2.6 Lookup attribute special case

`LookupAttributeMetadata` cannot be POSTed to `/Attributes` directly. Dataverse requires `CreateOneToManyRequest`, which atomically creates the lookup attribute *and* the 1:N relationship.

`add_attribute(kind="lookup", target_entity=..., schema_name=..., lookup_display=..., ...)` internally calls `create_one_to_many` with auto-generated relationship name (`<referencing_entity>_<schema_name>`). The CLI help on `metadata add-attribute --kind lookup` explicitly notes: "creates 1:N relationship as a side effect; use `create-one-to-many` directly for cascade control".

---

## 3. `add-attribute` — type catalog + CLI shape

### 3.1 Core API

```python
def add_attribute(
    backend: D365Backend,
    *,
    entity: str,                     # target entity logical name
    kind: AttributeKind,             # Literal of the 14 type strings
    schema_name: str,                # PascalCase with publisher prefix, e.g. "new_Amount"
    display_name: str,
    description: str | None = None,
    required: str = "None",          # "None"|"Recommended"|"ApplicationRequired"
    # type-specific (validated per kind, see §3.2):
    max_length: int | None = None,           # string, memo
    format_name: str | None = None,          # string: Text|Email|Url|Phone|TextArea
                                             # datetime: DateOnly|DateAndTime
    min_value: float | None = None,          # integer/bigint/decimal/double/money
    max_value: float | None = None,
    precision: int | None = None,            # decimal/double/money
    default_value: bool | int | None = None, # boolean default, picklist default
    true_label: str = "Yes",                 # boolean
    false_label: str = "No",
    optionset_name: str | None = None,       # picklist/multiselect — global ref
    options: list[tuple[int, str]] | None = None,  # picklist/multiselect — inline local
    target_entity: str | None = None,        # lookup — referenced entity logical name
    relationship_schema: str | None = None,  # lookup — override auto-generated rel name
    max_size_kb: int | None = None,          # image/file
    solution: str | None = None,
) -> AddAttributeResult: ...
```

### 3.2 Per-kind validation matrix

`D365Error` raised **before** HTTP if a forbidden flag is set or a required flag is missing.

| Kind | Required flags | Forbidden flags | Notes |
|---|---|---|---|
| string | `max_length` | `precision`, `target_entity`, `optionset_name`, `options` | `format_name` defaults to `"Text"` |
| memo | `max_length` | numeric flags, lookup flags | |
| integer | — | `precision`, `max_length` | `min_value` / `max_value` clamped to int32 |
| bigint | — | `precision`, `max_length` | |
| decimal | `precision` | `max_length` | `precision` ∈ [0, 10] |
| double | `precision` | `max_length` | `precision` ∈ [0, 5] |
| money | `precision` | `max_length` | `precision` ∈ [0, 4] |
| boolean | — | numeric, lookup, picklist | `true_label` / `false_label` build OptionSet |
| datetime | — | numeric, lookup, picklist | `format_name` ∈ `{DateOnly, DateAndTime}` |
| picklist | one-of `optionset_name` / `options` (mutually exclusive) | — | `default_value` is int option value |
| multiselect | one-of `optionset_name` / `options` (mutually exclusive) | — | Server v9.0+ |
| lookup | `target_entity` | numeric, picklist | Dispatches to `create_one_to_many` |
| image | — | numeric, lookup, picklist | Only one per entity (server enforces) |
| file | — | numeric, lookup, picklist | `max_size_kb` default 32768 |

### 3.3 CLI surface

```
crm metadata add-attribute <entity> \
    --kind <kind> \
    --schema-name <name> \
    --display <label> \
    [type-specific flags] \
    [--required None|Recommended|ApplicationRequired] \
    [--solution <name>] [--publish/--no-publish]
```

`--kind` is `click.Choice` over the 14 literals. Unknown / forbidden flags per kind raise `click.UsageError` via the validation table. The command docstring lists the per-kind flag matrix and one example per kind.

### 3.4 POST shape (string example)

```http
POST /api/data/v9.x/EntityDefinitions(LogicalName='new_project')/Attributes
{
  "@odata.type": "Microsoft.Dynamics.CRM.StringAttributeMetadata",
  "SchemaName": "new_Amount",
  "LogicalName": "new_amount",
  "DisplayName": _label("Amount"),
  "RequiredLevel": {"Value": "None"},
  "MaxLength": 100,
  "FormatName": {"Value": "Text"}
}
```

Each kind has a small `_<kind>_attr(opts) -> dict[str, Any]` builder in `metadata_attrs.py`. `add_attribute` dispatches via a `dict[AttributeKind, Callable[..., dict[str, Any]]]`. Lookup kind short-circuits and calls `create_one_to_many` instead of POSTing to `/Attributes`.

---

## 4. Relationship commands

### 4.1 `create_one_to_many`

```python
def create_one_to_many(
    backend: D365Backend,
    *,
    schema_name: str,                # e.g. "new_account_new_project"
    referenced_entity: str,          # "1" side, e.g. "account"
    referencing_entity: str,         # "N" side, e.g. "new_project"
    lookup_schema: str,              # lookup attribute on referencing entity
    lookup_display: str,
    lookup_required: str = "None",   # None|Recommended|ApplicationRequired
    lookup_description: str | None = None,
    cascade_assign: str = "NoCascade",
    cascade_delete: str = "RemoveLink",
    cascade_reparent: str = "NoCascade",
    cascade_share: str = "NoCascade",
    cascade_unshare: str = "NoCascade",
    cascade_merge: str = "NoCascade",
    menu_label: str | None = None,
    menu_behavior: str = "UseLabel",       # UseLabel|UseCollectionName|DoNotDisplay
    menu_order: int = 10000,
    solution: str | None = None,
) -> CreateRelationshipResult: ...
```

Calls `POST /api/data/v9.x/CreateOneToManyRequest`. Body nests:

```json
{
  "OneToManyRelationship": {
    "@odata.type": "Microsoft.Dynamics.CRM.OneToManyRelationshipMetadata",
    "SchemaName": "...",
    "ReferencedEntity": "...",
    "ReferencingEntity": "...",
    "AssociatedMenuConfiguration": { "Behavior": "...", "Label": _label(...), "Order": 10000 },
    "CascadeConfiguration": {
      "Assign": "NoCascade", "Delete": "RemoveLink", "Reparent": "NoCascade",
      "Share": "NoCascade", "Unshare": "NoCascade", "Merge": "NoCascade"
    }
  },
  "Lookup": {
    "@odata.type": "Microsoft.Dynamics.CRM.LookupAttributeMetadata",
    "SchemaName": "...", "DisplayName": _label(...), "RequiredLevel": {"Value": "None"}
  },
  "SolutionUniqueName": "<solution-or-omitted>"
}
```

Returns `{AttributeId, RelationshipId}`. Read-back: `GET RelationshipDefinitions(<RelationshipId>)?$select=SchemaName,ReferencingAttribute`.

### 4.2 `create_many_to_many`

```python
def create_many_to_many(
    backend: D365Backend,
    *,
    schema_name: str,
    entity1_logical: str,
    entity2_logical: str,
    intersect_entity: str,           # logical name for the intersect table
    entity1_menu_label: str | None = None,
    entity1_menu_behavior: str = "UseCollectionName",
    entity1_menu_order: int = 10000,
    entity2_menu_label: str | None = None,
    entity2_menu_behavior: str = "UseCollectionName",
    entity2_menu_order: int = 10000,
    solution: str | None = None,
) -> CreateRelationshipResult: ...
```

Calls `POST /api/data/v9.x/CreateManyToManyRequest`. Body nests `ManyToManyRelationship` + top-level `IntersectEntitySchemaName`. Returns `{ManyToManyRelationshipId}`. Read-back: `GET RelationshipDefinitions(<id>)?$select=SchemaName,IntersectEntityName`.

### 4.3 CLI surface

```
crm metadata create-one-to-many \
    --schema-name new_account_new_project \
    --referenced-entity account \
    --referencing-entity new_project \
    --lookup-schema new_AccountId \
    --lookup-display "Account" \
    [--lookup-required None|Recommended|ApplicationRequired] \
    [--cascade-assign ...] [--cascade-delete ...] [--cascade-reparent ...] \
    [--cascade-share ...] [--cascade-unshare ...] [--cascade-merge ...] \
    [--menu-label "..."] [--menu-behavior UseLabel|UseCollectionName|DoNotDisplay] \
    [--menu-order N] \
    [--solution <name>] [--publish/--no-publish]

crm metadata create-many-to-many \
    --schema-name new_account_project \
    --entity1 account --entity2 new_project \
    --intersect-entity new_account_project_intersect \
    [--entity1-menu-label "..."] [--entity1-menu-behavior ...] [--entity1-menu-order N] \
    [--entity2-menu-label "..."] [--entity2-menu-behavior ...] [--entity2-menu-order N] \
    [--solution <name>] [--publish/--no-publish]
```

### 4.4 Cascade enum

`NoCascade`, `Cascade`, `Active`, `UserOwned`, `RemoveLink`, `Restrict`. Pre-validated via `click.Choice`. Defaults match MS Learn recommended "Parental-light": `NoCascade` everywhere except `Delete=RemoveLink`.

### 4.5 Validation
- `schema_name` must include `_` (publisher prefix). Same check as `create_entity`.
- `lookup_schema` must include `_`; if its prefix differs from `schema_name`'s prefix, emit a non-fatal warning to stderr (`click.echo(..., err=True)`) and proceed — server enforces; the client warning is friendlier than a server 400.
- N:N: `entity1_logical != entity2_logical` (server rejects self-N:N anyway; surface clearly).
- `intersect_entity`: no client-side pre-check; server rejects on collision with a clear error.

---

## 5. Global option set CRUD

### 5.1 Helpers in `crm/core/optionsets.py`

```python
def list_optionsets(
    backend: D365Backend,
    *,
    custom_only: bool = False,
    top: int | None = None,
) -> list[OptionSetRow]:
    """GET /GlobalOptionSetDefinitions?$select=Name,DisplayName,IsCustomOptionSet,IsGlobal
    Client-side $top slice (server may not honor — same pattern as list_entities)."""

def get_optionset(backend: D365Backend, name: str) -> dict[str, Any]:
    """GET /GlobalOptionSetDefinitions(Name='<name>')?$expand=Options
    Casts via @odata.type=Microsoft.Dynamics.CRM.OptionSetMetadata."""

def create_optionset(
    backend: D365Backend,
    *,
    name: str,                       # fully prefixed, e.g. "new_priority"
    display_name: str,
    description: str | None = None,
    options: list[tuple[int | None, str]] | None = None,  # value (None=auto) + label
    is_global: bool = True,
    solution: str | None = None,
) -> OptionSetCreateResult:
    """POST /GlobalOptionSetDefinitions. Read-back via OData-EntityId."""

def delete_optionset(
    backend: D365Backend, name: str, *, solution: str | None = None,
) -> dict[str, Any]:
    """DELETE /GlobalOptionSetDefinitions(Name='<name>'). 400 if any picklist still references it."""
```

### 5.2 Granular `update_optionset`

```python
def update_optionset(
    backend: D365Backend,
    name: str,
    *,
    insert: list[tuple[int | None, str]] | None = None,   # (value or None=auto, label)
    update: list[tuple[int, str]] | None = None,          # (value, new_label)
    delete: list[int] | None = None,                      # [value, ...]
    reorder: list[int] | None = None,                     # full ordered value list
    solution: str | None = None,
) -> dict[str, Any]: ...
```

Dispatch order: `insert` → `update` → `delete` → `reorder`. Each step is its own bound-action call:

| Mutation | Action | Body |
|---|---|---|
| insert | `POST /InsertOptionValue` | `{OptionSetName, Value?, Label: _label(...)}` |
| update | `POST /UpdateOptionValue` | `{OptionSetName, Value, Label: _label(...), MergeLabels: false}` |
| delete | `POST /DeleteOptionValue` | `{OptionSetName, Value}` |
| reorder | `POST /OrderOption` | `{OptionSetName, Values: [int, ...]}` |

Each call accepts `--solution`. On partial failure, dispatcher stops at the failing stage and returns `{stage, completed_steps, error}`. **No rollback**: multi-stage atomic update is doable via Spec C's `$batch` changeset but out of scope here. Empty operation set → `D365Error("nothing to update")` before any HTTP.

### 5.3 CLI surface

```
crm metadata list-optionsets [--custom-only] [--top N]
crm metadata get-optionset <name>

crm metadata create-optionset \
    --name new_priority --display "Priority" [--description "..."] \
    [--option 1:Low --option 2:Medium --option 3:High]   # repeatable; value:label
    [--solution <name>] [--publish/--no-publish]

crm metadata update-optionset <name> \
    [--insert :NewLabel | --insert 7:Pinned]            # repeatable; empty value = auto
    [--update 2:NewMediumLabel]                          # repeatable
    [--delete 3]                                         # repeatable
    [--reorder 1,2,7,4]                                  # comma list, full set
    [--solution <name>] [--publish/--no-publish]

crm metadata delete-optionset <name> [--yes] [--solution <name>]
```

`delete-optionset` follows the same safety pattern as `delete-entity` (§6): `click.confirm` + `--yes` skip + client-side `IsCustomOptionSet=true` + `IsManaged=false` pre-flight.

### 5.4 `--option value:label` parsing

Custom Click param type `OptionTuple` parses `<int>:<string>` or `:<string>` (value auto-assigned by server). Repeatable. Validation: label non-empty, value is `int` or empty, no duplicate values within a single command.

---

## 6. `delete-entity` + delete-optionset safeguards

### 6.1 `delete_entity`

```python
def delete_entity(
    backend: D365Backend,
    logical_name: str,
    *,
    solution: str | None = None,
) -> dict[str, Any]: ...
```

Two-step:

1. **Pre-flight GET** — `EntityDefinitions(LogicalName='<x>')?$select=IsCustomEntity,IsManaged`.
   - Refuse with `D365Error(code="NotCustomEntity")` if `IsCustomEntity` is `False`.
   - Refuse with `D365Error(code="ManagedEntity")` if `IsManaged` is `True` (managed entities require uninstalling the parent solution).
2. **DELETE** — `DELETE /EntityDefinitions(LogicalName='<x>')` with `MSCRM.SolutionUniqueName` header if `solution` is set. Server enforces remaining dependency checks (workflows, forms, relationships) and returns 4xx on conflict — surfaced as `D365Error`.

Returns `{"deleted": True, "logical_name": "<x>", "solution": solution_or_None}`. No publish (delete doesn't require it).

### 6.2 CLI

```python
@metadata.command("delete-entity")
@click.argument("logical_name")
@click.option("--yes", is_flag=True, help="Skip interactive confirmation.")
@click.option("--solution", default=None, help="Apply via MSCRM.SolutionUniqueName.")
@pass_ctx
def metadata_delete_entity(ctx, logical_name, yes, solution):
    """Permanently delete a custom entity (table) and ALL its rows."""
    if not yes:
        if not click.confirm(
            f"This will permanently delete entity {logical_name!r} and ALL its data. Continue?",
            default=False,
        ):
            ctx.emit(False, error="aborted by user")
            return
    try:
        info = meta_mod.delete_entity(ctx.backend(), logical_name, solution=solution)
    except D365Error as exc:
        _handle_d365_error(ctx, exc)
        return
    ctx.emit(True, data=info)
```

### 6.3 Interactive notes

- `click.confirm` reads from stdin. In non-TTY contexts (CI, REPL eval, piped scripts) Click 8.x defaults to **abort** when stdin is non-interactive — fail-safe. `--yes` is mandatory for scripted use.
- `--json` mode: same flow. The confirm prompt prints to stderr; JSON emit happens after confirm passes (or `aborted by user` on the failure envelope).

### 6.4 `delete-optionset` (same pattern)

- Pre-flight: `GlobalOptionSetDefinitions(Name='<n>')?$select=IsCustomOptionSet,IsManaged`. Refuse on non-custom or managed.
- `click.confirm` + `--yes` skip.
- DELETE `/GlobalOptionSetDefinitions(Name='<n>')`. Server rejects with 400 if a picklist attribute still references it — error surfaces unmodified.

A shared helper `_confirm_destructive(thing: str, name: str, yes: bool) -> bool` in `cli.py` DRYs the confirm-or-yes pattern. Returns `True` to proceed, `False` to bail with `aborted by user`.

---

## 7. Testing

### 7.1 Unit (fake backend)

Fake backend records each call as `("METHOD", path, json_body, extra_headers)`. Mirrors existing `test_core.py` patterns.

New files:

```
crm/tests/
  test_metadata_attrs.py   — 14 kinds × {happy, validation error, dry-run, read-back fail}
  test_relationships.py    — 1:N + N:N happy, cascade defaults, schema validation
  test_optionsets.py       — list/get/create/update (4 mutation stages)/delete
  test_delete_entity.py    — refuse non-custom, refuse managed, happy, server-rejected
```

Coverage targets per `add_attribute`:

| Test class | What it asserts |
|---|---|
| `TestAddAttributeString` | `max_length` set, `format_name` default `Text`, schema-prefix check |
| `TestAddAttributeNumeric` | `precision` in range per kind, `min_value`/`max_value` wired |
| `TestAddAttributeBoolean` | `_label` for true/false built correctly |
| `TestAddAttributePicklist` | `--optionset-name` (global ref) vs `--option` inline (local) — mutually exclusive |
| `TestAddAttributeLookup` | Dispatches to `create_one_to_many`, threads `target_entity` / `lookup_schema` |
| `TestAddAttributeImageFile` | One-image-per-entity error path; file `max_size_kb` default |
| `TestAddAttributeValidation` | `D365Error` raised before HTTP for: forbidden flag per kind, missing required flag, bad enum value |
| `TestAddAttributeDryRun` | `_dry_run` envelope, no read-back, no publish |
| `TestAddAttributeReadbackFail` | `attribute_lookup_error` populated, `created: True` still surfaces |
| `TestAddAttributeNonAsciiLabel` | UTF-8 display names round-trip through `_label()` |

Per `update_optionset`:

- 4 dispatch-order tests (insert → update → delete → reorder), each isolated.
- Partial-failure mid-stage returns `{stage, completed_steps, error}`.
- Empty operation set → `D365Error("nothing to update")` before any HTTP.

Per `delete_entity`:

- Refuse on `IsCustomEntity=False`.
- Refuse on `IsManaged=True`.
- Happy: pre-flight GET + DELETE called in order, correct headers.
- DELETE failure surfaces `D365Error` unchanged.

### 7.2 CLI (Click `CliRunner`)

Extend `test_full_e2e.py`:

- `add-attribute --kind boolean ...` → success, JSON envelope shape.
- `add-attribute --kind picklist --option 1:A --option 2:B` parsed correctly.
- `create-one-to-many` with cascade defaults.
- `delete-entity` without `--yes`, non-TTY → `aborted by user`.
- `delete-entity --yes` happy path.
- `delete-entity` on system entity (`account`) → refused before HTTP.

`CliRunner.invoke(input="y\n")` exercises the `click.confirm` branch.

### 7.3 Live e2e (`@pytest.mark.live`)

Gated by `D365_LIVE=1` (existing pattern). Sequence:

1. `create-entity` ephemeral (unique schema suffix: epoch + 8-char uuid).
2. `add-attribute` all 14 kinds against the ephemeral entity (skip `image`/`file` if server feature flag disabled; mark skipped).
3. `create-optionset` ephemeral global.
4. `update-optionset` insert / update / delete / reorder, full dispatch.
5. `add-attribute --kind picklist --optionset-name <ephemeral>` referencing it.
6. `create-one-to-many` to a stock entity (`account`).
7. `create-many-to-many` between two ephemeral entities.
8. Cleanup: `delete-entity` on ephemeral entities; `delete-optionset` last.
9. Verify-gone: GETs return 404.

Ephemeral naming: `e2e_metadata_<timestamp>_<uuid8>`. Test marks itself `xfail` (visible signal, doesn't break CI) instead of hard-failing if cleanup leaves residue.

### 7.4 Pyright

All new files in `crm/core/*` strict zone — `pyrightconfig.json` glob covers them automatically. `crm/utils/d365_types.py` already strict. Build CI step unchanged.

---

## 8. PR sequencing

Decided in clarifier round: **one PR, ship as 0.5.0**. Matches Spec A/B/C cadence (each shipped under a single version bump per spec).

Suggested internal commit order for review-ability (not enforced; not separate PRs):

1. New `crm/core/optionsets.py` + unit tests.
2. New `crm/core/relationships.py` (move `list_relationships` out of `metadata.py`) + unit tests.
3. New `crm/core/metadata_attrs.py` + 14 builder tests.
4. Extend `metadata.py` with `delete_entity` + tests.
5. CLI surface: new click commands + click tests.
6. Live e2e additions in `test_full_e2e.py`.
7. `setup.py` 0.5.0 + `CHANGELOG.md` 0.5.0 section.
8. README updates (commands table only — Spec A established that pattern).

---

## 9. Risk + open issues

### Risks

| Risk | Likelihood | Impact | Mitigation |
|---|---|---|---|
| `add-attribute` flag matrix grows error-prone (14 kinds × ~10 flags each) | Medium | Confusing UX | Per-kind validation table (§3.2) raises `D365Error` before HTTP; unit-test every forbidden-flag combination |
| Lookup dispatching to `create_one_to_many` surprises users (creates relationship as side effect) | Medium | User astonishment | Docstring + `--help` warning: "creates 1:N relationship automatically; use `create-one-to-many` for cascade control" |
| `MultiSelectPicklist`, `Image`, `File` attribute metadata not uniformly available on v9.1 on-prem | Medium | Server 400 with vague error | Document supported-version notes in command docstring; surface server error verbatim — no client-side gate that could go stale |
| `update-optionset` partial failure leaves option set in half-mutated state | Low | Manual cleanup | `{stage, completed_steps, error}` envelope tells caller exactly where it stopped. Follow-up: wrap in `$batch` changeset (Spec C primitive exists) |
| Race between pre-flight GET and DELETE on `delete-entity` (entity removed by another caller in between) | Very low | Confusing 404 | DELETE 404 surfaces as `D365Error(code="NotFound")`; caller re-checks |
| `click.confirm` on `delete-entity` in non-TTY hangs on older Click | Very low | CI hang | Project pins `click>=8` (`setup.py`); 8.x aborts on non-TTY by default |
| Cascade enum mistyped at CLI | Low | Click error | `click.Choice` enforces; crisp error |
| `_label()` non-ASCII handling | Low | Garbled display name | Existing `_label()` accepts any `str`; Dataverse stores UTF-8. Add `test_non_ascii_label` |
| `MSCRM.SolutionUniqueName` against managed solution rejects with 4xx | Medium | Confusing | Surface server error; document that `--solution` must be the user's *unmanaged* working solution |
| Read-back GET after DELETE entity briefly returns the row (eventual consistency) | Low | Stale read | DELETE returns immediately on HTTP success; no verify-gone in the helper (test-only) |
| `File` / `Image` attribute requires server-side feature toggles on some v9.1 builds | Low | Server 400 | No client-side gate; document the toggle path in the command docstring |

### Open issues (deferred — called out so callers know)

1. **No `$batch` rollback for `update-optionset`.** Multi-stage update not transactional. Caller can construct a batch file manually using Spec C primitives. Future: `update-optionset --batch` flag.
2. **No `rename-entity` / `rename-attribute`.** PATCH on `EntityMetadata` / `AttributeMetadata` requires whole-object PUT semantics. Out of scope.
3. **No calculated / rollup attribute support.** Spec E candidate.
4. **No alternate-key (`EntityKey`) CRUD.** Different endpoint. Deferred.
5. **No `RetrieveDependenciesForDeleteRequest` pre-flight.** Server-side dependency check on DELETE is sufficient given the chosen UX. Add `delete-entity --check-dependencies` later if real demand emerges.
6. **Lookup attribute via `add_attribute` cannot configure cascade.** Default cascade only. Power users use `create-one-to-many` directly. Documented in help.
