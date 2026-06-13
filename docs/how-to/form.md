# How-to: form

List, clone, and export entity main forms (systemform). See the
[CLI reference](../reference/cli.md) for every flag.

## List the main forms for an entity

```bash
crm form list cwx_ticket
```

Output columns: `name`, `type`, `formid`, `isdefault`. Only **main** forms are
returned — quick-create, card, and mobile-express forms are not included.

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

## Export a form's formxml

```bash
crm form export cwx_ticket "Ticket Main Form"      # prints formxml to stdout
crm form export cwx_ticket "Ticket Main Form" --output ticket_main.xml
```

`export` is read-only and does not require `--solution`. The exported XML can
be inspected directly or used as a reference when authoring customizations.
`--output` writes the XML to the given path; omitting it prints to stdout,
which is convenient for piping or quick inspection.
