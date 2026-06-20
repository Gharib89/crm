# How-to: theme

Author application themes (product branding) headlessly — list, get, create,
update, and publish `theme` records without the theme editor. A theme carries
the navigation-bar / header / link colors and a logo; `publish` promotes one to
the **active org-wide theme** via the `PublishTheme` action. See the
[CLI reference](../reference/cli.md) for every flag.

!!! note "Themes are not solution-aware"
    A theme is **not** a solution component — it does not travel with a solution
    export, so there is no `--solution` flag on this group and you should not
    expect a theme to appear in a packaged solution or move across orgs with one.
    Move branding between orgs by re-running `theme create` / `theme update`.

## List themes

```bash
crm theme list
```

Org-wide; output columns are `name`, `themeid`, `type` (custom vs system), and
`isdefaulttheme`. `list` returns these summary columns only — use `theme get`
for a theme's colors.

## Get a single theme

```bash
crm theme get 1111aaaa-2222-bbbb-3333-cccccccccccc
```

Returns the full branding record (every color column) in the `--json` envelope.

## Create a theme

Set branding columns with repeatable `--set FIELD=VALUE` (the VALUE parses as
JSON with a raw-string fallback, so a `#rrggbb` color is taken verbatim):

```bash
crm theme create \
    --name "Corporate Blue" \
    --set maincolor=#0066cc \
    --set navbarbackgroundcolor=#002050 \
    --set headercolor=#ffffff
```

Common color columns: `maincolor`, `navbarbackgroundcolor`, `navbarshelfcolor`,
`headercolor`, `globallinkcolor`, `selectedlinkeffect`, `hoverlinkeffect`,
`processcontrolcolor`, `defaultentitycolor`, `defaultcustomentitycolor`,
`controlshade`, `pageheaderbackgroundcolor`, `panelheaderbackgroundcolor`.

### Set a logo

`--logo` takes a web resource name or GUID and binds it as the theme logo
(create the web resource first with `webresource create`):

```bash
crm theme create --name "Corporate Blue" --logo cwx_/icons/logo.png
```

## Update a theme

Change the name, any branding column, and/or the logo. At least one of `--name`,
`--set`, or `--logo` is required:

```bash
crm theme update 1111aaaa-2222-bbbb-3333-cccccccccccc --set maincolor=#ff0000
```

## Publish a theme (make it active)

```bash
crm theme publish 1111aaaa-2222-bbbb-3333-cccccccccccc
```

This sets the theme as the **active org-wide theme** for all users. There is no
inverse verb, so capture the current default first — the `theme list` row with
`isdefaulttheme: true` — and re-`publish` that id to roll back.

## Preview without writing

`create`, `update`, and `publish` honor the global `--dry-run` flag (a `--logo`
name is resolved live first, but no write is issued):

```bash
crm --dry-run theme create --name "Q" --set maincolor=#fff
```

The `--json` envelope carries `meta.dry_run: true` and a
`would_create` / `would_update` / `would_publish` preview of the request.

## Delete a theme

There is no `theme delete` verb — drop a theme with the generic record delete:

```bash
crm entity delete themes 1111aaaa-2222-bbbb-3333-cccccccccccc --yes
```
