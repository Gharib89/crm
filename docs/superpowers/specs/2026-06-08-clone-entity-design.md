# Design — `crm metadata clone-entity`

Issue: [#143](https://github.com/Gharib89/crm/issues/143)
Date: 2026-06-08
Status: approved-pending-implementation

## Problem

There is no single command to duplicate an entity under a new name while keeping
its components. The metadata/`apply` path recreates fields, views, lookups, and
reuses option sets, but offers no one-shot "clone this whole entity" verb. The
only full-duplicate path today is solution export → rename the schema token
throughout `customizations.xml` → reimport: heavy, footgun-laden XML surgery
(consistent GUID regeneration, negative-lookahead preserve-lists, system-relationship
pruning).

## Feasibility verdict — pure Web API, no XML

Every entity component is writable through the Dataverse Web API **except the
ribbon**:

| Component | API path | Status |
|---|---|---|
| Entity + attributes | `CreateEntity` metadata (existing `metadata.create_entity`) | writable |
| Option sets (reuse) | reference global option set by name | writable |
| Relationships (custom) | metadata API (`relationships.create_one_to_many` / `create_many_to_many`) | writable |
| Views + charts | create `savedquery` / `savedqueryvisualization` records | writable |
| Forms | direct create of `systemform` record with retargeted `formxml` + new `objecttypecode` | writable* |
| **Ribbon (`RibbonDiffXml`)** | **none — no API write path; solution import only** | **not writable** |

\* Forms create path is load-bearing and unverified — see Open Questions.

Microsoft docs are explicit: the ribbon XML "cannot be updated directly"; ribbon
changes deploy only via solution import (every `RibbonImport*` error fires only
during import). This is confirmed, not a gap to keep searching for.

**Consequence:** "clone via API / avoid XML" and "clone the ribbon too" cannot
both hold. Decision (below): drop ribbon from the API clone, detect-and-warn.
The gap is small in practice — a custom entity's ribbon is the default table
ribbon template, so its `RibbonDiffXml` is usually empty and nothing is lost.

## Command

```
crm metadata clone-entity <source> <new-schema-name>
    [--display "New Display"]      # default: "<source display> (Clone)"
    [--with-forms] [--with-views] [--with-charts] [--with-all]
    [--solution <unique-name>]     # add created components to a solution
    [--publish/--no-publish]       # default: publish
```

Lives under the `metadata` group (per the issue). No XML, no `solutionpackager`.

## Architecture

- **`crm/core/clone.py`** (pyright strict) — orchestrator `clone_entity(...)`.
  Thin: read source → create entity → relationships → views → forms → ribbon check.
  Returns a result dict (created entity logical name + counts + ribbon warning).
- **`crm/core/forms.py`** (pyright strict, **new**) — `read_entity_forms(backend, source)`
  and `clone_form_to_entity(backend, form, new_entity)`. Mirrors `views.py`
  (`read_entity_views` / `create_view`). Form retarget logic is isolated here so
  it is testable independently of the orchestrator.
- **`crm/commands/metadata.py`** — thin Click wrapper `clone-entity`.

Reused as-is: `metadata.create_entity` / `describe_entity` / `list_attributes`,
`relationships.read_entity_relationships` / `create_one_to_many` / `create_many_to_many`,
`views.read_entity_views` / `create_view`, `entity.create`, `solution.publish_all` /
`add_solution_component` / `_validate_customization_prefix`.

**A `crm form` user-facing command is out of scope** — filed as follow-up [#151](https://github.com/Gharib89/crm/issues/151).
The core `forms.py` module is built here so that command can wrap it later, the
same way `view` wraps `views.py`.

## Flow

1. Validate `<new-schema-name>` prefix (`_validate_customization_prefix`).
2. `describe_entity(source)` → metadata + attributes.
3. Build + `create_entity`:
   - Copy **custom** attributes only; skip system attributes (`createdby`,
     `ownerid`, `statecode`, `statuscode`, audit/owner columns, …).
   - Local option sets recreated inline; **global option sets referenced by name
     (reused, not duplicated)**.
   - Display name from `--display` or `"<source display> (Clone)"`, set at create time
     (no separate `update-entity` step — the API sets it directly).
4. Recreate **custom** relationships pointing at the **same target entities**.
   System relationships (`owner`/`team`/`businessunit`/`lk_*`) skipped — the
   platform recreates them. No `--retarget-lookup` in MVP.
5. `publish_all`.
6. `--with-views`: `read_entity_views(source)` → retarget `fetchxml`/`layoutxml`
   returned-type-code → `create_view` on clone.
7. `--with-forms`: `read_entity_forms(source)` → retarget `formxml` + `objecttypecode`
   → create `systemform` records → publish.
8. **Ribbon:** `RetrieveEntityRibbon(source)`. Non-trivial diff → print a clear
   warning that the custom ribbon was not copied (no API write path). Empty diff
   (common) → silent no-op.
9. `--solution`: `add_solution_component` for each created component.

## Defaults & decisions

- **Bare clone = skeleton only**: entity + attributes + reused option sets +
  custom relationships. Forms / views / charts are opt-in (`--with-*`).
  `--with-all` enables every component.
- **Ribbon**: detect-and-warn, skip. Never silently produces a clone that claims
  to include a ribbon it could not write.
- **Lookups**: recreated pointing at the same targets; no redirection in MVP.

## Open questions — verify live before building on them

1. **Forms create path (load-bearing).** Direct `systemform` create with a new
   `objecttypecode` needs live confirmation: (a) create is accepted, (b)
   `publish_all` is required afterward, (c) on modern Unified Interface the cloned
   form may need adding to the model-driven app's form list to be user-visible.
   The implementation plan reproduces this against a live org (TDD) before the
   orchestrator depends on it.
2. **`formxml` / `layoutxml` retargeting tokens.** Determine empirically from a
   real export which tokens carry the entity name: cell `datafieldname` stays;
   control bindings and `objecttypecode` change. Drives `forms.py` retarget logic.

## Staging

- **MVP (this PR):** skeleton + opt-in `--with-forms` / `--with-views` /
  `--with-charts`; ribbon detect-and-warn; `forms.py` core helper.
- **Deferred:** `--with-workflows` (blocked on #144, open), `--retarget-lookup`,
  `crm form` command group (follow-up [#151](https://github.com/Gharib89/crm/issues/151)).

## Docs (ship in same PR)

- `README.md` — capability line.
- `docs/how-to/metadata.md` — clone-entity section.
- `crm/skills/SKILL.md` — clone-entity entry.
- `docs/reference/cli.md` — auto-generated (good docstring/help only).
- Conventional Commit `feat: clone-entity …` drives the semantic-release bump.
- PR body: `Closes #143`.
