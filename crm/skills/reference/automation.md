# Automation — plug-ins, workflows, and SLAs

Register plug-in assemblies and processing steps; manage classic workflows,
business rules, and SLA activation. Groups: `plugin`, `workflow`, `sla`.
Flags/choices: `crm <group> --help`.

## Plug-ins — `plugin` (assembly + step + image lifecycle)

The full workflow is **upload assembly → verify the platform-generated types →
register a step** against one of those types, then optionally attach entity
images to the step:

```bash
# register-assembly: .dll bytes are base64'd into `content`. --solution sends
# MSCRM.SolutionUniqueName.
crm --json plugin register-assembly ./bin/Contoso.Plugins.dll --solution cwx_contoso

# --update: re-uploads content of an existing assembly (resolved by name); the
# identity flags --version/--culture/--public-key-token/--description/--isolation-mode
# are IGNORED under --update and produce a warning.
crm --json plugin register-assembly ./bin/Contoso.Plugins.dll --update

# list-types: platform-generated rows in plugintypes (one per IPlugin class).
crm --json plugin list-types --assembly Contoso.Plugins

# register-step: --message and --plugin-type are required. async forces postoperation.
# --entity sets primaryobjecttypecode (omit = all entities); --filtering-attributes
# (comma-separated) restricts an Update step. The step name is auto-derived as
# '<typename>: <message> of <entity>'; pass --name when that would exceed the
# 256-char platform limit.
crm --json plugin register-step \
    --message Update \
    --plugin-type Contoso.Plugins.AccountPostUpdate \
    --entity account --stage postoperation --mode sync \
    --filtering-attributes name,telephone1
```

```bash
# register-image: --step takes the step GUID or exact name. messagepropertyname
# is derived from the step's message (Send is ambiguous — pass
# --message-property-name FaxId|EmailId|TemplateId). Always pass --attributes:
# omitting it snapshots ALL columns (documented performance anti-pattern).
# Rejected client-side: pre-image on Create, post-image on Delete, post-image
# on a non-PostOperation step, messages that don't support images.
crm --json plugin register-image \
    --step "Contoso.Plugins.AccountPostUpdate: Update of account" \
    --type pre --alias preimg --attributes name,telephone1
```

```bash
# unregister-image: by name or GUID; only needed to remove an image while
# keeping the step — deleting a step cascades its images.
crm --json plugin unregister-image preimg --yes

# unregister-step: by name or GUID; an ambiguous name errors (use the GUID).
crm --json plugin unregister-step "Contoso.Plugins.AccountPostUpdate: Update of account" --yes

# unregister-assembly: cascades — deletes dependent steps first, then the assembly.
crm --json plugin unregister-assembly Contoso.Plugins --yes
```

`--dry-run` skips all writes (resolution GETs still fire); the `--json` envelope
carries `meta.dry_run: true`.

## Workflows — `workflow`

```bash
crm --json workflow list --entity cwx_ticket --category 0   # definitions on an entity

# Find duplicate definitions (same name, >1 row — e.g. after retried solution imports):
# group `list` output by name client-side. `list` returns only type=1 definitions, so the
# server-made same-name type=2 activation copies never false-flag activated workflows.
crm --json workflow list \
  | jq '[.data | group_by(.name)[] | select(length > 1)
         | {name: .[0].name, count: length,
            rows: [.[] | {workflowid, statecode, statuscode}]}]'

crm --json workflow activate <workflow-guid>
crm --json workflow deactivate <workflow-guid>
# A type=2 activation-record GUID is auto-resolved to its parent definition; the result carries meta.note naming both GUIDs (check it when looping on exact ids).

crm --json workflow delete <workflow-guid> --yes
# Deactivates the definition first when active, then deletes it. A type=2
# activation-record GUID resolves to its parent definition (the server removes
# the activation record with it) — meta.note names both GUIDs. NOT atomic: if
# the delete fails after the deactivate, the definition remains a draft (the
# error says so; no rollback). An activation record with no live parent has no
# supported Web API path — the command fails clean, pointing at the D365 UI.

crm --json workflow run <workflow-guid> --target <record-guid>   # trigger on-demand

# Clone a classic workflow onto another entity (xaml-retargeted; activates by default)
crm --json workflow clone <workflow-guid> --to-entity cwx_ticketclone
crm --json workflow clone <workflow-guid> --to-entity cwx_ticketclone \
    --name "My Clone" --solution my_solution --no-activate

# Export / import a workflow definition (incl. xaml) as JSON
crm --json workflow export <workflow-guid> --out ./wf.json
crm --json workflow import --file ./wf.json --activate
```

Category values: `0`=Workflow, `1`=Dialog, `2`=BusinessRule, `3`=Action, `4`=BPF,
`5`=ModernFlow. **Clone supports only `0` and `2`** — action/BPF/dialog/modern-flow
fail loudly. (This is the same constraint the entity-clone `--with-workflows` flag
hits; see `reference/metadata.md`.)

## SLAs — `sla`

An SLA cannot activate until every backing workflow (one per SLA item) is
active. `sla activate <sla-guid>` orchestrates the whole sequence: backing
workflows first (already-active ones skipped — re-running is safe), then the
SLA record itself.

```bash
crm --json sla activate <sla-guid>
# data.workflows[] reports per-workflow status: activated | already_active | failed
```

Gotchas:

- **Compile errors block the API path.** After a solution import, backing
  workflows can fail activation with `InvalidEntity`/`InvalidRelationship`
  compile errors. The command parses the platform's raw
  `ErrorMap Details: {Step: Err, ...}` string into structured
  `data.workflows[].errors` = `[{"step": ..., "errors": [...]}]` (raw message
  kept in `.error` either way). When that happens the SLA is NOT touched,
  exit is non-zero, and `data.ui_activation_required` is true — the only fix
  is the D365 UI (Settings → Service Level Agreements → open the SLA →
  Activate). Workflows already activated in the run stay active; re-run after
  fixing to pick up where it left off.
- `--dry-run` resolves the plan with live GETs and returns
  `{_dry_run, would_activate, already_active, would_activate_sla}` — nothing
  is PATCHed.
