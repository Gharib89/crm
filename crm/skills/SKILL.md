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
- Export/import D365 solutions (`.zip`).
- Manage **web resources** (HTML/JS/CSS/images) and set model-driven app icons.
- Register and manage **plug-in assemblies and processing steps**.
- Pull bulk datasets to CSV/JSON for analysis, or import CSV/JSONL records in bulk.
- Anything you'd otherwise script against the SOAP Organization Service.

## Install

The prebuilt `crm` binary bundles CPython and every dependency — no Python install
needed. One line per host:

**Windows (PowerShell):**

```powershell
irm https://pub-bbeb86c46454443ca76521dd4d29818e.r2.dev/install.ps1 | iex
```

**Linux:**

```bash
curl -fsSL https://pub-bbeb86c46454443ca76521dd4d29818e.r2.dev/install.sh | sh
```

Open a new shell so the updated PATH takes effect, then verify:

```bash
crm --version
```

## Configure

The CLI authenticates with **NTLM (Windows Integrated)** for on-prem, or
**OAuth 2.0 client-credentials** for Dataverse online. Set env vars (canonical
`D365_*` form **or** `CRM_*` aliases — both work, matching common on-prem tooling):

```bash
# Canonical names
export D365_URL="https://crm.contoso.local/contoso"
export D365_USERNAME="alice"
export D365_PASSWORD="..."             # not persisted by default (opt-in: connect/set-password --store-password)
export D365_DOMAIN="CONTOSO"           # optional if username is a UPN
export D365_AUTH="ntlm"
export D365_API_VERSION="v9.2"         # online: v9.2 · on-prem caps at v9.1 (v9.2 → HTTP 501)

# Or the CRM_* aliases (same effect)
export CRM_BASE_URL="http://internalcrm.example.local/ORG"
export CRM_USERNAME="DOMAIN\\user"     # DOMAIN\user is parsed automatically
export CRM_PASSWORD="..."
export CRM_API_VERSION="v9.1"
export CRM_AUTH="ntlm"
```

For **Dataverse online**, set `D365_AUTH=oauth` and supply the app-registration
credentials instead of username/password/domain:

```bash
export D365_URL="https://contoso.crm.dynamics.com"
export D365_AUTH="oauth"
export D365_TENANT_ID="<aad-tenant-id>"
export D365_CLIENT_ID="<app-registration-id>"
export D365_CLIENT_SECRET="..."        # env/.env, or store once: connection set-password --store-password
```

Scope (`https://<host>/.default`) and authority
(`https://login.microsoftonline.com/<tenant>`) are derived automatically; the
public cloud only. The bearer token is cached at `~/.crm/msal_token_cache.json`
(`0600`) and reused across invocations until expiry. The app registration needs
an **application user** with a security role in Dynamics. `CRM_*` aliases
(`CRM_TENANT_ID`, `CRM_CLIENT_ID`, `CRM_CLIENT_SECRET`) work here too. The client
secret can also be stored once for an existing OAuth profile (created by
`crm init`) with `crm connection set-password --profile <name> --store-password`
(OS keyring) or `--store-password-plaintext` (headless/CI), exactly like an NTLM
password — see **Hard constraints** below.

A `.env` file in the current directory (or its parent, or the path in
`CRM_DOTENV`) is auto-loaded on every command. Real env vars take
precedence. Example `.env`:

```
CRM_BASE_URL=http://internalcrm.example.local/ORG
CRM_API_VERSION=v9.1
CRM_USERNAME=DOMAIN\user
CRM_PASSWORD=secret
CRM_AUTH=ntlm
```

Optional: save a named profile for repeat use.

```bash
crm connection connect \
    --url https://crm.contoso.local/contoso \
    --username alice --domain CONTOSO \
    --profile-name prod
```

State directory: `~/.crm/` (override with `CRM_HOME`).

## On-prem vs cloud

Same Dataverse Web API; **only auth + API version differ** — the same commands run
against both targets.

| | On-prem (NTLM) | Cloud / online (OAuth) |
|---|---|---|
| `D365_AUTH` | `ntlm` (also `kerberos` / `negotiate`) | `oauth` |
| API version | **v9.1 max** (`v9.2` → HTTP 501) | `v9.2` |
| `CreateMultiple` / `UpdateMultiple` / `DeleteMultiple` | not available | available |
| Solution import (sync + `ImportSolutionAsync` / `StageSolution`) | available | available |

**Pin your profile when both credential sets are present.** If the environment
defines both `CRM_*`/NTLM and `D365_*`/OAuth variables, a bare `crm` command lets
the `D365_*` vars override the active profile and silently connect to cloud. Always
pass `--profile <name>` and confirm the real target with
`crm --json connection whoami` (check the `@odata.context` host).

## Command Groups

| Group        | Commands                                                                                | Purpose                                       |
|--------------|-----------------------------------------------------------------------------------------|-----------------------------------------------|
| `connection` | `connect`, `status`, `whoami`, `test`, `doctor`, `profiles`, `disconnect`, `set-password`, `delete-password` | Profiles + auth probe + connection diagnostic  |
| `entity`     | `get`, `create`, `update`, `upsert`, `delete`, `associate`, `disassociate`, `set-lookup`, `clear-lookup` | Record CRUD + relationships               |
| `query`      | `odata`, `fetchxml`, `saved`, `user`                                                    | OData v4, FetchXML, savedquery, userquery     |
| `metadata`   | `describe`, `entities`, `entity`, `attributes`, `attribute`, `picklist`, `relationships`, `dependencies`, `export-spec`, `clone-entity`, `cache-clear` | Schema introspection + option set values + dependency preview + spec export + entity clone + entity-def cache |
| `plugin`     | `register-assembly`, `list-types`, `register-step`, `unregister-assembly`, `unregister-step` | Plug-in assembly registration + step lifecycle |
| `solution`   | `create-publisher`, `create`, `set-version`, `list`, `info`, `components`, `dependencies`, `add-component`, `remove-component`, `export`, `import`, `import-result`, `extract`, `pack`, `publish-all`, `publish` | Solution lifecycle + publish customizations    |
| `view`       | `create`                                                                                | System views (savedquery)                      |
| `app`        | `create`, `add-components`, `set-sitemap`, `build-sitemap`                              | Model-driven apps (appmodule)                  |
| `webresource`| `create`, `update`, `get`, `list`                                                       | Web resources (HTML/JS/CSS/images) + app icons |
| `ribbon`     | `export`, `list`, `add-button`, `remove`                                            | Read and edit entity command-bar (ribbon) buttons; `--solution` required for list/add-button/remove |
| `security`   | `list-roles`, `list-user-roles`, `list-team-roles`, `assign-role`                       | Security roles — list + assign to users/teams  |
| `data`       | `export`, `import`                                                                      | Bulk CSV/JSON dataset export + JSONL/CSV import via `$batch` |
| `action`     | `function`, `invoke`                                                                    | Unbound OData functions/actions                |
| `session`    | `info`, `clear`, `history`, `audit`                                                     | Local session state + audit journal of mutations |
| _(top)_      | `apply -f spec.yaml`                                                                    | Declarative desired-state (publisher→…→views)  |
| `scaffold`   | `table DISPLAY --column 'DISPLAY:KIND[:opts]' ...`                                      | Quick one-table shorthand: entity + N columns in one publish |
| _(top)_      | `doctor`                                                                                | Alias for `connection doctor` — live connection diagnostic       |
| _(top)_      | `describe [group]`                                                                      | Machine-readable command/option/choice catalogue (no connection) |
| _(top)_      | `service-document`                                                                      | List every entity set the server exposes       |

`crm <group> --help` lists the per-command options.

## Agent guidance — JSON mode

**Always pass `--json` from agent contexts.** It produces a stable envelope:

```json
{ "ok": true,  "data": ..., "meta": {...} }
{ "ok": false, "error": "Record Not Found", "meta": {"status": 404, "code": "0x80040217", "category": "not_found", "retryable": false} }
```

**`meta.warnings`** is the one structured channel to scan for non-fatal advisories
— it is an array (multiple warnings never clobber). Scan it for staged-but-unpublished
changes, created-but-read-back-failed records (the `*_lookup_error` keys also kept in
`data` for back-compat are mirrored here), and partial-optionset advisories. When a
multi-stage optionset update fails mid-way the **error** envelope additionally carries
`meta.completed_steps` (steps that already landed on the server) and `meta.failed_stage`.

**Exit codes** — check `$?`, then read the envelope:
| code | meaning |
|------|---------|
| 0 | success (`ok: true`) |
| 1 | operational failure: server / validation / declined |
| 2 | usage error: bad/unknown flag, missing arg, or bare `crm` when non-interactive — under `--json` the standard `{ok:false,error}` envelope on stdout, else raw text on stderr |

Non-zero = the operation did not take effect. Pass `--yes` to skip confirmations non-interactively.

Use `--dry-run` to preview the HTTP request (method/URL/headers/body) without issuing it.
This is the safe way to validate a mutation before commit.

In `--json` mode every dry-run carries `meta.dry_run: true` — the canonical signal for
detecting a preview (covers batch and poll list-shaped previews uniformly).

```bash
crm --json --dry-run entity create contacts --data '{"firstname":"Test"}'
```

REPL is the default when no subcommand is given — but only on an interactive terminal.
A non-interactive caller (`--json`, `CRM_NO_REPL=1`, or a non-TTY stdin, as agents and
CI invoke it) gets a fail-fast **exit 2** with the usage message `no subcommand given;
run crm --help to list commands` instead of a hung prompt — under `--json` as the
standard `{ok:false,error}` envelope. Always pass a subcommand (e.g. `connection status`,
`entity get`); set `CRM_NO_REPL=1` to harden against an accidental bare `crm`. Explicit
`crm repl` always launches.

## Examples

### 1. Identity check

```bash
crm --json connection whoami
# -> {"ok": true, "data": {"UserId": "...", "BusinessUnitId": "...", "OrganizationId": "..."}}
```

### 2. Read with OData filter

```bash
crm --json query odata contacts \
    --filter "statecode eq 0" --select fullname,emailaddress1 --top 5

# token-efficient variant: --minimal strips OData annotation keys (@odata.etag,
# *@FormattedValue, *@...lookuplogicalname) from each --json record, keeping
# business fields, _*_value lookup GUIDs, and the primary id — the form to chain
# downstream. Also on query fetchxml/saved/user and entity get.
crm --json query odata contacts \
    --filter "statecode eq 0" --select fullname,emailaddress1 --top 5 --minimal
```

### 3. Create → update → delete a contact

```bash
# create
crm --json entity create contacts \
    --data '{"firstname":"Rafel","lastname":"Shillo"}'
# returns {"ok": true, "data": {"contactid": "<guid>", ...}}

# update
crm --json entity update contacts <guid> \
    --data '{"telephone1":"+1-555-0100"}'

# delete
crm --json entity delete contacts <guid> --yes
```

### 4. FetchXML query

```bash
crm --json query fetchxml accounts --xml '
<fetch top="10">
  <entity name="account">
    <attribute name="name"/>
    <attribute name="industrycode"/>
    <filter><condition attribute="statecode" operator="eq" value="0"/></filter>
  </entity>
</fetch>'
```

### 5. Browse metadata

```bash
crm --json metadata entities --custom-only --top 20
crm --json metadata attributes account
crm --json metadata attribute account industrycode

# --expect ATTR=VALUE asserts a field on the returned record (repeatable,
# AND-gated, stringified); a mismatch exits 1. Available on `metadata attribute`
# and `entity get` — see "Verify a metadata change landed" below.
crm --json metadata attribute account industrycode --expect AttributeType=Picklist
```

### 5a. Entity-definition cache (speed up repeated agent calls)

Pass `--cache-metadata` (or set `CRM_CACHE_METADATA=1`) to serve `metadata entities`
from a persistent per-profile on-disk cache instead of a live fetch. This is the
recommended form for agent loops that resolve entity set names repeatedly:

```bash
crm --json --cache-metadata metadata entities
# meta.cache: "hit"        — served from disk
# meta.cache: "miss"       — fetched live and saved (first call or TTL expired)
# meta.cache: "refreshed"  — live fetch forced by --refresh-metadata
```

Cache mode returns **only the 2-field rows** (LogicalName / EntitySetName) — enough
to resolve entity set names. `--custom-only` is incompatible with `--cache-metadata`
(exits 2); `--top` works as a client-side slice.

```bash
# Force a fresh fetch and overwrite the cache (one-shot; no env-var equivalent)
crm --json --refresh-metadata metadata entities

# Delete the active profile's cache file
crm --json metadata cache-clear
```

Cache file: `~/.crm/cache/<profile>/entitydefs.json` (override root with `CRM_HOME`).
TTL: ~15 min. Any metadata write (create/update/delete entity, attribute, optionset,
relationship, publish-all/xml) auto-invalidates the cache. Read-only schema only —
records and secrets are never cached.

### 5b. Export a live entity as an apply spec (round-trip)

```bash
# Export entity schema to YAML — ready for `crm apply -f`
crm metadata export-spec new_project \
    --with-views --with-relationships \
    -o project.yaml

# Re-create (or idempotently re-apply) in any environment
crm apply -f project.yaml
```

`export-spec` reads the entity over the Web API (pure GETs) and emits the
`crm apply -f` desired-state spec. Flags:

- `--with-views` — include the entity's public saved-query views.
- `--with-relationships` — include the entity's custom 1:N relationships.
- `-o FILE` — write bare YAML to FILE (directly consumable by `apply -f`). Without
  `-o` the spec is emitted under the standard JSON envelope.

Captures: entity definition, primary-name attribute, all custom apply-creatable
columns (deep-read for `MaxLength`/`RequiredLevel`/options), referenced global
option sets, relationships (with flag), views (with flag). Publisher/solution are
**not** emitted — supply them via `--solution` on `apply`, or edit the YAML.
Fidelity note: these round-trip through `apply`: `max_length`, `required`, options,
lookup `target_entity`, `precision` (decimal/double/money), and string `format_name`
(`Email`/`Phone`/`Url`/`TextArea`/etc.). Caveats: a string column whose live format
is `Json` or `RichText` (uncreatable by `apply`) is re-created as plain `Text`; a
datetime column's format is NOT captured (re-created with the default format); a
polymorphic (multi-target) lookup is exported with its first target only and
re-created as a single-target lookup; relationship `cascade` and `associated_menu`
are captured but not yet acted on (the relationship is re-created with default
cascade/menu). `apply` ignores unknown keys so the spec always remains apply-consumable.

### 5c. Clone a custom entity

Duplicate a custom entity under a new schema name. The bare clone copies entity
definition, custom attributes (including lookups, recreated pointing at the same
parent tables), and reuses referenced global option sets by name (not duplicated).
Forms, views, and workflows are opt-in.

```bash
# skeleton only (entity + attributes + lookups + reused option sets)
crm --json metadata clone-entity new_project cwx_TicketClone --display "Ticket Clone"

# everything cloneable over the API
crm --json metadata clone-entity new_project cwx_TicketClone --with-all --solution MySolution

# opt-in flags
crm --json metadata clone-entity new_project cwx_TicketClone \
    --with-forms --with-views --with-workflows
```

Key flags: `--display` overrides the display name of the new entity; `--solution`
scopes all created components; `--with-forms` (Main forms only), `--with-views`,
`--with-workflows` (classic workflows and business rules), `--with-all` enables
all three.

**Not cloned (Web API limits):**

- **Ribbon** (`RibbonDiffXml` has no Web API write path — solution import only).
  The result carries a `ribbon_note` field confirming this.
- **N:N relationships** and 1:N relationships where the source is the *parent*
  (referenced) side — cloning those would add lookups on *other* tables.
- **Polymorphic / Customer lookups** — only single-target lookups come across.
- **Charts** — deferred follow-up.

`--with-workflows` copies every classic workflow/business rule (`type=1`) whose
primary entity is the source, including managed ones (no "is custom" filter
available). Actions, BPFs, dialogs, and modern flows are skipped (reported under
`skipped_workflows`). On Unified Interface a cloned form may need adding to the
model-driven app's form list to be visible.

### 5e. Preview dependencies before deleting a metadata component

```bash
# Check what would block deleting an entity
crm --json metadata dependencies cwx_ticket

# Check what would block deleting a column (dotted entity.attribute)
crm --json metadata dependencies cwx_ticket.cwx_priority --kind attribute

# Check what depends on a global option set
crm --json metadata dependencies cwx_status --kind optionset --for dependents
```
Returns `{can_delete, blockers[], metadata_id, component_type, kind, for}`. Each
blocker has `dependent_type`, `dependent_id`, `dependent_parent_id`, `required_type`, `dependency_type`.
`--for delete` (default) uses `RetrieveDependenciesForDelete`; `--for dependents`
uses `RetrieveDependentComponents`. Read-only.

To fold dependency info directly into a delete result (non-destructive with `--dry-run`):

```bash
crm --json --dry-run metadata delete-attribute cwx_ticket cwx_priority --yes --check-dependencies
```
`--check-dependencies` is available on `delete-entity`, `delete-attribute`,
`delete-relationship`, and `delete-optionset`. Default off (no extra round-trip).

### 5f. Preview what blocks uninstalling a managed solution

```bash
crm --json solution dependencies CRMWorx
```
Solution-scoped counterpart to `metadata dependencies` (§5e): calls
`RetrieveDependenciesForUninstall(SolutionUniqueName='<name>')` and returns
`{solution, blockers[], count}`, each blocker shaped like the metadata-dependency
blockers (`dependent_type`, `dependent_id`, `dependent_parent_id`, `required_type`,
`dependency_type`). Read-only; the GET fires under `--dry-run`. Unknown solution
name → clean `{ok:false}`. Use this for "what stops me uninstalling solution X?";
use `metadata dependencies` for a single component.

### 6. Export a solution

```bash
crm solution list --unmanaged
crm solution export MyCustomSolution -o /tmp/snap.zip
# returns {"output": "/tmp/snap.zip", "bytes": 123456, "managed": false, ...}
```

Put a solution under source control with the offline SolutionPackager bridge
(no connection/profile needed; resolves the exe via `--solutionpackager-path` →
`CRM_SOLUTIONPACKAGER` → PATH, else errors naming the `Microsoft.CrmSdk.CoreTools`
NuGet). `git diff` on the extracted tree IS the solution diff:

```bash
crm solution extract --zipfile /tmp/snap.zip --folder src/MyCustomSolution
# ...commit + review git diff, then rebuild a zip from the tree
crm solution pack --zipfile dist/built.zip --folder src/MyCustomSolution
# --package-type Unmanaged|Managed|Both (default Unmanaged); a non-zero
# SolutionPackager exit fails the command. Envelope: {action, exit_code,
# folder, zipfile, stdout_tail}.
```

### 6a. Validate a solution zip before import

Offline static analysis — no connection or profile needed:

```bash
crm solution validate /tmp/snap.zip
# checks: RootComponents<->customizations parity, $webresource: ribbon refs,
# global option-set bindings, well-formed XML, required members present.
# exits non-zero on any error-severity finding.
```

Add `--against-org` to also check for colliding `formid`/`savedqueryid` GUIDs
and existence of referenced web resources and global option sets in the target org
(requires a connection/profile). Use before `solution import` as a CI gate.

### 7. Bulk CSV export

```bash
crm data export opportunities -o /tmp/op.csv \
    --filter "statecode eq 0" --select name,estimatedvalue,closeprobability \
    --page-size 500
```

### 7a. Bulk import via `$batch`

All writes are routed through `$batch` — the only on-prem bulk mechanism
(`CreateMultiple`/`UpsertMultiple` are cloud-only).

```bash
# Create records from a JSONL file (format inferred from suffix)
crm data import accounts records.jsonl

# Upsert (PATCH by GUID); id-column is stripped from the record body
crm data import contacts contacts.jsonl --mode upsert --id-column contactid

# CSV import (best-effort coercion; prefer JSONL for IDs / postal codes / lookups)
crm data import cwx_tickets tickets.csv

# Non-transactional + continue-on-error (requires --no-transaction)
crm data import accounts large.jsonl \
    --chunk-size 50 --no-transaction --continue-on-error

# Dry-run preview — zero writes, summary shows imported:0 dry_run:true
crm --dry-run data import accounts records.jsonl
```

Output: `{imported, failed, chunks, entity_set, mode, dry_run, format}`.
`failed > 0` surfaces a `meta.warnings` advisory; exit code is 0 on partial failure.

### 8. Call an arbitrary OData function

```bash
crm --json action function RetrieveCurrentOrganization \
    --params '{"AccessType":"Default"}'
```

### 9. Picklist / option set values (critical for agents writing valid records)

```bash
crm --json metadata picklist account industrycode
# data: raw {"OptionSet": {"Options": [{"Value": 1, "Label": {"UserLocalizedLabel": {"Label": "Accounting"}}}, ...]}}
# meta.options: flattened [{"value": 1, "label": "Accounting"}, ...] — same shape for `metadata get-optionset <name>`
```
`meta.options` (JSON mode only) flattens the nested labels to `[{value, label}]` so you
need not dig through `Label.UserLocalizedLabel.Label`; raw `data` is unchanged. Boolean
attributes have no `Options` array (`TrueOption` / `FalseOption` instead), so `meta.options`
is empty for them — read the raw `TrueOption` / `FalseOption` fields directly.

### 9a. Write-readiness brief — one call before writing a record

```bash
crm --json metadata describe new_project
# data: { entity_set_name, primary_id, primary_name, writable_attributes: [
#   { logical_name, attribute_type, required_level,
#     # lookups:                bind_key:"new_AccountId@odata.bind", targets:[{logical,set_name}]
#     # picklist/state/status:  options:[{value,label}]
#     # global-bound picklist:  + global_optionset_id (GUID) } ] }
```
One read-only call that consolidates everything needed to build a valid create/update
payload: the entity set name, primary id/name, every writable column with its required
level, lookup `@odata.bind` keys + resolvable targets, and inline option values. Prefer
this over chaining `attributes` + `picklist` + `relationships` by hand.

### 10. Associate / disassociate records

```bash
# Associate a contact to an account's contact_customer_accounts collection (1:N)
crm entity associate accounts <account-guid> \
    contact_customer_accounts contacts <contact-guid>

# Set a single-valued lookup (N:1) — sets parent account on a contact
crm entity set-lookup contacts <contact-guid> \
    parentcustomerid_account accounts <account-guid>

# Disassociate (collection) — supply --related-set + --related-id
crm entity disassociate accounts <account-guid> \
    contact_customer_accounts \
    --related-set contacts --related-id <contact-guid>

# Clear a single-valued lookup
crm entity clear-lookup contacts <contact-guid> \
    parentcustomerid_account
```

### 11. Execute a saved system view by GUID

```bash
# First discover the saved query
crm query odata savedqueries \
    --filter "name eq 'Active Accounts'" --select savedqueryid,name

# Then execute it against the entity set
crm --json query saved accounts <savedqueryid>
```

### 12. Publish customizations after a metadata or solution change

```bash
crm solution publish-all
# or selectively:
crm solution publish --xml \
    '<importexportxml><entities><entity>account</entity></entities></importexportxml>'
```

#### Stage many changes, then publish once

By default each create/update metadata command auto-publishes. Use the global
`--stage-only` flag (or `CRM_STAGE_ONLY=1`) to suppress publishing across a batch
of changes, then run `publish-all` once at the end. `--stage-only` forces every
create/update command to `--no-publish`; in `--json` mode the envelope `meta`
records `staged: true`. Combining `--stage-only` with an explicit `--publish` is
rejected.

```bash
crm --stage-only metadata add-attribute new_widget \
    --kind string --schema-name new_Label --display Label --max-length 100
crm --stage-only metadata create-optionset --name new_priority --display Priority \
    --option 1:Low --option 2:High
# ... more staged changes ...
crm solution publish-all   # single publish for all staged customizations
```

#### Verify a metadata change landed (`--expect`)

After a create→publish, poll the attribute until its definition reflects the
change — then retry if it hasn't propagated yet:

```bash
crm metadata add-attribute new_widget --kind string \
    --schema-name new_Label --display Label --max-length 100 \
  && crm solution publish-all \
  && crm --json metadata attribute new_widget new_label --expect AttributeType=String \
  || echo "attribute not ready yet — retry"
```

`--expect ATTR=VALUE` is repeatable, AND-gated, and stringified (each pair passes
only if `str(record[ATTR]) == VALUE`; a missing key never matches). The first
mismatch exits **1** with `{ok:false, error:"Expectation failed: …", meta:{attr, expected, actual}}`, so a shell
`||` branch — or an agent — can branch on the failure and retry. All pairs match →
normal `ok:true`, exit 0, record unchanged. A malformed `--expect` (no `=`) is a
usage error (exit 2) raised before any HTTP. Attribute logical names are lowercase
(`new_label`), the schema name PascalCase (`new_Label`).

The same flag on `entity get` asserts a write landed on the record side — e.g.
`crm --json entity get cwx_tickets <guid> --expect statecode=1` after a state
change. (The check runs against the full record, before any `--minimal` projection.)

### 13. Inspect the server's entity sets

```bash
crm --json service-document
# returns {"value": [{"name": "accounts", "url": "accounts", ...}, ...]}
```

## Destructive operations — `--yes` confirm contract

These verbs permanently delete or cancel server-side state. Every one accepts a
`--yes` flag to skip the interactive confirmation; whenever a confirmation would
be shown, omitting `--yes` in a non-TTY context aborts safely and emits
`{"ok": false, "error": "aborted by user"}` under `--json` (a human-formatted
error otherwise), exit 1. (`solution import --no-overwrite` shows no in-band
prompt, but the destructive-op gate still requires `--yes` for any import.)
Always pass `--yes` when invoking them non-interactively (e.g. from an agent),
and only after you have confirmed intent.

| Command | What it destroys |
| --- | --- |
| `crm metadata delete-entity <logical>` | A custom entity (table) and ALL its rows |
| `crm metadata delete-optionset <name>` | A custom global option set |
| `crm metadata delete-attribute <entity> <attribute>` | A custom column |
| `crm metadata delete-relationship <schema-name>` | A custom relationship (1:N or N:N) |
| `crm entity delete <set> <guid>` | A single record |
| `crm solution job-cancel <id>` | A running async job |
| `crm solution import <zip>` | OVERWRITES unmanaged customizations in the target org (default; `--no-overwrite` skips the prompt but the gate still needs `--yes`) |
| `crm solution remove-component --solution <name> --type <int\|name> --id <guid>` | Removes a component from an unmanaged solution |
| `crm async cancel <id>` | A pending/suspended async operation |

## Solution scaffolding — publisher + solution

```bash
# Publisher: --prefix is 2–8 alphanumeric, letter-first, not 'mscrm';
# --option-value-prefix is an integer 10000–99999.
crm --json solution create-publisher --name crmworx --display CRMWorx \
    --prefix cwx --option-value-prefix 30000 --if-exists skip

# Solution: --publisher XOR --publisher-id (mutually exclusive).
crm --json solution create --name CRMWorx --publisher crmworx --if-exists skip
```

With a named profile active, both verbs auto-wire `publisher_prefix` (from
`create-publisher`) and `default_solution` (from `create`) back into it, so
later `metadata create-*` commands target that prefix/solution by default. Pass
`--no-set-default` to opt out.

Bump the version (or friendly name / description) of an **unmanaged** solution
before exporting — at least one field is required, `--version` is validated as
4-part dotted numeric pre-HTTP, and managed solutions / patches are rejected
client-side:

```bash
crm --json solution set-version CRMWorx --version 1.0.1.0
crm --json solution set-version CRMWorx --friendly-name "CRM Worx" --description "RC build"
```

Add or remove an existing component to/from an **unmanaged** solution
(`AddSolutionComponent` / `RemoveSolutionComponent`). `--type` takes a
`componenttype` integer or a friendly name (case- and separator-insensitive:
`entity`=1, `attribute`=2, `relationship`=3, `optionset`=9, `entityrelationship`=10,
`webresource`=61, …; raw int for anything else). Both refuse managed targets
client-side. `remove-component` is destructive (`--yes` + the PreToolUse gate):

```bash
crm --json solution add-component --solution CRMWorx --type webresource --id <guid>
crm --json solution add-component --solution CRMWorx --type 1 --id <guid> --no-add-required
crm --json solution remove-component --solution CRMWorx --type 61 --id <guid> --yes
```

### Component drift detection — `components --save` / `--diff`

Snapshot and compare solution contents for CI gates or agent branching:

```bash
# Capture the expected inventory (normalized bare JSON list)
crm --json solution components CRMWorx --save components.json
# -> {"ok": true, "data": {"saved": "components.json", "count": 42}}

# Compare live against the snapshot — exit 0 = matches, exit 1 = drift
crm --json solution components CRMWorx --diff components.json
# on match:  {"ok": true,  "data": {"matches": true, "missing": [], "unexpected": []}, "meta": {"matches": true}}
# on drift:  {"ok": false, "data": {"matches": false, "missing": [...], "unexpected": [...]},
#             "error": "Drift detected: 1 missing, 0 unexpected component(s)."}
```

Each component entry: `{"componenttype": <int>, "objectid": "<guid-lowercase>", "rootcomponentbehavior": <int|null>}`. Components are keyed on the tuple `(componenttype, objectid, rootcomponentbehavior)` — `missing` = in expected not live; `unexpected` = in live not expected. **Exits 1 on drift.** The flags are mutually exclusive; bare `components <name>` lists components unchanged.

## Views — `view create` (savedquery)

```bash
# --otc is the entity ObjectTypeCode (from `metadata entity <name>`); --column
# is repeatable 'logical[:width]' (order preserved); --order sets ascending sort
# attribute; --filter-active scopes to statecode=0; --if-exists [error|skip].
crm --json view create cwx_ticket --name "Active Tickets" --otc 10127 \
    --column "cwx_name:220" --column "cwx_priority:120" \
    --filter-active --if-exists skip
```

The LayoutXml `object` attribute is the entity ObjectTypeCode (OTC) — get it from `metadata entity <name>`.

## Model-driven apps — `app` (appmodule)

```bash
# create: --unique-name is publisher-prefixed, e.g. 'cwx_crmworx'.
crm --json app create --name CRMWorx --unique-name cwx_crmworx --if-exists skip

# add-components: APP_ID positional + repeatable --component 'kind:guid'.
# kind ∈ view|chart|form|dashboard|sitemap|bpf (NOT 'entity' — tables surface
# via the sitemap's Entity= subareas, not AddAppComponents).
crm --json app add-components <appmoduleid> \
    --component view:<savedqueryid> --component chart:<savedqueryvisualizationid>

# set-sitemap: SITEMAP_NAME positional is the sitemap's descriptive name
# (stored as sitemapname); --unique-name is the app's uniquename and sets
# sitemapnameunique to auto-associate the sitemap with that app.
crm --json app set-sitemap "CRMWorx Sitemap" --xml-file /tmp/sitemap.xml --unique-name cwx_crmworx

# build-sitemap: generates the SiteMapXml for you, then creates it via the same
# path as set-sitemap. Grammar: --area 'id[:Title]', --group 'areaId/groupId[:Title]',
# --subarea 'areaId/groupId:entity=<logical>[:Title]' (binds a table via Entity=;
# titles optional everywhere). SubArea Ids are auto-allocated; refs/dup Ids are validated.
# crm --dry-run app build-sitemap ... prints the generated XML and does NOT POST.
crm --json app build-sitemap "CRMWorx Sitemap" \
    --area 'sales:Sales' --group 'sales/accounts:Customers' \
    --subarea 'sales/accounts:entity=account:Accounts' \
    --subarea 'sales/accounts:entity=contact' --unique-name cwx_crmworx
```

## Web resources — `webresource` (HTML/JS/CSS/images)

```bash
# create: --file bytes are base64'd into `content`; webresourcetype is inferred
# from the extension (.html=1, .css=2, .js=3, .xml=4, .png=5, .jpg=6, .gif=7,
# .xap=8, .xsl=9, .ico=10, .svg=11, .resx=12 — the real D365 option set, so
# CSS=2 and 8 is Silverlight). --display-name defaults to --name; --type <int>
# overrides inference (an unknown extension without --type is rejected).
crm --json webresource create --name cwx_/scripts/ribbon.js --file ./ribbon.js --solution cwx_crmworx

# update <name>: plain PATCH of only the sent fields (content from --file and/or
# --display-name; at least one required), resolved by name — not retrieve-merge.
crm --json webresource update cwx_/scripts/ribbon.js --file ./ribbon.js

# inspect
crm --json webresource get cwx_/scripts/ribbon.js
crm --json webresource list --custom-only

# use as a model-driven app icon: --icon-webresource takes a name or a GUID
# (omit to keep the platform default icon).
crm --json webresource create --name cwx_/icons/app.svg --file ./app.svg
crm --json app create --name CRMWorx --unique-name cwx_crmworx --icon-webresource cwx_/icons/app.svg
```

Both `create` and `update` honor `--solution` (`MSCRM.SolutionUniqueName`) and
publish after the write (`--no-publish` / global `--stage-only` suppress it).

## Plug-ins — `plugin` (assembly + step lifecycle)

```bash
# register-assembly: .dll bytes are base64'd into `content`; --name defaults to
# the filename stem; --version defaults to 1.0.0.0; --isolation-mode sandbox|none
# (sandbox=2, none=1; default sandbox). --solution sends MSCRM.SolutionUniqueName.
crm --json plugin register-assembly ./bin/Contoso.Plugins.dll --solution cwx_contoso

# --update: re-uploads content of an existing assembly by name; identity flags
# (--name, --version, etc.) are ignored under --update and produce a warning.
crm --json plugin register-assembly ./bin/Contoso.Plugins.dll --update

# list-types: platform-generated rows in plugintypes (one per IPlugin class).
# Columns: typename, friendlyname, plugintypeid. --assembly scopes to one assembly.
crm --json plugin list-types --assembly Contoso.Plugins

# register-step: --message and --plugin-type are required. Stage choices:
# prevalidation (10), preoperation (20), postoperation (40); default postoperation.
# Mode choices: sync (0), async (1); default sync. async forces postoperation.
# --entity sets primaryobjecttypecode (omit = all entities).
# --filtering-attributes (comma-separated) restricts an Update step.
# Step name is auto-derived as '<typename>: <message> of <entity>'; pass --name
# when the derived string would exceed the 256-char platform limit.
crm --json plugin register-step \
    --message Update \
    --plugin-type Contoso.Plugins.AccountPostUpdate \
    --entity account \
    --stage postoperation \
    --mode sync \
    --filtering-attributes name,telephone1

# unregister-step: by name or GUID; ambiguous name errors (use GUID).
crm --json plugin unregister-step "Contoso.Plugins.AccountPostUpdate: Update of account" --yes

# unregister-assembly: cascades — deletes dependent steps first, then the assembly.
crm --json plugin unregister-assembly Contoso.Plugins --yes
```

Full registration workflow (upload → verify types → register step):

```bash
crm --json plugin register-assembly ./bin/Contoso.Plugins.dll --solution cwx_contoso
crm --json plugin list-types --assembly Contoso.Plugins
crm --json plugin register-step \
    --message Create \
    --plugin-type Contoso.Plugins.AccountPreCreate \
    --entity account --stage preoperation --mode sync
```

`--dry-run` skips all writes (resolution GETs still fire); `--json` envelope carries
`meta.dry_run: true`.

## Workflows — `workflow`

```bash
# List workflow definitions on an entity
crm --json workflow list --entity cwx_ticket --category 0

# Activate / deactivate
crm --json workflow activate <workflow-guid>
crm --json workflow deactivate <workflow-guid>

# Trigger an on-demand workflow
crm --json workflow run <workflow-guid> --target <record-guid>

# Clone a classic workflow onto another entity (xaml-retargeted; activates by default)
crm --json workflow clone <workflow-guid> --to-entity cwx_ticketclone
crm --json workflow clone <workflow-guid> --to-entity cwx_ticketclone --no-activate
crm --json workflow clone <workflow-guid> --to-entity cwx_ticketclone \
    --name "My Clone" --solution my_solution

# Export / import a workflow definition (incl. xaml) as JSON
crm --json workflow export <workflow-guid> --out ./wf.json
crm --json workflow import --file ./wf.json
crm --json workflow import --file ./wf.json --activate
```

Category values: `0`=Workflow, `1`=Dialog, `2`=BusinessRule, `3`=Action, `4`=BPF, `5`=ModernFlow. Clone supports only `0` and `2`; action/BPF fail loudly.

## Security — `security` (roles and role assignment)

```bash
# List all security roles
crm --json security list-roles

# Filter to roles belonging to a specific business unit
crm --json security list-roles --business-unit 00000000-0000-0000-0000-000000000001

# Roles assigned to a system user (USER_ID is a GUID)
crm --json security list-user-roles 00000000-0000-0000-0000-000000000002

# Roles assigned to a team (TEAM_ID is a GUID)
crm --json security list-team-roles 00000000-0000-0000-0000-000000000003

# Assign a security role to a user — requires --yes (non-interactive) or interactive confirm
crm --json security assign-role 00000000-0000-0000-0000-000000000004 \
    --to-user 00000000-0000-0000-0000-000000000002 --yes

# Assign a security role to a team
crm --json security assign-role 00000000-0000-0000-0000-000000000004 \
    --to-team 00000000-0000-0000-0000-000000000003 --yes
```

`assign-role` requires exactly one of `--to-user` or `--to-team`. Role
assignment is cumulative and not cleanly reversible — omitting `--yes` in a
non-interactive context aborts (exit 1). The command also carries the standard
admin-header options (`--as-user GUID`, `--as-user-object-id GUID`,
`--suppress-dup-detection`, `--bypass-plugins`).

Security roles in D365 are **business-unit-scoped** — a role belongs to exactly
one business unit, and it can only be assigned to users or teams within the
same business unit.

## Declarative apply — `apply -f spec.yaml`

Stand up a whole table from one YAML/JSON spec instead of many imperative
commands. `apply` runs the metadata cores in dependency order (publisher →
solution → entities → option sets → attributes → relationships → views), each
with `if_exists=skip`, and publishes **once** at the end — re-applying an
unchanged spec is a no-op.

```bash
crm --json apply -f project.yaml              # create/skip, publish once
crm --dry-run --json apply -f project.yaml    # plan: dependents reported "planned"
crm --stage-only --json apply -f project.yaml # create without publishing
```

Emits `{ok, data:{applied, skipped, planned, failed}, meta:{staged}}`; each entry
is `{kind, name}` (a failed entry adds `error`). Metadata POSTs are
non-transactional, so a failure aborts-and-reports and leaves
staged-but-unpublished residue. A new table's views may report `planned` until
the first publish assigns its ObjectTypeCode — re-apply to land them. Run
`crm describe apply` for the full option catalogue.

```yaml
publisher: {unique_name: contosopub, prefix: contoso, option_value_prefix: 10000}
solution:  {unique_name: ContosoCore}
optionsets:
  - {name: contoso_priority, display_name: Priority, options: [{value: 100000000, label: Low}]}
entities:
  - schema_name: contoso_Project
    display_name: Project
    primary_attr: {schema_name: contoso_Name, label: Name}
    attributes:
      - {kind: string,   schema_name: contoso_Code,     display_name: Code, max_length: 100}
      - {kind: picklist, schema_name: contoso_Priority, display_name: Priority, optionset_name: contoso_priority}
      - {kind: lookup,   schema_name: contoso_Owner,    display_name: Owner, target_entity: systemuser}
    views:
      - {name: Active Projects, columns: [contoso_name, contoso_code]}
```

## Scaffold a table — `scaffold table`

Quick one-liner to create an entity + N columns in a single publish, running
through the same `apply` engine. Each resource is created with `if_exists=skip`
— re-running is a no-op.

```bash
# Create a table with typed columns (--json always in agent contexts)
crm --json scaffold table "Project" \
  --column "Name:string:max_length=200,required=ApplicationRequired" \
  --column "Due Date:datetime" \
  --column "Owner:lookup:target_entity=systemuser" \
  --column "Priority:picklist:optionset_name=new_priority"

# Dry-run plan (no changes; entity + all columns reported as planned)
crm --dry-run --json scaffold table "Project" \
  --column "Name:string" \
  --column "Due Date:datetime"

# Stage without publishing
crm --stage-only --json scaffold table "Project" \
  --column "Name:string"
```

Emits `{ok, data:{applied, skipped, planned, failed}, meta:{staged}}` — same
envelope as `apply`.

**Column shorthand:** `DISPLAY:KIND[:key=value,...]`. KIND ∈ `string`, `memo`,
`integer`, `bigint`, `decimal`, `double`, `money`, `boolean`, `datetime`,
`picklist`, `multiselect`, `lookup`, `image`, `file`.
- `string`/`memo` default `max_length` 100/2000; override with `max_length=N`.
  `max_length` on any other kind is an error.
- `lookup` requires `target_entity=<logical_name>`.
- `picklist`/`multiselect` require `optionset_name=<name>` (existing global
  option set — inline options are **not** supported; use `apply` for those).
- Optional: `required=None|Recommended|ApplicationRequired`, `description=<text>`.

Column schema names are derived `<publisher_prefix>_<PascalCase(DISPLAY)>` from
the profile's `publisher_prefix` (required — missing prefix → exit 2).
`--schema-name` overrides the entity schema only, not column names. Other flags:
`--display-collection`, `--ownership UserOwned|OrganizationOwned` (default
`UserOwned`), `--solution`, `--require-solution`.

**Limitations:** no views, no inline picklist options, single entity only. Use
`apply -f spec.yaml` for those cases.

## Agent guidance — record-create payloads (`@odata.bind`)

When constructing `entity create` payloads, lookup fields require an `@odata.bind`
suffix on the **navigation-property name** (the PascalCase schema name, e.g.
`cwx_CustomerId@odata.bind`), **not** the lowercase logical attribute.
A picklist bound to a global option set binds through `GlobalOptionSet@odata.bind`,
and on-prem 9.1 requires the option set's `MetadataId` GUID there (the `Name`
alternate key is rejected). `crm metadata describe <entity>` hands you the exact
`bind_key` per lookup and the `global_optionset_id` per global-bound picklist, so you
don't have to assemble them by hand.

Add `--validate` to `entity create`/`entity update` to field-name-check the payload
before the write. It runs 1-3 read-only metadata GETs and blocks unknown fields with
`{ok:false, meta:{unknown_fields, did_you_mean}}`; valid `<nav>@odata.bind` keys are
not flagged (checked against the nav-property union). It composes with `--dry-run`.
Scope is field-**name** only — option-set values are not validated.

## Errors & recovery

`D365Error` wraps any HTTP / API failure. In `--json` mode it becomes
`{"ok": false, "error": "...", "meta": {"status": N, "code": "0x...", "category": "...", "retryable": bool}}`.
A non-transactional optionset update that fails mid-stage adds `meta.completed_steps`
+ `meta.failed_stage` so the partial mutation is observable; all other failures carry
only the four keys above.

`meta.category` is a closed enum; `meta.retryable` flags the transient classes. The
backend auto-retries the `transport_error` / `throttled` (429) / `server_error` (5xx)
classes for idempotent verbs (`GET`/`PUT`/`PATCH`/`DELETE`), so for those
`retryable: true` is a post-exhaustion hint — act on it only after the error surfaces.
Two exceptions never auto-retry: `concurrency_conflict` (412) — refetch a fresh ETag
and retry; and any non-idempotent `POST` (record create, action, associate) —
a lost response may mean the write already landed, so the backend surfaces the
error rather than risk a duplicate side effect. To safely retry a create, re-run it as an
upsert-by-id (`entity upsert` with a client-supplied GUID) so the second call is
idempotent; or, if re-sending is acceptable, pass `--retry-on-ambiguous`
(env: `CRM_RETRY_ON_AMBIGUOUS`) to restore POST auto-retry. `$batch` keeps its own
independent retry loop and is unaffected by this gate.

| `category` | trigger | `retryable` | recovery |
|---|---|---|---|
| `not_found` | 404 / code `0x80040217` | no | record doesn't exist, or wrong entity set / GUID |
| `auth_failed` | 401 | no | NTLM: check `D365_DOMAIN\D365_USERNAME` + password. OAuth: app-registration (client id/secret, tenant) + an application user with a role |
| `forbidden` | 403 | no | the user lacks the privilege for that operation in the CRM security model; for `security assign-role` this also occurs when the role's business unit differs from the target user/team's business unit (roles are BU-scoped — assign a role from the same business unit as the principal) |
| `concurrency_conflict` | 412 | yes | another change won the race — retrieve a fresh ETag and retry |
| `duplicate_detected` | code `0x80040237` | no | a matching record exists; merge/resolve or pass `--suppress-dup-detection` |
| `validation` | other 4xx (e.g. 400), or a status-less client-side error (bad CLI input, schema/spec validation) | no | fix the request: bad payload / CLI input, alternate key, or OData syntax |
| `throttled` | 429 | yes | service-protection limit; the backend honors `Retry-After` (idempotent verbs only — a non-idempotent `POST` is not auto-retried) |
| `server_error` | 5xx | yes | transient server fault (idempotent verbs only — a non-idempotent `POST` is not auto-retried) |
| `transport_error` | request never got a response (network / TLS / timeout); message starts `HTTP transport failure` | yes | network / TLS / timeout before any response reached the client (idempotent verbs only — a non-idempotent `POST` is not auto-retried) |

## Hard constraints

- **NTLM (on-prem) or OAuth client-credentials (online).** IFD/Claims, certificate
  credentials, and other OAuth flows (device-code, interactive, ROPC) are out of
  scope; OAuth targets the public cloud only.
- **D365 CE on-prem 9.x or Dataverse online.** Same Web API; only auth differs.
- **Real server required.** No local mocking; a live D365 server must be reachable.
- **Secrets are not persisted by default; they may be stored on explicit opt-in**
  (`connection connect --store-password` → OS keyring, or `--store-password-plaintext`
  → profile file, `0600` on POSIX). For an already-existing profile — including an
  OAuth profile created by `crm init` — store the secret once with
  `connection set-password --profile <name>` (same `--store-password` /
  `--store-password-plaintext` flags; keyring by default), which works for both the
  OAuth client secret and the NTLM password. `connection delete-password` removes it.
  Resolution: `--password` > env/.env > stored secret > TTY prompt. (The OAuth bearer
  token is still cached at `~/.crm/msal_token_cache.json` (`0600`), as before.)

## Command discovery

- `crm describe` — machine-readable catalogue of every command, option, and choice (no connection needed).
- `crm <group> --help` — per-command options.
- `crm --json connection whoami` — confirm the live target before any mutation.
