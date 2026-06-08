# Design — `crm workflow clone` + `export`/`import`

Issue: [#144](https://github.com/Gharib89/crm/issues/144)
Date: 2026-06-08
Status: implemented (PR #152)
Sequenced **before** [#143](https://github.com/Gharib89/crm/issues/143) (clone-entity reuses `clone_workflow_to_entity`).

## Corrected premise

The issue says "there is no `workflow` command group." **That is stale** — the
group already exists (`crm/commands/workflow.py`, `crm/core/workflow.py`):

- `crm workflow list` — filters by category / entity / activated / on-demand (`list_workflows`)
- `crm workflow activate` / `deactivate` — `set_workflow_state`
- `crm workflow run` — `execute_workflow`
- constants present: `CATEGORY_*`, `TYPE_DEFINITION/ACTIVATION`, `STATE_DRAFT/ACTIVATED`

**Genuinely missing (this PR):**
1. `crm workflow clone <id> --to-entity <e>` — xaml-retarget clone. **#143 depends on this.**
2. `crm workflow export <id>` / `import --file` — xaml round-trip. Independent of #143.

## Command surface (new)

```
crm workflow clone <workflowId> --to-entity <entity>
    [--name "New Name"]            # default: "<source name> (Clone)"
    [--activate/--no-activate]     # default: activate (compiles the xaml)
    [--solution <unique-name>]     # add as component type 29

crm workflow export <workflowId> [--out <path>]    # dump xaml (+ key fields) to file/stdout
crm workflow import --file <path> [--activate]     # upsert a workflow from an exported file
```

## Core additions — `crm/core/workflow.py`

- `get_workflow(backend, workflow_id)` — retrieve the `type=1` definition incl. `xaml`,
  `category`, `primaryentity`, `name`.
- `clone_workflow_to_entity(backend, workflow_id, target_entity, *, name=None, activate=True, solution=None)`.
- `retarget_xaml(xaml, *, src_entity, dst_entity, src_id, dst_id)` — pure string/XML
  transform, **unit-testable without a backend** (this is the load-bearing, risky bit).
- `export_workflow` / `import_workflow` — file round-trip wrappers over `get_workflow`
  + `entity.upsert`.

`crm/commands/workflow.py` gains the three thin Click wrappers.

## xaml retarget rules (from the issue, verified live in the plan)

1. Always operate on the **`type=1` Definition** master, never the `type=2`
   activation copy (server-generated).
2. Mint a fresh `workflowid` GUID for the clone.
3. Retarget `primaryentity` → `<target>`.
4. In the xaml: rewrite entity references `<src_entity>` → `<dst_entity>`, **and**
   the `x:Class="XrmWorkflow<src_id-no-dashes>"` plus the matching
   `<this:XrmWorkflow...>` element tags → `XrmWorkflow<dst_id-no-dashes>`.
5. Leave attribute logical names unchanged — they resolve if they exist on the
   target (clone-entity guarantees this; standalone clone to an arbitrary entity
   relies on the user; activation surfaces any unresolved ref as a compile error).
6. Create as Draft (`statecode/statuscode = STATE_DRAFT`); if `activate`, transition
   to `STATE_ACTIVATED` via `set_workflow_state` (this compiles the xaml).

## Category support — tiered, loud-fail, never a silent half-clone

All four xaml-based categories are in scope, but they are **not** equally simple.
The clone does the **complete** job for a category or **refuses with a clear
error** — it never produces a process that looks cloned but does not function.

- **Tier 1 — classic workflow (`category=0`), business rule (`=2`):** xaml-retarget
  + activate is sufficient. Full support.
- **Tier 2 — action (`=3`):** the workflow record + xaml plus its sdkmessage / I/O
  argument wiring. Implementation verifies live what an action needs beyond xaml;
  supports it fully or fails loudly if the extra registration can't be reproduced
  via API.
- **Tier 3 — BPF (`=4`):** a BPF has a backing entity, stage records, and
  `processid` linkage — xaml-retarget alone does **not** yield a working BPF. The
  plan determines the full create sequence; if any required piece has no API path,
  `clone` refuses for BPF with an explicit message rather than half-cloning.

`category=1` (dialog, deprecated) and `=5` (modern flow, Power Automate) are out of
scope — rejected with a clear error.

## Open questions — verify live before building on them

1. **Action (`=3`) requirements beyond xaml** — does an activated cloned action need
   separate sdkmessage/message-pair records, or does activating the `workflow` record
   suffice? Reproduce against a live org first.
2. **BPF (`=4`) full create sequence** — backing entity, stage records, `processid`,
   and whether all are API-writable. If not fully writable, BPF clone is a loud refusal.
3. **`x:Class` / `<this:XrmWorkflow...>` token shape** — confirm exact casing and that
   the id is dashes-stripped, from a real exported xaml, before trusting `retarget_xaml`.

## Tests

- `retarget_xaml` unit tests (no backend): entity-ref rewrite, `x:Class` + element-tag
  rewrite, id dash-stripping, idempotence, leaves attribute names untouched.
- E2E (live creds, per existing pattern): clone a classic workflow → activates and
  behaves identically; business rule clone; action/BPF either work or fail loudly.

## Docs (ship in same PR)

- `README.md` — workflow clone/export/import capability lines.
- `docs/how-to/workflow.md` — new sections.
- `crm/skills/SKILL.md` — clone/export/import entries.
- `docs/reference/cli.md` — auto-generated (good docstrings/help only).
- Conventional Commit `feat: crm workflow clone/export/import …` drives the bump.
- PR body: `Closes #144`.
