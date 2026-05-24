# Changelog

All notable changes to `crm` are documented in this file.

The format is loosely based on [Keep a Changelog](https://keepachangelog.com/en/1.1.0/),
and this project adheres to [Semantic Versioning](https://semver.org/spec/v2.0.0.html).

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
