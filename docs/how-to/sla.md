# How-to: sla

Common `crm sla` recipes. See the [CLI reference](../reference/cli.md) for every flag.

## Activate an SLA and its backing workflows

```bash
crm --json sla activate <sla-guid>
```

SLAs are enforced by backing workflows — one per SLA item (`slaitem.workflowid`) — and the SLA cannot be activated until every one of them is active. The command:

1. Fetches the SLA and its SLA items, resolving each item's backing workflow.
2. Activates each backing workflow (already-active ones are skipped, so re-running is safe).
3. Activates the SLA itself (`statecode=1, statuscode=2`) — only if every backing workflow is active.

The result reports per-workflow status:

```json
{"ok": true, "data": {"sla_id": "...", "name": "Gold SLA", "sla_activated": true,
  "workflows": [
    {"workflow_id": "...", "name": "First response", "status": "activated"},
    {"workflow_id": "...", "name": "Resolve by", "status": "already_active"}
  ]}}
```

## When backing workflows have compile errors

After a solution import, backing workflows can carry compile errors (`InvalidEntity`, `InvalidRelationship`). The platform buries the detail in a raw message string — `ErrorMap Details: {ConditionBranchStep2: InvalidEntity, InvalidRelationship; ...}` — which the command parses into structured per-step entries:

```json
{"ok": false, "data": {"sla_activated": false, "ui_activation_required": true,
  "workflows": [
    {"workflow_id": "...", "name": "First response", "status": "failed",
     "error": "This workflow has errors. ErrorMap Details: {...}",
     "errors": [{"step": "ConditionBranchStep2",
                 "errors": ["InvalidEntity", "InvalidRelationship"]}]}
  ]},
 "error": "SLA ... was NOT activated: ..."}
```

If the message doesn't match the expected shape, the raw platform string is still reported in `error` — the detail is never dropped.

In this case the SLA is **not** touched and the exit code is non-zero. The Web API cannot activate a workflow that fails compilation, so activation must be done from the D365 UI (Settings → Service Level Agreements → open the SLA → Activate). Workflows that did activate during the run stay active — that matches platform behavior, and re-running after a fix picks up where it left off.

## Preview with --dry-run

```bash
crm --json --dry-run sla activate <sla-guid>
```

The resolution GETs run live; nothing is PATCHed. The preview lists what a live run would do:

```json
{"ok": true, "data": {"_dry_run": true, "would_activate": [...],
  "already_active": [...], "would_activate_sla": true}, "meta": {"dry_run": true}}
```
