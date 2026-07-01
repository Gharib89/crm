# Customization lifecycle — end to end

The connective tissue for schema-and-component work: the **order** customizations go in,
the disciplines that span domains, and how a change rides from one environment to the
next. Per-command flags and per-domain depth live in the sibling files linked below —
this file is the map, not the reference. Start here when the task is "customize this org"
and you don't yet know which group to reach for.

D365 / Dataverse ALM (MS Learn "Use a solution to customize", "ALM basics with Power
Platform"): build in a **dev** org inside your **own unmanaged solution**, export it **as
managed**, import that managed zip into **test** then **prod**. Never customize in the
default solution/publisher (wrong prefix, can't export), and never hand-edit a managed
component on a downstream org — that active unmanaged layer silently blocks future managed
updates.

## The shape of a customization job

Canonical order; each step's detail lives in the linked file:

1. **Publisher + solution first.** `solution create-publisher`, then `solution create` —
   name this solution explicitly (`--solution <unique_name>`) on every component you
   author next; nothing inherits it automatically. → `reference/solutions.md`
2. **Author schema.** Tables, columns, option sets, relationships — declaratively with
   `apply -f` / `scaffold table`, or imperatively with the `metadata create-*` verbs.
   → `reference/authoring.md`, `reference/metadata.md`
3. **UI layer.** Views (`view create`), forms (`form`), web resources (`webresource`),
   ribbon buttons (`ribbon`), model-driven app + sitemap (`app`).
   → `reference/authoring.md` (views), `reference/customizations.md` (the rest)
4. **Automation.** Plug-in assembly + steps (`plugin`), workflows (`workflow`), SLAs
   (`sla`). → `reference/automation.md`
5. **Publish once**, then **verify it landed** (disciplines 3–4 and 6 below).
6. **Package + promote.** export (managed) → validate → import to the next org → verify.
   → `reference/solutions.md`

**Dependency order bites.** A ribbon button needs its web resource to exist first; a new
table is invisible in an app until a sitemap subarea references it; a plug-in step needs
its assembly registered and its type confirmed first. The sibling files call out each.

## Cross-cutting disciplines (the things that bite)

**1 — `--solution <unique_name>` is mandatory on every customization write.** There is no
profile default and no opt-out: omit it and the command exits 2 before touching the
backend (even under `--dry-run`) with "`--solution is required for customization writes`".
This spans every domain in the shape above — `metadata create-*`/`update-*`/`delete-*`,
`apply` (a spec-level `solution:` block, not a flag — discipline 5), `scaffold table`,
`webresource`, `form`, `view`, `chart`, `dashboard`, `sitemap`, `app`, `plugin
register-*`/`update-step-*`, `sla`, `report`, `connectionrole`, `dup`, `fieldsec`,
`security create-role`, `ribbon`, `workflow clone`. Pass `--solution Default` for a
deliberate Default-Solution-only write. With a named profile active,
`solution create-publisher` still auto-wires `publisher_prefix` onto it (schema-name
derivation only) — that does not cover the solution target.

**2 — Publisher + solution before anything.** `solution create-publisher`, then
`solution create`, before authoring any component — then pass that solution's unique name
on every write in discipline 1. Skip this and there is nowhere valid to point `--solution`
at.

**3 — Stage many, publish once.** Each `metadata create/update` auto-publishes, and a
publish is slow and disruptive. Across a batch, set `--stage-only` (or `CRM_STAGE_ONLY=1`)
on every write, then run `solution publish-all` **once** at the end. This matters beyond
speed: **only published customizations export** — an unpublished change silently drops out
of the solution zip. → `reference/authoring.md`

**4 — Publish-before-read, then poll.** A just-published metadata change takes a beat to
propagate. Gate the read with `metadata ... --expect ATTR=VALUE` and retry on mismatch
rather than trusting an immediate read. → `reference/metadata.md`

**5 — `apply -f` vs imperative.** Use `apply -f spec.yaml` (or `scaffold table`) to stand
up a whole table idempotently in dependency order with a single publish — re-applying an
unchanged spec is a no-op, and `metadata export-spec` round-trips a live entity back into
a spec for the next environment. Use the imperative `metadata create-*` verbs for a single
targeted change to an existing table. `apply`'s mandatory solution target (discipline 1)
is a top-level `solution: {unique_name: ...}` block in the spec itself, not a `--solution`
flag; `metadata export-spec --solution <name>` bakes that block in on export.
→ `reference/authoring.md`

**6 — `--dry-run` first on every write.** Reads still fire under dry-run, so a preview
reports live facts (`_exists`, `would_skip`) not guesses; `meta.dry_run: true` flags it.
Validate a record payload with `entity ... --validate` before committing — but note
`--validate` is **record-write-only** (`entity create`/`update`): `metadata create-*`,
`solution`, and component writes have **no `--validate`** (their typed flags are checked
client-side), so `--dry-run` — plus `--expect` after publish — is their pre-write net.

## Promote to the next environment

```bash
crm solution set-version MyApp --version 1.2.0.0           # bump (unmanaged only)
crm solution publish-all                                   # unpublished == not exported
crm solution export MyApp -o myapp_managed.zip --managed   # ship downstream orgs MANAGED
crm solution validate myapp_managed.zip --against-org      # offline + live pre-flight
crm solution import myapp_managed.zip --yes                # into test, then prod
crm --json solution components MyApp --diff expected.json  # verify the target landed
```

On-prem imports run synchronously (no async job to poll); cloud returns an async op to
watch. A failed import has a full post-mortem path (`solution import-result`,
`job-status`), and the managed-upgrade verbs (`clone-as-patch`,
`stage-and-upgrade --promote`, `uninstall`) handle the patch / holding-solution dance.
→ `reference/solutions.md`

**Version ceiling — promote down a same-or-lower version path.** A managed zip carries the
*package version* of the org it was built in, and an org rejects any zip newer than itself
— a cloud (v9.2) export will **not** import into on-prem v9.1 (`0x80048068`). `solution
validate --against-org` now catches it pre-import (it compares the package's
`SolutionPackageVersion` against the target org's version); the offline `validate` is still
structural and does not. Build on the lowest version in your dev→test→prod chain, or keep
every tier on one platform.

## CI deploy spine — profile-per-tier, exit codes as gates

The promote flow above assumes one **active** profile. A CI pipeline instead targets a
different org per **tier** in one non-interactive run: the global `crm --profile <tier>`
(before the subcommand) picks the target, and each verb's **exit code gates the next
step** — any non-zero aborts the chain, so plain `&&` is the whole guard. On-prem gets the
pipeline experience Microsoft ships only for cloud (Pipelines/PPAC).

```bash
# Build the artifact once — export from dev, OR pack a source-controlled tree (offline, no profile)
crm --profile dev solution export MyApp -o myapp.zip --managed
crm solution pack --zipfile myapp.zip --folder src/MyApp        # alternative source — the pac bridge

# Ship the SAME zip up the tiers (test shown; prod is the identical two lines with --profile prod)
crm --profile test solution validate myapp.zip --against-org && \  # gate: version ceiling + ref/GUID collisions
crm --profile test solution import  myapp.zip --yes               # import activates imported workflows
crm --json --profile test solution components MyApp --diff expected.json  # exit 1 = drift ⇒ fail the build
```

- **Gates are exit codes** — `validate`, `import`, `components --diff` (1 on drift), and a
  non-zero `pac` all fail the command; no `--json` needed except to parse the diff.
- **Workflow activation rides on `import`** — run a standalone `workflow activate <id>` only
  for processes imported with `--no-publish`, or ones authored outside the solution.
- `--against-org` goes on each **target**, never the export source. → `reference/solutions.md`

## Tearing down — reverse the build order

Decommissioning undoes the build order **in reverse**: automation and UI components first,
then schema, then the solution/publisher that held them. The platform enforces the
dependency edges and the error code names the one you hit — a web resource won't delete
while a ribbon button references it (`0x8004f01f`); a model-driven app won't delete via a
bare `entity delete appmodules` while an `appsetting` row points at it (`0x80048d21`, on
both on-prem and online) — use `app delete <name|id>`, which sweeps those FK-blocking
dependents first (and refuses a managed app). The bulk shortcut: **delete the unmanaged
solution** and let the server cascade most components, then drop the global option sets and
publisher last; a custom table's `metadata delete-entity` cascades its own
columns/relationships/views/forms in one shot. → `reference/customizations.md`

## Don't reach past the API

Some customizations have **no Web API write path**, and the CLI says so rather than faking
it: ribbon `RibbonDiffXml` (solution-zip pipeline only — that's why a cloned entity's
ribbon doesn't come across), and early-bound class generation (external `CrmSvcUtil.exe` /
`pac modelbuilder`, never a `crm` verb).
→ `reference/customizations.md`, `reference/automation.md`
