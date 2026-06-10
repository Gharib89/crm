# How-to: translation

Export and re-import localizable display labels (entities, attributes, forms,
views, charts, dashboards, global option sets, entity messages, relationships)
for a solution. See the [CLI reference](../reference/cli.md) for every flag.

Translation is **solution-scoped**: the Web API `ExportTranslation` /
`ImportTranslation` actions take a solution, not an entity. To translate one
entity's labels, put that entity in a solution and export that solution's
translations. Both verbs work against on-prem v9.x and Dataverse online.

## The round-trip

```bash
# 1. Export all localizable labels for a solution
crm --json translation export --solution CRMWorx -o labels.zip

# 2. Hand labels.zip to the translator: it contains CrmTranslations.xml
#    (an Office-Excel-openable spreadsheet) + [Content_Types].xml.
#    Translators add a column per language code (e.g. 1034) and fill it in.

# 3. Zip the edited files back up, then import
crm --json translation import labels.zip --yes

# 4. Publish — imported labels do NOT surface until published
crm --json solution publish-all
```

`translation import` reports the `import_job_id`; per-component results are
retrievable from the import job (`crm solution import-result <id>`).

## Gotchas

- **Publish after import.** Imported labels stay invisible until you publish.
  The import envelope carries a `meta.warnings` reminder.
- **500-character limit.** The import fails if any translated string is longer
  than 500 characters.
- **Languages must be provisioned.** Install/provision the language packs on
  the target org first; labels for languages not enabled on the target are
  discarded with a warning when a solution carrying them is imported.
- **Customize in the base language.** Customization happens only in the base
  language; the export/translate/import loop is how every other language gets
  its labels.
- **Import takes the zip, not the XML.** `translation import` validates the
  file is a zip before any HTTP call — re-zip the edited
  `CrmTranslations.xml` + `[Content_Types].xml`, don't pass the bare XML.

## Alternatives

- **Native UI**: Power Apps / D365 solution view → **Translations → Export
  translations / Import translations** — the same actions these verbs wrap.
- **XrmToolBox [Easy Translator](https://www.xrmtoolbox.com/plugins/MsCrmTools.Translator/)**:
  SDK-based GUI with contextual information, works against on-prem and online.
  Community tool, not Microsoft-supported.
- Avoid `crm action invoke ExportTranslation`: the generic escape hatch returns
  the zip as a **base64 blob inside the JSON response body**, which you would
  have to decode and unpack yourself — that plumbing is exactly what
  `crm translation export` does for you.
