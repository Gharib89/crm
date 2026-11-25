# Automation — workflows and SLAs

Manage classic workflows and business rules, and the SLA lifecycle. Groups:
`workflow`, `sla`. Flags/choices: `crm <group> --help`. Composing step XAML:
`reference/workflow-xaml.md`.

## Workflows — `workflow`

**There is no `workflow create` verb — and that is deliberate.** A classic workflow is
authored as XAML, which has no practical CLI-flag surface (the same "don't reach past the
API" stance as ribbon and codegen). To stand up a *new* workflow:

- **`workflow clone <id> --to-entity …`** — copy an existing definition onto another table
  (categories `0` and `2` only), then edit/activate it; or
- author once in the D365 UI / Power Automate, then bring it over as JSON — this is the
  supported "create from scratch via CLI" path, in order: build it in the UI → `workflow
  list` to find its GUID → `workflow export <guid> --output wf.json` → tweak the JSON if needed
  → `workflow import --file wf.json --activate` (import upserts the definition).

A raw `entity create workflows …` is not a supported substitute: some orgs block
API-originated workflow creation outright and return `0x80045040` ("cannot be created
outside the D365 web application"). The verbs below all operate on **existing**
definitions.

```bash
crm --json workflow list --entity contoso_ticket --category 0   # definitions on an entity

# Find duplicate definitions (same name, >1 row — e.g. after retried solution imports):
# group `list` output by name client-side. `list` returns only type=1 definitions, so the
# server-made same-name type=2 activation copies never false-flag activated workflows.
crm --json workflow list \
  | jq '[.data | group_by(.name)[] | select(length > 1)
         | {name: .[0].name, count: length,
            rows: [.[] | {workflowid, statecode, statuscode}]}]'

crm --json workflow activate <workflow-guid>
crm --json workflow deactivate <workflow-guid> --yes
# deactivate is destructive (--yes gated — SKILL.md). A type=2 activation-record
# GUID is auto-resolved to its parent definition; the result carries meta.note
# naming both GUIDs (check it when looping on exact ids).

crm --json workflow delete <workflow-guid> --yes
# Deactivates the definition first when active, then deletes it. A type=2
# activation-record GUID resolves to its parent definition (the server removes
# the activation record with it) — meta.note names both GUIDs. NOT atomic: if
# the delete fails after the deactivate, the definition remains a draft (the
# error says so; no rollback). An activation record with no live parent has no
# supported Web API path — the command fails clean, pointing at the D365 UI.

crm --json workflow run <workflow-guid> --target <record-guid>   # trigger on-demand

# Clone a classic workflow onto another entity (xaml-retargeted; activates by
# default). clone is solution-scoped (--solution required).
crm --json workflow clone <workflow-guid> --to-entity contoso_ticketclone --solution my_solution
crm --json workflow clone <workflow-guid> --to-entity contoso_ticketclone \
    --name "My Clone" --solution my_solution --no-activate

# Export / import a workflow definition (incl. xaml) as JSON
crm --json workflow export <workflow-guid> --output ./wf.json
crm --json workflow import --file ./wf.json --activate
```

Category values: `0`=Workflow, `1`=Dialog, `2`=BusinessRule, `3`=Action, `4`=BPF,
`5`=ModernFlow. `--category` also accepts friendly names (`workflow`, `dialog`,
`businessrule`, `action`, `bpf`, `flow`), case-insensitive. **Clone supports only `0` and `2`** — action/BPF/dialog/modern-flow
fail loudly. (This is the same constraint the entity-clone `--with-workflows` flag
hits; see `reference/metadata-export.md`.) For an Action (`3`) or BPF (`4`) the refusal
is permanent and names the working path: **`solution export`/`import` moves the
process today** — as a redeployment of the original process (same primary entity,
e.g. into another org), not a retarget; pointing an Action/BPF at a *different*
entity means re-authoring it there. Dialog (`1`) and ModernFlow (`5`) are genuine
dead ends — deprecated and Power-Automate-owned respectively.

On on-prem v9.1, a published business rule (category `2`) cannot be deactivated
via the Web API — `deactivate` returns `0x80045002` (`Cannot update a published
workflow definition`); deactivate it from the classic UI instead. `deactivate`
works normally for classic workflows (category `0`).

`activate` / `deactivate` / `clone` honor `--dry-run` (resolution GETs still
fire): `activate`/`deactivate` preview as `{_dry_run, would_set_state:{statecode,
statuscode}, workflow_id, resolved_from_activation_id}` and `clone` as `{_dry_run,
would_clone:{source_id, workflow_id, name, primaryentity, category, activate,
solution}}` — never a fabricated `activated: true`.

### Updating a workflow — metadata and XAML paths

`workflow update <id>` has two distinct paths driven by whether `--xaml-file` is present:

- **Metadata path** (no `--xaml-file`) — rename, change scope/triggers. Works on both
  targets. Active workflows are auto-cycled (deactivate → edit → reactivate).
- **XAML logic path** (`--xaml-file`) — whole-definition replace of the step XAML. **On-prem
  only**. On Dataverse (cloud) the command refuses before any write with the provenance-wall
  error (`0x80045040` / `0x80045041`) — there is no flag to override this; compose/edit XAML
  on-prem or via the D365 UI, not through the CLI on cloud.

The two paths are mutually exclusive — combining `--xaml-file` with any metadata flag is a
usage error.

XAML-path gotchas (not in `--help`):

- **Whole replace, not a splice.** `--xaml-file` replaces the entire step-definition blob.
  To target a single step, export first (`workflow export`), edit the XAML, then write back.
- **Reference-validation runs live before writing.** The XAML is checked against the entity's
  attribute set. Warnings land on `meta.warnings`; `--strict` turns any warning into a failure
  and skips the write.
- **Reactivation rollback.** The server's reactivate step is the only compile authority; if
  it rejects the new XAML (any reactivation failure), the prior XAML is restored by default
  (`--rollback`). Pass `--no-rollback` to leave the rejected XAML in place for inspection; the
  workflow stays a draft.
- **Provenance wall** (`0x80045040`/`0x80045041`). Even on-prem, the platform rejects XAML it
  did not author; the CLI surfaces these as a clean error rather than a raw server fault.
- **type=2 auto-resolved.** An activation-record GUID is resolved to the parent definition
  on both paths.

### Migration readiness — `workflow migration-assess`

Plans (does not perform) a move of classic category-0 workflows to Power
Automate cloud flows. Read-only; no authoring — Microsoft has no supported API
to author cloud flows, so the value is the inventory + readiness verdict.

```bash
crm --json workflow migration-assess               # all category-0 definitions
crm --json workflow migration-assess --entity account
```

Each `data[]` row: `{id, name, primaryentity, state, mode, verdict, blockers}`.
`verdict` is `ready` or `blocked`; `blockers` lists which of three rules fired,
anchored to the MS capability table (no other blockers are invented):

- `real_time` — `mode` is real-time (synchronous); cloud flows run async only.
- `wait_condition` — the xaml contains a wait/wait-timeout step (`Postpone`).
- `custom_activity` — the xaml references a workflow activity from any assembly
  other than the out-of-box `Microsoft.Crm.Workflow` (first-party
  `Microsoft.Dynamics.*`/`Microsoft.PowerPages.*` solution activities count as
  custom too — they don't carry to a flow).

`blocked` means **needs redesign**, not impossible — each blocker maps to an MS
recommended pattern (switch/loop actions, recurrence trigger, changesets, custom
connectors). Runs on **both** targets: on on-prem the report is identical but
`meta.note` reminds you the migration target must be a Dataverse online
environment (cloud flows don't exist on-prem). `meta.count` is the row count.

## SLAs — `sla`

The full lifecycle is **`sla create` → `sla add-kpi` (one or more) → `sla activate`**.
Run them in that order.

### Key design fact

The Dataverse `sla` entity has **no condition attribute of its own** — per-KPI
applicability lives on `slaitem`, which is why the condition flags sit on
`sla add-kpi`, not `sla create`. SLA failure/warning *action steps* are
workflow/designer constructs — out of scope for the CLI.

### `sla create` — JSON contract

```json
{"ok": true, "data": {
  "created": true, "name": "...", "entity": "incident",
  "slaid": "<guid>", "sla_enabled": "already|set", "solution": "..."}}
```

`sla_enabled`:
- `already` — target entity's `IsSLAEnabled` flag was already true; nothing changed.
- `set` — the command flipped the flag and published the metadata change.
- `would_set` — dry-run preview: the flag would be flipped.

The `IsSLAEnabled` GET runs live even in dry-run (reads-execute rule) so the
preview is honest. Dry-run shape: `{_dry_run, would_create:{entity_set, body}, entity, sla_enabled}`.

### `sla add-kpi` — JSON contract

Attaches a KPI / SLA-item (`slaitem`) to an existing SLA. `--applicable-when`
and `--success-criteria` each accept an inline string **or** a `*-file` path;
exactly one source is required per condition.

```json
{"ok": true, "data": {
  "created": true, "slaitemid": "<guid>",
  "sla_id": "<guid>", "name": "firstresponsebykpiid", "solution": "..."}}
```

Dry-run shape: `{_dry_run, would_create:{entity_set, body}, sla_id}`.

### `sla activate` — JSON contract

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
