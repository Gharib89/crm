# How-to: session

Inspect local session state, taken from the CRMWorx build (§1). See the
[CLI reference](../reference/cli.md) for every flag.

## Show the active profile and last query

```bash
crm --json session info
```
Reports the active profile, the current entity set, and the last query run this session.

## Review the recent command history

```bash
crm --json session history
```
In `--json` mode (as above), it emits the full `history` array — raw command strings, no timestamps; text mode prints just the last 50.

## Clear the local session state

```bash
crm --json session clear
```
Wipes the cached session state (active entity set, last query, history); profiles are unaffected.

## Review the audit journal of mutations

Every mutating command (entity create/update/upsert/delete/associate/disassociate/
set-lookup/clear-lookup; all metadata create/update/delete-*; solution create/
create-publisher/set-version/add-component/remove-component/publish/publish-all/
import/job-cancel; batch; workflow activate/deactivate/run; action invoke;
webresource create/update; app create/add-components/remove-components/build-sitemap/set-sitemap;
view create; data import; plugin register-assembly/register-step/register-image/
unregister-assembly/unregister-step/unregister-image; security assign-role;
async cancel; apply) appends one line to the
audit journal on success.
Read, query, get, list, and export verbs never write to the journal.

Journal location: `${CRM_HOME:-~/.crm}/audit/<session>.jsonl` — one file per session
name (the `--session` value; default `default`). The file is append-only with fsync
so a crash mid-write cannot corrupt earlier lines.

```bash
# Print the current session's journal
crm session audit

# Last 20 entries only
crm session audit --tail 20

# Journal for a different session
crm session audit --session my-session

# JSON output
crm --json session audit
```

### Journal line schema

Each line is a JSON object with these keys (in order):

| Key | Type | Description |
|-----|------|-------------|
| `ts` | string | ISO-8601 UTC timestamp of the command |
| `profile` | string \| null | Active connection profile name (null if none resolved) |
| `command` | string | CLI verb, e.g. `"entity create"` |
| `target` | string \| null | Entity set / metadata name / solution, or null |
| `solution` | string \| null | Target solution unique name, or null |
| `staged` | bool | Whether the global `--stage-only` flag was active (see note below) |
| `dry_run` | bool | Whether `--dry-run` was active |
| `ok` | bool | Always `true` — only successful commands are journaled (a failing command raises before the journal write) |
| `result_id` | string \| null | GUID of the created/affected record when derivable, else null |

### Guarantees

- **Payload never stored** — only the metadata above is recorded; no request bodies, field values, or secrets.
- **Reads never journaled** — only mutating verbs write to the journal.
- **Only successes journaled** — a command that errors out raises before the journal write, so failures never appear.
- **`--dry-run` previews are journaled** with `dry_run: true`, so a preview is never mistaken for a real change.
- **Append-only with fsync** — a crash mid-write cannot corrupt earlier lines.

**`staged` reflects only `--stage-only`, not an unpublished write.** The
atomic metadata-write commands (`metadata`/`form`/`view`/`ribbon`/`sitemap`/
`app`/`dashboard`/`chart`/`webresource`) now **stage by default** — no
`PublishAllXml` unless `--publish` is passed — but a plain staged write still
journals `staged: false` unless the *global* `--stage-only` flag was also on.
To tell whether a given write actually published, check that command's own
result envelope for `data.published` (absent/missing when staged, `true` once
it publishes) rather than this journal column.

### Example

The human-mode view is a condensed line per entry — `timestamp  command  target
result_id` plus a bracketed suffix for `[dry-run]` / `[staged]` rows. Use `--json`
for the full 9-key record shown in the schema above.

```bash
# After running a few commands against a Contoso org:
crm session audit --tail 5
#   2026-06-06T10:00:01Z  entity create  contacts  3fa85f64-5717-4562-b3fc-2c963f66afa6
#   2026-06-06T10:00:15Z  entity update  contacts
#   2026-06-06T10:00:32Z  entity delete  accounts   [dry-run]
```
