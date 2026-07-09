---
name: crm
description: Operate Microsoft Dynamics 365 Customer Engagement — on-premises (v9.x, NTLM) or Dataverse online (OAuth) — from the shell. Use for record CRUD, OData/FetchXML queries, metadata browsing, solution lifecycle, UI customization (forms, sitemaps, dashboards, ribbons), plug-in and workflow automation, and bulk CSV/JSONL import and export. Triggers on Dynamics 365, D365 CE, Dataverse, Web API, FetchXML, NTLM CRM, on-prem CRM.
---

# crm

A stateful CLI for **Microsoft Dynamics 365 Customer Engagement — on-premises
9.x (NTLM) or Dataverse online (OAuth)**. Every command issues a real HTTP request
to the Dataverse Web API at `<url>/api/data/v9.x/`. There is no local mocking — the
live D365 server is a hard runtime dependency.

## On-prem vs cloud

Same Dataverse Web API; **only auth + API version differ** — the same commands run
against both targets.

| | On-prem (NTLM) | Cloud / online (OAuth) |
|---|---|---|
| Auth scheme | NTLM (also `kerberos` / `negotiate`) | OAuth (client-credentials) |
| API version | **v9.1 max** (`v9.2` → HTTP 501) | `v9.2` |
| `CreateMultiple` / `UpdateMultiple` / `DeleteMultiple` | not available | available |
| Solution import (sync + `ImportSolutionAsync` / `StageSolution`) | available | available |

## Agent contract — JSON mode

**Always pass `--json` from agent contexts.** It produces a stable envelope:

```json
{ "ok": true,  "data": ..., "meta": {"profile": "myprofile", "url": "https://host/org/api/data/v9.1/"} }
{ "ok": false, "error": "Record Not Found", "meta": {"status": 404, "code": "0x80040217", "category": "not_found", "retryable": false} }
```

**`meta.profile` and `meta.url`** appear on every success envelope from a command
that opened a backend connection — the serving profile name and Web API base URL,
so the result is self-identifying without eyeball-matching GUIDs. They are absent
on error envelopes and on local/meta verbs that never connect (`connection status`,
`session`, `skill`, `profile list`, `self-update`, `repl`, `scaffold`).

**Failure `meta` is optional.** Backend / Web API errors carry taxonomy metadata.
Usage errors may omit `meta`; non-usage Click-layer failures include an empty
`meta` object.

**`meta.warnings`** is the one structured channel to scan for non-fatal advisories —
it is an array (multiple warnings never clobber). Scan it for staged-but-unpublished
changes, created-but-read-back-failed records, and partial-optionset advisories. When
a multi-stage optionset update fails mid-way the **error** envelope additionally
carries `meta.completed_steps` and `meta.failed_stage`.

**Mind your context budget on fat list verbs.** Some read verbs return hundreds
to tens of thousands of rows (`solution list`, `metadata entities`, `webresource
list`, `solution components`) — enough to blow a per-call token budget. The global
`--fields KEY[,KEY...]` flag projects the response down to the columns you need
*client-side, post-response* (distinct from a request's `--select`), preserving
the `ok`/`data`/`meta` envelope so you can still page. Project (or count) a fat
verb before loading it, rather than reading the whole payload into context.

**Exit codes** — check `$?`, then read the envelope:

| code | meaning |
|------|---------|
| 0 | success (`ok: true`) |
| 1 | operational failure: server error, declined write, a server-data-dependent check, or a local file read/content error |
| 2 | usage error: bad/unknown flag, bad flag *combination*, missing arg, or bare `crm` when non-interactive — a caller mistake caught before any backend call; `--json` still emits `{"ok":false,"error":"…"}` on stdout |

Non-zero = the operation did not take effect.

**`--dry-run` — the reads-execute rule.** Previews mutations without issuing them —
the safe way to validate a write before commit. Reads (GET) always run for real
under `--dry-run` ("no writes", not "no traffic"), so a mutation's preview reports
live facts (`_exists`, `would_skip`) rather than guesses, and read verbs (`query`,
`entity get`, …) return real data. In `--json` mode every dry-run carries
`meta.dry_run: true`, the canonical signal for detecting a preview.

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
omitting it exits 2 before any backend call (even under `--dry-run`). Pass
`--solution Default` for a deliberate Default-Solution-only write. `apply` takes
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

**REPL fail-fast.** REPL is the default only on an interactive terminal. A
non-interactive caller (`--json`, `CRM_NO_REPL=1`, or non-TTY stdin — how agents and
CI invoke it) gets an **exit 2** with `no subcommand given; run crm --help to list
commands` instead of a hung prompt (under `--json`, the standard `{ok:false,error}`
envelope). Always pass a subcommand; set `CRM_NO_REPL=1` to harden against an
accidental bare `crm`. Explicit `crm repl` always launches.

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

## Hard constraints

- **NTLM (on-prem) or OAuth client-credentials (online).** IFD/Claims, certificate
  credentials, and other OAuth flows (device-code, interactive, ROPC) are out of
  scope; OAuth targets the public cloud only.
- **Secrets are saved by default.** `crm profile add` / `crm profile set-password`
  store the secret in the OS keyring, or a `0600` plaintext field in the profile
  file when the keyring is unavailable (WSL/headless) or `--store-password-plaintext`
  is passed. Keyring XOR plaintext (single store). Works for both the NTLM password
  and the OAuth client secret. `crm profile delete-password` removes it. Resolution:
  `--password` (per-run override) > stored secret > TTY prompt. No env-var fallback.

## Command discovery & where to look

For exact flags, choices, and defaults, **never guess** — interrogate the CLI:

- `crm describe [group]` — machine-readable catalogue of every command, option, and
  choice (no connection needed).
- `crm <group> --help` — per-command options.
- `crm --json connection whoami` — confirm the live target; `data.profile`,
  `data.url`, and `data.org_name` identify the org without GUID-matching.

The skill states only what those cannot: workflows, gotchas, and the JSON contract.

**Verb router:** to **list or query records** use `crm query odata` (the `entity`
group is single-record CRUD only — no `entity list` / `entity query`); to **browse
metadata** use `crm metadata entities` / `crm metadata attributes` /
`crm metadata list-*`.

For per-domain detail:

| Working on… | Read |
|---|---|
| first-time setup: install the `crm` binary, create/switch a connection profile (NTLM or OAuth, secret storage), `--json`/no-TTY behavior | `reference/setup.md` |
| end-to-end customization: where to start, the order components go in, stage→publish→promote a change across dev/test/prod | `reference/customization-lifecycle.md` |
| records: create/read/update/delete, query (OData/FetchXML/saved), associate/lookup, clone, upsert, ad-hoc `action` | `reference/records.md` |
| bulk data: CSV/JSONL export/import, server-side BulkDelete (`data delete`), hand-authored `$batch` files | `reference/bulk.md` |
| metadata (read): browse schema, picklists, dependencies, export-spec, clone-entity, write-readiness brief, entity-def cache, incremental sync (`metadata changes`), relationship eligibility (`can-relate`) | `reference/metadata.md` |
| metadata write gotchas: datetime behavior, auto-number strings, rollup/calculated columns, status/state option writes, field mappings, hierarchical & virtual tables | `reference/metadata-writes.md` |
| schema authoring: `apply -f`, `scaffold table`, option sets, views, stage-then-publish | `reference/authoring.md` |
| solutions: create/export/import, investigate a failed import, packager extract/pack, validate, component drift, label translation export/import, `export-spec` (org-to-org drift recipe) | `reference/solutions.md` |
| model-driven apps + sitemap: create an app, add components, generate or live-edit the navigation tree (add-area / add-group / add-subarea / move-node / remove-node / set-title / set-description) | `reference/apps-sitemap.md` |
| forms: add/remove/move fields, presentation props, JS event handlers, tab/section skeleton, manual FormXml splice | `reference/forms.md` |
| web resources (upload, bulk push, continuous redeploy) and ribbon / command-bar buttons | `reference/webresource-ribbon.md` |
| charts and dashboards: author headlessly, edit XML layers, splice/remove tiles | `reference/charts-dashboards.md` |
| themes (org branding) and reports (SSRS RDL upload / link registration) | `reference/themes-reports.md` |
| automation: plug-in assemblies, webhooks & service endpoints, steps, workflows, SLA lifecycle (create / add-kpi / activate) | `reference/automation.md` |
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
