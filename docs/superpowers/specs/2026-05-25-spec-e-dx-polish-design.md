# Spec E — DX Polish (v0.6.0)

**Date:** 2026-05-25  
**Issue:** #7  
**Version bump:** 0.5.x → **0.6.0**  
**Delivery:** Single PR `feat/spec-e-dx-polish`

---

## 1. Scope

Eight developer-experience improvements delivered in one PR:

| # | Feature | Description |
|---|---------|-------------|
| 1 | `cli.py` split | Extract command groups into `crm/commands/` package |
| 2 | `--log-level` / `--log-format` | HTTP transcript + structured logs |
| 3 | `--auth-scheme` / Kerberos | `requests_negotiate_sspi` as NTLM alternative |
| 4 | `crm init` | Env-template generator + interactive profile wizard |
| 5 | `query count` | `RetrieveTotalRecordCount` wrapper |
| 6 | `metadata list-actions` | OData action browser |
| 7 | `metadata list-functions` | OData function browser |
| 8 | REPL tab completion | Lazy entity-name cache + readline completer |

---

## 2. File Layout

### 2.1 New `crm/commands/` package

`cli.py` is reduced to the root Click group, `CLIContext`, `emit()`, `_sanitize()`, and `pass_ctx`. All command groups move to dedicated modules:

```
crm/
  cli.py              # root group, CLIContext, shared helpers only
  commands/
    __init__.py       # re-exports all groups for cli.py to add_command()
    entity.py         # entity get/create/update/delete/upsert
    query.py          # query/fetch  +  new: query count
    metadata.py       # metadata + new: list-actions, list-functions
    solution.py       # solution import/export
    workflow.py       # workflow trigger
    batch.py          # batch ops
    session.py        # session mgmt
    repl.py           # REPL entry + MetadataCache + tab completion
```

`cli.py` wires them via `cli.add_command()` calls, identical to today's pattern — just importing from `crm.commands` instead of defining inline.

### 2.2 `CLIContext` additions

```python
class CLIContext:
    log_level: str   = "warning"   # debug|info|warning|error
    log_format: str  = "text"      # text|json-line
    auth_scheme: str = "ntlm"      # ntlm|kerberos|negotiate
```

`__init__` configures `logging` based on `log_level` / `log_format` immediately (before any backend is created).

---

## 3. Logging

### 3.1 CLI surface

Two new root-group options (alongside `--json`, `--dry-run`, `--profile`):

```
--log-level [debug|info|warning|error]   (env: CRM_LOG_LEVEL, default: warning)
--verbose                                 alias: sets --log-level debug
--log-format [text|json-line]            (env: CRM_LOG_FORMAT, default: text)
```

### 3.2 Implementation

- Python stdlib `logging` only — no new dependencies.
- `crm.http` logger in `d365_backend.py` emits one `DEBUG` record per outgoing request (method, URL, elapsed ms) and one per response (status code, elapsed ms).
- `CLIContext.__init__` configures the `crm` root logger: sets `level`, attaches a `CrmLogHandler` (thin `StreamHandler` subclass writing to `stderr`), and removes any default handlers. Uses `logger.addHandler()` directly — not `logging.basicConfig()` which is a no-op once handlers exist.
- `CrmLogHandler` has two format modes:
  - **text**: `[LEVEL] METHOD url (Nms)`
  - **json-line**: `json.dumps({"level": ..., "method": ..., "url": ..., "ms": ...})`

### 3.3 Output examples

```
# text (--verbose)
[DEBUG] GET https://crm.corp/api/data/v9.2/accounts (142ms)
[DEBUG] 200 OK (142ms)

# json-line (--verbose --log-format json-line)
{"level":"debug","event":"request","method":"GET","url":"https://crm.corp/api/data/v9.2/accounts"}
{"level":"debug","event":"response","status":200,"ms":142}
```

---

## 4. Auth Scheme + Kerberos

### 4.1 CLI surface

New root-group option:

```
--auth-scheme [ntlm|kerberos|negotiate]  (env: CRM_AUTH_SCHEME, default: ntlm)
```

### 4.2 `ConnectionProfile` change

`ConnectionProfile` gains `auth_scheme: str = "ntlm"`. Profile JSON files written by `crm init` (§5) include this field.

### 4.3 `D365Backend._make_auth()`

```python
def _make_auth(self) -> AuthBase:
    if self.profile.auth_scheme == "ntlm":
        return HttpNtlmAuth(...)
    # kerberos / negotiate
    try:
        from requests_negotiate_sspi import HttpNegotiateAuth
    except ImportError:
        raise D365Error(
            "Kerberos/Negotiate auth requires 'requests_negotiate_sspi'. "
            "Install it: pip install crm[kerberos]"
        )
    return HttpNegotiateAuth()
```

### 4.4 `setup.py` optional extra

```python
extras_require={
    "kerberos": ["requests_negotiate_sspi"],
}
```

`requests_negotiate_sspi` is Windows-only; the extra is not added to the default install.

---

## 5. `crm init`

New top-level command (not a command group), registered directly on the root Click group.

### 5.1 `--template` mode

Writes `.env.example` to CWD:

```bash
# Dynamics 365 connection settings — copy to .env and fill in values
CRM_URL=https://your-crm.corp/
CRM_USERNAME=DOMAIN\\user
CRM_PASSWORD=
CRM_DOMAIN=CORP
CRM_AUTH_SCHEME=ntlm   # ntlm | kerberos | negotiate
CRM_LOG_LEVEL=warning  # debug | info | warning | error
CRM_LOG_FORMAT=text    # text | json-line
```

Exits with error if `.env.example` already exists (no silent overwrite).

### 5.2 Interactive wizard (no args)

Prompts: URL → username → password (hidden) → domain → auth-scheme → profile name.  
Writes `~/.crm/profiles/<name>.json`. Confirms before overwriting an existing profile.

---

## 6. New Commands

### 6.1 `query count <entity-logical-name>`

Calls `RetrieveTotalRecordCount` unbound action:

```
POST /api/data/v9.x/RetrieveTotalRecordCount
Body: {"EntityNames": ["<entity>"]}
```

Returns `{"count": N}` in JSON mode, plain `N` in text mode.  
Implemented in `crm/commands/query.py` + a thin wrapper in `crm/core/query.py`.

### 6.2 `metadata list-actions`

Fetches `$metadata` XML, extracts `<Action>` elements, emits a table:

| Name | Parameters |
|------|-----------|
| RetrieveTotalRecordCount | EntityNames (Collection(Edm.String)) |
| … | … |

### 6.3 `metadata list-functions`

Same as above but for `<Function>` elements. Both commands support `--json` for machine-readable output.

Both implemented in `crm/commands/metadata.py` + `crm/core/metadata.py`.

---

## 7. REPL Tab Completion

### 7.1 `MetadataCache`

New class in `crm/commands/repl.py`:

```python
class MetadataCache:
    def __init__(self) -> None:
        self._entities: list[str] | None = None

    def entities(self, backend: D365Backend) -> list[str]:
        if self._entities is None:
            self._entities = meta_mod.list_entity_names(backend)
        return self._entities
```

`meta_mod.list_entity_names(backend)` — new thin function in `crm/core/metadata.py`:

```python
def list_entity_names(backend: D365Backend) -> list[str]:
    data = backend.get("EntityDefinitions?$select=LogicalName")
    return [e["LogicalName"] for e in data.get("value", [])]
```

### 7.2 Readline completer

On REPL entry, register a completer via `readline.set_completer()`. The completer:

1. Splits the current line buffer.
2. Identifies the token position (e.g., after `entity get`, `query fetch`, etc.).
3. Returns entity names matching the current prefix when in an entity-name argument slot.
4. Falls back to command-name completion otherwise.

On first `<Tab>`, if entity names aren't cached yet, fetches them (with a brief status message to stderr: `Fetching entity list...`). Subsequent tabs are instant.

Cache is in-memory only — discarded when REPL exits. No disk writes, no TTL concerns.

### 7.3 Scope

Tab completion activates **only inside the REPL** (`crm` with no subcommand). One-shot CLI invocations are unaffected.

---

## 8. Testing

| Test file | Covers |
|-----------|--------|
| `tests/test_logging.py` | `CrmLogHandler` text format, json-line format, `--verbose` alias sets level to debug |
| `tests/test_auth_scheme.py` | ntlm path unchanged, kerberos path calls `HttpNegotiateAuth`, missing-package raises `D365Error` with install hint |
| `tests/test_crm_init.py` | `--template` writes correct `.env.example`, refuses to overwrite; interactive wizard writes profile JSON |
| `tests/test_new_commands.py` | `query count` payload + response parsing; `metadata list-actions` / `list-functions` XML parsing + table output |
| All existing tests | Must pass unchanged after cli split — no behavior change |

REPL tab completion: manual test only (readline completion is not unit-testable in CI).

---

## 9. Out of Scope

- Persistent disk metadata cache (would need TTL + invalidation logic — future spec).
- Attribute-level tab completion (depends on disk cache — future spec).
- Non-Windows Kerberos (MIT krb5 / `requests-kerberos`) — deferred; SSPI covers the on-prem Windows target.
- `--log-file` option (out of scope for this spec; stderr is sufficient for now).

---

## 10. PR Delivery

Single PR: `feat/spec-e-dx-polish`

Commit order within the PR:

1. `crm/commands/` skeleton + cli.py split (mechanical refactor, zero behavior change)
2. Logging (`--log-level`, `--log-format`, `--verbose`, `CrmLogHandler`)
3. Auth scheme (`--auth-scheme`, `ConnectionProfile.auth_scheme`, `D365Backend._make_auth()`)
4. `crm init` (template + wizard)
5. New commands (`query count`, `metadata list-actions`, `metadata list-functions`)
6. REPL tab completion (`MetadataCache`, readline completer)
7. Tests + CHANGELOG + version bump to **0.6.0**

**Version:** 0.5.x → **0.6.0** (minor bump; new commands + auth-scheme flag are additive but not breaking; `--auth-scheme` defaults to `ntlm` preserving full backward compat).
