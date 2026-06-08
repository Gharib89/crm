# Changelog

All notable changes to `crm` are documented in this file.

The format is loosely based on [Keep a Changelog](https://keepachangelog.com/en/1.1.0/),
and this project adheres to [Semantic Versioning](https://semver.org/spec/v2.0.0.html).
Releases from 0.12.0 on are cut automatically by python-semantic-release from
Conventional Commit messages; new version sections are inserted below this line.

<!-- version list -->

## v1.6.0 (2026-06-08)

### Chores

- Add .worktrees/ to .gitignore
  ([`2a01d5d`](https://github.com/Gharib89/crm/commit/2a01d5d7b63d3fad28f9ae76c9d2f018826eb15d))

### Features

- **metadata**: Add clone-entity command ([#143](https://github.com/Gharib89/crm/pull/143),
  [`a20ed6e`](https://github.com/Gharib89/crm/commit/a20ed6ef2283d84254d5592d84c3c76032ba5ce7))


## v1.5.0 (2026-06-08)

### Bug Fixes

- **workflow**: Address Copilot round-1 findings ([#152](https://github.com/Gharib89/crm/pull/152),
  [`a4f9fcd`](https://github.com/Gharib89/crm/commit/a4f9fcdd9ec71549e8fff82b457d298da8599c68))

- **workflow**: Address Copilot round-2 findings ([#152](https://github.com/Gharib89/crm/pull/152),
  [`a4f9fcd`](https://github.com/Gharib89/crm/commit/a4f9fcdd9ec71549e8fff82b457d298da8599c68))

- **workflow**: Address Copilot round-3 findings ([#152](https://github.com/Gharib89/crm/pull/152),
  [`a4f9fcd`](https://github.com/Gharib89/crm/commit/a4f9fcdd9ec71549e8fff82b457d298da8599c68))

### Documentation

- **plan**: Implementation plan for crm workflow clone/export/import
  ([#152](https://github.com/Gharib89/crm/pull/152),
  [`a4f9fcd`](https://github.com/Gharib89/crm/commit/a4f9fcdd9ec71549e8fff82b457d298da8599c68))

- **spec**: Design for crm metadata clone-entity ([#152](https://github.com/Gharib89/crm/pull/152),
  [`a4f9fcd`](https://github.com/Gharib89/crm/commit/a4f9fcdd9ec71549e8fff82b457d298da8599c68))

- **spec**: Design for crm workflow clone/export/import
  ([#152](https://github.com/Gharib89/crm/pull/152),
  [`a4f9fcd`](https://github.com/Gharib89/crm/commit/a4f9fcdd9ec71549e8fff82b457d298da8599c68))

- **spec**: Link clone-entity spec to follow-up #151
  ([#152](https://github.com/Gharib89/crm/pull/152),
  [`a4f9fcd`](https://github.com/Gharib89/crm/commit/a4f9fcdd9ec71549e8fff82b457d298da8599c68))

- **spec**: Sequence #144 before #143; --with-workflows back in scope
  ([#152](https://github.com/Gharib89/crm/pull/152),
  [`a4f9fcd`](https://github.com/Gharib89/crm/commit/a4f9fcdd9ec71549e8fff82b457d298da8599c68))

- **workflow**: Document clone/export/import ([#152](https://github.com/Gharib89/crm/pull/152),
  [`a4f9fcd`](https://github.com/Gharib89/crm/commit/a4f9fcdd9ec71549e8fff82b457d298da8599c68))

### Features

- **workflow**: Add 'workflow clone' command ([#152](https://github.com/Gharib89/crm/pull/152),
  [`a4f9fcd`](https://github.com/Gharib89/crm/commit/a4f9fcdd9ec71549e8fff82b457d298da8599c68))

- **workflow**: Add 'workflow export' and 'workflow import' commands
  ([#152](https://github.com/Gharib89/crm/pull/152),
  [`a4f9fcd`](https://github.com/Gharib89/crm/commit/a4f9fcdd9ec71549e8fff82b457d298da8599c68))

- **workflow**: Add clone, export, and import commands (#144)
  ([#152](https://github.com/Gharib89/crm/pull/152),
  [`a4f9fcd`](https://github.com/Gharib89/crm/commit/a4f9fcdd9ec71549e8fff82b457d298da8599c68))

- **workflow**: Add export_workflow/import_workflow round-trip
  ([#152](https://github.com/Gharib89/crm/pull/152),
  [`a4f9fcd`](https://github.com/Gharib89/crm/commit/a4f9fcdd9ec71549e8fff82b457d298da8599c68))

- **workflow**: Add get_workflow definition retrieval
  ([#152](https://github.com/Gharib89/crm/pull/152),
  [`a4f9fcd`](https://github.com/Gharib89/crm/commit/a4f9fcdd9ec71549e8fff82b457d298da8599c68))

- **workflow**: Add retarget_xaml transform for clone
  ([#152](https://github.com/Gharib89/crm/pull/152),
  [`a4f9fcd`](https://github.com/Gharib89/crm/commit/a4f9fcdd9ec71549e8fff82b457d298da8599c68))

- **workflow**: Clone_workflow_to_entity with tiered category guard
  ([#152](https://github.com/Gharib89/crm/pull/152),
  [`a4f9fcd`](https://github.com/Gharib89/crm/commit/a4f9fcdd9ec71549e8fff82b457d298da8599c68))

### Testing

- **workflow**: Align clone with live-org field requirements
  ([#152](https://github.com/Gharib89/crm/pull/152),
  [`a4f9fcd`](https://github.com/Gharib89/crm/commit/a4f9fcdd9ec71549e8fff82b457d298da8599c68))


## v1.4.0 (2026-06-08)

### Bug Fixes

- **ribbon**: Add dry-run support, fix pyright basic pragma in tests
  ([#150](https://github.com/Gharib89/crm/pull/150),
  [`2f20ed3`](https://github.com/Gharib89/crm/commit/2f20ed3bfa18bd6667d026c0c800f4b7dee02216))

- **ribbon**: Fix remaining pyright errors in test_ribbon.py
  ([#150](https://github.com/Gharib89/crm/pull/150),
  [`2f20ed3`](https://github.com/Gharib89/crm/commit/2f20ed3bfa18bd6667d026c0c800f4b7dee02216))

- **ribbon**: Guard empty slugify, replace assert with if-guard, remove unused code
  ([#150](https://github.com/Gharib89/crm/pull/150),
  [`2f20ed3`](https://github.com/Gharib89/crm/commit/2f20ed3bfa18bd6667d026c0c800f4b7dee02216))

- **ribbon**: Guard None solution before _load_solution_ribbon_diff
  ([#150](https://github.com/Gharib89/crm/pull/150),
  [`2f20ed3`](https://github.com/Gharib89/crm/commit/2f20ed3bfa18bd6667d026c0c800f4b7dee02216))

- **ribbon**: Harden ZIP decode, improve error messages, fix bundler spec
  ([#150](https://github.com/Gharib89/crm/pull/150),
  [`2f20ed3`](https://github.com/Gharib89/crm/commit/2f20ed3bfa18bd6667d026c0c800f4b7dee02216))

### Documentation

- **plan**: Implementation plan for crm ribbon command group
  ([#150](https://github.com/Gharib89/crm/pull/150),
  [`2f20ed3`](https://github.com/Gharib89/crm/commit/2f20ed3bfa18bd6667d026c0c800f4b7dee02216))

- **ribbon**: How-to, README capability, SKILL entry
  ([#150](https://github.com/Gharib89/crm/pull/150),
  [`2f20ed3`](https://github.com/Gharib89/crm/commit/2f20ed3bfa18bd6667d026c0c800f4b7dee02216))

- **spec**: Design for crm ribbon command group ([#150](https://github.com/Gharib89/crm/pull/150),
  [`2f20ed3`](https://github.com/Gharib89/crm/commit/2f20ed3bfa18bd6667d026c0c800f4b7dee02216))

- **spec**: Verify ribbon decode + group ids against live org
  ([#150](https://github.com/Gharib89/crm/pull/150),
  [`2f20ed3`](https://github.com/Gharib89/crm/commit/2f20ed3bfa18bd6667d026c0c800f4b7dee02216))

### Features

- **ribbon**: Add crm ribbon command group (#142) ([#150](https://github.com/Gharib89/crm/pull/150),
  [`2f20ed3`](https://github.com/Gharib89/crm/commit/2f20ed3bfa18bd6667d026c0c800f4b7dee02216))

- **ribbon**: Add_custom_action injects button + command nodes
  ([#150](https://github.com/Gharib89/crm/pull/150),
  [`2f20ed3`](https://github.com/Gharib89/crm/commit/2f20ed3bfa18bd6667d026c0c800f4b7dee02216))

- **ribbon**: Apply_ribbon_change export->validate->import->publish
  ([#150](https://github.com/Gharib89/crm/pull/150),
  [`2f20ed3`](https://github.com/Gharib89/crm/commit/2f20ed3bfa18bd6667d026c0c800f4b7dee02216))

- **ribbon**: Customizations.xml entity + RibbonDiffXml navigation
  ([#150](https://github.com/Gharib89/crm/pull/150),
  [`2f20ed3`](https://github.com/Gharib89/crm/commit/2f20ed3bfa18bd6667d026c0c800f4b7dee02216))

- **ribbon**: Decode RetrieveEntityRibbon CompressedEntityXml zip
  ([#150](https://github.com/Gharib89/crm/pull/150),
  [`2f20ed3`](https://github.com/Gharib89/crm/commit/2f20ed3bfa18bd6667d026c0c800f4b7dee02216))

- **ribbon**: Group mapping + deterministic button id helpers
  ([#150](https://github.com/Gharib89/crm/pull/150),
  [`2f20ed3`](https://github.com/Gharib89/crm/commit/2f20ed3bfa18bd6667d026c0c800f4b7dee02216))

- **ribbon**: Parse custom buttons from RibbonDiffXml
  ([#150](https://github.com/Gharib89/crm/pull/150),
  [`2f20ed3`](https://github.com/Gharib89/crm/commit/2f20ed3bfa18bd6667d026c0c800f4b7dee02216))

- **ribbon**: Remove_custom_action drops action + orphaned command
  ([#150](https://github.com/Gharib89/crm/pull/150),
  [`2f20ed3`](https://github.com/Gharib89/crm/commit/2f20ed3bfa18bd6667d026c0c800f4b7dee02216))

- **ribbon**: Retrieve_entity_ribbon via inline-literal RetrieveEntityRibbon
  ([#150](https://github.com/Gharib89/crm/pull/150),
  [`2f20ed3`](https://github.com/Gharib89/crm/commit/2f20ed3bfa18bd6667d026c0c800f4b7dee02216))

- **ribbon**: Ribbon add-button with webresource pre-flight
  ([#150](https://github.com/Gharib89/crm/pull/150),
  [`2f20ed3`](https://github.com/Gharib89/crm/commit/2f20ed3bfa18bd6667d026c0c800f4b7dee02216))

- **ribbon**: Ribbon export command + group registration
  ([#150](https://github.com/Gharib89/crm/pull/150),
  [`2f20ed3`](https://github.com/Gharib89/crm/commit/2f20ed3bfa18bd6667d026c0c800f4b7dee02216))

- **ribbon**: Ribbon list reads custom buttons from solution
  ([#150](https://github.com/Gharib89/crm/pull/150),
  [`2f20ed3`](https://github.com/Gharib89/crm/commit/2f20ed3bfa18bd6667d026c0c800f4b7dee02216))

- **ribbon**: Ribbon remove with destructive confirm
  ([#150](https://github.com/Gharib89/crm/pull/150),
  [`2f20ed3`](https://github.com/Gharib89/crm/commit/2f20ed3bfa18bd6667d026c0c800f4b7dee02216))


## v1.3.0 (2026-06-08)

### Chores

- **plan**: Add the plan file for issue 140
  ([`a7c26ec`](https://github.com/Gharib89/crm/commit/a7c26ecaab2e8202754f43a42586f05aa1ffeba0))

### Documentation

- **spec**: Design for crm solution validate ([#141](https://github.com/Gharib89/crm/pull/141),
  [`361510a`](https://github.com/Gharib89/crm/commit/361510a41305ef1e7785b53be3fb4b658c7ab05a))

### Features

- **solution**: Add crm solution validate for offline pre-import checks
  ([#141](https://github.com/Gharib89/crm/pull/141),
  [`4b1806a`](https://github.com/Gharib89/crm/commit/4b1806a37182e57cab8174844d3f1dfe3711a3aa))


## v1.2.0 (2026-06-08)

### Features

- **keyring**: Promote keyring to core dependency
  ([`3500745`](https://github.com/Gharib89/crm/commit/3500745df1f3c4f58bc6f7ba128a24936e2a51c0))


## v1.1.1 (2026-06-08)

### Bug Fixes

- **solution**: Omit ImportJobId on on-prem; recover server-assigned id from asyncop
  ([`2870969`](https://github.com/Gharib89/crm/commit/287096939e52f227f6d005afce86d47b8d993d40))

- **solution**: Suppress false Pyright unused-function warning on _import_job_id_rejected
  ([`ec37686`](https://github.com/Gharib89/crm/commit/ec376860d9184cfd5658f057ea79be198954675f))

### Documentation

- **install**: Add uv tool install option for ASR-blocked machines
  ([`bed9523`](https://github.com/Gharib89/crm/commit/bed952374cadfae49ae53abc01575858441cef86))


## v1.1.0 (2026-06-07)

### Documentation

- **connection**: Document 'set-password' for storing profile secrets
  ([#139](https://github.com/Gharib89/crm/pull/139),
  [`1dc7387`](https://github.com/Gharib89/crm/commit/1dc73876c89c839e70807988a0d8331f8cb3219d))

- **plan**: Add set-password (#137) implementation plan
  ([#139](https://github.com/Gharib89/crm/pull/139),
  [`1dc7387`](https://github.com/Gharib89/crm/commit/1dc73876c89c839e70807988a0d8331f8cb3219d))

### Features

- **connection**: Add 'set-password' to store a secret for any profile
  ([#139](https://github.com/Gharib89/crm/pull/139),
  [`1dc7387`](https://github.com/Gharib89/crm/commit/1dc73876c89c839e70807988a0d8331f8cb3219d))

- **connection**: Add 'set-password' to store a secret for any profile (#137)
  ([#139](https://github.com/Gharib89/crm/pull/139),
  [`1dc7387`](https://github.com/Gharib89/crm/commit/1dc73876c89c839e70807988a0d8331f8cb3219d))


## v1.0.0 (2026-06-07)

### Documentation

- **claude**: CHANGELOG is PSR-owned, don't hand-edit
  ([`68b3101`](https://github.com/Gharib89/crm/commit/68b3101f362455dc7fe29c7b8e8792ba5d649c3d))

### Features

- **release**: Graduate to 1.0.0 with configure-once credentials
  ([#130](https://github.com/Gharib89/crm/pull/130),
  [`7be46b0`](https://github.com/Gharib89/crm/commit/7be46b03d5691fb39c5cdb023ad52da110421922))

### Breaking Changes

- **release**: Secrets can now be persisted (opt-in keyring/plaintext); profile resolution restores
  the session active_profile when no --profile is given. Flips allow_zero_version so PSR cuts
  v1.0.0.


## v0.13.1 (2026-06-06)

### Bug Fixes

- **repl**: Keep --session sticky across REPL command lines
  ([#128](https://github.com/Gharib89/crm/pull/128),
  [`a53577b`](https://github.com/Gharib89/crm/commit/a53577bc051405672e31606e32f9df8afc7b1b8d))


## v0.13.0 (2026-06-06)

### Features

- **solution**: Add 'solution dependencies' uninstall-blocker read
  ([#116](https://github.com/Gharib89/crm/pull/116),
  [`27b401f`](https://github.com/Gharib89/crm/commit/27b401f9949ed6bdc0f81b82189203d8051e4540))

## [0.12.0] — 2026-06-07

### Security
- Profile and session names are now validated as single path components at creation/load, preventing path traversal in on-disk profile/session/cache paths. (#126)

### Fixed
- `crm --profile <missing>` now emits the standard `{ok:false, ...}` error envelope (exit 1) instead of a raw `FileNotFoundError` traceback (#109).
- `async`: `list_async_operations` / `list_all_async_operations` now normalize the `owner_id` GUID filter to canonical form (braced/uppercase/urn inputs were sent to Dataverse verbatim). (#120)

### Added
- `metadata export-spec <logical_name> [--with-views] [--with-relationships] [-o FILE]` —
  project a live entity into the `crm apply -f` desired-state spec (round-trip:
  export-spec → apply re-creates the entity). Pure GETs. Without `-o` the spec is
  emitted under the standard JSON envelope; with `-o` the bare YAML is written
  directly to FILE. The projection is always re-appliable: a string column whose
  live format is `Json`/`RichText` (which `apply` cannot create) is exported
  without `format_name` and re-created as `Text`, and a string/decimal column
  whose deep read lacks the mandatory `max_length`/`precision` (sparse reads) is
  skipped rather than emitted unappliable (#92).
- `solution import` result (real and dry-run) now includes a `managed` field
  (`true` = managed solution, `false` = unmanaged, `null` when undeterminable)
  sniffed from `solution.xml` inside the zip. The sniff is best-effort and never
  blocks the import (#91).

## [0.11.0] — 2026-06-06

**Added**
- `crm scaffold table DISPLAY --column 'DISPLAY:KIND[:opts]' ...` — create an
  entity + N columns in a single publish by building a one-entity in-memory spec
  and running it through the `apply` engine. Column shorthand: `KIND` is one of
  `string`, `memo`, `integer`, `bigint`, `decimal`, `double`, `money`, `boolean`,
  `datetime`, `picklist`, `multiselect`, `lookup`, `image`, `file`; each resource
  is created with `if_exists=skip` (re-running is a no-op); honors global
  `--dry-run` and `--stage-only`; requires a `publisher_prefix` on the active
  profile (#90).
- Append-only JSONL audit journal of every mutating command. On success, each
  mutating CLI verb (entity create/update/upsert/delete/associate/disassociate/
  set-lookup/clear-lookup; all metadata create/update/delete-*; solution create/
  create-publisher/set-version/add-component/remove-component/publish/publish-all/
  import/job-cancel; batch; workflow activate/deactivate/run; action invoke;
  webresource create/update; app create/add-components/build-sitemap/set-sitemap;
  view create; data import; plugin register-assembly/register-step/
  unregister-assembly/unregister-step; security assign-role; async cancel; apply)
  appends one JSON line to
  `${CRM_HOME:-~/.crm}/audit/<session>.jsonl`. Each line carries: `ts` (ISO-8601
  UTC), `profile`, `command`, `target`, `solution`, `staged`, `dry_run`, `ok`, and
  `result_id`. Read/query/get/list/export verbs never write to the journal.
  `--dry-run` previews are journaled with `dry_run: true` so they are never
  mistaken for real changes. The request payload is never stored (#89).
- `crm session audit [--tail N] [--session NAME]` prints the current (or a named)
  session's audit journal; honors `--json` (#89).
- Opt-in, persistent, read-only on-disk cache of entity definitions, per connection
  profile, to speed up repeated one-shot agent invocations. Enable with
  `--cache-metadata` (global flag) or `CRM_CACHE_METADATA=1` (truthy: `1`/`true`/`yes`/`on`).
  Force a one-shot refresh with `--refresh-metadata`. When the cache is active,
  `crm metadata entities` emits `meta.cache` = `"hit"` | `"miss"` | `"refreshed"` in
  both `--json` and human output. Cache-mode caveat: with `--cache-metadata` the
  command returns only the 2-field rows (LogicalName / EntitySetName); the full
  5-field listing is unchanged when the flag is absent. `--custom-only` is
  incompatible with `--cache-metadata` (the cache lacks the custom flag) and errors
  (exit 2). `--top` works (client-side slice). Cache files live at
  `<CRM_HOME or ~/.crm>/cache/<profile-name>/entitydefs.json`. The cache stores
  the `{logical, set_name}` list plus the source `url`, `api_version`, and
  `cached_at` timestamp; a url/api_version mismatch is treated as a miss.
  Invalidation: any successful metadata write (entity/attribute/optionset/relationship
  create/update/delete, and publish-all/publish-xml) deletes the profile's cache
  file so a stale cache cannot outlive a schema change; a ~15-minute TTL backstop
  also forces a refresh. Cache misses and read errors degrade gracefully (fall back to
  a live fetch). Read-only schema only — records and secrets are never cached.
  When launched with `--cache-metadata`, the REPL's entity-name completion is
  served from the same on-disk cache. `crm metadata cache-clear` deletes the active
  profile's cache file; emits `{"cleared": true|false}` (#88).
- `crm entity get` and `crm metadata attribute` gain a repeatable `--expect
  ATTR=VALUE` flag — a field-comparison verify primitive. Each pair is split on
  the FIRST `=` (so a VALUE may itself contain `=`); every pair must match
  `str(record[ATTR]) == VALUE` (AND-gate, a missing key never matches). The first
  mismatch (CLI order) exits 1 with `{ok:false, error:"Expectation failed: …",
  meta:{attr, expected, actual}}` (`actual` is the raw value); all match passes
  through unchanged as `ok:true` (exit 0). For `entity get` the check runs against
  the full record before any `--minimal` projection. A malformed `--expect` (no
  `=`, or empty attr) is a usage error (exit 2) raised before any server call.
  Enables a create→publish→verify loop, e.g. `metadata add-attribute … &&
  solution publish-all && metadata attribute <entity> <attr> --expect
  AttributeType=String` (#86).
- `--minimal` on `crm query odata` / `fetchxml` / `saved` / `user` and
  `crm entity get` strips OData annotation keys (any key containing `@`:
  `@odata.etag`, `*@OData.Community.Display.V1.FormattedValue`,
  `*@Microsoft.Dynamics.CRM.lookuplogicalname`) from each record in `--json`
  output, keeping business fields, `_*_value` lookup GUIDs, and the primary id.
  Shallow prune (top-level record keys only; expanded records under `--expand`
  are untouched), and the `value`-list envelope (`@odata.count` / `@odata.nextLink`
  / `@odata.context`) is preserved. No-op in human/table mode. Raw output remains
  the default (non-breaking) — a token-efficient projection for agents (#85).
- `--retry-on-ambiguous` root flag (env: `CRM_RETRY_ON_AMBIGUOUS`) re-enables
  auto-retry of non-idempotent `POST` creates on transport error / `429` / `503`,
  opting back into the duplicate-create risk (#84).
- `crm solution components <name> --save <path>` writes a normalized component
  inventory (a bare JSON list, each entry `{"componenttype": int, "objectid": str,
  "rootcomponentbehavior": int|null}`) to `<path>`, creating parent dirs as needed.
  Emits `{"saved": "<path>", "count": N}`. `--diff <expected.json>` fetches live
  components and compares them against the saved file; exits non-zero (1) on drift.
  Components are keyed on the tuple `(componenttype, objectid, rootcomponentbehavior)`
  after normalisation — `missing` = in expected but not live, `unexpected` = in live
  but not expected. The two flags are mutually exclusive; bare `components <name>` is
  unchanged. The round-trip `--save` then `--diff` against the same org reports no
  drift (#82).
- `crm metadata picklist` and `crm metadata get-optionset` now emit a flattened
  `meta.options` list (`[{value, label}]`) in `--json` mode, so agents need not
  dig through `Label.UserLocalizedLabel.Label`. The raw `data` is unchanged (no
  contract break). Labels resolve via `UserLocalizedLabel` then `LocalizedLabels`.
  Boolean attributes have no `Options` array (`TrueOption` / `FalseOption`
  instead), so `meta.options` is empty for them — read those raw fields directly (#76).
- `crm security` command group: list and assign Dynamics 365 security roles.
  `list-roles` lists all security roles (optionally filtered to a business unit
  via `--business-unit GUID`). `list-user-roles USER_ID` and
  `list-team-roles TEAM_ID` return the roles currently assigned to a system user
  or team (both positional args are GUIDs). `assign-role ROLE_ID` assigns a
  security role to a user (`--to-user GUID`) or a team (`--to-team GUID`) —
  exactly one target flag is required. Assignment is cumulative and not cleanly
  reversible, so the command is gated by an interactive confirmation prompt
  (bypass with `--yes`) and the destructive-op PreToolUse hook. Standard
  admin-header options (`--as-user`, `--as-user-object-id`,
  `--suppress-dup-detection`, `--bypass-plugins`) are available on `assign-role`
  (#83).
- `crm metadata dependencies <target>` is a new read-only command that returns
  `can_delete` (bool) plus a `blockers[]` list for any metadata component. `--kind`
  selects the component type (`entity` / `attribute` / `optionset` / `relationship`;
  default `entity`); attribute targets use dotted notation (`entity.attribute`).
  `--for delete` (default) calls `RetrieveDependenciesForDelete` and lists what
  would block deletion; `--for dependents` calls `RetrieveDependentComponents` and
  lists what currently depends on the target (#81).
- `--check-dependencies` flag (default off) on `metadata delete-entity`,
  `delete-attribute`, `delete-relationship`, and `delete-optionset`: folds
  `can_delete` and `blockers[]` into the result via a pre-delete
  `RetrieveDependenciesForDelete` call. Pair with `--dry-run` for a non-destructive
  dependency preview without issuing the DELETE (#81).

**Changed**
- Extracted the destructive-verb classification (`DESTRUCTIVE`, `ROLE_VERBS`,
  `is_destructive()`) into a new dependency-free `crm/core/destructive.py`. The
  PreToolUse destructive-op gate (`.claude/hooks/destructive_op_gate.py`) keeps
  its standalone stdlib copy so it stays import-free and offline on every Bash
  call; a new `crm/tests/test_destructive_sync.py` asserts the two copies stay
  aligned (#87).
- Non-idempotent `POST` record-creates (and actions) are no longer auto-retried on
  transport error / `429` / `503` by default — a lost response may have already
  committed the record, so a blind re-send risks a duplicate (#84). Idempotent verbs
  (`GET`/`PUT`/`PATCH`/`DELETE`) are unchanged, and `$batch` keeps its own independent
  retry loop. Pass `--retry-on-ambiguous` (or set `CRM_RETRY_ON_AMBIGUOUS`) to restore
  the old behavior when the re-send risk is acceptable.

**Fixed**
- `crm metadata delete-entity`, `delete-attribute`, `delete-relationship`, and
  `delete-optionset` under `--dry-run` now return
  `{"_dry_run": true, "would_delete": true, ...}` instead of falsely reporting
  `{"deleted": true, ...}` (#81).
- `crm metadata picklist` (human/table mode) now resolves option labels via the
  full `UserLocalizedLabel` → `LocalizedLabels` fallback, matching `--json`
  `meta.options`. Previously the table read `UserLocalizedLabel` only, so an
  option whose label is carried solely via `LocalizedLabels` rendered blank (#76).

## [0.10.0] — 2026-06-05

**Added**
- `crm plugin` command group: register and manage Dynamics 365 plug-in assemblies
  and processing steps via the `pluginassemblies` / `plugintypes` /
  `sdkmessageprocessingsteps` Web API entity sets.
  `register-assembly PATH` uploads a `.dll` as a plug-in assembly (base64-encoded
  into `content`); `--update` re-uploads the binary of an existing assembly by name
  without touching its identity metadata. `list-types` lists platform-generated
  plug-in types (`typename`, `friendlyname`, `plugintypeid`), optionally filtered
  by `--assembly NAME`. `register-step` registers an `sdkmessageprocessingstep`
  bound to a message (`--message Create/Update/…`) and a fully qualified type
  (`--plugin-type`); stage (`prevalidation`/`preoperation`/`postoperation`) and
  mode (`sync`/`async`) are configurable — async forces `postoperation` (other
  combinations are rejected). The step name is auto-derived as
  `<typename>: <message> of <entity>` (pass `--name` when the derived string would
  exceed 256 chars). `unregister-assembly ASSEMBLY` cascades — it deletes dependent
  steps first, then the assembly. `unregister-step STEP` deletes a step by name or
  GUID; an ambiguous name errors. `--solution` sends `MSCRM.SolutionUniqueName` on
  the writes. `--dry-run` skips all writes (resolution GETs still fire). `--json`
  mode returns the standard `{ok, data, meta}` envelope (#80).
- `crm app build-sitemap <SITEMAP_NAME>` builds a valid SiteMapXml from
  structured input and creates the sitemap. Three repeatable options describe the
  tree: `--area 'id[:Title]'` (at least one required), `--group 'areaId/groupId[:Title]'`
  (nested under an area), and `--subarea 'areaId/groupId:entity=<logical>[:Title]'`,
  where a SubArea binds a table via the SiteMapXml `Entity=` attribute (Title
  optional — omit it and the platform derives the label from the entity). SubArea
  Ids are auto-allocated from the entity logical name to stay unique across the
  document, and every attribute value is XML-escaped; Area/Group Ids and the
  references between them are validated, so broken references or duplicate Ids
  fail with an error. After building the XML it delegates to the `set-sitemap`
  path so the POST body is byte-identical, then optionally publishes. `--unique-name`
  sets `sitemapnameunique` to auto-associate the sitemap with that app (same as
  `set-sitemap`); `--publish/--no-publish` (default: publish) runs PublishAllXml
  after creation. `crm --dry-run app build-sitemap ...` prints the generated
  SiteMapXml and issues no POST. Complements `set-sitemap`, which uploads a
  pre-built XML file (#79).
- `crm webresource create` / `update` / `get` / `list` manage web resources
  (`webresourceset`). `create` base64-encodes the `--file` bytes into the
  `content` column and infers the `webresourcetype` from the file extension
  (`.html`=1, `.css`=2, `.js`=3, `.xml`=4, `.png`=5, `.jpg`=6, `.gif`=7,
  `.xap`=8, `.xsl`=9, `.ico`=10, `.svg`=11, `.resx`=12 — the real D365
  `webresource_webresourcetype` option set, so CSS=2 and 8 is Silverlight, not
  the other way around); `--type <int>` overrides inference and an unknown
  extension without it is rejected. `--display-name` defaults to the name.
  `update <name>` resolves the resource by name and issues a plain PATCH of only
  the sent fields (content from `--file` and/or `--display-name`; at least one
  required) — not retrieve-merge-write. Both honor `--solution` (sent as
  `MSCRM.SolutionUniqueName`) and publish after the write (`--no-publish` /
  `--stage-only` suppress it). `list --custom-only` keeps unmanaged resources;
  `get <name>` prints a record (#78).
- `crm app create --icon-webresource <name|guid>` sets the app icon to a web
  resource: a GUID is used directly, a name is resolved to its id, and omitting
  the flag keeps the platform default icon (#78).
- `crm data import <ENTITY_SET> <INPUT_FILE>` bulk-imports records via the
  Dataverse `$batch` endpoint — the only on-prem bulk mechanism (`CreateMultiple`
  / `UpsertMultiple` are cloud-only). Supports JSONL and CSV input (format
  inferred from the file suffix; override with `--format`). Two modes:
  `--mode create` (POST, default) and `--mode upsert` (PATCH by GUID via
  `--id-column`). Records are sent in chunks of `--chunk-size` (default 100);
  each chunk is a transactional changeset (atomic, all-or-nothing) by default.
  `--no-transaction` sends each operation as a top-level batch operation instead.
  `--continue-on-error` sends `Prefer: odata.continue-on-error` to skip past
  individual failures — it requires `--no-transaction` (a changeset is itself
  all-or-nothing; combining the two is rejected with a usage error). CSV values
  are coerced best-effort (empty→null, `true`/`false`→bool, integers, floats);
  non-finite tokens (`NaN`/`inf`) and integer-looking strings (`"007"`) are not
  preserved — use JSONL for IDs, postal codes, and lookup binds. Dry-run via the
  global `crm --dry-run data import ...` produces zero writes; the summary carries
  `dry_run: true`. Output: `{imported, failed, chunks, entity_set, mode, dry_run,
  format}`; `failed > 0` surfaces a `meta.warnings` advisory; exit code is 0 on
  partial failure, consistent with `crm batch` (#75).
- `crm connection doctor` (also exposed as the top-level alias `crm doctor`)
  runs a live, ordered connection probe and renders a five-line checklist:
  `dns_tcp`, `tls`, `version` (the configured `api_version`), `auth`, and an
  informational `rate_limit`. Each layer's failure is classified distinctly
  (DNS vs TCP vs TLS vs wrong api_version vs 401/403) with an actionable hint.
  It is a read-only diagnostic — it never negotiates or mutates the profile, and
  the raw GETs run regardless of `--dry-run`. `--json` emits
  `{ok, data:{checks:[{check,ok,detail,hint}]}}`; overall `ok` (and the exit
  code) is the AND of the four diagnostic checks, `rate_limit` never affects it
  (#74).
- `crm solution extract` and `crm solution pack` bridge the CoreTools
  `SolutionPackager.exe` to turn an exported solution zip into a source-controllable
  folder tree and back (`git diff` on the extracted tree _is_ the solution diff).
  These are **offline** local-file transforms: no connection, profile, or backend
  is required. `--package-type` selects `Unmanaged` (default) / `Managed` / `Both`;
  the executable is resolved via `--solutionpackager-path` → `CRM_SOLUTIONPACKAGER`
  env → `PATH`, and an absent binary fails with an actionable error naming the
  `Microsoft.CrmSdk.CoreTools` NuGet package (no bundling or auto-download). The
  subprocess honors `--timeout` and the emitted envelope carries
  `{action, exit_code, folder, zipfile, stdout_tail}`; a non-zero SolutionPackager
  exit fails the command (#73).
- `crm entity create` and `crm entity update` accept an opt-in `--validate` flag
  that field-name-checks the payload before the write. It runs 1-3 read-only
  metadata GETs (resolve entity-set → logical name, the entity's attribute names,
  and the ManyToOne navigation-property names), then flags any payload key absent
  from the union with a `did_you_mean` suggestion. `<nav>@odata.bind` deep-link
  keys validate against the nav-name union, so a bound lookup is not a false
  positive. On a miss the write is blocked with
  `{ok:false, meta:{unknown_fields, did_you_mean}}`. Composable with `--dry-run`
  (the validation GETs run for real even under dry-run). Scope is field-NAME only;
  option-set values are not checked (#72).
- `crm solution add-component` and `crm solution remove-component` add or remove
  an existing component to/from an unmanaged solution via the `AddSolutionComponent`
  / `RemoveSolutionComponent` Web API actions. `--type` accepts a `componenttype`
  integer or a friendly name (`entity`, `attribute`, `relationship`, `optionset`,
  `webresource`, … — names are case- and separator-insensitive; pass a raw int for
  any type not in the map). Both pre-flight `solution_info` and refuse a managed
  target client-side. `add-component` is non-destructive and supports
  `--no-add-required` (`AddRequiredComponents: false`) and `--no-subcomponents`
  (`DoNotIncludeSubcomponents: true`). `remove-component` is gated as a destructive
  operation: it prompts for confirmation (aborting cleanly in a non-TTY context
  unless `--yes`), and the verb-name PreToolUse hook blocks it without `--yes` (#71).
- `crm solution import` now parses the import job's `data` column into a
  solution-level `result` (`success`/`warning`/`failure`) plus a `components`
  list (`{name, type, result, errorcode?, errortext?}` per imported component).
  Any non-success component adds a `meta.warnings` note, so a partial failure
  under an overall-succeeded async op (`status: succeeded`) is no longer masked.
  `crm solution import-result <import_job_id>` re-fetches a completed job and
  runs the same parser to verify a prior import without re-importing. Both accept
  `--formatted` to also attach the Excel-format `RetrieveFormattedImportJobResults`
  report verbatim under `formatted_results` (opt-in, a separate round-trip) (#70).
- `crm metadata delete-attribute <entity> <attribute>` and
  `crm metadata delete-relationship <schema-name>` delete a custom column or a
  custom relationship (1:N or N:N). Both pre-flight against the metadata to refuse
  managed and non-custom targets client-side; `delete-attribute` additionally
  refuses primary (id/name) and sub-attribute (`AttributeOf`-set) columns. Each
  honors `--solution` (sent as `MSCRM.SolutionUniqueName`) and is gated as a
  destructive operation: each prompts for confirmation, aborting cleanly in a
  non-TTY context unless `--yes` is passed, and the verb-name PreToolUse hook
  blocks them without `--yes`. Remaining-dependency conflicts are left to the
  server's 4xx (#69).
- `crm metadata describe <entity>` returns a one-shot, read-only write-readiness
  brief: the entity set name, primary id/name, and every writable attribute with
  its required level. Lookups carry `bind_key` (`<Nav>@odata.bind`, self-derived
  from `ManyToOne` relationship metadata) plus `targets[]` with both the logical
  name and the `EntitySetName` so the bind VALUE is usable; picklist / state /
  status attributes carry inline `{value, label}` options, and a picklist bound to
  a global option set also carries its `global_optionset_id` GUID (which on-prem
  9.1 needs to bind on create). Built from pure GETs, gated so only the attribute
  kinds an entity actually uses cost a round-trip (#68).
- `crm solution import` is now gated as a destructive operation: an overwrite
  import (the default) clobbers unmanaged customizations in the target org, so it
  prompts for confirmation and, in a non-TTY context, aborts cleanly (exit 1;
  under `--json` the body is the standard `{"ok": false, "error": "aborted by
  user"}` envelope, otherwise a human-formatted error) unless `--yes` is passed.
  The PreToolUse destructive-op gate also blocks any `crm solution import` without
  `--yes` (verb-only, so a `--no-overwrite` import is gated too — any import
  mutates the org). Default import semantics are unchanged (#67).
- `crm solution set-version <unique_name>` updates an unmanaged solution's
  `version` / `friendlyname` / `description` in place. At least one field is
  required and `--version` is validated as 4-part dotted numeric before any HTTP;
  managed solutions and patches are rejected client-side (the server returns
  `CannotUpdateSolutionPatch` for a patch). Delegates to the shared record-update
  path, so `If-Match:*` and `--dry-run` are reused with no new HTTP path (#66).
- Non-interactive REPL guard: bare `crm` (no subcommand) now fails fast with
  exit 2 and a usage message pointing at `crm --help` whenever the caller is
  non-interactive — under `--json`, with `CRM_NO_REPL` set (`1`/`true`/`yes`/`on`),
  or when stdin is not a TTY (piped/redirected, as agents and CI invoke it). Under
  `--json` the message is the standard `{ok:false,error}` envelope; otherwise it
  goes to stderr. An interactive human and explicit `crm repl` still launch the
  REPL. A proactive isatty probe so an agent invocation never hangs (#65).
- Structured warnings channel: the JSON envelope now carries a `meta.warnings`
  array — the single place to scan for advisories (staged-but-unpublished,
  created-but-read-back-failed, partial-optionset). `*_lookup_error` read-back
  keys are mirrored into it (and left in `data` for back-compat), and a
  non-transactional optionset update that fails mid-stage surfaces
  `meta.completed_steps` / `meta.failed_stage` on the error envelope so a partial
  mutation is observable. Every other error site is unchanged (#64).

**Changed**
- **Breaking (envelope):** the singular `meta.warning` scalar is replaced by the
  `meta.warnings` array, so multiple advisories no longer clobber each other (#64).

**Fixed**
- `crm metadata update-optionset --dry-run` previously returned `{"updated": true, ...}`,
  incorrectly signalling a completed write. Under dry-run it now returns
  `{"_dry_run": true, "name": ..., "diff": {...}, "actions": [...]}` (#77).
  The `diff` classifies each pending change as `inserts` / `updates` (with
  `old_label` / `new_label` looked up from the live option set) / `deletes` (with
  `old_label`) / `reorder` (`{old, new}` value lists). The live GET fires for real
  to build the diff; no POSTs are issued.

## [0.9.0] — 2026-06-04

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
- Docs examples and test fixtures now use a neutral `contoso` org/publisher prefix
  (`contoso_`, host `internalcrm.contoso.local`) instead of an internal org name,
  so the published docs and shipped `SKILL.md` carry no environment-specific names.

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
