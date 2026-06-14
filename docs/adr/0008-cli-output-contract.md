---
status: accepted
---

# CLI output contract: curated `data`, normalized `_entity_id`, bare-array lists, concise human render

## Context

The `--json` envelope is the contract coding agents script against, yet the
`data` payload had drifted into per-command shapes: `query odata` returned the
raw OData envelope (`data.value` + `@odata.*`) while `metadata entities` returned
a bare array (#304); the three write verbs reported the record id under three
different keys — the PK attribute (`create`), `_entity_id` (`update`), `id`
(`delete`) (#303); and a single record in human mode dumped every attribute
(~190 lines) led by `@odata.context`/`@odata.etag`, burying the name and id
(#302). Only ADR 0001 (exit codes) governed output; nothing governed `data`.

## Decision

The emit envelope's `data` is a **curated, CLI-owned shape**, not a passthrough
of the raw D365 Web API response. Concretely:

1. **List payload** — every list verb puts a **bare array** of row objects in
   `data` (`data[0]` is the first row). OData paging is relocated to `meta`
   (`meta.next_link` ← `@odata.nextLink`, `meta.count` ← `@odata.count`), and
   per-row protocol keys (`@odata.etag`, `@odata.*`) are stripped. No command
   returns the OData envelope in `data`.

2. **Normalized entity id** — `_entity_id` (with `_entity_id_url`) is the single
   stable key for the affected record's GUID, present on `create` (alongside the
   full record), `update`, `delete` (`{deleted: true, _entity_id, _entity_id_url}`),
   and `entity get`. The leading underscore marks it CLI-synthesized, distinct
   from the real PK attribute (`accountid`, …). It is **not** injected per-row in
   list payloads — each row already carries its PK.

3. **Record render modes** — a single record renders per output mode with a
   default and one opt-out knob. **JSON**: default = full curated record
   (`@odata.*` stripped, `_entity_id` injected); `--minimal` trims it. **Human**:
   default = *concise* — null/empty fields hidden, `@odata.*` suppressed,
   `_entity_id` hoisted first (primary-name attribute hoisted too only when
   metadata is already cached, never via an added round-trip); `--full` expands
   to every field including nulls.

## Considered options

- **Passthrough `data`** (mirror the raw Web API response): rejected — it makes
  `@odata.*` and the `query odata` envelope a "feature," but forces a different
  extraction rule per command on the primary consumer (agents). The envelope is
  the CLI's contract (per CONTEXT.md), not a D365 mirror.
- **Bare `id` instead of `_entity_id`**: rejected — a bare `id` injected into
  create/get's full record would be indistinguishable from a genuine attribute
  and breaks the established leading-underscore convention for synthesized fields
  (`_dry_run`, `_exists`).
- **Always resolve the primary-name attribute for the human render**: rejected as
  default — it adds a metadata round-trip to a plain `entity get`. Surfaced
  best-effort only when metadata is already cached.

## Consequences

- **Breaking (pre-1.0, `major_on_zero=false` — permitted):**
  - `query odata` `--json` rows move `data.value` → `data`; `@odata.context`/
    `@odata.nextLink` leave `data` for `meta`.
  - `delete` `--json` renames `id` → `_entity_id`.
  - Human single-record output is concise by default; the old full dump is now
    behind `--full`.
- **Additive:** `create`/`update`/`entity get` gain `_entity_id`; `create` keeps
  its full record.
- These breaks are one-time, recorded here and in the `fix:`/`feat:` commit
  subjects so `python-semantic-release` documents them in `CHANGELOG.md`.
- The contract terms live in `CONTEXT.md` (Data payload, List payload,
  Normalized entity id, Record render modes).
