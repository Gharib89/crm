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

## Hide an out-of-box button

Use `hide-button` to suppress an OOB command-bar button you cannot delete. Two
methods are available; choose based on reversibility:

| Method | Reversibility | Gate |
|---|---|---|
| `display-rule` (default) | Reversible — delete the override to restore | none |
| `hide-action` | **One-way trapdoor** — removable only by a new solution version | `--yes` required |

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
without shipping a new solution version, so the command requires explicit `--yes`
confirmation.

Neither method touches the button's `classid`, `Command`, or `TemplateAlias`.
Both emit a warning that overriding or hiding an OOB command is on unsupported
ground and may change across platform updates.
