# How-to: webresource

Create and manage web resources (HTML/JS/CSS/images) and set them as model-driven app icons. See the
[CLI reference](../reference/cli.md) for every flag.

## Create a web resource (type inferred from extension)

```bash
crm --json webresource create --name cwx_/scripts/ribbon.js --file ./ribbon.js --solution cwx_crmworx
```
The file's bytes from `--file` are base64-encoded into the `content` column. The D365 `webresourcetype` is inferred from the file extension (`.js` → 3 / JScript above), so you don't pass a type for a known extension. `--display-name` defaults to the `--name` value when omitted, and `--solution` sends the `MSCRM.SolutionUniqueName` header so the resource lands in that solution. `create` **stages** by default (no publish) — pass `--publish` to publish it immediately.

## Supported file types

The extension → `webresourcetype` map (the D365 `webresource_webresourcetype` option set):

| Extension | Type | Label |
|---|---|---|
| `.htm`, `.html` | 1 | Webpage (HTML) |
| `.css` | 2 | Style Sheet (CSS) |
| `.js` | 3 | Script (JScript) |
| `.xml` | 4 | Data (XML) |
| `.png` | 5 | PNG |
| `.jpg`, `.jpeg` | 6 | JPG |
| `.gif` | 7 | GIF |
| `.xap` | 8 | Silverlight (XAP) |
| `.xsl`, `.xslt` | 9 | Style Sheet (XSL) |
| `.ico` | 10 | ICO |
| `.svg` | 11 | Vector format (SVG) |
| `.resx` | 12 | String (RESX) |

Pass `--type <int>` to override the inferred type — needed for an extensionless or unusual file. An unknown extension with no `--type` is rejected with an error asking for an explicit type.

## Update content or display name

```bash
crm --json webresource update cwx_/scripts/ribbon.js --file ./ribbon.js
```
`update` resolves the web resource by its name, then issues a plain PATCH of only the fields you pass — the content from `--file` and/or `--display-name` — not a retrieve-merge-write. At least one of `--file` / `--display-name` is required. `--solution` and the publish-after-write behavior match `create`.

## Inspect web resources

```bash
crm --json webresource get cwx_/scripts/ribbon.js
crm --json webresource list --custom-only
```
`get` resolves a web resource by name and prints its record. `list` returns a table in human mode (full data under `--json`); `--custom-only` keeps only unmanaged resources, and `--top N` limits the rows.

## Use a web resource as an app icon

```bash
crm --json webresource create --name cwx_/icons/app.svg --file ./app.svg
crm --json app create --name CRMWorx --unique-name cwx_crmworx --icon-webresource cwx_/icons/app.svg
```
`app create --icon-webresource <name|guid>` uses that web resource as the app icon. A GUID is used directly; a name is resolved to its id. Omit the flag to keep the platform default icon.

## Bulk push a directory — `push`

`push` walks a local directory and upserts every file as a web resource in one run:

```bash
crm --json webresource push ./webresources --prefix cwx
crm --json webresource push ./webresources --prefix cwx --solution cwx_crmworx
```

### Naming convention

Each file's web resource name is derived deterministically:

```
<prefix>_<relpath>
```

where `<relpath>` is the file's path relative to the `DIRECTORY` argument, with `/` separators regardless of OS. A file at `webresources/scripts/ribbon.js` pushed with `--prefix cwx` becomes `cwx_scripts/ribbon.js`. The prefix must be 2–8 alphanumeric characters, start with a letter, and not start with `mscrm` (reserved); an invalid prefix is rejected as a usage error before any call.

### Upsert + skip + optional publish-once

For each file the command:

1. Fetches the existing web resource by name (a live GET — always runs, even under `--dry-run`).
2. **Creates** it when it doesn't exist.
3. **Updates** it (PATCH of `content`) when the base64 content has changed.
4. **Skips** it (no write) when the stored content is byte-identical — cheap no-op.

`push` **stages** by default (no publish), same as `create`/`update`. Pass
`--publish` to publish once at the end — a single `PublishAllXml` fires **only
when `--publish` is passed and** at least one file was created or updated
(skipping all files skips the publish too). Without `--publish`, run
`crm solution publish-all` afterward once your batch of pushes is done.

### Partial-failure behavior

A failing file does not abort the run. Errors are collected in `failed` and reported at the end. The files that succeeded are still committed (and published, if `--publish` was passed). **Exit code is 1 if any file failed, 0 if all succeeded.**

### Dry-run preview

The global `--dry-run` flag runs all the live GETs but issues no writes:

```bash
crm --json --dry-run webresource push ./webresources --prefix cwx
```

Output shows `would_create` (names that would be created), `would_update` (names whose content changed), and `skipped` (byte-identical), so you can audit the changeset before committing.

### JSON envelope

Real run (with `--publish`; omit it and `published` reports `false` instead):

```json
{
  "ok": true,
  "data": {
    "pushed": 3,
    "updated": 1,
    "skipped": 2,
    "published": true,
    "failed": [],
    "files": [
      {"name": "cwx_scripts/ribbon.js", "action": "created"},
      {"name": "cwx_scripts/form.js",   "action": "updated"},
      {"name": "cwx_styles/main.css",   "action": "skipped"}
    ]
  }
}
```

Dry-run:

```json
{
  "ok": true,
  "data": {
    "_dry_run": true,
    "would_create": ["cwx_scripts/ribbon.js"],
    "would_update": ["cwx_scripts/form.js"],
    "skipped": 2,
    "published": false,
    "failed": [],
    "files": [...]
  }
}
```

### Continuous redeploy loop

`push` has no `--watch` flag. Wire it to `entr` or `watchexec` for a live-reload loop during front-end development:

```bash
# re-push whenever any JS/CSS/HTML file changes
# (find is portable; bash ** globstar is off by default, so `ls **/*` can silently match nothing)
find webresources -type f \( -name '*.js' -o -name '*.css' -o -name '*.html' \) | \
  entr crm webresource push webresources --prefix cwx

# watchexec equivalent (no glob expansion needed)
watchexec -e js,css,html -- crm webresource push webresources --prefix cwx
```

## Delete a web resource

```bash
crm webresource delete cwx_/scripts/ribbon.js --yes
```

`delete` resolves a unique name or a GUID to the record id (a GUID passes through with no lookup) and deletes the web resource. The `--yes` flag skips the interactive confirmation prompt; omit it on a TTY to confirm interactively. No publish step is needed after a delete.

Pass `--check-dependencies` to preview blocking dependencies before the delete — it calls `RetrieveDependenciesForDelete` and folds the result into the output as `can_delete` and `blockers`. This is informational only; it does not block or skip the actual delete:

```bash
crm --json webresource delete cwx_/scripts/ribbon.js --check-dependencies --yes
```

Pass the global `--dry-run` to see what would be deleted without sending the DELETE request.

**Web resource referenced by a ribbon button** — the server returns `0x8004f01f` and the command surfaces it as a clear error. Remove the ribbon button first (`crm ribbon remove …`), then retry the delete. Use `--check-dependencies` to identify blockers up front before attempting the delete.
