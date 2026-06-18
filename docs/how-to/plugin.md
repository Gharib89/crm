# How-to: plugin

Register and manage Dynamics 365 plug-in assemblies, processing steps, and step
entity images via the Dataverse Web API (`pluginassemblies`, `plugintypes`,
`sdkmessageprocessingsteps`, `sdkmessageprocessingstepimages`).
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
- `--solution` / `--require-solution` land the step row in a target solution
  (sets `MSCRM.SolutionUniqueName`); defaults to the profile's
  `default_solution`. `--require-solution` (or `CRM_REQUIRE_SOLUTION`) fails
  when no solution resolves.

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

## Register a step image

Step entity images snapshot the record before (`pre`) or after (`post`) the
core operation; plug-in code reads them from `PreEntityImages` /
`PostEntityImages` under the alias:

```bash
crm --json plugin register-image \
    --step "Contoso.Plugins.AccountPostUpdate: Update of account" \
    --type pre \
    --alias preimg \
    --attributes name,telephone1
```

Key points:

- `--step` accepts the step GUID or its exact name (an ambiguous name errors —
  use the GUID).
- `--alias` is the key your plug-in uses to read the image; `--name` defaults
  to the alias.
- `--attributes` (comma-separated) limits the columns captured in the image.
  Omitting it captures **all** columns — a documented performance
  anti-pattern; always pass a list.
- `messagepropertyname` is derived from the step's message automatically
  (`Target` for Assign/Create/Delete/Merge/Route/Update, `EmailId` for
  DeliverIncoming/DeliverPromote, `EntityMoniker` for SetState). `Send` steps
  are ambiguous (`FaxId`, `EmailId`, or `TemplateId`) and require an explicit
  `--message-property-name`; messages outside that table do not support
  images and are rejected client-side.
- Platform validity rules are enforced before any write: no pre-image on a
  `Create` step, no post-image on a `Delete` step, and post-images require a
  step registered in the **PostOperation** stage.
- `--solution` / `--require-solution` land the image row in a target solution
  (same semantics as on `register-step` and `register-assembly`).

## Unregister a step image

```bash
crm --json plugin unregister-image preimg --yes
```

Resolves by image name or GUID; an ambiguous name errors — use the GUID.
Deleting a step cascades its images automatically, so this is only needed to
remove an image while keeping the step.

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

For `register-step`, dry-run resolves the objects the step names — the SDK
message, the plug-in type, and (when `--entity` is given) the message filter for
that entity — and reports each under `data.references[] = {kind, value,
_exists}`. A reference that does not resolve keeps the preview non-failing
(`ok: true`) and adds a `meta.warnings` advisory naming it, so a bad message,
unregistered type, or unsupported entity is caught before the real write 400s.
