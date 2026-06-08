# Manage entity ribbon buttons

`crm ribbon` reads and edits an entity's command-bar (ribbon) buttons.

## Export the current ribbon

```bash
crm ribbon export cwx_ticket            # readable XML to stdout
crm ribbon export cwx_ticket --output ribbon.xml
```

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
