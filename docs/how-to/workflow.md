# How-to: workflow

Common `crm workflow` recipes. See the [CLI reference](../reference/cli.md) for every flag.

## List workflow definitions

```bash
crm --json workflow list --entity cwx_ticket --category 0
```
`--category` filters by kind (`0`=Workflow, `4`=BPF, `5`=Modern Flow); `--on-demand` restricts to on-demand workflows.

## Activate a workflow

```bash
crm --json workflow activate <workflow-guid>
```
Sets `statecode=1, statuscode=2`; `crm workflow deactivate` reverses it.

If you pass an activation-record GUID (type=2 — the compiled copy the server creates when a draft is activated), the command returns `ok: false` with a hint naming the parent draft GUID to pass instead.

## Trigger an on-demand workflow against a record

```bash
crm --json workflow run <workflow-guid> --target <record-guid>
```
Calls `ExecuteWorkflow` against the target record; `--as-user <guid>` impersonates a systemuser via `MSCRMCallerID`, or `--as-user-object-id <guid>` impersonates an Entra ID user via `CallerObjectId` (cloud). The two are mutually exclusive.

## Clone a workflow onto another entity

Duplicate a classic workflow or business rule onto a different entity. The clone gets a fresh GUID; the xaml is retargeted to reference the new entity.

```bash
# Clone and activate immediately (default)
crm --json workflow clone <workflow-guid> --to-entity cwx_ticketclone

# Clone as draft only
crm --json workflow clone <workflow-guid> --to-entity cwx_ticketclone --no-activate

# Custom name and add to a solution
crm --json workflow clone <workflow-guid> \
    --to-entity cwx_ticketclone \
    --name "Ticket Clone — Send notification" \
    --solution my_solution
```

Action and business-process-flow cloning is not yet supported; use solution export/import for those.

## Export a workflow definition

Save the full workflow definition (including xaml) to a JSON file for source control or migration.

```bash
crm --json workflow export <workflow-guid> --out ./workflows/update-request.json
```

## Import a workflow definition

Upsert a previously exported workflow definition back to an org.

```bash
# Import as draft (default)
crm --json workflow import --file ./workflows/update-request.json

# Import and activate immediately
crm --json workflow import --file ./workflows/update-request.json --activate
```

The `workflowid` in the JSON file is preserved — the import is an explicit-GUID upsert, so re-running is idempotent.
