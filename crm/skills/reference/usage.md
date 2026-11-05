# Usage: which components are live (`--in-use`)

Ask a listing for the components that hold data or fire, so a migration spends its
effort on what matters. Flags: `crm metadata entities --help`,
`crm legacy metadata --help`, `crm plugin list-types --help`,
`crm legacy components --help`.

## The `usage` object

With `--in-use`, every listed row gains `usage`: `{state, signal, value, as_of, reason}`.
Without the flag the rows are unchanged and nothing is probed.

- `state`: `used`, `unused` or `unknown`.
- `signal`: what was read. Entities use `record_presence`; plug-ins use
  `plugin_step_runs`, `async_job` or `plugin_trace`.
- `value`: a count, or `null` when only existence is known. A 2011 count capped by the
  server's 50,000 aggregate limit reads `50000` with `reason: "at least 50,000"`.
- `as_of`: UTC time the signal was read.
- `reason`: why a row is `unknown`, or a caveat on a count.

## Gotchas

- **`unknown` is not `unused`.** A failed read, a refused entity, a virtual, elastic or
  private table: the row stays, labelled `unknown`. `--in-use` drops only `unused`; add
  `--exclude-unknown` for a proven-used list. Treat an `unknown` row as live when
  scoping a migration, and read its `reason` before dropping it.
- **A v9 count of 0 means nothing.** `RetrieveTotalRecordCount` is a snapshot under 24
  hours old and reads 0 on a young org (`systemuser`, `contact`, `webresource`). The
  probe confirms every zero with a one-row read; read a zero from
  `crm query count` as unconfirmed.
- **A 2011 org has no count API.** Existence is a one-row read by the primary id
  attribute from metadata. An entity that refuses `RetrieveMultiple` is `unknown`.
- **The probe is one read per entity, run four at a time.** About 80 seconds for 900
  entities on Dataverse, two minutes for 425 on 2011. Progress goes to stderr
  (`[crm] usage stage=entities done=n/total`); stdout stays the JSON envelope. Give it a
  long timeout, and use `--top` to bound a trial run.
- `--in-use` does not combine with `--cache-metadata`, and `legacy metadata --in-use`
  takes no entity names.

## Plug-in steps, types and assemblies

`crm plugin list-types --in-use` (v9) and `crm legacy components --in-use` (2011) roll
step usage up to types and assemblies. Only a disabled step is provably unused.

- **A synchronous step leaves no run record**, so it stays `unknown` however long it
  has existed. Most steps are synchronous; expect most rows `unknown` and treat them as
  live. `used` needs an async job row (or a trace log row when tracing is on).
- **An absent job row proves nothing**: `asyncautodelete` steps lose successful jobs,
  and bulk-delete jobs purge old ones. Tracing is off by default and keeps about a day.
- A workflow-activity or custom API type has no steps and reads `unknown`, not `unused`.
- 2011 has no trace log. Its async job read stops at a 120 second or 100,000 row cap and the steps it
  did not reach stay `unknown`; read the `reason`.
