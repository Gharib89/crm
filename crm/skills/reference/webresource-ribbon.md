# Web resources & ribbon — `webresource`, `ribbon`

Upload and bulk-push web resources (HTML/JS/CSS/images), and edit entity
command-bar (ribbon) buttons. Groups: `webresource`, `ribbon`. Flags/choices:
`crm <group> --help`. Mutating verbs are solution-scoped and stage by default
(see the SKILL.md agent contract); a ribbon button that runs a web resource needs
that **web resource to already exist** — create it first.

## Web resources — `webresource` (HTML/JS/CSS/images)

```bash
# create: --file bytes are base64'd into `content`; webresourcetype is inferred from
# the extension (the real D365 option set: .css=2, .xap=8/Silverlight). An unknown
# extension without an explicit --type is rejected.
crm --json webresource create --name contoso_/scripts/ribbon.js --file ./ribbon.js --solution ContosoCore

# update <name>: plain PATCH of only the sent fields (content and/or display-name),
# resolved by name — NOT retrieve-merge.
crm --json webresource update contoso_/scripts/ribbon.js --file ./ribbon.js --solution ContosoCore

# inspect
crm --json webresource get contoso_/scripts/ribbon.js
crm --json webresource list --custom-only

# use as a model-driven app icon
crm --json webresource create --name contoso_/icons/app.svg --file ./app.svg --solution ContosoCore
crm --json app create --name "Contoso Sales" --unique-name contoso_salesapp --icon-webresource contoso_/icons/app.svg --solution ContosoCore
```

### Bulk push — `webresource push <DIRECTORY> --prefix <p>`

Upserts every file in a directory tree in one run.

**Naming convention:** each file maps to `<prefix>_<relpath>` where `<relpath>` is the
file's path relative to `DIRECTORY` using `/` separators. `webresources/scripts/ribbon.js`
with `--prefix contoso` → `contoso_scripts/ribbon.js`. Type is inferred from the file extension.

**Upsert semantics:**
- Creates a missing resource, updates one whose content changed, skips byte-identical ones
  (no write, no publish for that file).
- `push` **stages** by default, same as `create`/`update`. A single `PublishAllXml`
  fires at the end only when `--publish` is passed **and** at least one file was
  created or updated; otherwise run `solution publish-all` afterward.
- Per-file failures do not abort the run — the rest push (and publish, if `--publish`
  was passed). Exit 1 if any file failed, 0 otherwise.

**Dry-run** (global `--dry-run`) runs the live GETs, issues no writes, returns
`would_create` / `would_update` name lists plus a `skipped` count.

**JSON contract:**

Real run `data` (shown with `--publish`; omit it and `published` is `false`):
```json
{"pushed": 3, "updated": 1, "skipped": 2, "published": true,
 "failed": [], "files": [{"name": "contoso_scripts/ribbon.js", "action": "created"}, ...]}
```

Dry-run `data`:
```json
{"_dry_run": true, "would_create": ["contoso_scripts/ribbon.js"],
 "would_update": ["contoso_scripts/form.js"], "skipped": 2, "published": false, "failed": [], "files": [...]}
```

**Continuous redeploy** — there is no `--watch` flag; use `entr` or `watchexec`:

```bash
# find (not `ls **/*` — bash globstar is off by default and would match nothing)
find webresources -type f \( -name '*.js' -o -name '*.css' -o -name '*.html' \) | \
  entr crm webresource push webresources --prefix contoso
watchexec -e js,css,html -- crm webresource push webresources --prefix contoso
```

### Deleting a web resource

A web resource still referenced by a ribbon button won't delete — the server
fails with `0x8004f01f` (still referenced). Preview blockers first with
`crm webresource delete <name|id> --check-dependencies`, remove the button
(`ribbon remove …`), then retry `crm webresource delete <name|id> --yes`.

## Ribbon — `ribbon` (entity command-bar buttons)

The ribbon is stored as `RibbonDiffXml` and has **no first-class Web API write path**:
this group drives it through a solution zip + XML pipeline, so **every subcommand
except `export` works through the solution-zip pipeline — all solution-scoped.**

```bash
crm --json ribbon export account                 # one table's composed RibbonDiffXml
crm --json ribbon export --application           # application-wide ribbon (no ENTITY)
crm --json ribbon list account --solution ContosoCore
crm --json ribbon add-button account --solution ContosoCore ...
crm --json ribbon set-label account --solution ContosoCore --button-id <CustomAction_Id> ...
crm --json ribbon set-icon account --solution ContosoCore --button-id <CustomAction_Id> ...
crm --json ribbon remove account --solution ContosoCore ...
crm --json ribbon hide-button account --solution ContosoCore --target-id <OOB_Id>
crm --json ribbon set-rules account --solution ContosoCore \
    --command-id account.form.MyBtn.Command \
    --enable-rule Mscrm.SelectionCountExactlyOne
crm --json ribbon add-custom-rule account --solution ContosoCore \
    --command-id account.form.MyBtn.Command \
    --webresource contoso_/scripts/ribbon.js --function ns.canRun
```

**`ribbon export` — give exactly one target.** An `ENTITY` exports that one
table's ribbon; `--application` exports the app-wide ribbon (the commands not
bound to any table). They are mutually exclusive — omitting both, or giving both,
errors. The app-wide path returns its zipped XML under `CompressedApplicationRibbonXml`
(not the entity path's `CompressedEntityXml`) — relevant only if you decode the
raw `--dry-run` response yourself.

This is why a cloned entity's ribbon does not come across (see the clone caveats in
`reference/metadata.md`) — there is no API write path to copy it.

**Ribbon writes are slow and synchronous.** Because every write rides the solution-zip
pipeline, `add-button` / `set-label` / `set-icon` / `remove` / `hide-button` /
`set-rules` / `add-custom-rule` run a **full solution import per call** — 60–120s with no progress
ticks. The command has not hung; **do not retry** a slow call (a second, parallel
attempt races the first import). Confirm the outcome afterward with `ribbon list`.

**Platform rule allow-list — the server silently ignores unknown `Mscrm.*` ids.**
`set-rules` validates each `Mscrm.*` id against a curated allow-list and rejects
unrecognised platform ids before touching the solution, because the server would
otherwise accept the import and silently discard the unrecognised rule with no error.
Custom (non-`Mscrm.`) ids pass through — they reference rules defined in the same
solution. Allowed platform enable rules: `Mscrm.SelectionCountExactlyOne`,
`Mscrm.ShowOnGrid`, `Mscrm.ShowOnQuickAction`, `Mscrm.ShowOnGridAndQuickAction`.
Allowed platform display rules: `Mscrm.HideOnModern`, `Mscrm.ShowOnlyOnModern`.

**OOB command warning.** Both `set-rules` and `add-custom-rule` emit a
`meta.warnings` entry when `--command-id` is an out-of-the-box (`Mscrm.*`) command.
This is a warning, not a block — the write proceeds — but editing OOB commands is
unsupported ground and can break silently on a platform upgrade.

**`add-custom-rule` rule id.** The generated rule id (`data.rule_id`) follows the
pattern `{command_id}.{slug(function)}.EnableRule`. The rule is both defined in
`RuleDefinitions` and referenced on the command in the same write. To use the same
rule on other commands, pass the returned `rule_id` to `ribbon set-rules
--enable-rule`.

**`set-label` — `$LocLabels` directive ids are case-sensitive.** When `--lcid` is
given, the button attribute is set to a `$LocLabels:<id>` directive and the actual
text lands in a `<Title languagecode=LCID>` row. The directive id is derived
automatically (`{Button-Id}.{Attr}`) — if you hand-edit the RibbonDiffXml and
misspell the id's casing, the label silently falls back to the raw directive string
in the UI. `--lcid` is validated against the org's provisioned languages and errors
if not provisioned. Re-running for a second LCID adds a sibling `<Title>` (does not
overwrite).

**Button icons (`add-button` / `set-icon`) — reference forms differ; the web
resource must exist and match its slot type.** `--modern-image` (the Unified
Interface SVG icon) lands as the **bare web-resource name**; `--image16` /
`--image32` (the classic rasters) land as `$webresource:<name>` directives — the
CLI writes the correct form, so pass the plain name to every flag and never
pre-prefix. Beyond the group-wide "web resource must already exist" rule, each icon
resource must also **match its slot's type** — an SVG for `--modern-image`, a
PNG/JPG/GIF/ICO raster for `--image16`/`--image32`; a missing or wrong-type
resource errors up front, before the slow import round-trip. `set-icon` re-icons an
existing button in place (no recreate); `add-button` sets the icon while creating.

**`hide-button` — validate the target-id first.** `--target-id` is the OOB control Id
from `crm ribbon export ENTITY`. The command validates it against the live composed
ribbon before touching the solution, so a typo errors immediately rather than silently
completing a full import with no effect. If validation fails, re-export and find the
exact `Id=` attribute on the `<Button>` or `<FlyoutAnchor>` element.

**Two hide methods — choose by reversibility.**
`--method display-rule` (default) overrides the button's command with two always-false
platform DisplayRules. **Reversible** — delete the override to restore the button.
`--method hide-action` writes a `HideCustomAction`. **One-way trapdoor** — the button
cannot be restored without shipping a new solution version; the command therefore prompts
for confirmation, and `--yes` skips that prompt (required to run non-interactively, e.g.
under `--json`). Neither method touches the button's `classid`, `Command`, or
`TemplateAlias`. Both warn that hiding OOB commands is unsupported ground.
