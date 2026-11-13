---
name: crm
description: Operate Microsoft Dynamics 365 Customer Engagement, on-premises (v9.x, NTLM) or Dataverse online (OAuth), from the shell. Use for record CRUD, OData/FetchXML queries, metadata, solutions, forms, sitemaps, dashboards, ribbons, web resources, plug-ins and workflows, security roles, org inventory, and bulk CSV/JSONL import and export. Triggers on Dynamics 365, Dataverse, Web API, FetchXML.
---

# crm

A stateful CLI for **Microsoft Dynamics 365 Customer Engagement — on-premises
9.x (NTLM) or Dataverse online (OAuth)**. Every command except the reads of a CRM 2011 org
(over SOAP; `reference/setup.md` lists them) issues a real HTTP request to
the Dataverse Web API at `<url>/api/data/v9.x/`. There is no local mocking: the
live D365 server is a hard runtime dependency.

## On-prem vs cloud

Same Dataverse Web API; **only auth + API version differ** — the same commands run
against both targets.

| | On-prem (NTLM) | Cloud / online (OAuth) |
|---|---|---|
| Auth scheme | NTLM (also `kerberos` / `negotiate` / `adfs`) | OAuth (client-credentials, or delegated `oauth-user`) |
| API version | **v9.1 max** (`v9.2` → HTTP 501) | `v9.2` |
| `CreateMultiple` / `UpdateMultiple` / `DeleteMultiple` | not available | available |
| Solution import | synchronous `ImportSolution` (`ImportSolutionAsync` falls back to it) | `ImportSolutionAsync` |

## Agent contract — JSON mode

**Always pass `--json` from agent contexts.** It produces a stable envelope:

```json
{ "ok": true,  "data": ..., "meta": {"profile": "myprofile", "url": "https://host/org/api/data/v9.1/"} }
{ "ok": false, "error": "Record Not Found", "meta": {"status": 404, "code": "0x80040217", "category": "not_found", "retryable": false} }
```

**Flag placement — five flags go either side of the subcommand.** `--json`,
`--fields`, `--jq`, `--profile`, and `--dry-run` are dual-position: `crm entity get
accounts --json` is identical to `crm --json entity get accounts`, so append them
wherever it reads naturally. Every *other* global flag must still precede the
subcommand (a trailing one exits 2 with a position hint). Supplying the *same*
one of the five in both positions is a usage error (exit 2).

**`meta.profile` and `meta.url`** (the serving profile and Web API base URL) appear on
every success envelope from a command that opened a backend connection, so a result
is self-identifying. They are absent on error envelopes and on local verbs that never
connect (`connection status`, `session`, `skill`, `profile list`, `self-update`,
`repl`). `scaffold` connects.

**Failure `meta` is optional.** Backend errors carry taxonomy metadata; usage errors
may omit `meta`.

**`meta.warnings`** is the one structured channel to scan for non-fatal advisories —
it is an array (multiple warnings never clobber). Scan it for staged-but-unpublished
changes, created-but-read-back-failed records, and partial-optionset advisories. When
a multi-step write fails mid-way (optionset update, SLA create, app create) the **error**
envelope additionally carries `meta.completed_steps` and `meta.failed_stage`.

**Mind your context budget on fat list verbs.** Some read verbs return hundreds
to tens of thousands of rows (`solution list`, `metadata entities`, `webresource
list`, `solution components`). To *orient* in an unfamiliar org, run the one-call
inventory first (**Orienting in an unfamiliar org** below) rather than sweeping
several of them. When you need a fat verb, reshape it client-side, preserving the
`ok`/`data`/`meta` envelope: `--fields` projects each row to named keys, and
`--jq` runs a jq program over the payload (count, filter, `group_by`). `--jq` sees
the unwrapped `data` value, never the envelope: write `.[0].name`, not `.data[0].name`.

**Exit codes** — check `$?`, then read the envelope:

| code | meaning |
|------|---------|
| 0 | success (`ok: true`) |
| 1 | operational failure: server error, declined write, a server-data-dependent check, or a local file read/content error |
| 2 | usage error: bad/unknown flag, bad flag *combination*, missing arg, or bare `crm` when non-interactive — a caller mistake caught before any backend call; `--json` still emits `{"ok":false,"error":"…"}` on stdout |

Exit 1 can follow a partial write: read `meta.completed_steps` before retrying.

**`--dry-run`, the reads-execute rule.** Previews mutations without issuing them.
Reads (GET) always run for real ("no writes", not "no traffic"), so a preview reports
live facts (`_exists`, `would_skip`) and read verbs (`query`, `entity get`, …) return
real data. Under `--json` every dry-run carries `meta.dry_run: true`.

```bash
crm --json --dry-run entity create contacts --data '{"firstname":"Test"}'
```

**Validate-first is the recommended default for unattended writes.** On a record
create/update, an unknown field otherwise returns raw OData server noise the agent
cannot act on; validating first turns that into a clean `unknown_fields` envelope
(see `reference/records.md`). `--validate` applies to **record writes only** — `metadata`,
`solution`, and component writes have no `--validate`, so use `--dry-run` for those.

**Solution-scoped writes.** Every customization write — any verb that authors or
edits a solution component, in any group — requires an explicit
`--solution <unique_name>`: there is no profile default and no opt-out, and
omitting it exits 2 at parse time, before any confirmation prompt or backend
call (even under `--dry-run`). Pass
`--solution Default` for a deliberate Default-Solution-only write. A managed
`--solution` target is refused before the request is sent (exit 1,
`CannotModifyManagedSolution`). `apply` takes
the target as the spec's mandatory top-level `solution:` block instead of a flag.
The few exceptions (hard `metadata delete-*` verbs, N:N assign/match verbs) are
named in their reference files.

**Staged writes & publishing.** Atomic customization writes **stage** by default —
no `PublishAllXml` runs. A staged edit is invisible to reads: a GET returns the
*published* layer, so a pre-publish read-back false-negatives, and a second staged
edit on the same document reads the published layer and **silently discards the
first**. Never leave more than one edit staged per document — pass `--publish` per
write, or batch flagless writes and run `solution publish-all` once at the end.
Only published customizations export into a solution zip. The batch verbs (`apply`,
`scaffold table`) instead publish once at the end by default; global `--stage-only`
(or `CRM_STAGE_ONLY=1`) forces those to stage too, rejects an explicit `--publish`
alongside it, and records `meta.staged: true` in the envelope.

**No bare `crm` from an agent.** A non-interactive caller gets exit 2 instead of a
REPL; always pass a subcommand (`CRM_NO_REPL=1` hardens against a bare `crm`).

### Destructive operations — `--yes` required

Destructive operations delete/cancel/overwrite server-side state and are `--yes`-gated.
Without it, non-TTY runs fail fast (exit 1) with an error naming `--yes`
(`ok:false` under `--json`); TTY runs prompt. If a command exposes `--yes` only
for a destructive mode, the reference file names that risk.

**Inform first, back up first.** Tell the user what will be destroyed and get
explicit go-ahead — `--yes` asserts the *user's* intent, not the agent's. Before
irreversible operations, capture a restorable copy when possible and report where it landed:
`solution export` before `import`/`uninstall`/`apply-upgrade`, `data export` or
`entity get` before record deletes, `metadata export-spec` before
`delete-entity`/`delete-attribute`, `workflow export` before `workflow delete`,
`translation export` before `translation import`. Largest blast radii:
`solution import`, `data delete`, `metadata delete-entity`, and
`solution apply-upgrade` / `solution stage-and-upgrade --promote`.

That advice covers solution-level operations only. **Routine
customization is direct component writes** (`webresource update`, `form`/`view`/
`metadata` verbs) plus a publish: a per-change solution export/import cycle rewrites
every component in the solution and silently overwrites concurrent work in a shared
dev org. Back up at component scope instead (`webresource get <name> --out <file>`,
`solution export-spec`, `metadata export-spec`); at most one solution export per session.

## Hard constraints

- **NTLM or AD FS username/password (on-prem), OAuth client-credentials or delegated `oauth-user` (online).**
  AD FS OAuth, MFA, certificate credentials and ROPC are out of scope; OAuth targets
  the public cloud only. Delegated interactive and device-code sign-in is the
  `oauth-user` scheme (`reference/setup.md`). An IFD
  org (NTLM gets a bare HTTP 500) needs the `adfs` scheme: `reference/setup.md`.
- **Secrets are saved by default.** `crm profile add` / `crm profile set-password`
  store the secret in the OS keyring, or a `0600` plaintext field in the profile
  file when the keyring is unavailable (WSL/headless) or `--store-password-plaintext`
  is passed. Keyring XOR plaintext (single store). Works for both the NTLM password
  and the OAuth client secret. `crm profile delete-password` removes it. Resolution:
  `--password` (per-run override) > stored secret > TTY prompt. No env-var fallback.

## Command discovery & where to look

For exact flags, choices, and defaults, **never guess** — interrogate the CLI:

- `crm --json describe <group>`: catalogue of every command, option, and choice (no
  connection needed). Plain `describe` prints a 27 KB table, and unscoped `--json
  describe` is about 1 MB: always name a group.
- `crm <group> --help` — per-command options.
- `crm --json connection whoami` — confirm the live target; `data.profile`,
  `data.url`, and `data.org_name` identify the org without GUID-matching.

The skill states only what those cannot: workflows, gotchas, and the JSON contract.

### Orienting in an unfamiliar org — start here

Before assembling schema, queries, or writes against an org you don't know, run
the **one-call inventory** instead of stitching together `solution list` +
`metadata entities` + `publisher`/`app`/`plugin`/`workflow` sweeps:

```bash
crm --json org brief
```

Read-only; returns **counts and key names only, never full rows**, so it is cheap
on context and safe to run first. It covers
identity, solutions, publishers, schema, apps, automation and components. Two uses:

- **solutions** lists the unmanaged non-default names: candidates for a customization
  write's `--solution` target. If the server rejects one with `0x80040203` ("solution
  name is not valid", an org-specific template solution), fall back to another custom
  unmanaged solution, or `Default`, which the server accepts.
- **publishers** gives each customization prefix: what a new component's schema name
  must start with. Pick it *before* authoring schema.

Then drill into one domain with the fat verbs, shaped with `--fields` / `--jq`.
Counts saturate at 5000 (Dataverse's `$count` ceiling): read 5000 as "at least 5000".

**Verb router:** **list or query records** with `crm query odata` (`entity` is
single-record CRUD only); **browse metadata** with `crm metadata entities` /
`attributes` / `list-*`.

For per-domain detail:

| Working on… | Read |
|---|---|
| first-time setup: install the `crm` binary, create/switch a connection profile (NTLM, AD FS, OAuth or delegated `oauth-user` sign-in, secret storage), `--json`/no-TTY behavior; legacy CRM 2011 (read-only `crm legacy`); the PAC target check before a `pac` write | `reference/setup.md` |
| migration inventory of any org (`crm inventory extract`), comparing two (`crm inventory diff`), matching one to an application repository (`crm inventory map-source`), grouping one into work packages (`crm inventory packages`), judging recorded verification results against them (`crm inventory verify`), compatibility findings for a target (`crm inventory assess`) | `reference/inventory.md` |
| migrating an org (CRM 2011 or on-premises) to the cloud: the staged workflow and its evidence rules (if absent, suggest `crm skill install --skill crm-migration`) | the `crm-migration` skill |
| end-to-end customization: where to start, the order components go in, stage→publish→promote a change across dev/test/prod | `reference/customization-lifecycle.md` |
| repo-driven customizations-as-code: spec repo layout, plan → approve → `--from-plan` promote, seeding from a live org | `reference/customizations-as-code.md` |
| Microsoft Dataverse MCP server tools present (`read_query`, `invoke_api`): use vs `crm`, same-org proof, fallback to `crm` | `reference/dataverse-mcp.md` |
| records: create/read/update/delete, query (OData/FetchXML/saved), associate/lookup, clone, upsert, ad-hoc `action` | `reference/records.md` |
| bulk data: CSV/JSONL export/import, server-side BulkDelete (`data delete`), hand-authored `$batch` files | `reference/bulk.md` |
| metadata (read): browse schema, picklists, dependencies, export-spec, clone-entity, write-readiness brief, entity-def cache, incremental sync (`metadata changes`), relationship eligibility (`can-relate`); which entities hold data (`--in-use`, `unknown` vs `unused`) | `reference/metadata.md`, `reference/usage.md` |
| metadata write gotchas: datetime behavior, auto-number strings, rollup/calculated columns, status/state option writes, field mappings, hierarchical & virtual tables | `reference/metadata-writes.md` |
| schema authoring: `apply -f`, `scaffold table`, option sets, views, stage-then-publish | `reference/authoring.md` |
| solutions: create/export/import, investigate a failed import, async job polling/cancel (`async` group), packager extract/pack, validate, component drift, cascade/whole-entity audit, label translation export/import, `export-spec` (org-to-org drift recipe) | `reference/solutions.md` |
| model-driven apps + sitemap: create an app, add components, generate or live-edit the navigation tree (add-area / add-group / add-subarea / move-node / remove-node / set-title / set-description) | `reference/apps-sitemap.md` |
| forms: add/remove/move fields, presentation props, JS event handlers, tab/section skeleton, manual FormXml splice | `reference/forms.md` |
| web resources (upload, bulk push, continuous redeploy) and ribbon / command-bar buttons | `reference/webresource-ribbon.md` |
| charts and dashboards: author headlessly, edit XML layers, splice/remove tiles | `reference/charts-dashboards.md` |
| themes (org branding) and reports (SSRS RDL upload / link registration) | `reference/themes-reports.md` |
| automation: plug-in assemblies, webhooks & service endpoints, steps, workflows, SLA lifecycle (create / add-kpi / activate); which plug-ins and workflows run (`--in-use`, `unknown` vs `unused`) | `reference/automation.md`, `reference/usage.md` |
| composing classic-workflow **step XAML** to hand `workflow update --xaml-file` (on-prem logic path): the provenance wall, the direct-PATCH routine, the snippet library | `reference/workflow-xaml.md` |
| security: roles & assignment | `reference/security.md` |
| field (column) security: profiles, column permissions, assign to users/teams | `reference/fieldsec.md` |
| duplicate-detection rules: create/condition/publish/unpublish, check a candidate record, bulk-detect existing duplicates | `reference/dup.md` |
| connection roles: create, scope to an entity type, match as reciprocal partners | `reference/connectionrole.md` |
| server-side audit history (`audit history` / `audit detail`) — distinct from `session audit` | `reference/troubleshooting.md` |
| errors, retries, connection diagnostics, session/audit, on-prem vs cloud | `reference/troubleshooting.md` |

## Found a bug or missing capability?

If `crm` misbehaves or lacks something you need, **tell the user and offer to file
an issue — do not file silently.** On approval, see `reference/feedback.md`.
