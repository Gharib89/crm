# crm

Stateful CRM CLI for **Microsoft Dynamics 365 Customer Engagement on-premises,
version 9.x**. Wraps the real Dataverse Web API (OData v4) over HTTPS with NTLM
(Windows Integrated) authentication.

## Why

D365 CE on-prem ships with a GUI and a SOAP/.NET SDK. There's no first-party CLI
optimized for AI agents or shell scripting. This harness gives you:

- `crm` in your `PATH`
- One-shot subcommands for record CRUD, OData and FetchXML queries, metadata
  introspection, and solution lifecycle
- A stateful REPL for ad-hoc admin work
- `--json` everywhere for agent consumption
- `--dry-run` to preview the HTTP request before issuing it

## Requirements

| Requirement              | Version            | Notes                                  |
|--------------------------|--------------------|----------------------------------------|
| Python                   | ≥ 3.9              |                                        |
| Dynamics 365 CE on-prem  | 9.0 / 9.1 / 9.2    | Reachable from your machine over HTTPS |
| Auth                     | NTLM (Windows Integrated) | OAuth/IFD is out of scope here. |

The D365 server is a **hard runtime dependency** — without it the CLI has nothing
to talk to. E2E tests fail loudly if credentials are missing.

## Install

### Option 1: Prebuilt binary (no Python required)

Download the latest release for your platform from
<https://github.com/Gharib89/crm/releases/latest>:

- Linux x86_64: `crm-linux-x86_64`
- Windows x86_64: `crm-windows-x86_64.exe`

**Linux:**

```bash
curl -L -o crm https://github.com/Gharib89/crm/releases/latest/download/crm-linux-x86_64
chmod +x crm
sudo mv crm /usr/local/bin/
crm --version
```

**Windows (PowerShell):**

The downloaded `.exe` will be marked as coming from the internet and may
trigger a SmartScreen warning on first run. Unblock it once:

```powershell
Unblock-File .\crm-windows-x86_64.exe
Rename-Item .\crm-windows-x86_64.exe crm.exe
# Move crm.exe somewhere on your PATH
.\crm.exe --version
```

Glibc compatibility: the Linux binary is built on Ubuntu 22.04 (glibc 2.35)
and runs on any Linux distribution with glibc ≥ 2.35.

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

```bash
export D365_URL="https://crm.contoso.local/contoso"
export D365_USERNAME="alice"
export D365_PASSWORD="..."        # never persisted to disk
export D365_DOMAIN="CONTOSO"      # optional if username is a UPN
export D365_AUTH="ntlm"           # only supported mode
export D365_API_VERSION="v9.2"    # optional; default v9.2
```

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

Errors come back as `{"ok": false, "error": "...", "meta": {"status": 404, "code": "..."}}`.

## Command Groups

| Group        | Purpose                                                    |
|--------------|------------------------------------------------------------|
| `connection` | Profiles, WhoAmI, reachability checks                      |
| `entity`     | Record CRUD (get/create/update/upsert/delete)              |
| `query`      | OData v4 and FetchXML queries                              |
| `metadata`   | Entity / attribute / relationship introspection            |
| `solution`   | List / info / components / export / import solutions       |
| `data`       | Bulk CSV/JSON dataset export                               |
| `action`     | Call arbitrary OData functions and actions                 |
| `session`    | Local session state and command history                    |

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

- OAuth, IFD (claims), and certificate auth — only NTLM is supported here.
- Plugin / workflow source code deployment — use solution import for that.
- Audit log / report execution — out of scope; can be added as an extension.
- D365 online (Dataverse cloud) — works in theory but auth differs and is unconfigured.
