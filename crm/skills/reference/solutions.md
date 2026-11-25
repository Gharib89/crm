# Solutions — create, export, import, validate, failed-import investigation

Create, version, export/import solutions; validate zips offline; investigate a
failed import. Group: `solution`. Flags/choices: `crm solution --help`. Managed
upgrades, uninstall, label translation and the `pac solution` bridge:
`reference/solutions-managed.md`. Drift (`export-spec`, `components --diff`,
`audit`, `layer-conflicts`): `reference/solutions-drift.md`.

## Solution scaffolding — publisher + solution

```bash
crm --json solution create-publisher --name contosopub --display Contoso \
    --prefix contoso --option-value-prefix 30000 --if-exists skip

crm --json solution create --name ContosoCore --publisher contosopub --if-exists skip
```

With a named profile active, `create-publisher` auto-wires `publisher_prefix` back into
it (pass `--no-set-default` to opt out), which only sets the schema-name prefix used to
derive column names. It does **not** set a target solution: every customization write
stays solution-scoped (SKILL.md) and needs its own explicit `--solution <unique_name>`.

Bump the version (or friendly name / description) of an **unmanaged** solution before
exporting — at least one field is required, `--version` is validated as 4-part dotted
numeric pre-HTTP, and managed solutions / patches are rejected client-side:

```bash
crm --json solution set-version ContosoCore --version 1.0.1.0
crm --json solution set-version ContosoCore --friendly-name "Contoso Core" --description "RC build"
```

Add or remove an existing component to/from an **unmanaged** solution
(`AddSolutionComponent` / `RemoveSolutionComponent`). `--type` takes a `componenttype`
integer or a friendly name (case- and separator-insensitive: `entity`=1, `attribute`=2,
`relationship`=3, `optionset`=9, `entityrelationship`=10, `role`=20, `webresource`=61, …; raw int
for anything else). Both refuse managed targets. `remove-component` is destructive
(`--yes` required). Gotcha: `add-component --type entity` with required-components on
(the default) may pull required components into the solution beyond the one you asked
for — the server does not report them in its response (the CLI emits a `meta.note`
reminder). Interactively this cascade is previewed before it happens: if it would pull
in anything, you're shown the list and asked to confirm (`--yes` skips it, same as the
destructive gate above); under `--json` or a non-TTY the prompt is skipped and the
add proceeds, unchanged from before this preview existed — so scripted/agent callers
see no new prompt to handle. The preview is best-effort: if the required-components
lookup itself fails (transient error, permissions), the add proceeds with no preview
or confirmation — the absence of a prompt means "no candidates *or* preview
unavailable", not a guarantee of zero cascade. Audit an existing solution for this drift after the fact
with `solution audit` (`reference/solutions-drift.md`).

```bash
crm --json solution add-component --solution ContosoCore --type webresource --id <guid>
crm --json solution remove-component --solution ContosoCore --type 61 --id <guid> --yes
```

**Batching:** repeat `--id` and/or pass `--components-file <path>` (a JSON
list of rows) — the two sources merge. Two or more components run as one
transactional `$batch` changeset: a mid-batch failure rolls every row back, so a
batch either lands whole or not at all. A single `--id` alone is unaffected — same
one-call envelope as before. File rows are `{"type", "id"}`, plus optional
`"no_add_required"`/`"no_subcomponents"` for `add-component` (overriding the
command-level defaults per row). Any other key — including a `behavior` key, since
neither action takes a `RootComponentBehavior` parameter — is rejected outright,
not silently dropped.

**`--no-subcomponents` is entity-only.** The platform accepts
`DoNotIncludeSubcomponents: true` only on Entity (type 1) roots — sent for any
other type it returns `HTTP 500 … can not be set to true on non Entity root` and
rolls the whole `$batch` back. So the command-level `--no-subcomponents` is an
*entity-scoped* batch default: applied to entity rows, filtered off for every
non-entity row. A realistic mixed-type file (entities + attributes + forms + BPFs)
with `--no-subcomponents --no-add-required` therefore just works — no per-row
guarding needed. Asking for it *explicitly* on a non-entity (a per-row
`"no_subcomponents": true`, or `--id`/`--type` on a non-entity with the flag) is
rejected client-side before any request, naming the offending rows.

On full success the batch `--json` `data` is `{solution, added|removed: [{type,
id, ok, status, error}], count, succeeded, failed, rolled_back}` (all rows `ok`,
`failed: 0`). A rolled-back batch always exits 1 with `ok: false`, but the shape
depends on how the server reports the atomic failure: either the same per-row
`data` with `failed > 0` / `rolled_back: true` (pinpointing the rejected row), or
— the common case — a single `error` like `"$batch failed: HTTP 400: <reason>"`
with no `data`. Either way nothing was added/removed (the changeset is atomic).
`--dry-run` short-circuits the `$batch` itself (the solution-managed pre-flight
GET still runs) and returns `{_dry_run: true, solution, would_add|would_remove:
[{type, id}], count}`.

**Segment system tables; ship new tables whole.** When the solution extends a
**system table** (account, contact, …) or a custom table that already exists in the
target org, add the entity with `--no-subcomponents` and then add only the
subcomponents you actually changed (the specific attributes/forms/views) — pulling
the whole entity in creates needless managed layers and cross-solution dependencies
in every downstream org. The inverse holds for a **brand-new custom table**: it must
travel complete (default, subcomponents included) — a segmented entity whose base
doesn't exist in the target fails import with a missing-dependency error.

## Export a solution

```bash
crm solution list --unmanaged
crm solution info MyCustomSolution            # inspect one solution by unique name
crm solution export MyCustomSolution -o /tmp/snap.zip
# returns {"output": "/tmp/snap.zip", "bytes": 123456, "managed": false, ...}
```

## Import a solution

```bash
crm solution import /tmp/snap.zip --yes
# --no-overwrite keeps existing unmanaged customizations; --no-publish suppresses workflow activation
```

**Gotcha:** the defaults are the aggressive halves — overwrite unmanaged customizations,
activate imported workflows (see `--help` for the boolean pairs). Omitting `--yes` in a
non-interactive context aborts before any upload.

**Gotcha — product-update dependency block.** If the server rejects the import before
processing any components due to a product-update dependency check, pass
`--skip-dependency-check` to set `SkipProductUpdateDependencies` in the request body and
allow the import to proceed past it. Applies to both the async and the on-prem
synchronous-fallback path.

**Gotcha — version ceiling (cloud→on-prem).** A managed zip carries the *package version*
of the org it was exported from, and an org **rejects any zip newer than itself**. A
solution exported from Dataverse online (v9.2) fails to import into on-prem v9.1 with
`0x80048068` / HTTP 400 ("you can only import solutions with a package version of 9.1 or
earlier into this organization"). Promotion must travel **same-or-lower version** — build
on the lowest version in your dev→test→prod chain, or keep all tiers on one platform. This
is distinct from the API-version cap (a `v9.2` *request* → HTTP 501); here it's the
*solution* package version, not the endpoint. `solution validate --against-org` catches
this before the import (it compares `SolutionPackageVersion` against `RetrieveVersion()` —
see "Validate a solution zip before import" below), so pre-flight against the **target**.

**Gotcha:** importing a security **role** from a **managed** solution. On on-prem v9.x
this strips all *manually added* privileges of that role on the target org (privilege-level
*changes* survive; manual *additions* are removed); newer Dataverse online instead *merges*
role privileges and keeps them — per Microsoft Learn, "how managed solutions are merged →
merge security role privileges". Either way, mitigate by managing every update to a given
role from the **same** custom solution; never add privileges directly on the target org,
and don't move a role's updates to a different solution.

The result carries `import_job_id` and `async_operation_id` — capture both; they
drive the investigation workflow below.

## Validate a solution zip before import (CI gate)

Offline static analysis — no connection or profile needed:

```bash
crm solution validate /tmp/snap.zip
# checks: RootComponents<->customizations parity (entities, option sets, web
# resources, dashboards, roles, processes), $webresource: ribbon refs,
# global option-set bindings, well-formed XML, required members present.
# exits non-zero on any error-severity finding.
```

Add `--against-org` to also check for colliding `formid`/`savedqueryid` GUIDs,
BPF process-stage GUIDs (`StageId`/`NextStageId` in `Workflows/*.xaml`, probed
against `processstages` — the `CreateProcessStage` duplicate-key class),
existence of referenced web resources and global option sets, **and the
package-version ceiling** in the target org (requires a connection/profile). Use
before `solution import`.

**`--against-org` now enforces the version ceiling.** It reads the package's
`SolutionPackageVersion` and compares it to the org version (`RetrieveVersion()`):
a package newer than the org (even a newer minor) is an **error** finding —
import would fail with `0x80048068`. So a green `--against-org` validate now
clears that ceiling, not just GUID/ref collisions. The check is best-effort —
an absent/unparseable package version or an unreadable org version degrades to a
warning/skip and never falsely flips the report invalid; the offline (no
`--against-org`) run does **not** check it. Offline or not, a package exported
from CRM 2011 or earlier (`minimumversion` 5.x) is a `package-era` error: every
9.x and online org refuses it.
Against an on-premises profile `--against-org` also errors on an online-built
package (`generatedBy="CrmLive"`), which on-premises refuses at import; no `crm`
command retargets it, so rebuild from an on-premises export. And
run `--against-org` only against the **import target**, never the export source —
validating against the source always reports `formid`/`savedqueryid` collisions,
because those components already live there.

## Investigating a failed import

Work the timeline in order — gate, monitor, post-mortem, verify:

1. **Before** — `solution validate <zip>`, plus `--against-org` against the
   target (previous section). Offline findings are cheaper than a failed import.
2. **During** — `crm --json solution job-status <id>` (alias for
   `crm async get`) polls the import's async operation; the id is the import
   envelope's `async_operation_id`. Didn't capture it? `crm --json async list`
   finds the operation. The `async` group works on **any** asyncoperation, not
   just imports — `crm async cancel <id> --yes` (alias: `solution job-cancel`)
   kills a pending/suspended job.
3. **After** — `crm --json solution import-result <id>` re-fetches the
   ImportJob of any prior import and parses per-component pass/fail outcomes;
   the id is the import envelope's `import_job_id` — present **once the import job
   starts**. A pre-execution rejection (the version-ceiling gate above, or a declared
   missing dependency that fires at entry) returns an *error* envelope with no job id and
   `import-result` 404s — that case is "rejected before execution", not
   "executed-but-failed". Parsing is best-effort: a missing or unparseable ImportJob data
   column degrades to a `meta.warnings` note, never an error.

**On-prem caveat.** On-prem orgs import via the synchronous `ImportSolution`
action (`action: "ImportSolution"` in the import envelope): no progress ticks,
the request blocks until the import finishes, there is no async operation to
poll (`async_operation_id` is null), and a **declared** missing dependency
fails the import loudly (fault `0x80048033`). Two evidence holes remain:
per-component results may still be unavailable (the import's `meta.warnings`
says why), and an import whose components carry **undeclared** dangling
references can report success while leaving broken state — only read-back
catches it.

**Fallback verification — confirm components actually landed.** When
`import-result` has nothing, or a clean result needs corroborating, read the
target back:

```bash
# snapshot taken from the SOURCE org before export (see `reference/solutions-drift.md`)
crm --json solution components MySolution --diff expected.json
crm --json metadata entity new_widget       # spot-check key components exist
```

## Check what an exported solution needs before importing (`missing-components`)

Run against the **import target** org before importing. An empty result means the
org already has everything the solution needs — go ahead with `solution import`.
A non-empty list names what must be installed first (or the import will fail with
a dependency fault).

```bash
crm --json solution missing-components ./MySolution.zip
# → {ok, data:[<missing components>], meta:{count}}
```

The argument is a **path to an exported `.zip`** (not a unique name — the API
requires the file bytes). Read-only; fires under `--dry-run` too.

**Gotcha — URL-length limit.** The bytes ride in the query string as a parameter
alias. Excessively large zips can hit the server's URL-length ceiling (inherent
to `RetrieveMissingComponents`, not the CLI). If you hit it, pre-validate
offline with `solution validate` and split the solution if it is genuinely too large.
