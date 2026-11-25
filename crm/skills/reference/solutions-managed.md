# Solutions — managed upgrades, uninstall, translation, source control

Patch, upgrade and uninstall managed solutions; translate display labels; bridge
to `pac solution` for source control. Group: `solution` (plus `translation`).
Flags/choices: `crm solution --help`. Create, export, import and validate:
`reference/solutions.md`. Drift: `reference/solutions-drift.md`.

## Managed-solution upgrade lifecycle — `clone-as-patch` / `stage-and-upgrade` / `apply-upgrade` / `uninstall`

First-class verbs wrapping the `CloneAsPatch`, `ImportSolution` (`HoldingSolution`),
`DeleteAndPromote`, and solution-delete server actions — so you never drop to
`crm action invoke CloneAsPatch/DeleteAndPromote` (agent-infeasible). They all
work on **both** on-prem v9.x and Dataverse online.

```bash
# Small hotfix on top of a parent: clone a patch (version revision auto-bumps from the parent)
crm --json solution clone-as-patch --solution ContosoCore
# → {cloned, parent_solution, version, patch_solutionid}

# Major upgrade: stage the new managed zip as a HOLDING solution (not yet live)…
crm solution stage-and-upgrade /tmp/contoso_2_0.zip --yes
# …then apply it (DeleteAndPromote replaces the base + its patches) — one-shot:
crm solution stage-and-upgrade /tmp/contoso_2_0.zip --promote --solution ContosoCore --yes
# …or promote a separately-staged holding solution later (after verifying it):
crm solution apply-upgrade ContosoCore --yes

# Remove a solution (managed base also removes its patches, server-side)
crm solution uninstall --solution ContosoCore --yes
```

**Workflow:** a holding import alone does **not** make the upgrade live — it stages
and validates. Either promote in the same call (`stage-and-upgrade --promote`) or
stage now and run `apply-upgrade <name>` later — both fire the same `DeleteAndPromote`
(the destructive step that deletes the old base). The decoupled shape lets you stage,
verify with `solution import-result <id>`, then promote separately.

**Gotchas:**
- `clone-as-patch` auto-bump increments the **revision** (4th part); a patch must
  keep the parent's `major.minor`. Pass `--version` for a specific build/revision.
- `stage-and-upgrade` reuses the `solution import` pipeline — same per-component
  result parsing, the on-prem synchronous-`ImportSolution` fallback, and
  `meta.warnings` on a partial failure all apply. The two `import` escape hatches
  carry over too: `--skip-dependency-check` (past a product-update dependency block)
  and `--formatted` (attach the Excel report under `formatted_results`).
- `uninstall` **pre-checks** `RetrieveDependenciesForUninstall` and refuses (no
  DELETE) when blockers exist; `--force` skips the check. Inspect blockers first
  with `solution dependencies <name>`.
- A large `apply-upgrade` / `--promote` can outlast the request timeout. The call is
  not re-sent: the verb waits (`--timeout`, else the profile's `async_timeout`) until
  the base name resolves to a new solution id, then returns the usual envelope. Error
  code `PromoteStillRunning` means the promote may still be running or failed; read
  `solution list` before any re-run.
- All three are gated destructive verbs (`--yes`) except `clone-as-patch` (a
  create). `--dry-run` previews every one without mutating.

## Preview what blocks uninstalling a managed solution

```bash
crm --json solution dependencies ContosoCore
```

The solution-scoped counterpart to `metadata dependencies` (see
`reference/metadata.md`): calls `RetrieveDependenciesForUninstall(SolutionUniqueName=
'<name>')` and returns `{solution, blockers[], count}`, each blocker shaped like the
metadata-dependency blockers. Read-only; the GET fires under `--dry-run`. Unknown
solution name → clean `{ok:false}`. Use this for "what stops me uninstalling solution
X?"; use `metadata dependencies` for a single component.

## Translate display labels — `crm translation export` / `import`

Translation is **solution-scoped** (the `ExportTranslation` / `ImportTranslation`
actions take a solution, not an entity) — to translate one entity, put it in a
solution. Dual-target: on-prem v9.x and Dataverse online. Group: `translation`.
Flags/choices: `crm translation --help`.

```bash
crm --json translation export --solution ContosoCore -o labels.zip
# labels.zip = CrmTranslations.xml (Excel-openable spreadsheet) + [Content_Types].xml;
# translator adds a column per language code (e.g. 1034) and fills it in

crm --json translation import labels.zip --yes            # re-zipped edited files, NOT the bare XML
crm --json solution publish-all                           # labels do NOT surface until published

# One-step: import + publish in a single call (PublishAllXml after import)
crm --json translation import labels.zip --yes --publish
```

`--publish` runs `PublishAllXml` immediately after a successful import, so
labels surface without a separate `solution publish-all` step. Default off —
omitting it leaves the existing behavior (import only, `meta.warnings` publish
reminder). Under `--dry-run` the import is previewed and publish is skipped.

Gotchas: import fails on any translated string **>500 chars**; labels for
languages **not provisioned** on the target are discarded with a warning;
customization happens only in the base language. The import envelope carries
`import_job_id` (results via `crm solution import-result <id>`) and a
`meta.warnings` publish reminder.

Do **not** reach for `crm action invoke ExportTranslation` — it returns the zip
as a base64 blob inside the JSON body (manual decode + unpack); the
`translation` verbs are that plumbing. The GUI alternatives are the native
**Translations → Export/Import translations** menu and XrmToolBox
**Easy Translator** (community tool, on-prem + online).

## Source control — `pac solution` bridge

Put a solution under source control with the offline `pac solution unpack`/`pack`
bridge (no connection/profile needed; resolves the exe via `--pac-path` →
`CRM_PAC` → PATH — the old `--solutionpackager-path`/`CRM_SOLUTIONPACKAGER` names
still work as deprecated aliases — else errors pointing at the cross-platform
Power Platform CLI install — `dotnet tool install --global Microsoft.PowerApps.CLI.Tool`).
**`git diff` on the extracted tree IS the solution diff:**

```bash
crm solution extract --zipfile /tmp/snap.zip --folder src/MyCustomSolution
# ...commit + review git diff, then rebuild a zip from the tree
crm solution pack --zipfile dist/built.zip --folder src/MyCustomSolution
# a non-zero pac exit fails the command;
# envelope: {action, exit_code, folder, zipfile, stdout_tail}
```

**No zip-to-zip managed conversion.** Managed-vs-unmanaged is fixed at *export*
time — `pack`'s package-type option only selects which flavor to rebuild from a
tree that was extracted from that flavor; it cannot synthesize a managed build
from an unmanaged export. The path to a managed zip is the round-trip: import the
unmanaged zip into a dev org, then `solution export --managed` from there.
