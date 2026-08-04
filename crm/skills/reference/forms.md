# Forms — `form` (entity main forms / systemform)

Edit entity forms headlessly: fields, presentation properties, JS event handlers,
and the tab/section skeleton. Group: `form`. Flags/choices: `crm form --help`.
Every mutating form verb is solution-scoped and stages by default (see the
SKILL.md agent contract); `list` and `export` are read-only and take no
`--solution`.

```bash
crm --json form list contoso_ticket                                 # main forms only (the default)
crm --json form list contoso_ticket --all                           # every form type, not just main
crm --json form clone contoso_ticket "Information" --to contoso_ticketclone --solution ContosoCore  # clone a named form to another table
crm --json form export contoso_ticket "Information" --output form.xml     # export a form's formxml
```

## Add / remove / move a field — first-class verbs

Use `form add-field`, `form remove-field`, and `form set-field` directly — no manual
FormXml editing required. The CLI resolves the control `classid` from live metadata and
PATCHes the `systemform` record.

```bash
crm --json form add-field contoso_ticket contoso_priority --solution ContosoCore            # add to first section of first tab
crm --json form remove-field contoso_ticket contoso_priority --solution ContosoCore         # remove; errors if absent
crm --json form set-field contoso_ticket contoso_priority \
    --tab "Details" --section "Status" --solution ContosoCore                        # relocate; errors if not already present
```

**Verify after publish, not before.** `GET /systemforms` returns the *published*
FormXml (staged writes, SKILL.md), so re-export and check the control only
**after** publishing — pass `--publish` on the write or run `solution publish-all`.
A malformed splice publishes silently but the control is absent from the exported
XML, so the post-publish re-export is the real verification.

```bash
crm --json form add-field contoso_ticket contoso_priority --solution ContosoCore --publish   # PATCH + PublishAllXml in one call
```

**Unmapped types — fallback to hand-splice.** `add-field` maps the common
`AttributeType` values (text, numeric, money, datetime, boolean, option-set, lookup
families) to their control `classid` automatically. For a type with no mapped
constant (e.g. multi-select option sets, floating-point) the command **errors and
names the supported set** rather than guess an invalid classid — fall back to the
manual pipeline below for those.

**`--dry-run` support.** All three verbs honor the global `--dry-run` flag: live
metadata + the form are still fetched, no PATCH is issued, and the response
carries `would_add` / `would_remove` / `would_move: true`.

## Toggle field presentation properties — `set-field-props`

```bash
crm --json form set-field-props contoso_ticket contoso_priority \
    --disabled --hidden --locked --no-show-label --solution ContosoCore --publish
# → data: {updated: true, published: true, disabled: true, visible: false, locked: true, show_label: false}
```

Toggles presentation attributes of an existing field in-place (no GUID/classid
surface). At least one flag is required; omitted flags are left untouched — and only
the flags you pass echo back in the result (keyed by flag name, e.g. `locked`, not the
underlying `locklevel`). Errors if the field is not on the form — use `add-field` first.

**`--required` routes to metadata, not the form.** Required-level is an attribute
metadata property, not a form property. Passing `--required LEVEL` here errors with a
clear redirect to `crm metadata update-attribute ENTITY ATTRIBUTE --required LEVEL`
rather than silently no-op'ing at the form layer.

**Cell vs control — where each flag lands.** `disabled` is a `<control>` attribute;
`locklevel`, `showlabel`, and `visible` are `<cell>` attributes. The FormXml schema
rejects `visible` on a `<control>` — the CLI applies each flag to the correct element.

**`--dry-run`** returns `{_dry_run: true, would_update: true, …}` (plus the echoed
flags) with no PATCH.

## Wire JS event handlers — `add-library`, `add-handler`, `remove-handler`, `list-handlers`

**Web resource must already exist.** The editor never creates web resources. Register
with `webresource create` first (see `reference/webresource-ribbon.md`), then wire.

```bash
# 1. Register the library only (idempotent — safe to repeat)
crm --json form add-library contoso_ticket --library contoso_/scripts/ticket.js --solution ContosoCore

# 2. Wire a handler (registers the library too — deduped)
crm --json form add-handler contoso_ticket \
    --event onload --library contoso_/scripts/ticket.js --function App.onLoad --solution ContosoCore

# onchange needs --field naming a field that is already on the form
crm --json form add-handler contoso_ticket \
    --event onchange --field contoso_priority \
    --library contoso_/scripts/ticket.js --function App.onPriorityChange --solution ContosoCore

# 3. Inspect
crm --json form list-handlers contoso_ticket
# → data: bare array [{event, field, function, library, enabled, pass_context, handler_unique_id}];
#   meta: {formid, form}. Only <Handlers> (customizer-owned) — never <InternalHandlers>.

# 4. Remove (event + function; add --field for onchange)
crm --json form remove-handler contoso_ticket \
    --event onload --function App.onLoad --solution ContosoCore
```

**`--field` is required for `onchange`, invalid for `onload`/`onsave`.** The command
also validates that `--field` is on the form before wiring. Duplicate handlers (same
event + function) are refused.

**`--dry-run`:** reads run for real; no PATCH. add-library → `would_add_library`,
add-handler → `would_add_handler`, remove-handler → `would_remove_handler`.

**Handlers vs InternalHandlers.** Every `<event>` element holds two sibling blocks:
`<Handlers>` (customizer-owned, what the CLI writes) and `<InternalHandlers>`
(platform-owned, never touched). `list-handlers` reports only `<Handlers>`. Do not
hand-splice entries into `<InternalHandlers>`.

## Edit the tab/section skeleton — `form {add,remove,rename,move}-{tab,section}`

Eight verbs edit the form's tab/section structure (the same PATCH pipeline and
staged semantics as the field verbs; `--dry-run` returns `would_add` /
`would_remove` / `would_rename` / `would_move`):

```bash
crm --json form add-tab contoso_ticket contoso_details --label "Details" --solution ContosoCore     # tab + starter section
crm --json form add-section contoso_ticket contoso_status --tab contoso_details --solution ContosoCore  # section into a tab
crm --json form move-tab contoso_ticket contoso_details --after "General" --solution ContosoCore    # reorder
crm --json form remove-tab contoso_ticket contoso_details --force --solution ContosoCore            # --force orphans bound fields
```

Gotchas the flags don't tell you:

- A **new tab always carries a non-empty starter section** — an empty tab is
  XSD-valid but renders broken, so the verbs never produce one. `add-section` is the
  way to create a section to target before `add-field` on a sectionless tab.
- `rename-{tab,section}` changes the **display label only**; the logical `name`
  (what form scripts and `--tab`/`--section`/`--after` match on) is left intact.
- `remove-{tab,section}` **refuses an orphaning remove** (a tab/section still holding
  bound fields) and `remove-tab` refuses the **only** tab. Pass `--force` to remove
  anyway; the orphaned field names come back in the response under `orphaned`.
- Sections default to the **first tab** when `--tab` is omitted.

## Labels are a single-language projection — silent loss on write

Tab/section/cell labels are NOT stored in the formxml. The platform keeps them in
a per-language label store and serves/accepts `formxml` **projected to the caller's
`usersettings.uilanguageid`**. Consequences an agent must plan for:

- `export` shows each label in **your** UI language only, even when the store holds
  more. It emits a projection note (`--json` → `meta.warnings`; human → stderr, so
  piped formxml stays clean).
- Every `form` write **warns** (in `meta.warnings`) when the outgoing formxml carries
  a `<label>` in a language other than yours, because the platform **silently
  discards** it (204, no error) — then a read-back "passes" by re-projecting to your
  language, so bilingual loss is invisible. The warning is advisory; the write still
  proceeds.
- CLI-authored labels are hardcoded `1033`, so a non-1033 caller is warned even on a
  plain `add-field`.
- The raw seam (`entity update systemforms … {formxml}` in the manual splice below,
  and `crm batch` PATCH) is **unwarned** — the same discard still applies.

To set labels in another language, use `crm translation export`/`import`, **not**
formxml: there they appear as lowercase `displayname` rows keyed by `LabelId` (not
the capitalized attribute `DisplayName` rows).

## Manual splice — fallback for unmapped control types

Only needed when the attribute type has no mapped `classid` (see above):

```bash
crm --json form export contoso_ticket "Information" --output form.xml
# Copy the <control classid="…"> from a stock table that already carries that
# control type (e.g. account), splice a <cell> into the target <section>, then:
crm entity update systemforms <formid> --data-file form-update.json   # {"formxml":"…"}
crm solution publish --xml \
    '<importexportxml><entities><entity>contoso_ticket</entity></entities></importexportxml>'
```

Use `--data-file`, **not** inline `--data` — FormXml is quote-heavy and must be
JSON-escaped. Get `<formid>` from `form list`.

On Unified Interface a cloned/added form may need adding to the model-driven app's form
list to be visible.
