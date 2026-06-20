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
