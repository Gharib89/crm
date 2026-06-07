# crm

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
- `--dry-run` to preview the HTTP request before issuing it
- An append-only JSONL audit journal (`~/.crm/audit/<session>.jsonl`) of every
  mutating command; `crm session audit` to review it
- `--cache-metadata` for a persistent per-profile entity-definition cache
  (speeds up repeated one-shot agent calls; env: `CRM_CACHE_METADATA=1`)

## Documentation

Full docs (install, configure, per-group how-tos, generated CLI reference) live at
[crm-cli-docs.pages.dev](https://crm-cli-docs.pages.dev/).

Two surfaces feed AI agents:

- **`crm/skills/SKILL.md`** — the agent skill loaded by skill-aware harnesses
  (Claude Code, Copilot CLI, …) after `crm skill install`.
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
SmartScreen may warn on first run. To uninstall, download `install.ps1` and run
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

### Option 2: From source (development)

```bash
# From source (local dev)
pip install -e .

# Verify the command is on PATH
which crm
crm --version
```

## Configure

Credentials come from environment variables (preferred) or a saved profile.

**On-prem (NTLM, default):**

```bash
export D365_URL="https://crm.contoso.local/contoso"
export D365_USERNAME="alice"
export D365_PASSWORD="..."        # never persisted to disk
export D365_DOMAIN="CONTOSO"      # optional if username is a UPN
export D365_AUTH="ntlm"           # default
export D365_API_VERSION="v9.1"    # on-prem caps at v9.1 (v9.2 → HTTP 501); online uses v9.2
```

**Online / Dataverse cloud (OAuth 2.0 client-credentials):**

```bash
export D365_URL="https://contoso.crm.dynamics.com"
export D365_AUTH="oauth"
export D365_TENANT_ID="<aad-tenant-id>"
export D365_CLIENT_ID="<app-registration-id>"
export D365_CLIENT_SECRET="..."   # never persisted to disk
```

The app registration needs an **application user** in Dynamics with a suitable
security role. The token scope (`https://<host>/.default`) and authority
(`https://login.microsoftonline.com/<tenant>`) are derived automatically; the
bearer token is cached at `~/.crm/msal_token_cache.json` (mode `0600`) and reused
across invocations until it expires. Username/password/domain are not used in
this mode.

> **Pin your profile when both credential sets are present.** If your environment
> defines both `CRM_*`/NTLM and `D365_*`/OAuth variables, a bare `crm` command lets
> the `D365_*` vars override the active profile and silently connect to cloud.
> Always pass `--profile <name>` and confirm the real target with
> `crm --json connection whoami` (check the `@odata.context` host).

Or save a reusable profile (no password):

```bash
crm connection connect \
    --url https://crm.contoso.local/contoso \
    --username alice --domain CONTOSO \
    --profile-name prod
```

State lives under `~/.crm/` (override with `CRM_HOME`).

### Storing credentials once

By default secrets are never persisted. To configure once:

- `crm connection connect ... --store-password` saves the secret in your OS
  keyring (macOS Keychain / Windows Credential Manager / Linux SecretService).
  Requires the optional extra: `pip install crm[keyring]`.
- For headless/CI hosts with no keyring, `--store-password-plaintext` writes the
  secret into the profile file (`0600` on POSIX; perms unenforced on Windows).
- `crm connection delete-password --profile NAME` removes a stored secret.
- `crm connection profiles` shows each profile's storage type (keyring / plaintext / none).

Resolution order: `--password` > `D365_PASSWORD`/`CRM_PASSWORD` (env/.env) >
stored secret (keyring or plaintext) > interactive prompt (TTY only).

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

# Solution source control: unpack a zip to a diff-able tree and pack it back
# (offline — no connection needed; needs SolutionPackager.exe from CoreTools)
crm solution extract --zipfile /tmp/snap.zip --folder ./src/MyCustomSolution
crm solution pack    --zipfile /tmp/built.zip --folder ./src/MyCustomSolution

# Bulk export
crm data export opportunities -o /tmp/op.csv \
    --filter "statecode eq 0" --select name,estimatedvalue

# Bulk import (JSONL create; use --mode upsert --id-column <col> for PATCH)
crm data import accounts records.jsonl
crm --dry-run data import accounts records.jsonl   # preview: zero writes, dry_run:true

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
| `entity`     | Record CRUD (get/create/update/upsert/delete)              |
| `query`      | OData v4 and FetchXML queries                              |
| `metadata`   | Entity / attribute / relationship CRUD; global option set CRUD |
| `apply`      | Declarative desired-state from a YAML/JSON spec (`apply -f spec.yaml`) |
| `scaffold`   | Quick one-table shorthand: `scaffold table DISPLAY --column ...` creates an entity + N columns in one publish |
| `solution`   | List / info / components (`--save`/`--diff` for drift detection) / dependencies (uninstall-blocker preview) / add-component / remove-component / set-version / export / import / import-result / extract / pack solutions |
| `data`       | Bulk CSV/JSON dataset export + JSONL/CSV import via `$batch` |
| `webresource` | Create/update/get/list web resources (HTML/JS/CSS/images); set as app icons |
| `plugin`     | Register/update/unregister plug-in assemblies and SDK message processing steps |
| `security`   | List and assign security roles to users or teams           |
| `action`     | Call arbitrary OData functions and actions                 |
| `session`    | Local session state, command history, and audit journal    |

The `metadata` group covers both browsing and write verbs. `metadata describe
<entity>` returns a one-shot, read-only write-readiness brief — entity set, primary
id/name, writable columns + required levels, lookup `@odata.bind` keys + targets, and
picklist/state/status options — everything needed to build a valid record payload in
one call. Write verbs new in 0.5.0:

- `metadata add-attribute <entity> --kind <k>` — add a column (14 kinds)
- `metadata create-one-to-many` / `create-many-to-many` — relationships
- `metadata list-optionsets` / `get-optionset` / `create-optionset` / `update-optionset` / `delete-optionset` — global option sets
- `metadata delete-entity <logical-name>` — drop a custom entity (gated)
- `metadata delete-attribute <entity> <attribute>` / `delete-relationship <schema-name>` — drop a custom column or relationship (gated)
- `metadata dependencies <target> [--kind entity|attribute|optionset|relationship] [--for delete|dependents]` — pre-delete dependency preview: returns `can_delete` + `blockers[]`; pass `--check-dependencies` on a `metadata delete-*` verb (delete-entity, delete-attribute, delete-relationship, delete-optionset) to fold this into the preview
- `metadata export-spec <logical_name> [--with-views] [--with-relationships] [-o FILE]` — project a live entity into a `crm apply -f`–consumable desired-state spec (round-trip). Pure GETs; `-o` writes the bare YAML directly.

`crm apply -f spec.yaml` stands up a whole table (publisher, solution, entity,
columns, option sets, relationships, views) from one declarative spec, in
dependency order, publishing once at the end — idempotent on re-apply, with
`--dry-run` plan preview and `--stage-only` support. See
[how-to/apply](docs/how-to/apply.md).

`crm scaffold table DISPLAY --column 'DISPLAY:KIND[:opts]' ...` is the quick
one-liner path: builds an entity + N columns in memory and runs them through the
same apply engine in one publish. Use it for simple tables; use `apply` when you
need publishers, solutions, option sets, relationships, or views. See
[how-to/scaffold](docs/how-to/scaffold.md).

Use `crm <group> --help` for command-level details.

## Testing

```bash
# Unit tests (mocked HTTP, no server needed)
pip install -e .[dev]
pytest crm/tests/test_core.py -v

# E2E (requires live server credentials)
export D365_URL=... D365_USERNAME=... D365_PASSWORD=...
CRM_FORCE_INSTALLED=1 pytest crm/tests/ -v -s
```

See `tests/TEST.md` for the full test plan and recorded results.

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
