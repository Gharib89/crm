<p align="center">
  <picture>
    <source media="(prefers-color-scheme: dark)" srcset="docs/assets/logo-dark.svg">
    <img alt="CRM — Dynamics 365 CE CLI" src="docs/assets/logo.svg" width="300">
  </picture>
</p>

Stateful CRM CLI for **Microsoft Dynamics 365 Customer Engagement** — on-premises
v9.x (NTLM) or Dataverse online (OAuth). Wraps the real Dataverse Web API (OData
v4) over HTTPS; the same commands run against both targets.

## Why

D365 CE on-prem ships with a GUI and a SOAP/.NET SDK. There's no first-party CLI
optimized for AI agents or shell scripting. This harness gives you:

- `crm` in your `PATH`
- One-shot subcommands for record CRUD, OData and FetchXML queries, metadata
  introspection, solution lifecycle, and plug-in assembly + step registration
- A stateful REPL for ad-hoc admin work
- `--json` everywhere for agent consumption
- `--dry-run` to preview writes without issuing them (reads still run for real); on writes that name another object — a lookup's target entity, a picklist's option set, a plug-in step's message/type/entity — the preview also reports whether each reference exists (`data.references[]`, dangling ones flagged in `meta.warnings`)
- An append-only JSONL audit journal (`~/.crm/audit/<session>.jsonl`) of every
  mutating command; `crm session audit` to review it
- `--cache-metadata` for a persistent per-profile entity-definition cache
  (speeds up repeated one-shot agent calls; env: `CRM_CACHE_METADATA=1`)
- `crm ribbon` to read and edit entity command-bar buttons (export / list / add-button / set-label / remove / hide-button / set-rules / add-custom-rule) without manual solution-XML editing; `ribbon export --application` exports the application-wide ribbon
- `crm solution layer-conflicts` to detect components shared by a managed and an unmanaged solution (unmanaged-layer conflicts) — works on-prem too, where XrmToolBox's layer explorer can't

## Documentation

Full docs (install, configure, per-group how-tos, generated CLI reference) live at
[crm-cli-docs.pages.dev](https://crm-cli-docs.pages.dev/).

Two surfaces feed AI agents:

- **`crm/skills/`** — the agent skill loaded by skill-aware harnesses (Claude
  Code, Copilot CLI, …) after `crm skill install`: a thin `SKILL.md` router plus
  `reference/*.md` files loaded on demand.
- **`llms.txt` / `llms-full.txt`** — published at the docs-site root for *any*
  web-fetch agent: [`/llms.txt`](https://crm-cli-docs.pages.dev/llms.txt) is a
  curated index, [`/llms-full.txt`](https://crm-cli-docs.pages.dev/llms-full.txt)
  is every page in one fetch.

## Requirements

| Requirement              | Version            | Notes                                  |
|--------------------------|--------------------|----------------------------------------|
| Python                   | ≥ 3.9              |                                        |
| Dynamics 365 CE on-prem  | 9.0 / 9.1 / 9.2    | Reachable from your machine over HTTPS |
| Auth                     | NTLM (on-prem) · OAuth (online) | NTLM = Windows Integrated; OAuth = client-credentials for Dataverse cloud. |

The D365 server is a **hard runtime dependency** — without it the CLI has nothing
to talk to. E2E tests fail loudly if credentials are missing.

## Install

### Option 1: Install script (no Python required)

The prebuilt `crm` binary bundles CPython and all dependencies. Install it with
a one-liner — no GitHub account or Python needed.

**Windows (PowerShell):**

```powershell
irm https://pub-bbeb86c46454443ca76521dd4d29818e.r2.dev/install.ps1 | iex
```

Installs to `%LOCALAPPDATA%\Programs\crm` and adds it to your user PATH. Open a
new shell, then run `crm --version`. The binary is unsigned, so Windows
SmartScreen may warn on first run. On managed machines this binary may be
blocked outright by endpoint security (e.g. Microsoft Defender ASR or
AppLocker) — use [Option 2: uv tool install](#option-2-uv-tool-install-isolated-recommended-for-managed-machines)
below in that case. To uninstall, download `install.ps1` and run
`.\install.ps1 -Uninstall`.

**Linux:**

```bash
curl -fsSL https://pub-bbeb86c46454443ca76521dd4d29818e.r2.dev/install.sh | sh
```

Installs to `~/.local/share/crm` and links `~/.local/bin/crm`. Ensure
`~/.local/bin` is on your PATH. Built on Ubuntu 22.04, so it runs on any Linux
with glibc ≥ 2.35. To uninstall, download `install.sh` and run
`sh install.sh --uninstall`.

Pin a version by setting `CRM_VERSION` (e.g. `v0.6.0`) before running.

Both scripts verify the archive's SHA-256 against the published `SHA256SUMS`
before extracting and abort on a mismatch or if it can't be fetched. To pin a
hash from a trusted channel (or to install a release published before checksums
existed), set `CRM_SHA256` (`$env:CRM_SHA256` on Windows). See
[the install guide](docs/getting-started/install.md#integrity-verification).

### Option 2: uv tool install (isolated, recommended for managed machines)

Installs `crm` into an isolated environment that runs through your trusted
`python` interpreter instead of a standalone binary. Use this when Option 1's
prebuilt binary is blocked by endpoint security (Microsoft Defender ASR,
AppLocker, etc.) — there is no new unsigned executable for those policies to
flag.

First install [uv](https://docs.astral.sh/uv/getting-started/installation/) if
you don't already have it:

**Windows (PowerShell):**

```powershell
winget install --id=astral-sh.uv -e
```

**Linux / macOS:**

```bash
curl -LsSf https://astral.sh/uv/install.sh | sh
```

Then install `crm` from the repository (it is not published to PyPI, so install
from the Git source or a wheel):

```bash
uv tool install git+https://github.com/Gharib89/crm
crm --version
```

`uv tool install` places a small launcher on your PATH; run `uv tool
update-shell` once if `crm` isn't found in a new shell. If even that launcher is
blocked, run the CLI as a module instead — no executable is created at all:

```bash
uv run --from git+https://github.com/Gharib89/crm crm --version
```

### Option 3: From source (development)

```bash
# From source (local dev)
pip install -e .

# Verify the command is on PATH
which crm
crm --version
```

### Keeping crm up to date

On an interactive terminal, `crm` checks at most once a day whether a newer
release exists and prints a one-line notice (at most once a day) after a command
finishes. The check
runs in the background against the release server, never blocks the command, and
is silent under `--json`, when stderr is not a terminal, when `CI` is set, or when
`CRM_NO_UPDATE_CHECK` is set.

For binaries installed via the install script (Option 1), upgrade in place:

```bash
crm self-update           # download, checksum-verify, and swap the binary
crm self-update --check   # report current vs latest version, change nothing
```

`pip`/`uv`/source installs aren't modified by `self-update` — it points you at
`pip install -U crm` (or re-running `uv tool install`) instead. Either way,
`self-update` also re-syncs any agent skills you installed with `crm skill
install`, and any shell completion you installed with `crm completion install`
(below), so both stay current with the CLI.

### Shell completion

Enable tab-completion for `crm` in bash, zsh, fish, or PowerShell:

```bash
crm completion install            # caches the script under ~/.crm and prints one rc line
crm completion show --shell zsh   # or just print the script to stdout
```

`install` writes the completion script under `${CRM_HOME:-~/.crm}/completion/` and
prints the single line to add to your shell startup file — `source <path>` for
bash/zsh/fish, `. <path>` for PowerShell — and never edits the file for you.
`--shell` defaults to autodetecting `$SHELL`; PowerShell sets no `$SHELL`, so pass
`--shell powershell` explicitly. See the
[completion how-to](docs/how-to/completion.md) for per-shell setup.

## Configure

Credentials live in a saved **profile** — there is no `.env` file and no credential
environment variables. Create one with `crm profile add`:

```bash
crm profile add
```

On a terminal this runs an interactive wizard (URL → inferred auth scheme →
identity → secret), saves the profile, stores the secret, runs a `WhoAmI` to
confirm, and activates it. The first connection command run with no profile
configured launches this wizard automatically; under `--json` / no TTY it errors
cleanly and tells you to run `crm profile add`.

For scripting, pass flags instead. The auth scheme is inferred from the URL
(`*.dynamics.*` → OAuth, anything else → NTLM); override with `--auth-scheme`.

**On-prem (NTLM):**

```bash
crm profile add \
    --url https://crm.contoso.local/contoso \
    --username alice --domain CONTOSO \
    --password "$SECRET" \
    --name prod
```

`--domain` is optional when the username is a UPN. Omit `--api-version` to
auto-negotiate — on-prem caps at v9.1 (v9.2 → HTTP 501); online uses v9.2.

**Online / Dataverse cloud (OAuth 2.0 client-credentials):**

```bash
crm profile add \
    --url https://contoso.crm.dynamics.com \
    --tenant-id <aad-tenant-id> --client-id <app-registration-id> \
    --password "$CLIENT_SECRET" \
    --name online
```

The app registration needs an **application user** in Dynamics with a suitable
security role. The token scope (`https://<host>/.default`) and authority
(`https://login.microsoftonline.com/<tenant>`) are derived automatically; the
bearer token is cached at `~/.crm/msal_token_cache.json` (mode `0600`) and reused
across invocations until it expires. Username/password/domain are not used in
this mode.

Attach a default solution and schema-name prefix so metadata write commands target
them by default:

```bash
crm profile add --url ... --default-solution CRMWorx --publisher-prefix cwx --name crmworx
```

State lives under `~/.crm/` — `CRM_HOME` is the only env var involved in
credential/connection resolution (it relocates that directory). No credentials
are ever read from the environment. (Other `CRM_*` vars tune unrelated runtime
behavior — logging, retries, stage-only — but never supply connection config.)

### Storing credentials

`crm profile add` stores the secret automatically:

- By default it goes into your OS keyring (macOS Keychain / Windows Credential
  Manager / Linux SecretService). Keyring support is built in — no extra install.
- On hosts with no keyring backend (typical WSL / headless CI) it falls back
  automatically to a `0600` plaintext entry inside the profile file. Force plaintext
  anywhere with `--store-password-plaintext` (`0600` on POSIX; perms unenforced on
  Windows).
- `crm profile set-password --profile NAME` stores or replaces the secret for an
  existing profile (NTLM password or OAuth client secret alike); never contacts the
  server. `crm profile delete-password --profile NAME` removes it.
- `crm profile list` shows each profile's storage type (keyring / plaintext / none).

Secret resolution order: `--password` (per-run override) > stored secret (plaintext
entry, then keyring) > interactive prompt (TTY only). No environment variable is
consulted.

## Use

### Default REPL

```bash
$ crm
◆ d365 [prod] ❯ connection whoami
◆ d365 [prod] ❯ query odata contacts --top 5 --select fullname,emailaddress1
◆ d365 [prod] ❯ help
◆ d365 [prod] ❯ quit
```

Bare `crm` only opens the REPL on an interactive terminal. A non-interactive
caller — `--json`, `CRM_NO_REPL=1`, or a piped/redirected stdin (agents, CI) —
exits 2 with a usage message pointing at `crm --help` instead of hanging on a
prompt. Explicit `crm repl` always launches.

Inside the REPL, commands are typed without the `crm` prefix
(`connection whoami`, not `crm connection whoami`), but a single leading `crm`
is tolerated and ignored so a copy-paste from the docs still works.

### One-shot subcommands

```bash
# Preflight diagnostic — pinpoints which layer is broken (DNS/TLS/version/auth)
crm connection doctor          # or: crm doctor
crm --json connection doctor   # JSON: {ok, data:{checks:[{check,ok,detail,hint}]}}

# Identity check
crm --json connection whoami

# Read
crm query odata contacts \
    --filter "statecode eq 0" --select fullname,telephone1 --top 10

# Write (preview first)
crm --dry-run entity create contacts \
    --data '{"firstname":"Rafel","lastname":"Shillo"}'

# Then actually create
crm --json entity create contacts \
    --data '{"firstname":"Rafel","lastname":"Shillo"}'

# Catch typo'd field names before the write (1-3 metadata GETs; composes with --dry-run)
crm --json entity create contacts --validate \
    --data '{"firstnaem":"Rafel"}'   # -> ok:false, did_you_mean {firstnaem: firstname}

# Verify after write/publish — repeatable --expect ATTR=VALUE asserts a field on the
# returned record (AND-gated, stringified); first mismatch exits 1 with meta{attr,expected,actual}.
crm --json entity get contacts <guid> --expect statecode=1           # assert a write landed
crm --json metadata attribute account industrycode --expect AttributeType=Picklist  # assert a publish landed

# FetchXML
crm query fetchxml accounts --file ./reports/by_industry.xml

# Solution export
crm solution export MyCustomSolution -o /tmp/snap.zip

# Org-to-org drift recipe: project a whole solution into one apply-consumable spec on dev,
# then preview schema drift on prod without writing anything.
crm solution export-spec MyCustomSolution -o desired.yaml      # dev (source)
crm --dry-run apply -f desired.yaml                            # prod (preview drift)

# Solution source control: unpack a zip to a diff-able tree and pack it back
# (offline — no connection needed; needs the Power Platform CLI `pac` on PATH)
crm solution extract --zipfile /tmp/snap.zip --folder ./src/MyCustomSolution
crm solution pack    --zipfile /tmp/built.zip --folder ./src/MyCustomSolution

# Validate a solution zip before import (offline by default; --against-org adds
# GUID-collision, existence, and package-version-ceiling checks against the connected
# org). Exits non-zero on any error.
crm solution validate /tmp/snap.zip

# Bulk export
crm data export opportunities -o /tmp/op.csv \
    --filter "statecode eq 0" --select name,estimatedvalue

# Bulk import (JSONL create; use --mode upsert --id-column <col> for PATCH by GUID,
# or --mode upsert --key <attr> to PATCH by alternate key when no GUID is available;
# --mode delete removes records by the same GUID/alternate-key resolution).
# READ-shape lookups (_<attr>_value, as export emits) auto-rebind to
# <nav>@odata.bind from metadata, so an export round-trips with no hand-editing.
crm data import accounts records.jsonl
crm data import accounts updates.jsonl --mode upsert --key accountnumber
crm data import accounts to_delete.jsonl --mode delete --key accountnumber
crm --dry-run data import accounts records.jsonl   # preview: zero writes, dry_run:true

# Server-side BulkDelete job — deletes all records matching a FetchXML query
# (FetchXML is converted server-side to QueryExpression; raw OData $filter is not accepted)
crm --json data delete contacts \
    --fetchxml '<fetch><entity name="contact"><filter><condition attribute="statecode" operator="eq" value="1"/></filter></entity></fetch>' \
    --wait --yes
crm --dry-run data delete contacts --fetchxml-file ./stale-contacts.xml  # preview matched count

# Discover the CLI surface (no connection needed) — for agents and scripts
crm --json describe
```

### Output modes

Every command supports `--json`:

```json
{
  "ok": true,
  "data": {...},
  "meta": {"count": 12, "entity_set": "contacts"}
}
```

`data` is a **curated, CLI-owned shape**, not a passthrough of the raw Web API
response (ADR 0008): list verbs put a **bare array** in `data` (`data[0]` is the
first row) with paging relocated to `meta.next_link`/`meta.count`; `@odata.*`
protocol keys are stripped everywhere; and the written record's GUID is surfaced
under the single normalized key `_entity_id` (with `_entity_id_url`) on
`create`/`update`/`delete`/`clone`/`entity get` — so one extraction rule works
across commands.

`query odata` follows `@odata.nextLink` automatically when you opt in: `--all`
merges every page into one `data` array (no `meta.next_link` in the result); `--max-records N` follows pages only until N rows are collected and caps the array there. When `--max-records` truncated the result (more rows existed), `meta.truncated: true` appears in the envelope. Default (neither flag) is unchanged: one server page, `meta.next_link` present when more pages exist — that default single page also sets `meta.has_more: true` and adds a `meta.warnings` advisory to use `--all`/`--max-records`; a `--count` that lands exactly on the server's 5000-row cap alongside a cursor gets a second warning that the count is a clamped lower bound. `query odata|fetchxml|saved|user` all share this behavior. A read that fits in one page gets neither signal.

Errors come back as `{"ok": false, "error": "...", "meta": {"status": 404, "code": "...", "category": "not_found", "retryable": false}}`.
`meta.category` is a closed enum (`not_found`, `auth_failed`, `forbidden`,
`concurrency_conflict`, `duplicate_detected`, `validation`, `throttled`,
`server_error`, `transport_error`) and `meta.retryable` flags the transient classes.
The backend auto-retries the transient transport / `429` / `5xx` classes for
idempotent verbs (`concurrency_conflict` (412) is flagged `retryable` too but is
never auto-retried — refetch a fresh ETag and retry), but a non-idempotent `POST`
(record create, action, associate) is **not** retried by default — a lost response
may mean the write already landed. Pass
`--retry-on-ambiguous` (env: `CRM_RETRY_ON_AMBIGUOUS`) to opt back into retrying
POSTs when re-sending is acceptable. `$batch` keeps its own retry loop regardless.

`meta.warnings` is an array of non-fatal advisories — the one place to scan for
staged-but-unpublished changes, created-but-read-back-failed records, and
partial-optionset failures (which also surface `meta.completed_steps` /
`meta.failed_stage` on the error envelope).

## Command Groups

| Group        | Purpose                                                    |
|--------------|------------------------------------------------------------|
| `connection` | Profiles, WhoAmI, preflight diagnostic (`doctor`), reachability checks |
| `connectionrole` | Connection roles: `create` a role, `scope` it to an entity type, `match` two roles as reciprocal partners |
| `entity`     | Record CRUD (get/create/update/upsert/delete); `upsert` accepts `--key ATTR[,ATTR...]` to match by alternate key instead of a primary GUID (key values read from `--data`, key attrs stripped from the body), plus `--if-none-match` for a create-only upsert (412 if the record exists); `clone` (single-record clone, lookups rebound to the same parents, `--override`/`--unset`; `--with-children` also clones the custom 1:N child rows, repointing them to the new parent — continue-and-report on failure, `--skip-child-entity` prunes); `children` (per-1:N related-record counts via chunked `$batch`, not one query per relationship) |
| `query`      | OData v4 and FetchXML queries; `query odata --apply` runs server-side `$apply` aggregation / group-by / distinct |
| `metadata`   | Entity / attribute / relationship CRUD; global option set CRUD |
| `apply`      | Declarative desired-state from a YAML/JSON spec (`apply -f spec.yaml`); declares publishers, solutions, entities, option sets, web resources, security roles, and plug-in assemblies / types / steps / images; `--prune` opts in to solution-bounded deletion of org components the spec no longer declares (gated; preview with `--dry-run`) |
| `scaffold`   | Quick one-table shorthand: `scaffold table DISPLAY --column ...` creates an entity + N columns in one publish |
| `solution`   | List / info / components (`--save`/`--diff` for drift detection) / dependencies (uninstall-blocker preview) / missing-components (pre-import dependency check against the target org) / add-component / remove-component / set-version / export / import / import-result / extract / pack / validate solutions; `export-spec <unique_name> [-o FILE]` projects a whole solution (entities, security roles, web resources) into one `apply`-consumable desired-state spec (org-to-org drift recipe source); managed-upgrade lifecycle: clone-as-patch / stage-and-upgrade (holding import, `--promote`) / apply-upgrade (separate promote) / uninstall |
| `data`       | Bulk CSV/JSON dataset export + JSONL/CSV import via `$batch`; `--mode upsert`/`--mode delete` resolve records by GUID (`--id-column`) or alternate key (`--key`); `data delete` submits a **server-side BulkDelete async job** for records matching a FetchXML query |
| `webresource` | Create/update/get/list/delete web resources (HTML/JS/CSS/images); `push <DIR> --prefix <p>` bulk-upserts a directory tree (name = `<prefix>_<relpath>`, type inferred from extension, skips byte-identical, publishes once); set as app icons |
| `view`       | System views (savedquery): `list` / `create`; `edit-columns` to add, remove, resize, or reorder grid columns in place (keeps layoutxml + fetchxml coupled); `set-order` to replace, append, or clear the sort order; `add-filter` / `remove-filter` to edit FetchXML filter conditions in place — all without manual XML editing |
| `form`       | Entity main forms (systemform): list, clone to another table, export formxml; add-field / remove-field / set-field / set-field-props to edit form layouts; add / remove / rename / move tab & section to edit the form's tab/section structure; add-library / add-handler / remove-handler / list-handlers to wire and inspect JS event handlers — all without manual FormXml editing |
| `chart`      | System & user charts (savedqueryvisualization / userqueryvisualization): list / get / create / delete; author from datadescription + presentationdescription XML or a web-resource visualization; edit in-place with `update` (replace XML columns, name, description, or series chart type), `set-fetch` (swap the inner `<fetch>` while keeping categories), `add-series` / `remove-series` (add or remove an aggregate series), and `set-groupby` (change the grouping column) — all headlessly (no chart designer) |
| `sitemap`    | Live SiteMap navigation editors — edit an *existing* sitemap in place over a read-modify-write path (GET → mutate → PATCH) without re-authoring the whole document: `add-area`, `add-group`, `add-subarea` (exactly one of `--entity` / `--url` / `--dashboard`, with `--entity` and `--dashboard` validated to exist and `--pass-params` to append context params to a `--url`), `move-node` (reorder a node within its parent via `--before` / `--after` / `--index`), `remove-node` (`--comment-out` to soft-delete), `set-title` / `set-description` (set localized `<Titles>`/`<Descriptions>` per LCID on any node). Complements `app build-sitemap` / `app set-sitemap` which POST a whole new SiteMapXml. Find a sitemap's GUID with `crm query odata sitemaps --select sitemapname,sitemapid` |
| `dashboard`  | Organization-owned system dashboards (systemform type=0): list / get / create / delete; `add-chart` splices a ChartGrid tile (chart + grid) into an existing dashboard's FormXml; `add-view` splices a view-only grid tile; `add-iframe` embeds an IFRAME tile; `add-webresource` embeds a web-resource tile (validates existence, warns if not form-enabled); `remove-component` removes exactly one tile selected by `--cell-id` / `--index` / `--view` / `--chart` / `--url`. Create from a FormXml file, headlessly (no dashboard designer). Interactive (type-10) dashboards are not API-creatable and are rejected with a clear error |
| `theme`      | Application themes (product branding): list / get / create / update / publish; set colors via `--set FIELD=VALUE` and a logo web resource, then `publish` to make one the active org theme (`PublishTheme`). Themes are **not** solution-aware (they don't travel with a solution export) |
| `report`     | Register and manage custom reports headlessly: list / get / create / delete; `create --body-file` uploads an SSRS RDL (`reporttypecode 1`); `create --url` registers a link report (`reporttypecode 3`); `create --org` makes it org-wide (`ispersonal=false`); `set-category` files it under sales / service / marketing / administrative. Reports are solution-aware |
| `plugin`     | Register/update/unregister plug-in assemblies, plug-in types, webhook service endpoints, SDK message processing steps (bind to a plug-in type or a service endpoint), and step entity images |
| `security`   | Create security roles (`create-role`); grant / replace a role's privileges (`set-role-privileges`); list and assign roles to users or teams; show a user's effective privileges (incl. team-inherited); grant / revoke / list record sharing (POA) |
| `fieldsec`   | Column-level (field) security: create field security profiles, grant `--read`/`--create`/`--update` column permissions, assign profiles to users or teams, list / get |
| `dup`        | Duplicate-detection rules: create a rule, add match conditions, publish (async) / unpublish, and `check` a candidate record against the published rules (`RetrieveDuplicates`); list / get |
| `workflow`   | List, activate/deactivate, delete, trigger, clone, export, import, update (metadata and on-prem whole-XAML replace), and migration-assess (classic-workflow → cloud-flow readiness) D365 workflow definitions |
| `sla`        | Full SLA lifecycle: `create` an SLA for a target entity (auto-enabling `IsSLAEnabled` on that entity), `add-kpi` to attach KPI / SLA-item conditions (FetchXML `--applicable-when` / `--success-criteria`), and `activate` (activates backing workflows first, then the SLA, with structured per-step compile-error reporting) |
| `translation` | Export / import localizable display labels for a solution (`ExportTranslation` / `ImportTranslation`) |
| `action`     | Call arbitrary OData functions and actions                 |
| `audit`      | Retrieve server-side D365 audit change history (`audit history` / `audit detail`) — distinct from the local `session audit` journal |
| `session`    | Local session state, command history, and audit journal    |
| `completion` | Print or install shell completion (bash/zsh/fish/powershell); install caches the script + prints the rc line to source |
| `self-update` | Upgrade a frozen (install-script) binary in place and re-sync installed agent skills + shell completion; `--check` reports current vs latest |

For a continuous redeploy loop during front-end development, pipe `entr` (or `watchexec`) into `webresource push`:

```bash
# re-push whenever any JS file changes (find is portable; bash ** globstar is off by default)
find webresources -type f -name '*.js' | entr crm webresource push webresources --prefix cwx

# watchexec equivalent
watchexec -e js,css,html -- crm webresource push webresources --prefix cwx
```

See [how-to/webresource](docs/how-to/webresource.md) for the full `push` semantics (naming convention, upsert logic, dry-run).

The `metadata` group covers both browsing and write verbs. `metadata describe
<entity>` returns a one-shot, read-only write-readiness brief — entity set, primary
id/name, writable columns + required levels, lookup `@odata.bind` keys + targets, and
picklist/state/status options — everything needed to build a valid record payload in
one call. Write verbs new in 0.5.0:

- `metadata add-attribute <entity> --kind <k>` — add a column (15 kinds, incl. `customer` composite lookup); `--type rollup` or `--type calculated` layers rollup/calculated on top of a supported column kind (requires `--formula-file <xaml>` — XAML is editor-authored and sent verbatim)
- `metadata create-one-to-many` / `create-many-to-many` — relationships; `create-one-to-many` accepts `--hierarchical` to mark the 1:N as a parent/child hierarchy (self-referencing entity required)
- `metadata update-relationship <schema>` — update cascade/menu on an existing relationship; `--hierarchical / --no-hierarchical` sets `IsHierarchical` on a 1:N (rejected for N:N)
- `metadata can-relate <entity> --as referenced|referencing|many-to-many` — read-only eligibility check before creating a relationship; `--valid-partners` lists legal partner tables (N:N partner list is org-global, not entity-scoped)
- `metadata create-entity` accepts `--data-provider`, `--data-source`, `--external-name`, `--external-collection-name` to create a VIRTUAL (external-data-backed) table; on v9.1 virtual tables are read-only and require the data-provider record to exist first
- `metadata list-optionsets` / `get-optionset` / `create-optionset` / `update-optionset` / `delete-optionset` — global option sets
- `metadata keys <entity>` / `create-key <entity> --key-attributes col1,col2` / `delete-key <entity> <key>` — read, create, and drop alternate keys (the natural-key index that `entity upsert --key` / `data import --mode upsert --key` match on)
- `metadata delete-entity <logical-name>` — drop a custom entity (gated)
- `metadata delete-attribute <entity> <attribute>` / `delete-relationship <schema-name>` — drop a custom column or relationship (gated)
- `metadata dependencies <target> [--kind entity|attribute|optionset|relationship] [--for delete|dependents|required]` — pre-delete dependency preview: returns `can_delete` + `blockers[]`; `--for required` lists what the target itself depends on (`RetrieveRequiredComponents`); pass `--check-dependencies` on a `metadata delete-*` verb (delete-entity, delete-attribute, delete-relationship, delete-optionset) to fold this into the preview
- `metadata export-spec <logical_name> [--with-views] [--with-relationships] [-o FILE]` — project a live entity into a `crm apply -f`–consumable desired-state spec (round-trip). Pure GETs; `-o` writes the bare YAML directly. Custom columns that cannot be represented (permission limits, unsupported formats) are reported in `meta.warnings` instead of silently dropped.
- `metadata clone-entity <source> <new-schema-name>` — duplicate a custom entity (skeleton + opt-in `--with-forms` / `--with-views` / `--with-workflows` / `--with-charts`, or `--with-all`) purely over the Web API. The ribbon is not cloned (no API write path); N:N and parent-side relationships are not cloned.
- `metadata status-add <entity> --state <stateCode> --label <text>` — add a `statuscode` option tied to a state (`InsertStatusValue`); `--value` is optional (server assigns the next value with publisher prefix if omitted)
- `metadata state-relabel <entity> --value <stateCode> --label <text>` — relabel a `statecode` state option such as Active/Inactive (`UpdateStateValue`); `--merge-labels` preserves labels in other languages
- `metadata create-mapping <relationship> --from <attr> --to <attr>` — create a field/attribute mapping on a 1:N relationship; `--auto` bulk-generates the likely mappings via `AutoMapEntity` (replaces any existing maps for the pair)
- `metadata changes [--since <stamp>] [--entity <logical> ...] [--attributes]` — retrieve new/changed metadata since a version stamp (`RetrieveMetadataChanges`); save the returned `server_version_stamp` and pass it as `--since` next run to get only the delta. Omit `--since` for a baseline snapshot. Omit `--entity` to query every table (expensive on a baseline — scope with `--entity` when possible)

`crm apply -f spec.yaml` stands up a whole table (publisher, solution, entity,
columns, option sets, relationships, views, web resources, security roles, and
plug-in assemblies with their types, steps, and images) from one declarative
spec, in dependency order, publishing once at the end (when a publishable
component changed — security roles and plug-in components are not published). It
is **convergent**: a component that already exists is reconciled against the spec
— left untouched when it matches (`skipped`), updated in place when an allowed
field drifts (`updated`), or refused with no write when the divergence would
require a destructive drop-and-recreate (`replace_blocked`, `ok=false`, exit 1).
The full envelope is
`{ok, data:{applied, updated, skipped, replace_blocked, pruned, planned, failed}, meta:{staged}}`.
`--dry-run` reads the live org and reports the full drift without writing —
`planned` (would create), `updated` (would update), `replace_blocked`, and
`pruned` — so you can preview exactly what an apply would converge; in `--json`
mode the envelope's `meta` also carries `dry_run: true`. See
[how-to/apply](docs/how-to/apply.md).

`crm scaffold table DISPLAY --column 'DISPLAY:KIND[:opts]' ...` is the quick
one-liner path: builds an entity + N columns in memory and runs them through the
same apply engine in one publish. Use it for simple tables; use `apply` when you
need publishers, solutions, option sets, relationships, views, web resources, or
security roles. See [how-to/scaffold](docs/how-to/scaffold.md).

Use `crm <group> --help` for command-level details.

## Testing

```bash
# Unit tests (mocked HTTP, no server needed)
pip install -e .[dev]
pytest crm/tests/test_core.py -v

# E2E (requires live server credentials)
# The E2E fixture reads these env vars to SEED a temporary profile (the CLI itself
# resolves from the saved profile, not the env).
export D365_URL=... D365_USERNAME=... D365_PASSWORD=... D365_DOMAIN=...
CRM_FORCE_INSTALLED=1 pytest crm/tests/ -v -s
```

See `crm/tests/TEST.md` for the full test plan and recorded results.

## Architecture

```
crm (Click + REPL)
    │
    ├─ core/        record CRUD, queries, metadata, solutions, export, session
    └─ utils/
         ├─ d365_backend.py   requests + requests_ntlm → live Web API
         └─ repl_skin.py      shared REPL chrome
```

See `D365.md` in the project root for the full SOP.

## Limits / Out of Scope

- IFD (claims) auth, certificate credentials, and OAuth flows other than
  client-credentials (device-code, interactive, ROPC) — on-prem uses NTLM,
  cloud uses OAuth 2.0 client-credentials (secret) against the public cloud only.
- Plugin / workflow source code deployment — use solution import for that.
- Audit log / report execution — out of scope; can be added as an extension.

## License

Copyright 2026 Ahmed Gharib, Principal Advanced Analytics Engineer at ITWorx
(ahmed.gharib@itworx.com).

Licensed under the [PolyForm Noncommercial License 1.0.0](LICENSE) — free to use,
modify, and share for **noncommercial purposes** (personal, research, education,
charity, government). This applies to the source **and** the released binaries.

**Commercial use by any organization requires a separate written license from the
copyright holder.** Contact ahmed.gharib@itworx.com to arrange one.
