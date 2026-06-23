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

## Register a plug-in type

A content-only `register-assembly` does **not** create `plugintypes` rows — that
only happens when the Plug-in Registration Tool reflects the assembly client-side.
Assemblies uploaded via the CLI have zero type rows until you register each one
explicitly:

```bash
crm --json plugin register-type \
    --assembly Contoso.Plugins \
    --type Contoso.Plugins.PreCreateAccount
```

`--friendly-name` defaults to the type name. `version`/`culture`/`publickeytoken`
are read-only (server-derived from the bound assembly) and are never sent in the
request body. After this, `list-types` shows the row and `register-step
--plugin-type <typename>` resolves it.

Dry-run preview (no write): `crm --dry-run --json plugin register-type ...`
returns `{_dry_run, would_create: true}`. The assembly name-to-id resolution GET
runs live even under `--dry-run` (reads-execute rule).

Unknown assembly name raises a `D365Error` (clean error, no server round-trip
for the write).

`--solution` / `--require-solution` land the type row in a target solution.

## List plug-in types

For assemblies registered via the CLI, the listing is empty until each type is
registered with `register-type` (no platform reflection):

```bash
crm --json plugin list-types
crm --json plugin list-types --assembly Contoso.Plugins
```

Returned columns: `typename`, `friendlyname`, `plugintypeid`. Filter to a single
assembly with `--assembly NAME`.

## Register a webhook

Webhooks are `serviceendpoint` rows (contract=8). The platform POSTs the JSON
execution context to the webhook URL whenever a registered step fires.

```bash
crm --json plugin register-webhook \
    --name MyWebhook \
    --url https://func.azurewebsites.net/api/d365hook \
    --auth webhookkey \
    --auth-value 'abc123secret'
```

Auth scheme choices: `webhookkey` (appends `?code=<auth-value>` — the Azure
Functions default), `httpheader` (passes the value as an HTTP header),
`httpquerystring` (passes it as a query-string parameter). The auth value is
**write-only**: the platform never returns it on subsequent reads.

After registering the webhook, bind a step to it with `--service-endpoint`
(see "Register a processing step" below).

Dry-run preview (no write): `crm --dry-run --json plugin register-webhook ...`
carries `meta.dry_run: true`.

`--solution` / `--require-solution` land the endpoint row in a target solution.

## Register a processing step

Bind to a **plug-in type** (`--plugin-type`) *or* a **service endpoint** such
as a webhook (`--service-endpoint`) — pass exactly one.

**Bind to a plug-in type:**

```bash
crm --json plugin register-step \
    --message Update \
    --plugin-type Contoso.Plugins.AccountPostUpdate \
    --entity account \
    --stage postoperation \
    --mode sync \
    --filtering-attributes name,telephone1 \
    --configuration '{"key": "value"}'
```

**Bind a step to a webhook (service endpoint):**

```bash
crm --json plugin register-step \
    --message Create \
    --service-endpoint MyWebhook \
    --entity account \
    --stage postoperation \
    --mode async \
    --async-auto-delete
```

Key points:

- Exactly one of `--plugin-type` or `--service-endpoint` must be given — they
  are mutually exclusive; omitting or providing both is a usage error.
- `--service-endpoint` matches by the webhook (or other service endpoint) name
  given to `register-webhook`. The step is bound via the
  `eventhandler_serviceendpoint` navigation property.
- `--stage` choices: `prevalidation` (10), `preoperation` (20),
  `postoperation` (40). Default: `postoperation`.
- `--mode` choices: `sync` (0), `async` (1). Default: `sync`. Async mode
  requires `--stage postoperation` — other combinations are rejected.
- `--async-auto-delete` configures an async step to delete its system job upon success.
- `--configuration` stores an unsecure configuration string on the step, passed to the plug-in constructor.
- `--entity` sets the `primaryobjecttypecode`. Omit it to fire on all entities.
- `--filtering-attributes` (comma-separated) restricts an Update step to
  specific columns; ignored for non-Update messages.
- Step name is auto-derived as `<handler>: <message> of <entity>` (or
  `<handler>: <message> of any entity` when `--entity` is omitted), where
  `<handler>` is the plug-in typename or service endpoint name. Pass `--name`
  explicitly when the derived string would exceed the platform's 256-character
  limit.
- `--assembly` scopes the type lookup to a single assembly when multiple
  assemblies share a type name (relevant only with `--plugin-type`).
- `--solution` / `--require-solution` land the step row in a target solution
  (sets `MSCRM.SolutionUniqueName`); defaults to the profile's
  `default_solution`. `--require-solution` (or `CRM_REQUIRE_SOLUTION`) fails
  when no solution resolves.

## Full registration workflow

### Plug-in assembly → step

```bash
# 1. Upload the assembly
crm --json plugin register-assembly ./bin/Contoso.Plugins.dll --solution cwx_contoso

# 2. Register each IPlugin class explicitly — the CLI does not reflect the
#    assembly, so plugintype rows are NOT created automatically.
crm --json plugin register-type \
    --assembly Contoso.Plugins \
    --type Contoso.Plugins.AccountPostUpdate \
    --solution cwx_contoso

# 3. Register a post-operation sync step on account Update
crm --json plugin register-step \
    --message Update \
    --plugin-type Contoso.Plugins.AccountPostUpdate \
    --entity account \
    --stage postoperation \
    --mode sync \
    --filtering-attributes name,telephone1 \
    --configuration '{"key": "value"}'
```

### Webhook → step

```bash
# 1. Register the webhook endpoint
crm --json plugin register-webhook \
    --name MyWebhook \
    --url https://func.azurewebsites.net/api/d365hook \
    --auth webhookkey \
    --auth-value 'abc123secret' \
    --solution cwx_contoso

# 2. Bind an async step on account Create to the webhook
crm --json plugin register-step \
    --message Create \
    --service-endpoint MyWebhook \
    --entity account \
    --stage postoperation \
    --mode async \
    --async-auto-delete \
    --solution cwx_contoso
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
  `Create` step, no post-image on a `Delete` step, and post-images (or `both`) require a
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

## Set step state

```bash
crm --json plugin set-step-state "Contoso.Plugins.AccountPostUpdate: Update of account" --disable
crm --json plugin set-step-state "Contoso.Plugins.AccountPostUpdate: Update of account" --enable
```

Resolves by step name or GUID; an ambiguous name errors — use the GUID.

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
crm --dry-run --json plugin register-type \
    --assembly Contoso.Plugins --type Contoso.Plugins.AccountPostUpdate
crm --dry-run --json plugin register-webhook \
    --name MyWebhook --url https://func.azurewebsites.net/api/d365hook \
    --auth webhookkey --auth-value 'abc123secret'
crm --dry-run --json plugin register-step \
    --message Create --plugin-type Contoso.Plugins.AccountPreCreate --entity account
crm --dry-run --json plugin register-step \
    --message Create --service-endpoint MyWebhook --entity account
```

Resolution GETs (e.g. assembly lookup under `--update`) fire for real; all
writes are skipped. The `--json` envelope carries `meta.dry_run: true`.

For `register-step`, dry-run resolves the objects the step names — the SDK
message, the plug-in type or service endpoint, and (when `--entity` is given)
the message filter for that entity — and reports each under
`data.references[] = {kind, value, _exists}`. A reference that does not resolve
keeps the preview non-failing (`ok: true`) and adds a `meta.warnings` advisory
naming it, so a bad message, unregistered type/endpoint, or unsupported entity
is caught before the real write 400s.
