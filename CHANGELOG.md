# Changelog

All notable changes to `crm` are documented in this file.

The format is loosely based on [Keep a Changelog](https://keepachangelog.com/en/1.1.0/),
and this project adheres to [Semantic Versioning](https://semver.org/spec/v2.0.0.html).

## [Unreleased]

**Added**
- `crm describe [GROUP]`: machine-readable command/option/choice discovery. Walks
  the live Click tree (no live D365 connection, no mkdocs-click dependency) and in
  `--json` mode emits `{ok, data:{root_options, commands:[{name, path, help,
  is_group, args, params}]}}`. Each option carries `name, opts, secondary_opts,
  type, required, is_flag, multiple, choices, default, envvar` (`secondary_opts`
  holds the off-form of a flag-pair, e.g. `--no-publish`); `Choice` enums (ownership, the 14
  attribute kinds, `if-exists`, cascade behaviors, …) are surfaced verbatim. Root
  sticky globals (`--json`, `--dry-run`, `--profile`, `--auth-scheme`,
  `--log-level`, `--stage-only`, `--session`, …) are listed under `root_options`.
  The interactive `repl` leaf is excluded. `describe <group>` scopes to one
  subtree, importing just that module (a lazy win over the full walk) (#63).
- Agent-readable docs surface: the docs site now publishes `llms.txt` (curated
  index, per [llmstxt.org](https://llmstxt.org/)) and `llms-full.txt` (every page
  concatenated into one fetch) at the site root, generated at build time by the
  `mkdocs-llmstxt` plugin from the existing `docs/` tree. Any agent with web
  access — not just skill-aware harnesses loading `crm/skills/SKILL.md` — can pull
  the full CLI reference and how-tos from `<site>/llms-full.txt`. Adds a docs-only
  dependency on `mkdocs-llmstxt`.
- `crm apply -f spec.yaml`: declarative desired-state from a single YAML/JSON
  spec. Orchestrates the existing metadata cores in dependency order (publisher →
  solution → entities → option sets → attributes → relationships → views), each
  with `if_exists=skip`, and runs `PublishAllXml` once at the end, so re-applying
  an unchanged spec is a no-op. Emits `{ok, data:{applied, skipped, planned,
  failed}, meta:{staged}}`. Honors `--dry-run` (greenfield specs report
  dependents as planned-create instead of erroring) and `--stage-only` (create
  without publishing). Metadata POSTs are non-transactional, so a failure
  aborts-and-reports, leaving staged-but-unpublished residue. The spec is
  validated up front. Adds a runtime dependency on PyYAML (#60).
- Machine-readable error taxonomy: in `--json` mode the error envelope now carries
  `meta.category` (a closed enum: `not_found`, `auth_failed`, `forbidden`,
  `concurrency_conflict`, `duplicate_detected`, `validation`, `throttled`,
  `server_error`, `transport_error`) and `meta.retryable`, alongside the existing
  `meta.status` / `meta.code`. Classification is status-first, with two D365 error
  codes (`0x80040217` → `not_found`, `0x80040237` → `duplicate_detected`) honored
  regardless of status; `retryable` is true only for the transient classes. The
  backend auto-retries the `transport_error` / `throttled` (429) / `server_error`
  (5xx) classes, so for those `retryable` is a post-exhaustion hint;
  `concurrency_conflict` (412) is not auto-retried — the caller refetches a fresh
  ETag and retries. The status-less transport path now carries a `transport_error`
  signal, and the fragile `MissingPrivilege` message-substring synthesis is
  subsumed (403 → `forbidden`) (#62).
- Canonical `meta.dry_run` signal: in `--json` mode every dry-run invocation now
  carries `meta.dry_run: true` in the envelope. It is keyed off the invocation-level
  `--dry-run` flag (not by sniffing the data for the `_dry_run` sentinel), so
  list-shaped batch previews and poll previews are covered uniformly and forced-real
  existence-probe GETs do not false-positive. Existing `meta` keys (e.g. `staged`)
  are preserved; the in-data `_dry_run` sentinel is retained for back-compat (#61).

**Changed**
- Bundled agent skill (`crm/skills/SKILL.md`) is now fully standalone — it reads
  correctly once `crm skill install` drops it into a harness skill dir, with no
  references to files that don't ship alongside it. Install section shows the
  per-host one-liners (`install.ps1` / `install.sh`) instead of `pip install -e .`;
  removed repo-only pointers (`D365.md`, `crm/tests/TEST.md`, `README.md`,
  `docs/adr/…`, `docs/how-to/apply.md`, the `.claude/hooks` gate) in favor of
  in-CLI discovery (`crm describe`, `crm <group> --help`); broadened the
  frontmatter description to cover both on-prem (NTLM) and Dataverse online (OAuth).

## [0.8.0] — 2026-06-04

**Added**
- Installer SHA-256 integrity verification: `install.sh` / `install.ps1` verify the
  downloaded archive against a published `SHA256SUMS` (uploaded per release to
  `<tag>/` and `latest/` in R2) before extracting, and abort on a mismatch or if it
  can't be fetched. `CRM_SHA256` / `$env:CRM_SHA256` pins a hash out-of-band (#46).
- Cloud impersonation by Entra ID object id via the `CallerObjectId` header:
  new `--as-user-object-id <guid>` flag (alongside `--as-user`) and
  `CRM_AS_USER_OBJECT_ID` env default, on every command that already carries
  `--as-user`. Header selection is by which input you supply, independent of
  `auth_scheme`; `--as-user` (`MSCRMCallerID`) and `--as-user-object-id`
  (`CallerObjectId`) are mutually exclusive per request (#54).
- CHANGELOG is now published on the docs site at `/changelog/`, rendered
  from this file via `mkdocs-include-markdown-plugin`.

## 0.7.0 — Fast startup + R2 install

**Performance**
- CLI subcommands and the D365 backend stack now load lazily: `crm --version` and
  direct command invocations no longer import every command module (and their
  requests/NTLM/prompt_toolkit dependencies), cutting cold startup substantially.
  `crm --help` still loads all modules (accepted trade-off).

**Changed**
- PyInstaller builds switched from `--onefile` to `--onedir` (`dist/crm/`),
  eliminating per-launch self-extraction overhead.
- Install is now a one-line script served from a public Cloudflare R2 bucket
  (`irm …/install.ps1 | iex` on Windows, `curl …/install.sh | sh` on Linux),
  replacing the private-repo GitHub release URL that 404'd for users.

**Added**
- `scripts/install.ps1` (Windows) and `scripts/install.sh` (Linux): download the
  prebuilt onedir bundle from R2, install to a user dir, wire up PATH / a symlink,
  and support uninstall.

## 0.6.0 — Spec E: DX Polish

**Refactor**
- Split `crm/cli.py` (2098 lines) into focused modules under `crm/commands/`
  (one Click group per file). Pure refactor — zero behavior change.

**Added**
- `--log-level debug|info|warning|error` + `--log-format text|json-line` on
  the root CLI group (env: `CRM_LOG_LEVEL`, `CRM_LOG_FORMAT`).
- `--verbose` flag (alias for `--log-level debug`).
- `--auth-scheme ntlm|kerberos|negotiate` on the root CLI group
  (env: `CRM_AUTH_SCHEME`). Kerberos/Negotiate via `requests_negotiate_sspi`
  (install with `pip install crm[kerberos]`).
- `crm init` command: `--template` writes `.env.example`; no args runs an
  interactive profile wizard.
- `query count <entity>` — calls `RetrieveTotalRecordCount`.
- `metadata list-actions` — parses `$metadata` and lists OData actions.
- `metadata list-functions` — parses `$metadata` and lists OData functions.
- REPL tab completion for entity-name argument slots, backed by a lazy
  in-memory `MetadataCache`.

**Changed**
- `ConnectionProfile` gains an `auth_scheme` field (default `"ntlm"`,
  backward compatible).
- `crm/utils/repl_skin.py::create_prompt_session` accepts an optional
  `completer` argument.

## [0.5.0] — 2026-05-25

### Added

- `metadata add-attribute` — add columns to existing entities. Supports 14
  attribute kinds: string, memo, integer, bigint, decimal, double, money,
  boolean, datetime, picklist, multiselect, lookup, image, file.
- `metadata create-one-to-many` + `metadata create-many-to-many` — create
  1:N and N:N relationships via the dedicated Dataverse actions.
- Global option set CRUD: `metadata list-optionsets`, `get-optionset`,
  `create-optionset`, `update-optionset`, `delete-optionset`. `update`
  is granular: `--insert-option` / `--update-option` / `--delete-option`
  / `--reorder` flags map to the matching bound actions.
- `metadata delete-entity` — drop a custom table, guarded by interactive
  confirm + `--yes` skip + client-side `IsCustomEntity` + `IsManaged`
  pre-flight check.

All new write verbs accept `--solution <uniquename>` (header
`MSCRM.SolutionUniqueName`) and `--publish/--no-publish` (default ON),
matching `metadata create-entity`. Delete verbs skip publish.

## [0.4.0] — 2026-05-25

This release lands Spec C from the post-code-review roadmap: `$batch`
support, on-prem-correct impersonation via `MSCRMCallerID`, two admin
headers for write paths, an `asyncoperations` browse surface, and
explicit optimistic concurrency via `If-Match`. See
`docs/superpowers/specs/2026-05-24-spec-c-throughput-admin-design.md`
for the full design.

### Added

- `D365Backend.batch(operations, *, transactional=True, continue_on_error=False, timeout=None)` — execute a list of operations via POST `$batch`. Consecutive writes are auto-grouped into one changeset; GETs go as top-level operations.
- `crm batch <file.json>` CLI command with `--no-transaction`, `--continue-on-error`, `--output`, `--timeout` flags.
- Backend typed kwargs on every verb: `caller_id`, `suppress_duplicate_detection`, `bypass_custom_plugin_execution`, `etag`. Env defaults: `CRM_AS_USER`, `CRM_SUPPRESS_DUP`, `CRM_BYPASS_PLUGINS`.
- Per-command CLI flags on every write/action verb: `--as-user <guid>`, `--suppress-dup-detection`, `--bypass-plugins`. `--if-match <etag>` on `entity update` and `entity delete`.
- `crm async list/get/cancel` plus `crm solution job-status / job-cancel` aliases.
- New TypedDicts: `BatchOperation`, `BatchResult`, `AsyncOperationRow`.

### Changed

- HTTP `412` responses now map to `D365Error(code="PreconditionFailed")`.
- HTTP `403` responses whose body references `prvBypassCustomPluginExecution` map to `D365Error(code="MissingPrivilege")`.

### Deferred

- `CreateMultiple` / `UpdateMultiple` / `UpsertMultiple` — Dataverse cloud only; not present on Contoso 9.1.x on-prem.
- `CallerObjectId` impersonation header — requires Microsoft Entra ID; on-prem AD users use `MSCRMCallerID`.
- Server-side `$batch` size limits (typical Dataverse: 100 changesets per batch; 1000 ops per changeset) are not enforced client-side; the server's `MaxBatchSize` / `MaxChangesetSize` error surfaces verbatim.

### Notes for callers

- `POST $batch` is retried only on `429` and `503` (Spec B conservative-POST policy). A retried batch re-sends the assembled body verbatim — idempotency is the caller's responsibility.

## [0.3.0] — 2026-05-24

This release lands Spec B from the post-code-review roadmap: a retry
layer on every HTTP call plus a switch to the asynchronous variants of
`ImportSolution` and `ExportSolution`. See
`docs/superpowers/specs/2026-05-24-spec-b-resilience-design.md` for the
full design.

### Breaking

- **`crm.core.solution.import_solution` return shape changes.** Now
  returns `{import_job_id, async_operation_id, status, progress,
  started_on, completed_on, duration_ms}`. Any caller reading the old
  ImportSolution response keys (`ImportJobKey`, etc.) must switch.
- **`crm.core.solution.export_solution` return shape gains keys.**
  New fields: `async_operation_id`, `export_job_id`, `duration_ms`. The
  existing `output`, `bytes`, `managed`, `solution` keys are preserved.
- **Both functions can now block for up to `CRM_ASYNC_TIMEOUT` seconds
  (default 1800).** The sync versions blocked for up to
  `profile.timeout` seconds per HTTP call (default 120) with no
  client-side polling.

### Added

- `D365Backend.request` now retries on `429`, idempotent `5xx`
  (`502`/`503`/`504` on `GET`/`PUT`/`PATCH`/`DELETE`; `503` only on
  `POST`), and retryable transport errors (`ConnectionError`,
  `Timeout`, `ChunkedEncodingError`). Honors `Retry-After`; falls back
  to capped exponential backoff with full jitter.
- `D365Backend.poll_async_operation(async_operation_id, *, timeout,
  import_job_id, on_progress)` — blocks until an
  `asyncoperations(<id>)` row reaches `statecode=3`. Raises
  `D365Error` on failure (`statuscode=31`), cancellation (`32`), or
  timeout.
- `ConnectionProfile` gains seven new fields: `retry_max`,
  `retry_base_delay`, `retry_max_delay`, `retry_jitter`,
  `async_poll_initial`, `async_poll_max`, `async_timeout`.
- Env overrides: `CRM_RETRY_MAX`, `CRM_RETRY_BASE_DELAY`,
  `CRM_RETRY_MAX_DELAY`, `CRM_RETRY_JITTER`, `CRM_ASYNC_TIMEOUT`,
  `CRM_NO_RETRY`. Env wins over profile.
- New CLI flags on `crm solution export` and `crm solution import`:
  `--timeout N` (override `async_timeout` for this call), `--no-retry`
  (set `CRM_NO_RETRY=1` for this call). `crm solution import` also
  gets `--quiet` / `-q` to suppress per-tick progress lines.
- `x-ms-ratelimit-*` headers are logged to stderr on every retried 429,
  and on every response under `CRM_VERBOSE=1`.

### Changed

- `crm solution import` and `crm solution export` now block until the
  async operation reports completion, emitting per-tick progress to
  stderr (import only; suppress with `--quiet`).

[0.4.0]: https://github.com/Gharib89/crm/releases/tag/v0.4.0
[0.3.0]: https://github.com/Gharib89/crm/releases/tag/v0.3.0

## [0.2.0] — 2026-05-24

This release lands Spec A from the post-code-review roadmap: nine correctness
fixes plus pyright strict (zone-scoped) across `crm/core/*` and
`crm/utils/d365_backend.py`. See
`docs/superpowers/specs/2026-05-24-spec-a-correctness-pyright-design.md` for
the full design.

### Breaking

- **Error envelope `meta.status` and `meta.code` now emit JSON `null`** when
  absent, instead of the literal string `"n/a"`. Scripts that string-match
  `"n/a"` must switch to a null check. (§3.5)

### Added

- `--export-setting <name>` flag on `crm solution export`, repeatable.
  Accepted names: `autonumbering`, `calendar`, `customizations`,
  `email-tracking`, `general`, `isv-config`, `marketing`, `outlook-sync`,
  `relationship-roles`, `sales`. (§3.6)
- `crm/utils/d365_types.py` — `TypedDict` shapes for Web API responses.
- `pyright` (>=1.1.380) as a dev dependency and a CI step in
  `.github/workflows/build.yml`. Strict mode on `crm/core/*` +
  `crm/utils/d365_backend.py`; basic mode (via file-level `# pyright: basic`
  pragma) on `crm/cli.py`, `crm/utils/repl_skin.py`, and `crm/tests/*`.

### Changed

- `metadata create-entity` now reads `EntitySetName` back from the server
  instead of guessing it via English pluralisation. Adds one round-trip per
  create call. On read-back failure the entity is still reported as created,
  with `entity_set_name: null` and a diagnostic `entity_set_lookup_error`
  field. (§3.3)
- REPL keeps a single `D365Backend` per session instead of rebuilding on
  every command. Invalidated by `connection connect` / `connection
  disconnect`. (§3.7)
- `$count` queries parse `text/plain` directly in one HTTP call on the
  happy path. Falls back to `?$count=true` if the body is missing or
  non-numeric. (§3.9)
- `fetchxml_query` passes the FetchXML via `params=` instead of manual URL
  concatenation. No on-wire change. (§3.4)

### Fixed

- `entity create` no longer sends the non-spec `If-None-Match: null` header
  on POST. (§3.1)
- `data export` CSV no longer leaks `_value` lookup columns and `@odata.*`
  annotations into headers — `_ordered_keys` boolean precedence bug. (§3.2)
- `.env` value parser is now pair-aware: `KEY="foo's bar"` resolves to
  `foo's bar`, not `foos bar`. (§3.8)

[0.2.0]: https://github.com/Gharib89/crm/releases/tag/v0.2.0
