# How-to: webresource

Create and manage web resources (HTML/JS/CSS/images) and set them as model-driven app icons. See the
[CLI reference](../reference/cli.md) for every flag.

## Create a web resource (type inferred from extension)

```bash
crm --json webresource create --name cwx_/scripts/ribbon.js --file ./ribbon.js --solution cwx_crmworx
```
The file's bytes from `--file` are base64-encoded into the `content` column. The D365 `webresourcetype` is inferred from the file extension (`.js` → 3 / JScript above), so you don't pass a type for a known extension. `--display-name` defaults to the `--name` value when omitted, and `--solution` sends the `MSCRM.SolutionUniqueName` header so the resource lands in that solution. `create` publishes by default — pass `--no-publish` (or the global `--stage-only`) to suppress the publish.

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
