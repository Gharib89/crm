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

If you pass an activation-record GUID (type=2 — the compiled copy the server creates when a draft is activated), the command resolves the parent definition via the row's `parentworkflowid` lookup and applies the state change to the parent automatically. The result carries a note naming both GUIDs (in human and `--json` output alike), so you can see the redirect happened:

```json
{"ok": true, "data": {...}, "meta": {"note": "Operated on parent definition <parent-guid>; activation-record GUID <passed-guid> was passed."}}
```

Passing a draft GUID is unchanged (no note, no extra round-trip). If the parent cannot be resolved, the command surfaces the server's original `0x80045003` rejection with a hint naming the parent definition GUID when known. Under `--dry-run` the resolution GET still runs, so the preview is keyed on the same GUID the live run would patch.

`crm entity delete workflows <guid>` against that same activation-record GUID fails too — D365 rejects deleting activation rows directly (server code `0x80045004`). You can't delete the activation; deactivate its parent definition instead, which removes the activation. The error carries a hint: when the parent can be resolved it names the parent GUID and the exact `crm workflow deactivate <parent-guid>` command; otherwise it points you at the activation row's `parentworkflowid` lookup.

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
