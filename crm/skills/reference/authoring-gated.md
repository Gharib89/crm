# Schema authoring — approval-gated apply and pruning

Run `apply` behind a reviewed plan, and remove org components a spec no longer
declares. Command: top-level `apply`. Flags/choices: `crm describe apply`. The
spec format and plain `apply -f`: `reference/authoring.md`.

## Approval-gated apply (plan → verify → execute)

For an unattended/agent-driven apply where "what was approved must be what runs",
serialize the dry-run drift report as a **plan** and later execute it only if the
org has not drifted since:

```bash
crm --dry-run --json apply -f project.yaml -o plan.json  # 1. plan (review plan.json)
crm --dry-run --json apply --from-plan plan.json         # 2. verify (CI pre-check)
crm --json apply --from-plan plan.json                   # 3. execute the approved plan
```

- `--from-plan` is mutually exclusive with `-f`. It **replays** the plan's intent
  (`--prune` / `--allow-data-loss` / `--stage-only`) from the plan header — passing
  any of them (or `-o`) alongside `--from-plan` is a usage error (exit 2).
- Before executing, it recomputes the drift report and compares at the **action
  level** (component set + verdict + each `updated` component's changed-field set;
  live values are not byte-compared). **Any divergence → stale plan: zero writes,
  `ok=false`, exit 1**, with `data.divergences` = `[{kind, name, plan, live}]`. The
  fix is always re-plan, not a weaker gate.
- `--dry-run --from-plan` is **verify mode**: it reports `data.plan_valid`
  (`true`/`false`) and writes nothing.
- Pre-flight **refuses** (exit 1) a plan whose `plan_format` is unknown, whose
  `organization_id` ≠ the live WhoAmI, that carries `replace_blocked`/`failed`
  components, or whose pinned payloads are missing/changed. A URL or CLI-version
  mismatch is only a `meta.warnings` note. **Payload `file`s resolve relative to the
  plan file's directory** — keep referenced web-resource/DLL files beside the plan.
- Residual TOCTOU: the gate shrinks the window to verify-to-write but cannot close
  it (metadata writes are not transactional).

```yaml
publisher: {unique_name: contosopub, prefix: contoso, option_value_prefix: 10000}
solution:  {unique_name: ContosoCore}
optionsets:
  - {name: contoso_priority, display_name: Priority, options: [{value: 100000000, label: Low}]}
entities:
  - schema_name: contoso_Project
    display_name: Project
    primary_attr: {schema_name: contoso_Name, label: Name}
    attributes:
      - {kind: string,   schema_name: contoso_Code,     display_name: Code, max_length: 100}
      - {kind: memo,     schema_name: contoso_Notes,    display_name: Notes, max_length: 2000}
      - {kind: picklist, schema_name: contoso_Priority, display_name: Priority, optionset_name: contoso_priority}
      - {kind: lookup,   schema_name: contoso_Owner,    display_name: Owner, target_entity: systemuser}
    views:
      - {name: Active Projects, columns: [contoso_name, contoso_code]}
webresources:
  - name: contoso_/scripts/project.js   # unique name; webresourcetype inferred from .js
    file: scripts/project.js            # path relative to the spec file
    display_name: Project Script        # optional
security_roles:
  - name: Contoso Project Manager
    privileges:
      - {access: [read, write, create], entities: [contoso_project], depth: deep}
      - {privilege_names: [prvReadSystemForm], depth: global}
plugins:
  - assembly: Contoso.Plugins           # optional; defaults to DLL file stem
    file: bin/Contoso.Plugins.dll       # path relative to the spec file
    isolation_mode: sandbox             # optional (none|sandbox)
    types:
      - type_name: Contoso.Plugins.AccountHandler   # fully-qualified class; the convergent key
    steps:
      - name: Contoso Account Handler   # unique stable key
        message: Create
        plugin_type: Contoso.Plugins.AccountHandler
        entity: account                 # optional; omit for message-level
        stage: postoperation            # optional (prevalidation|preoperation|postoperation)
        images:
          - alias: PreImage
            image_type: pre             # pre|post|both
```

In a spec attribute block, `string` and `memo` `max_length` is optional — omit it and
the create defaults to 100 / 2000 (matching the `scaffold` / column-shorthand path). An
explicit `max_length` is honored verbatim; `max_length` on any other kind is rejected.
`source_type` (`simple` / `calculated` / `rollup`) with `formula_definition` (XAML
string) creates a rollup or calculated column — mirrors `metadata add-attribute --type`.
`source_type: calculated` / `rollup` requires `formula_definition` and is rejected on
`lookup`/`customer` kinds. Omitting `source_type` creates a plain column. Formula
drift is **not** reconciled — the reconcile pass ignores `formula_definition`.

**Security role convergence gotcha — baseline privileges and removal-only no-op.**
Dataverse auto-grants every role immovable baseline privileges (e.g. SharePoint
document management) that `ReplacePrivilegesRole` cannot remove — apply treats them
as invisible and will not block on them. A privilege *dropped* from the spec is only
removed if another declared privilege also drifts in the same run (triggering a fresh
replace). A removal-only change where all remaining declared privileges are already
satisfied is a convergent no-op; use `crm security set-role-privileges` to force it.

**Plug-in step binding is immutable — `replace_blocked` on message/entity/type change.**
The platform fixes a step's `message`, `entity`, and `plugin_type` at creation; there
is no PATCH path to change them. If a declared step's binding drifts from the live
record, apply classifies it `replace_blocked` (reported, no write, exits 1). To
rebind a step: unregister it manually (`crm plugin unregister-step`) then re-apply.

**Plug-in components are not publishable** — a plugins-only apply never issues
`PublishAllXml`.

## Pruning org-extras — `--prune`

`--prune` opts in to solution-bounded deletion of components that are **members of
the target solution but absent from the spec**. A plain `apply` never reads
solution members; pruning is strictly opt-in.

**Six eligible kinds:** `entity`, `attribute`, `view`, `security-role`,
`webresource`, `plugin-step`. All other solution component types are out of scope.

**Gating — what `--help` cannot convey:**

- **Schema-only kinds** (`view`, `security-role`, `webresource`, `plugin-step`) —
  deleted after confirmation.
- **Data-bearing kinds** (`entity`, `attribute`) — refused unless `--allow-data-loss`
  is also passed; they appear in `pruned` with `deleted: false` and a `reason` field.
- **`--json` / no-TTY** — for a **real** prune the interactive prompt is
  unavailable, so `--yes` is required or the run errors before doing anything. A
  `--dry-run` preview deletes nothing, so it needs no `--yes`.
- **Scoped to the spec's mandatory `solution:` block.** Every spec must declare a
  top-level `solution: {unique_name: ...}` (see the spec in `reference/authoring.md`) — `apply` has
  no `--solution` flag; the target is always explicit.
- **Suppressed on partial failure** — if the convergence phase produces any `failed`
  or `replace_blocked` entries, pruning is skipped entirely. Fix the spec, re-apply,
  then prune.
- **Never publishes** — a prune deletion triggers no publish (the end-of-run
  publish gate covers only created/updated components).

**Always dry-run before a real prune:**

```bash
crm --dry-run --json apply -f project.yaml --prune
```

Candidates appear in `data.pruned` with `deleted: false`; those that *would* be
deleted carry `would_prune: true`. No write is issued.

**JSON contract for `pruned` entries:**

```json
{"kind": "webresource", "name": "contoso_/scripts/old.js", "deleted": true}
{"kind": "view",        "name": "Old Projects",            "deleted": false, "would_prune": true}
{"kind": "attribute",   "name": "contoso_legacycode",      "deleted": false,
 "reason": "data-bearing; pass --allow-data-loss to delete"}
```

**Non-interactive (CI / agent) pattern:**

```bash
crm --json apply -f project.yaml --prune --yes
# Add --allow-data-loss only when entity/attribute deletion is intentional
```
