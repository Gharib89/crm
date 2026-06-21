---
name: crm
description: Operate Microsoft Dynamics 365 Customer Engagement — on-premises (v9.x, NTLM) or Dataverse online (OAuth) — from the shell. Wraps the real Dataverse Web API (OData v4) over HTTPS. Use for record CRUD, OData/FetchXML queries, metadata browsing, solution lifecycle, plug-in assembly and step registration, and bulk CSV/JSONL import and export. Triggers on Dynamics 365, D365 CE, Dataverse, Web API, FetchXML, NTLM CRM, on-prem CRM.
---

# crm

A stateful CLI for **Microsoft Dynamics 365 Customer Engagement — on-premises
9.x (NTLM) or Dataverse online (OAuth)**. Every command issues a real HTTP request
to the Dataverse Web API at `<url>/api/data/v9.x/`. There is no local mocking — the
live D365 server is a hard runtime dependency.

## When to use

- Issue ad-hoc record CRUD (accounts, contacts, opportunities, custom entities).
- Run OData v4 (`$filter`/`$select`/`$top`) or FetchXML queries.
- Browse schema metadata (entity / attribute / relationship definitions).
- Author schema declaratively, export/import D365 solutions (`.zip`).
- Manage web resources, ribbons, forms, model-driven apps, plug-ins, workflows, SLAs, roles.
- Pull bulk datasets to CSV/JSON, or import CSV/JSONL records in bulk.
- Anything you'd otherwise script against the SOAP Organization Service.

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
{ "ok": true,  "data": ..., "meta": {...} }
{ "ok": false, "error": "Record Not Found", "meta": {"status": 404, "code": "0x80040217", "category": "not_found", "retryable": false} }
```

**`meta.warnings`** is the one structured channel to scan for non-fatal advisories —
it is an array (multiple warnings never clobber). Scan it for staged-but-unpublished
changes, created-but-read-back-failed records, and partial-optionset advisories. When
a multi-stage optionset update fails mid-way the **error** envelope additionally
carries `meta.completed_steps` and `meta.failed_stage`.

**Exit codes** — check `$?`, then read the envelope:

| code | meaning |
|------|---------|
| 0 | success (`ok: true`) |
| 1 | operational failure: server / validation / declined |
| 2 | usage error: bad/unknown flag, missing arg, or bare `crm` when non-interactive — `--json` still emits `{"ok":false,"error":"…"}` on stdout |

Non-zero = the operation did not take effect.

**`--dry-run`** previews mutations without issuing them — the safe way to validate a
write before commit. Reads (GET) always run for real under `--dry-run` ("no writes",
not "no traffic"), so a mutation's preview reports live facts (`_exists`,
`would_skip`) rather than guesses, and read verbs (`query`, `entity get`, …) return
real data. In `--json` mode every dry-run carries `meta.dry_run: true`, the canonical
signal for detecting a preview.

```bash
crm --json --dry-run entity create contacts --data '{"firstname":"Test"}'
```

**Validate-first is the recommended default for unattended writes.** On a record
create/update, an unknown field otherwise returns raw OData server noise the agent
cannot act on; validating first turns that into a clean `unknown_fields` envelope
(see `reference/records.md`). `--validate` applies to **record writes only** — `metadata`,
`solution`, and component writes have no `--validate`, so use `--dry-run` for those.

**`--yes`** skips interactive confirmations; always pass it when invoking
destructive verbs non-interactively (and only after confirming intent).

**REPL fail-fast.** REPL is the default only on an interactive terminal. A
non-interactive caller (`--json`, `CRM_NO_REPL=1`, or non-TTY stdin — how agents and
CI invoke it) gets an **exit 2** with `no subcommand given; run crm --help to list
commands` instead of a hung prompt (under `--json`, the standard `{ok:false,error}`
envelope). Always pass a subcommand; set `CRM_NO_REPL=1` to harden against an
accidental bare `crm`. Explicit `crm repl` always launches.

### Destructive operations — `--yes` required

These verbs permanently delete or cancel server-side state. Omitting `--yes` in a
non-TTY context aborts safely (`{"ok": false, "error": "aborted by user"}`, exit 1).

| Command | What it destroys |
| --- | --- |
| `crm metadata delete-entity <logical>` | A custom entity (table) and ALL its rows |
| `crm metadata delete-optionset <name>` | A custom global option set |
| `crm metadata delete-attribute <entity> <attribute>` | A custom column |
| `crm metadata delete-relationship <schema-name>` | A custom relationship (1:N or N:N) |
| `crm entity delete <set> <guid>` | A single record |
| `crm data delete <entity_set> (--fetchxml\|--fetchxml-file)` | Submits a server-side BulkDelete async job — permanently deletes ALL records matching the FetchXML query |
| `crm solution job-cancel <id>` | A running async job |
| `crm solution import <zip>` | OVERWRITES unmanaged customizations in the target org |
| `crm solution remove-component --solution <name> --type <int\|name> --id <guid>` | Removes a component from an unmanaged solution |
| `crm solution stage-and-upgrade <zip> [--promote --solution <name>]` | Stages a holding-solution upgrade; `--promote` replaces the base solution + its patches |
| `crm solution apply-upgrade <name>` | Promotes a separately-staged holding solution (replaces the base solution + deletes its patches) |
| `crm solution uninstall --solution <name>` | Uninstalls a solution (managed base also removes its patches) |
| `crm translation import <zip>` | OVERWRITES localized labels in the target org |
| `crm async cancel <id>` | A pending/suspended async operation |
| `crm entity disassociate <set> <id> <nav> --related-set <s> --related-id <id>` | Removes a collection relationship link |
| `crm entity clear-lookup <set> <id> <nav>` | Clears a single-valued lookup (sets it to null) |
| `crm workflow deactivate <id>` | Deactivates a workflow definition (statecode=0) |

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
- `crm --json connection whoami` — confirm the live target (check the
  `@odata.context`) before any mutation.

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
| records: create/read/update/delete, query (OData/FetchXML/saved), associate/lookup, bulk import/export, ad-hoc `action` | `reference/records.md` |
| metadata: browse schema, picklists, dependencies, export-spec, clone-entity, write-readiness brief, entity-def cache, incremental sync (`metadata changes`) | `reference/metadata.md` |
| schema authoring: `apply -f`, `scaffold table`, option sets, views, stage-then-publish | `reference/authoring.md` |
| solutions: create/export/import, investigate a failed import, packager extract/pack, validate, component drift, label translation export/import | `reference/solutions.md` |
| customizations: model-driven apps, sitemap live-edit (add-area / add-group / add-subarea / move-node / remove-node), web resources, ribbon, forms, charts, dashboards, themes, reports | `reference/customizations.md` |
| automation: plug-in assemblies, webhooks & service endpoints, steps, workflows, SLA lifecycle (create / add-kpi / activate) | `reference/automation.md` |
| security: roles & assignment | `reference/security.md` |
| field (column) security: profiles, column permissions, assign to users/teams | `reference/fieldsec.md` |
| duplicate-detection rules: create/condition/publish/unpublish, check a candidate record | `reference/dup.md` |
| connection roles: create, scope to an entity type, match as reciprocal partners | `reference/connectionrole.md` |
| server-side audit history (`audit history` / `audit detail`) — distinct from `session audit` | `reference/troubleshooting.md` |
| errors, retries, connection diagnostics, session/audit, on-prem vs cloud | `reference/troubleshooting.md` |
| reporting a bug / requesting a feature | `reference/feedback.md` |

## Found a bug or missing capability?

If `crm` misbehaves or lacks something you need, **tell the user and offer to file
an issue — do not file silently.** On approval, see `reference/feedback.md`.
