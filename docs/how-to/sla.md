# How-to: sla

Common `crm sla` recipes. See the [CLI reference](../reference/cli.md) for every flag.

## Full SLA lifecycle

The three verbs run in order:

1. `sla create` — creates the SLA record and ensures the target entity is SLA-enabled.
2. `sla add-kpi` — attaches one or more KPI / SLA-item records with their applicability conditions.
3. `sla activate` — activates the backing workflows and then the SLA itself.

The `sla` entity has **no FetchXML condition attribute of its own** — per-KPI
conditions (`--applicable-when`, `--success-criteria`) live on each SLA item and
are set via `sla add-kpi`. `sla create` only takes `--applicable-from` (the
SLA's date-anchor field, e.g. `createdon`). SLA failure/warning action steps are
workflow-designer constructs and are outside CLI scope.

## Create an SLA

```bash
crm --json sla create \
    --name "Gold SLA" \
    --entity incident \
    --applicable-from createdon \
    --solution MySolution
```

`--entity` is the target entity's logical name. If that entity's `IsSLAEnabled`
metadata flag is not already set, the command enables and publishes it automatically.
The `sla_enabled` field in the response tells you what happened:

- `already` — the entity was already SLA-enabled; nothing changed.
- `set` — the command flipped the flag and published the metadata change.

```json
{"ok": true, "data": {
  "created": true, "name": "Gold SLA", "entity": "incident",
  "slaid": "<new-guid>", "sla_enabled": "already", "solution": "MySolution"}}
```

`--applicable-from` sets the date-anchor field the SLA calculates warning/failure
times from. `--business-hours <GUID>` associates a business-hours calendar.

### Preview before creating

```bash
crm --json --dry-run sla create \
    --name "Gold SLA" --entity incident --applicable-from createdon
```

The `IsSLAEnabled` GET runs live even in dry-run (so the preview is honest about
whether the flag needs flipping); the metadata write is suppressed. The response
carries `sla_enabled: "already"` or `sla_enabled: "would_set"`.

```json
{"ok": true, "data": {"_dry_run": true,
  "would_create": {"entity_set": "slas", "body": {...}},
  "entity": "incident", "sla_enabled": "already"},
 "meta": {"dry_run": true}}
```

## Add KPI / SLA-item conditions

After creating the SLA, attach one KPI per metric you want to track. Each KPI
needs an `--applicable-when` condition (when this KPI fires) and a
`--success-criteria` condition (what counts as success).

Pass conditions inline or from files — exactly one source per condition:

```bash
# Inline FetchXML/condition strings
crm --json sla add-kpi \
    --sla <sla-guid> \
    --kpi firstresponsebykpiid \
    --applicable-when '<fetch>...</fetch>' \
    --success-criteria '<fetch>...</fetch>'
```

```bash
# From files (useful when conditions are long)
crm --json sla add-kpi \
    --sla <sla-guid> \
    --kpi firstresponsebykpiid \
    --applicable-when-file ./conditions/applicable.xml \
    --success-criteria-file ./conditions/success.xml \
    --solution MySolution
```

`--name` defaults to the `--kpi` value when omitted. Repeat `sla add-kpi` for
each KPI you need on the same SLA.

```json
{"ok": true, "data": {
  "created": true, "slaitemid": "<item-guid>",
  "sla_id": "<sla-guid>", "name": "firstresponsebykpiid",
  "solution": "MySolution"}}
```

### Preview before adding

```bash
crm --json --dry-run sla add-kpi \
    --sla <sla-guid> --kpi firstresponsebykpiid \
    --applicable-when-file ./applicable.xml \
    --success-criteria-file ./success.xml
```

```json
{"ok": true, "data": {"_dry_run": true,
  "would_create": {"entity_set": "slaitems", "body": {...}},
  "sla_id": "<sla-guid>"},
 "meta": {"dry_run": true}}
```

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
