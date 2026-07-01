# Manage entity ribbon buttons

`crm ribbon` reads and edits an entity's command-bar (ribbon) buttons.

## Export the current ribbon

Export a single entity's composed ribbon, or the application-wide ribbon (commands
not bound to any specific table):

```bash
crm ribbon export cwx_ticket            # one table's ribbon — readable XML to stdout
crm ribbon export cwx_ticket --output ribbon.xml
crm ribbon export --application         # application-wide ribbon to stdout
crm ribbon export --application --output app_ribbon.xml
```

Pass `ENTITY` for one table's ribbon, or `--application` / `-a` for the app-wide
ribbon. Passing both, or neither, is an error. Read-only.

## List custom buttons

```bash
crm ribbon list cwx_ticket --solution MySolution
```

## Add a JavaScript button

```bash
crm ribbon add-button cwx_ticket --solution MySolution \
    --label Validate --location form \
    --webresource cwx_/scripts/x.js --function ns.fn --param PrimaryControl
```

`--location` is `form`, `homegrid`, or `subgrid`. Override the target group with
`--group <id>`. The web resource must already exist in the org/solution.

## Remove a button

```bash
crm ribbon remove cwx_ticket --solution MySolution \
    --button-id cwx_ticket.form.Validate.CustomAction --yes
```

## Set a button's label and tooltips

Use `set-label` to change the display text of a **custom** command-bar button —
its LabelText, ToolTipTitle, and ToolTipDescription — without touching the button's
Command, TemplateAlias, Sequence, or Id. Pass at least one of `--label`,
`--tooltip-title`, or `--tooltip-description`.

```bash
# Set label and tooltip inline (text is XML-escaped automatically)
crm ribbon set-label cwx_ticket \
    --solution MySolution \
    --button-id cwx_ticket.form.Validate.CustomAction \
    --label "Validate Ticket" \
    --tooltip-title "Validate" \
    --tooltip-description "Runs the validation rules for this ticket."

# Preview the change without importing
crm --dry-run ribbon set-label cwx_ticket \
    --solution MySolution \
    --button-id cwx_ticket.form.Validate.CustomAction \
    --label "Validate Ticket"

# Localize for a specific language (e.g. Arabic, LCID 1025)
crm ribbon set-label cwx_ticket \
    --solution MySolution \
    --button-id cwx_ticket.form.Validate.CustomAction \
    --label "التحقق من التذكرة" \
    --lcid 1025
```

`--button-id` is the CustomAction Id as reported by `crm ribbon list`. Like
every other mutating ribbon verb, `set-label` requires `--solution` — there is
no profile default and no opt-out; pass `--solution Default` for a deliberate
Default-Solution-only write.

**Localization with `--lcid`.** When `--lcid <LCID>` is given, the button
attribute is set to a `$LocLabels:<id>` directive and the actual text lands in a
`<LocLabel>/<Titles>/<Title languagecode=LCID>` row inside the RibbonDiffXml. The
LCID is validated against the org's provisioned languages and the command errors if
the language is not provisioned. Re-running for a second LCID adds a sibling
`<Title>` row rather than overwriting the first. The `$LocLabels` directive id is
**case-sensitive** — it is derived automatically from the button Id and attribute
name; do not hand-edit the directive.

**Only custom buttons.** `set-label` locates the button by its CustomAction Id.
Out-of-box buttons have no CustomAction entry and will not be found; use
`hide-button` to suppress OOB buttons instead.

## Hide an out-of-box button

Use `hide-button` to suppress an OOB command-bar button you cannot delete. Two
methods are available; choose based on reversibility:

| Method | Reversibility | Gate |
|---|---|---|
| `display-rule` (default) | Reversible — delete the override to restore | none |
| `hide-action` | **One-way trapdoor** — removable only by a new solution version | confirm prompt (`--yes` to skip) |

`--target-id` is the OOB button (control) Id as shown by `crm ribbon export ENTITY`.
The command validates it against the live composed ribbon — a typo errors immediately
instead of silently doing nothing after a slow solution import round-trip.

```bash
# Reversible: override the OOB command with two always-false DisplayRules
crm ribbon hide-button account --solution MySolution \
    --target-id Mscrm.Form.account.Save

# Irreversible: write a HideCustomAction (gated behind --yes)
crm ribbon hide-button account --solution MySolution \
    --target-id Mscrm.Form.account.Save \
    --method hide-action --yes
```

`--method display-rule` overrides the button's command with the platform's own
`Mscrm.HideOnModern` and `Mscrm.ShowOnlyOnModern` DisplayRules (both always false).
The original command definition is unchanged; remove the override to undo.

`--method hide-action` writes a `HideCustomAction` element. This is **irreversible**
without shipping a new solution version, so the command asks for confirmation first;
pass `--yes` to skip the prompt (required for `--json` / non-interactive runs).

Neither method touches the button's `classid`, `Command`, or `TemplateAlias`.
Both emit a warning that overriding or hiding an OOB command is on unsupported
ground and may change across platform updates.

## Set enable/display rules

Replace the enable or display rule references on an existing custom
`CommandDefinition` with an exact, ordered set. At least one `--enable-rule` or
`--display-rule` is required; a category with no flags is left untouched.

```bash
# Show the command only when exactly one row is selected (platform rule)
crm ribbon set-rules cwx_ticket --solution MySolution \
    --command-id cwx_ticket.form.Validate.Command \
    --enable-rule Mscrm.SelectionCountExactlyOne

# Combine a platform rule with a custom one; stage without publishing
# (writes publish by default — pass --no-publish to batch several edits first)
crm ribbon set-rules cwx_ticket --solution MySolution \
    --command-id cwx_ticket.form.Validate.Command \
    --enable-rule Mscrm.SelectionCountExactlyOne \
    --enable-rule cwx_ticket.form.Validate.myCustomRule.EnableRule \
    --no-publish

# Suppress the button on modern UI only (display rule)
crm ribbon set-rules cwx_ticket --solution MySolution \
    --command-id cwx_ticket.form.Validate.Command \
    --display-rule Mscrm.HideOnModern
```

Platform (`Mscrm.*`) rule ids are validated against a curated allow-list — the
server silently ignores an unknown `Mscrm.*` id, so passing one that isn't
recognised is rejected with a clear error. Allowed platform enable rules:
`Mscrm.SelectionCountExactlyOne`, `Mscrm.ShowOnGrid`, `Mscrm.ShowOnQuickAction`,
`Mscrm.ShowOnGridAndQuickAction`. Allowed platform display rules:
`Mscrm.HideOnModern`, `Mscrm.ShowOnlyOnModern`. Custom (non-`Mscrm.`) ids pass
through. The `CommandDefinition` `Id` is never touched.

Use `ribbon list` to find command ids; the command warns when `--command-id`
matches an out-of-the-box (`Mscrm.*`) command (editing OOB commands is
unsupported and may break on upgrade).

## Add a custom (JavaScript) rule

Define a custom `EnableRule` that calls a JavaScript function in a web resource,
and attach it to a command in one step. The web resource must already exist (see
`crm webresource create`). The generated rule id is
`{command_id}.{slug(function)}.EnableRule`.

```bash
# Create a custom enable rule that calls cwx_.ns.canValidate() and wire it
crm ribbon add-custom-rule cwx_ticket --solution MySolution \
    --command-id cwx_ticket.form.Validate.Command \
    --webresource cwx_/scripts/ribbon.js \
    --function ns.canValidate

# Dry-run: preview without writing
crm --dry-run ribbon add-custom-rule cwx_ticket --solution MySolution \
    --command-id cwx_ticket.form.Validate.Command \
    --webresource cwx_/scripts/ribbon.js \
    --function ns.canValidate
```

The command prints the generated `rule_id` in `data.rule_id` — capture it to
pass to `ribbon set-rules --enable-rule` in a subsequent step, or chain them in
sequence since `add-custom-rule` also references the rule on the command
automatically. The `CommandDefinition` `Id` is never touched.
