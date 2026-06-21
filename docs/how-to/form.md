# How-to: form

List, clone, and export entity forms (systemform). See the
[CLI reference](../reference/cli.md) for every flag.

## List an entity's forms

```bash
crm form list cwx_ticket
```

Output columns: `name`, `type`, `formid`, `isdefault` (`type` is the raw
systemform optionset integer). By **default only main forms are returned**.

Use `--type` to filter to one or more form types (repeatable,
case-insensitive); recognized tokens are `dashboard`, `main`, `quickview`,
`quickcreate`, `dialog`, and `card`:

```bash
crm form list cwx_ticket --type quickcreate
crm form list cwx_ticket --type main --type quickview
```

Use `--all` to list every form type (no type filter):

```bash
crm form list cwx_ticket --all
```

`--type` and `--all` are mutually exclusive — passing both is a usage error.

## Clone a form to another entity

```bash
crm form clone cwx_ticket "Ticket Main Form" --to new_incidentlog
```

The source form's `formxml` is read from `cwx_ticket`, the entity reference is
retargeted to `new_incidentlog`, the form's internal element/label ids are
regenerated, and a new `systemform` record is created via the Web API. No
solution-zip is needed. Regenerating the internal ids lets you clone the same
source form repeatedly without collisions on on-premises (which — unlike
Dataverse online — enforces those ids as unique); references to control types,
security roles, and subgrid views are preserved.

By default the clone also runs `PublishAllXml`. To defer the publish (e.g.
when doing multiple operations before a single publish at the end):

```bash
crm form clone cwx_ticket "Ticket Main Form" --to new_incidentlog --no-publish
```

### Add the clone to a solution

```bash
crm form clone cwx_ticket "Ticket Main Form" --to new_incidentlog \
    --solution cwx_crmworx
```

Use `--require-solution` to fail if no solution name is resolved:

```bash
crm form clone cwx_ticket "Ticket Main Form" --to new_incidentlog \
    --require-solution
```

### Ambiguous form names

If two main forms share the same name, the command errors rather than guessing:

```
Error: Ambiguous: 2 forms named 'Ticket Main Form' — cannot pick one
automatically. Matches: formid='aaa...' type=2, formid='bbb...' type=2
```

Use `crm form list cwx_ticket` to inspect the colliding formids, then rename
one form in the UI before cloning.

### Relationship to `metadata clone-entity --with-forms`

`crm form clone` is the standalone surface for the same form-clone logic that
`crm metadata clone-entity --with-forms` invokes when duplicating a whole
entity. Use `crm form clone` when you only need to push a single form to another
entity without cloning the entity itself.

## Add a field to a form

```bash
crm form add-field cwx_ticket cwx_priority
```

Resolves the control `classid` from the attribute's live metadata (no manual
lookup needed), then splices a `<cell>`/`<control>` into the target section and
PATCHes the `systemform` record. Errors if the field is already on the form, the
attribute does not exist on the entity, or the attribute type has no mapped
`classid` (see "Unmapped types" below).

By default the field lands in the first section of the first tab. Narrow the
target with `--tab` and `--section` (by name or id):

```bash
crm form add-field cwx_ticket cwx_priority --tab "General" --section "Details"
```

Use `--form` when the entity has more than one main form; without it the command
errors if the choice is ambiguous.

### Publishing

These verbs run `PublishAllXml` **by default** (the CLI-wide convention shared with
`form clone`, `metadata create-entity`, etc.), so a field edit takes effect right
away. This matters here: `GET /systemforms` returns the **published** FormXml, so an
unpublished PATCH is invisible in the UI and on re-export.

Use `--no-publish` to **stage a single edit** and publish it later with
`crm solution publish`:

```bash
crm form add-field cwx_ticket cwx_priority --no-publish   # stage one edit
crm solution publish                                       # publish when ready
```

!!! warning "Don't chain `--no-publish` edits to one form"
    Each verb recomputes the FormXml from the form's **published** snapshot. A
    second `--no-publish` edit therefore reads the form *without* the first
    edit's pending change and overwrites it. To make several edits, keep the
    default (publish each), or publish between edits — only the last
    `--no-publish` write survives otherwise.

### Add to a solution

```bash
crm form add-field cwx_ticket cwx_priority --solution cwx_crmworx
```

### Preview without writing

```bash
crm --dry-run form add-field cwx_ticket cwx_priority
```

Returns `would_add: true` with the resolved `classid` and target coordinates;
no PATCH is issued.

### Unmapped attribute types

The command maps these `AttributeType` values to their control `classid`:
String, Memo, Integer, Decimal, Money, DateTime, Boolean, Picklist, State,
Status, Lookup, Customer, Owner, PartyList. For any other type (Double,
MultiSelectPicklist, BigInt, Uniqueidentifier, …) the command errors rather
than guess a classid. In that case, export the form, hand-splice the
`<control>` (copy the `classid` from a stock table that already has the
control), PATCH back, and publish — same pipeline as before this feature
existed (see `crm form export`).

## Remove a field from a form

```bash
crm form remove-field cwx_ticket cwx_priority
```

Removes the field's `<cell>` from the form layout (and tidies an emptied
`<row>`), then PATCHes the `systemform` record. Errors if the field is not on
the form.

```bash
crm form remove-field cwx_ticket cwx_priority --no-publish   # stage only
crm --dry-run form remove-field cwx_ticket cwx_priority      # would_remove: true
```

## Move a field to a different tab or section

```bash
crm form set-field cwx_ticket cwx_priority --tab "Details" --section "Status"
```

Relocates an existing field's `<cell>` to the target tab/section. The cell
(including its id and control binding) is preserved — only its position changes.
Errors if the field is not already on the form (use `add-field` first).

```bash
crm form set-field cwx_ticket cwx_priority \
    --tab "Details" --section "Status" --solution cwx_crmworx
crm --dry-run form set-field cwx_ticket cwx_priority --tab "Details"  # would_move: true
```

## Wire JS event handlers

The CLI manages JS script libraries and event handlers directly in the form's
`<formLibraries>` and `<events>` XML without any manual FormXml editing. The three
mutating verbs mirror the field editors and share the same `--form`, `--publish`,
`--solution`, `--json`, and `--dry-run` conventions; `list-handlers` is read-only
(`--form` and `--json` only).

**Prerequisite: the web resource must already exist.** The editor never creates
web resources — register them first with `crm webresource create`, then wire them.

### Register a script library

```bash
crm form add-library cwx_ticket --library cwx_/scripts/ticket.js
```

Registers `cwx_/scripts/ticket.js` in the form's `<formLibraries>`. The
operation is idempotent — if the library is already registered, the command
succeeds without adding a duplicate entry. Under `--dry-run` the response
carries `would_add_library: true` and issues no PATCH.

```bash
crm form add-library cwx_ticket --library cwx_/scripts/ticket.js \
    --solution cwx_crmworx
crm --dry-run form add-library cwx_ticket --library cwx_/scripts/ticket.js
```

### Wire an event handler

`form add-handler` registers the library (deduped) **and** adds the handler in
one call. The handler lands in `<Handlers>` (the customizer-owned block) and is
always appended last, so existing handler order is preserved.

```bash
# onload — fires when the form opens
crm form add-handler cwx_ticket \
    --event onload \
    --library cwx_/scripts/ticket.js \
    --function App.onLoad

# onsave — fires when the record is saved
crm form add-handler cwx_ticket \
    --event onsave \
    --library cwx_/scripts/ticket.js \
    --function App.onSave

# onchange — fires when a specific field changes; --field is required
crm form add-handler cwx_ticket \
    --event onchange --field cwx_priority \
    --library cwx_/scripts/ticket.js \
    --function App.onPriorityChange
```

**`--field` is required for `onchange`, and invalid for `onload`/`onsave`.**
The command also validates that `--field` names a field that is already on the
form — add it first with `crm form add-field` if needed.

**Duplicate handlers are refused.** Adding the same event + function combination
a second time errors rather than creating a duplicate entry.

Additional options (defaults shown):

- `--pass-context` / `--no-pass-context` — pass the execution context as the
  function's first argument (default: on).
- `--enabled` / `--no-enabled` — whether the handler is active (default: on).
- `--param` — repeatable; emitted as a comma-separated `parameters` attribute.

```bash
crm form add-handler cwx_ticket \
    --event onload \
    --library cwx_/scripts/ticket.js \
    --function App.onLoad \
    --no-pass-context \
    --param "debug=false" \
    --param "verbose=true"
```

Under `--dry-run` the response carries `would_add_handler: true`.

### List handlers

`form list-handlers` is read-only — no `--publish` or `--solution` flag.

```bash
crm form list-handlers cwx_ticket
```

Output columns: `event`, `field` (blank for onload/onsave), `function`, `library`,
`enabled`, `pass_context`. Per the output contract, `data` is a **bare array** of
handler rows and the resolved form goes to `meta`:

```json
{
  "ok": true,
  "data": [
    {
      "event":            "onload",
      "field":            null,
      "function":         "App.onLoad",
      "library":          "cwx_/scripts/ticket.js",
      "enabled":          true,
      "pass_context":     true,
      "handler_unique_id":"<guid>"
    }
  ],
  "meta": { "formid": "<guid>", "form": "Ticket Main Form" }
}
```

Only the customizer-wired `<Handlers>` are listed — the platform-internal
`<InternalHandlers>` are never reported.

### Remove a handler

```bash
crm form remove-handler cwx_ticket \
    --event onload --function App.onLoad

# onchange requires --field
crm form remove-handler cwx_ticket \
    --event onchange --field cwx_priority --function App.onPriorityChange
```

Removes the handler identified by event + function (plus field for onchange).
Tidies any now-empty `<Handlers>`, `<event>`, or `<events>` containers so no
invalid empty XML is left behind. Errors if the handler is not found.

```bash
crm --dry-run form remove-handler cwx_ticket \
    --event onload --function App.onLoad   # would_remove_handler: true, no PATCH
```

### Publishing and the publish-then-read-back gotcha

These verbs run `PublishAllXml` by default (same as the field editors). Because
`GET /systemforms` returns the **published** FormXml, an unpublished PATCH is
invisible on re-export and in the UI — always publish before verifying with
`form list-handlers` or `form export`.

!!! warning "Don't chain `--no-publish` edits to one form"
    Each verb recomputes the FormXml from the **published** snapshot. A second
    `--no-publish` edit overwrites any pending unpublished change from the first.
    Keep the default (publish each), or publish between edits — only the last
    `--no-publish` write survives otherwise.

```bash
crm form add-handler cwx_ticket \
    --event onload --library cwx_/scripts/ticket.js \
    --function App.onLoad --no-publish    # stage only
crm solution publish                       # publish when ready
```

### Handlers vs InternalHandlers

The FormXml `<events>` element contains two sibling blocks per event:
`<Handlers>` (customizer-owned) and `<InternalHandlers>` (platform-owned).
The CLI only reads and writes `<Handlers>`. Do not hand-splice entries into
`<InternalHandlers>` — those are managed by the platform and survive upgrades
independently.

## Export a form's formxml

```bash
crm form export cwx_ticket "Ticket Main Form"      # prints formxml to stdout
crm form export cwx_ticket "Ticket Main Form" --output ticket_main.xml
```

`export` is read-only and does not require `--solution`. The exported XML can
be inspected directly or used as a reference when authoring customizations.
`--output` writes the XML to the given path; omitting it prints to stdout,
which is convenient for piping or quick inspection.
