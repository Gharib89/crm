# Automation — plug-in registration and trace logs

Register plug-in assemblies, types, webhooks, steps and images, and debug a
plug-in through its trace logs. Group: `plugin`. Flags/choices: `crm plugin
--help`. Deploying a plug-in package and generating early-bound classes:
`reference/automation-plugin-dev.md`. Workflows and SLAs:
`reference/automation-workflows.md`.

## Plug-ins — `plugin` (assembly + step + image lifecycle)

The full workflow is **upload assembly → register-type (one per IPlugin class;
no reflection, you name them) → register a step** against one of those types,
then optionally attach entity images to the step:

`register-assembly`, `register-type`, `register-webhook`, `register-step`, and
`register-image` are all solution-scoped (`--solution` required — SKILL.md).

```bash
# register-assembly: .dll bytes are base64'd into `content`. --solution sends
# MSCRM.SolutionUniqueName.
crm --json plugin register-assembly ./bin/Contoso.Plugins.dll --solution ContosoCore
# `name` in the output is the name the org stored (the DLL's own name can override
# --name); `name_differs: true` plus a warning flags it: look up by that `name`.

# --update: re-uploads content of an existing assembly (resolved by name); the
# identity flags --version/--culture/--public-key-token/--description/--isolation-mode
# are IGNORED under --update and produce a warning. --solution is still required.
crm --json plugin register-assembly ./bin/Contoso.Plugins.dll --update --solution ContosoCore

# register-type: create one plugintypes row per IPlugin class. The CLI does NOT
# reflect the assembly: plugintype rows are never auto-created via the Web API
# on this route (a package pushed with `pac plugin push` does create them: see
# "Deploy a plug-in package" in reference/automation-plugin-dev.md).
# For a workflow activity (isworkflowactivity: true), the type's `name` column
# is what the CLASSIC workflow designer shows as the Add-Step label — a blank
# name there is the empty-label bug this default exists to avoid.
crm --json plugin register-type --assembly Contoso.Plugins \
    --type Contoso.Plugins.AccountPostUpdate --solution ContosoCore

# list-types: shows explicitly registered rows; empty until register-type is run
# (a pushed package's types show at once).
crm --json plugin list-types --assembly Contoso.Plugins

# register-webhook: creates a serviceendpoint (contract=8). The platform POSTs
# the JSON execution context to --url; the webhookkey auth mode delivers the
# secret by appending ?code=<auth-value> to that URL.
crm --json plugin register-webhook \
    --name MyWebhook \
    --url https://func.azurewebsites.net/api/d365hook \
    --auth webhookkey --auth-value 'abc123secret' \
    --solution ContosoCore

# register-step: the polymorphic event handler binds via plugintypeid
# (--plugin-type) or eventhandler_serviceendpoint by endpoint NAME
# (--service-endpoint, e.g. a webhook from register-webhook). The step name is
# auto-derived as '<handler>: <message> of <entity>' — when register-image's
# --step is given a name rather than a GUID, it must match this derived string
# exactly. --secure-configuration lands in a
# separate related record; a --dry-run preview echoes the request body INCLUDING
# the secret, so treat dry-run output as sensitive.
crm --json plugin register-step \
    --message Update \
    --plugin-type Contoso.Plugins.AccountPostUpdate \
    --entity account --stage postoperation --mode sync \
    --filtering-attributes name,telephone1 --solution ContosoCore

# Bind a step to a webhook instead of a plug-in type:
crm --json plugin register-step \
    --message Create --service-endpoint MyWebhook \
    --entity account --stage postoperation --mode async --solution ContosoCore
```

```bash
# register-image: --step takes the step GUID or exact name. messagepropertyname
# is derived from the step's message (Send is ambiguous — pass
# --message-property-name FaxId|EmailId|TemplateId). Always pass --attributes:
# omitting it snapshots ALL columns (documented performance anti-pattern).
# Rejected client-side: pre-image on Create, post-image on Delete, post-image
# on a non-PostOperation step, messages that don't support images. A "both"
# image counts as pre AND post for these checks — none of the four rules is
# bypassed by choosing "both".
crm --json plugin register-image \
    --step "Contoso.Plugins.AccountPostUpdate: Update of account" \
    --type pre --alias preimg --attributes name,telephone1 --solution ContosoCore
```

```bash
# unregister-image: by name or GUID; only needed to remove an image while
# keeping the step — deleting a step cascades its images.
crm --json plugin unregister-image preimg --yes

# unregister-step: by name or GUID; an ambiguous name errors (use the GUID).
crm --json plugin unregister-step "Contoso.Plugins.AccountPostUpdate: Update of account" --yes

# set-step-state: disable/enable a step (toggles statecode) — a reversible
# rollback; prefer over unregister-step when you may re-enable. Pass exactly
# one of --disable / --enable.
crm --json plugin set-step-state "Contoso.Plugins.AccountPostUpdate: Update of account" --disable

# unregister-assembly: cascades — deletes dependent steps first, then the assembly.
crm --json plugin unregister-assembly Contoso.Plugins --yes
```

`--dry-run` skips all writes (resolution GETs still fire); the `--json` envelope
carries `meta.dry_run: true`. On `register-step` the preview also resolves the
objects the step names — the SDK message, the plug-in type or service endpoint,
and (entity-scoped) the message filter for that entity — and reports each under
`data.references[] = {kind, value, _exists}`. A reference that does not resolve
stays a non-failing preview (`ok: true`) and adds a `meta.warnings` advisory
naming it, so a dangling name is caught before the real write 400s.
`set-step-state --dry-run` previews as `{_dry_run, would_set_state:{statecode,
statuscode}, sdkmessageprocessingstepid}` — never a bare `updated: true`.

### Debugging a plug-in via trace logs

No dedicated trace verb — the loop runs through `entity update` (the org switch)
and `query odata plugintracelogs` (the reader). `plugintracelogsetting` is a
picklist on the `organizations` entity: `0` = Off (default), `1` = Exception
(traces only on failure), `2` = All.

```bash
# 1. Enable. The single organizations row carries the switch.
orgid=$(crm --json query odata organizations --select organizationid \
        | jq -r '.data[0].organizationid')
crm --json entity update organizations "$orgid" --data '{"plugintracelogsetting": 2}'

# 2. Reproduce the plug-in run, then read its traces. typename starts with the
# plug-in class (it is assembly-qualified); messageblock holds the ITracingService output. messagename/createdon
# help when one class fires on several messages.
crm --json query odata plugintracelogs \
    --filter "startswith(typename,'Contoso.Plugins.AccountPostUpdate')" \
    --select typename,messagename,createdon,messageblock

# 3. Disable when done — trace rows consume org storage.
crm --json entity update organizations "$orgid" --data '{"plugintracelogsetting": 0}'
```

Gotchas (server-side, not in `--help`):

- **Read same-day.** A daily bulk-delete job purges trace rows ~24h after creation.
- **`messageblock` caps at 10 KB** — the oldest trace lines are dropped first, so a
  chatty plug-in can lose its early output.
- **Traces survive transaction rollback.** When a synchronous plug-in throws and the
  transaction rolls back, the trace rows remain — which is what makes the table useful
  for diagnosing the failure that erased everything else.
