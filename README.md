# crm

Stateful CRM CLI for **Microsoft Dynamics 365 Customer Engagement** — on-premises
v9.x (NTLM) or Dataverse online (OAuth). Wraps the real Dataverse Web API (OData
v4) over HTTPS; the same commands run against both targets.

## Why

D365 CE on-prem ships with a GUI and a SOAP/.NET SDK. There's no first-party CLI
optimized for AI agents or shell scripting. This harness gives you:

- `crm` in your `PATH`
- One-shot subcommands for record CRUD, OData and FetchXML queries, metadata
  introspection, and solution lifecycle
- A stateful REPL for ad-hoc admin work
- `--json` everywhere for agent consumption
- `--dry-run` to preview the HTTP request before issuing it

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

## Use

### Default REPL

```bash
$ crm
◆ d365 [prod] ❯ connection whoami
◆ d365 [prod] ❯ query odata contacts --top 5 --select fullname,emailaddress1
◆ d365 [prod] ❯ help
◆ d365 [prod] ❯ quit
```

### One-shot subcommands

```bash
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

# FetchXML
crm query fetchxml accounts --file ./reports/by_industry.xml

# Solution export
crm solution export MyCustomSolution -o /tmp/snap.zip

# Bulk CSV
crm data export opportunities -o /tmp/op.csv \
    --filter "statecode eq 0" --select name,estimatedvalue

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

`meta.warnings` is an array of non-fatal advisories — the one place to scan for
staged-but-unpublished changes, created-but-read-back-failed records, and
partial-optionset failures (which also surface `meta.completed_steps` /
`meta.failed_stage` on the error envelope).

## Command Groups

| Group        | Purpose                                                    |
|--------------|------------------------------------------------------------|
| `connection` | Profiles, WhoAmI, reachability checks                      |
| `entity`     | Record CRUD (get/create/update/upsert/delete)              |
| `query`      | OData v4 and FetchXML queries                              |
| `metadata`   | Entity / attribute / relationship CRUD; global option set CRUD |
| `apply`      | Declarative desired-state from a YAML/JSON spec (`apply -f spec.yaml`) |
| `solution`   | List / info / components / export / import solutions       |
| `data`       | Bulk CSV/JSON dataset export                               |
| `action`     | Call arbitrary OData functions and actions                 |
| `session`    | Local session state and command history                    |

The `metadata` group covers both browsing and write verbs. New in 0.5.0:

- `metadata add-attribute <entity> --kind <k>` — add a column (14 kinds)
- `metadata create-one-to-many` / `create-many-to-many` — relationships
- `metadata list-optionsets` / `get-optionset` / `create-optionset` / `update-optionset` / `delete-optionset` — global option sets
- `metadata delete-entity <logical-name>` — drop a custom entity (gated)

`crm apply -f spec.yaml` stands up a whole table (publisher, solution, entity,
columns, option sets, relationships, views) from one declarative spec, in
dependency order, publishing once at the end — idempotent on re-apply, with
`--dry-run` plan preview and `--stage-only` support. See
[how-to/apply](docs/how-to/apply.md).

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
