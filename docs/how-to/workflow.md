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

## Trigger an on-demand workflow against a record

```bash
crm --json workflow run <workflow-guid> --target <record-guid>
```
Calls `ExecuteWorkflow` against the target record; `--as-user <guid>` impersonates a user via `MSCRMCallerID`.
