# Plan: Pre-delete dependency preview (`metadata dependencies` + `--check-dependencies`)

Issue: #81 (source research R22). Branch: `feat/metadata-dependencies-81`.

## Goal

A read-only `crm metadata dependencies <target>` command + core function that calls the
Dataverse Web API function `RetrieveDependenciesForDelete(ObjectId=<guid>,ComponentType=<int>)`
(or `RetrieveDependentComponents` via `--for dependents`) and returns
`{can_delete: bool, blockers: [...]}`. Plus an opt-in `--check-dependencies` flag on the four
delete verbs that folds the blocker set into the dry-run preview.

## Decisions (locked with user 2026-06-05)

- **Kind resolution:** `--kind entity|attribute|optionset|relationship` option, default `entity`.
  Attribute uses dotted target `entity.attribute`. Single `<target>` positional.
- **`--for`:** ship `delete` (default) + `dependents` only — both share the
  `(ObjectId, ComponentType)` signature. **Drop `uninstall`** (its real signature is
  `SolutionUniqueName` + `Component` entity ref, not "switch only the function name"); file a
  follow-up issue.
- **`--check-dependencies`:** wire into **all four** delete verbs
  (delete-entity/-attribute/-relationship/-optionset), default off.

## Authoritative API facts (verified via Microsoft Learn 2026-06-05)

- URL: `RetrieveDependenciesForDelete(ObjectId=<guid>,ComponentType=<int>)`.
  `ObjectId`=`Edm.Guid`, `ComponentType`=`Edm.Int32`. Returns `Collection(dependency)` →
  JSON envelope `{"value": [ <dependency record>, ... ]}`.
- `--for dependents` → `RetrieveDependentComponents(ObjectId=<guid>,ComponentType=<int>)` (same signature).
- **Web API inline literal encoding:** `Edm.Guid` params are passed **unquoted**
  (`ObjectId=01234567-89ab-...`); `Edm.Int32` unquoted (`ComponentType=9`). This is the one
  detail to keep in a single tested helper so a server rejection is a one-line fix.
- `componenttype` per kind: **entity=1, attribute=2, optionset=9, relationship=10**
  (Entity Relationship; legacy 3 = "Relationship", not used here).
- `dependency` record fields: `dependentcomponenttype`, `dependentcomponentobjectid`,
  `dependentcomponentparentid`, `requiredcomponenttype`, `requiredcomponentobjectid`,
  `dependencytype` — the `*componenttype` fields are `componenttype` picklist **codes**.
  The API returns **no display name** for the dependent object (only type code + GUID).
- `componenttype` label map (common subset, fall back to `str(code)` for unknown):
  1 Entity, 2 Attribute, 3 Relationship, 9 Option Set, 10 Entity Relationship, 24 Form,
  20 Role, 26 SavedQuery (view), 60 SystemForm, 29 Workflow, etc. — embed only the stable,
  commonly-hit subset; unknown codes render as their integer.

## Resolution paths (GET `$select=MetadataId`, reuse the dry-run-off trick)

- entity → `EntityDefinitions(LogicalName='<name>')`, componenttype 1
- attribute → `EntityDefinitions(LogicalName='<entity>')/Attributes(LogicalName='<attr>')`, componenttype 2
- optionset → `GlobalOptionSetDefinitions(Name='<name>')`, componenttype 9
- relationship → `RelationshipDefinitions(SchemaName='<schema>')`, componenttype 10

Read `MetadataId` from the response dict. 404 → `D365Error` "not found" (re-raise non-404).

## Blocker shape (returned per dependency)

The API gives type codes + GUIDs, not names, so:

```json
{
  "dependent_type": "Attribute",        // label from componenttype map
  "dependent_id": "<guid>",             // dependentcomponentobjectid
  "dependent_parent_id": "<guid|null>", // dependentcomponentparentid
  "required_type": "Option Set",        // label from componenttype map
  "dependency_type": "<label|code>"     // dependencytype
}
```

`can_delete` = `len(value) == 0`.

---

## Task 1 — Core module `crm/core/dependencies.py` (pyright strict, TDD)

**Files:** new `crm/core/dependencies.py`; new tests `crm/tests/test_dependencies.py`.

Implement:

- `_COMPONENT_TYPE: dict[int, str]` label map (stable subset above).
- `_KIND_RESOLVERS` mapping kind → (path-builder, componenttype int).
- `resolve_target(backend, kind, target) -> tuple[str, int]`: builds the per-kind
  `$select=MetadataId` path, forces a real GET via the **dry-run-off trick**
  (`was_dry = backend.dry_run; backend.dry_run = False; ... finally restore`), reads
  `MetadataId`. Attribute target is dotted `entity.attribute` → split on the **first** `.`.
  Raise `D365Error` for empty/missing target, bad kind, dotted-form errors, and 404
  (message: `"<kind> '<target>' not found"`). Re-raise non-404 `D365Error`.
- `_FUNCTIONS: dict[str, str]` = `{"delete": "RetrieveDependenciesForDelete",
  "dependents": "RetrieveDependentComponents"}`.
- `build_dependency_path(metadata_id, component_type, for_) -> str`: the **single** place
  that encodes the inline function URL (`f"{fn}(ObjectId={metadata_id},ComponentType={component_type})"`).
- `_map_blocker(record) -> dict[str, Any]`: maps one dependency record to the blocker shape,
  resolving type codes through `_COMPONENT_TYPE`.
- `retrieve_dependencies(backend, kind, target, *, for_="delete") -> dict[str, Any]`:
  resolves target, GETs the function path (`as_dict(backend.get(path))`), maps
  `result.get("value", [])` to blockers, returns
  `{"can_delete": bool, "blockers": [...], "metadata_id": ..., "component_type": ...,
  "kind": kind, "for": for_}`. The dependency GET is read-only → also force dry-run-off so
  the function works under `--dry-run` (preview must reflect the live answer).

**Tests (TDD, requests_mock, assert exact URLs):**
- One test per kind asserting the exact `$select=MetadataId` GET URL issued
  (`backend.url_for(...)`), with a mocked `MetadataId`.
- Exact function URL for `--for delete` and `--for dependents`
  (`RetrieveDependenciesForDelete(ObjectId=<guid>,ComponentType=<ct>)` etc.).
- `can_delete is True` when `value: []`; `can_delete is False` + correct blocker mapping
  when `value` has records (assert label resolution + GUID passthrough).
- 404 on resolve → `D365Error` with "not found".
- Dotted attribute target splits correctly; bad kind raises.

**Verify:** `pytest crm/tests/test_dependencies.py` green; `pyright --pythonpath .venv/bin/python crm/core/dependencies.py` clean.

## Task 2 — CLI command `metadata dependencies <target>`

**Files:** edit `crm/commands/metadata.py`; new/extended tests `crm/tests/test_cmd_metadata_dependencies.py`.

- `@metadata_group.command("dependencies")`, `@click.argument("target")`,
  `@click.option("--kind", type=click.Choice([...]), default="entity")`,
  `@click.option("--for", "for_", type=click.Choice(["delete","dependents"]), default="delete")`,
  `@pass_ctx`. Call `dep_mod.retrieve_dependencies(...)`; wrap in `try/except D365Error` →
  `_handle_d365_error`.
- **Emit:** JSON mode → `ctx.emit(True, data=info, meta={...} )` with full dict.
  Human mode → a table of blockers (headers: Dependent Type, Dependent Id, Required Type,
  Dependency Type) + status lines. **Gate JSON-only meta keys on `ctx.json_mode`** (emit renders
  `meta=` in human mode too). `can_delete` shown as a status line in both modes.
- Tests via `CliRunner` (Click 8.2: stdout/stderr separate, no `mix_stderr`): json output has
  `can_delete`+`blockers`; human output renders the table; `--kind`/`--for` plumb through.

**Verify:** `pytest crm/tests/test_cmd_metadata_dependencies.py` green.

## Task 3 — `--check-dependencies` on the four delete verbs

**Files:** edit `crm/core/metadata.py` (delete_entity), `crm/core/metadata_attrs.py`
(delete_attribute), `crm/core/relationships.py` (delete_relationship), `crm/core/optionsets.py`
(delete_optionset); edit `crm/commands/metadata.py` (4 wrappers); extend the delete tests.

- Add `check_dependencies: bool = False` to each core delete fn. Each already does a pre-flight
  GET for `IsCustom*/IsManaged` — **add `MetadataId` to that same `$select`** so no extra
  resolution round-trip. When `check_dependencies` is true, call
  `dependencies.retrieve_dependencies(...)` **by MetadataId** (add a small
  `dependencies_by_id(backend, metadata_id, component_type, for_="delete")` helper in the core
  module so the delete path reuses the already-fetched id instead of re-resolving). Merge
  `can_delete` + `blockers` into the returned dict.
- Gate so the dependency GET only fires when the flag is set (no extra round-trip otherwise).
  Most useful under `--dry-run` (preview) but allowed in both; it never mutates.
- Add `--check-dependencies` (`is_flag=True`, default False) to the four Click wrappers; pass through.
- Tests: with flag → result/preview includes `can_delete`+`blockers` and the function GET is
  issued; without flag → **no** dependency GET in `request_history`.

**Verify:** existing delete tests still green + new assertions; `pyright` clean on all four core files.

## Task 4 — Docs + CHANGELOG + SKILL sync

**Files:** `README.md`, `CHANGELOG.md` (`## [Unreleased]`), `docs/how-to/metadata.md`,
`docs/reference/cli.md`, `crm/skills/SKILL.md`.

- Document `metadata dependencies <target> [--kind ...] [--for delete|dependents]` and the
  `--check-dependencies` flag on the delete verbs. Every doc note must be a verified factual
  claim (check against `--help`/code — no plausible-sounding fabrication).
- CHANGELOG entry under `## [Unreleased]` (Keep a Changelog).
- Keep `crm/skills/SKILL.md` (tracked source of truth) in sync — add the new command/flag.
- File a follow-up issue for `--for uninstall` (distinct solution-scoped signature).

**Verify:** `mkdocs build --strict` clean (no broken links / stale refs).

## Final

Full-implementation code review → `superpowers:finishing-a-development-branch` (PR + Copilot review).
