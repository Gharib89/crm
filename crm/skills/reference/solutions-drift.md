# Solutions — drift: export-spec, component snapshots, audit, layer conflicts

Detect drift between a solution and an org, or between two orgs. Group:
`solution`. Flags/choices: `crm solution --help`. Create, export, import and
validate: `reference/solutions.md`.

## Project a solution into a desired-state spec — `export-spec`

The **source side of the org-to-org drift recipe**: project every component in a solution
— entities, security roles, web resources, and model-driven apps — into one merged
apply-consumable spec, then run `apply --dry-run` against the target org to see what drifts
— pure reads, no writes on either side.

```bash
# Dev org: write the bare YAML spec
crm solution export-spec MyCustomSolution -o desired.yaml

# Target org: preview schema drift, zero writes
crm --dry-run apply -f desired.yaml
```

Without `-o`, the JSON envelope carries a summary `data` payload plus the `skipped` bucket.

**JSON contract (without `-o`):** `data.entities` / `data.optionsets` are name lists,
`data.security_roles` / `data.webresources` are name lists, `data.attributes` /
`data.forms` are total counts. The `skipped` bucket rides in `data`, not `meta`;
`meta` carries only the standard envelope keys (`profile`/`url`, and `warnings`
when any drop-reasons accumulated).
```json
{
  "ok": true,
  "data": {
    "solution": "MyCustomSolution",
    "entities": ["contoso_Ticket", "contoso_Project"],
    "attributes": 12,
    "forms": 2,
    "optionsets": ["contoso_priority"],
    "security_roles": ["Contoso Project Manager"],
    "webresources": ["contoso_/scripts/project.js"],
    "apps": ["contoso_projecthub"],
    "skipped": [
      {"type": "pluginassembly", "objectid": "<guid>",
       "reason": "plug-in component not projectable from a live org (assembly DLL bytes absent); ..."}
    ]
  }
}
```
With `-o FILE`, `data` is instead `{path, solution, entities: <count>, attributes,
forms, optionsets: <count>, security_roles: <count>, webresources: <count>,
apps: <count>, skipped}`.

**Security roles** project as `security_roles[]` — name, optional `business_unit`, and
privileges grouped by depth into `privilege_names` selector rows. Roles whose privileges
are all at non-authorable depths (e.g. RecordFilter) are routed to `skipped`.

**Web resources** project as `webresources[]` — body carried inline as base64 `content`
(no sidecar file), plus `display_name` and `webresourcetype`. The inline form requires
`webresourcetype` when applied (apply cannot infer the type without a file extension).

**Forms** ride along inside each touched entity's projection (ADR 0024) — its seedable
main form is emitted under that entity's `forms:` block (custom-field placement, JS
libraries, event handlers), governed by the ADR 0019 seedable invariant. Non-seedable
form content drops to `warnings`; a non-seedable *whole form* (an additional, non-primary
main form) lands in `skipped`. `data.forms` counts the entities that carried a projected
form.

**Model-driven apps** project as `apps[]` — a top-level, non-entity-rooted pass over the
solution's `appmodule` members (ADR 0024). Each emits `{name, unique_name, description?,
sitemap?}`; the sitemap projects only its **Entity subareas** (a table logical name is
portable, so navigation re-seeds on a fresh org). Gotchas, all governed by the ADR 0019
seedable invariant — never a silent drop:

- A Url / dashboard subarea binds to an org-specific target `apply` cannot re-create → dropped to `warnings`.
- Record-backed component bindings (views / forms / charts / BPFs) bind by org-specific id and are **not** projected → surfaced in `warnings` with a count. Tables reach the app through the sitemap's entity subareas, not these bindings.
- An app whose `unique_name` has no publisher prefix (a first-party app `apply` cannot re-create) → routed to `skipped`.
- A standalone `sitemap` member (type 62) → `skipped`: it is projected under its app, not on its own.

**Skipped bucket** — components that cannot be projected from live metadata (plug-in
assemblies, dashboards, workflows, additional main forms, and other non-seedable types)
land here. The verb **never fails** on an unsupported component (exit 0, `ok: true`) and
never drops one silently.

**Known limitation:** projection is driven by entity members only — a subcomponent member
is NOT resolved to its parent entity. So attribute / view / relationship members **always**
appear in `skipped`; their data is still exported when the parent entity is itself a solution
member (projected in full), and only a lone subcomponent whose parent entity is absent is
genuinely not exported (ADR 0019).

The emitted spec includes a top-level `solution:` key so `apply --dry-run` auto-scopes its
drift/prune report. For a single entity, use `metadata export-spec` (see
`reference/metadata-export.md`); `solution export-spec` composes it across every entity in the
solution.

## Component drift detection — `components --save` / `--diff`

Snapshot and compare solution contents for CI gates or agent branching:

```bash
# Capture the expected inventory (normalized bare JSON list)
crm --json solution components ContosoCore --save components.json
# -> {"ok": true, "data": {"saved": "components.json", "count": 42}}

# Compare live against the snapshot — exit 0 = matches, exit 1 = drift
crm --json solution components ContosoCore --diff components.json
# on match:  {"ok": true,  "data": {"matches": true, "missing": [], "unexpected": []}, "meta": {"matches": true}}
# on drift:  {"ok": false, "data": {"matches": false, "missing": [...], "unexpected": [...]},
#             "error": "Drift detected: 1 missing, 0 unexpected component(s)."}
```

Snapshot/diff entries use the normalized 3-key form: `{"componenttype": <int>,
"objectid": "<guid-lowercase>", "rootcomponentbehavior": <int|null>}`. Components are
keyed on the tuple `(componenttype, objectid, rootcomponentbehavior)` — `missing` = in
expected not live; `unexpected` = in live not expected. **Exits 1 on drift.** The flags
are mutually exclusive; bare `components <name>` (no `--save`/`--diff`) emits the full
OData rows including `componenttypename` (the friendly string for the integer
`componenttype`, e.g. `"entity"`, `"webresource"`, `"sla"`) — so agent code does not
need to maintain its own type-code map.

Add `--resolve` (opt-in; not valid with `--save`/`--diff`) to turn raw `objectid`
GUIDs into names in one pass instead of a lookup per row: each row gains `name` (the
resolved logical/schema name, `null` when the id can't be resolved — never an error),
`rootcomponentbehaviorname` (decodes the `rootcomponentbehavior` int), and, for
entity-scoped types, an `entity` naming the parent table. It costs extra reads (a
batched by-id fan-out plus a bulk attribute-metadata pull), which is why it is off by
default and the bare listing stays byte-for-byte unchanged.

## Cascade / whole-entity drift audit — `audit`

Read-only. Answers "did an `AddRequiredComponents` cascade quietly bloat this
solution?" after the fact — the counterpart to the add-time preview in
`reference/solutions.md` (`add-component`).

```bash
crm --json solution audit ContosoCore
# {"ok": true, "data": {"solution": "ContosoCore",
#   "summary": {"total_components": 42, "entity_count": 5, "whole_entity_count": 2,
#               "shell_count": 3, "required_only_count": 1, "by_type": {"entity": 5, "webresource": 3}},
#   "whole_entities": [{"objectid": "…", "name": "contoso_document", "rootcomponentbehavior": 0,
#                        "behavior_label": "whole-entity (all subcomponents)"}],
#   "shell_entities": [...],
#   "required_only_candidates": [{"componenttype": 1, "type_name": "entity", "objectid": "…",
#                                  "name": "contoso_requesttype", "rootcomponentbehavior": 0,
#                                  "required_by": ["contoso_document"]}]}}
```

Two independent hygiene signals, both scoped to what's already in the solution:
**whole-entity vs shell** buckets `componenttype == 1` rows by
`rootcomponentbehavior` (`0` = whole-entity, where accidental bloat hides; `1`/`2`
= shell) — a hub entity you meant to ship as a shell landing in `whole_entities`
is the tell. **`required_only_candidates`** are components required by another
component in the *same* solution (via `RetrieveRequiredComponents` over the
solution's entities) — `required_by` names the in-solution requirer(s): a strong
signal a row rode in via a cascade rather than being added on purpose, though not
proof of it. Human mode prints the
summary line plus both lists; this only reports drift, it never removes anything.

## Unmanaged-layer conflicts — `layer-conflicts`

Find managed components that *also* carry unmanaged-layer customizations (the
classic source of upgrade surprises). The on-prem detection path that XrmToolBox's
Solution Layers Explorer can't give you — it needs the online-only
`msdyn_componentlayer`; this verb needs only `solutioncomponents`, so it runs
identically on v9.x on-prem and Dataverse online.

```bash
crm --json solution layer-conflicts --solution MyManagedSln --unmanaged-solution MyDevSln
# overlap:  {"ok": true, "data": [{"componenttype": 1, "type_name": "entity",
#            "objectid": "…", "managed_rootcomponentbehavior": 0,
#            "unmanaged_rootcomponentbehavior": 0}], "meta": {"count": 1}}
# none:     {"ok": true, "data": [], "meta": {"count": 0}}   (human: "no conflicts found")
```

The result is the **intersection** of the two solutions' components, keyed on
`(componenttype, objectid)` — `rootcomponentbehavior` is *ignored* for matching (the
row carries both sides' values for inspection). **Always exits 0** when the flags
resolve to the right kinds, conflicts or not — this is a report, not a gate (contrast
`components --diff`, which exits 1 on drift). Kind is validated client-side: a
wrong-kind flag (managed where unmanaged is expected, or vice versa) fails with
`{ok:false}`, exit 1, naming the offending flag — before any comparison.

**Granularity limit:** matching is per solution-component. A table added whole to the
managed solution whose single attribute was customized in the unmanaged solution shows
**no** conflict — the attribute is a separate component (own `objectid`/type).
Subcomponent correlation is out of scope.
