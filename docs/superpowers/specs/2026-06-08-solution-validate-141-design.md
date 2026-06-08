# Design — `crm solution validate <zip>` (issue #141)

**Date:** 2026-06-08
**Issue:** [#141](https://github.com/Gharib89/crm/issues/141) — `feat: crm solution validate <zip> — offline pre-import checks`

## Problem

Building a solution zip by hand and importing it is a slow guess-and-check loop. Each
failed import is a full async round-trip (~30s) that surfaces **one** error at a time. A
single clone this session took 4 import attempts, each revealing the next problem:

1. `component cwx_slatier of type 9 is not declared in the solution file as a root component` — option set present in the `<optionsets>` node but missing from `<RootComponents>`.
2. `The label 'ColorStrip', id: '...' already exists. Supply unique labelid values.` — cloned form-element `id="{...}"` GUIDs collided with GUIDs already in the org.
3. `component {...} of type 60 is not declared in the solution file as a root component` — stray dashboard left in `<InteractionCentricDashboards>`.
4. success.

## Goal

`crm solution validate <zip>` — a static analyzer that reports **all** discoverable problems
in one pass instead of one-per-round-trip. Offline by default (no connection). An opt-in
`--against-org` flag adds the online checks that require knowing what already exists in the
target org.

## Scope decisions (confirmed with user, 2026-06-08)

- **v1 ships offline checks AND `--against-org`.** Not deferred.
- **Issue class #2 (label/GUID collision) is reported only under `--against-org`.** Offline
  cannot know what exists in the org, so the offline pass does not attempt this class. The
  acceptance "all 3 classes in one pass" is therefore satisfied by `validate --against-org`
  (2 offline parity classes + 1 online collision class).

## Architecture

New strict core module **`crm/core/solution_validate.py`** (pyright strict; `crm/core/*` is
strict). Rationale: `crm/core/solution.py` is already ~1000 lines; a separate, single-purpose
module is easier to test in isolation and keeps the parser/checker logic together. Reuses the
`zipfile` + `xml.etree.ElementTree` pattern already proven in
`solution.py::_sniff_solution_managed`, and reuses `SOLUTION_COMPONENT_TYPES` for the
node→component-type map.

New command **`crm solution validate`** in `crm/commands/solution.py`, mirroring the offline
`extract`/`pack` commands for the no-flag path (never calls `ctx.backend()`), and the online
`import` command (acquires `ctx.backend()`) only when `--against-org` is given.

### Core API

```python
@dataclass(frozen=True)
class Finding:
    severity: str        # "error" | "warning"
    check: str           # "xml" | "root-parity" | "webresource-ref" | "optionset-binding" | "guid-collision"
    message: str         # human-readable, includes the failing component/id
    component: str | None = None   # e.g. "cwx_slatier"
    location: str | None = None    # e.g. "customizations.xml/<optionsets>"

def validate_solution(
    zip_path: str | Path,
    *,
    backend: D365Backend | None = None,
) -> dict[str, Any]:
    """Static analysis of a solution package.

    Returns {"valid": bool, "findings": [Finding-as-dict, ...], "checks_run": [str, ...]}.
    `valid` is False iff any finding has severity "error".
    When `backend` is provided (only for --against-org), also runs the online
    collision/existence checks. Raises D365Error on unreadable/non-zip input or on
    a failed org query.
    """
```

### Checkers

Each checker is a focused function `(...parsed XML...) -> list[Finding]`. `validate_solution`
parses the package once, runs every applicable checker, concatenates findings, and computes
`valid`.

| check (`Finding.check`) | offline | online (`--against-org`) | catches |
|---|---|---|---|
| `_check_well_formed` | yes | — | `solution.xml` / `customizations.xml` missing or unparseable. **Fatal**: returns the finding and skips the remaining checks (cannot analyze unparseable XML). |
| `_check_root_parity` | yes | — | a component present in a `customizations.xml` node (`<optionsets>`, `<InteractionCentricDashboards>`, `<WebResources>`, `<Entities>`, …) but missing from `solution.xml` `<RootComponents>`, **and** a `<RootComponent>` with no backing definition in `customizations.xml`. Covers issue class #1 (optionset/type 9) and #3 (dashboard/type 60). |
| `_check_webresource_refs` | yes | resolves against org too | `$webresource:NAME` tokens in `RibbonDiffXml` that do not resolve to a web resource in the package (and, with backend, not in the org either). |
| `_check_optionset_bindings` | yes | resolves against org too | a picklist attribute referencing a global option set that is not declared in `<optionsets>` (and, with backend, does not exist in the org). |
| `_check_org_collisions` | — | yes | `formid` / `savedqueryid` / form-element labelid GUIDs that already exist in the target org. Covers issue class #2. |

### Parity logic

`customizations.xml` root is `<ImportExportXml>` with child container nodes
(`<Entities>`, `<optionsets>`, `<Roles>`, `<Workflows>`, `<InteractionCentricDashboards>`,
`<WebResources>`, …). `solution.xml` carries `<RootComponents>` with entries like
`<RootComponent type="9" schemaName="cwx_slatier" />` (`type` + `schemaName`, or `id`).

`_check_root_parity` builds two sets keyed by `(type:int, name)`:
- from `customizations.xml`: map each container node to its component-type int via an explicit,
  extensible `NODE_COMPONENT_TYPE` table (built from `SOLUTION_COMPONENT_TYPES`), reading each
  child's identifying attribute (`schemaName` / `Name` / `name`).
- from `solution.xml` `<RootComponents>`: `(int(type), schemaName-or-id)`.

Report orphans in both directions as `error` findings, each naming the component and the
direction.

### Command wiring

```python
@solution_group.command("validate")
@click.argument("zip_path", type=click.Path(exists=True, dir_okay=False))
@click.option("--against-org", is_flag=True,
              help="Also run online checks against the connected org "
                   "(GUID collisions, web-resource & option-set existence). "
                   "Requires a connection/profile.")
@pass_ctx
def solution_validate_cmd(ctx: CLIContext, zip_path, against_org):
    """Statically validate a solution zip before import (offline; --against-org adds online checks)."""
    backend = ctx.backend() if against_org else None
    try:
        report = sol_mod.validate_solution(zip_path, backend=backend)
    except D365Error as exc:
        _handle_d365_error(ctx, exc)
        return
    ctx.emit(report["valid"], data=report)   # ok=False ⇒ exit 1 (ADR 0001)
```

Exit code: any `error`-severity finding ⇒ `valid=False` ⇒ `emit(False)` ⇒ exit 1. A clean
package, or one with only `warning` findings, ⇒ exit 0.

## Testing

The three real broken packages are not in the repo, so tests build minimal fixtures with a
`_make_pkg(solution_xml, customizations_xml)` helper (writes both members into a zip, same
shape as the existing `_make_solution_zip` helper). Online checks are mocked with
`requests_mock`, matching the existing solution tests.

Fixtures / cases:
- `bad_optionset` — optionset in `<optionsets>`, absent from `<RootComponents>` ⇒ one
  `root-parity` error (class #1), exit non-zero.
- `bad_dashboard` — dashboard in `<InteractionCentricDashboards>`, absent from
  `<RootComponents>` ⇒ one `root-parity` error (class #3), exit non-zero.
- `bad_label` + mocked org ⇒ one `guid-collision` error (class #2) under `--against-org`.
- `all_three` + mocked org ⇒ a single `validate --against-org` pass reports all three classes,
  `valid=False`, exit non-zero. **(primary acceptance test)**
- `good` ⇒ `valid=True`, no findings, exit 0. **(acceptance)**
- Unit coverage per checker: well-formedness fatal-short-circuit; parity both directions;
  `$webresource:` unresolved vs resolved; optionset binding declared vs missing.

## Docs (shipped in the same change — CLAUDE.md "keep docs in sync")

- **README.md** — add `validate` to the solution capabilities.
- **docs/how-to/solution.md** — how-to for `validate` and `--against-org`.
- **docs/reference/cli.md** — CLI reference entry.
- **crm/skills/SKILL.md** — add `validate` (skill ↔ CLI parity).
- Commit subject `feat(solution): …` ⇒ semantic-release cuts a minor bump.

## Out of scope (YAGNI)

- No auto-repair / rewrite of broken packages.
- No managed-solution-specific rules beyond well-formedness + parity.
- Only `solution.xml` and `customizations.xml` are inspected (the two files where these errors
  live); not `[Content_Types].xml`, assemblies, or web-resource file contents.
