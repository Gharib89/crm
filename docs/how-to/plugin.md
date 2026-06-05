# How-to: plugin

Register and manage Dynamics 365 plug-in assemblies and processing steps via the
Dataverse Web API (`pluginassemblies`, `plugintypes`, `sdkmessageprocessingsteps`).
See the [CLI reference](../reference/cli.md) for every flag.

## Register an assembly

```bash
crm --json plugin register-assembly ./bin/Contoso.Plugins.dll \
    --solution cwx_contoso
```

The `.dll` bytes are base64-encoded and written to the `content` column of
`pluginassemblies`. `--name` defaults to the filename stem (`Contoso.Plugins`);
`--version` defaults to `1.0.0.0`; `--isolation-mode` defaults to `sandbox`
(isolation mode 2). Pass `--isolation-mode none` for full-trust assemblies.

## Re-upload assembly content after a rebuild

```bash
crm --json plugin register-assembly ./bin/Contoso.Plugins.dll --update
```

`--update` resolves the existing assembly by name and patches only the `content`
column — it does not touch `name`, `version`, `culture`, or `publickeytoken`.
Identity flags (`--version`, `--culture`, `--public-key-token`, `--description`,
`--isolation-mode`) are ignored under `--update` and produce a warning if passed.
`--solution` is still honored.

## List plug-in types

After the platform processes a registered assembly it generates one `plugintypes`
row per public class that implements `IPlugin`:

```bash
crm --json plugin list-types
crm --json plugin list-types --assembly Contoso.Plugins
```

Returned columns: `typename`, `friendlyname`, `plugintypeid`. Filter to a single
assembly with `--assembly NAME`.

## Register a processing step

```bash
crm --json plugin register-step \
    --message Update \
    --plugin-type Contoso.Plugins.AccountPostUpdate \
    --entity account \
    --stage postoperation \
    --mode sync \
    --filtering-attributes name,telephone1
```

Key points:

- `--message` and `--plugin-type` are required.
- `--stage` choices: `prevalidation` (10), `preoperation` (20),
  `postoperation` (40). Default: `postoperation`.
- `--mode` choices: `sync` (0), `async` (1). Default: `sync`. Async mode
  requires `--stage postoperation` — other combinations are rejected.
- `--entity` sets the `primaryobjecttypecode`. Omit it to fire on all entities.
- `--filtering-attributes` (comma-separated) restricts an Update step to
  specific columns; ignored for non-Update messages.
- Step name is auto-derived as `<typename>: <message> of <entity>` (or
  `<typename>: <message> of any entity` when `--entity` is omitted). Pass
  `--name` explicitly when the derived string would exceed the platform's
  256-character limit.
- `--assembly` scopes the type lookup to a single assembly when multiple
  assemblies share a type name.

## Full registration workflow

```bash
# 1. Upload the assembly
crm --json plugin register-assembly ./bin/Contoso.Plugins.dll --solution cwx_contoso

# 2. Confirm the platform generated plug-in types
crm --json plugin list-types --assembly Contoso.Plugins

# 3. Register a post-operation sync step on account Update
crm --json plugin register-step \
    --message Update \
    --plugin-type Contoso.Plugins.AccountPostUpdate \
    --entity account \
    --stage postoperation \
    --mode sync \
    --filtering-attributes name,telephone1
```

## Unregister a step

```bash
crm --json plugin unregister-step "Contoso.Plugins.AccountPostUpdate: Update of account" --yes
```

Resolves by step name or GUID. An ambiguous name (multiple steps sharing the same
name) errors — use the GUID instead. `--yes` skips the interactive confirmation.

## Unregister an assembly (cascading)

```bash
crm --json plugin unregister-assembly Contoso.Plugins --yes
```

Deletes all dependent steps first, then removes the assembly. Pass `--yes` to
skip confirmation. Accepts either the assembly name or its GUID.

## Dry-run preview

```bash
crm --dry-run --json plugin register-assembly ./bin/Contoso.Plugins.dll --solution cwx_contoso
crm --dry-run --json plugin register-step \
    --message Create --plugin-type Contoso.Plugins.AccountPreCreate --entity account
```

Resolution GETs (e.g. assembly lookup under `--update`) fire for real; all
writes are skipped. The `--json` envelope carries `meta.dry_run: true`.
