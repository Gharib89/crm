# Spec A — Correctness Fixes + Pyright

**Date:** 2026-05-24
**Status:** Approved (pending user review of written spec)
**Target version:** 0.2.0
**Scope:** First of five specs decomposing the post-code-review roadmap. Specs B–E (Resilience, Throughput/Admin, Metadata-write, DX) will be brainstormed separately.

---

## 1. Goals + non-goals

### Goals

- Eliminate the 9 correctness/UX defects identified in code review (listed in §3).
- Land pyright strict (zone-scoped) so all subsequent specs build on type-safe code.
- Bump to **0.2.0** to signal the JSON envelope contract change.
- Preserve every existing CLI command name and flag, except the one breaking change documented below.

### Non-goals

- No new functionality (`$batch`, `CreateMultiple`, async solutions, impersonation — all Spec B+).
- No refactor of the `cli.py` monolith (Spec E).
- No retry / resilience layer (Spec B).
- No metadata write API beyond fixing the existing `create-entity` return shape.

### Breaking changes (one)

- Error envelope `meta.status` and `meta.code` change from the string `"n/a"` to JSON `null` when absent. Documented in `CHANGELOG.md` under `0.2.0`.

---

## 2. Architecture

No new modules. Edits live in existing files:

```
crm/
  utils/d365_backend.py    — touch only for text/plain parse path (§3.9)
  core/
    entity.py              — §3.1
    query.py               — §3.4, §3.9
    export.py              — §3.2
    metadata.py            — §3.3
    solution.py            — §3.6
    connection.py          — §3.8
  cli.py                   — §3.5, §3.6, §3.7
pyrightconfig.json         — NEW
.github/workflows/build.yml — MODIFY (add pyright step)
setup.py                   — add pyright to extras_require['dev']; bump to 0.2.0
CHANGELOG.md               — NEW
```

### Pyright configuration

Two-zone strictness via `executionEnvironments`:

```json
{
  "include": ["crm"],
  "exclude": ["**/__pycache__", "build", "dist"],
  "pythonVersion": "3.9",
  "typeCheckingMode": "strict",
  "reportMissingTypeStubs": false,
  "executionEnvironments": [
    { "root": "crm/cli.py",             "typeCheckingMode": "basic" },
    { "root": "crm/utils/repl_skin.py", "typeCheckingMode": "basic" },
    { "root": "crm/tests",              "typeCheckingMode": "basic" }
  ]
}
```

Strict applies to `crm/core/*` and `crm/utils/d365_backend.py`. CLI and REPL skin stay basic; tests stay basic.

### Typing strategy

Pragmatic (approach 2 of three considered): add full annotations + `TypedDict`s for wire shapes in backend + core; leave Click handlers + output formatters under basic mode. Any pyright errors surfaced inside strict zones during PR1 are fixed inline, not deferred or suppressed.

---

## 3. Per-fix detail

### 3.1 Drop `If-None-Match: null` on POST

- **File:** `crm/core/entity.py:77`
- **Change:** Remove `headers = {"If-None-Match": "null"}` from `create()`. POST is already create-only per Web API spec; the header is non-standard and either no-op or proxy-hostile.
- **Test:** `test_create_no_if_none_match_header` — mock backend, assert `extra_headers` does not contain `If-None-Match`.

### 3.2 Fix `_ordered_keys` filter

- **File:** `crm/core/export.py:96-108`
- **Bug:** Boolean precedence in `if k.startswith("@") or k.startswith("_") and not k.startswith("_"):` makes the second clause unreachable. `_value` lookup columns leak into CSV headers.
- **Change:** Replace the loop body with `if k.startswith(("@", "_")): continue`.
- **Test:** `test_ordered_keys_drops_lookups_and_annotations` — input record with `_owner_value`, `@odata.etag`, `name` → output column list is `["name"]`.

### 3.3 EntitySetName read-back after `create-entity`

- **File:** `crm/core/metadata.py:198-206`
- **Bug:** Local pluralization (`logical + "es" if endswith("s") else "s"`) is wrong for most English nouns (city → citys, fox → foxs). Dataverse derives `EntitySetName` server-side.
- **Change:** After successful POST, extract the `MetadataId` GUID from the URL inside `_entity_id_url` (the value of the `OData-EntityId` header — shaped like `<base>/EntityDefinitions(<guid>)`) using `re.search(r"EntityDefinitions\(([0-9a-fA-F-]{36})\)", url)`, then issue `GET EntityDefinitions(<MetadataId>)?$select=EntitySetName,LogicalName` and return the server value. Adds one round-trip per `create-entity` call.
- **Return shape:**
  ```python
  {
    "created": True,
    "schema_name": "...",
    "logical_name": "...",
    "entity_set_name": "new_cities" | None,   # None only on read-back failure
    "entity_set_lookup_error": "<msg>" | absent,
    "metadata_id_url": "...",
    "solution": "..." | None,
  }
  ```
- **Failure mode:** Read-back HTTP error does not fail the command — entity was created successfully. Returns partial dict with `entity_set_name: None` and a diagnostic key `entity_set_lookup_error`.
- **Tests:**
  - `test_create_entity_returns_server_entity_set_name` — mock returns `EntitySetName: "new_cities"`; assert returned dict carries that exact value.
  - `test_create_entity_partial_when_readback_fails` — mock read-back returns 500; assert `created: True`, `entity_set_name: None`, `entity_set_lookup_error` present.
  - `test_create_entity_partial_when_odata_entityid_header_missing` — proxy strips `OData-EntityId` header so `_entity_id_url` is `None`; assert `created: True`, `entity_set_name: None`, `metadata_id_url: None`, and a diagnostic key flagging the missing header.

### 3.4 `fetchxml_query` uses `params=`

- **File:** `crm/core/query.py:79-87`
- **Change:** Drop manual `urllib.parse.quote` + path concatenation. Pass `params={"fetchXml": fetch_xml}` so `requests` encodes it consistently with every other call.
- **Test:** `test_fetchxml_passes_params_dict` — mock backend, assert `params={"fetchXml": "<fetch>...</fetch>"}` is forwarded; URL has no embedded `?`.

### 3.5 JSON envelope null status/code

- **File:** `crm/cli.py:127-131`
- **Bug:** `meta={"status": exc.status or "n/a", "code": exc.code or "n/a"}` emits string `"n/a"` when fields are absent, breaking type-checking by agent consumers.
- **Change:**
  ```python
  ctx.emit(False, error=str(exc), meta={"status": exc.status, "code": exc.code})
  ```
- **Breaking change.** Documented in `CHANGELOG.md` 0.2.0 entry.
- **Test:** `test_error_envelope_null_when_status_missing` — `D365Error(...)` without `status` → JSON `meta.status` is `null` (not `"n/a"`).

### 3.6 Solution export flags

- **Files:** `crm/core/solution.py:45-82`, `crm/cli.py:824-835`
- **Bug:** All `Export*Settings` hardcoded to `False`. Real admin exports often need `ExportCustomizationSettings=true`.
- **Change:** Promote each setting to a kwarg of `export_solution` (default `False`). CLI exposes a single repeatable option:
  ```
  --export-setting <name>     repeatable; one per --include
  ```
  where `<name>` is a `click.Choice` of: `customizations`, `calendar`, `general`, `isv-config`, `marketing`, `outlook-sync`, `relationship-roles`, `sales`, `autonumbering`, `email-tracking`. Internally each value maps to its `Export*Settings` body key. Example:
  ```
  crm solution export MySol -o snap.zip --export-setting customizations --export-setting general
  ```
- **Rationale:** keeps `--help` short (one option line vs ten); names are autocompletable via `click.Choice`; same expressiveness as 10 boolean flags.
- **Test:** `test_export_solution_passes_flags_to_body` — supply two settings; assert the POST body has those two keys set to `True` and the other eight keys set to `False`.

### 3.7 REPL backend reuse

- **Files:** `crm/cli.py:97-106, 1239-1289`
- **Root cause:** The REPL loop invokes `cli.main(args=argv, standalone_mode=False, prog_name="crm")` (`cli.py:1276`) without passing `obj=ctx`. Click's `make_pass_decorator(CLIContext, ensure=True)` therefore constructs a **fresh `CLIContext` per command**, and the first `ctx.backend()` call on that fresh context builds a brand-new `D365Backend` — triggering a fresh NTLM handshake every line.
- **Change:** Pass the outer REPL `CLIContext` through to Click: `cli.main(args=argv, obj=ctx, standalone_mode=False, prog_name="crm")`. Click reuses the existing context; the cached `_backend` survives across commands. Add a public `CLIContext.invalidate_backend()` method (sets the internal cache to `None`); `connection_connect` and `connection_disconnect` call it to force rebuild when the profile changes. (Public method keeps the API clean even though `cli.py` is in the pyright basic zone.)
- **Invalidation policy:** REPL lifetime; only `connection connect` / `connection disconnect` invalidate. No TTL, no auto-invalidate on auth error (locked decision).
- **Tests:**
  - `test_repl_reuses_backend_across_commands` — spy on `D365Backend.__init__`; run two REPL commands; assert called once.
  - `test_repl_backend_invalidated_on_connect` — after `connection connect`, the next command rebuilds the backend.

### 3.8 `.env` unquote

- **File:** `crm/core/connection.py:106`
- **Bug:** `.strip('"').strip("'")` strips mixed/extra quote characters; eats legitimate inner apostrophes.
- **Change:**
  ```python
  if len(raw) >= 2 and raw[0] == raw[-1] and raw[0] in ('"', "'"):
      value = raw[1:-1]
  else:
      value = raw
  ```
- **Test:** `test_dotenv_preserves_inner_quotes` — input line `KEY="foo's bar"` → env value `foo's bar`.

### 3.9 `$count` text/plain parse path

- **Files:** `crm/utils/d365_backend.py:_parse_response`, `crm/core/query.py:148-161`
- **Bug:** `_parse_response` discards body when `expect_json=False`, forcing `count_entity_set` to fall back to a second `$count=true` round-trip.
- **Change:**
  - In `_parse_response`, when 2xx and not expecting JSON, return `resp.text.strip()` if `Content-Type` starts with `text/plain` and the body is non-empty; otherwise return `None` as today.
  - **Signature change:** `_parse_response` return type widens from `dict | None` to `dict | str | None`. All callers (every method on `D365Backend` and indirectly every `core/*` function) must narrow before use. Under pyright strict, this is enforced — concretely: `D365Backend.get/post/patch/delete` annotations widen to `dict | str | None`, then each `core/*` call site either asserts the expected branch or accepts the wider type. Most callers already do `result or {}` which collapses `None` and `str` to `{}` truthily — needs explicit `isinstance(result, dict)` checks where shape matters. Audit + fix is part of PR2.
  - In `count_entity_set`, parse `int(result)` directly when result is a non-empty string. **Fallback retained:** if the `text/plain` parse returns `None` or an empty/non-numeric string (proxy stripping body, unexpected `Content-Type`, etc.), fall through to `odata_query(entity_set, top=1, count=True)` and read `@odata.count` from the response. The fallback is the current behavior; keeping it preserves resilience against edge proxies. The win from §3.9 is **one HTTP call instead of two on the happy path**, not eliminating the second call entirely.
- **Tests:**
  - `test_count_returns_int_from_text_plain` — mock `200` + `Content-Type: text/plain` + body `"42"` → returns `int(42)` with exactly one request issued.
  - `test_count_falls_back_when_text_plain_empty` — mock `200` + empty body on `$count` endpoint, then mock subsequent `?$count=true` returning `@odata.count: 7` → returns `int(7)`; assert two requests issued in order.

### 3.10 Pyright setup

- **Files:**
  - `pyrightconfig.json` (new, shape in §2).
  - `setup.py` — add `pyright>=1.1.380` to `extras_require['dev']`.
  - `.github/workflows/build.yml` — modify; add a `pyright` step after the existing test step, on the same Python matrix. (Repo currently has `build.yml` + `release.yml`; no separate `ci.yml` is created.)
- **Pre-count (run on the current main, 2026-05-24):** strict mode on `crm/core/*` + `crm/utils/d365_backend.py` surfaces **342 errors** across 10 files; ~11 are real (`reportAttributeAccessIssue`, `reportUnnecessaryIsInstance`, `reportMissingImports`, `reportMissingParameterType`); the remaining 331 are the `reportUnknown*` family (97 `UnknownVariableType`, 82 `UnknownMemberType`, 61 `UnknownParameterType`, 56 `MissingTypeArgument`, 35 `UnknownArgumentType`) — driven primarily by `requests`/`json` returns leaking `Any`.
- **Decision (full typing approach):** PR1 does the full typing work — no error suppression. This expands PR1 materially (1–2 days of typing grunt-work) but lands strict-from-day-one cleanly.
- **Required typing artifacts to add in PR1:**
  - `crm/utils/d365_types.py` — new module with `TypedDict` definitions for the Web API response shapes the codebase actually consumes: `WhoAmIResponse`, `EntityDefinition`, `AttributeDefinition`, `SolutionRow`, `SolutionComponent`, `WorkflowRow`, `OptionSetResponse`, `ODataCollection[T]` (generic with `value: list[T]`, `@odata.nextLink`, `@odata.count`).
  - Explicit return type annotations on every public function in `crm/core/*` and `crm/utils/d365_backend.py`.
  - `D365Backend.request` / `get` / `post` / `patch` / `delete` get overloads or a single union return `dict[str, Any] | str | None` with a narrowing helper (`_as_dict(result)`) used by callers that need a dict.
  - `cast(...)` is permitted at the wire boundary (raw `resp.json()` → TypedDict) but discouraged inside `core/*`.
- **Acceptance:** `pyright` from the repo root exits `0` on a clean checkout. CI job fails the PR if exit code != 0.

---

## 4. Data-flow / behavior diffs

Three flows change observably:

### A. Error envelope (breaking)

**Before:**
```json
{"ok": false, "error": "HTTP 404", "meta": {"status": 404, "code": "n/a"}}
```
**After (0.2.0):**
```json
{"ok": false, "error": "HTTP 404", "meta": {"status": 404, "code": null}}
```

### B. `create-entity` return shape

- Adds one round-trip after POST to resolve `EntitySetName` from the server.
- Returned `entity_set_name` is now authoritative (`str`) on success, or `None` with a diagnostic key on read-back failure.

### C. REPL backend lifetime

- One `D365Backend` per REPL session; rebuilt only on `connection connect` / `connection disconnect`.
- Mid-session 401 surfaces normally as `D365Error`; user runs `connection connect` to refresh.

### Non-observable changes

§3.1, §3.2, §3.4, §3.8, §3.9 are pure internals — no CLI surface change.

---

## 5. Error handling

No new error paths. Existing `D365Error` flow unchanged. Two specific notes:

- **§3.3 read-back failure:** caught and converted to a diagnostic field on the success payload; never raised. The entity exists on the server regardless.
- **§3.7 cached backend + auth failure:** no auto-invalidate; user re-runs `connection connect` to refresh. Avoids masking real auth misconfiguration.

---

## 6. Testing

### Unit tests (mocked HTTP) — `crm/tests/test_core.py`

| # | Test | Verifies |
|---|------|----------|
| 3.1 | `test_create_no_if_none_match_header` | POST headers omit `If-None-Match` |
| 3.2 | `test_ordered_keys_drops_lookups_and_annotations` | `_value` + `@` keys filtered |
| 3.3a | `test_create_entity_returns_server_entity_set_name` | Server-truth EntitySetName |
| 3.3b | `test_create_entity_partial_when_readback_fails` | Partial success on read-back failure |
| 3.3c | `test_create_entity_partial_when_odata_entityid_header_missing` | Partial success when `OData-EntityId` header absent |
| 3.4 | `test_fetchxml_passes_params_dict` | `params={"fetchXml": ...}` shape |
| 3.5 | `test_error_envelope_null_when_status_missing` | `meta.status` emits `null` |
| 3.6 | `test_export_solution_passes_flags_to_body` | All `Export*Settings` flags flow through |
| 3.7a | `test_repl_reuses_backend_across_commands` | Single backend per REPL session |
| 3.7b | `test_repl_backend_invalidated_on_connect` | `connection connect` resets cache |
| 3.8 | `test_dotenv_preserves_inner_quotes` | Outer pair stripped, inner intact |
| 3.9a | `test_count_returns_int_from_text_plain` | Single request, integer return |
| 3.9b | `test_count_falls_back_when_text_plain_empty` | Fallback to `$count=true` path on empty body |

### E2E (real server) — `crm/tests/test_full_e2e.py`

- `test_e2e_create_custom_entity_reads_back_set_name` — full round-trip; assert returned `entity_set_name` is discoverable via `metadata entities`.
- `test_e2e_solution_export_with_customization_flag` — export with `--export-customizations` yields non-empty zip.

### Pyright check

- Local: `pyright` exits 0 from repo root on a clean checkout.
- CI: the existing `.github/workflows/build.yml` runs `pyright` as a new step after the test step. Non-zero exit blocks PR merge.

### Coverage gate

None. New tests cover new branches; existing tests are not relaxed.

---

## 7. PR sequencing

| PR | Branch | Contents | Risk |
|----|--------|----------|------|
| **PR1** | `feat/pyright-setup` | `pyrightconfig.json`, `setup.py` dev extra, `build.yml` pyright step, **new `crm/utils/d365_types.py` module with TypedDicts**, full type annotations across `crm/core/*` + `crm/utils/d365_backend.py` to clear all 342 strict errors surfaced by the pre-count. | Medium — large mechanical diff, no behavioral change but every strict-zone file gets edited. |
| **PR2** | `fix/correctness` | §3.1, §3.2, §3.4, §3.8, §3.9 + unit tests. Pure internals; no CLI surface change. | Low. |
| **PR3** | `feat/api-shape-0.2.0` | §3.3, §3.5, §3.6, §3.7 + unit tests + e2e tests + version bump to **0.2.0** + new `CHANGELOG.md` covering all three PRs. | Medium — one breaking envelope change + new CLI flags. |

Merge order is strict: PR1 → PR2 → PR3. PR2 and PR3 rebase on top of PR1 as they land. Each PR is reviewable independently.

---

## 8. Out of scope (deferred to later specs)

- **Spec B** — Resilience: 429/Retry-After loop, `ImportSolutionAsync` + `ImportJob` polling, `ExportSolutionAsync` + `DownloadSolutionExportData`, `x-ms-ratelimit-*` header surfacing.
- **Spec C** — Throughput + admin surface: `$batch`, `CreateMultiple` / `UpdateMultiple` / `UpsertMultiple`, impersonation (`CallerObjectId` / `MSCRMCallerID`), `MSCRM.SuppressDuplicateDetection`, `MSCRM.BypassCustomPluginExecution`, `asyncoperations` browse, optimistic concurrency via `If-Match: <etag>`.
- **Spec D** — Metadata write API: add-attribute, create-relationship (1:N + N:N), global option set CRUD, delete-entity.
- **Spec E** — DX polish: `--verbose` HTTP transcript, structured logs, env-template generator, Kerberos via `requests_negotiate_sspi`, REPL metadata-cache + tab completion, split `cli.py` per command group, `RetrieveTotalRecordCount`, `metadata list-actions` / `list-functions`.
