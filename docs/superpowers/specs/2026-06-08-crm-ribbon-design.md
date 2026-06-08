# Design: `crm ribbon` command group (issue #142)

**Date:** 2026-06-08
**Issue:** [#142](https://github.com/Gharib89/crm/issues/142) — `feat: crm ribbon command group (add/list/remove buttons; ribbon export)`
**Status:** Approved design, pending implementation plan
**Depends on:** #140 (import `--wait` fix, CLOSED), #141 (`solution validate`, CLOSED) — both landed.

## Problem

Adding a single command-bar (ribbon) button to a D365 entity currently requires a full manual round-trip: `crm solution export` → unzip → hand-edit the entity's `RibbonDiffXml` in `customizations.xml` (inject `<CustomAction>` + `<CommandDefinition>` with `<JavaScriptFunction Library="$webresource:..." FunctionName="...">` and `<CrmParameter>`) → discover valid group IDs → repack → import → publish.

Reading the current ribbon is also awkward. Two undocumented sharp edges cost time:

1. `RetrieveEntityRibbon`'s `RibbonLocationFilter` rejects an int (`7`) and requires the string member `"All"`.
2. The returned `CompressedEntityXml` is a **ZIP** (PK header), not gzip.

## Goal

A `crm ribbon` command group that adds, lists, removes, and exports entity command-bar buttons with no manual XML editing.

Acceptance criteria (from the issue):
- `crm ribbon add-button cwx_ticket --label Validate --location form --webresource ... --function ... --param PrimaryControl` produces a working button with no manual XML editing.
- `crm ribbon export cwx_ticket` returns readable ribbon XML directly.

## Decisions (resolved during brainstorming)

| Topic | Decision |
|-------|----------|
| Apply mechanism (write verbs) | **User-supplied `--solution <name>`**: export that whole solution, edit the entity's `RibbonDiffXml`, reimport the whole solution + publish. Matches the proven manual dance. Reuses #140 import + #141 validate. |
| Locations | **form, homegrid, subgrid**, each with a default target group, plus an optional **`--group <id>`** override. |
| Scope | **All four verbs in one PR**, one `feat:` commit, one minor bump. |
| `list` source | Reads the **solution's `RibbonDiffXml`** (requires `--solution`) so its IDs share one space with `add-button`/`remove`. `export` is the only verb with no `--solution`. |
| ID scheme | Human-readable, deterministic: `<entity>.<location>.<slug(label)>` base, suffixed `.CustomAction`/`.Button`/`.Command`. No random GUIDs. |

## Architecture

New domain `ribbon`, mirroring existing command groups.

- **`crm/core/ribbon.py`** — all logic; pyright **strict** (it is a `core/*` module).
  - `retrieve_entity_ribbon(backend, entity, location="All") -> ET.Element` — the read primitive (decode below).
  - parse / enumerate custom buttons from a `RibbonDiffXml` element.
  - mutate: inject / remove `<CustomAction>` + `<CommandDefinition>`.
  - apply: `export_solution` → edit `customizations.xml` → repack → `validate_solution` → `import_solution` → `publish_all`.
- **`crm/commands/ribbon.py`** — thin Click wrappers (`export` / `list` / `add-button` / `remove`).
- **`crm/cli.py`** — one-line registration in the `_lazy_commands` dict (`crm/cli.py:273`): `"ribbon": "crm.commands.ribbon:ribbon_group"`.

Reuses, no changes needed to them:
- `export_solution` (`crm/core/solution.py:556`), `import_solution` (`:685`), `publish_all` (`:970`).
- `validate_solution` (`crm/core/solution_validate.py:323`); the `RibbonDiffXml` / `$webresource:` ElementTree pattern (`:159`).
- `resolve_webresource_id` (`crm/core/webresource.py:219`).
- `D365Backend.get/post` (`crm/utils/d365_backend.py:659`/`:662`).

One new core module + one new command module + one registration line. No edits to existing modules' behavior.

## Read path — `export` + `list`

The decode, isolated in `retrieve_entity_ribbon`:

```
GET RetrieveEntityRibbon(EntityName=@p,RibbonLocationFilter=@f)
    ?@p='cwx_ticket'
    &@f=Microsoft.Dynamics.CRM.RibbonLocationFilter'All'   # string member, NOT int 7
-> resp["CompressedEntityXml"]  (base64)
-> base64.b64decode -> bytes starting PK\x03\x04            # ZIP, NOT gzip
-> zipfile.ZipFile(BytesIO(...)) -> read XML member(s)
-> ElementTree
```

**`crm ribbon export <entity> [--output FILE]`** — pretty-prints the composed ribbon XML to stdout, or writes to `--output`. No `--solution`. Satisfies "returns readable ribbon XML directly" and is the debugging tool.

**`crm ribbon list <entity> --solution NAME`** — exports the solution, parses the entity's `RibbonDiffXml`, prints a table of custom buttons:

```
button-id │ label │ location │ command │ function │ library
```

`list` reads the same `RibbonDiffXml` that `remove` edits, so the IDs it prints are exactly what `remove --button-id` accepts.

## Write path — `add-button` + `remove`

Shared flow:

```
export_solution(--solution, managed=False) -> unzip to temp dir
parse customizations.xml -> locate <Entity><Name>{entity}</Name> ... <RibbonDiffXml>
mutate RibbonDiffXml (add or remove)
repack zip -> validate_solution(zip) -> import_solution(--wait) -> publish_all
```

If the entity has no `RibbonDiffXml`, create the skeleton: `<RibbonDiffXml><CustomActions/><Templates>...<CommandDefinitions/><RuleDefinitions>...<LocLabels/></RibbonDiffXml>`.

### `add-button` injects three nodes

- `<CustomAction Id={base}.CustomAction Location={group} Sequence={N}>` → `<CommandUIDefinition>` → `<Button Id={base}.Button Command={base}.Command LabelText={label} .../>`
- `<CommandDefinition Id={base}.Command>` → `<EnableRules/><DisplayRules/><Actions>` → `<JavaScriptFunction Library="$webresource:{webresource}" FunctionName="{function}"><CrmParameter Value="{param}"/></JavaScriptFunction>`

**ID scheme** (deterministic, human-readable):

```
base = {entity}.{location}.{slug(label)}        e.g. cwx_ticket.form.Validate
CustomAction Id = {base}.CustomAction
Button Id       = {base}.Button
Command Id      = {base}.Command
```

Overridable with `--id BASE`. Collision check: if any `{base}.*` ID already exists in the `RibbonDiffXml`, error and suggest a different `--label`/`--id`.

**`--location` → default group** (exact group IDs confirmed against the live org during build; `--group` overrides):

| `--location` | default group |
|--------------|---------------|
| `form` | `Mscrm.Form.{entity}.MainTab` (Save-area group) |
| `homegrid` | `Mscrm.HomepageGrid.{entity}.MainTab` (Management group) |
| `subgrid` | `Mscrm.SubGrid.{entity}.MainTab` |

**`--param`** → `<CrmParameter Value="...">`: `PrimaryControl` (form) or `SelectedControlSelectedItemIds` (grid). **`--sequence N`** optional.

**Pre-flight:** `add-button` resolves `--webresource` via `resolve_webresource_id`; errors early if the JS web resource is not in the org/solution (otherwise the `$webresource:` bind dangles — the same condition `validate` flags).

### `remove <entity> --solution NAME --button-id ID [--yes]`

Deletes the matching `<CustomAction>` and its orphaned `<CommandDefinition>` from the `RibbonDiffXml`; same repack → validate → import → publish. Destructive verb → `click.confirm` + `--yes` skip (the established crm destructive pattern). If `--button-id` is not found, error and list the available IDs.

## CLI surface

```bash
crm ribbon export <entity> [--output FILE]                    # composed ribbon XML, no --solution
crm ribbon list <entity> --solution NAME                      # custom-button table
crm ribbon add-button <entity> --solution NAME --label L \
    --location form|homegrid|subgrid [--group ID] \
    --webresource cwx_/scripts/x.js --function ns.fn \
    --param PrimaryControl|SelectedControlSelectedItemIds \
    [--sequence N] [--id BASE]
crm ribbon remove <entity> --solution NAME --button-id ID [--yes]
```

All verbs accept the global connection flags inherited through the group, like the other command modules.

## Error handling

Fail early with a clear message; never swallow:

- `RetrieveEntityRibbon` decode shape mismatch → wrap with the enum/PK-zip gotcha hint.
- `--webresource` not in org/solution → error before import.
- `--button-id` not found → error listing available IDs.
- `add-button` ID collision → error, suggest `--id`/different label.
- import / validate failures bubble up from the existing #140 / #141 paths.

## Testing

- **Unit (offline — the bulk):**
  - decode: base64 → PK-zip → XML fixture → assert parse.
  - `add-button` / `remove` mutate a fixture `customizations.xml` → assert exact `RibbonDiffXml` nodes and IDs.
  - collision + missing-button errors.
  - `--param` / `--location` / `--group` mapping.
- **E2E (live, `.env`-gated like existing E2E):** round-trip `add-button` → `list` shows it → `remove` → gone. Skipped without creds.
- **Manual (live D365 UI):** button renders and fires on the form/grid — the "working button" criterion that only the UI confirms.

## Docs (same PR — CLAUDE.md sync rule)

- `README.md` — capability line.
- `docs/how-to/ribbon.md` — new how-to.
- `crm/skills/SKILL.md` — ribbon section.
- `docs/reference/cli.md` — auto-renders from the Click tree via mkdocs-click; write good docstrings/help, do not hand-add entries.
- `CHANGELOG.md` — untouched; `python-semantic-release` owns it. Squash subject: `feat(ribbon): add crm ribbon command group (export/list/add-button/remove)`.

## Out of scope

- Application / global ribbon (this is entity-scoped only).
- Enable/display rules beyond the default empty set.
- Ribbon for views / dashboards.
